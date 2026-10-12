"""Keeping the semantic index current, and rebuilding it when it is not.

## The shape, and why it is a background task

Embedding is an HTTP request. Every writer in the library -- `documents.create`,
`notes.edit`, `skills.save`, `reports.create` -- is synchronous and is called
from a route or a tool runner that has just committed a row, and none of them
should wait on a model server to answer before saying "saved".

So indexing is **fired and forgotten**: `schedule(kind, id)` starts a task and
returns immediately. A save that cannot be indexed is a save; the row is written
either way and the search falls back to keywords for that record until the next
rebuild. That is the whole degradation story, and it is the same one that covers
having no embedding model at all.

## Nothing is written when no model is chosen

`embedding_model_id` empty means the FTS path exactly as it has always been --
no chunk rows, no requests, no cost. That is what makes this safe to add to an
instance that never asked for it, and it is asserted rather than assumed.

## Staleness is a hash, not a timestamp

Every chunk carries `source_hash` (of the text it was built from), `model_id`
and `dims`. Re-indexing an unchanged record is free; a record whose text moved
is rebuilt; a record embedded by a *different* model is rebuilt on the next pass
and, until then, ignored by the scorer rather than trusted. Vectors from two
spaces score against each other perfectly happily and mean nothing, which is a
search that works and is wrong -- the worst failure this feature can have.

## Indexing is never charged

`/v1/embeddings` is charged to whoever calls it, like a reply; the index this
module keeps is not, and that is deliberate. An embedding here is the instance
maintaining its own search -- fired by a save, re-run by a rebuild an
administrator starts, repeated whenever the embedding model changes -- not
something the person asked a model for. Charging it would bill somebody for
the administrator's choice of model, and twice over for a rebuild they never
saw; and a save cannot be refused for want of credits, so a charge here could
only overdraw. The cost of keeping the index is the instance's.

## The rebuild is restartable and reports itself

A half-finished index has to be usable rather than empty, so the rebuild walks
records one at a time and commits each. `progress()` is what the admin page
polls; it is in-process, because a rebuild does not survive a restart and
pretending otherwise would mean a progress bar that never moves.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    CHUNK_DOCUMENT,
    CHUNK_KINDS,
    CHUNK_NOTE,
    CHUNK_REPORT,
    CHUNK_SKILL,
    Chunk,
    Connection,
    Document,
    KnowledgeBase,
    Model,
    Note,
    Report,
    Skill,
)
from lembas.db.session import session_scope
from lembas.services import settings_store
from lembas.services.library import chunks as chunk_service
from lembas.services.llm.openai_client import Endpoint, LLMError
from lembas.web.i18n import t

log = logging.getLogger(__name__)

# What each kind is, and how to get its text. One table rather than four
# branches, for the reason `tool_labels` is one table: four copies of "which
# columns make up the searchable text" is three chances to disagree.
SOURCES: dict[str, tuple[type, tuple[str, ...]]] = {
    CHUNK_DOCUMENT: (Document, ("title", "description", "extracted_text")),
    CHUNK_NOTE: (Note, ("title", "body")),
    CHUNK_SKILL: (Skill, ("name", "description", "body")),
    CHUNK_REPORT: (Report, ("title", "summary", "body")),
}

# Tasks in flight, so a record saved twice in quick succession is indexed once
# more rather than twice at the same time. Keyed on kind and id.
_TASKS: dict[tuple[str, str], asyncio.Task] = {}


# --- What the model is ----------------------------------------------------------
@dataclass(frozen=True)
class Embedder:
    """Which model turns text into vectors, resolved while a session is open."""

    endpoint: Endpoint
    model_id: str
    batch: int = 16


def embedder(db: DBSession, group: str | None = None) -> Embedder | None:
    """The embedding model for one data group, or the instance's, or None.

    None is the answer to every "no" -- none chosen, the model row deleted, its
    connection disabled -- and every caller reads it the same way: do nothing,
    and let the keyword search stand. That is deliberately not an error. An
    instance that never configured this is the common case, not a broken one.

    **A group may name its own.** The embedder is sent the full text of every
    record it indexes, so a group that keeps its data away from a provider has
    to be able to keep it away from that provider's embedder too. A group that
    names none uses the instance's, which is what every group starts with --
    and the Data groups page says so, per group, beside the providers.

    Mixing is impossible by construction rather than by care: a `Chunk` carries
    the model that made its vector and `retrieval.semantic_ids` skips any other,
    so a query embedded by one group's model never meets another's vectors.
    """
    values = settings_store.extraction(db)
    batch = int(values.get("embed_batch") or 16)
    if group:
        from lembas.services import data_groups

        row = data_groups.get(db, group)
        if row is not None and row.embedding_model_id:
            return _resolve(db, row.embedding_model_id, row.embedding_connection_id, batch)
    wanted = str(values.get("embedding_model_id") or "").strip()
    if not wanted:
        return None
    return _resolve(db, wanted, "", batch)


def _resolve(db: DBSession, wanted: str, connection_id: str, batch: int) -> Embedder | None:
    query = (
        select(Model)
        .join(Connection)
        .where(
            Model.model_id == wanted,
            Model.enabled.is_(True),
            Connection.enabled.is_(True),
            # Anthropic's API has no embeddings.
            Connection.kind != "anthropic",
        )
        .order_by(Connection.id != (connection_id or ""), Connection.position)
    )
    model = db.scalar(query)
    if model is None:
        log.info("embedding model %r is configured but not available", wanted)
        return None
    connection = db.get(Connection, model.connection_id)
    if connection is None:
        return None
    return Embedder(
        endpoint=Endpoint.from_connection(connection),
        model_id=model.model_id,
        batch=batch,
    )


def group_of_row(db: DBSession, row) -> str:
    """The data group a record is in. A document is in its base's."""
    from lembas.services import data_groups

    if isinstance(row, Document):
        base = db.get(KnowledgeBase, row.base_id) if row.base_id else None
        return data_groups.group_of(base) if base is not None else data_groups.DEFAULT_GROUP
    return data_groups.group_of(row)


