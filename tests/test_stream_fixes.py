"""The chat-window round of 2026-10-23, held here.

- Text before a tool call is not held back until the call appears.
- A thinking block closes when the model moves on, not at the end of the reply.
- "Preparing …" while a call's arguments stream.
- Stop lands with no chunk in flight: in a prefill, during a tool.
- An edit or a regenerate pressed while a reply runs stops it and goes ahead.
- The live tail is rendered at the cost of what is new.
- An image counts by its dimensions in the server's estimates.
"""

from __future__ import annotations

import asyncio
import base64
import struct
import time

from lembas.db.models import ROLE_ASSISTANT, Chat, Connection, Message, Model
from lembas.services import generation as generation_service
from lembas.services import steps as steps_service
from lembas.services import tokens
from lembas.services.markdown import LiveRender, render_markdown
from lembas.services.reasoning import CONTENT, ReasoningSplitter


def _chat(db, user_id, tools=True):
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": tools}))
    db.commit()
    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id, title="T",
                title_generated=True)
    db.add(chat)
    db.commit()
    db.add(Message(chat_id=chat.id, role="user", content="Look it up.", complete=True))
    db.commit()
    reply = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(reply)
    db.commit()
    return chat.id, reply.id


def _reasoning(text):
    return {"choices": [{"delta": {"reasoning_content": text}}]}


def _text(text):
    return {"choices": [{"delta": {"content": text}}]}


def _call(name, arguments):
    return {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": name, "arguments": arguments}}]}}]}


# --- Held-back text ---------------------------------------------------------------------
def test_text_with_no_tag_in_it_is_never_held_back():
    splitter = ReasoningSplitter()
    assert list(splitter.feed("Let me look that up for you.")) == [
        (CONTENT, "Let me look that up for you.")
    ]
    # Only a real tag's beginning waits.
    assert list(splitter.feed(" <thi")) == [(CONTENT, " ")]
    assert list(splitter.feed("nk>hm</think>ok")) == [("reasoning", "hm"), (CONTENT, "ok")]


# --- Thinking settles, "Preparing …" ---------------------------------------------------
async def test_thinking_closes_when_prose_starts_and_a_call_says_preparing(
    db, user_id, monkeypatch
):
    chat_id, message_id = _chat(db, user_id)
    seen: dict = {}
    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)

    async def stream_chat(_endpoint, payload):
        if seen.get("round"):
            yield _text("Done.")
            return
        seen["round"] = 1
        yield _reasoning("I should search.")
        await asyncio.sleep(0.02)
        yield _text("Let me look that up.")
        # The thinking is a closed step now, and the prose is whole in the tail.
        seen["marks_after_text"] = len(generation.steps)
        seen["tail_after_text"] = steps_service.tail(generation)
        yield _call("web_search", '{"query"')
        seen["status_preparing"] = generation.status
        yield _call("", ': "mallorn"}')

    async def fake_search(_config, _query, *, limit=None):
        return []

    monkeypatch.setattr(generation_service, "stream_chat", stream_chat)
    monkeypatch.setattr("lembas.services.search.run", fake_search)
    from lembas.services import settings_store

    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    await generation_service._run(generation)

    assert seen["marks_after_text"] == 1
    assert generation.steps[0]["thinking_ms"] > 0
    assert seen["tail_after_text"] == ("", "Let me look that up.")
    assert seen["status_preparing"].startswith("Preparing ")
    stored = steps_service.for_message(db.get(Message, message_id))
    assert [s.kind for s in stored][:2] == ["thinking", "text"]
    assert stored[0].label.startswith("for ")


# --- Stop ------------------------------------------------------------------------------------
async def test_stop_lands_during_a_prefill_with_no_chunk_in_flight(db, user_id, monkeypatch):
    chat_id, message_id = _chat(db, user_id, tools=False)
    closed = asyncio.Event()

    async def stream_chat(_endpoint, payload):
        try:
            await asyncio.sleep(60)  # the upstream is still processing the prompt
            yield _text("too late")
        finally:
            closed.set()

    monkeypatch.setattr(generation_service, "stream_chat", stream_chat)
    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    task = asyncio.create_task(generation_service._run(generation))
    await asyncio.sleep(0.2)
    started = time.monotonic()
    generation_service._RUNNING[message_id] = generation
    assert generation_service.request_stop(message_id)
    await asyncio.wait_for(task, 5)
    assert time.monotonic() - started < 2
    assert generation.stopped and closed.is_set(), "the upstream stream was closed"
    assert db.get(Message, message_id) is not None


