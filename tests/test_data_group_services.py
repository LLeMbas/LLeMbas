"""The services that read a group's data, and the screens that arrange groups.

The embedder is sent the full text of everything it indexes and the reviewer is
sent every picture with its prompt, so a group can name its own of each and
falls back to the instance's when it names none. Then the three places groups
are arranged: the admin page and the person's own settings.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import (
    DEFAULT_GROUP,
    Connection,
    DataGroup,
    Model,
    User,
)
from lembas.services import data_groups, settings_store
from lembas.services import tools as tools_service
from lembas.services.crypto import encrypt
from lembas.services.images import tool as image_tool

HOSTED = "hosted"


@pytest.fixture
def owner(db, registered) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


@pytest.fixture
def setup(db, owner):
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
            Model(connection_id=local.id, model_id="local-embed",
                  capabilities_json={"embeddings": True}),
            Model(connection_id=cloud.id, model_id="cloud-embed",
                  capabilities_json={"embeddings": True}),
            Model(connection_id=local.id, model_id="local-eye",
                  capabilities_json={"vision": True}),
            Model(connection_id=cloud.id, model_id="cloud-eye",
                  capabilities_json={"vision": True}),
        ]
    )
    db.commit()
    return local, cloud


def _plain_user(db) -> User:
    user = User(email="sam@shire.test", name="Sam", password_hash="x", role="user")
    db.add(user)
    db.commit()
    return user


# --- The embedder -----------------------------------------------------------------
# --- The reviewer -------------------------------------------------------------------
def test_the_reviewer_is_the_groups_own_when_it_names_one(db, setup):
    group = data_groups.get(db, HOSTED)
    group.review_model_id = "cloud-eye"
    db.commit()
    config = {"review_enabled": True, "review_model_id": "local-eye"}
    hosted = tools_service.ToolContext(owner_id="x", image_config=config, data_group=HOSTED)
    home = tools_service.ToolContext(owner_id="x", image_config=config)
    assert image_tool._reviewer(hosted)[1] == "cloud-eye"
    assert image_tool._reviewer(home)[1] == "local-eye"


# --- The admin page ---------------------------------------------------------------------
def test_the_admin_page_lists_groups_and_creates_one(client, db, setup):
    assert "Hosted" in client.get("/admin/data-groups").text
    response = client.post("/admin/data-groups", data={"name": "Work"}, follow_redirects=False)
    assert response.status_code == 303
    assert db.scalar(select(DataGroup).where(DataGroup.name == "Work")) is not None


def test_putting_a_connection_into_a_group_and_taking_it_out(client, db, setup):
    local, cloud = setup
    client.post(
        f"/admin/data-groups/{HOSTED}",
        data={"name": "Hosted", "connections_sent": "1", "connection_ids": [local.id]},
    )
    db.expire_all()
    assert db.get(Connection, local.id).data_group_id == HOSTED
    # Unticked: back to the default group, never to "no group".
    assert db.get(Connection, cloud.id).data_group_id == DEFAULT_GROUP


def test_the_detail_page_flags_a_service_in_another_group(client, db, setup):
    settings_store.update(db, {"embedding_model_id": "local-embed"}, key=settings_store.EXTRACTION)
    page = client.get(f"/admin/data-groups/{HOSTED}").text
    assert "its connection is in Default" in page


def test_deleting_a_group_in_use_is_refused_with_the_reason(client, db, setup):
    response = client.post(f"/admin/data-groups/{HOSTED}/delete", follow_redirects=True)
    assert "connections" in response.text
    assert data_groups.get(db, HOSTED) is not None


def test_the_admin_page_is_for_administrators(client, db, setup, owner):
    owner.role = "user"
    db.commit()
    assert client.get("/admin/data-groups").status_code in (303, 403, 404)


def test_the_connection_form_sets_the_group(client, db, setup):
    local, _ = setup
    client.post(
        f"/admin/connections/{local.id}",
        data={"name": "Local", "base_url": local.base_url, "data_group_id": HOSTED},
    )
    db.expire_all()
    assert db.get(Connection, local.id).data_group_id == HOSTED


# --- The person's own settings -------------------------------------------------------------
def test_remapping_for_oneself_needs_the_permission(client, db, setup, owner):
    local, _ = setup
    owner.role = "user"
    db.commit()
    client.post("/api/preferences/data-groups", data={f"group__{local.id}": HOSTED})
    db.expire_all()
    assert data_groups.personal_map(db.get(User, owner.id)) == {}

    settings_store.update(db, {"default_permissions": {data_groups.PERMISSION: True}})
    client.post("/api/preferences/data-groups", data={f"group__{local.id}": HOSTED})
    db.expire_all()
    user = db.get(User, owner.id)
    assert data_groups.personal_map(user) == {local.id: HOSTED}
    assert data_groups.for_connection(db, user, local.id) == HOSTED


def test_a_personal_group_is_made_and_seen_only_by_its_owner(client, db, setup, owner):
    client.post("/api/preferences/data-groups/new", data={"name": "Private"})
    group = db.scalar(select(DataGroup).where(DataGroup.name == "Private"))
    assert group.owner_id == owner.id
    other = _plain_user(db)
    assert group.id not in {g.id for g in data_groups.usable(db, other)}


def test_the_data_tab_appears_once_there_is_a_choice(client, db, setup):
    page = client.get("/settings").text
    assert 'id="tab-data"' in page
    assert "Which group each connection reads" in page


# --- Two embedders of the same width -----------------------------------------------------