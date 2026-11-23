"""`/api/v2`: a message that waits, and the two things its person does with it.

A message waits (`queued: true`) behind a reply -- or, in a chat on a linked
computer, because what became of it there is not known (`origin: "unsure"`)
or the computer holds it for its next turn (`origin: "carried"`). The page has
"Send now" and "Discard" on such a bubble; these are the same two, for an app,
through the same cores. Editing one is refused on both doors.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import Chat, Message, User
from lembas.security import api_tokens
from lembas.services import generation as generation_service


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    api_tokens.reset_rates()
    from lembas.security import hardening

    hardening.reset_throttles()

    # Replies are started and not written: what is asserted is the rows.
    async def run(generation):
        generation.done = True

    monkeypatch.setattr(generation_service, "_run", run)
    yield
    generation_service._RUNNING.clear()
    generation_service._TASKS.clear()


def _bearer(db, email="frodo@shire.test") -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == email))
    _, raw = api_tokens.create(db, user, name="LLeMbas for Android", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def _waiting(db, chat_id: str, origin: str = "") -> str:
    row = Message(chat_id=chat_id, role="user", content="and the linter?", complete=True,
                  queued=True, origin=origin)
    db.add(row)
    db.commit()
    return row.id


@pytest.mark.parametrize("origin", ["", "unsure", "carried"])
def test_a_waiting_message_is_read_sent_and_answered_like_a_turn(
    client, db, registered, make_chat, origin
):
    chat_id = make_chat()
    waiting = _waiting(db, chat_id, origin)
    headers = _bearer(db)
    read = client.get(f"/api/v2/chats/{chat_id}/messages", headers=headers).json()["messages"]
    assert [(m["id"], m["queued"], m["origin"]) for m in read] == [(waiting, True, origin)]

    sent = client.post(f"/api/v2/chats/{chat_id}/messages/{waiting}/send", headers=headers)
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["message"]["id"] == waiting and body["message"]["queued"] is False
    assert body["message"]["origin"] == ""
    assert body["reply"]["role"] == "assistant" and body["reply"]["complete"] is False
    db.expire_all()
    assert db.get(Message, waiting).queued is False

    # No longer waiting: said, in words, with a 409 -- by both actions.
    for action in ("send", "discard"):
        again = client.post(f"/api/v2/chats/{chat_id}/messages/{waiting}/{action}", headers=headers)
        assert again.status_code == 409 and "not waiting" in again.json()["detail"]


def test_a_waiting_message_is_discarded(client, db, registered, make_chat):
    chat_id = make_chat()
    waiting = _waiting(db, chat_id, "unsure")
    headers = _bearer(db)
    gone = client.post(f"/api/v2/chats/{chat_id}/messages/{waiting}/discard", headers=headers)
    assert gone.status_code == 200 and gone.json() == {"id": waiting, "discarded": True}
    db.expire_all()
    assert db.get(Message, waiting) is None
    again = client.post(f"/api/v2/chats/{chat_id}/messages/{waiting}/discard", headers=headers)
    assert again.status_code == 404


def test_sending_now_is_refused_while_a_reply_is_being_written(client, db, registered, make_chat):
    chat_id = make_chat()
    waiting = _waiting(db, chat_id)
    db.add(Message(chat_id=chat_id, role="assistant", content="", complete=False))
    db.commit()
    refused = client.post(
        f"/api/v2/chats/{chat_id}/messages/{waiting}/send", headers=_bearer(db)
    )
    assert refused.status_code == 409 and "current reply" in refused.json()["detail"]
    db.expire_all()
    assert db.get(Message, waiting).queued is True


def test_the_two_actions_are_the_chat_owners_and_need_the_app_scope(
    client, db, registered, make_chat
):
    from lembas.security import passwords

    chat_id = make_chat()
    waiting = _waiting(db, chat_id, "unsure")
    db.add(User(email="sam@shire.test", name="Sam", password_hash=passwords.hash_password("x")))
    db.commit()
    for action in ("send", "discard"):
        path = f"/api/v2/chats/{chat_id}/messages/{waiting}/{action}"
        assert client.post(path).status_code == 401
        # (Whatever every other write to somebody else's chat answers.)
        assert client.post(path, headers=_bearer(db, "sam@shire.test")).status_code in (403, 404)
    db.expire_all()
    assert db.get(Message, waiting).queued is True


@pytest.mark.parametrize("origin", ["", "unsure", "carried"])
def test_a_waiting_message_cannot_be_edited_on_either_door(
    client, db, registered, make_chat, origin
):
    """It has not been a turn yet, so there is nothing to rewind to -- and on a
    linked computer the old words may still be held there: edited and sent,
    the model could be given both. Refused in words; Send or Discard."""
    chat_id = make_chat()
    waiting = _waiting(db, chat_id, origin)
    app = client.post(
        f"/api/v2/chats/{chat_id}/messages/{waiting}/edit", headers=_bearer(db),
        json={"text": "something else"},
    )
    assert app.status_code == 409 and "still waiting" in app.json()["detail"]
    page = client.post(
        f"/api/chats/{chat_id}/messages/{waiting}/edit", data={"content": "something else"}
    )
    assert page.status_code == 409
    db.expire_all()
    row = db.get(Message, waiting)
    assert row.content == "and the linter?" and row.queued and row.origin == origin
    assert db.get(Chat, chat_id) is not None
