"""The blob store: file contents by SHA-256, in `lembas-blobs.db`.

The only writer of `blobs` and `blob_parts` (`db/models/blob.py`). A blob is
written once and never changed; the same bytes written twice are one blob.
Contents are split into parts of `PART_SIZE`, and both writing and reading go
part by part, so nothing here ever holds more than one part of a large file.

**Reference counting.** `refcount` counts the rows that name a blob: a node's
current content, each of its versions, an organisation's logo, a file a device
showed in a chat. `incref` and `decref` are called by whoever writes those
rows, in the same transaction. A
blob at zero is not deleted at once: `sweep` removes only those older than
`SWEEP_GRACE`, so a blob put a moment ago whose node row has not been written
yet is never collected under it. A `put` of bytes that already exist at zero
refreshes `created_at` for the same reason.

All functions take the caller's session and never commit: a blob and the node
that names it go in one transaction.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.orm import Session as DBSession

from lembas.db.models.blob import blob_parts, blobs

PART_SIZE = 4 * 1024 * 1024
SWEEP_GRACE = timedelta(hours=1)
EMPTY_HASH = hashlib.sha256(b"").hexdigest()


class BlobMissing(LookupError):
    """A hash that names no blob."""


def exists(db: DBSession, digest: str) -> bool:
    return db.execute(select(blobs.c.hash).where(blobs.c.hash == digest)).first() is not None


def size(db: DBSession, digest: str) -> int:
    row = db.execute(select(blobs.c.size).where(blobs.c.hash == digest)).first()
    if row is None:
        raise BlobMissing(digest)
    return int(row[0])


def refcount(db: DBSession, digest: str) -> int:
    row = db.execute(select(blobs.c.refcount).where(blobs.c.hash == digest)).first()
    if row is None:
        raise BlobMissing(digest)
    return int(row[0])


# --- Writing ------------------------------------------------------------------------
def put_bytes(db: DBSession, data: bytes) -> str:
    """Store `data`; returns its hash. Refcount unchanged (see `incref`)."""
    return put_stream(db, [data])[0]


def put_stream(db: DBSession, chunks: Iterable[bytes]) -> tuple[str, int]:
    """Store what `chunks` yields, re-cut into parts; returns (hash, size).

    The hash is known only at the end, so the parts are written under a
    temporary key and renamed -- or dropped, when the same content is already
    stored.
    """
    digest = hashlib.sha256()
    temporary = f"tmp-{uuid.uuid4().hex}"
    buffer = bytearray()
    seq = 0
    total = 0

    def flush(part: bytes) -> None:
        nonlocal seq
        db.execute(insert(blob_parts).values(hash=temporary, seq=seq, data=part))
        seq += 1

    for chunk in chunks:
        if not chunk:
            continue
        digest.update(chunk)
        total += len(chunk)
        buffer += chunk
        while len(buffer) >= PART_SIZE:
            flush(bytes(buffer[:PART_SIZE]))
            del buffer[:PART_SIZE]
    if buffer:
        flush(bytes(buffer))

    final = digest.hexdigest()
    now = datetime.now(UTC)
    if exists(db, final):
        db.execute(delete(blob_parts).where(blob_parts.c.hash == temporary))
        # Unreferenced and about to be named again: not collectable just yet.
        db.execute(
            update(blobs)
            .where(blobs.c.hash == final, blobs.c.refcount <= 0)
            .values(created_at=now)
        )
        return final, total
    db.execute(update(blob_parts).where(blob_parts.c.hash == temporary).values(hash=final))
    db.execute(
        insert(blobs).values(hash=final, size=total, parts=seq, refcount=0, created_at=now)
    )
    return final, total


def incref(db: DBSession, digest: str, by: int = 1) -> None:
    done = db.execute(
        update(blobs).where(blobs.c.hash == digest).values(refcount=blobs.c.refcount + by)
    ).rowcount
    if not done:
        raise BlobMissing(digest)


def decref(db: DBSession, digest: str | None, by: int = 1) -> None:
    """One reference fewer. A missing blob or a None is ignored: dropping a
    reference to something already gone leaves nothing to do."""
    if not digest:
        return
    db.execute(
        update(blobs)
        .where(blobs.c.hash == digest)
        .values(refcount=func.max(blobs.c.refcount - by, 0))
    )


# --- Reading ------------------------------------------------------------------------
def iter_parts(db: DBSession, digest: str) -> Iterator[bytes]:
    """The content, one part at a time."""
    row = db.execute(select(blobs.c.parts).where(blobs.c.hash == digest)).first()
    if row is None:
        raise BlobMissing(digest)
    for seq in range(int(row[0])):
        part = db.execute(
            select(blob_parts.c.data).where(blob_parts.c.hash == digest, blob_parts.c.seq == seq)
        ).first()
        if part is None:
            raise BlobMissing(f"{digest} part {seq}")
        yield bytes(part[0])


def read_bytes(db: DBSession, digest: str) -> bytes:
    """The whole content. For what is known to be small; stream the rest."""
    return b"".join(iter_parts(db, digest))


# --- Collecting ---------------------------------------------------------------------
def sweep(db: DBSession, *, now: datetime | None = None, grace: timedelta = SWEEP_GRACE) -> int:
    """Delete unreferenced blobs older than `grace`. Returns how many went."""
    cutoff = (now or datetime.now(UTC)) - grace
    doomed = [
        row[0]
        for row in db.execute(
            select(blobs.c.hash).where(blobs.c.refcount <= 0, blobs.c.created_at < cutoff)
        )
    ]
    for digest in doomed:
        db.execute(delete(blob_parts).where(blob_parts.c.hash == digest))
        db.execute(delete(blobs).where(blobs.c.hash == digest, blobs.c.refcount <= 0))
    # Parts left under a temporary key by a write that died half way.
    db.execute(
        delete(blob_parts).where(
            blob_parts.c.hash.like("tmp-%"),
            blob_parts.c.hash.not_in(select(blobs.c.hash)),
        )
    )
    return len(doomed)


def recount(db: DBSession) -> int:
    """Recompute every refcount from the rows that name blobs. Returns how
    many changed. A repair, and what the tests check the counting against."""
    counted: dict[str, int] = {}
    for statement in (
        "SELECT blob_hash FROM nodes WHERE blob_hash IS NOT NULL",
        "SELECT blob_hash FROM node_versions WHERE blob_hash IS NOT NULL",
        "SELECT logo_blob_hash FROM organisations WHERE logo_blob_hash IS NOT NULL",
        "SELECT blob_hash FROM shown_files",
    ):
        for (digest,) in db.execute(text(statement)):
            counted[digest] = counted.get(digest, 0) + 1
    changed = 0
    for digest, current in list(db.execute(select(blobs.c.hash, blobs.c.refcount))):
        want = counted.get(digest, 0)
        if want != current:
            db.execute(update(blobs).where(blobs.c.hash == digest).values(refcount=want))
            changed += 1
    return changed


__all__ = [
    "EMPTY_HASH",
    "PART_SIZE",
    "SWEEP_GRACE",
    "BlobMissing",
    "decref",
    "exists",
    "incref",
    "iter_parts",
    "put_bytes",
    "put_stream",
    "read_bytes",
    "recount",
    "refcount",
    "size",
    "sweep",
]
