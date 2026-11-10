"""A scheduled run from due to told, on a fake clock.

The reply itself is stubbed (`wake_chat` writes a finished or failed reply at
once, and `generation.get` answers with what the script says), because what is
tested here is everything around it: which model is chosen and why, waiting for
a free one, retrying only what is safe, the catch-up window, `[SILENT]`, and how
often a failure is told. Every one of these fails quietly when it is wrong.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from lembas.db.models import (
    ROLE_ASSISTANT,
    ROLE_USER,
    Chat,
    Connection,
    Message,
    Model,
    Schedule,
    ScheduleRun,
    User,
)
from lembas.services import generation as generation_service
from lembas.services import schedules as schedules_service
from lembas.services import settings_store
from lembas.services import wake as wake_service
from lembas.services.crypto import encrypt
from lembas.services.schedule import models as models_service
from lembas.services.schedule import notify, runner, runs, ticker
from lembas.services.schedule import rule as rule_service

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)  # a Monday


@dataclass
class FakeGeneration:
    done: bool = True
    version: int = 1
    error_status: int | None = None
    error_retry_after: float | None = None
    error_unreachable: bool = False


class Script:
    """What the stubbed replies say, in order, and the clock."""

    def __init__(self) -> None:
        self.now = T0
        self.replies: list[dict] = []
        self.generations: dict[str, FakeGeneration] = {}
        self.turns: list[str] = []
        self.told: list[dict] = []
        self.busy: Counter[str] = Counter()

    def next_reply(self) -> dict:
        return self.replies.pop(0) if self.replies else {"content": "Done."}


@pytest.fixture
def script(db, registered, monkeypatch):
    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    s = Script()
    monkeypatch.setattr(runner, "_now", lambda: s.now)

    def write(message, reply: dict) -> None:
        message.content = reply.get("content", "")
        message.error = reply.get("error", "")
        message.complete = True
        s.generations[message.id] = FakeGeneration(
            error_status=reply.get("status"),
            error_retry_after=reply.get("retry_after"),
            error_unreachable=bool(reply.get("unreachable")),
        )

    async def wake(chat_id, content, *, model_id="", connection_id=None):
        from lembas.db.session import session_scope

        s.turns.append(content)
        with session_scope() as session:
            session.add(Message(chat_id=chat_id, role=ROLE_USER, content=content, machine=True))
            message = Message(
                chat_id=chat_id, role=ROLE_ASSISTANT, model_id=model_id,
                connection_id=connection_id, complete=False,
            )
            session.add(message)
            session.flush()
            write(message, s.next_reply())
            session.commit()
            return message.id

    def restart(chat_id, message_id):
        from lembas.db.session import session_scope

        with session_scope() as session:
            message = session.get(Message, message_id)
            write(message, s.next_reply())
            session.commit()

    async def announce(db_, owner, *, schedule, run, failed, ask_pause=False):
        s.told.append({"run": run.id, "status": run.status, "failed": failed,
                       "ask_pause": ask_pause})
        return True

    monkeypatch.setattr(wake_service, "wake_chat", wake)
    monkeypatch.setattr(generation_service, "restart", restart)
    monkeypatch.setattr(generation_service, "get", lambda mid: s.generations.get(mid))
    monkeypatch.setattr(notify, "announce", announce)
    real_ours = models_service.ours
    monkeypatch.setattr(models_service, "ours", lambda d: real_ours(d) + s.busy)
    models_service.forget()
    yield s
    models_service.forget()


def _owner(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _connection(db, name: str, *, one_at_a_time: bool = False) -> Connection:
    connection = Connection(
        name=name, base_url=f"http://127.0.0.1:1/{name}", api_key_encrypted=encrypt(""),
        one_model_at_a_time=one_at_a_time,
    )
    db.add(connection)
    db.commit()
    return connection


def _model(db, connection, model_id: str, position: int, **fields) -> Model:
    row = Model(
        connection_id=connection.id, model_id=model_id, position=position,
        capabilities_json={"tools": True}, **fields,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def three(db, script):
    """Two local models on one server that holds one at a time, and a hosted
    one with two slots; the scheduling list in that order."""
    local = _connection(db, "local", one_at_a_time=True)
    hosted = _connection(db, "hosted")
    a = _model(db, local, "local-big", 0, display_name="Big")
    b = _model(db, local, "local-small", 1, display_name="Small")
    c = _model(db, hosted, "hosted", 2, display_name="Hosted", parallel=2)
    settings_store.update(
        db, {"models": ["local-big", "hosted"]}, key=settings_store.SCHEDULES
    )
    return a, b, c


def _make(db, *, rule=None, model_id="", **settings) -> Schedule:
    schedule = schedules_service.create(
        db,
        owner=_owner(db),
        title="Build check",
        instruction="Check the build.",
        rule=rule or {"start": T0.isoformat(), "every": {"hours": 1}},
        model_id=model_id,
        settings=settings or None,
    )
    schedule.next_fire_at = T0
    db.commit()
    return schedule


async def _sweep(script, at: datetime) -> int:
    script.now = at
    started = await ticker.sweep(now=at)
    for task in list(ticker._FIRING):
        await task
    return started


def _runs(db, schedule) -> list[ScheduleRun]:
    db.expire_all()
    return list(
        db.scalars(
            select(ScheduleRun)
            .where(ScheduleRun.schedule_id == schedule.id)
            .order_by(ScheduleRun.created_at)
        )
    )


# --- Choosing a model ----------------------------------------------------------------------
@pytest.mark.anyio
async def test_the_first_choice_runs_it_when_free(db, three, script):
    schedule = _make(db, model_id="local-big")
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert (run.status, run.model_id, run.model_reason) == ("ok", "local-big", "pinned")
    assert run.timing == "on_time"


@pytest.mark.anyio
async def test_a_busy_first_choice_switches_to_the_next_free_one(db, three, script):
    big, small, hosted = three
    schedule = _make(db, model_id="local-big")
    script.busy[small.id] = 1  # somebody is being answered on the same server
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.model_id == "hosted"
    assert run.model_reason == "switched"
    assert run.switched_from == "Big"


@pytest.mark.anyio
async def test_a_parallel_model_with_a_free_slot_is_free(db, three, script):
    big, small, hosted = three
    schedule = _make(db, model_id="hosted")
    script.busy[hosted.id] = 1  # one of its two slots
    await _sweep(script, T0)
    assert _runs(db, schedule)[0].model_id == "hosted"


@pytest.mark.anyio
async def test_never_evicts_a_model_answering_someone(db, three, script):
    """Every slot busy everywhere: nothing is taken away; the run waits."""
    big, small, hosted = three
    schedule = _make(db, model_id="local-big")
    script.busy[big.id] = 1
    script.busy[hosted.id] = 2
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "waiting"
    assert run.waiting_for == "model"
    assert run.message_id == ""
    assert "Big" in run.error and "Hosted" in run.error


@pytest.mark.anyio
async def test_a_loading_server_is_not_free(db, three, script, monkeypatch):
    big, small, hosted = three

    async def swap(connection):
        return [{"model": "local-small", "state": "starting"}]

    monkeypatch.setattr(models_service, "_swap", swap)
    schedule = _make(db, model_id="local-big")
    await _sweep(script, T0)
    assert _runs(db, schedule)[0].model_id == "hosted"


@pytest.mark.anyio
async def test_a_person_narrows_the_list(db, three, script):
    owner = _owner(db)
    owner.settings_json = {**(owner.settings_json or {}), "schedule_models": ["hosted"]}
    db.commit()
    assert models_service.scheduling_list(db, owner) == ["hosted"]
    schedule = _make(db)  # no first choice: the person's list decides
    await _sweep(script, T0)
    assert _runs(db, schedule)[0].model_id == "hosted"


# --- Waiting, retrying, skipping ------------------------------------------------------------
@pytest.mark.anyio
async def test_waiting_for_a_model_retries_at_2_5_10_15_then_every_15_then_skips(
    db, three, script
):
    big, small, hosted = three
    schedule = _make(db, model_id="local-big")  # hourly: a 30-minute window
    script.busy[big.id] = 1
    script.busy[hosted.id] = 2
    await _sweep(script, T0)
    tries = []
    for _ in range(10):
        (run,) = _runs(db, schedule)
        if run.status != "waiting":
            break
        retry = run.retry_at.replace(tzinfo=UTC)
        tries.append(int((retry - T0).total_seconds() // 60))
        await _sweep(script, retry)
    assert tries == [2, 5, 10, 15, 30]
    (run,) = _runs(db, schedule)
    assert run.status == "skipped"
    assert run.error_class == "no_model"
    # A skipped run is told, as a failure is.
    assert script.told and script.told[-1]["failed"]


@pytest.mark.anyio
async def test_a_waiting_run_runs_once_a_model_is_free(db, three, script):
    big, small, hosted = three
    schedule = _make(db, model_id="local-big")
    script.busy[big.id] = 1
    script.busy[hosted.id] = 2
    await _sweep(script, T0)
    script.busy.clear()
    await _sweep(script, T0 + timedelta(minutes=2))
    (run,) = _runs(db, schedule)
    assert (run.status, run.model_id, run.attempts) == ("ok", "local-big", 2)


@pytest.mark.anyio
async def test_a_model_never_reached_is_retried_at_5_15_30_then_fails(db, three, script):
    schedule = _make(db, model_id="hosted")
    script.replies = [{"error": "Could not reach it.", "unreachable": True}] * 4
    await _sweep(script, T0)
    waits = []
    for _ in range(5):
        (run,) = _runs(db, schedule)
        if run.status != "waiting":
            break
        assert run.waiting_for == "retry"
        retry = run.retry_at.replace(tzinfo=UTC)
        waits.append(int((retry - script.now).total_seconds() // 60))
        await _sweep(script, retry)
    assert waits == [5, 15, 30]
    (run,) = _runs(db, schedule)
    assert (run.status, run.error_class) == ("failed", "unreachable")
    # One run, one chat, one reply: a retry asks again in place.
    replies = db.scalars(
        select(Message).where(Message.chat_id == run.chat_id, Message.role == ROLE_ASSISTANT)
    ).all()
    assert len(replies) == 1


@pytest.mark.anyio
async def test_a_retry_that_gets_through_is_ok(db, three, script):
    schedule = _make(db, model_id="hosted")
    script.replies = [{"error": "502", "status": 502}, {"content": "All green."}]
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    await _sweep(script, run.retry_at.replace(tzinfo=UTC))
    (run,) = _runs(db, schedule)
    assert (run.status, run.retries, run.preview) == ("ok", 1, "All green.")


@pytest.mark.anyio
async def test_a_429_holds_until_its_retry_after(db, three, script):
    schedule = _make(db, model_id="hosted")
    script.replies = [{"error": "Rate limited.", "status": 429, "retry_after": 600}]
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.waiting_for == "rate_limit"
    assert run.retry_at.replace(tzinfo=UTC) == T0 + timedelta(minutes=10)


@pytest.mark.anyio
async def test_a_real_failure_is_not_retried(db, three, script):
    schedule = _make(db, model_id="hosted")
    script.replies = [{"error": "The API key was rejected.", "status": 401}]
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert (run.status, run.error_class) == ("failed", "model")


@pytest.mark.anyio
async def test_a_failure_after_words_were_written_is_not_retried(db, three, script):
    schedule = _make(db, model_id="hosted")
    script.replies = [{"content": "Half an ans", "error": "Lost.", "unreachable": True}]
    await _sweep(script, T0)
    assert _runs(db, schedule)[0].status == "failed"


@pytest.mark.anyio
async def test_a_reply_that_streams_nothing_for_ten_minutes_is_stopped(
    db, three, script, monkeypatch
):
    schedule = _make(db, model_id="hosted")
    stuck = FakeGeneration(done=False, version=7)
    stopped: list[str] = []

    async def wake(chat_id, content, *, model_id="", connection_id=None):
        from lembas.db.session import session_scope

        with session_scope() as session:
            message = Message(chat_id=chat_id, role=ROLE_ASSISTANT, complete=False)
            session.add(message)
            session.commit()
            script.generations[message.id] = stuck
            return message.id

    ticks = iter(range(0, 10_000, 120))
    monkeypatch.setattr(wake_service, "wake_chat", wake)
    monkeypatch.setattr(runner, "_monotonic", lambda: float(next(ticks)))

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(runner, "_sleep", no_wait)
    monkeypatch.setattr(
        generation_service, "request_stop", lambda mid: stopped.append(mid) or True
    )
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert stopped == [run.message_id]
    assert (run.status, run.error_class) == ("failed", "timeout")


# --- The catch-up window ---------------------------------------------------------------------
@pytest.mark.anyio
async def test_inside_the_window_a_run_is_late(db, three, script):
    schedule = _make(db, model_id="hosted")  # hourly: 30 minutes
    await _sweep(script, T0 + timedelta(minutes=20))
    (run,) = _runs(db, schedule)
    assert (run.timing, run.missed, run.status) == ("late", 0, "ok")


@pytest.mark.anyio
async def test_beyond_the_window_one_catch_up_run_records_what_was_missed(db, three, script):
    schedule = _make(db, model_id="hosted")
    await _sweep(script, T0 + timedelta(hours=5, minutes=40))
    (run,) = _runs(db, schedule)
    # 09:00 .. 14:00 came due; 14:00 is the latest, 40 minutes late (> 30).
    assert (run.timing, run.missed, run.status) == ("catch_up", 5, "ok")
    assert run.due_at.replace(tzinfo=UTC) == T0 + timedelta(hours=5)
    db.refresh(schedule)
    assert schedule.next_fire_at.replace(tzinfo=UTC) == T0 + timedelta(hours=6)


@pytest.mark.anyio
async def test_beyond_the_window_a_skip_schedule_runs_nothing(db, three, script):
    schedule = _make(db, model_id="hosted", catch_up="skip")
    await _sweep(script, T0 + timedelta(hours=2, minutes=40))
    (run,) = _runs(db, schedule)
    assert (run.status, run.missed, run.message_id) == ("missed", 3, "")
    assert script.turns == []


def test_the_window_is_half_the_period_clamped():
    zone = UTC
    every = lambda minutes: Schedule(  # noqa: E731
        rule_json=rule_service.validate({"start": T0.isoformat(), "every": {"minutes": minutes}})
    )
    assert runs.grace(every(1), now=T0, zone=zone) == timedelta(minutes=2)
    assert runs.grace(every(60), now=T0, zone=zone) == timedelta(minutes=30)
    assert runs.grace(every(60 * 24), now=T0, zone=zone) == timedelta(hours=2)
    once = Schedule(rule_json=rule_service.validate({"start": T0.isoformat()}))
    assert runs.grace(once, now=T0, zone=zone) == timedelta(minutes=2)
    own = every(60 * 24)
    own.grace_minutes = 300
    assert runs.grace(own, now=T0, zone=zone) == timedelta(minutes=300)


# --- Fresh or continuing ------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fresh_runs_get_a_chat_each_and_the_last_result(db, three, script):
    schedule = _make(db, model_id="hosted")
    script.replies = [{"content": "The build is red: test_x."}, {"content": "Still red."}]
    await _sweep(script, T0)
    await _sweep(script, T0 + timedelta(hours=1))
    first, second = _runs(db, schedule)
    assert first.chat_id != second.chat_id
    assert "Last time" in script.turns[1]
    assert "The build is red: test_x." in script.turns[1]
    assert "Last time" not in script.turns[0]
    for run in (first, second):
        chat = db.get(Chat, run.chat_id)
        assert chat.kind == "task" and chat.unattended
        assert schedules_service.for_chat(db, chat).id == schedule.id


@pytest.mark.anyio
async def test_a_silent_result_is_not_the_previous_result(db, three, script):
    _make(db, model_id="hosted")
    script.replies = [{"content": "Red."}, {"content": "[SILENT]"}, {"content": "Green."}]
    for hour in range(3):
        await _sweep(script, T0 + timedelta(hours=hour))
    assert "Red." in script.turns[2]


@pytest.mark.anyio
async def test_continue_runs_share_one_chat(db, three, script):
    schedule = _make(db, model_id="hosted", mode="continue")
    await _sweep(script, T0)
    await _sweep(script, T0 + timedelta(hours=1))
    first, second = _runs(db, schedule)
    assert first.chat_id == second.chat_id == schedule.chat_id
    assert "Last time" not in script.turns[1]


# --- Telling somebody ------------------------------------------------------------------------
@pytest.mark.anyio
async def test_silent_is_recorded_and_not_told(db, three, script):
    schedule = _make(db, model_id="hosted")
    script.replies = [{"content": "Nothing new.\n[SILENT]"}]
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "silent"
    assert script.told == []
    assert notify.HINT in script.turns[0]


@pytest.mark.anyio
async def test_a_result_that_matters_is_told(db, three, script):
    schedule = _make(db, model_id="hosted")
    script.replies = [{"content": "The build broke."}]
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert script.told == [{"run": run.id, "status": "ok", "failed": False, "ask_pause": False}]
    assert run.notified


@pytest.mark.anyio
async def test_never_is_never_told(db, three, script):
    _make(db, model_id="hosted", notify="never")
    script.replies = [{"content": "The build broke."}, {"error": "x", "status": 400}]
    await _sweep(script, T0)
    await _sweep(script, T0 + timedelta(hours=1))
    assert script.told == []
    assert notify.HINT not in script.turns[0]


@pytest.mark.anyio
async def test_a_one_shot_defaults_to_always(db, three, script):
    schedule = _make(db, model_id="hosted", rule={"start": "2099-01-01T00:00:00Z"})
    schedule.rule_json = rule_service.validate({"start": T0.isoformat()})
    db.commit()
    assert schedule.notify == "always"
    await _sweep(script, T0)
    assert len(script.told) == 1


@pytest.mark.anyio
async def test_failures_are_told_first_then_every_six_hours_and_the_third_asks(
    db, three, script
):
    schedule = _make(
        db, model_id="hosted", rule={"start": T0.isoformat(), "every": {"minutes": 10}}
    )
    script.replies = [{"error": "Model 7 crashed (id 123).", "status": 400}] * 4 + [
        {"error": "Model 9 crashed (id 456).", "status": 400}
    ]
    for step in range(5):
        await _sweep(script, T0 + timedelta(minutes=10 * step))
    told = [(t["failed"], t["ask_pause"]) for t in script.told]
    # 1st: told. 2nd: same error, quiet. 3rd: asks about pausing. 4th, 5th
    # (the numbers differ, the signature does not): quiet.
    assert told == [(True, False), (True, True)]
    db.refresh(schedule)
    assert schedule.failure_streak == 5


@pytest.mark.anyio
async def test_a_success_resets_the_streak(db, three, script):
    schedule = _make(db, model_id="hosted")
    script.replies = [{"error": "x", "status": 400}, {"content": "Fine."}]
    await _sweep(script, T0)
    await _sweep(script, T0 + timedelta(hours=1))
    db.refresh(schedule)
    assert schedule.failure_streak == 0


def test_silent_sentinel_reading():
    assert notify.is_silent("[SILENT]")
    assert notify.is_silent("  [silent].  ")
    assert notify.is_silent("Nothing changed.\n\n[SILENT]")
    assert notify.is_silent("[SILENT]\nnothing to add")
    assert not notify.is_silent("The word [SILENT] in the middle of a line.")
    assert not notify.is_silent("")
    assert notify.strip_sentinel("[SILENT]\nnothing to add") == "nothing to add"


def test_failure_decision_forgets_old_signatures():
    tell, ask, notes = notify.failure_decision({}, sig="a", streak=1, now=T0)
    assert tell and not ask
    later = T0 + timedelta(hours=1)
    tell, ask, notes = notify.failure_decision(notes, sig="a", streak=2, now=later)
    assert not tell
    later = T0 + timedelta(hours=7)
    tell, ask, notes = notify.failure_decision(notes, sig="a", streak=4, now=later)
    assert tell and not ask
    _, _, notes = notify.failure_decision(notes, sig="b", streak=5, now=T0 + timedelta(days=3))
    assert set(notes) == {"b"}


def test_the_push_payload_carries_ids_actions_and_the_notification():
    body = notify.payload(
        schedule_id="s", run_id="r", chat_id="c", status="failed", failed=True, ask_pause=True
    )
    assert body == {
        "schedule_id": "s", "run_id": "r", "chat_id": "c", "status": "failed",
        "visibility": "private", "actions": ["open", "run_again", "pause"], "ask": "pause",
    }
    told = {"title": "t"}
    assert notify.payload(
        schedule_id="s", run_id="r", chat_id="c", status="ok", failed=False, ask_pause=False,
        told=told,
    )["notification"] is told


def test_the_notification_has_a_full_and_a_public_version():
    """Unlocked: the task's name and the result's first lines. Lock screen:
    the name and how it ended, nothing the run wrote (owner's plan, 2026-11-10)."""
    schedule = SimpleNamespace(title="  Morning   build  ")
    run = SimpleNamespace(preview="All green.\nTwo warnings.", error="")
    told = notify.notification(schedule, run, failed=False, ask_pause=False, translate=str)
    assert told["title"] == "Morning build"
    assert told["body"] == "All green. Two warnings."
    assert told["public"] == {"title": "Morning build", "body": "Finished"}
    assert told["actions"] == ["open", "run_again"]
    assert told["labels"] == {"open": "Open", "run_again": "Run again"}
    failed = SimpleNamespace(preview="", error="Out of memory.\nTraceback …")
    told = notify.notification(schedule, failed, failed=True, ask_pause=False, translate=str)
    assert told["body"] == "Out of memory." and told["public"]["body"] == "Failed"
    assert told["actions"] == ["open", "run_again", "pause"] and told["ask"] == ""
    asked = notify.notification(schedule, failed, failed=True, ask_pause=True, translate=str)
    assert asked["body"] == "Failed 3 times in a row. Pause it?" and asked["ask"] == "pause"
    nameless = notify.notification(
        SimpleNamespace(title=""), SimpleNamespace(preview="x" * 900, error=""),
        failed=False, ask_pause=False, translate=str,
    )
    assert nameless["title"] == "Scheduled task" and len(nameless["body"]) == notify.BODY_MAX


@pytest.mark.anyio
async def test_the_notify_decision_reaches_web_push_and_unifiedpush(db, registered, monkeypatch):
    """One decision, both channels: a browser's registration (while Web Push is
    on) and an app's (UnifiedPush, always). The payload is a fixed sentence, the
    ids and a deep link to the run -- nothing the run wrote."""
    from lembas.db.models import PushSubscription
    from lembas.services import push

    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    settings_store.update(db, {"web_push_enabled": True}, key=settings_store.GENERAL)
    owner = _owner(db)
    schedule = schedules_service.create(
        db, owner=owner, title="Secret project", instruction="Check the build.",
        rule={"start": T0.isoformat(), "every": {"hours": 1}},
    )
    run = ScheduleRun(
        schedule_id=schedule.id, user_id=owner.id, status="ok", chat_id=schedule.chat_id,
        preview="The private result the model wrote.",
    )
    db.add(run)
    for endpoint, token in (
        ("https://push.example/browser", None), ("https://up.example/app", "t" * 32),
    ):
        db.add(PushSubscription(
            user_id=owner.id, endpoint=endpoint, p256dh="k", auth_secret="a", token_id=token,
        ))
    db.commit()

    sent: list[tuple[str, dict]] = []

    async def send_one(db_, subscription, payload):
        sent.append((subscription.endpoint, payload))
        return True

    monkeypatch.setattr(push, "send_one", send_one)
    assert await notify.announce(db, owner, schedule=schedule, run=run, failed=False)
    assert sorted(endpoint for endpoint, _ in sent) == [
        "https://push.example/browser", "https://up.example/app",
    ]
    for _, payload in sent:
        assert payload["url"] == f"/scheduled/{schedule.id}?run={run.id}"
        assert payload["schedule_id"] == schedule.id and payload["run_id"] == run.id
        assert payload["visibility"] == "private"
        # The fixed sentence for an app that reads only title and body; the
        # result only inside `notification`, beside a public version without it.
        assert "private result" not in payload["title"] + payload["body"]
        told = payload["notification"]
        assert told["title"] == "Secret project"
        assert told["body"] == "The private result the model wrote."
        assert told["public"] == {"title": "Secret project", "body": "Finished"}
    # Kept on the run, for the apps that follow the `schedules` event.
    assert run.notification_json == sent[0][1]["notification"]

    # Web Push off: the app is still told, the browser is not.
    sent.clear()
    settings_store.update(db, {"web_push_enabled": False}, key=settings_store.GENERAL)
    assert await notify.announce(db, owner, schedule=schedule, run=run, failed=True)
    assert [endpoint for endpoint, _ in sent] == ["https://up.example/app"]
    assert sent[0][1]["actions"] == ["open", "run_again", "pause"]


# --- Run settings ----------------------------------------------------------------------------
@pytest.mark.anyio
async def test_settings_go_onto_the_run_chat(db, three, script):
    big, small, hosted = three
    hosted.reasoning_efforts = ["low", "high"]
    db.commit()
    schedule = _make(db, model_id="hosted", effort="high", tools=["search", "fetch", "image"])
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    chat = db.get(Chat, run.chat_id)
    assert chat.params_json.get("reasoning_effort") == "high"
    off = chat.scope_json["families"]
    assert "search" not in off and "fetch" not in off and "image" not in off
    for family in ("files", "memory", "schedule", "ask", "mcp", "custom"):
        assert off.get(family) is False


def test_scheduling_tools_are_never_offered_inside_a_run(db, three):
    from lembas.services.tools import resolve_tools

    big, small, hosted = three
    settings_store.update(db, {"default_permissions": {"schedule.use": True}})
    owner = _owner(db)
    task = Chat(user_id=owner.id, kind="task", unattended=True, model_id="hosted",
                connection_id=hosted.connection_id)
    plain = Chat(user_id=owner.id, model_id="hosted", connection_id=hosted.connection_id)
    db.add_all([task, plain])
    db.commit()
    in_run = {d.name for d in resolve_tools(db, task, owner).defs}
    in_chat = {d.name for d in resolve_tools(db, plain, owner).defs}
    assert "schedule_create" in in_chat
    assert not {"schedule_create", "schedule_update", "schedule_list"} & in_run


def test_apply_settings_is_total():
    schedule = Schedule(rule_json={})
    schedules_service.apply_settings(
        schedule,
        {"mode": "sideways", "notify": 3, "catch_up": None, "grace_minutes": "x",
         "agents": 99, "tools": "search", "effort": "x" * 40},
    )
    assert schedule.mode == "fresh"
    assert schedule.notify == ""
    assert schedule.catch_up == "one"
    assert schedule.grace_minutes is None
    assert schedule.agents == schedules_service.MAX_AGENTS
    assert schedule.tools_json == []
    assert len(schedule.effort) == 16


# --- History ------------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_old_runs_and_their_fresh_chats_are_swept(db, three, script):
    schedule = _make(db, model_id="hosted")
    await _sweep(script, T0)
    await _sweep(script, T0 + timedelta(hours=1))
    old, recent = _runs(db, schedule)
    old.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    db.commit()
    assert runs.sweep(db, now=T0 + timedelta(days=1)) == 1
    db.expire_all()
    assert db.get(ScheduleRun, old.id) is None
    assert db.get(Chat, old.chat_id) is None
    assert db.get(Chat, recent.chat_id) is not None


@pytest.mark.anyio
async def test_a_paged_history(db, three, script):
    schedule = _make(db, model_id="hosted")
    for hour in range(5):
        await _sweep(script, T0 + timedelta(hours=hour))
    page, more = runs.history(db, schedule, limit=3)
    assert len(page) == 3 and more
    rest, more = runs.history(db, schedule, limit=3, before=page[-1].id)
    assert len(rest) == 2 and not more


# --- Monitor mode ------------------------------------------------------------------------------
def _watching(monkeypatch, pages: list):
    """`monitor.fetch_url` answering from `pages` in order: text, or an
    exception to raise. Returns the list of addresses looked at."""
    from lembas.services.schedule import monitor

    looked: list[str] = []

    async def fetch_url(url, *, allow_private=False):
        looked.append(url)
        page = pages.pop(0)
        if isinstance(page, Exception):
            raise page
        raw = page.encode()
        return monitor.Look(page, monitor.digest(raw), len(raw))

    monkeypatch.setattr(monitor, "fetch_url", fetch_url)
    return looked


@pytest.mark.anyio
async def test_a_monitor_runs_the_model_only_when_the_page_changed(
    db, three, script, monkeypatch
):
    from lembas.services.schedule import monitor

    looked = _watching(monkeypatch, ["Price: 10", "Price: 10", "Price: 12"])
    schedule = _make(db, model_id="hosted", notify="always", watch="https://shop.example/item")
    assert (schedule.watch_kind, schedule.watch_target) == ("url", "https://shop.example/item")

    # First look: the model runs, told it is the first look.
    await _sweep(script, T0)
    # Same page: recorded unchanged, no model, nobody told.
    await _sweep(script, T0 + timedelta(hours=1))
    # Changed: the model runs with the diff.
    await _sweep(script, T0 + timedelta(hours=2))

    first, same, changed = _runs(db, schedule)
    assert looked == ["https://shop.example/item"] * 3
    assert [first.status, same.status, changed.status] == ["ok", "unchanged", "ok"]
    assert len(script.turns) == 2
    assert "first look" in script.turns[0] and "<watched>\nPrice: 10\n</watched>" in script.turns[0]
    assert "-Price: 10" in script.turns[1] and "+Price: 12" in script.turns[1]
    assert [t["run"] for t in script.told] == [first.id, changed.id]
    assert same.chat_id in (None, "") and not same.notified
    assert runs.watch_dict(same.watch_json)["changed"] is False
    assert runs.watch_dict(changed.watch_json)["changed"] is True
    assert "block" not in runs.watch_dict(changed.watch_json)
    db.refresh(schedule)
    assert schedule.watch_hash == monitor.digest(b"Price: 12")
    assert runs.schedule_dict(db, schedule, owner=_owner(db))["watch"]["url"] == (
        "https://shop.example/item"
    )


@pytest.mark.anyio
async def test_a_failed_look_fails_the_run_and_is_never_a_change(db, three, script, monkeypatch):
    from lembas.services.schedule import monitor

    _watching(monkeypatch, ["v1", monitor.WatchError("example answered HTTP 503."), "v1"])
    schedule = _make(db, model_id="hosted", watch="https://status.example/")
    await _sweep(script, T0)
    await _sweep(script, T0 + timedelta(hours=1))
    await _sweep(script, T0 + timedelta(hours=2))
    first, broken, after = _runs(db, schedule)
    assert broken.status == "failed" and broken.error_class == runner.ERROR_WATCH
    assert "HTTP 503" in broken.error
    # The kept hash was left alone: the same page after the failure is unchanged.
    assert after.status == "unchanged"
    assert len(script.turns) == 1


def test_a_new_address_starts_afresh_and_bad_ones_are_refused(db, three, script):
    schedule = _make(db, model_id="hosted", watch="https://a.example/")
    schedule.watch_hash, schedule.watch_last = "abc", "old"
    schedules_service.apply_settings(schedule, {"watch": "https://a.example/"})
    assert schedule.watch_hash == "abc"
    schedules_service.apply_settings(schedule, {"watch": {"kind": "url", "target": "https://b.example/"}})
    assert (schedule.watch_target, schedule.watch_hash, schedule.watch_last) == (
        "https://b.example/", "", ""
    )
    # A command is watched on a linked computer: refused for a server run.
    for bad in ("ftp://a.example/", {"kind": "command", "command": "ls"}, {"kind": "file"}):
        with pytest.raises(schedules_service.ScheduleError):
            schedules_service.apply_settings(schedule, {"watch": bad})
    schedules_service.apply_settings(schedule, {"watch": ""})
    assert (schedule.watch_kind, schedule.watch_target) == ("", "")


@pytest.mark.anyio
async def test_the_look_goes_through_the_ssrf_guard_and_is_bounded(mock_http):
    import httpx

    from lembas.services.schedule import monitor

    # A private address is refused before anything is sent.
    with pytest.raises(monitor.WatchError):
        await monitor.fetch_url("http://127.0.0.1:9/")

    # A redirect to one is refused at the hop.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:9/secret"})
        return httpx.Response(200, content=b"x" * (monitor.MAX_BYTES + 10))

    mock_http(handler)
    look = await monitor.fetch_url("http://192.0.2.9/big", allow_private=True)
    assert look.truncated and look.size == monitor.MAX_BYTES
    assert look.digest == monitor.digest(b"x" * monitor.MAX_BYTES)
    with pytest.raises(monitor.WatchError):
        # allow_private=False on the hop: the first address is a public literal.
        await monitor.fetch_url("http://93.184.215.14/hop")
