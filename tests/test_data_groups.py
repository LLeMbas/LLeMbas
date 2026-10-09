"""Data groups: how one is resolved, the startup sweep, deleting one, and upgrading.

What a group *isolates* is `test_data_group_isolation.py`; how a chat is pinned to
one is `test_data_group_chat_pin.py`. This file is the machinery underneath both:
the resolution order, which lives in one function and must keep living there, and
the upgrade from a database that has never heard of groups.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, select, text

from lembas.db.migrations import sync_schema
from lembas.db.models import (
    DEFAULT_GROUP,
    Chat,
    Connection,
    DataGroup,
    Memory,
    Model,
    Note,
    User,
)
from lembas.db.session import get_engine
from lembas.services import data_groups, settings_store
from lembas.services.crypto import encrypt


@pytest.fixture
def owner(db, registered) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


@pytest.fixture
def reader(db, registered) -> User:
    """A second account, not an administrator, so permissions actually apply."""
    user = User(email="sam@shire.test", name="Sam", password_hash="x", role="user")
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def two(db) -> tuple[Connection, Connection]:
    """A local connection in the default group and a hosted one in its own."""
    db.add(DataGroup(id="hosted", name="Hosted"))
    local = Connection(name="Local", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt(""))
    cloud = Connection(
        name="Cloud",
        base_url="http://127.0.0.1:2",
        api_key_encrypted=encrypt(""),
        data_group_id="hosted",
    )
    db.add_all([local, cloud])
    db.flush()
    db.add_all(
        [
            Model(connection_id=local.id, model_id="local-model", position=0),
            Model(connection_id=cloud.id, model_id="cloud-model", position=1),
        ]
    )
    db.commit()
    return local, cloud


def _grant_manage(db, user: User) -> None:
    settings_store.update(db, {"default_permissions": {data_groups.PERMISSION: True}})


# --- Resolution --------------------------------------------------------------------
def test_a_connection_with_no_group_is_in_the_default_one(db, two, reader):
    local, _ = two
    assert data_groups.for_connection(db, reader, local.id) == DEFAULT_GROUP


def test_the_administrators_choice_applies_to_everybody(db, two, reader):
    _, cloud = two
    assert data_groups.for_connection(db, reader, cloud.id) == "hosted"
    assert data_groups.for_connection(db, None, cloud.id) == "hosted"


def test_a_personal_mapping_needs_the_permission(db, two, reader):
    """Stored and ignored without `data.manage`: taking the permission away puts a
    person back on the instance's arrangement without anybody clearing anything."""
    local, _ = two
    reader.settings_json = {data_groups.SETTING_KEY: {local.id: "hosted"}}
    db.commit()
    assert data_groups.for_connection(db, reader, local.id) == DEFAULT_GROUP

    _grant_manage(db, reader)
    assert data_groups.for_connection(db, reader, local.id) == "hosted"


def test_a_mapping_to_somebody_elses_personal_group_is_ignored(db, two, reader, owner):
    local, _ = two
    db.add(DataGroup(id="theirs", name="Theirs", owner_id=owner.id))
    reader.settings_json = {data_groups.SETTING_KEY: {local.id: "theirs"}}
    db.commit()
    _grant_manage(db, reader)
    assert data_groups.for_connection(db, reader, local.id) == DEFAULT_GROUP


def test_a_connection_naming_a_deleted_group_falls_back_to_the_default(db, two, reader):
    _, cloud = two
    cloud.data_group_id = "gone"
    db.commit()
    assert data_groups.for_connection(db, reader, cloud.id) == DEFAULT_GROUP


def test_a_pair_without_a_connection_resolves_through_the_model(db, two, reader):
    assert data_groups.for_pair(db, reader, "cloud-model") == "hosted"
    assert data_groups.for_pair(db, reader, "local-model") == DEFAULT_GROUP


def test_a_chat_keeps_the_group_it_was_stamped_with(db, two, reader):
    """Derived once, then read. A model moved afterwards does not carry the chat."""
    _, cloud = two
    chat = Chat(user_id=reader.id, model_id="cloud-model", connection_id=cloud.id)
    db.add(chat)
    db.commit()
    assert data_groups.for_chat(db, chat) == "hosted"
    db.commit()

    cloud.data_group_id = DEFAULT_GROUP
    db.commit()
    assert data_groups.for_chat(db, chat) == "hosted"


def test_usable_groups_are_the_instances_and_ones_own(db, two, reader, owner):
    db.add_all(
        [
            DataGroup(id="mine", name="Mine", owner_id=reader.id),
            DataGroup(id="not-mine", name="Not mine", owner_id=owner.id),
        ]
    )
    db.commit()
    ids = {group.id for group in data_groups.usable(db, reader)}
    assert ids == {DEFAULT_GROUP, "hosted", "mine"}


