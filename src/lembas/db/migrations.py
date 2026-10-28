"""Additive schema synchronisation.

This project has no Alembic, by design: it is SQLite-only and the schema is
created at startup. That was fine until the first live instance had data in it,
at which point adding a column to a model stopped being free -- ``create_all``
only creates missing *tables*, so a new column silently never appears and every
query mentioning it fails.

What this module does instead is derive the migration from the models: compare
each table's declared columns against what the database actually has, and
``ALTER TABLE ... ADD COLUMN`` for whatever is missing. That covers new tables
and new columns, which is essentially every schema change this project makes.

What it deliberately does NOT do:

* rename, drop or retype a column
* add a PRIMARY KEY or UNIQUE constraint to an existing table
* backfill anything requiring application logic

SQLite cannot do most of those with ALTER TABLE anyway; they need the
create-copy-swap dance. Anything in that category is a hand-written job and
should be added to MANUAL_STEPS below so it is at least visible.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import Engine, inspect, text
from sqlalchemy.schema import Column, Table

from lembas.db.base import Base

log = logging.getLogger(__name__)

# Schema changes that this module cannot perform. Kept as documentation so a
# failure has somewhere to point rather than being a mystery.
MANUAL_STEPS: list[str] = []



def _default_shape(column: Column) -> type | None:
    """`list` or `dict`, from the column's own Python-side default.

    `default=list` and `default=dict` are how the two JSON flavours are
    declared, and SQLAlchemy keeps the callable. Calling it is cheap and is the
    only way to tell a MutableList column from a MutableDict one -- see the note
    in `_literal_default`.
    """
    default = column.default
    if default is None or not getattr(default, "is_callable", False):
        return None
    try:
        # SQLAlchemy wraps a zero-argument callable to take a context.
        produced = default.arg(None)
    except Exception:  # a default we cannot call tells us nothing
        return None
    if isinstance(produced, list):
        return list
    if isinstance(produced, dict):
        return dict
    return None


def _literal_default(column: Column) -> str | None:
    """A SQL literal to backfill an existing row's new column with.

    SQLite refuses to add a NOT NULL column without a default, and refuses a
    non-constant default. Python-side defaults (``default=dict``,
    ``default=utcnow``) are callables and cannot be expressed in DDL, so the
    value is derived from the column type instead. New rows still get the real
    Python default; this only fills the rows that already exist.
    """
    default = column.default
    if default is not None and not default.is_callable and not default.is_clause_element:
        value: Any = default.arg
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            escaped = value.replace("'", "''")
            return f"'{escaped}'"

    affinity = column.type.__class__.__name__.upper()
    if "JSON" in affinity:
        # MutableList columns must start as [] and MutableDict as {}; guessing
        # wrong makes the first read blow up rather than return empty.
        #
        # 🚨 NOT `column.type.python_type`. `MutableList.as_mutable(JSON)`
        # returns the *same* JSON type object with an event listener attached --
        # it does not subclass or wrap it -- so the type cannot tell you which
        # of the two it is, and `JSON.python_type` is `dict` for both. Reading
        # it would backfill every list column with `'{}'`, and `MutableList`
        # refuses a dict on *load*: not an empty list but a ValueError, and a
        # 500 on every page that touches the table.
        #
        # The Python-side default is the only honest signal: a JSONList column
        # is declared `default=list` and a JSONDict one `default=dict`, and
        # calling it says which. Anything that cannot be called or produces
        # neither falls back to `{}`, which is what this always assumed.
        return "'[]'" if _default_shape(column) is list else "'{}'"
    if "BOOL" in affinity:
        return "0"
    if any(token in affinity for token in ("INT", "FLOAT", "NUMERIC", "DECIMAL")):
        return "0"
    if "DATE" in affinity or "TIME" in affinity:
        return "CURRENT_TIMESTAMP"
    if any(token in affinity for token in ("STRING", "TEXT", "VARCHAR", "CHAR")):
        return "''"
    return None


def _add_column_sql(table: Table, column: Column, dialect) -> str | None:
    type_sql = column.type.compile(dialect)
    default = _literal_default(column)

    if not column.nullable and default is None:
        log.error(
            "cannot add NOT NULL column %s.%s: no usable default. Add it by hand.",
            table.name,
            column.name,
        )
        return None

    prefix = f'"{table.schema}".' if table.schema else ""
    parts = [f'ALTER TABLE {prefix}"{table.name}" ADD COLUMN "{column.name}" {type_sql}']
    if not column.nullable:
        # SQLite refuses a NOT NULL column with no default, so existing rows
        # have to be given something. That is the only reason a default is
        # emitted at all.
        parts.append("NOT NULL")
        parts.append(f"DEFAULT {default}")
    # A nullable column gets no default on purpose. Backfilling one would give
    # existing rows a value the model does not consider absent -- an added
    # foreign key would arrive as "" rather than NULL, and every "is this set?"
    # check downstream would be wrong about rows that predate it.
    return " ".join(parts)


def repair_json_shapes(engine: Engine) -> list[str]:
    """Put right any JSON column backfilled with the wrong empty value.

    A shape read off `column.type.python_type` -- which is `dict` for a
    MutableList column as well as a MutableDict one -- backfills a list-shaped
    JSON column with `'{}'` on every row that already exists (see
    `_literal_default`). `MutableList` refuses a dict, and refuses it while
    *loading*, so the symptom is not an empty list but a `ValueError` and a 500
    on every page that touches the table.

    Converges: it runs on every start, it is
    idempotent, and on a database that was never damaged it does nothing. Only
    the exact wrong value is rewritten -- `'{}'` in a column whose default
    produces a list -- because `{}` cannot be a legitimate value there, while
    anything else in that column might be somebody's data.
    """
    fixed: list[str] = []
    inspector = inspect(engine)
    known = set(inspector.get_table_names())

    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in known:
                continue
            for column in table.columns:
                if "JSON" not in column.type.__class__.__name__.upper():
                    continue
                if _default_shape(column) is not list:
                    continue
                result = connection.execute(
                    text(
                        f'UPDATE "{table.name}" SET "{column.name}" = \'[]\' '
                        f"WHERE \"{column.name}\" = '{{}}'"
                    )
                )
                if result.rowcount:
                    fixed.append(f"{table.name}.{column.name} ({result.rowcount} row(s))")
                    log.warning(
                        "repaired %s.%s on %d row(s): was '{}' in a list column",
                        table.name,
                        column.name,
                        result.rowcount,
                    )
    return fixed


def _setting(connection, key: str) -> dict[str, Any]:
    """One settings group as stored, or {} -- read in SQL, because these steps
    run before anything may load an ORM row."""
    row = connection.execute(text("SELECT value FROM settings WHERE key = :k"), {"k": key}).first()
    if row is None:
        return {}
    try:
        value = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def infer_model_kinds(engine: Engine) -> list[str]:
    """Mark the models this instance already embeds with as `embedding`.

    `Model.kind` arrives NULL on every existing row -- "nobody has said", served
    as a chat model that `/v1/embeddings` still accepts. Where the instance
    itself says otherwise, it is written down: the model the library's semantic
    search uses (Admin -> Extraction), any data group's own embedding model, a
    model on a connection whose name or provider says it serves embeddings
    (`embedding`, `embeddings-lan`), and a model whose id says so
    (`guess_kind`). Only NULL rows are looked at, so an administrator's own
    choice is never overruled, and a start after this has run finds the same
    rows and converges.
    """
    from lembas.db.models.connection import MODEL_EMBEDDING, guess_kind

    done: list[str] = []
    with engine.begin() as connection:
        existing = set(inspect(connection).get_table_names())
        if "models" not in existing or "connections" not in existing:
            return done
        if "kind" not in {c["name"] for c in inspect(connection).get_columns("models")}:
            return done
        named: set[str] = set()
        pairs: set[tuple[str, str]] = set()
        if "settings" in existing:
            chosen = str(_setting(connection, "extraction").get("embedding_model_id") or "")
            if chosen.strip():
                named.add(chosen.strip())
        if "data_groups" in existing:
            columns = {c["name"] for c in inspect(connection).get_columns("data_groups")}
            if {"embedding_model_id", "embedding_connection_id"} <= columns:
                for model_id, connection_id in connection.execute(
                    text(
                        "SELECT embedding_model_id, embedding_connection_id FROM data_groups "
                        "WHERE embedding_model_id IS NOT NULL AND embedding_model_id != ''"
                    )
                ):
                    if connection_id:
                        pairs.add((model_id, connection_id))
                    else:
                        named.add(model_id)
        connection_columns = {c["name"] for c in inspect(connection).get_columns("connections")}
        provider = "c.provider" if "provider" in connection_columns else "''"
        rows = list(
            connection.execute(
                text(
                    f"SELECT m.id, m.model_id, m.connection_id, c.name, {provider} "
                    "FROM models m JOIN connections c ON c.id = m.connection_id "
                    "WHERE m.kind IS NULL"
                )
            )
        )
        marked = 0
        for row_id, model_id, connection_id, name, provider_name in rows:
            for_embedding = (
                model_id in named
                or (model_id, connection_id) in pairs
                or "embed" in f"{name or ''} {provider_name or ''}".lower()
                or guess_kind(model_id) == MODEL_EMBEDDING
            )
            if for_embedding:
                connection.execute(
                    text("UPDATE models SET kind = :k WHERE id = :i"),
                    {"k": MODEL_EMBEDDING, "i": row_id},
                )
                marked += 1
        if marked:
            done.append(f"mark {marked} model(s) as embedding models")
    for line in done:
        log.info("migration: %s", line)
    return done


def provider_types(engine: Engine) -> list[str]:
    """Give every connection that predates provider types the type its row
    means (`providers.from_legacy_kind`): `kind = anthropic` is Anthropic, a
    known cloud host is its own type, the rest stay OpenAI-compatible (the
    backfill). Run only on the start that adds the column, so a type an
    administrator chose afterwards is never changed back."""
    from lembas.services.llm import providers

    done: list[str] = []
    with engine.begin() as connection:
        rows = connection.execute(text("SELECT id, kind, base_url FROM connections")).all()
        for row_id, kind, base_url in rows:
            wanted = providers.from_legacy_kind(kind, base_url or "")
            if wanted != providers.OPENAI_COMPATIBLE:
                connection.execute(
                    text("UPDATE connections SET provider_type = :t, kind = :k WHERE id = :i"),
                    {"t": wanted, "k": providers.protocol_of(wanted), "i": row_id},
                )
                done.append(f"connection {row_id} is {wanted}")
    return done


def ensure_default_org(engine: Engine) -> list[str]:
    """The first organisation (`organisation.DEFAULT_ORG`), made once.

    Accounts, groups and data groups created without naming an organisation
    belong to it, so it has to exist before the first of them is inserted.
    In SQL, because this runs before anything may load an ORM row.
    """
    from lembas.db.models.organisation import DEFAULT_ORG, DEFAULT_ORG_NAME

    with engine.begin() as connection:
        found = connection.execute(
            text("SELECT 1 FROM organisations WHERE id = :i"), {"i": DEFAULT_ORG}
        ).first()
        if found:
            return []
        connection.execute(
            text(
                "INSERT INTO organisations (id, name, slug, created_at, updated_at) "
                "VALUES (:i, :n, :s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"i": DEFAULT_ORG, "n": DEFAULT_ORG_NAME, "s": DEFAULT_ORG},
        )
    log.info("migration: created the default organisation")
    return ["create the default organisation"]


def sync_schema(engine: Engine) -> list[str]:
    """Bring the database up to the declared schema. Returns what it changed."""
    import lembas.db.models  # noqa: F401  (registers every table on the metadata)

    changes: list[str] = []

    inspector = inspect(engine)
    # The blob store's tables live in the attached schema (`db/models/blob.py`).
    known_tables = {
        (schema, name)
        for schema in {table.schema for table in Base.metadata.sorted_tables}
        for name in inspector.get_table_names(schema=schema)
    }
    for table in Base.metadata.sorted_tables:
        if (table.schema, table.name) not in known_tables:
            changes.append(f"create table {table.fullname}")

    # Creates anything missing; existing tables are left alone.
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            existing = {
                col["name"] for col in inspector.get_columns(table.name, schema=table.schema)
            }
            for column in table.columns:
                if column.name in existing:
                    continue
                statement = _add_column_sql(table, column, engine.dialect)
                if statement is None:
                    continue
                connection.execute(text(statement))
                changes.append(f"add column {table.fullname}.{column.name}")
                log.info("schema: %s", statement)

    # The first organisation, before anything can insert an account into it.
    try:
        changes.extend(ensure_default_org(engine))
    except Exception:  # pragma: no cover - logged, and the next start retries
        log.exception("could not create the default organisation")

    # The models this instance embeds with say so. Converges on its own, and
    # failing must not stop the start.
    for step in (infer_model_kinds,):
        try:
            changes.extend(step(engine))
        except Exception:  # a conversion that fails must not stop a start
            log.exception("could not run %s", step.__name__)
    if "add column connections.provider_type" in changes:
        try:
            changes.extend(provider_types(engine))
        except Exception:  # a conversion that fails must not stop a start
            log.exception("could not give the connections their provider types")

    # Before the search indexes, and before anything can try to load a row:
    # a column left holding the wrong empty value makes the ORM raise on read.
    try:
        for repair in repair_json_shapes(engine):
            changes.append(f"repair {repair}")
    except Exception:  # a repair that fails must not stop a start
        log.exception("could not repair JSON column shapes")

    # The file and chat index's own full-text table (services/index/schema.py).
    try:
        from lembas.services.index import schema as index_schema

        for index in index_schema.ensure(engine):
            changes.append(f"create search index {index}")
    except Exception:  # search is not worth refusing to start over
        log.exception("could not create the file index's full-text table")

    if changes:
        log.info("schema synchronised: %d change(s)", len(changes))
    for step in MANUAL_STEPS:
        log.warning("manual schema step still required: %s", step)

    return changes
