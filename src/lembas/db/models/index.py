"""The search index: content indexed once per hash, and what is where now.

**Content is indexed by hash, not by path.** `index_text` has one row per
content hash -- a file's blob hash, or a chat message's text hash -- and
`index_chunk` the pieces it was cut into, with their float32 vectors. A file
in many places, a copy, a rename, a move or a file unchanged across commits is
the same hash and is cut and embedded once.

**`path_hash` is what is where now**: one row per indexed file path (or chat
message) naming the hash it holds. A move rewrites rows here and touches no
chunk; a hash no row names any more is collected with its chunks.

Two more tables are not models, because SQLAlchemy cannot describe them: the
FTS5 index `index_fts` over `index_chunk.text` (external content, kept by
triggers) and the sqlite-vec table `index_vec` of one-bit vectors, its width
the embedder's. Both are made by `services/index/schema.py`.

Who may see a hit is not here: search scores across the organisation first and
asks `security/access.py` (files) or the chat's owner (chats) afterwards.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base

# What a mapping row points at.
INDEXED_FILE = "file"
INDEXED_CHAT = "chat"
INDEXED_KINDS = (INDEXED_FILE, INDEXED_CHAT)

# Where a hash is in the pipeline.
INDEX_PENDING = "pending"  # waiting to be cut (and embedded)
INDEX_DONE = "done"
INDEX_SKIPPED = "skipped"  # not text, too large, empty: nothing to index
INDEX_ERROR = "error"  # tried and failed; `error` says why, retried on rebuild
INDEX_STATES = (INDEX_PENDING, INDEX_DONE, INDEX_SKIPPED, INDEX_ERROR)


class IndexText(Base):
    """One content hash, and how far indexing it has got."""

    __tablename__ = "index_text"

    hash: Mapped[str] = mapped_column(String(80), primary_key=True)
    status: Mapped[str] = mapped_column(String(8), default=INDEX_PENDING, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    chunked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The embedder that made the vectors ('' when none did): a change of model
    # sends the hash back to pending, because two models' vectors do not mix.
    model: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    dims: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


Index("ix_index_text_status", IndexText.status)


class IndexChunk(Base):
    """One piece of one hash's text, and its vector when an embedder made one."""

    __tablename__ = "index_chunk"

    # INTEGER PRIMARY KEY: the rowid, which `index_fts` and `index_vec` use.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hash: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Where the piece is in the extracted text, 1-based and inclusive, so a hit
    # can say `file.md:40-72`.
    line_start: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    line_end: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # float32 little-endian, unit length; NULL when no embedder made one.
    vector: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)


class PathHash(Base):
    """What is where now: a file path (or a chat message) and the hash it holds."""

    __tablename__ = "path_hash"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), default=INDEXED_FILE, nullable=False)
    # A file: its node. A chat message: the chat.
    node_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # A file: its path. A chat message: '' (a chat has no place in the tree).
    path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    hash: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    # A chat message: its id, and the chat's owner (who may find it).
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


Index("ix_path_hash_org_kind_path", PathHash.org_id, PathHash.kind, PathHash.path)
Index("ix_path_hash_node", PathHash.node_id)
Index("ix_path_hash_message", PathHash.message_id)


__all__ = [
    "INDEX_DONE",
    "INDEX_ERROR",
    "INDEX_PENDING",
    "INDEX_SKIPPED",
    "INDEX_STATES",
    "INDEXED_CHAT",
    "INDEXED_FILE",
    "INDEXED_KINDS",
    "IndexChunk",
    "IndexText",
    "PathHash",
]