async def test_stop_lands_while_a_tool_runs(db, user_id, monkeypatch):
    chat_id, message_id = _chat(db, user_id)
    requests = []

    async def stream_chat(_endpoint, payload):
        requests.append(payload)
        yield _call("web_search", '{"query": "mallorn"}')

    async def slow_search(_config, _query, *, limit=None):
        await asyncio.sleep(60)
        return []

    monkeypatch.setattr(generation_service, "stream_chat", stream_chat)
    monkeypatch.setattr("lembas.services.search.run", slow_search)
    from lembas.services import settings_store

    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    generation = generation_service.Generation(chat_id=chat_id, message_id=message_id)
    generation_service._RUNNING[message_id] = generation
    task = asyncio.create_task(generation_service._run(generation))
    await asyncio.sleep(0.3)
    started = time.monotonic()
    generation_service.request_stop(message_id)
    await asyncio.wait_for(task, 5)
    assert time.monotonic() - started < 2
    assert generation.stopped
    assert len(requests) == 1, "no second request after a stop"
    assert generation.tool_events[-1]["error"] == "Stopped before it finished."


# --- Rewind while a reply runs ------------------------------------------------------------
async def test_an_edit_while_a_reply_runs_stops_it_and_goes_ahead(db, user_id, monkeypatch):
    from lembas.api import chats as chats_api
    from lembas.db.models import User

    chat_id, message_id = _chat(db, user_id, tools=False)

    async def stream_chat(_endpoint, payload):
        if payload["messages"][-1]["content"].endswith("Edited."):
            yield _text("New answer.")
            return
        await asyncio.sleep(60)
        yield _text("never")

    monkeypatch.setattr(generation_service, "stream_chat", stream_chat)
    running = generation_service.ensure(chat_id, message_id)
    await asyncio.sleep(0.2)
    chat = db.get(Chat, chat_id)
    asked = db.query(Message).filter_by(chat_id=chat_id, role="user").one()
    started = time.monotonic()
    reply = await chats_api.rewind_to(db, chat, db.get(User, user_id), asked.id, "Edited.")
    assert time.monotonic() - started < 3
    assert running.stopped
    assert reply.id != message_id


# --- The live tail --------------------------------------------------------------------------
def test_the_live_tail_renders_the_same_text_for_far_less():
    body = ("A **paragraph** with `code`.\n\n" * 3 + "```python\n" + "x = 1\n" * 30 + "```\n\n") * 8
    live = LiveRender()
    out = ""
    for end in range(50, len(body) + 50, 50):
        out = live.render(body[:end])
    assert out.count("<p>") == render_markdown(body, live=True).count("<p>")
    assert out.count("<pre") == render_markdown(body).count("<pre")


def test_live_code_is_not_highlighted_but_the_final_render_is():
    text = "```python\ndef f():\n    return 1\n```"
    assert "class=\"k\"" not in render_markdown(text, live=True)
    assert "def" in render_markdown(text, live=True)


def test_a_tail_that_starts_over_is_rendered_from_scratch():
    live = LiveRender()
    live.render("First round's prose.\n\nMore.")
    assert "Second" in live.render("Second round.") and "First" not in live.render("Second round.")


# --- Images in the server's estimates ---------------------------------------------------------
def test_an_image_counts_by_its_dimensions():
    header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 2560, 1440)
    png = header + b"\0" * 1_500_000
    data = base64.b64encode(png).decode()
    assert tokens.image_tokens(data) == 92 * 52
    part = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}
    assert tokens.estimate_content([part]) == 92 * 52
    from lembas.services.llm.anthropic_client import _estimate

    block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}
    assert _estimate([{"role": "user", "content": [block]}]) < 6000
