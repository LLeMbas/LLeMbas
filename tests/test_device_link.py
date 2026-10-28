"""Agent chats on a linked device.

A fake device speaks the device's half over the real WebSocket: hello, then it
answers `initialize`, `session/new` and `session/prompt`, streaming `_lembas/event`
notifications while it works and asking `session/request_permission` once. The
chat is started the way the composer starts one. Nothing runs on this machine.
"""

from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from lembas.db.models import TOKEN_DEVICE, Chat, Message, User
from lembas.security import api_tokens
from lembas.services import device_link, settings_store


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    yield
    device_link.reset()


def _device(db, scopes=("models", "link")) -> tuple[str, str]:
    from lembas.db.models import Connection, Model

    if db.scalar(select(Connection)) is None:
        connection = Connection(name="c", base_url="http://127.0.0.1:1/v1", api_key_encrypted="")
        db.add(connection)
        db.commit()
        db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
        db.commit()
    # Agent chats are behind Admin -> Agents.
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    user = db.scalar(select(User))
    token, raw = api_tokens.create(
        db,
        user,
        name="LLeMbas CLI on device-1",
        scopes=list(scopes),
        kind=TOKEN_DEVICE,
        device_host="device-1",
    )
    return token.id, raw


def _hello(ws, roots=("/srv/work",)):
    ws.send_text(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "_lembas/hello",
                "params": {
                    "protocol": 3,
                    "version": "0.19.0",
                    "harness_spec": "1.3.0",
                    "device": {"host": "device-1"},
                    "limits": {"roots": list(roots), "max_mode": "edit"},
                },
            }
        )
    )
    init = ws.receive_json()
    assert init["method"] == "initialize"
    # Says it is LLeMbas, and which protocol it speaks.
    assert init["params"]["clientCapabilities"]["_meta"]["lembas"] == {"protocol": 3, "show": True}
    ws.send_text(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": init["id"],
                "result": {"protocolVersion": 1, "agentInfo": {"name": "LLeMbas CLI"}},
            }
        )
    )


def _wait_linked(token_id: str) -> None:
    for _ in range(100):
        link = device_link.link_for(token_id)
        if link is not None and link.initialized:
            return
        time.sleep(0.02)
    raise AssertionError("the device never linked")


def _reply(db, chat_id: str) -> Message:
    for _ in range(200):
        db.expire_all()
        reply = db.scalar(
            select(Message).where(Message.chat_id == chat_id, Message.role == "assistant")
        )
        if reply is not None and reply.complete:
            return reply
        time.sleep(0.05)
    raise AssertionError("the reply never finished")


def _event(ws, session_id, event):
    ws.send_text(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "_lembas/event",
                "params": {"sessionId": session_id, "event": event},
            }
        )
    )


def test_a_token_without_link_is_refused(client, db, registered):
    _, raw = _device(db, scopes=("models",))
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws, pytest.raises(WebSocketDisconnect):
        ws.receive_text()


@pytest.mark.parametrize("protocol", [1, 2, "two"])
def test_a_1x_cli_is_closed_with_4400_and_told_to_update(client, db, registered, protocol):
    """Link protocol 3 alone (harness spec acp.md *Versions*): a hello of 1 or 2
    -- a LLeMbas CLI 1.x -- is closed with 4400, which it reads as "stop
    dialling", and the reason says which side to update."""
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        ws.send_text(json.dumps({
            "jsonrpc": "2.0", "method": "_lembas/hello",
            "params": {"protocol": protocol, "version": "1.7.2", "limits": {"roots": ["/"]}},
        }))
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 4400
    assert closed.value.reason == "update LLeMbas CLI to 2.0"
    assert device_link.link_for(token_id) is None or not device_link.link_for(token_id).initialized


