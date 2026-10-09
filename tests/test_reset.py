"""Start over: a person empties their own data, at three levels.

Each level deletes exactly its own tables and no more, files go from disk with
their rows, another person's data is untouched, a running reply refuses it, and
the full reset needs the password and a typed word.
"""

from __future__ import annotations

from sqlalchemy import func, select

from lembas.db.models import (
    Chat,
    Folder,
    Memory,
    Message,
    Note,
    Schedule,
    Session,
    Skill,
    Usage,
    User,
)
from lembas.security.passwords import hash_password
from lembas.services import reset as reset_service

PASSWORD = "speak-friend-and-enter"


def _count(db, model, **where) -> int:
    query = select(func.count()).select_from(model)
    for name, value in where.items():
        query = query.where(getattr(model, name) == value)
    db.expire_all()
    return int(db.scalar(query) or 0)


def _seed(db, owner_id: str) -> None:
    """One of everything a reset touches, plus things it must keep."""
    folder = Folder(user_id=owner_id, name="Quests")
    db.add(folder)
    db.commit()
    chat = Chat(user_id=owner_id, title="Road", folder_id=folder.id)
    temporary = Chat(user_id=owner_id, title="Passing", temporary=True)
    db.add_all([chat, temporary])
    db.commit()
    db.add(Message(chat_id=chat.id, role="user", content="hello"))
    db.add(Memory(owner_id=owner_id, content="Likes second breakfast."))
    db.add(Note(owner_id=owner_id, title="Map", body="There and back."))
    db.add(Skill(owner_id=owner_id, name="cook", description="Cook", body="Stew."))
    db.add(Schedule(user_id=owner_id, title="Daily", chat_id=chat.id))
    db.add(Usage(user_id=owner_id, period="2026-10"))
    db.commit()


def _other(db) -> str:
    other = User(name="Sam", email="sam@shire.test", password_hash=hash_password("x" * 12))
    db.add(other)
    db.commit()
    return other.id


def test_deleting_chats_takes_chats_and_folders_only(client, db, registered, user_id):
    _seed(db, user_id)
    response = client.post("/api/preferences/reset/chats", follow_redirects=False)
    assert response.status_code == 303 and "saved=" in response.headers["location"]

    assert _count(db, Chat, user_id=user_id) == 0
    assert _count(db, Folder, user_id=user_id) == 0
    assert _count(db, Message) == 0
    assert _count(db, Memory, owner_id=user_id) == 1
    assert _count(db, Note, owner_id=user_id) == 1


def test_forgetting_takes_memories_only(client, db, registered, user_id):
    _seed(db, user_id)
    client.post("/api/preferences/reset/memory")
    assert _count(db, Memory, owner_id=user_id) == 0
    assert _count(db, Chat, user_id=user_id) == 2


def test_a_full_reset_keeps_the_account_settings_and_usage(client, db, registered, user_id):
    _seed(db, user_id)
    user = db.get(User, user_id)
    user.settings_json = {"language": "sk"}
    db.commit()

    response = client.post(
        "/api/preferences/reset/all",
        data={"current_password": PASSWORD, "confirm_word": "reset"},
        follow_redirects=False,
    )
    assert "saved=" in response.headers["location"]

    for model, column in (
        (Chat, "user_id"),
        (Folder, "user_id"),
        (Memory, "owner_id"),
        (Note, "owner_id"),
        (Skill, "owner_id"),
        (Schedule, "user_id"),
    ):
        assert _count(db, model, **{column: user_id}) == 0, model.__name__

    assert db.get(User, user_id) is not None
    assert db.get(User, user_id).settings_json == {"language": "sk"}
    assert _count(db, Usage, user_id=user_id) == 1
    # Still signed in: the page answers rather than redirecting to login.
    assert client.get("/settings").status_code == 200
    assert _count(db, Session, user_id=user_id) >= 1


def test_another_persons_data_is_untouched(client, db, registered, user_id):
    _seed(db, user_id)
    other = _other(db)
    _seed(db, other)
    client.post(
        "/api/preferences/reset/all",
        data={"current_password": PASSWORD, "confirm_word": "RESET"},
    )
    assert _count(db, Chat, user_id=other) == 2
    assert _count(db, Memory, owner_id=other) == 1
    assert _count(db, Note, owner_id=other) == 1


def test_a_wrong_password_deletes_nothing(client, db, registered, user_id):
    _seed(db, user_id)
    response = client.post(
        "/api/preferences/reset/all",
        data={"current_password": "wrong", "confirm_word": "RESET"},
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]
    assert _count(db, Note, owner_id=user_id) == 1


def test_the_word_is_required(client, db, registered, user_id):
    _seed(db, user_id)
    response = client.post(
        "/api/preferences/reset/all",
        data={"current_password": PASSWORD, "confirm_word": "yes"},
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]
    assert _count(db, Note, owner_id=user_id) == 1


def test_a_running_reply_refuses_it(client, db, registered, user_id):
    from lembas.services import generation as generation_service

    _seed(db, user_id)
    chat = db.scalar(select(Chat).where(Chat.user_id == user_id))
    reply = Message(chat_id=chat.id, role="assistant", content="", complete=False)
    db.add(reply)
    db.commit()
    generation_service._RUNNING[reply.id] = generation_service.Generation(
        chat_id=chat.id, message_id=reply.id
    )
    response = client.post("/api/preferences/reset/chats", follow_redirects=False)
    assert "error=" in response.headers["location"]
    assert _count(db, Chat, user_id=user_id) == 2


def test_files_go_from_disk(client, db, registered, user_id):
    from lembas.db.models import Attachment
    from lembas.services.files import stored_path

    chat = Chat(user_id=user_id, title="With a file")
    db.add(chat)
    db.commit()
    client.post("/api/files", files={"file": ("notes.txt", b"some words", "text/plain")})
    attachment = db.scalar(select(Attachment))
    attachment.chat_id = chat.id
    db.commit()
    path = stored_path(attachment.stored_name)
    assert path is not None and path.exists()

    client.post("/api/preferences/reset/chats")
    assert not path.exists()


def test_the_settings_page_counts_what_each_level_deletes(client, db, registered, user_id):
    _seed(db, user_id)
    page = client.get("/settings").text
    assert "Start over" in page
    assert "chats: 2, messages: 1, folders: 1" in page
    preview = reset_service.preview(db, db.get(User, user_id))
    assert preview["memory"].rows == {"memories": 1}


def test_an_unknown_level_is_refused(client, db, registered):
    response = client.post("/api/preferences/reset/everything", follow_redirects=False)
    assert "error=" in response.headers["location"]
