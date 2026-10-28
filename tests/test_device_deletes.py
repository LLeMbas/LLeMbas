"""A device chat deleted here deletes its session on the device -- even when the
device was off at the time, or said no.

A request sent once, on whatever link is open at that moment, with every answer
but success swallowed, leaves a chat deleted while the device was away in the
CLI's /sessions for good, and a refusal in neither side's log. So the request is
written down with the chat's deletion (`DeviceSessionDelete`), sent again when
the device links, crossed off when it answers, and every refusal or failure is a
warning in the log.

The device is a `Link` with its requests answered here, as in test_device_sync.py.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from sqlalchemy import select

from lembas.db.models import TOKEN_DEVICE, ApiToken, Chat, DeviceSessionDelete, User
from lembas.security import api_tokens
from lembas.services import device_link, settings_store

CAPS = ("directories", "configure", "steer", "sessions", "status", "delete", "compact", "title")


@pytest.fixture(autouse=True)
def _agents_on(db):
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    device_link.DELETED.clear()
    api_tokens.reset_rates()
    yield
    device_link.reset()
    device_link.DELETED.clear()


def _token(db) -> ApiToken:
    user = db.scalar(select(User))
    token, _ = api_tokens.create(
        db, user, name="LLeMbas CLI on device-1", scopes=["models", "link"],
        kind=TOKEN_DEVICE, device_host="device-1",
    )
    return token


def _link(token: ApiToken, *, answer=None, caps=CAPS) -> tuple[device_link.Link, list]:
    async def send_text(text):
        json.loads(text)

    async def nothing(*_a, **_k):
        return None

    link = device_link.Link(
        token_id=token.id, user_id=token.user_id, name="LLeMbas CLI on device-1",
        send_text=send_text, close=nothing,
        hello={"device": {"host": "device-1"},
               "limits": {"roots": ["/home/dev"], "max_mode": "auto"}},
    )
    link.initialized = {"_meta": {"lembas": {"capabilities": list(caps)}}}
    asked: list[tuple[str, dict]] = []

    async def request(method, params=None, *, timeout=60):
        asked.append((method, params))
        if answer is not None:
            result = answer(method, params)
            if isinstance(result, BaseException):
                raise result
            return result
        return {"deleted": True}

    link.request = request
    return link, asked


def _device_chat(db, make_chat, token_id: str, session_id: str = "ses_1") -> str:
    chat = db.get(Chat, make_chat())
    chat.kind = "agent"
    chat.device_token_id = token_id
    chat.device_session_id = session_id
    chat.project_dir = "/home/dev"
    db.commit()
    return chat.id


def _pending(db) -> list[DeviceSessionDelete]:
    db.expire_all()
    return list(db.scalars(select(DeviceSessionDelete)))


def _wait(asked, n=1):
    for _ in range(100):
        if len(asked) >= n:
            return
        asyncio.run(asyncio.sleep(0.01))


def test_a_chat_deleted_while_the_device_is_away_reaches_it_when_it_links(
    client, db, registered, make_chat
):
    token = _token(db)
    chat_id = _device_chat(db, make_chat, token.id)
    # Not linked: nothing can be asked now.
    assert client.delete(f"/api/chats/{chat_id}").status_code == 204
    [row] = _pending(db)
    assert (row.token_id, row.session_id) == (token.id, "ses_1")

    # This instance restarts (the tombstone was memory only) and the device links.
    device_link.DELETED.clear()
    link, asked = _link(token)
    device_link.LINKS[token.id] = link
    device_link.tombstone_pending(link)
    # An announcement before the delete is through does not bring the chat back.
    asyncio.run(
        link._notification("_lembas/session/announce", {"sessionId": "ses_1", "cwd": "/home/dev"})
    )
    assert "ses_1" not in link.announced
    asyncio.run(device_link.send_pending_deletes(link))
    assert asked == [("_lembas/session/delete", {"sessionId": "ses_1"})]
    assert _pending(db) == []
    # And once answered, the next link asks nothing.
    link2, asked2 = _link(token)
    asyncio.run(device_link.send_pending_deletes(link2))
    assert asked2 == []


def test_a_delete_answered_at_once_leaves_no_record(client, db, registered, make_chat):
    token = _token(db)
    link, asked = _link(token)
    device_link.LINKS[token.id] = link
    chat_id = _device_chat(db, make_chat, token.id)
    assert client.delete(f"/api/chats/{chat_id}").status_code == 204
    _wait(asked)
    assert asked == [("_lembas/session/delete", {"sessionId": "ses_1"})]
    for _ in range(50):
        if not _pending(db):
            break
        asyncio.run(asyncio.sleep(0.01))
    assert _pending(db) == []


def test_a_refusal_is_logged_kept_and_given_up_on_in_the_end(
    client, db, registered, make_chat, caplog
):
    token = _token(db)
    refuse = device_link.DeviceError("/home/dev is not in a trusted project on this device")
    link, asked = _link(token, answer=lambda m, p: refuse)
    device_link.LINKS[token.id] = link
    chat_id = _device_chat(db, make_chat, token.id)
    with caplog.at_level(logging.WARNING, logger="lembas.services.device_link"):
        assert client.delete(f"/api/chats/{chat_id}").status_code == 204
        _wait(asked)
        for _ in range(50):
            if _pending(db) and _pending(db)[0].attempts:
                break
            asyncio.run(asyncio.sleep(0.01))
    # Said, not swallowed.
    assert any("refused to delete session ses_1" in r.getMessage() for r in caplog.records)
    [row] = _pending(db)
    assert row.attempts == 1
    assert "not in a trusted project" in row.last_error
    # Asked again on each link -- a device upgraded meanwhile deletes it -- and
    # not for ever.
    for _ in range(device_link.MAX_DELETE_ATTEMPTS - 1):
        asyncio.run(device_link.send_pending_deletes(link))
    assert _pending(db) == []
    assert any("giving up" in r.getMessage() for r in caplog.records)


def test_no_answer_is_kept_for_the_next_link(db, registered, make_chat, caplog):
    token = _token(db)
    chat = db.get(Chat, _device_chat(db, make_chat, token.id))
    from lembas.services import chat as chat_service

    chat_service.delete_chats(db, [chat])
    db.commit()
    link, asked = _link(token, answer=lambda m, p: device_link.LinkClosed("dropped"))
    with caplog.at_level(logging.WARNING, logger="lembas.services.device_link"):
        asyncio.run(device_link.send_pending_deletes(link))
    assert asked and any("did not answer" in r.getMessage() for r in caplog.records)
    [row] = _pending(db)
    assert row.attempts == 1

    # The next link answers.
    link2, _ = _link(token)
    asyncio.run(device_link.send_pending_deletes(link2))
    assert _pending(db) == []


def test_a_device_without_delete_keeps_it_until_it_has_it(db, registered, make_chat):
    token = _token(db)
    chat = db.get(Chat, _device_chat(db, make_chat, token.id))
    from lembas.services import chat as chat_service

    chat_service.delete_chats(db, [chat])
    db.commit()
    old, asked = _link(token, caps=("directories",))
    asyncio.run(device_link.send_pending_deletes(old))
    assert asked == [] and len(_pending(db)) == 1


def test_revoking_the_device_drops_what_waits_for_it(client, db, registered, make_chat):
    token = _token(db)
    chat = db.get(Chat, _device_chat(db, make_chat, token.id))
    from lembas.services import chat as chat_service

    chat_service.delete_chats(db, [chat])
    db.commit()
    assert len(_pending(db)) == 1
    api_tokens.revoke(db, db.get(ApiToken, token.id))
    assert _pending(db) == []


def test_a_chat_of_a_token_that_is_gone_records_nothing(db, registered, make_chat):
    chat = db.get(Chat, _device_chat(db, make_chat, "gone"))
    from lembas.services import chat as chat_service

    chat_service.delete_chats(db, [chat])
    db.commit()
    assert _pending(db) == []
    assert ("gone", "ses_1") in device_link.DELETED


def test_over_the_real_link_the_missed_delete_follows_initialize(client, db, registered, make_chat):
    """The wiring in `api/devices.py`: tombstones before the device speaks, the
    missed deletes after `initialize`, on the WebSocket itself."""
    import time

    token, raw = api_tokens.create(
        db, db.scalar(select(User)), name="LLeMbas CLI on device-1", scopes=["models", "link"],
        kind=TOKEN_DEVICE, device_host="device-1",
    )
    from lembas.services import chat as chat_service

    chat_service.delete_chats(db, [db.get(Chat, _device_chat(db, make_chat, token.id))])
    db.commit()
    device_link.DELETED.clear()  # as after a restart
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        ws.send_text(json.dumps({"jsonrpc": "2.0", "method": "_lembas/hello", "params": {
            "protocol": 3, "version": "0.27.0", "harness_spec": "2.1.0",
            "device": {"host": "device-1"},
            "limits": {"roots": ["/home/dev"], "max_mode": "auto"}}}))
        init = ws.receive_json()
        assert init["method"] == "initialize"
        assert (token.id, "ses_1") in device_link.DELETED
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": init["id"], "result": {
            "protocolVersion": 1, "agentInfo": {"name": "lembas"},
            "_meta": {"lembas": {"capabilities": list(CAPS)}}}}))
        asked = ws.receive_json()
        assert asked["method"] == "_lembas/session/delete"
        assert asked["params"] == {"sessionId": "ses_1"}
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": asked["id"], "result": {"deleted": True}}))
        for _ in range(100):
            if not _pending(db):
                break
            time.sleep(0.02)
        assert _pending(db) == []


def test_a_chat_started_here_deletes_the_session_the_device_named(client, db, registered):
    """A web-started chat's delete goes through, with the
    id the device answered `session/new` with -- not the chat's id, not a token's."""
    import time

    from lembas.db.models import Connection, Model

    connection = Connection(name="c", base_url="http://127.0.0.1:1/v1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    token, raw = api_tokens.create(
        db, db.scalar(select(User)), name="LLeMbas CLI on device-1", scopes=["models", "link"],
        kind=TOKEN_DEVICE, device_host="device-1",
    )
    with client.websocket_connect(
        "/api/devices/link", headers={"Authorization": f"Bearer {raw}"}
    ) as ws:
        ws.send_text(json.dumps({"jsonrpc": "2.0", "method": "_lembas/hello", "params": {
            "protocol": 3, "version": "0.27.0", "harness_spec": "2.1.0",
            "device": {"host": "device-1"},
            "limits": {"roots": ["/home/dev"], "max_mode": "auto"}}}))
        init = ws.receive_json()
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": init["id"], "result": {
            "protocolVersion": 1, "agentInfo": {"name": "lembas"},
            "_meta": {"lembas": {"capabilities": list(CAPS)}}}}))
        for _ in range(100):
            link = device_link.link_for(token.id)
            if link is not None and link.initialized:
                break
            time.sleep(0.02)
        client.post("/api/chats/start", data={
            "content": "hello", "kind": "agent", "ssh_profile_id": f"device:{token.id}",
            "project_dir": "/home/dev", "agent_mode": "auto"})
        chat = db.scalar(select(Chat))

        def until(method):
            while True:
                m = ws.receive_json()
                if m.get("method") == method:
                    return m
                if "id" in m and "method" in m:  # anything else asked: answered empty
                    ws.send_text(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": {}}))

        new = until("session/new")
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": new["id"],
                                 "result": {"sessionId": "ses_web"}}))
        prompt = until("session/prompt")
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": prompt["id"],
                                 "result": {"stopReason": "end_turn"}}))
        for _ in range(100):
            db.expire_all()
            if db.get(Chat, chat.id).device_session_id == "ses_web":
                break
            time.sleep(0.02)
        assert client.delete(f"/api/chats/{chat.id}").status_code == 204
        asked = until("_lembas/session/delete")
        assert asked["params"] == {"sessionId": "ses_web"}
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": asked["id"], "result": {"deleted": True}}))