def any_configured(db: DBSession) -> bool:
    """Whether any group -- or the instance -- has an embedder to index with."""
    from lembas.services import data_groups

    if embedder(db) is not None:
        return True
    return any(
        embedder(db, group.id) is not None
        for group in data_groups.all_groups(db)
        if group.embedding_model_id
    )


def enabled(db: DBSession) -> bool:
    return embedder(db) is not None


# --- Reading a record -----------------------------------------------------------
def text_of(row) -> str:
    """The searchable text of one record, in the same order the FTS index uses.

    Blank fields are dropped rather than joined as empty lines, so a note with
    no body hashes the same before and after somebody clears its body twice.
    """
    kind = kind_of(row)
    if kind is None:
        return ""
    _, columns = SOURCES[kind]
    parts = [str(getattr(row, name, "") or "").strip() for name in columns]
    return "\n\n".join(part for part in parts if part)


def kind_of(row) -> str | None:
    for kind, (model, _) in SOURCES.items():
        if isinstance(row, model):
            return kind
    return None


def owner_of(row) -> str:
    return str(getattr(row, "owner_id", "") or "")


# --- Writing the index ----------------------------------------------------------
def forget_resource(db: DBSession, kind: str, resource_id: str) -> int:
    """Drop every chunk of one record. Called when it is deleted.

    A plain DELETE rather than a cascade, because `resource_id` has no foreign
    key -- it points at one of four tables depending on `resource_type`, which
    SQLite cannot express. Same reasoning as `Share.principal_id`.
    """
    result = db.execute(
        delete(Chunk).where(Chunk.resource_type == kind, Chunk.resource_id == resource_id)
    )
    db.commit()
    return int(result.rowcount or 0)


def current_hash(db: DBSession, kind: str, resource_id: str) -> tuple[str, str]:
    """The hash and model of the chunks already stored for a record."""
    row = db.execute(
        select(Chunk.source_hash, Chunk.model_id)
        .where(Chunk.resource_type == kind, Chunk.resource_id == resource_id)
        .limit(1)
    ).first()
    return (str(row[0] or ""), str(row[1] or "")) if row else ("", "")


