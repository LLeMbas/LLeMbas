"""Temporary chats: hidden from the sidebar, and swept a day later."""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Folder, Message, Model
from lembas.services import chat as chat_service
from lembas.services.crypto import encrypt


def _connection(db) -> Connection:
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()
    return connection


def _temporary(db, chat_id: str, *, title: str = "Passing thought") -> Chat:
    chat = db.get(Chat, chat_id)
    chat.temporary = True
    chat.title = title
    db.commit()
    return chat


# --- Hidden ------------------------------------------------------------------
def test_a_temporary_chat_is_not_in_the_sidebar(client: TestClient, db, registered, make_chat):
    _connection(db)
    _temporary(db, make_chat())
    assert "Passing thought" not in client.get("/chat").text


def test_a_temporary_chat_inside_a_folder_is_not_listed(
    client: TestClient, db, registered, make_chat
):
    """The folder branch renders through the relationship, which is why this
    needs asserting separately from the unfiled list."""
    _connection(db)
    client.post("/api/folders", data={"name": "Quests"})
    folder = db.scalar(select(Folder))

    chat_id = make_chat()
    client.patch(f"/api/chats/{chat_id}", data={"folder_id": folder.id})
    _temporary(db, chat_id)

    page = client.get("/chat").text
    assert "Passing thought" not in page
    assert "Empty" in page


def test_a_temporary_chat_gets_no_unread_dot(client: TestClient, db, registered, make_chat):
    """There is no sidebar row for the dot, and the toast would name a chat
    nobody can navigate to."""
    _connection(db)
    chat = _temporary(db, make_chat())
    chat.unread = True
    db.commit()

    response = client.get("/api/chats/unread")
    assert chat.id not in response.text
    assert "HX-Trigger" not in response.headers


def test_a_finished_temporary_reply_is_not_unread(db, registered, make_chat):
    from lembas.services import generation as generation_service

    _connection(db)
    chat_id = make_chat()
    _temporary(db, chat_id)
    reply = Message(chat_id=chat_id, role="assistant", content="", complete=False)
    db.add(reply)
    db.commit()

    generation = generation_service.Generation(chat_id=chat_id, message_id=reply.id)
    generation.content.append("Waybread.")
    generation_service._persist(generation, "", 0.0)

    db.expire_all()
    assert db.get(Chat, chat_id).unread is False


# --- Starting one -------------------------------------------------------------
def test_the_new_chat_screen_carries_the_flag(client: TestClient, db, registered):
    _connection(db)
    assert 'name="temporary"' not in client.get("/chat").text
    assert 'name="temporary"' in client.get("/chat?temporary=1").text


def _temporary_link(page: str) -> str:
    match = re.search(r'href="([^"]*)"\s+aria-label="Temporary chat"', page)
    assert match, "no temporary toggle on the page"
    return html.unescape(match.group(1))


def _picker_url(page: str) -> str:
    match = re.search(r'data-picker-navigate="([^"]*)"', page)
    assert match, "no navigating model picker on the page"
    return html.unescape(match.group(1))


def test_the_temporary_toggle_keeps_the_chosen_model(client: TestClient, db, registered):
    """Temporary chats could only be started on the default model: the toggle
    linked to a bare `/chat?temporary=1`, so the model picked a moment before
    was dropped."""
    connection = _connection(db)
    db.add(Model(connection_id=connection.id, model_id="other-model"))
    db.commit()

    on = _temporary_link(client.get("/chat?model=other-model").text)
    assert "model=other-model" in on and "temporary=1" in on
    assert 'name="model_id" value="other-model"' in client.get(on).text

    off = _temporary_link(client.get(on).text)
    assert "model=other-model" in off and "temporary" not in off


def test_choosing_a_model_keeps_the_temporary_flag(client: TestClient, db, registered):
    """The other half: the picker navigated to a bare `/chat?model=`, so
    picking a model after the toggle quietly made the chat an ordinary one."""
    connection = _connection(db)
    db.add(Model(connection_id=connection.id, model_id="other-model"))
    db.commit()

    url = _picker_url(client.get("/chat?temporary=1").text)
    assert url.endswith("model=")
    page = client.get(url + "other-model").text
    assert 'name="temporary"' in page
    assert 'name="model_id" value="other-model"' in page
    assert _picker_url(client.get("/chat").text) == "/chat?model="


