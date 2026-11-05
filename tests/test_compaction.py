"""Compaction: what is summarised, what is sent, and when it happens by itself."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Message, Model
from lembas.services import chat as chat_service
from lembas.services import compaction as compaction_service
from lembas.services import prompts, settings_store
from lembas.services.crypto import encrypt


@pytest.fixture
def chat(db, registered, make_chat) -> Chat:
    connection = Connection(
        name="Test", base_url="http://x.test", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(
        Model(connection_id=connection.id, model_id="test-model", context_length=1000)
    )
    db.commit()
    return db.get(Chat, make_chat())


def _exchange(db, chat: Chat, *, question: str, answer: str, minutes: int = 0) -> Message:
    """One user turn and its reply, backdated so ordering is deterministic."""
    when = datetime.now(UTC) - timedelta(minutes=minutes)
    db.add(Message(chat_id=chat.id, role="user", content=question, created_at=when))
    reply = Message(
        chat_id=chat.id,
        role="assistant",
        content=answer,
        created_at=when + timedelta(seconds=1),
    )
    db.add(reply)
    db.commit()
    return reply


def _usage(reply: Message, *, context_tokens: int, limit: int = 1000) -> None:
    reply.usage_json = {
        "prompt_tokens": context_tokens,
        "completion_tokens": 0,
        "total_tokens": context_tokens,
        "context_tokens": context_tokens,
        "context_limit": limit,
        "estimated": False,
        "elapsed_ms": 10,
        "rounds": 1,
    }


# --- The boundary -------------------------------------------------------------
def test_an_uncompacted_chat_splits_into_nothing_and_everything(db, chat):
    _exchange(db, chat, question="one", answer="two")
    messages = list(db.scalars(select(Message).order_by(Message.created_at)))
    assert compaction_service.split(db, chat, messages) == ([], messages)


def test_a_dangling_cutoff_reads_as_uncompacted(db, chat):
    """There is no foreign key to null it out on an upgraded database, so the
    guard is load-bearing rather than defensive."""
    reply = _exchange(db, chat, question="one", answer="two", minutes=10)
    compaction_service.apply(chat, summary="a summary", upto=reply)
    db.commit()

    db.delete(reply)
    db.commit()

    assert compaction_service.cutoff_message(db, chat) is None
    assert compaction_service.split(db, chat, [])[0] == []


def test_the_cutoff_lands_on_a_finished_reply(db, chat):
    """So the kept history starts on a user turn, which is what every chat
    template expects."""
    _exchange(db, chat, question="one", answer="two", minutes=10)
    db.add(Message(chat_id=chat.id, role="user", content="three"))
    db.commit()

    assert compaction_service.last_complete(db, chat).content == "two"


# --- What gets sent -----------------------------------------------------------
def test_the_summary_replaces_the_compacted_turns(db, chat):
    old = _exchange(db, chat, question="what is lembas?", answer="Waybread.", minutes=10)
    _exchange(db, chat, question="how much?", answer="A bite.", minutes=1)
    compaction_service.apply(chat, summary="They asked about lembas.", upto=old)
    db.commit()

    messages = chat_service.build_messages(db, chat)
    contents = [m["content"] for m in messages]

    assert "what is lembas?" not in contents
    assert "how much?" in contents
    assert any("They asked about lembas." in c for c in contents)


def test_the_summary_is_carried_by_a_user_and_an_assistant_turn(db, chat):
    """A leading assistant turn breaks templates requiring the first non-system
    message to be user; a lone leading user turn produces user, user whenever the
    kept history starts on a user turn -- which it always does."""
    old = _exchange(db, chat, question="one", answer="two", minutes=10)
    _exchange(db, chat, question="three", answer="four", minutes=1)
    compaction_service.apply(chat, summary="Summary.", upto=old)
    db.commit()

    roles = [m["role"] for m in chat_service.build_messages(db, chat)]
    assert roles[:2] == ["user", "assistant"]
    # And it still alternates from there.
    assert roles == ["user", "assistant", "user", "assistant"]


def test_there_is_still_exactly_one_system_message(db, chat):
    settings_store.update(db, {"system_prompt": "Speak as Gandalf."})
    old = _exchange(db, chat, question="one", answer="two", minutes=10)
    compaction_service.apply(chat, summary="Summary.", upto=old)
    db.commit()

    roles = [m["role"] for m in chat_service.build_messages(db, chat, system_prompt="S")]
    assert roles.count("system") == 1
    assert roles[0] == "system"


def test_clearing_the_lead_fragment_still_sends_the_summary(db, chat):
    prompts.save(db, {"task.compact_lead": "", "task.compact_ack": ""})
    old = _exchange(db, chat, question="one", answer="two", minutes=10)
    compaction_service.apply(chat, summary="Summary.", upto=old)
    db.commit()

    messages = chat_service.build_messages(db, chat)
    assert messages[0] == {"role": "user", "content": "Summary."}


# --- The request (spec 3.1.0, context.md §7) ----------------------------------------
def _sse(text: str, *, usage: dict | None = None) -> str:
    """A streamed reply saying `text`, with a usage chunk when given."""
    frames = [json.dumps({"choices": [{"delta": {"content": text}}]})]
    if usage is not None:
        frames.append(json.dumps({"choices": [], "usage": usage}))
    return "".join(f"data: {frame}\n\n" for frame in frames) + "data: [DONE]\n\n"


def _asks_for_summary(body: dict) -> bool:
    last = (body.get("messages") or [{}])[-1]
    return last.get("role") == "user" and "Summarise this conversation" in str(
        last.get("content")
    )


async def test_the_summary_is_the_next_turn_of_the_same_conversation(db, chat, mock_http):
    """The same system prompt, the same tools, every turn as it was sent -- and
    then the compaction text with an empty transcript. Nothing is pasted: the
    model has the conversation, so a server that cached it reads only the ask."""
    from lembas.db.models import User

    user = db.get(User, chat.user_id)
    db.add(Message(chat_id=chat.id, role="user", content="what is lembas?"))
    db.commit()
    placeholder = Message(chat_id=chat.id, role="assistant", content="", complete=False)
    db.add(placeholder)
    db.commit()
    tools = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
    sent = chat_service.build_request(
        db, chat, upto=placeholder, tools=tools, user=user, persist=True
    )
    db.commit()
    placeholder.content, placeholder.complete = "Waybread.", True
    db.commit()

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=_sse("Asking about lembas."))

    mock_http(handler)
    from lembas.services import tools as tools_service

    real = tools_service.resolve_tools

    class _Set:
        schemas = tools

    tools_service.resolve_tools = lambda *a, **k: _Set()
    try:
        summary = await compaction_service.summarise(chat.id, upto_id=placeholder.id)
    finally:
        tools_service.resolve_tools = real

    assert summary == "Asking about lembas."
    body = seen[0]
    assert body["tools"] == sent["tools"]
    assert body["messages"][: len(sent["messages"])] == sent["messages"]
    assert body["messages"][len(sent["messages"])] == {
        "role": "assistant",
        "content": "Waybread.",
    }
    ask = body["messages"][-1]
    assert _asks_for_summary(body)
    # The transcript is empty in this form: no turn is pasted into the ask.
    assert "what is lembas?" not in ask["content"]
    assert len(body["messages"]) == len(sent["messages"]) + 2


async def test_a_tool_call_in_the_summary_is_not_run(db, chat, mock_http):
    """Its text before the call is the summary; a reply with nothing before the
    call is no summary."""
    reply = _exchange(db, chat, question="one", answer="two", minutes=5)
    call = {
        "choices": [
            {"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {
                "name": "read", "arguments": "{}"}}]}}
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        frames = [
            {"choices": [{"delta": {"content": "The summary."}}]},
            call,
            {"choices": [{"delta": {"content": " after"}}]},
        ]
        body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body)

    mock_http(handler)
    assert await compaction_service.summarise(chat.id, upto_id=reply.id) == "The summary."

    mock_http(lambda request: httpx.Response(
        200, text=f"data: {json.dumps(call)}\n\ndata: [DONE]\n\n"
    ))
    assert await compaction_service.summarise(chat.id, upto_id=reply.id) == ""


async def test_the_summary_request_is_counted_with_its_cached_tokens(db, chat, mock_http):
    from lembas.db.models import Usage

    reply = _exchange(db, chat, question="one", answer="two", minutes=5)
    usage = {
        "prompt_tokens": 900,
        "completion_tokens": 40,
        "prompt_tokens_details": {"cached_tokens": 880},
    }
    mock_http(lambda request: httpx.Response(200, text=_sse("S.", usage=usage)))

    assert await compaction_service.summarise(chat.id, upto_id=reply.id) == "S."
    db.expire_all()
    row = db.scalar(select(Usage).where(Usage.user_id == chat.user_id))
    assert (row.tokens_in, row.tokens_cached, row.tokens_out, row.requests) == (900, 880, 40, 1)


def test_the_ask_reads_right_with_both_variables_empty(db, chat):
    """The model has the conversation, the earlier summary as its lead turn:
    nothing is pasted, and no heading is left hanging over nothing."""
    template = prompts.resolve(db, "task.compact")
    ask = prompts.substitute(template, {"transcript": "", "previous_summary": ""}).strip()
    assert ask.startswith("Summarise this conversation so far")
    assert "below" not in ask
    assert ask.endswith("stay under 500 words.")


def test_compaction_makes_the_system_prompt_again(db, chat):
    """§10 rule 1: everything is read again after a compaction anyway, so the
    frozen prompt goes and the next request makes it from what is true then."""
    from lembas.db.models import User

    reply = _exchange(db, chat, question="one", answer="two", minutes=5)
    chat_service.build_request(db, chat, user=db.get(User, chat.user_id), persist=True)
    db.commit()
    assert chat.system_frozen

    compaction_service.apply(chat, summary="Summary.", upto=reply)
    db.commit()
    assert not chat.system_frozen


# --- The button ---------------------------------------------------------------
def _summariser(text: str = "## What we are doing\n\nAsking about lembas."):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_sse(text))

    return handler


async def test_compacting_summarises_and_hides_the_earlier_turns(
    client: TestClient, db, chat, mock_http
):
    _exchange(db, chat, question="what is lembas?", answer="Waybread.", minutes=10)
    mock_http(_summariser())

    response = client.post(f"/api/chats/{chat.id}/compact")
    assert response.status_code == 200

    db.refresh(chat)
    assert "Asking about lembas." in chat.compact_summary
    assert chat.compacted_through_id
    # The messages are still there, behind the divider.
    assert "earlier messages, summarised" in response.text
    assert db.scalar(select(Message).where(Message.content == "what is lembas?")) is not None


async def test_compacting_while_a_reply_is_being_written_is_refused(
    client: TestClient, db, chat, mock_http
):
    _exchange(db, chat, question="one", answer="two", minutes=10)
    db.add(Message(chat_id=chat.id, role="assistant", content="", complete=False))
    db.commit()

    response = client.post(f"/api/chats/{chat.id}/compact")
    assert response.status_code == 409


async def test_compaction_can_be_turned_off_by_clearing_its_prompt(
    client: TestClient, db, chat, mock_http
):
    _exchange(db, chat, question="one", answer="two", minutes=10)
    prompts.save(db, {"task.compact": ""})

    response = client.post(f"/api/chats/{chat.id}/compact")
    assert response.status_code == 409
    assert "turned off" in response.json()["detail"]


async def test_compacting_an_empty_chat_is_refused(client: TestClient, db, chat, mock_http):
    assert client.post(f"/api/chats/{chat.id}/compact").status_code == 409


# --- Rewinding across the boundary --------------------------------------------
def test_editing_at_or_before_the_cutoff_clears_the_compaction(
    client: TestClient, db, chat, mock_http
):
    """A rewind deletes everything after the edited message, so a boundary at or
    behind it no longer describes anything that exists."""
    first_reply = _exchange(db, chat, question="one", answer="two", minutes=20)
    _exchange(db, chat, question="three", answer="four", minutes=10)
    compaction_service.apply(chat, summary="Summary.", upto=first_reply)
    db.commit()

    first_user = db.scalar(select(Message).where(Message.content == "one"))
    mock_http(_summariser())
    client.post(
        f"/api/chats/{chat.id}/messages/{first_user.id}/edit", data={"content": "one again"}
    )

    db.refresh(chat)
    assert chat.compact_summary == ""
    assert chat.compacted_through_id is None


# --- Automatic ----------------------------------------------------------------
def test_it_fires_when_the_window_is_nearly_full(db, chat):
    reply = _exchange(db, chat, question="one", answer="two", minutes=5)
    _usage(reply, context_tokens=960)
    db.commit()

    assert compaction_service.should_compact(db, chat) is True


def test_it_does_not_fire_with_room_to_spare(db, chat):
    reply = _exchange(db, chat, question="one", answer="two", minutes=5)
    _usage(reply, context_tokens=400)
    db.commit()

    assert compaction_service.should_compact(db, chat) is False


def test_a_large_pending_turn_is_counted(db, chat):
    """The recorded figure is one turn stale. Fifty thousand characters pasted
    into the composer overflow a window that measured 90% last time."""
    reply = _exchange(db, chat, question="one", answer="two", minutes=5)
    _usage(reply, context_tokens=900)
    db.commit()

    assert compaction_service.should_compact(db, chat) is False
    assert compaction_service.should_compact(db, chat, pending="x" * 400) is True


def test_it_never_fires_without_a_context_length(db, chat):
    """Acting on a number nobody supplied is exactly what 0-means-unknown is
    there to prevent."""
    reply = _exchange(db, chat, question="one", answer="two", minutes=5)
    _usage(reply, context_tokens=99_000, limit=0)
    db.commit()

    assert compaction_service.should_compact(db, chat) is False


def test_a_threshold_of_zero_turns_it_off(db, chat):
    settings_store.update(db, {"compact_threshold": 0})
    reply = _exchange(db, chat, question="one", answer="two", minutes=5)
    _usage(reply, context_tokens=999)
    db.commit()

    assert compaction_service.should_compact(db, chat) is False


def test_it_does_not_fire_on_the_first_turn(db, chat):
    assert compaction_service.should_compact(db, chat) is False


def test_it_acts_on_estimated_counts_too(db, chat):
    """A premature compaction costs one turn of answer quality, not data -- the
    messages are still there. That is what makes acting on an estimate safe."""
    reply = _exchange(db, chat, question="one", answer="two", minutes=5)
    _usage(reply, context_tokens=960)
    reply.usage_json = {**reply.usage_json, "estimated": True}
    db.commit()

    assert compaction_service.should_compact(db, chat) is True


async def test_a_generation_compacts_before_it_asks(db, chat, mock_http):
    """At the top of _run, so build_request is called once and what goes out is
    the compacted conversation."""
    from lembas.services import generation as generation_service

    reply = _exchange(db, chat, question="what is lembas?", answer="Waybread.", minutes=10)
    _usage(reply, context_tokens=980)
    db.commit()

    pending = Message(chat_id=chat.id, role="user", content="more?")
    db.add(pending)
    db.commit()
    placeholder = Message(chat_id=chat.id, role="assistant", content="", complete=False)
    db.add(placeholder)
    db.commit()

    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if _asks_for_summary(body):
            return httpx.Response(200, text=_sse("A summary."))
        return httpx.Response(200, text="data: [DONE]\n\n")

    mock_http(handler)
    generation = generation_service.Generation(chat_id=chat.id, message_id=placeholder.id)
    generation_service._RUNNING[placeholder.id] = generation
    await generation_service._run(generation)
    generation_service._RUNNING.clear()

    db.expire_all()
    assert db.get(Chat, chat.id).compact_summary == "A summary."
    # The summary was asked as the conversation's next turn, then the reply's
    # request went out after compaction, carrying the summary.
    assert _asks_for_summary(sent[0])
    assert any(m.get("content") == "Waybread." for m in sent[0]["messages"])
    streamed = next(b for b in sent if not _asks_for_summary(b))
    assert any("A summary." in str(m.get("content")) for m in streamed["messages"])
    assert not any(m.get("content") == "what is lembas?" for m in streamed["messages"])


async def test_a_failed_compaction_still_sends_the_reply(db, chat, mock_http):
    """Refusing to answer because the summariser was unavailable is a worse
    trade than sending the uncompacted request."""
    from lembas.services import generation as generation_service

    reply = _exchange(db, chat, question="one", answer="two", minutes=10)
    _usage(reply, context_tokens=980)
    db.commit()
    placeholder = Message(chat_id=chat.id, role="assistant", content="", complete=False)
    db.add(placeholder)
    db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if not _asks_for_summary(json.loads(request.content)):
            return httpx.Response(
                200, text='data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
            )
        return httpx.Response(500, json={"error": {"message": "no"}})

    mock_http(handler)
    generation = generation_service.Generation(chat_id=chat.id, message_id=placeholder.id)
    generation_service._RUNNING[placeholder.id] = generation
    await generation_service._run(generation)
    generation_service._RUNNING.clear()

    assert generation.text == "hi"
    assert not generation.error
