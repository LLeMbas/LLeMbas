"""The ticker and the runner: claiming a firing, and not doing it twice.

`rule.py` is tested on its own in `test_schedule_rule.py`. What is tested here
is everything that goes wrong *around* a correct rule — which is the half that
fails silently. Nothing in this file needs an endpoint: the firing itself is
stubbed, because what is being checked is the bookkeeping, not the reply.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import (
    ROLE_ASSISTANT,
    TARGET_CHAT,
    TARGET_REPORT,
    Chat,
    Message,
    Schedule,
    User,
)
from lembas.services import settings_store
from lembas.services.schedule import clock, runner, ticker
from lembas.services.schedule import rule as rule_service


@pytest.fixture(autouse=True)
def scheduling_on(db):
    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    return None


def _user(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _schedule(db, *, chat_id: str = "", **kwargs) -> Schedule:
    fields = {
        "user_id": _user(db).id,
        "title": "A schedule",
        "instruction": "Do the thing.",
        "rule_json": rule_service.validate(
            {"start": "2026-01-01T00:00:00Z", "every": {"hours": 1}}
        ),
        "target": TARGET_CHAT,
        "chat_id": chat_id,
        "enabled": True,
        "next_fire_at": datetime(2026, 1, 1, tzinfo=UTC),
        **kwargs,
    }
    schedule = Schedule(**fields)
    db.add(schedule)
    db.commit()
    return schedule


# --- Claiming -------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_firing_that_raises_still_moves_the_schedule_on(
    client: TestClient, db, registered, make_chat, monkeypatch
):
    """The single most important property here.

    Claim, commit, *then* fire. The other order is a hot loop: a schedule whose
    firing fails is retried every tick for ever, against whatever it was that
    failed — and the only symptom is load.
    """
    schedule = _schedule(db, chat_id=make_chat())

    async def explode(schedule_id, **kwargs):
        raise RuntimeError("the endpoint is down")

    monkeypatch.setattr(runner, "fire", explode)

    fired = await ticker.sweep(now=datetime(2026, 1, 1, 0, 30, tzinfo=UTC))
    assert fired == 1
    for task in list(ticker._FIRING):
        with pytest.raises(RuntimeError):
            await task

    db.refresh(schedule)
    assert schedule.next_fire_at is not None
    assert schedule.fired_count == 1


@pytest.mark.anyio
async def test_two_overlapping_sweeps_fire_once(
    client: TestClient, db, registered, make_chat, monkeypatch
):
    """A sweep can take minutes — a firing awaits a model. The lock is what
    stops the next tick claiming the same row again."""
    schedule = _schedule(db, chat_id=make_chat())
    calls: list[str] = []

    async def record(schedule_id, **kwargs):
        calls.append(schedule_id)

    monkeypatch.setattr(runner, "fire", record)

    now = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
    await ticker.sweep(now=now)
    await ticker.sweep(now=now)
    for task in list(ticker._FIRING):
        await task

    assert calls == [schedule.id]


@pytest.mark.anyio
async def test_exhaustion_disables_rather_than_looping(
    client: TestClient, db, registered, make_chat, monkeypatch
):
    """"Five times" meaning "for ever" is the failure. A rule with nothing left
    switches the row off, so it stops being examined at all."""
    monkeypatch.setattr(runner, "fire", lambda *a, **k: _noop())
    schedule = _schedule(
        db,
        chat_id=make_chat(),
        rule_json=rule_service.validate(
            {"start": "2026-01-01T00:00:00Z", "every": {"hours": 1}, "count": 1}
        ),
    )

    await ticker.sweep(now=datetime(2026, 1, 1, 0, 30, tzinfo=UTC))
    for task in list(ticker._FIRING):
        await task

    db.refresh(schedule)
    assert schedule.fired_count == 1
    assert schedule.enabled is False
    assert schedule.next_fire_at is None


@pytest.mark.anyio
async def test_a_missed_week_fires_once(
    client: TestClient, db, registered, make_chat, monkeypatch
):
    """The host was off. It comes back owing one run, not a hundred and
    sixty-eight — and the next one is measured from now."""
    calls: list[str] = []

    async def record(schedule_id, **kwargs):
        calls.append(schedule_id)

    monkeypatch.setattr(runner, "fire", record)
    schedule = _schedule(db, chat_id=make_chat())

    now = datetime(2026, 1, 8, 0, 30, tzinfo=UTC)
    await ticker.sweep(now=now)
    for task in list(ticker._FIRING):
        await task

    assert len(calls) == 1
    db.refresh(schedule)
    assert schedule.fired_count == 1
    assert schedule.next_fire_at.replace(tzinfo=UTC) > now


@pytest.mark.anyio
async def test_one_unreadable_row_does_not_stop_the_sweep(
    client: TestClient, db, registered, make_chat, monkeypatch
):
    """A ticker that dies on one bad row stops every schedule on the instance,
    and nothing anywhere says so."""
    calls: list[str] = []

    async def record(schedule_id, **kwargs):
        calls.append(schedule_id)

    monkeypatch.setattr(runner, "fire", record)

    broken = _schedule(db, chat_id=make_chat(), title="Broken")
    healthy = _schedule(db, chat_id=make_chat(), title="Healthy")

    real_advance = rule_service.advance

    def selective(rule, **kwargs):
        if rule.get("_broken"):
            raise ValueError("unreadable")
        return real_advance(rule, **kwargs)

    broken.rule_json = {**broken.rule_json, "_broken": True}
    db.commit()
    monkeypatch.setattr(rule_service, "advance", selective)

    await ticker.sweep(now=datetime(2026, 1, 1, 0, 30, tzinfo=UTC))
    for task in list(ticker._FIRING):
        await task

    assert calls == [healthy.id]
    db.refresh(broken)
    assert broken.enabled is False
    assert broken.last_error


def test_a_deleted_account_takes_its_schedules_with_it(
    client: TestClient, db, registered, make_chat
):
    """`user_id` is a real ForeignKey with CASCADE — unlike `chat_id`, which is
    a plain id because `migrations.py` cannot add a REFERENCES clause to a table
    that already exists. So an orphaned schedule cannot be reached at all, and
    the owner check in the sweep is a guard rather than a path.

    Worth pinning as the *reason* that guard looks unreachable: somebody
    removing it would be right about today and wrong the moment the column
    convention changes.
    """
    _schedule(db, chat_id=make_chat())
    owner = _user(db)

    db.delete(owner)
    db.commit()
    # The cascade is enforced by SQLite, not by an ORM relationship, so the
    # session's identity map still holds the row it was told about. Ask the
    # database rather than the cache.
    db.expunge_all()

    assert db.scalars(select(Schedule)).all() == []


@pytest.mark.anyio
async def test_nothing_fires_while_scheduling_is_switched_off(
    client: TestClient, db, registered, make_chat, monkeypatch
):
    """The instance switch is a switch, not a suggestion. Checked in the sweep
    rather than at the routes, so a row created while it was on does not go on
    firing after it is turned off."""
    calls: list[str] = []
    monkeypatch.setattr(runner, "fire", lambda sid, **k: calls.append(sid) or _noop())
    _schedule(db, chat_id=make_chat())
    settings_store.update(db, {"enabled": False}, key=settings_store.SCHEDULES)

    assert await ticker.sweep(now=datetime(2026, 1, 1, 0, 30, tzinfo=UTC)) == 0
    assert calls == []


# --- The runner -----------------------------------------------------------------
async def _noop() -> None:
    return None


@pytest.mark.anyio
async def test_a_firing_writes_a_machine_turn_and_starts_a_reply(
    client: TestClient, db, registered, make_chat
):
    """The role stays `user` — `build_messages` needs one there and `_inject`
    sends a queued turn verbatim. `machine` is what stops the transcript
    claiming the reader typed it."""
    chat_id = make_chat()
    schedule = _schedule(db, chat_id=chat_id)

    await runner.fire(schedule.id)

    turns = list(
        db.scalars(select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at))
    )
    assert turns[0].role == "user"
    assert turns[0].machine is True
    assert "started by a schedule" in turns[0].content
    assert "Do the thing." in turns[0].content
    # And a reply was opened for it.
    assert turns[-1].role == ROLE_ASSISTANT
    assert turns[-1].complete is False


@pytest.mark.anyio
async def test_a_deleted_chat_switches_the_schedule_off(
    client: TestClient, db, registered, make_chat
):
    """Rather than firing into nothing on every tick from now on — which is a
    schedule that looks alive and produces nothing."""
    schedule = _schedule(db, chat_id="nosuchchat")

    await runner.fire(schedule.id)

    db.refresh(schedule)
    assert schedule.enabled is False
    assert "no longer exists" in schedule.last_error


@pytest.mark.anyio
async def test_a_backlog_skips_rather_than_queues_for_ever(
    client: TestClient, db, registered, make_chat
):
    """`_drain` takes one queued turn per reply, so a schedule firing faster
    than its chat can answer would build a backlog that outlives the day that
    caused it."""
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    for index in range(5):
        db.add(
            Message(chat_id=chat.id, role="user", content=f"waiting {index}", queued=True)
        )
    db.commit()
    schedule = _schedule(db, chat_id=chat_id)

    await runner.fire(schedule.id)

    db.refresh(schedule)
    assert "previous run was still going" in schedule.last_error
    assert schedule.claimed_at is None


@pytest.mark.anyio
async def test_run_now_does_not_consume_the_scheduled_run(
    client: TestClient, db, registered, make_chat
):
    """Testing a schedule must not skip the run it was testing. Advancing is the
    ticker's job and nothing else's."""
    schedule = _schedule(db, chat_id=make_chat())
    before = clock.as_utc(schedule.next_fire_at)
    fired_before = schedule.fired_count

    await runner.run_now(schedule.id)

    db.refresh(schedule)
    # Through `as_utc` on both sides: a row read back from SQLite is naive while
    # one still in the session keeps its tzinfo, and comparing the two raises.
    assert clock.as_utc(schedule.next_fire_at) == before
    assert schedule.fired_count == fired_before


def _finished(db, chat_id: str, text: str) -> Message:
    """A reply that has already been written.

    `deliver` is tested against this rather than against the output of
    `runner.fire`, because `fire` starts a real generation — which, with no
    endpoint configured, races the test to write the same row. The delivery's
    job begins at a finished message, so that is what it is handed.
    """
    message = Message(
        chat_id=chat_id, role=ROLE_ASSISTANT, content=text, complete=True, model_id="m"
    )
    db.add(message)
    db.commit()
    return message


def _reports(db, owner) -> dict[str, str]:
    """The files in the owner's reports directory, name -> text."""
    from lembas.services import fs

    v = fs.view(db, owner)
    home = fs.home_of(db, owner).path
    db.expire_all()
    try:
        entries = fs.list_dir(v, f"{home}/reports")
    except fs.FsError:
        return {}
    return {e.name: fs.read(v, e.path).decode() for e in entries}