def test_both_links_keep_the_folder(client: TestClient, db, registered):
    _connection(db)
    client.post("/api/folders", data={"name": "Quests"})
    folder = db.scalar(select(Folder))

    page = client.get(f"/chat?folder={folder.id}").text
    assert f"folder={folder.id}" in _temporary_link(page)
    assert f"folder={folder.id}" in _picker_url(page)


def test_starting_a_temporary_chat_sets_the_flag(client: TestClient, db, registered):
    _connection(db)
    client.post("/api/chats/start", data={"content": "hello", "temporary": "true"})
    assert db.scalar(select(Chat)).temporary is True


def test_starting_an_ordinary_chat_does_not(client: TestClient, db, registered):
    _connection(db)
    client.post("/api/chats/start", data={"content": "hello"})
    assert db.scalar(select(Chat)).temporary is False


# --- Keeping one --------------------------------------------------------------
def test_keeping_a_chat_clears_the_flag(client: TestClient, db, registered, make_chat):
    _connection(db)
    chat = _temporary(db, make_chat())

    response = client.post(f"/api/chats/{chat.id}/keep")
    assert response.status_code == 204
    assert response.headers["HX-Refresh"] == "true"

    db.refresh(chat)
    assert chat.temporary is False
    assert "Passing thought" in client.get("/chat").text


def test_keeping_someone_elses_chat_is_not_found(client: TestClient, db, registered, make_chat):
    from lembas.db.models import User
    from lembas.security.passwords import hash_password

    _connection(db)
    chat = _temporary(db, make_chat())
    other = User(name="Sam", email="s@shire.test", password_hash=hash_password("x"))
    db.add(other)
    db.commit()
    chat.user_id = other.id
    db.commit()

    assert client.post(f"/api/chats/{chat.id}/keep").status_code == 404


# --- The sweep ----------------------------------------------------------------
def _aged(db, chat_id: str, hours: float) -> None:
    """Backdate the chat's newest message, which is what the sweep measures."""
    when = datetime.now(UTC) - timedelta(hours=hours)
    message = Message(chat_id=chat_id, role="user", content="hello", created_at=when)
    db.add(message)
    db.commit()


def test_the_sweep_removes_a_stale_temporary_chat(db, registered, make_chat):
    _connection(db)
    chat_id = make_chat()
    _temporary(db, chat_id)
    _aged(db, chat_id, 25)

    assert chat_service.sweep_temporary(db) == 1
    assert db.get(Chat, chat_id) is None


def test_the_sweep_keeps_one_still_in_use(db, registered, make_chat):
    """Age is measured from the newest message. A conversation still going at
    hour 23 must not vanish mid-sentence."""
    _connection(db)
    chat_id = make_chat()
    _temporary(db, chat_id)
    _aged(db, chat_id, 40)
    _aged(db, chat_id, 0.5)

    assert chat_service.sweep_temporary(db) == 0
    assert db.get(Chat, chat_id) is not None


def test_the_sweep_leaves_ordinary_chats_alone(db, registered, make_chat):
    _connection(db)
    chat_id = make_chat()
    _aged(db, chat_id, 500)

    assert chat_service.sweep_temporary(db) == 0
    assert db.get(Chat, chat_id) is not None


def test_a_temporary_chat_with_no_messages_ages_from_its_own_row(db, registered, make_chat):
    _connection(db)
    chat_id = make_chat()
    chat = _temporary(db, chat_id)
    chat.created_at = datetime.now(UTC) - timedelta(hours=30)
    db.commit()

    assert chat_service.sweep_temporary(db) == 1


def test_the_sweep_unlinks_the_files_too(client: TestClient, db, registered, make_chat):
    """Deleting a chat cascades the rows but leaves the files on disk. Anything
    that deletes chats has to remove them while the rows still say which."""
    from lembas.db.models import Attachment
    from lembas.services.files import stored_path

    _connection(db)
    chat_id = make_chat()
    client.post("/api/files", files={"file": ("notes.txt", b"some words", "text/plain")})
    attachment = db.scalar(select(Attachment))
    attachment.chat_id = chat_id
    message = Message(chat_id=chat_id, role="user", content="look")
    db.add(message)
    db.commit()
    attachment.message_id = message.id
    db.commit()

    path = stored_path(attachment.stored_name)
    assert path is not None and path.exists()

    _temporary(db, chat_id)
    db.query(Message).filter(Message.id == message.id).update(
        {"created_at": datetime.now(UTC) - timedelta(hours=30)}
    )
    db.commit()

    chat_service.sweep_temporary(db)
    assert not path.exists()
