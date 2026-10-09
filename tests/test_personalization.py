"""Custom instructions and a personality: a person's own, for every
model, opt-in -- nothing is sent until an administrator offers it and the
person has chosen.
"""

from __future__ import annotations

from sqlalchemy import select

from lembas.db.models import Chat, Connection, Model, User
from lembas.services import chat as chat_service
from lembas.services import personalization, settings_store


def _model(db):
    connection = Connection(name="c", base_url="http://127.0.0.1:1/v1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m"))
    db.commit()


def _system(db, chat_id) -> str:
    chat = db.get(Chat, chat_id)
    body = chat_service.build_request(db, chat)
    return body["messages"][0]["content"] if body["messages"][0]["role"] == "system" else ""


def test_nothing_is_sent_until_offered_and_chosen(client, db, registered, make_chat):
    _model(db)
    chat_id = make_chat()
    user = db.scalar(select(User))
    user.settings_json = {
        **(user.settings_json or {}),
        "personalization": {"instructions": "Call me Frodo.", "personality": "concise"},
    }
    db.commit()
    assert "Call me Frodo." not in _system(db, chat_id)

    settings_store.update(db, {"personalization_enabled": True})
    system = _system(db, chat_id)
    assert system.rstrip().endswith(personalization.preset_text("concise"))
    assert "Call me Frodo." in system


def test_the_settings_form_saves_it(client, db, registered):
    settings_store.update(db, {"personalization_enabled": True})
    response = client.post(
        "/api/preferences/personalization",
        data={"instructions": "Be kind.", "personality": "custom", "custom": "Talk like a hobbit."},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.expire_all()
    assert personalization.of(db.scalar(select(User))) == {
        "instructions": "Be kind.",
        "personality": "custom",
        "custom": "Talk like a hobbit.",
        # Saved here is switched on (a CLI may switch it off).
        "on": True,
    }
    assert "Talk like a hobbit." in personalization.block(db, db.scalar(select(User)))
    assert 'id="personalization"' in client.get("/settings").text


def test_an_unknown_preset_is_kept_and_sends_nothing(db, registered):
    """A LLeMbas CLI on a newer harness spec may name a preset this release does
    not list: kept as named, so saving through it does not blank the
    choice -- and with no text for it here, nothing is sent. Anything that is
    not an id is still nothing."""
    assert personalization.cleaned("", "pirate", "")["personality"] == "pirate"
    assert personalization.cleaned("", "../etc/passwd", "")["personality"] == ""
    from lembas.services import settings_store

    settings_store.update(db, {"personalization_enabled": True})
    user = db.scalar(select(User))
    user.settings_json = {"personalization": personalization.cleaned("", "pirate", "")}
    db.commit()
    assert personalization.block(db, user) == ""


def test_sign_in_goes_through_the_identity_provider(client, registered):
    from lembas.security import identity

    assert identity.provider().name == "local"
    client.post("/auth/logout")
    response = client.post(
        "/auth/login",
        data={"email": registered["email"], "password": registered["password"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