def test_a_reply_is_written_by_the_device(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)

        # The device is offered in the agent picker.
        page = client.get("/chat?kind=agent").text
        # Named by its host, and starting in the most its limits allow.
        assert f'value="device:{token_id}"' in page and ">device-1</option>" in page
        assert 'data-mode="edit"' in page

        client.post(
            "/api/chats/start",
            data={
                "content": "List the files",
                "kind": "agent",
                "ssh_profile_id": f"device:{token_id}",
                "project_dir": "/srv/work/app",
                "agent_mode": "auto",
            },
        )
        chat = db.scalar(select(Chat))
        assert chat.device_token_id == token_id and chat.project_dir == "/srv/work/app"

        new = ws.receive_json()
        assert new["method"] == "session/new"
        assert new["params"]["cwd"] == "/srv/work/app"
        assert new["params"]["_meta"]["mode"] == "auto"  # the device clamps it, not LLeMbas
        # The chat's model, by the name /v1 serves it under, and its effort.
        assert new["params"]["_meta"]["lembas_model"] == "c/m"
        assert new["params"]["_meta"]["effort"] == "off"
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": new["id"], "result": {"sessionId": "s1"}}))

        prompt = ws.receive_json()
        assert prompt["method"] == "session/prompt"
        assert prompt["params"]["prompt"][0]["text"] == "List the files"
        _event(ws, "s1", {"type": "reasoning", "text": "Let me look."})
        _event(
            ws, "s1", {"type": "tool_start", "id": "t1", "name": "bash", "args": {"command": "ls"}}
        )

        # One approval, on the same card an SSH chat uses.
        ws.send_text(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 77,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "s1",
                        "toolCall": {
                            "toolCallId": "t1",
                            "title": "ls",
                            "kind": "execute",
                            "rawInput": {"command": "ls"},
                        },
                        "options": [
                            {"optionId": "once", "kind": "allow_once"},
                            {"optionId": "deny", "kind": "reject_once"},
                        ],
                    },
                }
            )
        )
        from lembas.services import generation as generation_service

        card = None
        for _ in range(100):
            running = generation_service.running_for(chat.id)
            if running is not None and running.pending is not None:
                card = running.pending
                break
            time.sleep(0.02)
        assert card is not None and card.items[0].detail == "ls"
        generation_service.answer(chat.id, card.id, verdict="allow")
        permitted = ws.receive_json()
        assert permitted["id"] == 77
        assert permitted["result"]["outcome"]["optionId"] == "once"

        _event(
            ws,
            "s1",
            {
                "type": "tool_end",
                "id": "t1",
                "name": "bash",
                "ms": 5,
                "result": {"output": "README.md\nsrc", "title": "ls · exit 0"},
            },
        )
        _event(ws, "s1", {"type": "text", "text": "There are two entries."})
        ws.send_text(
            json.dumps({"jsonrpc": "2.0", "id": prompt["id"], "result": {"stopReason": "end_turn"}})
        )

        reply = _reply(db, chat.id)
        assert reply.content == "There are two entries."
        assert "Let me look." in (reply.reasoning or "")
        assert any("README.md" in (e.get("text") or "") for e in (reply.tool_calls_json or []))
        assert not reply.error


def test_an_unlinked_device_says_so(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        client.post(
            "/api/chats/start",
            data={
                "content": "hi",
                "kind": "agent",
                "ssh_profile_id": f"device:{token_id}",
                "project_dir": "/srv/work",
            },
        )
        chat = db.scalar(select(Chat))
        new = ws.receive_json()
        ws.send_text(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": new["id"],
                    "error": {
                        "code": -32602,
                        "message": "/srv/work is outside the directories this device accepts",
                    },
                }
            )
        )
        reply = _reply(db, chat.id)
        assert "refused" in reply.error and "outside" in reply.error
    # Disconnected: the next turn is told plainly.
    device_link.reset()


def test_a_wrong_protocol_is_closed(client, db, registered):
    _, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        ws.send_text(
            json.dumps({"jsonrpc": "2.0", "method": "_lembas/hello", "params": {"protocol": 99}})
        )
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()


