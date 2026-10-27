"""Keeping the index current: what is where, and the text of each hash.

Two halves, and only the second is slow.

**Mapping** (`reconcile_org`, `sync_messages`) is synchronous and cheap: it
reads the tree (or a chat's messages) and makes `path_hash` say what is where
now -- adding rows for new files, rewriting a moved file's path, dropping rows
for trashed or ignored files -- and gives every hash it has not seen an
`index_text` row at `pending`. It is the whole of a rename, a move or a copy:
the content is already indexed under its hash. `collect` then drops hashes no
row names any more, with their chunks.

**Indexing** (`index_hash`) is the slow half: read the content, extract the
text, cut it into pieces by tokens, embed them (when the instance has an
embedder and sqlite-vec), write the chunks. One hash at a time, its own short
sessions, and the HTTP request to the embedder made while no session is open.
The chunk set of a hash is replaced whole or not at all, so a failure leaves
the old pieces (or none) rather than half of them.

**What is indexed.** Live files whose path no `.lembasignore` leaves out
(`ignore.py`), up to `extract.MAX_BYTES`; text, Markdown, code, JSON, PDF and
DOCX by their bytes (`extract.py`). Chat messages from people and from the
model, complete and not empty, in chats that are not temporary. A chat
message's hash is `chat:` + the SHA-256 of its text, so it never shares an
entry with a file's blob hash (whose text depends on how the bytes extract).

**Never charged.** The embedder is the instance maintaining its own search,
not a person asking a model for something -- the same rule as 1.x's library.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session as DBSession

from lembas.db import vec
from lembas.db.models import (
    INDEX_DONE,
    INDEX_ERROR,
    INDEX_PENDING,
    INDEX_SKIPPED,
    INDEXED_CHAT,
    INDEXED_FILE,
    KIND_FILE,
    ROLE_ASSISTANT,
    ROLE_USER,
    Chat,
    Connection,
    IndexChunk,
    IndexText,
    Message,
    Model,
    Node,
    PathHash,
    User,
)
from lembas.db.session import session_scope
from lembas.services import blobs, settings_store
from lembas.services.index import chunks, extract, ignore, schema
from lembas.services.llm.openai_client import Endpoint, LLMError

log = logging.getLogger(__name__)

CHAT_PREFIX = "chat:"
CHAT_ROLES = (ROLE_USER, ROLE_ASSISTANT)


# --- The embedder ---------------------------------------------------------------
@dataclass(frozen=True)
class Embedder:
    """The instance's embedding model, resolved while a session is open."""

    endpoint: Endpoint
    model_id: str
    batch: int = 16


def embedder(db: DBSession) -> Embedder | None:
    """The one embedder the tree is indexed with, or None.

    One per instance (the plan's decision): the instance's embedding model from
    Admin -> Extraction. None for every "no" -- none chosen, the model gone or
    disabled, or **sqlite-vec missing**, because vectors that cannot be searched
    are not worth an HTTP request each. None means keywords only, never an
    error.
    """
    if not vec.available():
        return None
    values = settings_store.extraction(db)
    wanted = str(values.get("embedding_model_id") or "").strip()
    if not wanted:
        return None
    model = db.scalar(
        select(Model)
        .join(Connection)
        .where(
            Model.model_id == wanted,
            Model.enabled.is_(True),
            Connection.enabled.is_(True),
            Connection.kind != "anthropic",  # no embeddings there
        )
        .order_by(Connection.position)
    )
    if model is None:
        return None
    connection = db.get(Connection, model.connection_id)
    if connection is None:
        return None
    return Embedder(
        endpoint=Endpoint.from_connection(connection),
        model_id=model.model_id,
        batch=int(values.get("embed_batch") or 16),
    )


# Which embedders answered `/tokenize`: (base url, model) -> bool.
_TOKENIZES: dict[tuple[str, str], bool] = {}


