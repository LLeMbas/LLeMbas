"""The background half: noticing changes, and working through the queue.

## Noticing

Two SQLAlchemy session events, as 1.x's library indexer had, rather than a call
in each writer of the tree: `after_flush` collects the organisations whose
nodes changed and the chat messages that did, `after_commit` hands them over.
Collecting happens in the flush because `session.deleted` is empty by the time
the commit fires; handing over waits for the commit because the worker reads
with its own session and must not see a transaction that may still roll back.
That covers every way the tree changes -- `fs.write`, `mv` (whose subtree
rewrite is one raw UPDATE, but the moved node itself is always flushed), `rm`,
`restore`, an upload, a forge sync later -- without `services/fs.py` knowing
the index exists.

Commits happen on the event loop and on FastAPI's worker threads alike, so the
hand-over is `call_soon_threadsafe` onto the loop the worker was started on.

## The queue is the database

What is waiting is `index_text.status = 'pending'`, not an in-memory list: a
restart, a crash or a commit made with no worker running (a CLI command, a
test) loses nothing. The worker reconciles everything when it starts and every
`RECONCILE_EVERY` seconds after, which also retries hashes that failed (an
embedder that was down) and re-embeds what another model made.

## One at a time

Hashes are indexed one after another, never gathered: the far side is usually
one local model server, and twenty concurrent requests against it are slower
than twenty sequential ones as well as ruder. Between two hashes the worker
maps whatever changed meanwhile first -- mapping is milliseconds, and a rename
should not wait behind a thousand-page PDF.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from lembas.db import vec
from lembas.db.models import IndexChunk, IndexText, Message, Node
from lembas.db.session import session_scope
from lembas.services.index import pipeline

log = logging.getLogger(__name__)

DEBOUNCE = 0.5
RECONCILE_EVERY = 3600.0

_PENDING = "lembas_index_changes"


@dataclass
class State:
    loop: asyncio.AbstractEventLoop | None = None
    task: asyncio.Task | None = None
    wake: asyncio.Event | None = None
    orgs: set[str] = field(default_factory=set)
    messages: set[str] = field(default_factory=set)
    everything: bool = True
    # What the admin page shows while it runs.
    current: str = ""
    indexed: int = 0
    started_at: float = 0.0
    last_error: str = ""


_STATE = State()


# --- Noticing -------------------------------------------------------------------
def _collect(session, _flush_context) -> None:
    seen = session.info.setdefault(_PENDING, (set(), set()))
    for row in (*session.new, *session.dirty, *session.deleted):
        if isinstance(row, Node):
            org = row.__dict__.get("org_id")
            if org:
                seen[0].add(org)
            else:
                session.info["lembas_index_everything"] = True
        elif isinstance(row, Message):
            message_id = row.__dict__.get("id")
            if message_id:
                seen[1].add(message_id)


def _fire(session) -> None:
    orgs, messages = session.info.pop(_PENDING, (set(), set()))
    everything = session.info.pop("lembas_index_everything", False)
    if orgs or messages or everything:
        notify(orgs, messages, everything=everything)


def _forget(session) -> None:
    session.info.pop(_PENDING, None)
    session.info.pop("lembas_index_everything", None)


def install() -> None:
    """Listen for the tree and chats changing. Idempotent (the app factory runs per test)."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    for name, handler in (
        ("after_flush", _collect),
        ("after_commit", _fire),
        ("after_rollback", _forget),
    ):
        if not event.contains(Session, name, handler):
            event.listen(Session, name, handler)


def notify(orgs=(), messages=(), *, everything: bool = False) -> None:
    """Tell the worker what changed. Safe from any thread; a no-op when it is not running."""
    loop = _STATE.loop
    if loop is None or loop.is_closed():
        return

    def add() -> None:
        _STATE.orgs.update(orgs)
        _STATE.messages.update(messages)
        _STATE.everything = _STATE.everything or everything
        if _STATE.wake is not None:
            _STATE.wake.set()

    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        add()
    else:
        with contextlib.suppress(RuntimeError):  # the loop closed under us
            loop.call_soon_threadsafe(add)


