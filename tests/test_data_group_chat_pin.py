"""A chat stays in the data group it was started in.

Its history is the group's data, so every way a chat could reach a model in
another group is closed here: switching its model, the endpoint fallback, a
helper, a base and the `@` menu. And if a chat's own model is moved into another
group afterwards, the next reply is refused rather than sent.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import (
    DEFAULT_GROUP,
    Chat,
    Connection,
    DataGroup,
    Model,
    User,
)
from lembas.services import chat as chat_service
from lembas.services import data_groups, schedules
from lembas.services import subagent as subagent_service
from lembas.services.crypto import encrypt
from lembas.services.library import notes

HOSTED = "hosted"


@pytest.fixture
def owner(db, registered) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


@pytest.fixture
def setup(db, owner):
    """Two local models in the default group, one hosted model in its own."""
    db.add(DataGroup(id=HOSTED, name="Hosted"))
    local = Connection(name="Local", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt(""))
    cloud = Connection(
        name="Cloud",
        base_url="http://127.0.0.1:2",
        api_key_encrypted=encrypt(""),
        data_group_id=HOSTED,
    )
    db.add_all([local, cloud])
    db.flush()
    db.add_all(
        [
            Model(connection_id=local.id, model_id="local-a", position=0),
            Model(connection_id=local.id, model_id="local-b", position=1),
            Model(connection_id=cloud.id, model_id="cloud-model", position=2),
        ]
    )
    db.commit()
    return local, cloud


def _chat(db, owner, model_id="local-a", connection=None, group=DEFAULT_GROUP) -> Chat:
    chat = Chat(
        user_id=owner.id,
        model_id=model_id,
        connection_id=connection.id if connection else None,
        data_group_id=group,
        title="t",
    )
    db.add(chat)
    db.commit()
    return chat


# --- Starting and switching ---------------------------------------------------------
def test_a_new_chat_takes_its_models_group(client, db, setup):
    client.post("/api/chats/start", data={"content": "hi", "model_id": "cloud-model"})
    chat = db.scalars(select(Chat).order_by(Chat.created_at.desc())).first()
    assert chat.data_group_id == HOSTED


def test_a_chat_cannot_be_switched_to_another_groups_model(client, db, owner, setup):
    local, _ = setup
    chat = _chat(db, owner, connection=local)
    response = client.patch(f"/api/chats/{chat.id}", data={"model_id": "cloud-model"})
    assert response.status_code == 409
    assert "data group" in response.text
    db.refresh(chat)
    assert chat.model_id == "local-a"


def test_a_chat_can_switch_within_its_group(client, db, owner, setup):
    local, _ = setup
    chat = _chat(db, owner, connection=local)
    response = client.patch(f"/api/chats/{chat.id}", data={"model_id": "local-b"})
    assert response.status_code in (200, 204)
    db.refresh(chat)
    assert chat.model_id == "local-b"


def test_the_picker_names_the_models_it_leaves_out(client, db, owner, setup):
    """Named, not silently missing -- and not offered as options either."""
    local, _ = setup
    chat = _chat(db, owner, connection=local)
    page = client.get(f"/chat/{chat.id}").text
    assert "In another data group" in page
    assert 'data-picker-value="cloud-model"' not in page


def test_available_models_narrow_to_a_group(db, owner, setup):
    ids = [m.model_id for m in chat_service.available_models(db, owner, HOSTED)]
    assert ids == ["cloud-model"]
    everything = [m.model_id for m in chat_service.available_models(db, owner)]
    assert everything == ["local-a", "local-b", "cloud-model"]


# --- The endpoint fallback ----------------------------------------------------------
def test_the_fallback_never_repoints_a_chat_into_another_group(db, owner, setup):
    """A model id served by two connections: the chat's own going away must not
    land it on the other provider, which would be handed the whole history."""
    local, cloud = setup
    db.add(Model(connection_id=cloud.id, model_id="local-a"))
    local.enabled = False
    db.commit()
    chat = _chat(db, owner, connection=local)
    with pytest.raises(chat_service.LLMError):
        chat_service.resolve_endpoint(db, chat)
    db.refresh(chat)
    assert chat.connection_id == local.id


def test_a_chat_whose_model_moved_is_refused_rather_than_sent(db, owner, setup):
    local, _ = setup
    chat = _chat(db, owner, connection=local)
    speaker = chat_service.speaker_for(db, chat)
    assert data_groups.refusal(db, owner, chat, speaker) == ""

    local.data_group_id = HOSTED
    db.commit()
    refusal = data_groups.refusal(db, owner, chat, speaker)
    assert "Default" in refusal and "Hosted" in refusal


# --- Other models reaching the conversation ------------------------------------------
def test_a_helper_reads_its_parents_group(db, owner, setup):
    """A helper runs on its parent's own model, so it is in the
    parent's group -- never the default one by omission."""
    _, cloud = setup
    parent = _chat(db, owner, model_id="cloud-model", connection=cloud, group=HOSTED)
    child = subagent_service._create_child(db, parent, title="q", write=False)
    assert child.data_group_id == HOSTED


# --- Bases and the @ menu -------------------------------------------------------------
def test_a_base_from_another_group_cannot_be_attached(client, db, owner, setup):
    from lembas.services.library import documents

    local, _ = setup
    chat = _chat(db, owner, connection=local)
    base = documents.create_base(db, owner=owner, name="Hosted base", group=HOSTED)
    response = client.post(f"/api/chats/{chat.id}/bases", data={"base_id": base.id})
    assert response.status_code == 404


def test_the_mention_menu_offers_only_the_chats_group(client, db, owner, setup):
    local, _ = setup
    chat = _chat(db, owner, connection=local)
    notes.create(db, owner=owner, title="Home note", body="x")
    notes.create(db, owner=owner, title="Hosted note", body="x", group=HOSTED)
    page = client.get(f"/api/files/mention-picker?q=&chat_id={chat.id}").text
    assert "Home note" in page and "Hosted note" not in page


def test_on_the_new_chat_screen_the_chosen_model_decides(client, db, owner, setup):
    notes.create(db, owner=owner, title="Hosted note", body="x", group=HOSTED)
    page = client.get("/api/files/mention-picker?q=&model_id=cloud-model").text
    assert "Hosted note" in page


def test_a_note_from_another_group_cannot_be_attached(client, db, owner, setup):
    local, _ = setup
    chat = _chat(db, owner, connection=local)
    hosted = notes.create(db, owner=owner, title="Hosted note", body="x", group=HOSTED)
    response = client.post(
        "/api/files/from-note", data={"note_id": hosted.id, "chat_id": chat.id}
    )
    assert "not available" in response.text


# --- Schedules -------------------------------------------------------------------------
def test_a_schedule_is_stamped_with_its_models_group(db, owner, setup):
    rule = {"at": {"weekdays": [0], "times": ["15:00"]}}
    schedule = schedules.create(
        db, owner=owner, title="t", instruction="i", rule=rule, model_id="cloud-model"
    )
    assert schedule.data_group_id == HOSTED
    assert db.get(Chat, schedule.chat_id).data_group_id == HOSTED
