"""A schedule run on a linked computer (harness spec 3.5.0, acp.md *Scheduled
work on a linked computer*), on the fake clock of test_schedule_runs.py.

The device is a `Link` whose requests are answered here; the turn itself is
the stubbed reply of that file's `script`. What is tested is the server's
side: where a run may go, the agent chat it becomes, the `scheduled` object
its session is told, waiting for an away computer and then `missed`, a
computer that does not accept scheduled work, the device's own outcome, and
the watch command's hash.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from lembas.db.models import KIND_AGENT, TOKEN_DEVICE, Chat, Schedule, User
from lembas.db.session import session_scope
from lembas.security import api_tokens
from lembas.services import device_link, settings_store
from lembas.services import schedules as schedules_service
from lembas.services import wake as wake_service
from lembas.services.schedule import clock, device, runs
from tests import test_schedule_runs as base
from tests.test_schedule_runs import T0, _owner, _runs, _sweep

# That file's fake clock and stubbed replies, and its three models.
script = base.script
three = base.three

FOLDER = "/home/dev/waybread"
DAILY = {"at": {"times": ["02:00"]}}


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    device.TURNS.clear()
    yield
    device_link.reset()
    device.TURNS.clear()


def _token(db, host="device-1"):
    token, raw = api_tokens.create(
        db, _owner(db), name=f"LLeMbas CLI on {host}", scopes=["models", "link"],
        kind=TOKEN_DEVICE, device_host=host,
    )
    return token, raw


def _link(db, token, *, accepts=True, capabilities=("scheduled", "turns"), answer=None,
          max_mode="auto"):
    async def nothing(*_a, **_k):
        return None

    link = device_link.Link(
        token_id=token.id, user_id=token.user_id, name=token.name,
        send_text=nothing, close=nothing,
        hello={"device": {"host": token.device_host},
               "limits": {"roots": ["/home/dev"], "max_mode": max_mode,
                          "scheduled": accepts}},
    )
    link.initialized = {"_meta": {"lembas": {"capabilities": list(capabilities)}}}
    asked: list[tuple[str, dict]] = []

    async def request(method, params=None, *, timeout=60):
        asked.append((method, params))
        return answer(method, params) if answer is not None else {}

    link.request = request
    device_link.LINKS[token.id] = link
    return link, asked


def _agents_on(db):
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)


def _on_device(db, token, **runs_on) -> Schedule:
    schedule = schedules_service.create(
        db, owner=_owner(db), title="Nightly tests", instruction="Run the tests.",
        rule={"start": T0.isoformat(), "every": {"hours": 1}},
        settings={"notify": "always",
                  "runs_on": {"device": token.id, "cwd": FOLDER, **runs_on}},
    )
    schedule.next_fire_at = T0
    db.commit()
    return schedule


# --- Where it may run ----------------------------------------------------------------------
def test_where_it_runs_is_validated(db, three, script):
    token, _ = _token(db)
    owner = _owner(db)
    by_host = _on_device(db, token)
    assert (by_host.device_token_id, by_host.device_dir, by_host.device_mode) == (
        token.id, FOLDER, "edit"
    )
    said = runs.schedule_dict(db, by_host, owner=owner)["runs_on"]
    assert said == {"device": token.id, "device_name": "device-1", "cwd": FOLDER,
                    "mode": "edit", "approval_wait_minutes": 30}

    def apply(value, **kw):
        schedules_service.apply_settings(by_host, {"runs_on": value}, db=db, owner=owner, **kw)

    apply({"device": "DEVICE-1", "cwd": FOLDER, "mode": "auto", "approval_wait_minutes": 5})
    assert (by_host.device_mode, by_host.approval_wait_minutes) == ("auto", 5)
    for bad in (
        {"device": token.id, "cwd": FOLDER, "mode": "plan"},
        {"device": token.id, "cwd": "relative/dir"},
        {"device": "elsewhere", "cwd": FOLDER},
        {"device": "this", "cwd": FOLDER},  # only a device's own call says "this"
        "device-1",
    ):
        with pytest.raises(schedules_service.ScheduleError):
            apply(bad)
    apply({"device": "this", "cwd": FOLDER}, calling_device=token.id)
    assert by_host.device_token_id == token.id
    # A command is watched there; back on the server it cannot be.
    schedules_service.apply_settings(
        by_host, {"watch": {"kind": "command", "command": "git rev-parse HEAD"}},
        db=db, owner=owner,
    )
    assert runs.schedule_dict(db, by_host, owner=owner)["watch"]["command"] == (
        "git rev-parse HEAD"
    )
    with pytest.raises(schedules_service.ScheduleError):
        apply(None)
    schedules_service.apply_settings(by_host, {"watch": "", "runs_on": None}, db=db, owner=owner)
    assert by_host.device_token_id is None and by_host.watch_kind == ""


def test_the_effective_mode_never_widens():
    assert device.effective(accept=True, mode=None, max_mode="auto")["mode"] == "edit"
    assert device.effective(accept=True, mode="auto", max_mode="edit")["mode"] == "edit"
    assert device.effective(accept=True, mode="auto", max_mode="auto")["mode"] == "auto"
    assert device.effective(accept=True, mode="plan", max_mode="auto") == {"refused": "plan"}
    assert device.effective(accept=False, mode="edit", max_mode="auto") == {
        "refused": "scheduled_off"
    }


# --- A run there ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_run_is_an_agent_chat_on_the_computer(db, three, script, monkeypatch):
    _agents_on(db)
    token, _ = _token(db)
    link, _ = _link(db, token)
    schedule = _on_device(db, token, approval_wait_minutes=10)
    told: list[dict] = []
    stubbed = wake_service.wake_chat

    async def wake(chat_id, content, **kw):
        told.append(dict(device.TURNS.get(chat_id) or {}))
        with session_scope() as session:
            chat = session.get(Chat, chat_id)
            told[-1]["context"] = device_link.context_for(
                session, link, session.get(User, chat.user_id), chat
            ).get("scheduled")
        return await stubbed(chat_id, content, **kw)

    monkeypatch.setattr(wake_service, "wake_chat", wake)
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "ok", run.error
    chat = db.get(Chat, run.chat_id)
    assert (chat.kind, chat.device_token_id, chat.project_dir, chat.agent_mode) == (
        KIND_AGENT, token.id, FOLDER, "edit"
    )
    meta = {"schedule_id": schedule.id, "run_id": run.id, "title": "Nightly tests",
            "mode": "edit", "approval_wait_s": 600}
    # `notify` (spec 3.8.0) is the server's own until a device says it reads
    # it: this one declares no `reports`, so its context carries none.
    assert told == [{**meta, "notify": "always", "context": meta}]
    link.initialized["_meta"]["lembas"]["capabilities"].append("reports")
    assert device.for_link({**meta, "notify": "always"}, link)["notify"] == "always"
    assert device.TURNS == {}  # only while the run lasts


@pytest.mark.anyio
async def test_an_away_computer_is_waited_for_then_missed(db, three, script):
    _agents_on(db)
    token, _ = _token(db)
    schedule = _on_device(db, token)
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert (run.status, run.waiting_for, run.error_class) == ("waiting", "device", "device_away")
    assert "device-1" in run.error and run.message_id == ""
    assert runs.run_dict(run)["waiting_for"] == "device"
    # Its window closes before the next try: missed, and the person is told.
    retry = clock.as_utc(run.retry_at)
    run.deadline = retry - timedelta(seconds=1)
    db.commit()
    await _sweep(script, retry)
    (run,) = _runs(db, schedule)
    assert run.status == "missed" and run.error_class == "device_away"
    assert script.turns == []


@pytest.mark.anyio
async def test_a_computer_back_in_time_runs_it(db, three, script):
    _agents_on(db)
    token, _ = _token(db)
    schedule = _on_device(db, token)
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    _link(db, token)
    await _sweep(script, clock.as_utc(run.retry_at))
    (run,) = _runs(db, schedule)
    assert run.status == "ok" and len(script.turns) == 1


@pytest.mark.anyio
async def test_a_computer_that_does_not_accept_it_fails_the_run(db, three, script):
    _agents_on(db)
    token, _ = _token(db)
    _link(db, token, accepts=False)
    schedule = _on_device(db, token)
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert (run.status, run.error_class) == ("failed", "device")
    assert run.error == device.text("off") and run.retry_at is None
    assert script.turns == []


@pytest.mark.anyio
async def test_an_old_cli_is_told_to_update(db, three, script):
    _agents_on(db)
    token, _ = _token(db)
    _link(db, token, capabilities=("turns",))
    schedule = _on_device(db, token)
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "failed" and "too old" in run.error


@pytest.mark.anyio
async def test_the_devices_word_on_its_turn_is_the_runs(db, three, script, monkeypatch):
    _agents_on(db)
    token, _ = _token(db)
    _link(db, token)
    schedule = _on_device(db, token)
    stubbed = wake_service.wake_chat

    async def wake(chat_id, content, **kw):
        message_id = await stubbed(chat_id, content, **kw)
        script.generations[message_id].scheduled_outcome = {
            "run_id": "x", "status": "failed", "text": "", "error": "The tests would not start.",
        }
        return message_id

    monkeypatch.setattr(wake_service, "wake_chat", wake)
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert (run.status, run.error_class, run.error) == (
        "failed", "device", "The tests would not start."
    )


# --- The watch command ---------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_watched_command_runs_the_turn_only_when_it_changed(db, three, script):
    _agents_on(db)
    token, _ = _token(db)
    outputs = [b"abc123", b"abc123", b"def456"]

    def answer(method, params):
        if method != "_lembas/monitor":
            return {}
        out = outputs.pop(0)
        return {"hash": device.monitor_hash(out, 0), "exit": 0, "timed_out": False,
                "excerpt": out.decode(), "truncated": False, "bytes": len(out),
                "duration_ms": 5}

    _, asked = _link(db, token, answer=answer, max_mode="edit")
    schedule = _on_device(db, token, mode="auto")
    schedules_service.apply_settings(
        schedule, {"watch": {"kind": "command", "command": "git rev-parse HEAD",
                             "timeout_s": 20}},
        db=db, owner=_owner(db),
    )
    db.commit()
    for hours in range(3):
        await _sweep(script, T0 + timedelta(hours=hours))
    first, same, changed = _runs(db, schedule)
    assert [first.status, same.status, changed.status] == ["ok", "unchanged", "ok"]
    looks = [p for m, p in asked if m == "_lembas/monitor"]
    assert looks[0] == {"cwd": FOLDER, "command": "git rev-parse HEAD", "mode": "auto",
                        "schedule_id": schedule.id, "run_id": first.id, "timeout_s": 20}
    assert len(script.turns) == 2
    assert "first look" in script.turns[0] and "<watched>\nabc123\n</watched>" in script.turns[0]
    assert "-abc123" in script.turns[1] and "+def456" in script.turns[1]
    db.refresh(schedule)
    assert schedule.watch_hash == device.monitor_hash(b"def456", 0)


@pytest.mark.anyio
async def test_a_refused_watch_command_fails_the_run(db, three, script):
    _agents_on(db)
    token, _ = _token(db)

    def answer(method, params):
        raise device_link.DeviceError("The watch command is refused here: rm.")

    _link(db, token, answer=answer)
    schedule = _on_device(db, token)
    schedules_service.apply_settings(
        schedule, {"watch": {"kind": "command", "command": "rm -rf build"}},
        db=db, owner=_owner(db),
    )
    db.commit()
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "failed" and "refused" in run.error
    assert script.turns == []


# --- /api/v2 with the device's token -------------------------------------------------------
def test_the_device_token_reaches_the_schedules_and_nothing_else(client, db, registered):
    from lembas.security import hardening

    hardening.reset_throttles()
    token, raw = _token(db)
    auth = {"Authorization": f"Bearer {raw}"}
    listed = client.get("/api/v2/schedules", headers=auth)
    assert listed.status_code == 200, listed.text
    made = client.post("/api/v2/schedules", headers=auth, json={
        "title": "Nightly tests", "instruction": "Run the tests.",
        "rule": DAILY,
        "runs_on": {"device": "this", "cwd": FOLDER},
    })
    assert made.status_code == 201, made.text
    said = made.json()["schedule"]["runs_on"]
    assert (said["device"], said["mode"], said["approval_wait_minutes"]) == (token.id, "edit", 30)
    assert client.get("/api/v2/chats", headers=auth).status_code in (401, 403)
    # An app's token names no calling device.
    _, app_raw = api_tokens.create(db, _owner(db), name="LLeMbas for Android", scopes=["app"])
    refused = client.post("/api/v2/schedules", headers={"Authorization": f"Bearer {app_raw}"},
                          json={
                              "title": "x", "instruction": "y", "rule": DAILY,
                              "runs_on": {"device": "this", "cwd": FOLDER},
                          })
    assert refused.status_code == 400
    assert db.scalar(select(Schedule).where(Schedule.title == "x")) is None


# --- The form ------------------------------------------------------------------------------
def test_the_form_offers_a_computer_that_accepts_it(client, db, registered):
    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    settings_store.update(db, {"default_permissions": {"schedule.use": True}})
    accepting, _ = _token(db, "device-1")
    refusing, _ = _token(db, "device-2")
    _link(db, accepting)
    _link(db, refusing, accepts=False)
    page = client.get("/scheduled/new").text
    assert f'<option value="{accepting.id}"' in page
    assert re.search(rf'<option value="{refusing.id}"\s+disabled', page)
    assert "does not accept scheduled work" in page
    made = client.post("/api/schedules", follow_redirects=False, data={
        "title": "Nightly tests", "instruction": "Run the tests.", "target": "chat",
        "repeat": "calendar", "weekdays": "0", "times": "02:00", "count": "0",
        "device": accepting.id, "device_dir": FOLDER, "device_mode": "auto",
        "approval_wait_minutes": "15", "watch_command": "git rev-parse HEAD",
    })
    assert made.status_code == 303, made.text
    schedule = db.scalars(select(Schedule)).one()
    assert (schedule.device_token_id, schedule.device_mode, schedule.approval_wait_minutes,
            schedule.watch_kind) == (accepting.id, "auto", 15, "command")
    shown = client.get(f"/scheduled/{schedule.id}").text
    assert "device-1" in shown and "git rev-parse HEAD" in shown
    # Back on the server, the hidden command field is not a watch.
    changed = client.post(f"/api/schedules/{schedule.id}", follow_redirects=False, data={
        "title": "Nightly tests", "instruction": "Run the tests.", "target": "chat",
        "repeat": "calendar", "weekdays": "0", "times": "02:00", "count": "0",
        "device": "", "watch_command": "git rev-parse HEAD",
    })
    assert changed.status_code == 303, changed.text
    db.expire_all()
    schedule = db.get(Schedule, schedule.id)
    assert schedule.device_token_id is None and schedule.watch_kind == ""


# --- A client older than the reports (harness spec 3.8.0, reports.md §6) ------------------------
@pytest.mark.anyio
@pytest.mark.parametrize(
    ("notify_when", "reply", "status", "hint", "reported"),
    [
        # `when`: the 3.5.0 hint, and no report whatever it answers.
        ("when", "The tests fail.", "ok", True, False),
        ("when", "[SILENT]", "silent", True, False),
        # `always`: no hint; the server files its answer -- but not a silence.
        ("always", "The tests fail.", "ok", False, True),
        ("always", "[SILENT]", "silent", False, False),
        ("never", "The tests fail.", "ok", False, False),
    ],
)
async def test_a_device_without_reports_is_told_and_settled_as_before(
    db, three, script, monkeypatch, notify_when, reply, status, hint, reported
):
    from lembas.db.models import Report
    from lembas.services.schedule import notify

    _agents_on(db)
    token, _ = _token(db)
    link, _ = _link(db, token)  # declares `scheduled` and `turns`, not `reports`
    schedule = _on_device(db, token)
    schedule.notify = notify_when
    db.commit()
    script.replies = [{"content": reply}]
    stubbed = wake_service.wake_chat

    async def wake(chat_id, content, **kw):
        message_id = await stubbed(chat_id, content, **kw)
        script.generations[message_id].scheduled_outcome = {
            "run_id": "x", "status": status, "text": reply, "error": None,
        }
        # The client is updated while the run is going: the run was told the
        # old words, and is settled by the old rule -- decided once, at its start.
        link.initialized["_meta"]["lembas"]["capabilities"].append("reports")
        return message_id

    monkeypatch.setattr(wake_service, "wake_chat", wake)
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert run.status == "ok", run.error
    assert run.legacy_notify is True
    # Never an instruction naming a tool the device does not have.
    assert "report_write" not in script.turns[0]
    assert (notify.LEGACY_HINT in script.turns[0]) is hint
    db.expire_all()
    filed = list(db.scalars(select(Report)))
    assert bool(filed) is reported and bool(run.report_id) is reported
    if reported:
        assert filed[0].body == "The tests fail." and script.reported == [(run.id, filed[0].title)]
    else:
        assert script.reported == []


@pytest.mark.anyio
async def test_a_device_with_reports_gets_the_new_instruction_and_its_report_is_filed(
    db, three, script, monkeypatch
):
    from lembas.db.models import Report

    _agents_on(db)
    token, _ = _token(db)
    _link(db, token, capabilities=("scheduled", "turns", "reports"))
    schedule = _on_device(db, token)
    schedule.notify = "when"
    db.commit()
    script.replies = [{"content": "Filed a report."}]
    stubbed = wake_service.wake_chat

    async def wake(chat_id, content, **kw):
        message_id = await stubbed(chat_id, content, **kw)
        script.generations[message_id].scheduled_outcome = {
            "run_id": "x", "status": "ok", "text": "Filed a report.", "error": None,
            "report": {"title": "Tests fail", "body": "Two of them.", "importance": "high"},
        }
        return message_id

    monkeypatch.setattr(wake_service, "wake_chat", wake)
    await _sweep(script, T0)
    (run,) = _runs(db, schedule)
    assert "report_write" in script.turns[0] and "SILENT" not in script.turns[0]
    db.expire_all()
    (report,) = db.scalars(select(Report))
    assert (report.title, report.body, report.importance) == ("Tests fail", "Two of them.", "high")
    assert run.report_id == report.id and script.reported == [(run.id, "Tests fail")]
