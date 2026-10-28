"""Engine, session factory and startup schema creation."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from lembas.config import settings
from lembas.db.models.blob import BLOB_SCHEMA

log = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


@event.listens_for(Engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record) -> None:
    """Apply the pragmas SQLite needs to behave under a concurrent web server.

    - WAL lets readers proceed while a write is in flight, which matters because
      a streaming reply holds a write open for the length of the generation.
    - foreign_keys is OFF by default in SQLite, so every ondelete= in the models
      would be decoration without this.
    - busy_timeout makes concurrent writers wait rather than fail instantly.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    # The blob store (`db/models/blob.py`) beside the main file, attached as
    # the schema `blobs` with the same journal settings, so one transaction can
    # write a node and its contents. `foreign_keys` and `busy_timeout` are per
    # connection and already cover it.
    cursor.execute(
        "ATTACH DATABASE ? AS " + BLOB_SCHEMA, (blobs_path_for(_main_file(cursor)),)
    )
    cursor.execute(f"PRAGMA {BLOB_SCHEMA}.journal_mode=WAL")
    cursor.execute(f"PRAGMA {BLOB_SCHEMA}.synchronous=NORMAL")
    cursor.close()
    # The search index's vectors, when the optional extension is installed
    # (`db/vec.py`). Without it the search is keywords only.
    from lembas.db import vec

    vec.load(dbapi_connection)


def _main_file(cursor) -> str:
    for _seq, name, file in cursor.execute("PRAGMA database_list").fetchall():
        if name == "main":
            return file or ""
    return ""


def blobs_path_for(main: str | Path) -> str:
    """`lembas.db` -> `lembas-blobs.db`, beside it. In memory stays in memory."""
    main = str(main)
    if not main or main == ":memory:":
        return ":memory:"
    path = Path(main)
    return str(path.with_name(f"{path.stem}-blobs{path.suffix}"))


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings.ensure_dirs()
        _engine = create_engine(
            f"sqlite:///{settings.db_path}",
            # FastAPI runs sync endpoints in a threadpool, so a connection can
            # legitimately be used from a thread other than the one that made it.
            connect_args={"check_same_thread": False},
            echo=False,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
        )
    return _SessionFactory


# `PRAGMA user_version` of a 2.x database. One value for the whole major; later
# schema steps are additive and run by `db/migrations.py` as before.
SCHEMA_VERSION = 2000
OLD_SUFFIX = ".old"


class OldDatabaseInTheWay(RuntimeError):
    """A 1.x database to set aside, and `lembas.db.old` already taken."""


def _is_1x(path: Path) -> bool:
    """A database with tables in it, below 2000 and without the 2.x tree."""
    connection = sqlite3.connect(str(path))
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        connection.close()
    return bool(tables) and version < SCHEMA_VERSION and "nodes" not in tables


def set_aside_1x(path: Path | None = None) -> Path | None:
    """Rename a 1.x database to `lembas.db.old` so 2.x starts on an empty one.

    2.0 carries nothing over by itself: the old file is kept whole, for going
    back to 1.7 (rename it back) and for the import. Only a 1.x database is
    moved, and only when no `.old` exists -- a second 2.x update must never put
    a 2.x database over the 1.x one. `uploads/`, the secret key and `backups/`
    are not touched: the old database needs all three.

    The WAL is checkpointed first, so the renamed file is whole on its own; any
    `-wal`/`-shm` left beside it move with it. Returns the new path, or None
    when there was nothing to do.
    """
    path = path or settings.db_path
    if not path.exists() or not _is_1x(path):
        return None
    old = path.with_name(path.name + OLD_SUFFIX)
    if old.exists():
        raise OldDatabaseInTheWay(
            f"{path} is a LLeMbas 1.x database and {old} already exists. 2.x would set the "
            f"1.x database aside as {old.name}, but will not overwrite what is there. Move "
            f"{old.name} somewhere safe (or delete it, if it is a copy you no longer need) "
            "and start again."
        )
    # Nothing of ours may hold the file open while it moves.
    reset_engine()
    connection = sqlite3.connect(str(path))
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    path.rename(old)
    for sidecar in ("-wal", "-shm"):
        beside = path.with_name(path.name + sidecar)
        if beside.exists():
            beside.rename(old.with_name(old.name + sidecar))
    log.warning(
        "set the 1.x database aside as %s; 2.x starts on a new, empty one (the import "
        "under Admin reads the old file)",
        old,
    )
    return old


def init_db() -> None:
    """Bring the database up to the declared schema.

    First, a restore staged from Admin → Backups is put in place, the current
    data moved aside (`services/backup.py:apply_pending`). Then a 1.x database
    is set aside (`set_aside_1x`). Then missing tables
    and columns are created -- see db/migrations.py for what that does and
    does not cover -- and the file is marked as 2.x (`SCHEMA_VERSION`).
    """
    from lembas.db.migrations import sync_schema
    from lembas.services import backup

    backup.apply_pending()
    set_aside_1x()
    engine = get_engine()
    changes = sync_schema(engine)
    with engine.begin() as connection:
        current = connection.exec_driver_sql("PRAGMA main.user_version").scalar() or 0
        if current < SCHEMA_VERSION:
            connection.exec_driver_sql(f"PRAGMA main.user_version = {SCHEMA_VERSION}")
    if changes:
        log.info("database schema updated: %s", ", ".join(changes))
    log.debug("schema ensured at %s", settings.db_path)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background work and CLI commands.

    Request handlers should use the `db` dependency in lembas.api.deps instead.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop cached engine/factory. Used by tests to rebind to a temp database."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
