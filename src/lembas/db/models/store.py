"""The index of the skills and notes store, and its queue.

The store itself is files in a git repository (`services/skillstore/repo.py`);
these tables are what makes it searchable and what holds a change that waits.
`store_skills` and `store_notes` are **rebuilt from the files** and hold
nothing the files do not (except how often a skill was read, which is the
instance's own). `store_proposals` is the queue: a change an agent asked for
under the *approve* policy, kept whole until a person says yes or no.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from lembas.db.types import JSONDict, JSONList


class _Indexed:
    """What both kinds of indexed file carry: the text, and its last change."""

    title: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    sections_json: Mapped[list] = mapped_column(JSONList, default=list, nullable=False)
    # The last change, as its commit says it (provenance; the whole history is git's).
    commit_sha: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    changed_by: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    changed_account_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    changed_harness: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    changed_model: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    changed_model_said: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    changed_origin: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    # The text's vector by the instance's embedder, when it has one.
    # Fields the file's header names twice (a file made elsewhere): read by the
    # first, and said on its page.
    header_twice: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_key: Mapped[str] = mapped_column(String(400), default="", nullable=False)


class StoreSkill(UUIDPrimaryKey, Timestamps, _Indexed, Base):
    __tablename__ = "store_skills"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    subcategory: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class StoreNote(UUIDPrimaryKey, Timestamps, _Indexed, Base):
    __tablename__ = "store_notes"

    # The note's name: its path below `notes/` without `.md`.
    ident: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    subjects_json: Mapped[list] = mapped_column(JSONList, default=list, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="note", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    # The data group it was written under; a plain id (see `InDataGroup`).
    data_group_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    shared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    updated: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    author: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    author_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    # Which of the account's tokens wrote it (a digest of the token's id, or
    # "web"): what "its own note" is decided by. The harness is only a label.
    author_key: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    harness: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # The model's name is the caller's own claim, not the server's knowledge.
    model_said: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(120), default="", nullable=False)


class StoreReader(Base):
    """That an account read a skill on a day: a read counts once per account
    and day, so nobody reads a skill of theirs into everybody's main skills."""

    __tablename__ = "store_readers"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), primary_key=True)


class StoreRead(Base):
    """How often a skill was read on one day: what "most read lately" counts."""

    __tablename__ = "store_reads"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


PROPOSAL_PENDING = "pending"
PROPOSAL_APPROVED = "approved"
PROPOSAL_REJECTED = "rejected"
PROPOSAL_STALE = "stale"


class StoreProposal(UUIDPrimaryKey, Base):
    """One change that waits for a person: the file as it would be, and who asked."""

    __tablename__ = "store_proposals"

    target: Mapped[str] = mapped_column(String(8), nullable=False)  # skill | note
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    section: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    # The whole file after the change; NULL removes it.
    new_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A section's new text on its own, so an approval can make the change
    # again on a file that moved on meanwhile.
    section_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The header fields the change sets (None: removes), kept so they are set
    # again with the section; and the section's text as it was when the change
    # was asked for, by its hash: the section is found by what it said, not by
    # where it stood.
    fields_json: Mapped[dict | None] = mapped_column(JSONDict, nullable=True)
    section_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    # The file's hash when the change was asked for ('' : it was not there).
    base_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    data_group_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    account_id: Mapped[str] = mapped_column(String(32), default="", nullable=False, index=True)
    account_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    harness: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    model_said: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    session: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    origin: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    status: Mapped[str] = mapped_column(String(12), default=PROPOSAL_PENDING, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    decided_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    word: Mapped[str] = mapped_column(Text, default="", nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(64), default="", nullable=False)


Index("ix_store_proposals_status", StoreProposal.status, StoreProposal.created_at)
Index("ix_store_notes_group", StoreNote.data_group_id, StoreNote.status)
