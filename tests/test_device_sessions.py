"""One session, both sides: a session the LLeMbas CLI's terminal shares is a
chat here, its turns mirrored as they happen; a chat keeps the device's id for
its session; an approval asked on both sides goes when the terminal answers it;
and a device reply says how long it thought.

A fake device speaks the device's half over the real WebSocket, as in
test_device_link.py. Nothing runs on this machine.
"""

from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select

from lembas.db.models import TOKEN_DEVICE, Chat, Message, User
from lembas.security import api_tokens
from lembas.services import device_link, settings_store
from lembas.services import generation as generation_service


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    yield
    device_link.reset()


def _device(db) -> tuple[str, str]:
    from lembas.db.models import Connection, Model

    if db.scalar(select(Connection)) is None:
        connection = Connection(name="c", base_url="http://127.0.0.1:1/v1", api_key_encrypted="")
        db.add(connection)
        db.commit()
        db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
        db.add(Model(connection_id=connection.id, model_id="flash"))
        db.commit()
    # Agent chats are behind Admin -> Agents.
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    user = db.scalar(select(User))
    token, raw = api_tokens.create(
        db,
        user,
        name="LLeMbas CLI on device-1",
        scopes=["models", "link"],
        kind=TOKEN_DEVICE,
        device_host="device-1",
    )
    return token.id, raw


def _send(ws, body: dict) -> None:
    ws.send_text(json.dumps({"jsonrpc": "2.0", **body}))


def _hello(ws) -> None:
    _send(
        ws,
        {
            "method": "_lembas/hello",
            "params": {
                "protocol": 1,
                "version": "0.24.0",
                "device": {"host": "device-1"},
                "limits": {"roots": ["/home/dev"], "max_mode": "auto"},
            },
        },
    )
    init = ws.receive_json()
    assert init["method"] == "initialize"
    _send(
        ws,
        {
            "id": init["id"],
            "result": {
                "protocolVersion": 1,
                "_meta": {
                    "lembas": {"capabilities": ["directories", "configure", "steer", "sessions"]}
                },
            },
        },
    )


def _linked(token_id: str) -> None:
    for _ in range(100):
        link = device_link.link_for(token_id)
        if link is not None and link.initialized:
            return
        time.sleep(0.02)
    raise AssertionError("the device never linked")


def _event(ws, session_id: str, event: dict) -> None:
    _send(ws, {"method": "_lembas/event", "params": {"sessionId": session_id, "event": event}})


def _announce(ws, session_id: str, **extra) -> None:
    _send(
        ws,
        {
            "method": "_lembas/session/announce",
            "params": {
                "sessionId": session_id,
                "cwd": "/home/dev/app",
                "title": extra.pop("title", ""),
                "model": "example/flash",
                "mode": "auto",
                "effort": "off",
                "origin": "terminal",
                **extra,
            },
        },
    )


def _history(ws, turns: list[dict]) -> None:
    asked = ws.receive_json()
    assert asked["method"] == "_lembas/session/history"
    _send(ws, {"id": asked["id"], "result": {"turns": turns}})


def _until(check, what: str):
    for _ in range(200):
        value = check()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError(f"timed out waiting for {what}")


def _chat(db, session_id: str) -> Chat | None:
    db.expire_all()
    return db.scalar(select(Chat).where(Chat.device_session_id == session_id))


def _history_rows(db, chat_id: str, count: int) -> list[Message] | None:
    """The chat's messages, once there are `count` of them."""
    db.expire_all()
    rows = db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    ).all()
    return list(rows) if len(rows) >= count else None


