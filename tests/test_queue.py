"""Typing while a reply is being written.

Before this existed, a second message during a stream was simply accepted: it
wrote a second assistant placeholder, started a second `Generation`, and left
two replies answering the same chat from two different prefixes of it -- with
Stop pointing at whichever bubble came first in the document.

Now it queues. The queue is not an object: it is "the rows in this chat with
`queued` set, oldest first". That is the whole reason it survives a restart and
the reason `_prune` cannot take it away.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.api.chats import MAX_QUEUED
from lembas.db.models import ROLE_ASSISTANT, ROLE_USER, Chat, Connection, Message, Model
from lembas.services import chat as chat_service
from lembas.services import generation as generation_service
from lembas.services.crypto import encrypt


@pytest.fixture(autouse=True)
def empty_registry():
    yield
    generation_service._RUNNING.clear()
    generation_service._TASKS.clear()


@pytest.fixture
def no_upstream(monkeypatch):
    """Replace the producer, so the routes can be driven without a server."""
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


def _mid_reply(db, chat_id: str) -> Message:
    """A chat with a reply still being written, which is the whole precondition."""
    db.add(Message(chat_id=chat_id, role=ROLE_USER, content="what is lembas?"))
    reply = Message(chat_id=chat_id, role=ROLE_ASSISTANT, content="Way", complete=False)
    db.add(reply)
    db.commit()
    return reply


def _messages(db, chat_id: str) -> list[Message]:
    return list(
        db.scalars(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
        )
    )


# --- The column --------------------------------------------------------------
def test_an_ordinary_message_is_not_queued(db, registered, make_chat):
    """Every row that predates the column reads the same way, because
    `sync_schema` adds a NOT NULL boolean with a literal default of 0."""
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)

    assert chat_service.create_message(db, chat, ROLE_USER, "hello").queued is False


def test_a_queued_message_is_left_out_of_the_request(db, registered, make_chat):
    """It is in the transcript and it is not in the request. That distinction is
    the entire feature."""
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat_service.create_message(db, chat, ROLE_USER, "first")
    chat_service.create_message(db, chat, ROLE_ASSISTANT, "an answer")
    chat_service.create_message(db, chat, ROLE_USER, "typed while it worked", queued=True)

    sent = chat_service.build_messages(db, chat)

    assert [m["content"] for m in sent] == ["first", "an answer"]


def test_a_delivered_message_is_sent_on_the_next_turn(db, registered, make_chat):
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat_service.create_message(db, chat, ROLE_USER, "first")
    waiting = chat_service.create_message(db, chat, ROLE_USER, "second", queued=True)

    waiting.queued = False
    db.commit()

    assert [m["content"] for m in chat_service.build_messages(db, chat)] == ["first", "second"]


# --- Queueing ----------------------------------------------------------------
def test_a_message_sent_during_a_reply_is_queued(
    client: TestClient, db, registered, make_chat, no_upstream
):
    """The bug this replaces: a second POST used to start a second generation."""
    _connection(db)
    chat_id = make_chat()
    _mid_reply(db, chat_id)

    response = client.post(f"/api/chats/{chat_id}/messages", data={"content": "and also this"})
    assert response.status_code == 200

    rows = _messages(db, chat_id)
    assert rows[-1].content == "and also this"
    assert rows[-1].queued is True
    # Exactly one reply in flight, which is the point.
    assert len([m for m in rows if m.role == ROLE_ASSISTANT and not m.complete]) == 1


def test_a_queued_bubble_never_carries_a_streaming_shell(
    client: TestClient, db, registered, make_chat, no_upstream
):
    """`sse-connect` is the only thing that starts a generation, so a queued
    turn carrying one would be the second concurrent reply all over again.

    Asserted on the body rather than on a row, unusually and deliberately: the
    attribute *is* the behaviour here.
    """
    _connection(db)
    chat_id = make_chat()
    _mid_reply(db, chat_id)

    body = client.post(f"/api/chats/{chat_id}/messages", data={"content": "later"}).text

    assert "sse-connect" not in body
    assert "Waiting to be sent" in body


def test_a_message_with_nothing_in_flight_is_sent_as_before(
    client: TestClient, db, registered, make_chat, no_upstream
):
    _connection(db)
    chat_id = make_chat()

    response = client.post(f"/api/chats/{chat_id}/messages", data={"content": "hello"})

    assert "sse-connect" in response.text
    rows = _messages(db, chat_id)
    assert rows[0].queued is False
    assert rows[1].role == ROLE_ASSISTANT and rows[1].complete is False


def test_the_queue_is_bounded(client: TestClient, db, registered, make_chat, no_upstream):
    """A `for` loop in the terminal panel with Auto send on can produce commands
    far faster than any model answers them."""
    _connection(db)
    chat_id = make_chat()
    _mid_reply(db, chat_id)

    for n in range(MAX_QUEUED):
        assert client.post(
            f"/api/chats/{chat_id}/messages", data={"content": f"line {n}"}
        ).status_code == 200

    refused = client.post(f"/api/chats/{chat_id}/messages", data={"content": "one too many"})

    assert refused.status_code == 409
    assert len([m for m in _messages(db, chat_id) if m.queued]) == MAX_QUEUED


# --- Delivery ----------------------------------------------------------------
async def test_a_finished_reply_delivers_the_next_prompt(
    db, registered, make_chat, no_upstream
):
    _connection(db)
    chat_id = make_chat()
    reply = _mid_reply(db, chat_id)
    chat = db.get(Chat, chat_id)
    waiting = chat_service.create_message(db, chat, ROLE_USER, "next please", queued=True)

    generation = generation_service.Generation(chat_id=chat_id, message_id=reply.id)
    generation_service._drain(generation)

    db.refresh(waiting)
    assert waiting.queued is False
    assert generation.drained is True
    assert len([m for m in _messages(db, chat_id) if not m.complete]) == 2


async def test_only_one_prompt_is_delivered_at_a_time(db, registered, make_chat, no_upstream):
    """Draining the lot would put two consecutive user turns in the next
    request, which several local chat templates refuse outright."""
    _connection(db)
    chat_id = make_chat()
    reply = _mid_reply(db, chat_id)
    chat = db.get(Chat, chat_id)
    first = chat_service.create_message(db, chat, ROLE_USER, "one", queued=True)
    second = chat_service.create_message(db, chat, ROLE_USER, "two", queued=True)

    generation_service._drain(
        generation_service.Generation(chat_id=chat_id, message_id=reply.id)
    )

    db.refresh(first)
    db.refresh(second)
    assert first.queued is False
    assert second.queued is True


async def test_a_stopped_reply_leaves_the_queue_alone(db, registered, make_chat, no_upstream):
    """Stop means stop. This is the decision the whole feature was shaped
    around, and it must never be relaxed into "stop this one and start the
    next"."""
    _connection(db)
    chat_id = make_chat()
    reply = _mid_reply(db, chat_id)
    chat = db.get(Chat, chat_id)
    waiting = chat_service.create_message(db, chat, ROLE_USER, "next please", queued=True)

    generation = generation_service.Generation(chat_id=chat_id, message_id=reply.id)
    generation.stopped = True
    generation_service._drain(generation)

    db.refresh(waiting)
    assert waiting.queued is True
    assert generation.drained is False