async def tokenizer_for(worker: Embedder | None, body: str) -> chunks.Tokenizer:
    """The embedder's own tokenizer for `body` where its server has one, else the estimate.

    llama.cpp's `/tokenize` with `with_pieces` gives each token's text, which
    `chunks.spans_from_pieces` turns into spans. A server that answers anything
    else is remembered as not having one, and is not asked again.
    """
    if worker is None:
        return chunks.estimate
    key = (worker.endpoint.base_url, worker.model_id)
    if _TOKENIZES.get(key) is False:
        return chunks.estimate
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                worker.endpoint.root_url("/tokenize"),
                headers=worker.endpoint.headers(),
                json={"content": body, "with_pieces": True, "model": worker.model_id},
            )
            response.raise_for_status()
            payload = response.json()
        pieces = [
            item["piece"] if isinstance(item.get("piece"), str) else ""
            for item in payload.get("tokens", [])
            if isinstance(item, dict)
        ]
        spans = chunks.spans_from_pieces(body, pieces) if pieces else None
    except (httpx.HTTPError, ValueError, AttributeError, TypeError):
        spans = None
    if spans is None:
        _TOKENIZES[key] = False
        return chunks.estimate
    _TOKENIZES[key] = True
    return lambda text: spans if text == body else chunks.estimate(text)


async def _embed(worker: Embedder, texts: list[str]) -> list[list[float]]:
    from lembas.services.llm import embeddings

    return await embeddings.embed(worker.endpoint, worker.model_id, texts)


MAX_HALVINGS = 6


async def embed_pieces(
    worker: Embedder, pieces: list[str]
) -> list[list[tuple[str, list[float]]]]:
    """Every piece embedded, in order; a piece refused is halved, never dropped.

    Returns one group per piece: the piece and its vector, or the halves it was
    cut into and theirs. A batch that fails is tried one piece at a time; a
    single piece that fails is cut in two (`chunks.halve`) and both halves
    tried, down to `MAX_HALVINGS` levels. A piece that still fails then is a
    real failure (the embedder is down, say), and `LLMError` goes up: the hash
    is marked `error` and retried later, rather than indexed with a hole in it.
    """
    out: list[list[tuple[str, list[float]]]] = []
    size = max(1, worker.batch)
    for start in range(0, len(pieces), size):
        batch = pieces[start : start + size]
        try:
            vectors = await _embed(worker, batch)
        except LLMError:
            for piece in batch:
                out.append(await _embed_one(worker, piece, 0))
            continue
        out.extend([(piece, vector)] for piece, vector in zip(batch, vectors, strict=True))
    return out


async def _embed_one(worker: Embedder, piece: str, depth: int) -> list[tuple[str, list[float]]]:
    try:
        return [(piece, (await _embed(worker, [piece]))[0])]
    except LLMError:
        halves = chunks.halve(piece) if depth < MAX_HALVINGS else None
        if halves is None:
            raise
    left, right = halves
    return [
        *(await _embed_one(worker, left, depth + 1)),
        *(await _embed_one(worker, right, depth + 1)),
    ]


def pack(vector: list[float]) -> bytes:
    import struct

    return struct.pack(f"<{len(vector)}f", *vector)


# --- Mapping: files ---------------------------------------------------------------
def _ensure_pending(db: DBSession, hashes: set[str]) -> int:
    if not hashes:
        return 0
    known: set[str] = set()
    listed = list(hashes)
    for start in range(0, len(listed), 500):
        part = listed[start : start + 500]
        known.update(db.scalars(select(IndexText.hash).where(IndexText.hash.in_(part))))
    fresh = hashes - known
    for digest in fresh:
        db.add(IndexText(hash=digest, status=INDEX_PENDING))
    db.flush()
    return len(fresh)


def _ignore_matcher(db: DBSession, org_id: str) -> ignore.Matcher:
    rows = db.execute(
        select(Node.path, Node.blob_hash).where(
            Node.org_id == org_id,
            Node.kind == KIND_FILE,
            Node.name == ignore.NAME,
            Node.trashed_at.is_(None),
        )
    ).all()
    files = []
    for path, digest in rows:
        if not digest:
            continue
        try:
            data = blobs.read_bytes(db, digest)
        except blobs.BlobMissing:
            continue
        files.append((path, data.decode("utf-8", "replace")))
    return ignore.Matcher.build(files)


