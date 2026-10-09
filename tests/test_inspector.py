"""The admin request inspector."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Message, Model, User
from lembas.services import settings_store
from lembas.services.crypto import encrypt


def _connection(db, **model_kwargs) -> None:
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model", **model_kwargs))
    db.commit()


@pytest.fixture
def plain_user(client: TestClient, db, registered) -> User:
    """A second, non-admin account, left signed in."""
    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    return db.scalar(select(User).where(User.email == "sam@shire.test"))


# --- Access -------------------------------------------------------------------
def test_an_admin_sees_the_toggle(client: TestClient, db, registered, make_chat):
    _connection(db)
    page = client.get(f"/chat/{make_chat()}").text
    assert 'data-toggle="#inspector"' in page
    assert 'id="inspector"' in page


def test_a_plain_user_has_no_inspector(client: TestClient, db, plain_user, make_chat):
    _connection(db)
    chat_id = make_chat(email="sam@shire.test")
    page = client.get(f"/chat/{chat_id}").text
    assert 'data-toggle="#inspector"' not in page
    assert 'id="inspector"' not in page


def test_a_plain_user_is_refused_the_endpoint(client: TestClient, db, plain_user, make_chat):
    """Owning the chat is not enough. Reading a conversation is a different act
    from configuring the instance, which is why sharing has no admin branch
    either -- and this must not become one by another name."""
    _connection(db)
    chat_id = make_chat(email="sam@shire.test")
    assert client.get(f"/api/chats/{chat_id}/inspect").status_code == 403


def test_an_admin_cannot_inspect_someone_elses_chat(
    client: TestClient, db, registered, make_chat
):
    _connection(db)
    chat_id = make_chat()
    other = User(name="Sam", email="s@shire.test", password_hash="x")
    db.add(other)
    db.commit()
    db.get(Chat, chat_id).user_id = other.id
    db.commit()

    assert client.get(f"/api/chats/{chat_id}/inspect").status_code == 404


# --- What it shows ------------------------------------------------------------
def test_it_says_it_is_rebuilt_not_recorded(client: TestClient, db, registered, make_chat):
    _connection(db)
    body = client.get(f"/api/chats/{make_chat()}/inspect").text
    assert "not a recording" in body


def test_it_shows_the_system_message_and_the_request(
    client: TestClient, db, registered, make_chat
):
    settings_store.update(db, {"system_prompt": "Speak as Gandalf."})
    _connection(db, context_length=8192)
    chat_id = make_chat()
    db.add(Message(chat_id=chat_id, role="user", content="what is lembas?"))
    db.commit()

    body = client.get(f"/api/chats/{chat_id}/inspect").text
    assert "Speak as Gandalf." in body
    assert "test-model" in body
    assert "what is lembas?" in body
    assert "8192 tokens" in body


def test_model_output_is_escaped(client: TestClient, db, registered, make_chat):
    """The JSON is full of model output and search results. Hard rule 6 applies
    here exactly as it does to a chat bubble."""
    _connection(db)
    chat_id = make_chat()
    db.add(Message(chat_id=chat_id, role="assistant", content="<script>alert(1)</script>"))
    db.commit()

    body = client.get(f"/api/chats/{chat_id}/inspect").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_an_image_is_not_dumped_into_the_dom(
    client: TestClient, db, registered, make_chat
):
    """A phone photo is megabytes of base64. Fidelity is the point of the panel,
    but not that much of it."""
    import io

    from PIL import Image

    from lembas.db.models import Attachment

    _connection(db, capabilities_json={"vision": True})
    chat_id = make_chat()

    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), "red").save(buffer, format="PNG")
    client.post("/api/files", files={"file": ("p.png", buffer.getvalue(), "image/png")})
    attachment = db.scalar(select(Attachment))

    message = Message(chat_id=chat_id, role="user", content="look")
    db.add(message)
    db.commit()
    attachment.message_id = message.id
    attachment.chat_id = chat_id
    db.commit()

    body = client.get(f"/api/chats/{chat_id}/inspect").text
    assert "base64 image omitted" in body
    assert "iVBORw0" not in body


def test_a_chat_with_no_reply_yet_says_so(client: TestClient, db, registered, make_chat):
    _connection(db)
    assert "Nothing has been answered" in client.get(f"/api/chats/{make_chat()}/inspect").text