def _finished(db, chat_id: str) -> Message | None:
    db.expire_all()
    rows = db.scalars(
        select(Message)
        .where(Message.chat_id == chat_id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
    ).all()
    return rows[0] if rows and rows[0].complete else None


def test_a_shared_session_with_history_becomes_a_chat(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _linked(token_id)
        _announce(ws, "ses_1", title="Fix the login form")
        _history(
            ws,
            [
                {"role": "user", "text": "look at login.py"},
                {
                    "role": "assistant",
                    "text": "It reads the form twice.",
                    "reasoning": "hm",
                    "tools": [
                        {"id": "c1", "name": "read", "args": '{"path": "login.py"}', "output": "x"}
                    ],
                },
            ],
        )
        chat = _until(lambda: _chat(db, "ses_1"), "the chat")
        assert chat.device_token_id == token_id and chat.kind == "agent"
        assert chat.title == "Fix the login form" and chat.unread
        assert chat.project_dir == "/home/dev/app" and chat.agent_mode == "auto"
        # The model the terminal runs on, where this instance serves it.
        assert chat.model_id == "flash"
        # Waited for, not read once. The chat row is committed by
        # `_adopt_chat` and its history by `_import_turns` a moment later, in a
        # second transaction on the link's thread; under `-n 4` the read above
        # could land between the two and find the chat with nothing in it yet.
        # Two commits rather than one is harmless to a reader (the history
        # arrives a few milliseconds after the row), so it is the test that
        # waits -- with a deadline, never a bare sleep.
        rows = _until(lambda: _history_rows(db, chat.id, 2), "the imported history")
        assert [(m.role, m.origin) for m in rows] == [("user", "terminal"), ("assistant", "")]
        assert rows[1].tool_calls_json[0]["query"] == "login.py"
        page = client.get(f"/chat/{chat.id}").text
        assert "in the terminal" in page and "It reads the form twice." in page


def test_a_turn_typed_in_the_terminal_is_mirrored_and_timed(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _linked(token_id)
        _announce(ws, "ses_2")
        _history(ws, [])
        time.sleep(0.1)
        # An empty session leaves nothing behind until something is said in it.
        assert _chat(db, "ses_2") is None

        _event(ws, "ses_2", {"type": "task", "state": "start"})
        _event(ws, "ses_2", {"type": "prompt", "text": "what is in here?"})
        _event(ws, "ses_2", {"type": "step", "n": 1})
        _event(ws, "ses_2", {"type": "reasoning", "text": "Let me "})
        time.sleep(0.15)
        _event(ws, "ses_2", {"type": "reasoning", "text": "think."})
        _event(ws, "ses_2", {"type": "text", "text": "Two files."})
        chat = _until(lambda: _chat(db, "ses_2"), "the chat")
        _event(ws, "ses_2", {"type": "done", "reason": "stop"})
        _event(ws, "ses_2", {"type": "task", "state": "end"})
        reply = _until(lambda: _finished(db, chat.id), "the mirrored reply")
        assert reply.content == "Two files." and not reply.error
        asked = db.scalar(select(Message).where(Message.chat_id == chat.id, Message.role == "user"))
        assert asked.content == "what is in here?" and asked.origin == "terminal"
        # How long it thought, measured as the events came.
        assert reply.reasoning_ms >= 100
        assert reply.usage_json["thinking_ms"] >= 100
        page = client.get(f"/chat/{chat.id}").text
        assert "Thought for" in page and "thought " in page

        # Typed here, it goes to the terminal's session by its id -- no new one.
        client.post(f"/api/chats/{chat.id}/messages", data={"content": "and the tests?"})
        prompt = ws.receive_json()
        assert prompt["method"] == "session/prompt"
        assert prompt["params"]["sessionId"] == "ses_2"
        _event(ws, "ses_2", {"type": "text", "text": "None yet."})
        _send(ws, {"id": prompt["id"], "result": {"stopReason": "end_turn"}})
        _until(lambda: (r := _finished(db, chat.id)) and r.content == "None yet.", "the reply")


def test_a_chat_keeps_its_session_across_a_new_link(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _linked(token_id)
        client.post(
            "/api/chats/start",
            data={
                "content": "hello",
                "kind": "agent",
                "ssh_profile_id": f"device:{token_id}",
                "project_dir": "/home/dev",
                "agent_mode": "auto",
            },
        )
        chat = db.scalar(select(Chat))
        new = ws.receive_json()
        assert new["method"] == "session/new"
        _send(ws, {"id": new["id"], "result": {"sessionId": "ses_kept"}})
        prompt = ws.receive_json()
        _send(ws, {"id": prompt["id"], "result": {"stopReason": "end_turn"}})
        _until(lambda: _finished(db, chat.id), "the first reply")
        assert _chat(db, "ses_kept").id == chat.id

    # The service restarted: a new link, and the chat carries on the same session.
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _linked(token_id)
        client.post(f"/api/chats/{chat.id}/messages", data={"content": "again"})
        methods = []
        while True:
            message = ws.receive_json()
            methods.append(message["method"])
            assert message["params"]["sessionId"] == "ses_kept"
            if message["method"] == "session/prompt":
                break
            _send(ws, {"id": message["id"], "result": {}})
        assert "session/new" not in methods
        _send(ws, {"id": message["id"], "result": {"stopReason": "end_turn"}})
        _until(lambda: (r := _finished(db, chat.id)) and "afresh" not in r.content, "the reply")


def test_an_approval_answered_in_the_terminal_goes_from_here(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _linked(token_id)
        # Nothing here to show it on: said, not refused on the person's behalf.
        _send(
            ws,
            {
                "id": 5,
                "method": "session/request_permission",
                "params": {
                    "sessionId": "nobody",
                    "toolCall": {"toolCallId": "x", "title": "ls"},
                    "options": [],
                    "_meta": {"lembas": {"patient": True}},
                },
            },
        )
        assert "error" in ws.receive_json()

        _announce(ws, "ses_3")
        _history(ws, [])
        _event(ws, "ses_3", {"type": "prompt", "text": "run it"})
        chat = _until(lambda: _chat(db, "ses_3"), "the chat")
        _until(lambda: generation_service.running_for(chat.id), "the mirror")
        _send(
            ws,
            {
                "id": 9,
                "method": "session/request_permission",
                "params": {
                    "sessionId": "ses_3",
                    "toolCall": {
                        "toolCallId": "call-1",
                        "title": "rm x",
                        "rawInput": {"command": "rm x"},
                    },
                    "options": [{"optionId": "once"}, {"optionId": "deny"}],
                    "_meta": {"lembas": {"patient": True}},
                },
            },
        )
        _until(lambda: generation_service.running_for(chat.id).pending, "the card")
        _send(
            ws,
            {
                "method": "_lembas/permission/settled",
                "params": {"sessionId": "ses_3", "toolCallId": "call-1"},
            },
        )
        answered = ws.receive_json()
        assert answered["id"] == 9
        _until(lambda: generation_service.running_for(chat.id).pending is None, "the card gone")
        assert not generation_service.running_for(chat.id).stopped
        _event(ws, "ses_3", {"type": "task", "state": "end"})
        _until(lambda: _finished(db, chat.id), "the reply")


def test_the_device_reply_thinking_is_timed_per_round():
    g = generation_service.Generation(chat_id="c", message_id="m")
    device_link.apply_event(g, {"type": "reasoning", "text": "a"})
    time.sleep(0.05)
    device_link.apply_event(g, {"type": "reasoning", "text": "b"})
    device_link.apply_event(g, {"type": "tool_start", "id": "t", "name": "ls"})
    first = g.thinking_ms
    assert first >= 50 and g.reasoning_ms == first
    device_link.apply_event(g, {"type": "step", "n": 2})
    device_link.apply_event(g, {"type": "reasoning", "text": "c"})
    time.sleep(0.03)
    device_link.apply_event(g, {"type": "reasoning", "text": "d"})
    device_link.apply_event(g, {"type": "text", "text": "done"})
    assert g.thinking_ms >= first + 30
    # The step mark carries the cumulative figure the block labels are diffed from.
    assert g.steps[0]["thinking_ms"] == first


def test_the_todo_list_is_a_panel_above_the_composer_not_a_block(client, db, registered):
    """A device's todo list (the former `todo` tool) arrives as the chat's
    plan -- one "Work" phase, `plans.from_todos` -- and is shown in the plan
    panel above the composer, never as a block in the transcript. `todos_json`
    is not written any more (1.3.0)."""
    from lembas.services import plans

    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _linked(token_id)
        _announce(ws, "ses_4")
        _history(ws, [])
        _event(ws, "ses_4", {"type": "prompt", "text": "do the three things"})
        chat = _until(lambda: _chat(db, "ses_4"), "the chat")
        items = [
            {"content": "Read the form", "status": "completed"},
            {"content": "Fix the <validation>", "status": "in_progress"},
            {"content": "Write a test", "status": "pending"},
        ]
        _event(ws, "ses_4", {"type": "tool_start", "id": "t1", "name": "todo", "args": {}})
        _event(ws, "ses_4", {"type": "todos", "items": items})
        _event(
            ws,
            "ses_4",
            {
                "type": "tool_end",
                "id": "t1",
                "name": "todo",
                "ms": 1,
                "result": {"output": "[x] Read the form", "title": "1/3 done"},
            },
        )
        _event(ws, "ses_4", {"type": "text", "text": "On it."})
        _event(ws, "ses_4", {"type": "task", "state": "end"})
        _until(lambda: _finished(db, chat.id), "the reply")
        db.expire_all()
        assert db.get(Chat, chat.id).todos_json in (None, [])
        assert db.get(Chat, chat.id).plan == plans.from_todos(items)
        page = client.get(f"/chat/{chat.id}").text
        panel = page[page.index('id="plan-panel"') :]
        panel = panel[: panel.index("</div>\n", panel.index("plan-panel__list"))]
        assert "1/3" in panel and "Fix the &lt;validation&gt;" in panel
        assert "is-in-progress" in panel
        # Not also a block in the transcript.
        assert "[x] Read the form" not in page

        # All of it done: the panel goes.
        db.get(Chat, chat.id).plan_json = plans.from_todos(
            [{"content": "Read the form", "status": "completed"}]
        )
        db.commit()
        page = client.get(f"/chat/{chat.id}").text
        assert 'id="plan-panel" class="plan-panel" hidden' in page