@dataclass
class Reconciled:
    added: int = 0
    moved: int = 0
    removed: int = 0
    pending: int = 0


def reconcile_org(db: DBSession, org_id: str) -> Reconciled:
    """Make `path_hash` say what files are where in one organisation now.

    Reads every live file of the organisation (path, hash, size) and the rows
    there are, and changes only the difference. A file too large to index is
    left out here, before its hash ever reaches the queue. Flushes; the caller
    commits.
    """
    matcher = _ignore_matcher(db, org_id)
    wanted: dict[str, tuple[str, str]] = {}  # node id -> (path, hash)
    for node_id, path, digest, size in db.execute(
        select(Node.id, Node.path, Node.blob_hash, Node.size).where(
            Node.org_id == org_id, Node.kind == KIND_FILE, Node.trashed_at.is_(None)
        )
    ):
        if not digest or (size or 0) > extract.MAX_BYTES or matcher.ignored(path):
            continue
        wanted[node_id] = (path, digest)

    have = {
        row.node_id: row
        for row in db.scalars(
            select(PathHash).where(PathHash.org_id == org_id, PathHash.kind == INDEXED_FILE)
        )
    }
    done = Reconciled()
    for node_id, row in have.items():
        if node_id not in wanted:
            db.delete(row)
            done.removed += 1
            continue
        path, digest = wanted[node_id]
        if row.path != path or row.hash != digest:
            done.moved += row.path != path
            row.path, row.hash = path, digest
    for node_id, (path, digest) in wanted.items():
        if node_id not in have:
            db.add(
                PathHash(
                    org_id=org_id, kind=INDEXED_FILE, node_id=node_id, path=path, hash=digest
                )
            )
            done.added += 1
    db.flush()
    done.pending = _ensure_pending(db, {digest for _, digest in wanted.values()})
    return done


def reconcile_all(db: DBSession) -> int:
    """Every organisation, then every chat message not yet mapped. Returns pending added."""
    from lembas.db.models import Organisation

    pending = 0
    for org_id in list(db.scalars(select(Organisation.id))):
        pending += reconcile_org(db, org_id).pending
    pending += sync_unmapped_messages(db)
    db.execute(
        delete(PathHash).where(
            PathHash.kind == INDEXED_CHAT,
            PathHash.message_id.not_in(select(Message.id)),
        )
    )
    return pending


# --- Mapping: chats ---------------------------------------------------------------
def chat_hash(content: str) -> str:
    return CHAT_PREFIX + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _message_wanted(message: Message, chat: Chat | None) -> bool:
    return (
        chat is not None
        and not chat.temporary
        and message.role in CHAT_ROLES
        and bool(message.complete)
        and bool((message.content or "").strip())
    )


def sync_messages(db: DBSession, message_ids: list[str]) -> int:
    """Map these messages (or drop their rows when gone or not indexable)."""
    if not message_ids:
        return 0
    rows = {
        row.message_id: row
        for row in db.scalars(select(PathHash).where(PathHash.message_id.in_(message_ids)))
    }
    fresh: set[str] = set()
    chats: dict[str, Chat | None] = {}
    owners: dict[str, User | None] = {}
    for message_id in message_ids:
        message = db.get(Message, message_id)
        row = rows.get(message_id)
        chat = None
        if message is not None:
            if message.chat_id not in chats:
                chats[message.chat_id] = db.get(Chat, message.chat_id)
            chat = chats[message.chat_id]
        if message is None or not _message_wanted(message, chat):
            if row is not None:
                db.delete(row)
            continue
        if chat.user_id not in owners:
            owners[chat.user_id] = db.get(User, chat.user_id)
        owner = owners[chat.user_id]
        if owner is None:
            continue
        digest = chat_hash(message.content)
        if row is None:
            db.add(
                PathHash(
                    org_id=owner.org_id,
                    kind=INDEXED_CHAT,
                    node_id=chat.id,
                    path="",
                    hash=digest,
                    message_id=message.id,
                    user_id=chat.user_id,
                )
            )
        else:
            row.hash, row.org_id, row.user_id = digest, owner.org_id, chat.user_id
        fresh.add(digest)
    db.flush()
    return _ensure_pending(db, fresh)


