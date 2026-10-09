"""Temporary chats read but do not write, and live as long as the admin says.

"Read yes, write no", and the lifetime is an administrator's setting. The state
is derived from `Chat.temporary` plus "is not a helper's chat", so `Keep this
chat` lifts every restriction with no second flag.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from lembas.db.models import Chat, Connection, Message, Model, User
from lembas.services import chat as chat_service
from lembas.services import harness, settings_store
from lembas.services import tools as tools_service
from lembas.services.crypto import encrypt
from lembas.services.tools import RISK_WRITE


def _chat(db, user_id: str, *, temporary: bool = True, **fields) -> Chat:
    connection = db.scalar(select(Connection))
    if connection is None:
        connection = Connection(
            name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
        )
        db.add(connection)
        db.commit()
        db.add(
            Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True})
        )
        db.commit()
    chat = Chat(
        user_id=user_id,
        model_id="m",
        connection_id=connection.id,
        temporary=temporary,
        **fields,
    )
    db.add(chat)
    db.commit()
    return chat


def _writes(db, chat: Chat, user_id: str) -> set[str]:
    offered = tools_service.resolve_tools(db, chat, db.get(User, user_id))
    return {tool.name for tool in offered.by_name.values() if tool.risk == RISK_WRITE}


def _reads(db, chat: Chat, user_id: str) -> set[str]:
    offered = tools_service.resolve_tools(db, chat, db.get(User, user_id))
    return {tool.name for tool in offered.by_name.values() if tool.risk != RISK_WRITE}


# --- Read yes, write no --------------------------------------------------------
def test_an_ordinary_chat_may_write(db, user_id):
    chat = _chat(db, user_id, temporary=False)
    assert "memory_add" in _writes(db, chat, user_id)


def test_a_temporary_chat_is_offered_no_write_tool(db, user_id):
    chat = _chat(db, user_id)
    assert _writes(db, chat, user_id) == set()
    # And keeps what reads: "read yes".
    reads = _reads(db, chat, user_id)
    assert {"notes_search", "knowledge_search"} & reads


def test_keeping_the_chat_gives_writing_back(client, db, registered, user_id):
    chat = _chat(db, user_id)
    assert client.post(f"/api/chats/{chat.id}/keep").status_code == 204
    db.refresh(chat)
    assert "memory_add" in _writes(db, chat, user_id)


def test_a_helpers_chat_is_governed_by_its_own_scope(db, user_id):
    """A helper's chat is also `temporary` (so it is in no listing), and a helper
    sent to write must still be able to. Its parent link tells them apart."""
    parent = _chat(db, user_id, temporary=False)
    child = _chat(db, user_id, parent_chat_id=parent.id, scope_json={"write": True})
    assert not chat_service.read_only_temporary(child)
    assert "memory_add" in _writes(db, child, user_id)


def test_a_temporary_chat_cannot_send_a_writing_helper(db, user_id):
    import asyncio

    from lembas.services import subagent
    from lembas.services.tools import ToolContext

    chat = _chat(db, user_id)
    outcome = asyncio.run(
        subagent._run_subagent(
            ToolContext(owner_id=user_id, chat_id=chat.id),
            {"task": "Write a note about lembas.", "write": True},
        )
    )
    assert "temporary chat" in outcome.content


def test_the_model_is_told_the_chat_is_temporary(db, user_id):
    chat = _chat(db, user_id)
    values = harness.context_variables(db, db.get(User, user_id), [], chat)
    assert values["temporary"] == "yes"
    assert values["temporary_lifetime"] == "1 hour after the last message"

    ordinary = _chat(db, user_id, temporary=False)
    assert harness.context_variables(db, db.get(User, user_id), [], ordinary)["temporary"] == ""


def test_the_lifetime_in_the_prompt_is_from_the_models_side(db, user_id):
    settings_store.update(db, {"temporary_lifetime_minutes": 0})
    chat = _chat(db, user_id)
    values = harness.context_variables(db, db.get(User, user_id), [], chat)
    assert values["temporary_lifetime"] == "when the person leaves it"


# --- The lifetime -------------------------------------------------------------
def _said(db, chat: Chat, minutes_ago: float) -> None:
    db.add(
        Message(
            chat_id=chat.id,
            role="user",
            content="hello",
            created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        )
    )
    db.commit()


def test_the_default_lifetime_is_an_hour(db, user_id):
    young, old = _chat(db, user_id), _chat(db, user_id)
    _said(db, young, 50)
    _said(db, old, 70)
    assert chat_service.sweep_temporary(db) == 1
    assert db.get(Chat, young.id) is not None


def test_the_sweep_honours_the_setting(db, user_id):
    settings_store.update(db, {"temporary_lifetime_minutes": 300})
    chat = _chat(db, user_id)
    _said(db, chat, 120)
    assert chat_service.sweep_temporary(db) == 0
    settings_store.update(db, {"temporary_lifetime_minutes": 90})
    assert chat_service.sweep_temporary(db) == 1


def test_a_helpers_kept_chat_still_lives_a_day(db, user_id):
    parent = _chat(db, user_id, temporary=False)
    child = _chat(db, user_id, parent_chat_id=parent.id)
    _said(db, child, 120)
    assert chat_service.sweep_temporary(db) == 0
    _said(db, child, 0)
    db.query(Message).filter(Message.chat_id == child.id).update(
        {"created_at": datetime.now(UTC) - timedelta(hours=25)}
    )
    db.commit()
    assert chat_service.sweep_temporary(db) == 1


def test_a_chat_with_a_reply_running_is_not_swept(db, user_id):
    from lembas.services import generation as generation_service

    chat = _chat(db, user_id)
    _said(db, chat, 300)
    reply = Message(chat_id=chat.id, role="assistant", content="", complete=False)
    db.add(reply)
    db.commit()
    db.query(Message).filter(Message.id == reply.id).update(
        {"created_at": datetime.now(UTC) - timedelta(minutes=300)}
    )
    db.commit()
    generation_service._RUNNING[reply.id] = generation_service.Generation(
        chat_id=chat.id, message_id=reply.id
    )
    assert chat_service.sweep_temporary(db) == 0
    generation_service._RUNNING.clear()
    assert chat_service.sweep_temporary(db) == 1


def test_the_admin_sets_the_lifetime(client, db, registered):
    page = client.get("/admin/general").text
    assert 'name="temporary_lifetime_minutes"' in page
    client.post("/admin/general", data={"temporary_lifetime_minutes": "5000"})
    db.expire_all()
    assert settings_store.get(db, "temporary_lifetime_minutes") == 1440
    client.post("/admin/general", data={"temporary_lifetime_minutes": "15"})
    db.expire_all()
    assert settings_store.get(db, "temporary_lifetime_minutes") == 15


# --- "Until you leave it" -----------------------------------------------------
def test_leaving_marks_and_the_sweep_deletes_after_the_grace(client, db, registered, user_id):
    settings_store.update(db, {"temporary_lifetime_minutes": 0})
    chat = _chat(db, user_id)
    _said(db, chat, 1)
    assert client.post(f"/api/chats/{chat.id}/leave").status_code == 204

    assert chat_service.sweep_temporary(db) == 0  # inside the grace
    later = datetime.now(UTC) + chat_service.LEAVE_GRACE + timedelta(seconds=1)
    assert chat_service.sweep_temporary(db, now=later) == 1


def test_coming_back_cancels_the_leave(client, db, registered, user_id):
    """A reload reports a leave too; the page that comes back cancels it."""
    settings_store.update(db, {"temporary_lifetime_minutes": 0})
    chat = _chat(db, user_id)
    _said(db, chat, 1)
    client.post(f"/api/chats/{chat.id}/leave")
    assert client.get(f"/chat/{chat.id}").status_code == 200

    later = datetime.now(UTC) + chat_service.LEAVE_GRACE + timedelta(seconds=1)
    assert chat_service.sweep_temporary(db, now=later) == 0


def test_leave_does_nothing_to_an_ordinary_chat(client, db, registered, user_id):
    settings_store.update(db, {"temporary_lifetime_minutes": 0})
    chat = _chat(db, user_id, temporary=False)
    client.post(f"/api/chats/{chat.id}/leave")
    later = datetime.now(UTC) + timedelta(hours=1)
    assert chat_service.sweep_temporary(db, now=later) == 0
    assert db.get(Chat, chat.id) is not None


def test_the_floor_catches_a_leave_nobody_reported(db, user_id):
    settings_store.update(db, {"temporary_lifetime_minutes": 0})
    chat = _chat(db, user_id)
    _said(db, chat, 5)
    assert chat_service.sweep_temporary(db) == 0
    _said(db, chat, 0)
    db.query(Message).filter(Message.chat_id == chat.id).update(
        {"created_at": datetime.now(UTC) - timedelta(minutes=11)}
    )
    db.commit()
    assert chat_service.sweep_temporary(db) == 1


def test_the_page_carries_the_beacon_only_when_leaving_deletes(client, db, registered, user_id):
    chat = _chat(db, user_id)
    _said(db, chat, 1)
    page = client.get(f"/chat/{chat.id}").text
    assert "data-temporary-leave" not in page
    assert "Temporary · read-only" in page

    settings_store.update(db, {"temporary_lifetime_minutes": 0})
    page = client.get(f"/chat/{chat.id}").text
    assert f'data-temporary-leave="/api/chats/{chat.id}/leave"' in page


def test_the_ticker_runs_the_sweep(db, user_id):
    from lembas.services.schedule import ticker

    chat = _chat(db, user_id)
    chat_id = chat.id
    _said(db, chat, 120)
    ticker.tidy(force=True)
    db.expunge_all()
    assert db.get(Chat, chat_id) is None


def test_a_temporary_device_chat_is_held_at_plan(db, registered):
    """On a linked CLI too (where agent chats run, and nowhere else): the turn
    goes out in Plan whatever the composer says, and the chat keeps its own mode
    for "Keep this chat" to give back."""
    from lembas.services import generation

    user = db.scalar(select(User))
    chat = _chat(db, user.id, kind="agent", device_token_id="tok", agent_mode="auto")
    message = Message(chat_id=chat.id, role="user", content="Tidy the repo")
    db.add(message)
    db.commit()

    turn = generation._device_turn(db, chat, message, generation.Generation(chat.id, "m"))
    assert turn["mode"] == "plan"
    assert db.get(Chat, chat.id).agent_mode == "auto"

    chat.temporary = False
    db.commit()
    turn = generation._device_turn(db, chat, message, generation.Generation(chat.id, "m"))
    assert turn["mode"] == "auto"