async def index_resource(kind: str, resource_id: str, *, force: bool = False) -> int:
    """Rebuild one record's chunks. Returns how many were written.

    Opens its own session, for the reason every background worker here does: it
    outlives the request that scheduled it. Never raises -- a failure leaves the
    old chunks in place, which is a slightly stale index rather than a hole, and
    is strictly better than deleting first and failing to write.
    """
    if kind not in SOURCES:
        return 0
    try:
        with session_scope() as db:
            model, _ = SOURCES[kind]
            row = db.get(model, resource_id)
            if row is None:
                forget_resource(db, kind, resource_id)
                return 0
            worker = embedder(db, group_of_row(db, row))
            if worker is None:
                return 0
            body = text_of(row)
            owner = owner_of(row)
            values = settings_store.extraction(db)
            digest = chunk_service.digest(body)
            stored_hash, stored_model = current_hash(db, kind, resource_id)

        if not body.strip():
            with session_scope() as db:
                forget_resource(db, kind, resource_id)
            return 0
        if not force and digest == stored_hash and stored_model == worker.model_id:
            return 0

        pieces = chunk_service.split(
            body, size=int(values["chunk_chars"]), overlap=int(values["chunk_overlap"])
        )
        if not pieces:
            with session_scope() as db:
                forget_resource(db, kind, resource_id)
            return 0

        vectors = await _embed_all(worker, pieces)

        # Written only once every vector is in hand. Deleting first and failing
        # half way through would leave a record indexed by half of itself, which
        # ranks worse than not being indexed at all and looks like nothing.
        with session_scope() as db:
            db.execute(
                delete(Chunk).where(
                    Chunk.resource_type == kind, Chunk.resource_id == resource_id
                )
            )
            for ordinal, (piece, vector) in enumerate(zip(pieces, vectors, strict=True)):
                db.add(
                    Chunk(
                        owner_id=owner,
                        resource_type=kind,
                        resource_id=resource_id,
                        ordinal=ordinal,
                        text=piece,
                        vector=chunk_service.pack(vector),
                        dims=len(vector),
                        model_id=worker.model_id,
                        source_hash=digest,
                    )
                )
            db.commit()
        return len(pieces)
    except LLMError as exc:
        log.info("could not index %s %s: %s", kind, resource_id, exc)
        return 0
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - one bad record must not stop a rebuild
        log.exception("indexing %s %s failed", kind, resource_id)
        return 0


async def _embed_all(worker: Embedder, pieces: list[str]) -> list[list[float]]:
    from lembas.services.llm import embeddings as embeddings_service

    vectors: list[list[float]] = []
    for start in range(0, len(pieces), worker.batch):
        batch = pieces[start : start + worker.batch]
        vectors.extend(await embeddings_service.embed(worker.endpoint, worker.model_id, batch))
    return vectors


# --- Scheduling -----------------------------------------------------------------
def schedule(kind: str, resource_id: str) -> None:
    """Index a record soon, without making its writer wait.

    Called from synchronous writers that have just committed. Two things it is
    careful about:

    - **No running loop means do nothing.** A CLI command, a test, or the
      startup sweep has no event loop to attach to, and building a coroutine
      there produces "never awaited" at the caller's own line. The check is
      before the coroutine, the same trap `push.announce_later` documents.
    - **A record already being indexed is left alone.** Saving twice in a second
      would otherwise embed the same text twice at once; the second call is
      dropped and the record is picked up by the *next* save or rebuild, which
      is why `index_resource` re-reads the row rather than taking text passed in.
    """
    if kind not in SOURCES or not resource_id:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    key = (kind, resource_id)
    existing = _TASKS.get(key)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(index_resource(kind, resource_id))
    _TASKS[key] = task
    task.add_done_callback(lambda _t, k=key: _TASKS.pop(k, None))


# --- Noticing a change ----------------------------------------------------------
# Two SQLAlchemy session events rather than a call in each of the ten writers
# that touch these four tables. That is a departure from this codebase's taste
# for explicit seams, and the reason is the one `tool_label` gives for being a
# Jinja global: a step every writer has to remember is a step one of them will
# forget, and here forgetting is *silent* -- the record saves, the keyword search
# still finds it, and only its semantic recall is quietly stale.
#
# `after_flush` collects and `after_commit` acts, in that order and never
# merged. Inside a flush the transaction has not landed yet, so a task started
# there could read the row before it exists; and `session.deleted` is empty by
# the time the commit fires, so the collecting has to happen while it is not.
_PENDING = "lembas_index_pending"


def _collect(session, _flush_context) -> None:
    seen: set[tuple[str, str]] = session.info.setdefault(_PENDING, set())
    for row in (*session.new, *session.dirty, *session.deleted):
        kind = kind_of(row)
        if kind is None:
            continue
        resource_id = str(getattr(row, "id", "") or "")
        if resource_id:
            seen.add((kind, resource_id))


def _fire(session) -> None:
    # A deletion is scheduled exactly like a change: `index_resource` finds no
    # row and drops the chunks. One path rather than two, and the one that runs
    # is the one that has to be right anyway.
    for kind, resource_id in session.info.pop(_PENDING, set()):
        schedule(kind, resource_id)


def _forget(session) -> None:
    session.info.pop(_PENDING, None)


def install() -> None:
    """Listen for library records changing. Called once, from the app factory.

    Idempotent: `event.contains` is checked, because the app factory is called
    per test in the suite and registering the same listener a hundred times
    would index every record a hundred times over.
    """
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    for name, handler in (
        ("after_flush", _collect),
        ("after_commit", _fire),
        ("after_rollback", _forget),
    ):
        if not event.contains(Session, name, handler):
            event.listen(Session, name, handler)