@pytest.mark.anyio
async def test_a_report_target_files_the_reply(
    client: TestClient, db, registered, make_chat
):
    """Reports are files since 2.0: the reply is written as Markdown in the
    owner's `reports/` directory, named by when the run began."""
    chat_id = make_chat()
    schedule = _schedule(db, chat_id=chat_id, target=TARGET_REPORT, title="Daily news")
    assistant = _finished(db, chat_id, "Three things happened.")

    await runner.deliver(
        schedule.id, assistant.id, since=datetime(2020, 1, 1, 9, 30, tzinfo=UTC)
    )

    assert _reports(db, _user(db)) == {
        "2020-01-01 0930 Daily news.md": "# Daily news\n\nThree things happened.\n"
    }


@pytest.mark.anyio
async def test_a_run_that_produced_nothing_still_files_something(
    client: TestClient, db, registered, make_chat
):
    """A scheduled report that silently did not appear is indistinguishable
    from a schedule that never fired. So the failure is filed as a report."""
    chat_id = make_chat()
    schedule = _schedule(db, chat_id=chat_id, target=TARGET_REPORT, title="Daily news")
    broken = Message(chat_id=chat_id, role=ROLE_ASSISTANT, content="", complete=True,
                     error="the endpoint refused")
    db.add(broken)
    db.commit()

    await runner.deliver(schedule.id, broken.id, since=datetime(2020, 1, 1, tzinfo=UTC))

    filed = _reports(db, _user(db))
    assert len(filed) == 1
    assert "did not produce a reply" in next(iter(filed.values()))


