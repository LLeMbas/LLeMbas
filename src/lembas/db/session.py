"""Engine, session factory and startup schema creation."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from lembas.config import settings

log = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


@event.listens_for(Engine, "connect")
def _configure_sqlite(dbapi_connection, connection_record) -> None:  # noqa: ANN001
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
    cursor.close()


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


def init_db() -> None:
    """Bring the database up to the declared schema.

    Creates missing tables and adds missing columns -- see db/migrations.py for
    what that does and does not cover. Additive changes need nothing else;
    renames, drops and retypes are still a hand job.
    """
    from lembas.db.migrations import sync_schema

    changes = sync_schema(get_engine())
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
