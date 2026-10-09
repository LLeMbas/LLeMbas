"""The services that read a group's data, and the screens that arrange groups.

The embedder is sent the full text of everything it indexes and the reviewer is
sent every picture with its prompt, so a group can name its own of each and
falls back to the instance's when it names none. Then the three places groups
are arranged: the admin page, the person's own settings, and the library.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import (
    DEFAULT_GROUP,
    Connection,
    DataGroup,
    Model,
    Note,
    User,
)
from lembas.services import data_groups, settings_store
from lembas.services import tools as tools_service
from lembas.services.crypto import encrypt
from lembas.services.images import tool as image_tool
from lembas.services.library import indexing, notes

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
def test_a_group_without_its_own_embedder_uses_the_instances(db, setup):
    settings_store.update(db, {"embedding_model_id": "local-embed"}, key=settings_store.EXTRACTION)
    assert indexing.embedder(db, HOSTED).model_id == "local-embed"


def test_a_group_with_its_own_embedder_uses_that(db, setup):
    settings_store.update(db, {"embedding_model_id": "local-embed"}, key=settings_store.EXTRACTION)
    group = data_groups.get(db, HOSTED)
    group.embedding_model_id = "cloud-embed"
    db.commit()
    assert indexing.embedder(db, HOSTED).model_id == "cloud-embed"
    assert indexing.embedder(db, DEFAULT_GROUP).model_id == "local-embed"


def test_a_record_is_indexed_by_its_own_groups_embedder(db, owner, setup):
    group = data_groups.get(db, HOSTED)
    group.embedding_model_id = "cloud-embed"
    db.commit()
    note = notes.create(db, owner=owner, title="t", body="b", group=HOSTED)
    assert indexing.group_of_row(db, note) == HOSTED
    assert indexing.embedder(db, indexing.group_of_row(db, note)).model_id == "cloud-embed"


def test_any_configured_counts_a_groups_own_embedder(db, setup):
    assert indexing.any_configured(db) is False
    group = data_groups.get(db, HOSTED)
    group.embedding_model_id = "cloud-embed"
    db.commit()
    assert indexing.any_configured(db) is True


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


# --- The library --------------------------------------------------------------------------------
def test_a_note_is_created_in_the_chosen_group(client, db, setup):
    client.post("/api/library/notes", data={"title": "n", "body": "b", "data_group_id": HOSTED})
    assert db.scalar(select(Note)).data_group_id == HOSTED


def test_a_group_the_person_may_not_use_falls_back_to_the_default(client, db, setup, owner):
    other = _plain_user(db)
    db.add(DataGroup(id="theirs", name="Theirs", owner_id=other.id))
    db.commit()
    client.post("/api/library/notes", data={"title": "n", "body": "b", "data_group_id": "theirs"})
    assert db.scalar(select(Note)).data_group_id == DEFAULT_GROUP


def test_moving_a_note_needs_the_permission(client, db, setup, owner):
    note = notes.create(db, owner=owner, title="n", body="b")
    owner.role = "user"
    db.commit()
    client.post(f"/api/library/notes/{note.id}", data={"title": "n", "body": "b",
                                                       "data_group_id": HOSTED})
    db.expire_all()
    assert db.get(Note, note.id).data_group_id == DEFAULT_GROUP

    settings_store.update(db, {"default_permissions": {data_groups.PERMISSION: True,
                                                        "library.use": True}})
    client.post(f"/api/library/notes/{note.id}", data={"title": "n", "body": "b",
                                                       "data_group_id": HOSTED})
    db.expire_all()
    assert db.get(Note, note.id).data_group_id == HOSTED


def test_the_notes_list_filters_by_group(client, db, setup, owner):
    notes.create(db, owner=owner, title="Home note", body="b")
    notes.create(db, owner=owner, title="Hosted note", body="b", group=HOSTED)
    page = client.get(f"/library/notes?group={HOSTED}").text
    assert "Hosted note" in page and "Home note" not in page


def test_a_single_group_instance_shows_nothing_about_groups(client, db, owner):
    """The ordinary instance: no chip, no select, no group cards -- for anybody who
    could not make a personal group either. An administrator can, so the cards are
    theirs. The Data tab itself is always there: Start over lives in it."""
    notes.create(db, owner=owner, title="n", body="b")
    owner.role = "user"
    db.commit()
    assert 'name="data_group_id"' not in client.get("/library/notes/new").text
    page = client.get("/settings").text
    assert "Your data groups" not in page
    assert 'id="tab-data"' in page and "Start over" in page


# --- Two embedders of the same width -----------------------------------------------------
def test_a_query_skips_chunks_another_model_of_the_same_width_made(db, owner):
    """Width cannot tell two 1024-wide models apart, and with an embedder per
    group two of them on one instance is ordinary. The query says which model
    made it, and only that model's chunks are scored."""
    from lembas.db.models import CHUNK_NOTE, Chunk
    from lembas.services.library import chunks as chunk_service
    from lembas.services.library import retrieval

    ours = notes.create(db, owner=owner, title="Ours", body="x")
    theirs = notes.create(db, owner=owner, title="Theirs", body="y")
    for note, model_id, vector in ((ours, "embed-a", [0.6, 0.8]), (theirs, "embed-b", [1.0, 0.0])):
        db.add(
            Chunk(
                owner_id=owner.id,
                resource_type=CHUNK_NOTE,
                resource_id=note.id,
                ordinal=0,
                text="t",
                vector=chunk_service.pack(vector),
                dims=2,
                model_id=model_id,
            )
        )
    db.commit()

    query = retrieval.QueryVector([1.0, 0.0])
    query.model_id = "embed-a"
    assert [hit.id for hit in retrieval.semantic_ids(db, CHUNK_NOTE, query)] == [ours.id]
    # A plain list keeps the old width-only behaviour, and the closer vector wins.
    plain = retrieval.semantic_ids(db, CHUNK_NOTE, [1.0, 0.0])
    assert [hit.id for hit in plain][0] == theirs.id