@pytest.mark.anyio
async def test_two_runs_in_one_minute_file_two_reports(
    client: TestClient, db, registered, make_chat
):
    chat_id = make_chat()
    schedule = _schedule(db, chat_id=chat_id, target=TARGET_REPORT, title="A/b: c")
    when = datetime(2020, 1, 1, tzinfo=UTC)
    await runner.deliver(schedule.id, _finished(db, chat_id, "One.").id, since=when)
    await runner.deliver(schedule.id, _finished(db, chat_id, "Two.").id, since=when)
    assert sorted(_reports(db, _user(db))) == [
        "2020-01-01 0000 A b c (2).md",
        "2020-01-01 0000 A b c.md",
    ]


# --- Lifecycle -------------------------------------------------------------------
def test_starting_twice_makes_one_ticker(client: TestClient, registered):
    """Two tickers in one process fires everything twice, which is the failure
    two workers would cause and the reason this is idempotent."""
    ticker.start()
    first = ticker._TICKER
    ticker.start()
    assert ticker._TICKER is first


def test_release_claims_clears_an_interrupted_run(client: TestClient, db, registered):
    """A restart abandons a firing in flight. Without this the row keeps its
    claim stamp for ever and reads as permanently running."""
    schedule = _schedule(db, claimed_at=datetime(2026, 1, 1, tzinfo=UTC))

    assert ticker.release_claims() == 1

    db.refresh(schedule)
    assert schedule.claimed_at is None
    assert "interrupted by a restart" in schedule.last_error