def sweep_orphans(db: DBSession) -> int:
    """Drop chunks whose record has gone.

    A backstop for the one case the listeners cannot cover: a delete that
    happened with no event loop running -- a CLI command, a test, a cascade from
    deleting a user -- where `schedule` had nowhere to put its task. Cheap
    enough to run at startup and at the end of every rebuild: one NOT IN per
    kind, against an indexed column.
    """
    removed = 0
    for kind, (model, _) in SOURCES.items():
        result = db.execute(
            delete(Chunk).where(
                Chunk.resource_type == kind,
                Chunk.resource_id.not_in(select(model.id)),
            )
        )
        removed += int(result.rowcount or 0)
    if removed:
        db.commit()
        log.info("dropped %d orphaned chunk(s)", removed)
    return removed


# --- Rebuilding everything ------------------------------------------------------
@dataclass
class Progress:
    """What a rebuild has done so far.

    In-process, because a rebuild does not survive a restart. Persisting it
    would mean a progress bar that stops moving and never finishes, which is
    worse than one that admits it is gone.
    """

    running: bool = False
    total: int = 0
    done: int = 0
    written: int = 0
    error: str = ""
    kinds: dict[str, int] = field(default_factory=dict)

    @property
    def percent(self) -> int:
        return int(self.done * 100 / self.total) if self.total else 0


_PROGRESS = Progress()
_REBUILD: asyncio.Task | None = None


def progress() -> Progress:
    return _PROGRESS


def counts(db: DBSession) -> dict[str, int]:
    """How many chunks exist per kind. What the page shows when nothing is running."""
    rows = db.execute(
        select(Chunk.resource_type, func.count()).group_by(Chunk.resource_type)
    ).all()
    return {str(kind): int(count) for kind, count in rows}


async def rebuild_all(*, force: bool = True) -> None:
    """Walk every record and index it, committing as it goes.

    One at a time and never gathered. The far side is usually one local model
    server, and twenty concurrent embedding requests against it is slower than
    twenty sequential ones as well as being ruder.
    """
    global _PROGRESS
    _PROGRESS = Progress(running=True)
    try:
        with session_scope() as db:
            if not any_configured(db):
                _PROGRESS.error = t("No embedding model is configured.")
                return
            work: list[tuple[str, str]] = []
            for kind, (model, _) in SOURCES.items():
                ids = [row[0] for row in db.execute(select(model.id)).all()]
                work.extend((kind, str(row_id)) for row_id in ids)
        _PROGRESS.total = len(work)

        for kind, resource_id in work:
            written = await index_resource(kind, resource_id, force=force)
            _PROGRESS.done += 1
            _PROGRESS.written += written
            _PROGRESS.kinds[kind] = _PROGRESS.kinds.get(kind, 0) + written

        # After the walk, not before: a record deleted while this was running
        # would otherwise be swept and then re-indexed from a row that no longer
        # exists. `index_resource` handles that case too, and doing it in this
        # order means one pass reconciles both directions.
        with session_scope() as db:
            sweep_orphans(db)
    except asyncio.CancelledError:
        _PROGRESS.error = "Stopped."
        raise
    except Exception as exc:  # noqa: BLE001 - a rebuild failing must be reportable
        log.exception("rebuilding the index failed")
        _PROGRESS.error = str(exc)
    finally:
        _PROGRESS.running = False


def start_rebuild(*, force: bool = True) -> bool:
    """Start a rebuild if one is not already going. True if this call started it."""
    global _REBUILD
    if _REBUILD is not None and not _REBUILD.done():
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    _REBUILD = asyncio.create_task(rebuild_all(force=force))
    return True


async def shutdown() -> None:
    """Cancel the rebuild and any in-flight indexing.

    Nothing here is lost that matters: a chunk set is either written whole or
    not at all, and the next rebuild picks up whatever was missed.
    """
    global _REBUILD
    tasks = [task for task in (_REBUILD, *_TASKS.values()) if task is not None]
    _TASKS.clear()
    _REBUILD = None
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def clear() -> None:
    """For tests: forget the in-process state without touching the database."""
    global _REBUILD, _PROGRESS
    _TASKS.clear()
    _REBUILD = None
    _PROGRESS = Progress()


__all__ = [
    "CHUNK_KINDS",
    "SOURCES",
    "Embedder",
    "Progress",
    "clear",
    "counts",
    "embedder",
    "enabled",
    "forget_resource",
    "index_resource",
    "kind_of",
    "progress",
    "rebuild_all",
    "schedule",
    "shutdown",
    "start_rebuild",
    "text_of",
]
