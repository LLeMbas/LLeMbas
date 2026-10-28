"""`_lembas/show` (harness spec 3.0.0): a device's `show` tool sends a file's text,
which is kept as a blob attached to the chat and becomes the same card the
chat's own `show` makes. The device is a `Link` with its requests answered here
(test_device_sync.py's harness); nothing runs on this machine.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from lembas.db.models import Message, ShownFile
from lembas.services import blobs
from lembas.services import chat as chat_service
from lembas.services import generation as generation_service
from tests.test_device_sync import _agents_on, _clean, _device_chat, _linked, _say  # noqa: F401


def _shows(link, answers: list[dict], *, text="# Plan\n\nship it\n", call="call_7", **extra):
    """A turn whose `show` call sends `text` for docs/plan.md."""

    async def answer(method, params):
        if method != "session/prompt":
            return {}
        events = [{"type": "task", "state": "start"},
                  {"type": "tool_start", "id": call, "name": "show",
                   "args": {"path": "docs/plan.md"}}]
        for event in events:
            await link._notification("_lembas/event", {"sessionId": "s1", "event": event})
        request = {"jsonrpc": "2.0", "id": 41, "method": "_lembas/show", "params": {
            "sessionId": "s1", "toolCallId": call, "path": "docs/plan.md", "kind": "markdown",
            "text": text, **extra,
        }}
        await link.receive(json.dumps(request))
        for _ in range(100):
            if answers:
                break
            await asyncio.sleep(0.01)
        shown = answers[-1]["result"]
        for event in (
            {"type": "tool_end", "id": call, "name": "show", "result": {
                "output": "Shown." if shown.get("shown") else shown.get("reason", ""),
                "isError": not shown.get("shown")}},
            {"type": "text", "text": "There it is."},
            {"type": "task", "state": "end"},
        ):
            await link._notification("_lembas/event", {"sessionId": "s1", "event": event})
        return {"stopReason": "end_turn"}

    return answer


def _run(db, make_chat, **kwargs):
    answers: list[dict] = []
    holder: dict = {}

    def answer(method, params):
        return holder["answer"](method, params)

    link, _ = _linked(db, answer=answer)
    link.initialized = {"_meta": {"lembas": {"capabilities": ["sessions", "show"]}}}
    real_send = link.send_text

    async def send_text(text):
        message = json.loads(text)
        if message.get("id") == 41:
            answers.append(message)
        await real_send(text)

    link.send_text = send_text
    holder["answer"] = _shows(link, answers, **kwargs)
    chat_id = _device_chat(db, make_chat, link)
    _say(db, chat_id, "user", "show me the plan")
    reply_id = _say(db, chat_id, "assistant", "", complete_=False)

    async def scenario():
        generation_service.ensure(chat_id, reply_id)
        await asyncio.wait_for(generation_service._TASKS[reply_id], 5)

    asyncio.run(scenario())
    db.expire_all()
    return chat_id, db.get(Message, reply_id), answers


def test_a_device_s_show_is_kept_and_drawn_as_the_chat_s_show_card(
    client, db, registered, make_chat
):
    chat_id, reply, answers = _run(db, make_chat)
    assert answers, reply.error
    assert answers[-1]["result"] == {"shown": True}
    assert reply.content == "There it is."
    tool = next(t for t in reply.tool_calls_json if t.get("name") == "show")
    show = tool["show"]
    # The chat `show` tool's keys, the tree's ids empty, and the kept copy named.
    assert {"path", "name", "kind", "language", "line", "node_id", "version_id", "sha",
            "size", "mime"} <= set(show)
    assert show["source"] == "device" and show["node_id"] == "" and show["kind"] == "markdown"
    assert show["path"] == "docs/plan.md" and show["name"] == "plan.md"
    row = db.get(ShownFile, show["shown_id"])
    assert row.chat_id == chat_id and blobs.refcount(db, row.blob_hash) == 1
    # The viewer reads it as plain text, never as a page.
    page = client.get(show["url"])
    assert page.status_code == 200 and page.text == "# Plan\n\nship it\n"
    assert page.headers["content-type"].startswith("text/plain")
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in page.headers["content-security-policy"]
    assert client.get(f"/api/chats/{chat_id}/shown/nope").status_code == 404


def test_html_is_still_served_as_text(client, db, registered, make_chat):
    chat_id, reply, _ = _run(db, make_chat, text="<script>alert(1)</script>", kind="html")
    show = next(t for t in reply.tool_calls_json if t.get("name") == "show")["show"]
    page = client.get(show["url"])
    assert page.headers["content-type"].startswith("text/plain")


def test_over_a_megabyte_is_refused_with_the_reason(client, db, registered, make_chat):
    _, reply, answers = _run(db, make_chat, text="x" * (1024 * 1024 + 1))
    result = answers[-1]["result"]
    assert result["shown"] is False and "at most 1 MB" in result["reason"]
    tool = next(t for t in reply.tool_calls_json if t.get("name") == "show")
    assert "show" not in tool
    assert db.scalar(select(ShownFile)) is None


def test_deleting_the_chat_releases_the_blob(client, db, registered, make_chat):
    from lembas.db.models import Chat

    chat_id, reply, _ = _run(db, make_chat)
    row = db.scalar(select(ShownFile))
    digest = row.blob_hash
    chat_service.delete_chats(db, [db.get(Chat, chat_id)])
    db.commit()
    assert db.scalar(select(ShownFile)) is None
    assert blobs.refcount(db, digest) == 0
    assert blobs.recount(db) == 0


def test_a_show_for_a_session_nobody_has_open_is_not_shown(db, registered):
    link, _ = _linked(db)
    sent: list[dict] = []

    async def send_text(text):
        sent.append(json.loads(text))

    link.send_text = send_text

    async def scenario():
        await link.receive(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "_lembas/show",
                                       "params": {"sessionId": "zz", "path": "a.md",
                                                  "kind": "markdown", "text": "x"}}))
        for _ in range(100):
            if sent:
                return
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    assert sent[-1]["result"]["shown"] is False
