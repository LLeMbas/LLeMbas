"""What an audit found about chats on a linked device.

The gates a device chat is created behind hold on every later turn too; a
malformed message from a device costs that message, not the link; a deleted
device chat leaves a tombstone, whichever way it was deleted; a device chat
cannot be edited or regenerated, because the device's history cannot be
rewound from here; and a device cannot make one reply hold without limit.
"""

from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select

from lembas.db.models import Chat, Message, User
from lembas.services import chat as chat_service
from lembas.services import device_link, settings_store

from .test_device_link import _clean, _device, _event, _hello, _wait_linked  # noqa: F401


def _turn(ws, session_id="s1", *, first=True):
    """Answer the device's side of one turn: open the session the first time,
    then take the prompt and finish it."""
    if first:
        new = ws.receive_json()
        assert new["method"] == "session/new"
        ws.send_text(
            json.dumps({"jsonrpc": "2.0", "id": new["id"], "result": {"sessionId": session_id}})
        )
    while True:
        message = ws.receive_json()
        if message.get("method") == "session/prompt":
            break
    _event(ws, session_id, {"type": "text", "text": "ok"})
    ws.send_text(
        json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"stopReason": "end_turn"}})
    )
    return message


def _newest_reply(db, chat_id: str, after: int = 0) -> Message:
    for _ in range(200):
        db.expire_all()
        replies = list(
            db.scalars(
                select(Message)
                .where(Message.chat_id == chat_id, Message.role == "assistant")
                .order_by(Message.created_at)
            )
        )
        if len(replies) > after and replies[-1].complete:
            return replies[-1]
        time.sleep(0.05)
    raise AssertionError("the reply never finished")


def _started_chat(client, db, ws, token_id) -> Chat:
    client.post(
        "/api/chats/start",
        data={"content": "one", "kind": "agent", "ssh_profile_id": f"device:{token_id}"},
    )
    chat = db.scalar(select(Chat))
    _turn(ws)
    _newest_reply(db, chat.id)
    return chat


def test_switching_agent_chats_off_stops_the_chats_that_exist(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        chat = _started_chat(client, db, ws, token_id)

        settings_store.update(db, {"enabled": False}, key=settings_store.AGENTS)
        assert client.post(
            f"/api/chats/{chat.id}/messages", data={"content": "carry on"}
        ).status_code == 200
        reply = _newest_reply(db, chat.id, after=1)
        assert "switched off" in reply.error


def test_the_gate_names_each_reason(db, registered):
    user = db.scalar(select(User))
    settings_store.update(db, {"enabled": False}, key=settings_store.AGENTS)
    assert "switched off" in device_link.refusal(db, user)
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    assert device_link.refusal(db, user) == ""
    assert "no longer signed in" in device_link.refusal(db, user, "no-such-token")


def test_a_malformed_message_does_not_end_the_link(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        ws.send_text(json.dumps({"jsonrpc": "2.0", "method": "_lembas/event", "params": [1, 2]}))
        time.sleep(0.3)
        assert device_link.link_for(token_id) is not None


def test_a_device_chat_cannot_be_edited_or_regenerated(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        chat = _started_chat(client, db, ws, token_id)
        asked = db.scalar(select(Message).where(Message.chat_id == chat.id, Message.role == "user"))
        reply = _newest_reply(db, chat.id)

        page = client.get(f"/chat/{chat.id}").text
        assert f"/messages/{reply.id}/regenerate" not in page
        assert f"/messages/{asked.id}/edit" not in page
        edited = client.post(
            f"/api/chats/{chat.id}/messages/{asked.id}/edit", data={"content": "two"}
        )
        assert edited.status_code == 409
        assert client.post(
            f"/api/chats/{chat.id}/messages/{reply.id}/regenerate"
        ).status_code == 409


def test_deleting_a_device_chat_any_way_leaves_a_tombstone(db, registered, make_chat):
    chat = db.get(Chat, make_chat())
    chat.kind = "agent"
    chat.device_token_id = "tok"
    chat.device_session_id = "sess"
    db.commit()
    chat_service.delete_chats(db, [chat])
    db.commit()
    assert ("tok", "sess") in device_link.DELETED


@pytest.mark.parametrize("size", [10, device_link.MAX_ARGS_CHARS + 10])
def test_tool_arguments_are_kept_up_to_a_limit(size):
    args = {"command": "x" * size}
    kept = device_link._bounded_args(args)
    assert (kept == args) == (size <= device_link.MAX_ARGS_CHARS - 20)


def test_a_reply_stops_growing_at_its_limit():
    from lembas.services.generation import Generation

    generation = Generation("c", "m")
    assert device_link._room(generation, "x" * device_link.MAX_REPLY_CHARS)
    assert not device_link._room(generation, "one more")