def sync_unmapped_messages(db: DBSession, *, batch: int = 500) -> int:
    """Messages with no row yet: the catch-up after a start, a restore or a crash."""
    pending = 0
    mapped = select(PathHash.message_id).where(PathHash.message_id.is_not(None))
    while True:
        ids = list(
            db.scalars(
                select(Message.id)
                .join(Chat, Chat.id == Message.chat_id)
                .where(
                    Message.id.not_in(mapped),
                    Message.role.in_(CHAT_ROLES),
                    Message.complete.is_(True),
                    Message.content != "",
                    Chat.temporary.is_(False),
                )
                .limit(batch)
            )
        )
        if not ids:
            return pending
        before = pending
        pending += sync_messages(db, ids)
        still = db.scalar(
            select(Message.id).where(Message.id.in_(ids), Message.id.not_in(mapped)).limit(1)
        )
        if still is not None and pending == before:
            # Messages that map to nothing (an owner gone): stop, rather than loop.
            return pending


# --- Collecting -------------------------------------------------------------------
def collect(db: DBSession) -> int:
    """Drop hashes no row names any more, with their chunks and vectors."""
    gone = list(
        db.scalars(
            select(IndexText.hash).where(IndexText.hash.not_in(select(PathHash.hash)))
        )
    )
    for digest in gone:
        _drop_chunks(db, digest)
        db.execute(delete(IndexText).where(IndexText.hash == digest))
    db.flush()
    return len(gone)


def _drop_chunks(db: DBSession, digest: str) -> None:
    ids = list(db.scalars(select(IndexChunk.id).where(IndexChunk.hash == digest)))
    if not ids:
        return
    schema.drop_vec_rows(db, ids)
    db.execute(delete(IndexChunk).where(IndexChunk.hash == digest))


# --- Indexing one hash ------------------------------------------------------------
def _source_text(db: DBSession, digest: str) -> extract.Extracted:
    if digest.startswith(CHAT_PREFIX):
        message_id = db.scalar(
            select(PathHash.message_id).where(PathHash.hash == digest).limit(1)
        )
        message = db.get(Message, message_id) if message_id else None
        if message is None or chat_hash(message.content or "") != digest:
            return extract.Extracted(skip="message gone or changed")
        return extract.Extracted(text=message.content)
    try:
        if blobs.size(db, digest) > extract.MAX_BYTES:
            return extract.Extracted(skip="too large")
        data = blobs.read_bytes(db, digest)
    except blobs.BlobMissing:
        return extract.Extracted(skip="content gone")
    return extract.extract(data)


def _finish(db: DBSession, digest: str, status: str, *, error: str = "", model: str = "",
            dims: int = 0) -> None:
    db.execute(
        update(IndexText)
        .where(IndexText.hash == digest)
        .values(status=status, error=error[:1000], chunked_at=datetime.now(UTC), model=model,
                dims=dims)
    )


