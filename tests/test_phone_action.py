"""`phone_action` (harness spec 3.6.0, `phone.md`): done on the person's phone.

Offered once a message came from the phone app (`client: ["phone_actions"]`
on `/api/v2`), kept for the chat; a call reaches the phone only while the
message being answered is the phone's, waits on a `phone_action` card in the
stream's `ask` frame, and the app's answer through the interactions route is
the call's result. The checks and the texts are the conformance area `phone`
(`test_harness_spec.py`).
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from lembas.db.models import ROLE_ASSISTANT, Chat, Connection, Message, Model, User
from lembas.security import api_tokens
from lembas.services import generation as generation_service
from lembas.services import harness_spec, interaction, phone
from lembas.services import tools as tools_service

ALARM = {"action": "alarm", "summary": "Alarm at 07:00", "time": "07:00"}


def _model(db):
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    return connection


def _chat(db, user_id, **extra):
    connection = _model(db)
    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id, **extra)
    db.add(chat)
    db.commit()
    return chat


def _turn(db, chat, *, from_phone: bool):
    user = Message(chat_id=chat.id, role="user", content="Wake me at seven.", complete=True)
    db.add(user)
    db.commit()
    if from_phone:
        chat.phone_message_id = user.id
    reply = Message(chat_id=chat.id, role=ROLE_ASSISTANT, content="", complete=False)
    db.add(reply)
    db.commit()
    return reply.id


def _offered(db, chat, user_id) -> set[str]:
    user = db.get(User, user_id)
    return {t.name for t in tools_service.resolve_tools(db, chat, user).defs}


def _call(name: str, arguments: dict) -> dict:
    return {"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": name, "arguments": json.dumps(arguments)}}
    ]}}]}


def _text(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


def _stub(rounds, seen):
    async def stream_chat(_endpoint, payload, **_):
        seen.append(payload)
        for chunk in rounds[min(len(seen) - 1, len(rounds) - 1)]:
            yield chunk

    return stream_chat


async def _run_until_card(generation, message_id):
    task = asyncio.ensure_future(generation_service._run(generation))
    generation_service._RUNNING[message_id] = generation
    for _ in range(300):
        if generation.pending is not None or task.done():
            break
        await asyncio.sleep(0.01)
    return task


# --- Offered -------------------------------------------------------------------------
def test_it_is_offered_only_once_the_phone_has_written(db, user_id):
    chat = _chat(db, user_id)
    assert "phone_action" not in _offered(db, chat, user_id)
    _turn(db, chat, from_phone=True)
    db.commit()
    assert "phone_action" in _offered(db, chat, user_id)
    # Kept: a later message from the web does not take it out of the list.
    _turn(db, chat, from_phone=False)
    assert "phone_action" in _offered(db, chat, user_id)


def test_never_in_a_chat_nobody_attends_or_an_agent_chat(db, user_id):
    for extra in ({"unattended": True}, {"kind": "agent"}):
        chat = _chat(db, user_id, **extra)
        _turn(db, chat, from_phone=True)
        db.commit()
        assert "phone_action" not in _offered(db, chat, user_id), extra


def test_the_tool_is_sent_as_the_spec_says(db):
    tool = tools_service.REGISTRY["phone_action"]
    spec = harness_spec.tools()["phone_action"]
    assert tool.description == spec["description"]
    assert tool.parameters == spec["parameters"]
    assert spec["scope"] == "llembas" and spec["requires"] == ["phone"]


# --- The capability over /api/v2 -------------------------------------------------------
def _bearer(db) -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == "frodo@shire.test"))
    _, raw = api_tokens.create(db, user, name="LLeMbas for Android", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def test_the_app_declares_the_capability_with_a_message(client, db, registered, monkeypatch):
    monkeypatch.setattr(generation_service, "ensure", lambda *a, **k: None)
    _model(db)
    headers = _bearer(db)
    made = client.post("/api/v2/chats", headers=headers,
                       json={"text": "Hello", "model": "m", "client": ["phone_actions"]})
    assert made.status_code == 201, made.text
    chat_id = made.json()["chat"]["id"]
    db.expire_all()
    chat = db.get(Chat, chat_id)
    first = chat.phone_message_id
    assert first

    # From elsewhere: the mark stays on the phone's message.
    sent = client.post(f"/api/v2/chats/{chat_id}/messages", headers=headers,
                       json={"text": "And from the web"})
    assert sent.status_code == 201
    db.expire_all()
    assert db.get(Chat, chat_id).phone_message_id == first

    plain = client.post("/api/v2/chats", headers=headers, json={"text": "Hi", "model": "m"})
    db.expire_all()
    assert db.get(Chat, plain.json()["chat"]["id"]).phone_message_id == ""
    assert phone.declares({"phone_actions": True}) and not phone.declares("phone_actions")


# --- A call -------------------------------------------------------------------------
async def test_a_call_waits_on_the_card_and_the_phones_answer_is_the_result(
    db, user_id, monkeypatch
):
    chat = _chat(db, user_id)
    message_id = _turn(db, chat, from_phone=True)
    seen: list[dict] = []
    monkeypatch.setattr(generation_service, "stream_chat", _stub(
        [[_call("phone_action", {**ALARM, "seconds": 5})], [_text("Set.")]], seen))
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    task = await _run_until_card(generation, message_id)
    try:
        card = generation.pending
        assert card is not None and card.kind == interaction.KIND_PHONE
        # The action the phone is sent: its own fields only.
        assert card.items[0].phone == ALARM
        assert generation.status == "Waiting for you to confirm it on your phone…"
        assert generation_service.answer(
            chat.id, card.id, picks={"phone": ["done"], "text.phone": ["Alarm set for 07:00."]}
        )
        await asyncio.wait_for(task, 5)
    finally:
        generation_service._RUNNING.pop(message_id, None)

    result = seen[1]["messages"][-1]["content"]
    assert "Done on the phone.\n\nThe phone says: Alarm set for 07:00." in result
    event = generation.tool_events[0]
    assert event["phone_result"] == {"status": "done", "message": "Alarm set for 07:00."}
    assert "phone_pending" not in event


async def test_declined_on_the_page_is_the_persons_no(db, user_id, monkeypatch):
    chat = _chat(db, user_id)
    message_id = _turn(db, chat, from_phone=True)
    seen: list[dict] = []
    monkeypatch.setattr(generation_service, "stream_chat", _stub(
        [[_call("phone_action", ALARM)], [_text("Fine.")]], seen))
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    task = await _run_until_card(generation, message_id)
    try:
        assert generation_service.answer(chat.id, generation.pending.id,
                                         verdict=interaction.DENY)
        await asyncio.wait_for(task, 5)
    finally:
        generation_service._RUNNING.pop(message_id, None)
    assert seen[1]["messages"][-1]["content"].endswith(
        harness_spec.loop()["phone"]["texts"]["declined"])


async def test_a_message_not_from_the_phone_answers_unavailable(db, user_id, monkeypatch):
    chat = _chat(db, user_id)
    _turn(db, chat, from_phone=True)
    message_id = _turn(db, chat, from_phone=False)
    seen: list[dict] = []
    monkeypatch.setattr(generation_service, "stream_chat", _stub(
        [[_call("phone_action", ALARM)], [_text("I cannot.")]], seen))
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    await asyncio.wait_for(generation_service._run(generation), 5)
    assert generation.pending is None
    assert seen[1]["messages"][-1]["content"].endswith(
        harness_spec.tools()["phone_action"]["unavailable"])


async def test_a_bad_call_never_reaches_the_phone(db, user_id, monkeypatch):
    chat = _chat(db, user_id)
    message_id = _turn(db, chat, from_phone=True)
    seen: list[dict] = []
    monkeypatch.setattr(generation_service, "stream_chat", _stub(
        [[_call("phone_action", {"action": "add_stop", "summary": "Add a stop"})],
         [_text("Which?")]], seen))
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    await asyncio.wait_for(generation_service._run(generation), 5)
    assert seen[1]["messages"][-1]["content"].endswith(
        "add_stop needs stop and destination. Nothing was sent to the phone.")


async def test_no_answer_in_time_is_said_so(db, user_id, monkeypatch):
    chat = _chat(db, user_id)
    message_id = _turn(db, chat, from_phone=True)
    seen: list[dict] = []
    monkeypatch.setattr(phone, "wait_seconds", lambda: 0.05)
    monkeypatch.setattr(generation_service, "stream_chat", _stub(
        [[_call("phone_action", ALARM)], [_text("Never mind.")]], seen))
    generation = generation_service.Generation(chat_id=chat.id, message_id=message_id)
    await asyncio.wait_for(generation_service._run(generation), 5)
    assert seen[1]["messages"][-1]["content"].endswith(
        harness_spec.loop()["phone"]["texts"]["no_answer"])


# --- The app's answer -------------------------------------------------------------------
def test_the_interactions_route_carries_the_result(client, db, registered, monkeypatch):
    got: dict = {}

    def answer(chat_id, interaction_id, **kwargs):
        got.update(kwargs)
        return True

    monkeypatch.setattr(generation_service, "answer", answer)
    user = db.scalar(select(User).where(User.email == "frodo@shire.test"))
    chat = _chat(db, user.id)
    response = client.post(
        f"/api/v2/chats/{chat.id}/interactions/abc", headers=_bearer(db),
        json={"result": {"status": "failed", "message": "Google Maps cannot stop a route."}},
    )
    assert response.status_code == 200, response.text
    assert got["picks"]["phone"] == ["failed"]
    assert got["picks"]["text.phone"] == ["Google Maps cannot stop a route."]
