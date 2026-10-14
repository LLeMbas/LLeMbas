"""What an agent chat on a linked device does besides a prompt: its real
context, a message typed while it works, a terminal on the device, and Continue.

The device is a `Link` with its requests answered here, as in
test_device_link.py's directory tests; nothing runs on this machine.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from sqlalchemy import select

from lembas.db.models import TOKEN_DEVICE, Chat, Message, User
from lembas.security import api_tokens
from lembas.services import device_link
from lembas.services import generation as generation_service
from lembas.services.agent import terminal as terminal_service


@pytest.fixture(autouse=True)
def _clean():
    device_link.reset()
    api_tokens.reset_rates()
    yield
    device_link.reset()
    asyncio.run(terminal_service.shutdown())


def _linked(db, *, capabilities=("directories", "configure", "steer", "terminal"), answer=None):
    user = db.scalar(select(User))
    token, _ = api_tokens.create(db, user, name="LLeMbas CLI on device-1",
                                 scopes=["models", "link"],
                                 kind=TOKEN_DEVICE, device_host="device-1")
    sent: list[dict] = []

    async def send_text(text):
        sent.append(json.loads(text))

    async def nothing(*_a, **_k):
        return None

    link = device_link.Link(
        token_id=token.id, user_id=user.id, name="LLeMbas CLI on device-1",
        send_text=send_text, close=nothing,
        hello={"device": {"host": "device-1"},
               "limits": {"roots": ["/home/dev"], "max_mode": "auto"}},
    )
    link.initialized = {"_meta": {"lembas": {"capabilities": list(capabilities)}}}
    asked: list[tuple[str, dict]] = []

    async def request(method, params=None, *, timeout=60):
        asked.append((method, params))
        if answer is not None:
            return answer(method, params)
        return {}

    link.request = request
    device_link.LINKS[token.id] = link
    return link, asked, sent


def _model(db):
    from lembas.db.models import Connection, Model

    connection = Connection(name="c", base_url="http://127.0.0.1:1/v1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", context_length=180_224))
    db.commit()


# --- Context -------------------------------------------------------------------------
def test_a_device_reports_what_the_session_holds_not_the_sum():
    g = generation_service.Generation(chat_id="c", message_id="m")
    for _ in range(3):
        device_link.apply_event(g, {"type": "usage", "usage": {"input": 150_000, "output": 500},
                                    "context": 180_224, "used": 151_000})
    # Cost: every step's prompt. Context: what is in the window now.
    assert g.prompt_tokens == 450_000
    assert g.context_tokens == 151_000 and g.context_limit == 180_224


def test_the_chip_says_used_of_window(client, db, registered, make_chat):
    _model(db)
    chat_id = make_chat()
    from lembas.services import chat as chat_service

    chat = db.get(Chat, chat_id)
    reply = chat_service.create_message(db, chat, "assistant", "Done.")
    reply.usage_json = {"prompt_tokens": 1_544_332, "completion_tokens": 2_000,
                        "total_tokens": 1_546_332, "context_tokens": 142_000,
                        "context_limit": 180_224, "elapsed_ms": 30_000, "rounds": 12}
    db.commit()
    page = client.get(f"/chat/{chat_id}").text
    assert "142k / 180k · 79%" in page
    assert "1.55M processed" in page


# --- A message typed while it works --------------------------------------------------
def _queued(db, chat_id, text="also check the tests"):
    from lembas.services import chat as chat_service

    chat = db.get(Chat, chat_id)
    message = chat_service.create_message(db, chat, "user", text, queued=True)
    db.commit()
    return message.id


def test_a_queued_message_goes_to_the_device_at_once(db, registered, make_chat):
    chat_id = make_chat()
    placeholder = _placeholder(db, chat_id)
    waiting = _queued(db, chat_id)
    link, asked, _ = _linked(db)
    g = generation_service.Generation(chat_id=chat_id, message_id=placeholder)
    asyncio.run(device_link._pass_on_queued(link, g, chat_id, "s1"))
    assert [(m, {k: v for k, v in p.items() if k != "messageId"}) for m, p in asked] == [
        ("_lembas/steer", {"sessionId": "s1", "text": "also check the tests"})
    ]
    # With the message's id, so it is shown where the device took it in.
    assert asked[0][1]["messageId"]
    db.expire_all()
    assert db.get(Message, waiting).queued is False


def test_one_the_device_did_not_take_goes_back_in_the_queue(db, registered, make_chat):
    chat_id = make_chat()
    placeholder = _placeholder(db, chat_id)
    waiting = _queued(db, chat_id)

    def refuse(method, params):
        raise device_link.DeviceError("this session is not working on anything")

    link, _, _ = _linked(db, answer=refuse)
    g = generation_service.Generation(chat_id=chat_id, message_id=placeholder)
    asyncio.run(device_link._pass_on_queued(link, g, chat_id, "s1"))
    db.expire_all()
    assert db.get(Message, waiting).queued is True


def _placeholder(db, chat_id):
    from lembas.services import chat as chat_service

    chat = db.get(Chat, chat_id)
    message = chat_service.create_message(db, chat, "assistant", "")
    db.commit()
    return message.id


# --- A terminal on the device --------------------------------------------------------
def test_a_terminal_on_the_device(db, registered, make_chat):
    link, asked, sent = _linked(
        db, answer=lambda m, p: {"terminalId": "t1", "cwd": "/home/dev/project"}
    )

    async def scenario():
        session = await terminal_service.open_session(
            "chat-1", owner_id="u", label="device-1",
            project_dir="/home/dev/project", device_token_id=link.token_id,
        )
        viewer = session.attach(100, 30)
        assert asked[0] == ("_lembas/terminal/open",
                            {"cwd": "/home/dev/project", "cols": 80, "rows": 24})
        # The device's output reaches the viewer and the scrollback.
        await link._notification("_lembas/terminal/output", {
            "terminalId": "t1", "data": base64.b64encode(b"dev@device-1:~$ ").decode()})
        assert await viewer.queue.get() == b"dev@device-1:~$ "
        # Keystrokes and the size go back over the link.
        await session.send(b"ls\n")
        await asyncio.sleep(0)
        inputs = [f for f in sent if f.get("method") == "_lembas/terminal/input"]
        assert base64.b64decode(inputs[-1]["params"]["data"]) == b"ls\n"
        assert any(f.get("method") == "_lembas/terminal/resize" for f in sent)
        # The shell exiting on the device ends the session here.
        await link._notification("_lembas/terminal/exit", {"terminalId": "t1", "code": 0})
        assert session.closed and session.closed_reason == terminal_service.CLOSED_EXITED

    asyncio.run(scenario())


def test_a_device_that_does_not_open_terminals_says_how_to(db, registered):
    link, _, _ = _linked(db, capabilities=("directories", "configure", "steer"))

    async def scenario():
        from lembas.services.agent.base import ExecError

        with pytest.raises(ExecError, match="remote.terminal: true"):
            await terminal_service.open_session(
                "chat-2", owner_id="u", label="device-1",
                device_token_id=link.token_id,
            )

    asyncio.run(scenario())


def test_the_terminal_button_follows_the_device(db, registered, make_chat):
    from lembas.services import settings_store

    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    link, _, _ = _linked(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat.kind = "agent"
    chat.device_token_id = link.token_id
    chat.project_dir = "/home/dev"
    db.commit()
    link.initialized = {"_meta": {"lembas": {"capabilities": ["steer"]}}}
    from lembas.api.pages import _terminal_enabled

    user = db.scalar(select(User))
    assert _terminal_enabled(db, user, db.get(Chat, chat_id)) is False
    link.initialized = {"_meta": {"lembas": {"capabilities": ["terminal"]}}}
    assert _terminal_enabled(db, user, db.get(Chat, chat_id)) is True


# --- Continue --------------------------------------------------------------------------
def test_continue_sends_the_harness_sentence_and_shows_it_as_continue(
    client, db, registered, make_chat, mock_http
):
    from lembas.services import harness_spec

    _model(db)
    chat_id = make_chat()
    response = client.post(f"/api/chats/{chat_id}/continue")
    assert response.status_code == 200
    sent = db.scalars(select(Message).where(Message.chat_id == chat_id,
                                            Message.role == "user")).all()
    assert sent[-1].content == harness_spec.prompt("tasks/continue.md")
    assert "Continue from where you stopped" in sent[-1].content
    page = client.get(f"/chat/{chat_id}").text
    assert "↻ Continue" in page
    assert "data-continue" in page