async def index_hash(digest: str) -> str:
    """Cut (and embed) one hash's text. Returns the status it ended in.

    Never raises for one bad file: a failure is the hash's `error` status and
    the queue moves on.
    """
    try:
        with session_scope() as db:
            row = db.get(IndexText, digest)
            if row is None:
                return ""
            source = _source_text(db, digest)
            worker = embedder(db)
        if source.skip or source.error:
            with session_scope() as db:
                _drop_chunks(db, digest)
                _finish(db, digest, INDEX_ERROR if source.error else INDEX_SKIPPED,
                        error=source.error or source.skip)
            return INDEX_ERROR if source.error else INDEX_SKIPPED

        body = chunks.normalise(source.text)
        tokenize = await tokenizer_for(worker, body.strip()) if worker else chunks.estimate
        pieces = chunks.split(body, tokenize)
        if not pieces:
            with session_scope() as db:
                _drop_chunks(db, digest)
                _finish(db, digest, INDEX_SKIPPED, error="empty")
            return INDEX_SKIPPED

        groups: list[list[tuple[str, list[float]]]] | None = None
        if worker is not None:
            groups = await embed_pieces(worker, [p.text for p in pieces])

        rows: list[tuple[str, int, int, int, bytes | None]] = []
        for n, piece in enumerate(pieces):
            if groups is None:
                parts: list[tuple[str, list[float] | None]] = [(piece.text, None)]
            else:
                parts = list(groups[n])
            for part, vector in parts:
                # A half keeps its place: its lines are found inside its piece.
                at = body.find(part, piece.start, piece.end) if len(parts) > 1 else piece.start
                at = piece.start if at < 0 else at
                first, last = chunks.line_range(body, at, at + len(part))
                tokens = piece.tokens if len(parts) == 1 else chunks.count_estimate(part)
                rows.append((part, tokens, first, last, pack(vector) if vector else None))

        with session_scope() as db:
            if db.get(IndexText, digest) is None:
                return ""  # collected while we worked
            _drop_chunks(db, digest)
            dims, has_vec = 0, False
            if groups:
                dims = len(groups[0][0][1])
                has_vec = schema.ensure_vec(db, dims)
            for ordinal, (piece_text, tokens, first, last, vector) in enumerate(rows):
                chunk = IndexChunk(
                    hash=digest,
                    ordinal=ordinal,
                    text=piece_text,
                    tokens=tokens,
                    line_start=first,
                    line_end=last,
                    vector=vector,
                )
                db.add(chunk)
                db.flush()
                if vector is not None and has_vec:
                    db.execute(
                        text(f"INSERT INTO {schema.VEC}(rowid, embedding) "
                             "VALUES (:id, vec_quantize_binary(:v))"),
                        {"id": chunk.id, "v": vector},
                    )
            _finish(db, digest, INDEX_DONE, model=worker.model_id if groups else "", dims=dims)
        return INDEX_DONE
    except LLMError as exc:
        log.info("index: could not embed %s: %s", digest[:16], exc)
        with session_scope() as db:
            _finish(db, digest, INDEX_ERROR, error=str(exc))
        return INDEX_ERROR
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # one bad file must not stop the queue
        log.exception("index: indexing %s failed", digest[:16])
        with session_scope() as db:
            _finish(db, digest, INDEX_ERROR, error=str(exc))
        return INDEX_ERROR


def requeue_stale(db: DBSession) -> int:
    """Send back to pending what the current embedder did not make.

    A new model, a model removed, sqlite-vec installed or gone: hashes indexed
    under another answer are cut again (and embedded), one at a time, while
    the old pieces stay searchable by keyword.
    """
    worker = embedder(db)
    model = worker.model_id if worker else ""
    result = db.execute(
        update(IndexText)
        .where(IndexText.status == INDEX_DONE, IndexText.model != model)
        .values(status=INDEX_PENDING)
    )
    return int(result.rowcount or 0)


def retry_errors(db: DBSession) -> int:
    result = db.execute(
        update(IndexText).where(IndexText.status == INDEX_ERROR).values(status=INDEX_PENDING)
    )
    return int(result.rowcount or 0)


def next_pending(db: DBSession, limit: int = 50) -> list[str]:
    return list(
        db.scalars(select(IndexText.hash).where(IndexText.status == INDEX_PENDING).limit(limit))
    )


__all__ = [
    "CHAT_PREFIX",
    "Embedder",
    "Reconciled",
    "chat_hash",
    "collect",
    "embed_pieces",
    "embedder",
    "index_hash",
    "next_pending",
    "reconcile_all",
    "reconcile_org",
    "requeue_stale",
    "retry_errors",
    "sync_messages",
    "sync_unmapped_messages",
    "tokenizer_for",
]