def test_one_group_means_there_is_nothing_to_choose(db, reader):
    assert data_groups.several(db, reader) is False
    db.add(DataGroup(id="second", name="Second"))
    db.commit()
    assert data_groups.several(db, reader) is True


# --- Deleting ------------------------------------------------------------------------
def test_the_default_group_cannot_be_deleted(db):
    with pytest.raises(ValueError, match="default group"):
        data_groups.delete(db, data_groups.ensure_default(db))


def test_a_group_with_records_in_it_cannot_be_deleted(db, reader):
    group = DataGroup(id="busy", name="Busy")
    db.add(group)
    db.add(Note(owner_id=reader.id, title="n", body="b", data_group_id="busy"))
    db.commit()
    with pytest.raises(ValueError, match="1 notes"):
        data_groups.delete(db, group)


def test_a_group_a_connection_is_in_cannot_be_deleted(db, two):
    with pytest.raises(ValueError, match="connections"):
        data_groups.delete(db, data_groups.get(db, "hosted"))


def test_deleting_an_empty_group_clears_every_mapping_to_it(db, reader):
    group = DataGroup(id="empty", name="Empty")
    db.add(group)
    reader.settings_json = {data_groups.SETTING_KEY: {"some-connection": "empty"}}
    db.commit()
    data_groups.delete(db, group)
    db.refresh(reader)
    assert data_groups.personal_map(reader) == {}
    assert data_groups.get(db, "empty") is None


# --- The sweep -------------------------------------------------------------------------
def test_the_sweep_puts_old_rows_in_the_default_group(db, reader):
    db.add(Memory(owner_id=reader.id, content="old"))
    db.commit()
    assert db.scalar(select(Memory)).data_group_id is None

    data_groups.sweep_unassigned(db)
    assert db.scalar(select(Memory)).data_group_id == DEFAULT_GROUP


def test_the_sweep_gives_a_chat_its_models_group(db, two, reader):
    """Not simply the default: a chat some path created without stamping one
    belongs where its model is, or its next turn is refused as a moved chat."""
    chat = Chat(user_id=reader.id, model_id="cloud-model")
    db.add(chat)
    db.commit()
    data_groups.sweep_unassigned(db)
    db.refresh(chat)
    assert chat.data_group_id == "hosted"


def test_a_row_the_sweep_has_not_reached_still_counts_as_default(db, reader):
    db.add(Note(owner_id=reader.id, title="old", body=""))
    db.commit()
    found = db.scalars(select(Note).where(data_groups.condition(Note, DEFAULT_GROUP))).all()
    assert [note.title for note in found] == ["old"]


# --- Upgrading a database from before data groups -----------------------------------------
# What data groups add: one table, and one column on each of these. Taken from the
# models rather than invented, and `test_the_recorded_shape_is_still_real`
# below is what stops the list rotting.
NEW_TABLE = "data_groups"
GROUPED_TABLES = (
    "chats",
    "connections",
    "knowledge_bases",
    "memories",
    "notes",
    "reports",
    "schedules",
    "skills",
)


def _rollback_to_before_data_groups(engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {NEW_TABLE}"))
        for table in GROUPED_TABLES:
            connection.execute(text(f"ALTER TABLE {table} DROP COLUMN data_group_id"))


def test_the_recorded_shape_is_still_real():
    from lembas.db.base import Base

    grouped = {
        table.name for table in Base.metadata.sorted_tables if "data_group_id" in table.columns
    }
    assert grouped == set(GROUPED_TABLES)


def test_a_database_from_before_data_groups_upgrades(db, reader):
    engine = get_engine()
    db.add(Memory(owner_id=reader.id, content="kept"))
    db.commit()
    db.close()

    _rollback_to_before_data_groups(engine)
    assert "data_group_id" not in {c["name"] for c in inspect(engine).get_columns("memories")}

    changes = sync_schema(engine)
    assert any(NEW_TABLE in change for change in changes)
    for table in GROUPED_TABLES:
        assert "data_group_id" in {c["name"] for c in inspect(engine).get_columns(table)}

    # A nullable column arrives empty; the sweep is what files it.
    memory = db.scalar(select(Memory))
    assert memory.data_group_id is None
    data_groups.sweep_unassigned(db)
    db.refresh(memory)
    assert memory.data_group_id == DEFAULT_GROUP

    assert data_groups.get(db, DEFAULT_GROUP) is not None