async def test_an_errored_reply_leaves_the_queue_alone(db, registered, make_chat, no_upstream):
    """The endpoint has just failed; sending the next prompt into it spends
    somebody's words to produce a second failure."""
    _connection(db)
    chat_id = make_chat()
    reply = _mid_reply(db, chat_id)
    chat = db.get(Chat, chat_id)
    waiting = chat_service.create_message(db, chat, ROLE_USER, "next please", queued=True)

    generation = generation_service.Generation(chat_id=chat_id, message_id=reply.id)
    generation.error = "The endpoint refused."
    generation_service._drain(generation)

    db.refresh(waiting)
    assert waiting.queued is True


async def test_a_superseded_generation_does_not_drain(db, registered, make_chat, no_upstream):
    """A regeneration cancels its predecessor, whose `finally:` still runs --
    the same reason `_persist` refuses. Without this, regenerating would drain
    the queue as a side effect."""
    _connection(db)
    chat_id = make_chat()
    reply = _mid_reply(db, chat_id)
    chat = db.get(Chat, chat_id)
    waiting = chat_service.create_message(db, chat, ROLE_USER, "next please", queued=True)

    abandoned = generation_service.Generation(chat_id=chat_id, message_id=reply.id)
    generation_service._RUNNING[reply.id] = generation_service.Generation(
        chat_id=chat_id, message_id=reply.id
    )
    generation_service._drain(abandoned)

    db.refresh(waiting)
    assert waiting.queued is True


