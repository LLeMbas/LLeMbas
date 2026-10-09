"""Regenerating a reply, and the registry rules that make it work.

Regeneration is the one caller that reuses a Message row rather than creating a
new one, which is why it is the one caller `ensure` was wrong for.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Message, Model
from lembas.services import generation as generation_service
from lembas.services.crypto import encrypt


@pytest.fixture(autouse=True)
def empty_registry():
    """The registry is module state. A test that leaves an entry behind changes
    what the next one sees."""
    yield
    generation_service._RUNNING.clear()
    generation_service._TASKS.clear()


@pytest.fixture
def no_upstream(monkeypatch):
    """Replace the producer, so a test can watch the registry without a server.

    Records the generations it was asked to run.
    """
    started: list = []

    async def _fake_run(generation):
        started.append(generation)

    monkeypatch.setattr(generation_service, "_run", _fake_run)
    return started


def _connection(db) -> Connection:
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()
    return connection


def _reply(db, chat_id: str, *, complete: bool = True) -> Message:
    db.add(Message(chat_id=chat_id, role="user", content="what is lembas?"))
    reply = Message(chat_id=chat_id, role="assistant", content="Waybread.", complete=complete)
    db.add(reply)
    db.commit()
    return reply


def _finished(chat_id: str, message_id: str, *, text: str = "old") -> generation_service.Generation:
    """A generation in the state a just-completed reply leaves behind."""
    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    generation.content.append(text)
    generation.done = True
    generation.finished_at = datetime.now(UTC)
    generation_service._RUNNING[message_id] = generation
    return generation


# --- The bug -----------------------------------------------------------------
def test_regenerate_starts_a_fresh_generation(
    client: TestClient, db, registered, make_chat, no_upstream
):
    """The bug: `ensure` handed back the finished generation still sitting in
    the registry, so no request was ever made and the browser reconnected to a
    stream that had nothing left to say."""
    _connection(db)
    chat_id = make_chat()
    reply = _reply(db, chat_id)
    stale = _finished(chat_id, reply.id)

    response = client.post(f"/api/chats/{chat_id}/messages/{reply.id}/regenerate")
    assert response.status_code == 200

    fresh = generation_service.get(reply.id)
    assert fresh is not stale
    assert not fresh.done
    assert fresh.text == ""
    assert no_upstream == [fresh]


def test_regenerate_blanks_the_row_and_returns_a_streaming_shell(
    client: TestClient, db, registered, make_chat, no_upstream
):
    _connection(db)
    chat_id = make_chat()
    reply = _reply(db, chat_id)

    body = client.post(f"/api/chats/{chat_id}/messages/{reply.id}/regenerate").text
    assert "sse-connect=" in body
    assert 'sse-swap="render"' in body

    db.expire_all()
    row = db.get(Message, reply.id)
    assert row.complete is False
    assert row.content == ""


def test_regenerating_twice_in_a_row_works(
    client: TestClient, db, registered, make_chat, no_upstream
):
    """The reported symptom was that it worked once, sometimes."""
    _connection(db)
    chat_id = make_chat()
    reply = _reply(db, chat_id)

    client.post(f"/api/chats/{chat_id}/messages/{reply.id}/regenerate")
    first = generation_service.get(reply.id)
    first.done = True
    first.finished_at = datetime.now(UTC)

    client.post(f"/api/chats/{chat_id}/messages/{reply.id}/regenerate")
    assert generation_service.get(reply.id) is not first
    assert len(no_upstream) == 2


def test_regenerating_someone_elses_reply_is_not_found(
    client: TestClient, db, registered, make_chat, no_upstream
):
    from lembas.db.models import User
    from lembas.security.passwords import hash_password

    _connection(db)
    chat_id = make_chat()
    reply = _reply(db, chat_id)

    someone_else = User(name="Sam", email="s@shire.test", password_hash=hash_password("x"))
    db.add(someone_else)
    db.commit()
    db.scalar(select(Chat).where(Chat.id == chat_id)).user_id = someone_else.id
    db.commit()

    response = client.post(f"/api/chats/{chat_id}/messages/{reply.id}/regenerate")
    assert response.status_code == 404


# --- Registry rules ----------------------------------------------------------
async def test_ensure_still_attaches_to_an_unfinished_reply(db, no_upstream):
    """Idempotence is load-bearing: a page load that finds an unfinished reply
    must attach to it, not start a second one."""
    first = generation_service.ensure("chat", "message")
    assert generation_service.ensure("chat", "message") is first
    await asyncio.sleep(0)  # let the scheduled task actually start
    assert len(no_upstream) == 1


async def test_prune_expires_a_finished_generation_before_the_lookup(db, no_upstream):
    """`_prune` used to sit below the early return, where it could never reach
    the one entry that needed it."""
    stale = _finished("chat", "message")
    stale.finished_at = datetime.now(UTC) - generation_service.KEEP_FINISHED - timedelta(minutes=1)

    assert generation_service.ensure("chat", "message") is not stale


def test_a_superseded_generation_does_not_write_the_row(db, registered, make_chat):
    """A cancelled predecessor's `finally:` runs _persist on the same message,
    and it must not overwrite the reply that replaced it."""
    chat_id = make_chat()
    reply = _reply(db, chat_id)

    abandoned = generation_service.Generation(chat_id=chat_id, message_id=reply.id)
    abandoned.content.append("the abandoned attempt")
    current = generation_service.Generation(chat_id=chat_id, message_id=reply.id)
    current.content.append("the reply that replaced it")
    generation_service._RUNNING[reply.id] = current

    generation_service._persist(abandoned, "", 0.0)

    db.expire_all()
    assert db.get(Message, reply.id).content == "Waybread."


async def test_restart_cancels_a_generation_still_running(db, no_upstream):
    live = generation_service.ensure("chat", "message")
    generation_service.restart("chat", "message")
    assert live.cancel is True
    assert generation_service.get("message") is not live


# --- Ordering ----------------------------------------------------------------
async def test_the_reply_is_persisted_before_it_is_marked_done(
    db, registered, make_chat, monkeypatch
):
    """`_follow` breaks out the instant it sees `done` and re-renders the bubble
    from the row, so the row has to be right first."""
    chat_id = make_chat()
    reply = _reply(db, chat_id, complete=False)
    seen: list[bool] = []

    def _record(generation, title, elapsed):
        seen.append(generation.done)

    monkeypatch.setattr(generation_service, "_persist", _record)

    generation = generation_service.Generation(chat_id=chat_id, message_id=reply.id)
    generation_service._RUNNING[reply.id] = generation
    # No connection row, so resolve_endpoint fails and _run goes straight to
    # its finally: which is the part under test.
    await generation_service._run(generation)

    assert seen == [False]
    assert generation.done is True


# --- The streaming shell ------------------------------------------------------
def test_live_reasoning_is_replaced_not_appended(
    client: TestClient, db, registered, make_chat, no_upstream
):
    """The frame carries the whole block each time. Appending it repeated
    everything already shown, so the panel grew quadratically."""
    _connection(db)
    chat_id = make_chat()
    reply = _reply(db, chat_id)

    body = client.post(f"/api/chats/{chat_id}/messages/{reply.id}/regenerate").text
    line = next(line for line in body.splitlines() if 'sse-swap="reasoning"' in line)
    assert 'hx-swap="innerHTML"' in line
    assert "beforeend" not in line


async def test_a_silent_generation_gets_a_keepalive(db, registered, make_chat, monkeypatch):
    """A model thinking for a minute emits nothing, and an idle connection is
    what a proxy closes."""
    from lembas.api import chats as chats_api

    chat_id = make_chat()
    reply = _reply(db, chat_id, complete=False)

    generation = generation_service.Generation(chat_id=chat_id, message_id=reply.id)
    generation_service._RUNNING[reply.id] = generation
    monkeypatch.setattr(chats_api, "KEEPALIVE_AFTER", 0.0)

    frames: list[str] = []
    stream = chats_api._follow(chat_id, reply.id).__aiter__()

    async def _finish():
        await asyncio.sleep(0.05)
        generation.done = True

    task = asyncio.create_task(_finish())
    async for frame in stream:
        frames.append(frame)
        if frame.startswith("event: close"):
            break
    await task

    assert any(frame == ": keepalive\n\n" for frame in frames)
