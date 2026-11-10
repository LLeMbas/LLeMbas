"""An agent chat's subagents (harness spec 3.4.0, `acp.md` *Subagents in an
agent chat*): the server carries the chat's Subagents setting down with every
turn and the tree, the phase and approvals up.

A fake device listing `agents`, `tree` and `progress` opens a chat from the
apps' door: `session/new` and `session/prompt` carry `_meta.lembas.agents`;
its `team` and `phase` events reach the app's stream and are kept with the
chat; a subagent's approval names it on the card; `_lembas/team/*` are asked
of it; and a device that lists none of these gets none of it.
"""

from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select

from lembas.db.models import Chat
from lembas.services import device_link, settings_store
from tests.test_device_link import _device, _event, _reply, _wait_linked
from tests.test_device_protocol2 import ALL, _hello
from tests.test_device_v2_cards import _app_bearer, _follow_soon

TREE = {
    "min": 1, "max": 4, "size": 2, "open_slots": 0,
    "agents": [
        {"id": "lead", "name": "lead", "position": "the main agent", "parent": None,
         "state": "working", "model": "qwen", "mode": "edit", "request": [], "manager": True,
         "progress": {"phase": "waiting_for_subagents", "since": 1, "running": 1}},
        {"id": "s1", "name": "parser", "position": "reviews <b>the</b> parser", "parent": "lead",
         "state": "working", "model": "qwen", "mode": "manual", "request": ["write"],
         "manager": False, "task": "Review.", "context": {"used": 10, "window": 100},
         "findings": 2, "messages": 1},
    ],
    "ended": [],
}


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    yield
    device_link.reset()


def _start(client, db, ws, token_id, headers, *, agents=(2, 3)):
    made = client.post(
        "/api/v2/chats",
        headers=headers,
        json={"text": "Use gpt-big for the review", "kind": "agent", "device": token_id,
              "directory": "/srv/work/app", "mode": "edit"},
    )
    assert made.status_code == 201, made.text
    chat_id = made.json()["chat"]["id"]
    chat = db.get(Chat, chat_id)
    db.refresh(chat)
    return made.json(), chat_id


def _open_session(ws):
    new = ws.receive_json()
    assert new["method"] == "session/new"
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": new["id"], "result": {"sessionId": "s1"}}))
    prompt = ws.receive_json()
    while prompt.get("method") != "session/prompt":
        prompt = ws.receive_json()
    return new, prompt


def test_the_setting_goes_down_and_the_tree_phase_and_approvals_come_up(
    client, db, registered
):
    from lembas.api.v2.stream import follow_json

    settings_store.update(db, {"enabled": True}, key=settings_store.SUBAGENTS)
    settings_store.update(db, {"default_permissions": {"tools.subagent": True}})
    token_id, raw = _device(db)
    headers = _app_bearer(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, [*ALL, "context", "agents", "tree", "progress", "jobs"])
        _wait_linked(token_id)
        body, chat_id = _start(client, db, ws, token_id, headers)
        new, prompt = _open_session(ws)
        sent = (prompt["params"].get("_meta") or {}).get("lembas") or {}
        opened = (new["params"].get("_meta") or {}).get("lembas") or {}
        assert opened["agents"] == {"min": 1, "max": 4} == sent["agents"]
        assert "subagents" not in sent
        turn = sent.get("turnId")
        _event(ws, "s1", {"type": "task", "state": "start", "turnId": turn})

        app = _follow_soon(
            client, lambda: follow_json(chat_id, body["reply"]["id"]),
            lambda event, data: event == "ask" and bool(json.loads(data)["cards"]),
        )
        time.sleep(0.2)
        _event(ws, "s1", {"type": "phase", "progress": {
            "phase": "waiting_for_subagents", "since": 1762790400000, "running": 1}})
        _event(ws, "s1", {"type": "team", "tree": TREE})
        time.sleep(0.4)
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "id": 78, "method": "session/request_permission",
            "params": {
                "sessionId": "s1",
                "toolCall": {"toolCallId": "t9", "title": "git push", "kind": "execute",
                             "rawInput": {"command": "git push"}},
                "options": [{"optionId": "once", "kind": "allow_once"},
                            {"optionId": "deny", "kind": "reject_once"}],
                "_meta": {"lembas": {"subagent": {"id": "s1", "name": "parser",
                                                  "position": "reviews the parser"}}},
            },
        }))
        frames = app.result(timeout=10)
        statuses = [json.loads(d) for e, d in frames if e == "status"]
        assert any((s["progress"] or {}).get("phase") == "waiting_for_subagents"
                   for s in statuses), frames
        [card] = [json.loads(d)["cards"] for e, d in frames if e == "ask"][-1]
        assert card["items"][0]["subagent"] == {
            "id": "s1", "name": "parser", "position": "reviews the parser"}
        trees = [json.loads(d) for e, d in frames if e == "tree"]
        assert trees and trees[-1]["tree"]["agents"][1]["name"] == "parser"

        # Kept with the chat, for a client opening it later.
        db.expire_all()
        kept = db.get(Chat, chat_id).team_tree_json
        assert kept["agents"][1]["position"] == "reviews <b>the</b> parser"

        answered = client.post(
            f"/api/v2/chats/{chat_id}/interactions/{card['id']}", headers=headers,
            json={"verdict": "deny"},
        )
        assert answered.status_code == 200
        assert ws.receive_json()["id"] == 78
        _event(ws, "s1", {"type": "text", "text": "Done."})
        _event(ws, "s1", {"type": "task", "state": "end", "turnId": turn})
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": prompt["id"],
                                 "result": {"stopReason": "end_turn"}}))
        assert _reply(db, chat_id).content == "Done."


def test_said_carries_the_persons_earlier_words_and_subagents_off_says_so(
    client, db, registered
):
    from lembas.db.models import ROLE_USER, Message

    settings_store.update(db, {"enabled": False}, key=settings_store.SUBAGENTS)
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, [*ALL, "context", "agents", "tree"])
        _wait_linked(token_id)
        link = device_link.link_for(token_id)
        chat = Chat(user_id=db.scalar(select(Chat.user_id).limit(1)) or _user_id(db),
                    title="t", kind="agent", device_token_id=token_id)
        db.add(chat)
        db.commit()
        older = Message(chat_id=chat.id, role=ROLE_USER, content="Use gpt-big.", complete=True)
        db.add(older)
        db.commit()
        time.sleep(0.01)
        now = Message(chat_id=chat.id, role=ROLE_USER, content="Go on.", complete=True)
        db.add(now)
        db.commit()
        time.sleep(0.01)
        answering = Message(chat_id=chat.id, role="assistant", content="", complete=False)
        db.add(answering)
        db.commit()
        assert device_link.said_before(chat.id, answering.id) == ["Use gpt-big."]
        context = device_link.session_context(link, chat.id)
        assert context["agents"] == {"min": 1, "max": 4}
        assert context["subagents"] is False


def _user_id(db):
    from lembas.db.models import User

    return db.scalar(select(User.id).limit(1))


def test_a_device_without_the_capabilities_gets_none_of_it(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws, [*ALL, "context"])
        _wait_linked(token_id)
        link = device_link.link_for(token_id)
        chat = Chat(user_id=_user_id(db), title="t", kind="agent", device_token_id=token_id)
        db.add(chat)
        db.commit()
        assert "agents" not in device_link.session_context(link, chat.id)
        with pytest.raises(device_link.DeviceError):
            device_link._tree_link(token_id, "s1")