def test_revoking_the_token_drops_the_link(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        client.post(f"/api/tokens/{token_id}/revoke")
        assert device_link.link_for(token_id) is None


def test_a_device_chat_is_not_charged_twice(client, db, registered):
    """The device spends through /v1 when it is logged in here; the chat records
    nothing of its own."""
    from lembas.db.models import Usage

    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        client.post(
            "/api/chats/start",
            data={"content": "hi", "kind": "agent", "ssh_profile_id": f"device:{token_id}"},
        )
        chat = db.scalar(select(Chat))
        new = ws.receive_json()
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": new["id"], "result": {"sessionId": "s9"}}))
        prompt = ws.receive_json()
        _event(ws, "s9", {"type": "usage", "usage": {"input": 1000, "output": 50}})
        _event(ws, "s9", {"type": "text", "text": "ok"})
        ws.send_text(
            json.dumps({"jsonrpc": "2.0", "id": prompt["id"], "result": {"stopReason": "end_turn"}})
        )
        _reply(db, chat.id)
        assert db.scalar(select(Usage)) is None or db.scalar(select(Usage)).tokens_in == 0


def test_a_mode_changed_in_the_composer_reaches_the_device(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        client.post(
            "/api/chats/start",
            data={
                "content": "one",
                "kind": "agent",
                "ssh_profile_id": f"device:{token_id}",
                "agent_mode": "manual",
            },
        )
        chat = db.scalar(select(Chat))
        new = ws.receive_json()
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": new["id"], "result": {"sessionId": "s2"}}))
        prompt = ws.receive_json()
        ws.send_text(
            json.dumps({"jsonrpc": "2.0", "id": prompt["id"], "result": {"stopReason": "end_turn"}})
        )
        _reply(db, chat.id)

        client.patch(f"/api/chats/{chat.id}", data={"agent_mode": "edit"})
        client.post(f"/api/chats/{chat.id}/messages", data={"content": "two"})
        set_mode = ws.receive_json()
        assert set_mode["method"] == "session/set_mode"
        assert set_mode["params"] == {"sessionId": "s2", "modeId": "edit"}
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": set_mode["id"], "result": {}}))
        second = ws.receive_json()
        assert second["method"] == "session/prompt"
        assert second["params"]["prompt"][0]["text"] == "two"
        ws.send_text(
            json.dumps({"jsonrpc": "2.0", "id": second["id"], "result": {"stopReason": "end_turn"}})
        )


def test_a_device_that_went_away_is_said_plainly(client, db, registered):
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        client.post(
            "/api/chats/start",
            data={"content": "one", "kind": "agent", "ssh_profile_id": f"device:{token_id}"},
        )
        chat = db.scalar(select(Chat))
        new = ws.receive_json()
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": new["id"], "result": {"sessionId": "s3"}}))
        prompt = ws.receive_json()
        ws.send_text(
            json.dumps({"jsonrpc": "2.0", "id": prompt["id"], "result": {"stopReason": "end_turn"}})
        )
        _reply(db, chat.id)
    for _ in range(100):
        if device_link.link_for(token_id) is None:
            break
        time.sleep(0.02)
    client.post(f"/api/chats/{chat.id}/messages", data={"content": "two"})
    for _ in range(200):
        db.expire_all()
        replies = list(
            db.scalars(
                select(Message).where(Message.chat_id == chat.id, Message.role == "assistant")
            )
        )
        if len(replies) == 2 and all(r.complete for r in replies):
            break
        time.sleep(0.05)
    last = sorted(replies, key=lambda m: m.created_at)[-1]
    assert "not linked right now" in (last.error or "")


# --- Choosing a directory on the device ---------------------------------------------
def _fake_link(db, answer):
    """A linked device whose `_lembas/directories` answers with `answer(params)`
    (or raises it, when it is an exception)."""
    token_id, _ = _device(db)
    user = db.scalar(select(User))

    async def nothing(*_a, **_k):
        return None

    link = device_link.Link(
        token_id=token_id, user_id=user.id, name="LLeMbas CLI on device-1",
        send_text=nothing, close=nothing,
        hello={"limits": {"roots": ["/home/dev"], "max_mode": "auto"}},
    )
    asked = []

    async def request(method, params=None, *, timeout=60):
        asked.append((method, params))
        if isinstance(answer, Exception):
            raise answer
        return answer(params)

    link.request = request
    device_link.LINKS[token_id] = link
    return token_id, asked


def test_the_device_offers_its_places_then_what_is_in_them(client, db, registered):
    def answer(params):
        if not params["path"]:
            place = {"path": "/home/dev", "name": "dev", "accepted": True}
            return {"path": "", "places": [place]}
        return {
            "path": "/home/dev", "name": "dev", "accepted": True, "parent": "",
            "entries": [
                {"path": "/home/dev/project", "name": "project", "accepted": True},
                {"path": "/home/dev/<b>odd</b>", "name": "<b>odd</b>", "accepted": False,
                 "reason": "not in a trusted project"},
            ],
        }

    token_id, asked = _fake_link(db, answer)
    places = client.get(f"/api/devices/{token_id}/browse").text
    assert 'data-dir-open="/home/dev"' in places and "Places on this device" in places
    inside = client.get(f"/api/devices/{token_id}/browse?path=/home/dev").text
    assert 'data-here="/home/dev"' in inside
    assert 'data-dir-open="/home/dev/project"' in inside
    # A name from the device's disk is text, never markup; a refused one says so.
    assert "<b>odd</b>" not in inside and "&lt;b&gt;odd&lt;/b&gt;" in inside
    assert "Not trusted on the device" in inside
    assert "All places" in inside
    assert asked == [("_lembas/directories", {"path": ""}),
                     ("_lembas/directories", {"path": "/home/dev"})]


def test_an_older_device_is_said_to_be_older(client, db, registered):
    token_id, _ = _fake_link(db, device_link.DeviceError("Method not found: _lembas/directories"))
    page = client.get(f"/api/devices/{token_id}/browse").text
    assert "too old to list its directories" in page


def test_a_device_refusing_a_path_says_why(client, db, registered):
    refusal = device_link.DeviceError("/etc is outside the directories this device accepts")
    token_id, _ = _fake_link(db, refusal)
    assert "outside the directories" in client.get(f"/api/devices/{token_id}/browse?path=/etc").text


def test_someone_elses_device_is_not_browsed(client, db, registered):
    token_id, _ = _fake_link(db, lambda p: {"path": "", "places": []})
    from lembas.db.models import ApiToken

    other = User(email="sam@shire.test", name="Sam", password_hash="x", role="user")
    db.add(other)
    db.commit()
    token = db.get(ApiToken, token_id)
    token.user_id = other.id
    db.commit()
    assert client.get(f"/api/devices/{token_id}/browse").status_code == 404


def test_a_device_not_linked_is_said_so(client, db, registered):
    token_id, _ = _device(db)
    assert "not linked right now" in client.get(f"/api/devices/{token_id}/browse").text


def _answer_turn(ws, session_reply=None):
    """Answer one turn as the device: session/new (when it comes), then the prompt."""
    first = ws.receive_json()
    if first["method"] == "session/new":
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": first["id"],
                                 "result": session_reply or {"sessionId": "s1"}}))
        first = ws.receive_json()
    seen = [first]
    while first["method"] != "session/prompt":
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": first["id"], "result": {}}))
        first = ws.receive_json()
        seen.append(first)
    _event(ws, "s1", {"type": "text", "text": "Done."})
    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": first["id"],
                             "result": {"stopReason": "end_turn"}}))
    return seen


