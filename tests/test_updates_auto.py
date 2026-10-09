"""Updates that wait for an idle instance: the policy, the window, what
counts as busy, the drain, the backup before the request, and the notes of every
release between."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime

import pytest

from lembas import __version__
from lembas.services import backup, settings_store, updates, updates_auto


@pytest.fixture(autouse=True)
def _fresh():
    updates_auto.reset()
    yield
    updates_auto.reset()


def _bump(part: str) -> str:
    major, minor, patch = (int(x) for x in __version__.split("."))
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major + 1}.0.0"


def test_the_policy():
    assert updates_auto.allowed(__version__, _bump("patch"), "patch")
    assert not updates_auto.allowed(__version__, _bump("minor"), "patch")
    assert updates_auto.allowed(__version__, _bump("minor"), "minor")
    assert not updates_auto.allowed(__version__, _bump("major"), "minor")
    assert updates_auto.allowed(__version__, _bump("major"), "all")
    assert not updates_auto.allowed(__version__, __version__, "all")
    assert not updates_auto.allowed(__version__, "0.1.0", "all")
    assert not updates_auto.allowed(__version__, _bump("patch"), "off")


def test_the_window():
    at = lambda h, m: datetime(2026, 10, 7, h, m)  # noqa: E731
    assert updates_auto.in_window("", at(12, 0))
    assert updates_auto.in_window("02:00-05:00", at(3, 30))
    assert not updates_auto.in_window("02:00-05:00", at(5, 0))
    assert updates_auto.in_window("23:00-02:00", at(1, 0))
    assert not updates_auto.in_window("23:00-02:00", at(12, 0))


def test_recent_activity_is_busy():
    assert updates_auto.busy(10) == []
    updates_auto.stamp_activity()
    assert "active" in updates_auto.busy(10)[0]


def test_a_running_reply_is_busy(db, registered, make_chat):
    from lembas.services import generation as generation_service

    generation_service._RUNNING["m1"] = generation_service.Generation(
        chat_id=make_chat(), message_id="m1"
    )
    try:
        assert "being written" in updates_auto.busy(0)[0]
    finally:
        generation_service._RUNNING.clear()


def test_a_page_counts_as_activity_and_a_poll_does_not(client, registered):
    updates_auto.reset()
    client.get("/api/chats/unread")
    assert updates_auto.LAST_ACTIVITY["at"] == 0.0
    client.get("/settings", headers={"Accept": "text/html"})
    assert updates_auto.LAST_ACTIVITY["at"] > 0


def _wanted(monkeypatch, label: str, *, helper=True):
    state = updates.State(available=updates.Target(ref=f"v{label}", label=label, sha="x"))
    monkeypatch.setattr(updates, "read", lambda fetch=False: state)
    monkeypatch.setattr(updates, "helper_installed", lambda: helper)
    requested = []
    monkeypatch.setattr(updates, "request_update", lambda who="": requested.append(who) or "")
    return requested


def test_an_idle_instance_is_backed_up_then_updated(db, monkeypatch):
    settings_store.update(
        db, {"auto_apply": "patch", "idle_minutes": 0}, key=settings_store.UPDATES
    )
    requested = _wanted(monkeypatch, _bump("patch"))
    before = len(backup.listing())
    asyncio.run(updates_auto.tick())
    assert requested == ["automatic"]
    assert len(backup.listing()) == before + 1
    # The backup is a real, whole database.
    with sqlite3.connect(str(backup.listing()[0])) as copy:
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_a_release_outside_the_policy_is_left(db, monkeypatch):
    settings_store.update(
        db, {"auto_apply": "patch", "idle_minutes": 0}, key=settings_store.UPDATES
    )
    requested = _wanted(monkeypatch, _bump("minor"))
    asyncio.run(updates_auto.tick())
    assert requested == [] and updates_auto.STATE.wanted == ""


def test_off_never_checks(db, monkeypatch):
    calls = []
    monkeypatch.setattr(updates, "read", lambda fetch=False: calls.append(fetch) or updates.State())
    asyncio.run(updates_auto.tick())
    assert calls == []


def test_a_busy_instance_waits_and_then_drains(db, monkeypatch):
    settings_store.update(
        db,
        {"auto_apply": "all", "idle_minutes": 10, "drain_after_minutes": 60},
        key=settings_store.UPDATES,
    )
    requested = _wanted(monkeypatch, _bump("patch"))
    updates_auto.stamp_activity()
    asyncio.run(updates_auto.tick())
    assert requested == [] and updates_auto.STATE.waiting_for
    assert updates_auto.refusing() == ""
    # An hour later, still busy: new replies are held so it can become idle.
    updates_auto.STATE.wanted_since = time.monotonic() - 3601
    asyncio.run(updates_auto.tick())
    assert updates_auto.refusing().startswith("Updating in a moment")


def test_a_drain_refuses_a_new_reply(client, db, registered, make_chat):
    from sqlalchemy import select

    from lembas.db.models import Message

    updates_auto.STATE.draining = True
    chat_id = make_chat()
    client.post(f"/api/chats/{chat_id}/messages", data={"content": "hello"})
    for _ in range(100):
        db.expire_all()
        reply = db.scalar(
            select(Message).where(Message.chat_id == chat_id, Message.role == "assistant")
        )
        if reply is not None and reply.complete:
            break
        time.sleep(0.05)
    assert "Updating in a moment" in (reply.error or "")


def test_no_backup_no_update(db, monkeypatch):
    settings_store.update(db, {"auto_apply": "all", "idle_minutes": 0}, key=settings_store.UPDATES)
    requested = _wanted(monkeypatch, _bump("patch"))

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(backup, "take", broken)
    asyncio.run(updates_auto.tick())
    assert requested == [] and "backup" in updates_auto.STATE.error


def test_apply_when_idle_from_the_page(client, db, registered, monkeypatch):
    requested = _wanted(monkeypatch, _bump("minor"))
    r = client.post("/admin/updates/apply", data={"when": "idle"}, follow_redirects=False)
    assert r.status_code == 303 and requested == []
    assert updates_auto.STATE.manual and updates_auto.STATE.wanted == _bump("minor")
    settings_store.update(db, {"idle_minutes": 0}, key=settings_store.UPDATES)
    updates_auto.LAST_ACTIVITY["at"] = 0.0
    asyncio.run(updates_auto.tick())
    assert requested == ["an administrator"]


def test_apply_now_still_backs_up(client, db, registered, monkeypatch):
    requested = _wanted(monkeypatch, _bump("patch"))
    before = len(backup.listing())
    client.post("/admin/updates/apply", data={"when": "now"})
    assert requested and len(backup.listing()) == before + 1


def test_the_backup_command(tmp_path):
    from typer.testing import CliRunner

    from lembas.cli import app as cli_app

    result = CliRunner().invoke(cli_app, ["backup", "--label", "by hand", "--keep", "2"])
    assert result.exit_code == 0, result.output
    assert "lembas-" in result.output and "by-hand" in result.output
    assert len(backup.listing()) <= 2


def test_backups_keep_the_newest(db):
    for n in range(4):
        backup.take(f"n{n}", keep=0)
        time.sleep(0.01)
    assert backup.prune(2) >= 2
    assert len(backup.listing()) == 2