# --- Send now and Discard ----------------------------------------------------
def test_discarding_removes_the_row(client: TestClient, db, registered, make_chat):
    _connection(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    waiting = chat_service.create_message(db, chat, ROLE_USER, "never mind", queued=True)
    waiting_id = waiting.id

    response = client.post(f"/api/chats/{chat_id}/messages/{waiting_id}/discard")

    assert response.status_code == 200
    # Queried rather than `db.get`: the route committed in a session of its own,
    # and this one still holds the instance.
    db.expire_all()
    assert db.scalar(select(Message).where(Message.id == waiting_id)) is None


def test_discarding_a_delivered_message_is_refused(client: TestClient, db, registered, make_chat):
    """Discard removes a row outright, so it must only ever reach one that was
    never sent."""
    _connection(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    sent = chat_service.create_message(db, chat, ROLE_USER, "already gone")

    assert client.post(f"/api/chats/{chat_id}/messages/{sent.id}/discard").status_code == 404
    assert db.get(Message, sent.id) is not None


def test_send_now_delivers_and_starts_a_reply(
    client: TestClient, db, registered, make_chat, no_upstream
):
    _connection(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    waiting = chat_service.create_message(db, chat, ROLE_USER, "go on then", queued=True)

    response = client.post(f"/api/chats/{chat_id}/messages/{waiting.id}/send-now")

    assert response.status_code == 200
    db.refresh(waiting)
    assert waiting.queued is False
    assert len(no_upstream) == 1


def test_send_now_is_refused_while_a_reply_is_running(
    client: TestClient, db, registered, make_chat, no_upstream
):
    """Jumping the queue is starting a second generation, which is the thing
    this whole mechanism exists to stop."""
    _connection(db)
    chat_id = make_chat()
    _mid_reply(db, chat_id)
    chat = db.get(Chat, chat_id)
    waiting = chat_service.create_message(db, chat, ROLE_USER, "me first", queued=True)

    response = client.post(f"/api/chats/{chat_id}/messages/{waiting.id}/send-now")

    assert response.status_code == 409
    db.refresh(waiting)
    assert waiting.queued is True


def test_neither_route_reaches_another_readers_chat(
    client: TestClient, db, registered, make_chat
):
    _connection(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    waiting = chat_service.create_message(db, chat, ROLE_USER, "mine", queued=True)

    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )

    assert client.post(f"/api/chats/{chat_id}/messages/{waiting.id}/discard").status_code == 404
    assert client.post(f"/api/chats/{chat_id}/messages/{waiting.id}/send-now").status_code == 404
    assert db.get(Message, waiting.id) is not None


# --- Everything else that touches the thread ---------------------------------
def test_editing_is_refused_while_a_reply_is_running(
    client: TestClient, db, registered, make_chat, no_upstream
):
    """Editing rewinds and then starts a reply unconditionally. Pressing it
    mid-stream was a second concurrent generation behind a pencil icon, and was
    reachable before the queue existed too."""
    _connection(db)
    chat_id = make_chat()
    _mid_reply(db, chat_id)
    first = _messages(db, chat_id)[0]

    response = client.post(
        f"/api/chats/{chat_id}/messages/{first.id}/edit", data={"content": "rewritten"}
    )

    assert response.status_code == 409
    db.refresh(first)
    assert first.content == "what is lembas?"
    assert len([m for m in _messages(db, chat_id) if not m.complete]) == 1


def test_compaction_does_not_summarise_a_waiting_prompt(db, registered, make_chat):
    """It would fold words no model has seen into the record, and then deliver
    them again afterwards."""
    from lembas.services import compaction as compaction_service

    _connection(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat_service.create_message(db, chat, ROLE_USER, "what is lembas?")
    reply = chat_service.create_message(db, chat, ROLE_ASSISTANT, "Waybread.")
    chat_service.create_message(db, chat, ROLE_USER, "still waiting", queued=True)

    text = compaction_service.transcript(db, chat, upto=reply)

    assert "still waiting" not in text