# --- Working ----------------------------------------------------------------------
def _map_changes() -> int:
    """Apply what changed to `path_hash`. Returns how many hashes became pending."""
    orgs, messages = set(_STATE.orgs), set(_STATE.messages)
    everything = _STATE.everything
    _STATE.orgs.clear()
    _STATE.messages.clear()
    _STATE.everything = False
    pending = 0
    with session_scope() as db:
        if everything:
            pipeline.retry_errors(db)
            pipeline.requeue_stale(db)
            pending += pipeline.reconcile_all(db)
        else:
            for org_id in orgs:
                pending += pipeline.reconcile_org(db, org_id).pending
            pending += pipeline.sync_messages(db, sorted(messages))
        pipeline.collect(db)
    return pending


def _changes_waiting() -> bool:
    return bool(_STATE.orgs or _STATE.messages or _STATE.everything)


async def run_once(*, everything: bool = False) -> int:
    """Map what changed, then index every pending hash. Returns how many were indexed.

    What the worker does each time it wakes; also what a test or a CLI command
    awaits to bring the index up to date without a worker (`everything=True`:
    re-read the whole tree rather than only what it was told about).
    """
    done = 0
    _STATE.everything = _STATE.everything or everything
    _map_changes()
    while True:
        with session_scope() as db:
            batch = pipeline.next_pending(db)
        if not batch:
            break
        for digest in batch:
            if _changes_waiting():
                _map_changes()
            _STATE.current = digest
            status = await pipeline.index_hash(digest)
            _STATE.current = ""
            if status:
                done += 1
                _STATE.indexed += 1
            if status == "error":
                with session_scope() as db:
                    row = db.get(IndexText, digest)
                    _STATE.last_error = row.error if row is not None else ""
    return done


async def _run() -> None:
    assert _STATE.wake is not None
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the worker must outlive any one failure
            log.exception("index: the worker failed a pass")
            _STATE.last_error = str(exc)
        _STATE.wake.clear()
        if _changes_waiting():
            continue
        try:
            await asyncio.wait_for(_STATE.wake.wait(), timeout=RECONCILE_EVERY)
        except TimeoutError:
            _STATE.everything = True
        await asyncio.sleep(DEBOUNCE)


def start() -> bool:
    """Start the worker on the running loop. True if this call started it."""
    if _STATE.task is not None and not _STATE.task.done():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _STATE.loop = loop
    _STATE.wake = asyncio.Event()
    _STATE.everything = True
    _STATE.started_at = time.time()
    _STATE.task = loop.create_task(_run())
    return True


def rebuild() -> None:
    """Re-read everything (the admin's button): mapping, failures, other models' vectors."""
    notify(everything=True)


async def shutdown() -> None:
    """Stop the worker. A hash half done is redone: chunks are replaced whole or not at all."""
    task = _STATE.task
    _STATE.task = None
    _STATE.loop = None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def clear() -> None:
    """For tests: forget the in-process state."""
    global _STATE
    _STATE = State()


def progress() -> dict[str, Any]:
    """What the admin page shows: the queue by state, the pieces, the worker."""
    with session_scope() as db:
        states = dict(
            db.execute(select(IndexText.status, func.count()).group_by(IndexText.status)).all()
        )
        pieces = int(db.scalar(select(func.count()).select_from(IndexChunk)) or 0)
        vectors = int(
            db.scalar(
                select(func.count()).select_from(IndexChunk).where(IndexChunk.vector.is_not(None))
            )
            or 0
        )
        worker = pipeline.embedder(db)
    return {
        "running": _STATE.task is not None and not _STATE.task.done(),
        "current": _STATE.current,
        "indexed": _STATE.indexed,
        "last_error": _STATE.last_error,
        "states": {str(k): int(v) for k, v in states.items()},
        "pending": int(states.get("pending", 0)),
        "pieces": pieces,
        "vectors": vectors,
        "embedder": worker.model_id if worker else "",
        "vectors_available": vec.available(),
    }


__all__ = [
    "DEBOUNCE",
    "RECONCILE_EVERY",
    "clear",
    "install",
    "notify",
    "progress",
    "rebuild",
    "run_once",
    "shutdown",
    "start",
]
