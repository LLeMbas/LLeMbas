"""Reports: what a schedule's run leaves for its owner (harness spec 3.8.0).

The rules themselves -- notify-when, what a run leaves -- are driven by
`conformance/reports.json` (`tests/test_harness_spec.py`) and, through a run,
by `tests/test_schedule_runs.py`. Here: the store, the pages, `/api/v2`, the
tool, the notification, and everything a report's conversation and a
schedule's run chats must stay *out of*.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from lembas.db.models import (
    KIND_AGENT,
    KIND_REPORT,
    KIND_TASK,
    KINDS,
    Chat,
    Message,
    Report,
    Schedule,
    ScheduleRun,
    User,
)
from lembas.security import api_tokens
from lembas.services import reports as reports_service
from lembas.services import schedules as schedules_service
from lembas.services import settings_store
from lembas.services.schedule import notify, runs
from tests.test_schedules_ui import _as_stranger


@pytest.fixture(autouse=True)
def _fresh_rates():
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()
    yield


def _owner(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _bearer(db, user: User) -> dict[str, str]:
    _, raw = api_tokens.create(db, user, name="LLeMbas for Android", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def _schedule(db, owner: User, title: str = "Build check", **settings) -> Schedule:
    return schedules_service.create(
        db, owner=owner, title=title, instruction="Check the build.",
        rule={"at": {"times": ["09:00"]}}, settings=settings or None,
    )


def _file(db, owner: User, schedule: Schedule | None = None, *, title="Build is red",
          body="test_x fails.", run: ScheduleRun | None = None, **more) -> Report:
    row = reports_service.file(
        db, owner_id=owner.id, report={"title": title, "body": body, **more},
        schedule=schedule, run=run,
    )
    db.commit()
    return row


# --- The store ------------------------------------------------------------------------
def test_a_second_report_of_one_run_replaces_the_first(db, registered):
    owner = _owner(db)
    schedule = _schedule(db, owner)
    run = ScheduleRun(schedule_id=schedule.id, user_id=owner.id, status="running")
    db.add(run)
    db.commit()
    first = _file(db, owner, schedule, run=run, title="Draft")
    first.unread = False
    db.commit()
    second = _file(db, owner, schedule, run=run, title="Final", body="All of it.")
    assert second.id == first.id == run.report_id
    assert (second.title, second.body, second.unread) == ("Final", "All of it.", True)
    assert len(db.scalars(select(Report)).all()) == 1


def test_the_list_is_unread_first_and_filters(db, registered):
    owner = _owner(db)
    build, news = _schedule(db, owner), _schedule(db, owner, "News")
    old = _file(db, owner, build, title="Old")
    read = _file(db, owner, news, title="Read")
    new = _file(db, owner, build, title="New")
    now = datetime.now(UTC)
    old.created_at, read.created_at, new.created_at = (
        now - timedelta(hours=3), now - timedelta(hours=1), now - timedelta(hours=2)
    )
    read.unread = False
    db.commit()
    titles = lambda rows: [r.title for r in rows]  # noqa: E731
    assert titles(reports_service.listing(db, owner)) == ["New", "Old", "Read"]
    assert titles(reports_service.listing(db, owner, unread=True)) == ["New", "Old"]
    assert titles(reports_service.listing(db, owner, schedule_id=news.id)) == ["Read"]
    assert reports_service.unread_count(db, owner.id) == 2


def test_old_notify_values_are_migrated(db, registered):
    from lembas.db.migrations import notify_values
    from lembas.db.session import get_engine

    owner = _owner(db)
    old, loud = _schedule(db, owner), _schedule(db, owner, "Loud", notify="always")
    db.execute(text("UPDATE schedules SET notify = 'matters' WHERE id = :id"), {"id": old.id})
    db.commit()
    assert notify_values(get_engine())
    db.expire_all()
    assert (db.get(Schedule, old.id).notify, db.get(Schedule, loud.id).notify) == (
        "when", "always"
    )
    assert db.get(Schedule, old.id).notify_when == ""  # the spec's default condition
    assert notify_values(get_engine()) == []  # once


# --- /api/v2 ----------------------------------------------------------------------------
def test_v2_list_read_reply_delete(client: TestClient, db, registered):
    owner = _owner(db)
    auth = _bearer(db, owner)
    schedule = _schedule(db, owner)
    report = _file(db, owner, schedule, summary="One test fails", importance="high",
                   body="See:\n\n```html-view Failures\n<p>test_x</p>\n```\n")
    other = _file(db, owner, _schedule(db, owner, "News"), title="Headlines")
    other.unread = False
    db.commit()

    listed = client.get("/api/v2/reports", headers=auth).json()
    assert listed["unread"] == 1
    assert [r["id"] for r in listed["reports"]] == [report.id, other.id]
    assert listed["reports"][0] == {
        "id": report.id, "title": "Build is red", "summary": "One test fails",
        "importance": "high", "schedule": {"id": schedule.id, "title": "Build check"},
        "created": listed["reports"][0]["created"], "unread": True,
    }
    # `?unread=1` is what the terminal client sends; `true` is the same.
    for value in ("1", "true"):
        only = client.get(f"/api/v2/reports?unread={value}", headers=auth).json()["reports"]
        assert [r["id"] for r in only] == [report.id]
    by = client.get(f"/api/v2/reports?schedule={schedule.id}", headers=auth).json()["reports"]
    assert [r["id"] for r in by] == [report.id]

    one = client.get(f"/api/v2/reports/{report.id}", headers=auth).json()["report"]
    assert one["body"].startswith("See:") and one["chat"] is None
    # A page in the body: where text is read it is its projection.
    assert one["text"] == "See:\n\n[page: Failures]\n"
    assert [p["title"] for p in one["pages"]] == ["Failures"]
    # Reading it over the API does not mark it read; saying so does.
    assert one["unread"] is True
    assert client.post(f"/api/v2/reports/{report.id}/read", headers=auth).status_code in (200, 204)
    assert client.get("/api/v2/reports", headers=auth).json()["unread"] == 0

    replied = client.post(f"/api/v2/reports/{report.id}/reply", headers=auth).json()
    again = client.post(f"/api/v2/reports/{report.id}/reply", headers=auth).json()
    assert replied == again and set(replied) == {"chat"}
    assert client.get(f"/api/v2/reports/{report.id}", headers=auth).json()["report"]["chat"] == (
        replied["chat"]
    )

    assert client.delete(f"/api/v2/reports/{report.id}", headers=auth).status_code == 204
    assert client.get(f"/api/v2/reports/{report.id}", headers=auth).status_code == 404
    db.expunge_all()
    assert db.get(Chat, replied["chat"]) is None  # its conversation went with it


def test_somebody_elses_report_is_not_there(client: TestClient, db, registered):
    owner = _owner(db)
    report = _file(db, owner, _schedule(db, owner))
    sam = _as_stranger(client, db)
    settings_store.update(
        db, {"default_permissions": {"api.use": True, "schedule.use": True}}
    )
    auth = _bearer(db, sam)
    assert client.get("/api/v2/reports", headers=auth).json() == {"reports": [], "unread": 0}
    assert client.get(f"/api/v2/reports/{report.id}", headers=auth).status_code == 404
    assert client.post(f"/api/v2/reports/{report.id}/read", headers=auth).status_code == 404
    assert client.post(f"/api/v2/reports/{report.id}/reply", headers=auth).status_code == 404
    assert client.delete(f"/api/v2/reports/{report.id}", headers=auth).status_code == 404
    # ...and by the pages, signed in as Sam.
    assert client.get(f"/reports/{report.id}").status_code == 404
    assert client.post(f"/reports/{report.id}/delete").status_code == 404
    assert "Build is red" not in client.get("/reports").text
    db.expire_all()
    assert db.get(Report, report.id).unread is True and db.get(Report, report.id).chat_id is None


def test_reports_need_the_schedule_permission(client: TestClient, db, registered):
    sam = _as_stranger(client, db)
    settings_store.update(
        db, {"default_permissions": {"api.use": True, "schedule.use": True}}
    )
    auth = _bearer(db, sam)
    assert client.get("/api/v2/reports", headers=auth).status_code == 200
    settings_store.update(
        db, {"default_permissions": {"api.use": True, "schedule.use": False}}
    )
    assert client.get("/api/v2/reports", headers=auth).status_code == 403
    assert client.get("/reports").status_code == 403


# --- The pages ----------------------------------------------------------------------------
def test_the_pages_list_show_mark_read_and_delete(client: TestClient, db, registered):
    owner = _owner(db)
    schedule = _schedule(db, owner)
    report = _file(db, owner, schedule, body="**Bold** and <script>alert(1)</script>")

    side = client.get("/chat").text
    assert 'href="/reports"' in side and 'id="unread-reports"' in side
    listed = client.get("/reports").text
    assert "Build is red" in listed and f'href="/reports/{report.id}"' in listed

    page = client.get(f"/reports/{report.id}").text
    assert "<strong>Bold</strong>" in page
    # Model output: through the Markdown pipeline, never as markup of its own.
    assert "<script>alert(1)</script>" not in page
    db.expire_all()
    assert db.get(Report, report.id).unread is False  # opening it is reading it

    gone = client.post(f"/reports/{report.id}/delete", follow_redirects=False)
    assert (gone.status_code, gone.headers["location"]) == (303, "/reports")
    assert client.get(f"/reports/{report.id}").status_code == 404


def test_the_unread_poll_carries_the_reports_count(client: TestClient, db, registered):
    owner = _owner(db)
    _file(db, owner, _schedule(db, owner))
    first = client.get("/api/chats/unread")
    assert 'id="unread-reports"' in first.text and ">1<" in first.text
    # ...and the report is said once, by the toast a reply gets: then never again.
    said = json.loads(first.headers["HX-Trigger"])["lembas:unread"]["items"]
    assert [(i["kind"], i["title"]) for i in said] == [("report", "Build is red")]
    assert said[0]["url"].startswith("/reports/")
    again = client.get("/api/chats/unread")
    assert "HX-Trigger" not in again.headers and ">1<" in again.text


# --- Reply: a conversation that stays with its report ---------------------------------------
def test_a_reply_chat_is_in_no_list(client: TestClient, db, registered):
    owner = _owner(db)
    auth = _bearer(db, owner)
    report = _file(db, owner, _schedule(db, owner), title="Mallorn harvest",
                   body="The mallorn harvest is late.")

    opened = client.post(f"/reports/{report.id}/reply", follow_redirects=False)
    assert opened.status_code == 303
    chat_id = opened.headers["location"].rsplit("/", 1)[1]
    chat = db.get(Chat, chat_id)
    assert chat.kind == KIND_REPORT and KIND_REPORT not in KINDS
    said = list(db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    ))
    # User-first for the templates that insist on it; the report is what is said.
    assert [(m.role, m.machine) for m in said] == [("user", True), ("assistant", False)]
    assert said[1].content == "# Mallorn harvest\n\nThe mallorn harvest is late."
    # A reply arrived while nobody looked: still no dot and no push for it.
    chat.unread = True
    db.commit()

    page = client.get(f"/chat/{chat_id}")
    assert page.status_code == 200
    assert f'href="/reports/{report.id}"' in page.text  # the way back
    # Not in the sidebar, the unread poll, either search, or the apps' chats.
    assert f'href="/chat/{chat_id}"' not in client.get("/chat").text
    assert f"unread-{chat_id}" not in client.get("/api/chats/unread").text
    assert chat_id not in client.get("/search?q=mallorn").text
    listed = client.get("/api/v2/chats", headers=auth).json()
    assert chat_id not in json.dumps(listed)
    assert chat_id not in json.dumps(client.get("/api/v2/chats/unread", headers=auth).json())

    from lembas.services.index import pipeline

    assert not pipeline._message_wanted(said[1], chat)


# --- Run chats stay out of the way ---------------------------------------------------------
def test_a_run_on_a_linked_computer_is_in_no_list(client: TestClient, db, registered):
    owner = _owner(db)
    auth = _bearer(db, owner)
    run_chat = Chat(user_id=owner.id, kind=KIND_AGENT, title="Nightly mallorn tests",
                    scheduled=True, unread=True)
    own = Chat(user_id=owner.id, kind=KIND_AGENT, title="My own mallorn session")
    db.add_all([run_chat, own])
    db.commit()
    message = Message(chat_id=run_chat.id, role="assistant", content="mallorn ok", complete=True)
    db.add(message)
    db.commit()
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)

    assert f"unread-{run_chat.id}" not in client.get("/api/chats/unread").text
    found = client.get("/search?q=mallorn").text
    assert own.id in found and run_chat.id not in found
    listed = json.dumps(client.get("/api/v2/chats?kind=agent", headers=auth).json())
    assert own.id in listed and run_chat.id not in listed
    assert run_chat.id not in json.dumps(
        client.get("/api/v2/chats/unread", headers=auth).json()
    )
    side = client.get("/chat?side=agents").text
    assert f'href="/chat/{run_chat.id}"' not in side

    from lembas.services.index import pipeline

    assert not pipeline._message_wanted(message, run_chat)
    assert not pipeline._message_wanted(message, Chat(user_id=owner.id, kind=KIND_TASK))


def test_the_sweep_takes_old_run_chats_and_leaves_reports(db, registered):
    owner = _owner(db)
    schedule = _schedule(db, owner)
    long_ago = datetime.now(UTC) - timedelta(days=400)
    task = Chat(user_id=owner.id, kind=KIND_TASK, title="run")
    db.add(task)
    db.commit()
    run = ScheduleRun(schedule_id=schedule.id, user_id=owner.id, status="ok", chat_id=task.id)
    db.add(run)
    db.commit()
    report = _file(db, owner, schedule, run=run)
    reply = reports_service.reply_chat(db, owner, report)
    # A run row that (wrongly) pointed at the reply chat must not take it either.
    stray = ScheduleRun(schedule_id=schedule.id, user_id=owner.id, status="ok", chat_id=reply.id)
    db.add(stray)
    db.commit()
    for row in (run, stray):
        row.created_at = long_ago
    db.commit()
    task_id, reply_id, report_id = task.id, reply.id, report.id

    assert runs.sweep(db) == 2
    db.expunge_all()
    assert db.get(Chat, task_id) is None
    kept = db.get(Report, report_id)
    assert kept is not None and kept.chat_id == reply_id
    assert db.get(Chat, reply_id) is not None


# --- The tool -------------------------------------------------------------------------------
@pytest.mark.anyio
async def test_report_write_is_the_runs_and_nobody_elses(db, registered):
    from lembas.services.tools import report as report_tool
    from lembas.services.tools.core import ToolContext

    owner = _owner(db)
    schedule = _schedule(db, owner)
    task = Chat(user_id=owner.id, kind=KIND_TASK, title="run")
    plain = Chat(user_id=owner.id, kind="chat", title="mine")
    agent = Chat(user_id=owner.id, kind=KIND_AGENT, title="device run", scheduled=True)
    db.add_all([task, plain, agent])
    db.commit()
    reply = Message(chat_id=task.id, role="assistant", content="", complete=False)
    db.add(reply)
    db.commit()
    run = ScheduleRun(schedule_id=schedule.id, user_id=owner.id, status="running",
                      chat_id=task.id, message_id=reply.id)
    db.add_all([run, ScheduleRun(schedule_id=schedule.id, user_id=owner.id, status="ok",
                                 chat_id=agent.id)])
    db.commit()

    # Offered: only in the session a schedule opened, on the server.
    assert report_tool.session_schedule(db, task).id == schedule.id
    assert report_tool.session_schedule(db, plain) is None
    assert report_tool.session_schedule(db, agent) is None
    schedule.notify = "never"
    db.commit()
    assert report_tool.session_schedule(db, task) is None
    schedule.notify = "when"
    db.commit()

    args = {"title": "Build is red", "body": "test_x fails.", "importance": "high"}
    context = ToolContext(owner_id=owner.id, chat_id=task.id, message_id=reply.id)
    first = await report_tool._run_report_write(context, args)
    second = await report_tool._run_report_write(context, {**args, "title": "Build is red (2)"})
    assert first.content != second.content  # filed, then replaced
    db.expire_all()
    (report,) = db.scalars(select(Report)).all()
    assert (report.title, report.importance, report.owner_id) == (
        "Build is red (2)", "high", owner.id
    )
    assert db.get(ScheduleRun, run.id).report_id == report.id

    # The person writing into the run's chat afterwards: another reply, not the
    # run's. The tool is in the list and says it is not a scheduled run.
    later = ToolContext(owner_id=owner.id, chat_id=task.id, message_id="0" * 32)
    refused = await report_tool._run_report_write(later, args)
    assert "not a scheduled run" in refused.content
    assert len(db.scalars(select(Report)).all()) == 1


# --- The notification -----------------------------------------------------------------------
@pytest.mark.anyio
async def test_one_notification_and_nothing_of_the_report_on_a_lock_screen(
    db, registered, monkeypatch
):
    from lembas.services import push

    owner = _owner(db)
    report = _file(
        db, owner, _schedule(db, owner), title="Mallorn harvest is late",
        body="The eastern grove.\n\n```html-view Groves\n<b>secret</b>\n```\n",
    )
    sent: list[dict] = []

    async def announce(db_, user, **kw):
        sent.append(kw)
        return 1

    monkeypatch.setattr(push, "announce", announce)
    assert await notify.announce_report(db, owner, report=report)
    (told,) = sent
    assert (told["kind"], told["url"]) == ("report", f"/reports/{report.id}")
    # What may be shown anywhere: our own fixed words.
    assert told["plain_body"] is True and told["body"] == "New report"
    assert told["title"] == "Reports"
    card = told["extra"]["notification"]
    assert card["public"] == {"title": "New report", "body": ""}
    assert told["extra"]["visibility"] == "private"
    # For the unlocked screen: the title, and the first lines with a page as
    # its projection -- never its markup.
    assert card["title"] == "Mallorn harvest is late"
    assert card["body"] == "The eastern grove. [page: Groves]"
    payload = push.build_payload("LLeMbas", body=told["body"], url=told["url"],
                                 kind=told["kind"], extra=told["extra"])
    outside = {k: v for k, v in payload.items() if k != "notification"}
    assert "Mallorn" not in json.dumps(outside) and "grove" not in json.dumps(outside)


def test_a_reports_body_is_bounded_whoever_wrote_it():
    """A model's `report_write`, a linked computer's report in its answer, and
    an answer filed under `always` all end at the same size."""
    long = "x" * (reports_service.BODY_MAX + 5000)
    written = reports_service.clean_call({"title": "T", "body": long})
    assert len(written["body"].encode()) <= reports_service.BODY_MAX + 200
    assert written["body"].endswith(reports_service.CUT_NOTE)
    from_device, _ = reports_service.outcome(
        notify="when", title="T", calls=[{"title": "T", "body": long}], answer="Filed."
    )
    assert from_device["body"].endswith(reports_service.CUT_NOTE)
    from_answer, _ = reports_service.outcome(notify="always", title="T", calls=[], answer=long)
    assert from_answer["body"].endswith(reports_service.CUT_NOTE)
    short = reports_service.clean_call({"title": "T", "body": "Fine."})
    assert short["body"] == "Fine."
