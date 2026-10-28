"""The upgrade path, which every existing install takes on update.

There is no Alembic here by design -- hard rule 4, additive-only, with
`db/migrations.py:sync_schema` deriving the change from the models by diffing
them against the live database. That makes the differ the *only* thing standing
between an older deployment and a broken one, and this file is what exercises
it as a migration.

`tests/conftest.py` runs `create_all` and *then* `sync_schema`, so the schema it
is handed is always already current: the differ finds nothing missing, does
nothing, and reports success. A suite run alone only proves that a no-op is a
no-op, and a unit test of `_add_column_sql`'s DDL string never touches a
database.

So these build an **old-shaped database with rows in it** and upgrade it for
real. The shape is not invented: `OLD_TABLES` and `OLD_COLUMNS` are tables and
columns that were really added to the models after an earlier release.
`test_the_recorded_shape_is_still_real`
is what stops that list rotting into a test that upgrades nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from lembas.db.base import Base
from lembas.db.migrations import sync_schema
from lembas.db.session import get_engine

# Tables an old schema does not have. `sync_schema` has to create them.
OLD_TABLES = (
    "push_subscriptions",
    "usage",
)

# Columns added to tables that already existed, and therefore already had rows.
# These are the interesting half: a new *table* is empty by definition, but a
# new *column* has to arrive beside data somebody cares about.
OLD_COLUMNS = (
    ("chats", "parent_chat_id"),
    ("chats", "unattended"),
    ("chats", "start_dir"),
    ("groups", "limits_json"),
    # What the other models are told about this one. A Text column with a scalar
    # default, so the backfill is the easy kind -- listed because the hard kind
    # (`reasoning_efforts`, below) breaks a live instance when nothing catches
    # it, and a column absent from this list is a column the migration
    # tests do not exercise.
    ("models", "notes"),
    # Which model wrote a message. Nullable, so the backfill is the easy kind --
    # listed because a column absent from here is one the migration tests do not
    # exercise at all.
    ("messages", "connection_id"),
    # The plan on the chat (1.3.0). Nullable JSON: an older row reads NULL and
    # `Chat.plan` falls back to the todo list or the message snapshot it had.
    ("chats", "plan_json"),
)


def _rollback_to_an_old_schema(engine) -> None:
    """Take a current database back to an old schema's shape.

    Backwards rather than forwards because the alternative is importing two
    versions of the models into one interpreter, which is not a thing Python
    will do -- `lembas.db.models` is already imported and registered on one
    `Base.metadata`. Dropping what was added produces the same *shape* the
    differ has to repair, which is what is under test.
    """
    with engine.begin() as connection:
        for table in OLD_TABLES:
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
        for table, column in OLD_COLUMNS:
            connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))


def _columns(engine, table: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table)}


def test_the_recorded_shape_is_still_real():
    """The rollback above names tables and columns by hand, and a name that has
    since been removed or renamed would make it silently upgrade nothing --
    a test that passes because it tested an empty set.
    """
    tables = Base.metadata.tables

    for table in OLD_TABLES:
        assert table in tables, f"{table} is no longer a table; fix OLD_TABLES"
    for table, column in OLD_COLUMNS:
        assert table in tables, table
        assert column in tables[table].c, f"{table}.{column} is gone; fix OLD_COLUMNS"


def test_an_old_database_with_data_upgrades(db):
    """The whole point. Rows written before the upgrade must still be there
    afterwards, with their values, and the new columns must exist beside them.

    A failure here is somebody's instance not starting after pressing Update,
    or -- worse and quieter -- starting with a column that silently reads NULL.
    """
    engine = get_engine()

    # Data first, while the schema still has the columns the ORM expects.
    from lembas.db.models import Chat, Group, User

    owner = User(name="Frodo", email="f@shire.test", password_hash="x")
    db.add(owner)
    db.commit()
    group = Group(name="Fellowship")
    db.add(group)
    db.commit()
    chat = Chat(user_id=owner.id, model_id="mithril", title="A chat from before")
    db.add(chat)
    db.commit()
    chat_id, owner_id, group_id = chat.id, owner.id, group.id
    db.close()

    _rollback_to_an_old_schema(engine)

    # Confirm the rollback actually removed things, or the assertions below
    # would pass against a database that was never old.
    assert "usage" not in inspect(engine).get_table_names()
    assert "unattended" not in _columns(engine, "chats")

    changes = sync_schema(engine)

    assert changes, "the differ reported nothing to do on an old database"
    for table in OLD_TABLES:
        assert table in inspect(engine).get_table_names(), table
    for table, column in OLD_COLUMNS:
        assert column in _columns(engine, table), f"{table}.{column}"

    # And the rows are still what they were.
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT title, model_id, user_id FROM chats WHERE id = :id"),
            {"id": chat_id},
        ).one()
        assert row.title == "A chat from before"
        assert row.model_id == "mithril"
        assert row.user_id == owner_id
        assert (
            connection.execute(
                text("SELECT name FROM groups WHERE id = :id"), {"id": group_id}
            ).scalar()
            == "Fellowship"
        )


def test_a_nullable_column_arrives_empty_rather_than_defaulted(db):
    """A nullable column is added with **no** default, so an existing row reads
    NULL -- the value the model treats as absent.

    An earlier version defaulted every column by type, which meant an added
    foreign key arrived as `""` on old rows, and every "is this set?" check
    downstream was wrong about them. `parent_chat_id` is exactly that shape: a
    chat that predates subagents is not a helper, and `""` would not say so.
    """
    engine = get_engine()

    from lembas.db.models import Chat, User

    owner = User(name="Sam", email="s@shire.test", password_hash="x")
    db.add(owner)
    db.commit()
    chat = Chat(user_id=owner.id, model_id="m")
    db.add(chat)
    db.commit()
    chat_id = chat.id
    db.close()

    _rollback_to_an_old_schema(engine)
    sync_schema(engine)

    with engine.connect() as connection:
        parent = connection.execute(
            text("SELECT parent_chat_id FROM chats WHERE id = :id"), {"id": chat_id}
        ).scalar()
    assert parent is None, "an added foreign key must be absent, not empty"


def test_a_not_null_column_backfills_every_existing_row(db):
    """SQLite refuses to add a NOT NULL column without a default, so one is
    derived from the type. `chats.unattended` is a boolean: an old row has to
    come back False rather than NULL, or every `if chat.unattended` downstream
    reads a null as a value.
    """
    engine = get_engine()

    from lembas.db.models import Chat, User

    owner = User(name="Merry", email="m@shire.test", password_hash="x")
    db.add(owner)
    db.commit()
    db.add(Chat(user_id=owner.id, model_id="m"))
    db.commit()
    db.close()

    _rollback_to_an_old_schema(engine)
    sync_schema(engine)

    with engine.connect() as connection:
        values = connection.execute(text("SELECT unattended FROM chats")).scalars().all()
    assert values, "no rows survived the upgrade"
    assert all(value in (0, False) for value in values), values


def test_upgrading_twice_changes_nothing_the_second_time(db):
    """It runs on every startup and has to converge. A second pass reporting
    work would mean it was re-adding something, which on SQLite is an error that
    stops the application booting."""
    engine = get_engine()
    _rollback_to_an_old_schema(engine)

    assert sync_schema(engine)
    assert sync_schema(engine) == []


@pytest.mark.parametrize("table", OLD_TABLES)
def test_each_new_table_is_usable_after_the_upgrade(db, table):
    """Created is not the same as correct. A table the differ made but got the
    columns wrong on fails at the first insert, which is the first *reply* on an
    upgraded instance rather than at startup."""
    engine = get_engine()
    _rollback_to_an_old_schema(engine)
    sync_schema(engine)

    declared = {column.name for column in Base.metadata.tables[table].c}
    assert _columns(engine, table) == declared


# --- A list-shaped JSON column added to a database that already had rows -----
#
# Got wrong, a live instance answers 500 the moment it updates:
#
#   ValueError: Attribute 'reasoning_efforts' does not accept objects
#               of type <class 'dict'>
#
# The shape cannot be read off `column.type.python_type`:
# `MutableList.as_mutable(JSON)` returns the *same* JSON type object with a
# listener attached -- it does not subclass it -- so `python_type` is `dict` for
# both flavours. A list column backfilled with `'{}'` fails on load, because
# MutableList refuses a dict while *loading*, so every page that lists models
# raises.
#
# A fresh database does not show it, because `conftest.py` creates the column
# from the model rather than backfilling it by a migration. These tests exercise
# the path an upgrade actually runs.
def test_a_list_column_is_backfilled_with_a_list():
    from lembas.db.migrations import _default_shape, _literal_default
    from lembas.db.models import Model

    columns = {c.name: c for c in Model.__table__.columns}
    assert _default_shape(columns["reasoning_efforts"]) is list
    assert _literal_default(columns["reasoning_efforts"]) == "'[]'"


def test_a_dict_column_still_gets_a_dict():
    from lembas.db.migrations import _literal_default
    from lembas.db.models import Model

    columns = {c.name: c for c in Model.__table__.columns}
    assert _literal_default(columns["capabilities_json"]) == "'{}'"
    assert _literal_default(columns["params_json"]) == "'{}'"


def _seed_model(engine, **overrides):
    """A real row, made the way the application makes one.

    Built through the ORM rather than a hand-written INSERT: the table has
    several NOT NULL columns and a test that enumerates them is a test that
    breaks every time one is added, for reasons having nothing to do with what
    it is checking.
    """
    from sqlalchemy.orm import Session

    from lembas.db.models import Connection, Model

    with Session(engine) as session:
        connection = Connection(
            name="local", base_url="http://127.0.0.1:1", api_key_encrypted=""
        )
        session.add(connection)
        session.flush()
        model = Model(connection_id=connection.id, model_id="bonsai", **overrides)
        session.add(model)
        session.commit()
        return model.id


def test_the_damage_already_written_is_repaired_on_start(tmp_path):
    """The fix to `_literal_default` helps the next instance. This is the one
    that helps the instance that has already updated."""
    from sqlalchemy import create_engine, text

    from lembas.db.migrations import repair_json_shapes, sync_schema

    engine = create_engine(f"sqlite:///{tmp_path}/repair.db")
    sync_schema(engine)
    model_id = _seed_model(engine)

    # Exactly what the broken backfill left behind on a row that predated the
    # column: the wrong empty value, in a column that refuses it on load.
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE models SET reasoning_efforts = '{}' WHERE id = :id"),
            {"id": model_id},
        )

    assert repair_json_shapes(engine)

    with engine.begin() as connection:
        stored = connection.execute(
            text("SELECT reasoning_efforts FROM models WHERE id = :id"), {"id": model_id}
        ).scalar()
    assert stored == "[]"

    # And the row loads again, which is the whole point -- the failure was a
    # ValueError while reading, not a wrong value sitting harmlessly.
    from sqlalchemy.orm import Session

    from lembas.db.models import Model

    with Session(engine) as session:
        assert session.get(Model, model_id).reasoning_efforts == []

    # Converges: a second run finds nothing left to do.
    assert repair_json_shapes(engine) == []


def test_the_repair_leaves_a_dict_column_alone(tmp_path):
    """`{}` is a legitimate value in a MutableDict column and must survive."""
    from sqlalchemy import create_engine, text

    from lembas.db.migrations import repair_json_shapes, sync_schema

    engine = create_engine(f"sqlite:///{tmp_path}/keep.db")
    sync_schema(engine)
    model_id = _seed_model(engine)

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE models SET capabilities_json = '{}' WHERE id = :id"),
            {"id": model_id},
        )
    repair_json_shapes(engine)
    with engine.begin() as connection:
        stored = connection.execute(
            text("SELECT capabilities_json FROM models WHERE id = :id"), {"id": model_id}
        ).scalar()
    assert stored == "{}"
