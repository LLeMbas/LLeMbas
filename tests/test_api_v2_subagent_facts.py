"""What /api/v2 tells an app about subagents (harness spec 3.3.0,
`subagents.md` §4), for each case an app has to draw: a chat on the server and
an agent chat, on a model serving one request at a time and on one serving
several. The apps never work it out themselves, so every place a chat or a
model is sent says the same as the page -- or says nothing (`null`) where it
was not worked out, never a "no" it did not check.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import KIND_AGENT, Chat, Connection, Model, User
from lembas.security import api_tokens
from lembas.services import settings_store, team_run
from lembas.services.crypto import encrypt


@pytest.fixture(autouse=True)
def subagents_on(db, registered):
    settings_store.update(db, {"enabled": True}, key=settings_store.SUBAGENTS)
    api_tokens.reset_rates()
    yield


def _connection(db, name: str, *, one_model: bool = False) -> Connection:
    row = Connection(
        name=name,
        base_url="http://127.0.0.1:1",
        api_key_encrypted=encrypt(""),
        one_model_at_a_time=one_model,
    )
    db.add(row)
    db.commit()
    return row


def _model(db, connection, model_id: str, *, parallel=None) -> Model:
    row = Model(
        connection_id=connection.id,
        model_id=model_id,
        parallel=parallel,
        single_session=parallel == 1,
        capabilities_json={"tools": True},
    )
    db.add(row)
    db.commit()
    return row


def _chat(db, model: Model, *, kind: str = "chat") -> Chat:
    user = db.scalars(select(User).order_by(User.created_at)).first()
    chat = Chat(
        user_id=user.id, title="t", kind=kind,
        model_id=model.model_id, connection_id=model.connection_id,
    )
    if kind == KIND_AGENT:
        chat.device_token_id = "token-1"
        chat.project_dir = "/srv/work"
        chat.agent_mode = "edit"
    db.add(chat)
    db.commit()
    return chat


def _bearer(db) -> dict[str, str]:
    user = db.scalars(select(User).order_by(User.created_at)).first()
    _, raw = api_tokens.create(db, user, name="LLeMbas Desktop", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def _facts(client, headers, chat: Chat) -> dict:
    """The chat's own facts, and every model's for that chat and for a new chat."""
    detail = client.get(f"/api/v2/chats/{chat.id}", headers=headers).json()["chat"]
    for_chat = client.get(f"/api/v2/models?chat={chat.id}", headers=headers).json()
    for_new = client.get("/api/v2/models", headers=headers).json()
    return {
        "chat": detail["subagents"],
        "models_for_chat": {m["id"]: m["subagents"] for m in for_chat["models"]},
        "models_new": {m["id"]: m["subagents"] for m in for_new["models"]},
        "cap": for_new["subagents_cap"],
    }


def test_a_chat_on_a_model_serving_several_is_offered(client, db):
    model = _model(db, _connection(db, "A"), "wide", parallel=4)
    chat = _chat(db, model)
    facts = _facts(client, _bearer(db), chat)
    assert facts["chat"] == {"offered": True, "named_only": False, "cap": team_run.cap(db)}
    assert facts["models_for_chat"]["wide"] == {"offered": True, "named_only": False}
    assert facts["models_new"]["wide"] == {"offered": True, "named_only": False}
    assert facts["cap"] == team_run.cap(db)


def test_no_stated_limit_counts_as_several(client, db):
    model = _model(db, _connection(db, "A"), "open")
    facts = _facts(client, _bearer(db), _chat(db, model))
    assert facts["chat"]["offered"] is True and facts["chat"]["named_only"] is False


def test_a_one_request_model_alone_is_not_offered(client, db):
    model = _model(db, _connection(db, "Swap", one_model=True), "narrow", parallel=1)
    facts = _facts(client, _bearer(db), _chat(db, model))
    assert facts["chat"]["offered"] is False
    assert facts["models_for_chat"]["narrow"]["offered"] is False
    assert facts["models_new"]["narrow"]["offered"] is False


def test_a_one_request_model_with_another_server_is_offered_named_only(client, db):
    narrow = _model(db, _connection(db, "Swap", one_model=True), "narrow", parallel=1)
    _model(db, _connection(db, "Far"), "far")
    facts = _facts(client, _bearer(db), _chat(db, narrow))
    assert facts["chat"] == {"offered": True, "named_only": True, "cap": team_run.cap(db)}
    assert facts["models_new"]["narrow"] == {"offered": True, "named_only": True}
    assert facts["models_new"]["far"] == {"offered": True, "named_only": False}


@pytest.mark.parametrize("parallel", [1, 4])
def test_an_agent_chats_subagents_are_its_devices_and_offered_whatever_the_model(
    client, db, parallel
):
    """Spec 3.4.0 (subagents.md §14, acp.md capability `agents`): an agent
    chat's subagents run on its device, on the person's models, and its
    setting goes down with every turn -- so it is offered wherever the
    instance and the person allow subagents, whatever the chat's model serves
    (the device judges the models). `/models?chat=` says the same."""
    model = _model(db, _connection(db, "A"), "m", parallel=parallel)
    _model(db, _connection(db, "B"), "other")
    facts = _facts(client, _bearer(db), _chat(db, model, kind=KIND_AGENT))
    assert facts["chat"]["offered"] is True and facts["chat"]["named_only"] is False
    assert all(s["offered"] and not s["named_only"] for s in facts["models_for_chat"].values())


def test_the_list_does_not_say_no_where_it_did_not_look(client, db):
    model = _model(db, _connection(db, "A"), "wide", parallel=4)
    chat = _chat(db, model)
    headers = _bearer(db)
    listed = client.get("/api/v2/chats", headers=headers).json()["chats"]
    row = next(c for c in listed if c["id"] == chat.id)
    # Not worked out for a page of the sidebar: null, read the chat for it.
    assert row["subagents"] is None
    assert client.get(f"/api/v2/chats/{chat.id}", headers=headers).json()["chat"]["subagents"][
        "offered"
    ] is True


def test_off_on_the_instance_is_off_everywhere(client, db):
    settings_store.update(db, {"enabled": False}, key=settings_store.SUBAGENTS)
    model = _model(db, _connection(db, "A"), "wide", parallel=4)
    facts = _facts(client, _bearer(db), _chat(db, model))
    assert facts["chat"]["offered"] is False
    assert facts["models_new"]["wide"]["offered"] is False


def test_a_new_agent_chat_asks_models_with_kind_agent(client, db):
    """Spec 3.4.0: a new agent chat's Subagents setting is offered by the
    instance and the person, not by a model -- the fact the web's new-chat
    slider reads -- and switched off on the instance it is off."""
    _model(db, _connection(db, "A"), "m", parallel=1)
    headers = _bearer(db)
    got = client.get("/api/v2/models?kind=agent", headers=headers).json()
    assert got["agent_subagents"] == {"offered": True, "device_runs": None}
    assert all(m["subagents"] == {"offered": True, "named_only": False} for m in got["models"])
    unknown = client.get("/api/v2/models?kind=agent&device=nope", headers=headers).json()
    assert unknown["agent_subagents"]["device_runs"] is None
    settings_store.update(db, {"enabled": False}, key=settings_store.SUBAGENTS)
    off = client.get("/api/v2/models?kind=agent", headers=headers).json()
    assert off["agent_subagents"]["offered"] is False
    assert "agent_subagents" not in client.get("/api/v2/models", headers=headers).json()