def test_a_model_changed_in_the_chat_reaches_the_device(client, db, registered):
    from lembas.db.models import Connection, Model

    token_id, raw = _device(db)
    connection = db.scalar(select(Connection))
    db.add(Model(connection_id=connection.id, model_id="deepseek-flash",
                 capabilities_json={"tools": True}))
    db.commit()
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        client.post("/api/chats/start", data={
            "content": "One", "kind": "agent", "ssh_profile_id": f"device:{token_id}",
            "project_dir": "/srv/work", "agent_mode": "edit", "model_id": "m",
        })
        chat = db.scalar(select(Chat))
        _answer_turn(ws)
        _reply(db, chat.id)

        client.patch(f"/api/chats/{chat.id}", data={"model_id": "deepseek-flash"})
        client.post(f"/api/chats/{chat.id}/messages", data={"content": "Two"})
        seen = _answer_turn(ws)
        configure = next(m for m in seen if m["method"] == "_lembas/configure")
        assert configure["params"]["model"] == "c/deepseek-flash"
        assert configure["params"]["sessionId"] == "s1"


def test_no_own_model_notice_and_a_model_refusal_is_the_reply_s_error(client, db, registered):
    """The chat's model, full stop (spec 3.0.0): nothing is said about a device
    "answering on its own model" any more, and a device that cannot run the
    chat's model refuses `session/new` with -32602 -- shown as a plain error."""
    token_id, raw = _device(db)
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        _hello(ws)
        _wait_linked(token_id)
        client.post("/api/chats/start", data={
            "content": "One", "kind": "agent", "ssh_profile_id": f"device:{token_id}",
            "project_dir": "/srv/work", "agent_mode": "edit",
        })
        chat = db.scalar(select(Chat))
        _answer_turn(ws, {"sessionId": "s1",
                          "_meta": {"lembas": {"model": "local/bonsai-abl"}}})
        reply = _reply(db, chat.id)
        assert "own model" not in reply.content and reply.content == "Done."

        client.post("/api/chats/start", data={
            "content": "Two", "kind": "agent", "ssh_profile_id": f"device:{token_id}",
            "project_dir": "/srv/work", "agent_mode": "edit",
        })
        second = db.scalar(select(Chat).where(Chat.id != chat.id))
        new = ws.receive_json()
        assert new["method"] == "session/new"
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": new["id"], "error": {
            "code": -32602, "message": "this device cannot use the model c/m"}}))
        failed = _reply(db, second.id)
        assert "cannot use the model c/m" in (failed.error or "")
        assert "own model" not in (failed.content or "")
