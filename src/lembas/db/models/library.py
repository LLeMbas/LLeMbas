"""What the model can reach for: knowledge, notes, memory and skills.

Four stores rather than one, because they differ in the two ways that matter --
who writes a record, and how a record reaches the model:

* **Document** is uploaded by a person and searched by the model. It is the
  only one holding a file, and it is deliberately shaped like ``Attachment``:
  both come out of ``services.files.prepare`` and carry the same processed
  content.
* **Note** is written by the model and edited by a person. Long enough that it
  has to be searched rather than injected.
* **Memory** is one short fact, and *is* injected -- every one of them, every
  turn, up to a budget. Anything that would not survive that treatment belongs
  in a note.
* **Skill** is a named instruction document. Its description is injected so the
  model knows the skill exists; the body is fetched only when it decides to use
  it, which is what keeps a hundred skills affordable.

Everything except Memory can be shared -- see ``Share`` below and
``services.sharing``. Memory cannot: a record about a person is not content to
hand round, and "share my memories with the team" is a question nobody asked.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.models.data_group import InDataGroup

# Who wrote a record. Not decoration: a skill the model wrote itself is the one
# worth looking at twice when its behaviour changes unexpectedly.
AUTHOR_USER = "user"
AUTHOR_MODEL = "model"

# Where a document came from.
SOURCE_UPLOAD = "upload"
SOURCE_LINK = "link"

# Resource kinds that can be shared. Values are stored, so they are part of the
# schema rather than an implementation detail.
RESOURCE_BASE = "base"
RESOURCE_NOTE = "note"
RESOURCE_SKILL = "skill"
# A report is shareable and a memory is not, and the line between them is the
# one already drawn elsewhere: a finished piece of work is exactly the thing
# somebody wants to hand over, and a record *about a person* is not content to
# pass round. The constant lives here beside the other three even though Report
# is not a library model, because `Share.resource_type` is one column and its
# vocabulary belongs in one place.
RESOURCE_REPORT = "report"

PRINCIPAL_USER = "user"
PRINCIPAL_GROUP = "group"

# Which knowledge bases a chat draws on. A chat with none searches everything
# its owner can see; a chat with some is scoped to those, which is the point --
# "answer from the contract folder" is a different question from "answer from
# everything I have ever uploaded".
chat_knowledge_bases = Table(
    "chat_knowledge_bases",
    Base.metadata,
    Column("chat_id", String(32), ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "base_id",
        String(32),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class KnowledgeBase(UUIDPrimaryKey, Timestamps, InDataGroup, Base):
    """A named collection of documents.

    Sharing lives here rather than on the individual document: "this folder is
    the team's" is the granularity people actually think in, and per-document
    grants would mean answering "who can see this?" by checking every file.
    A document is visible to whoever can see the base it is in.
    """

    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    documents: Mapped[list[Document]] = relationship(
        back_populates="base", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<KnowledgeBase {self.name!r}>"


class Document(UUIDPrimaryKey, Timestamps, Base):
    """One item in a knowledge library: a file, an image or a saved web page.

    The content columns mirror ``Attachment`` exactly because both are produced
    by ``services.files.prepare`` -- images downscaled, PDF text extracted once,
    type decided by sniffing bytes. Keeping the shapes identical is what lets a
    document be attached to a message by copying rather than converting.
    """

    __tablename__ = "documents"

    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nullable only so the column could be added to an existing table. The
    # service always sets it, and a startup sweep files anything that predates
    # bases into its owner's default -- see documents.sweep_unfiled.
    base_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    source: Mapped[str] = mapped_column(String(16), default=SOURCE_UPLOAD, nullable=False)
    # Set for a saved web page, so it can be re-fetched and cited.
    source_url: Mapped[str] = mapped_column(Text, default="")

    # --- The same content columns as Attachment ---
    filename: Mapped[str] = mapped_column(String(300), default="")
    stored_name: Mapped[str] = mapped_column(String(120), default="")
    media_type: Mapped[str] = mapped_column(String(100), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="text", nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    pages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extraction_error: Mapped[str] = mapped_column(Text, default="")

    base: Mapped[KnowledgeBase] = relationship(back_populates="documents")

    @property
    def is_image(self) -> bool:
        return self.kind == "image"

    @property
    def human_size(self) -> str:
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB"):
            if size < 1024 or unit == "MB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} MB"

    def __repr__(self) -> str:
        return f"<Document {self.title!r}>"


class Note(UUIDPrimaryKey, Timestamps, InDataGroup, Base):
    """Something the model wrote down, or a person did.

    Longer and more specific than a memory. Not injected: a handful of notes
    would fill a context window on their own, so the model searches for the one
    it needs.
    """

    __tablename__ = "notes"

    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(16), default=AUTHOR_USER, nullable=False)

    def __repr__(self) -> str:
        return f"<Note {self.title!r}>"


class Memory(UUIDPrimaryKey, Timestamps, InDataGroup, Base):
    """One short fact, in front of the model on every turn.

    Deliberately not shareable and deliberately small. The length cap is
    enforced in the service rather than by the column, so an over-long write
    from a tool is trimmed with an explanation instead of failing the turn.
    """

    __tablename__ = "memories"

    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(16), default=AUTHOR_MODEL, nullable=False)

    def __repr__(self) -> str:
        return f"<Memory {self.content[:40]!r}>"


class Skill(UUIDPrimaryKey, Timestamps, InDataGroup, Base):
    """A named set of instructions the model can choose to follow.

    `description` is the load-bearing field: it is what gets injected, and it is
    the only thing the model has to decide whether the skill is relevant. The
    body is fetched with a tool.
    """

    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Slug, referenced by the model when it asks for the body.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    author: Mapped[str] = mapped_column(String(16), default=AUTHOR_USER, nullable=False)

    revisions: Mapped[list[SkillRevision]] = relationship(
        back_populates="skill",
        cascade="all, delete-orphan",
        order_by="SkillRevision.created_at.desc()",
    )

    def __repr__(self) -> str:
        return f"<Skill {self.name}>"


class SkillRevision(UUIDPrimaryKey, Timestamps, Base):
    """The state of a skill before a change.

    A model may rewrite its own skills, so every write snapshots what was there
    first. That is the whole safety story for self-modification: not a gate, but
    a record and a way back.
    """

    __tablename__ = "skill_revisions"

    skill_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    # Who made the change this revision is the "before" of.
    author: Mapped[str] = mapped_column(String(16), default=AUTHOR_USER, nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="")

    skill: Mapped[Skill] = relationship(back_populates="revisions")


class Share(UUIDPrimaryKey, Timestamps, Base):
    """One grant of access to one resource.

    A single table across documents, notes and skills rather than three
    association tables, because the rule is identical in all three cases and
    ``services.sharing`` is the only thing that reads it.

    A grant, never a denial -- the same principle as group permissions. Somebody
    who cannot see a resource simply has no row here.
    """

    # `library_shares` since 2.0: `shares` is the file tree's (`NodeShare`).
    # This one goes with the library stores themselves when the file tools
    # replace them.
    __tablename__ = "library_shares"
    __table_args__ = (
        UniqueConstraint(
            "resource_type", "resource_id", "principal_type", "principal_id"
        ),
    )

    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(32), nullable=False)

    principal_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # No foreign key: this column points at users or groups depending on
    # principal_type, and SQLite cannot express that. services.sharing deletes
    # dangling rows when a user or group goes.
    principal_id: Mapped[str] = mapped_column(String(32), nullable=False)

    def __repr__(self) -> str:
        return f"<Share {self.resource_type}:{self.resource_id} -> {self.principal_type}>"


Index("ix_shares_resource", Share.resource_type, Share.resource_id)
Index("ix_shares_principal", Share.principal_type, Share.principal_id)


# --- Semantic index -----------------------------------------------------------
# What a chunk belongs to. Strings rather than a foreign key per store, because
# one table serving four of them is what stops the chunking, the scoring and the
# rebuild being written four times and drifting three ways.
CHUNK_DOCUMENT = "document"
CHUNK_NOTE = "note"
CHUNK_SKILL = "skill"
CHUNK_REPORT = "report"

CHUNK_KINDS = (CHUNK_DOCUMENT, CHUNK_NOTE, CHUNK_SKILL, CHUNK_REPORT)


class Chunk(UUIDPrimaryKey, Timestamps, Base):
    """A piece of one library record, and its embedding.

    **Additive, so `sync_schema` creates it at startup with no manual step**, and
    absent-means-nothing: an instance with no embedding model chosen never writes
    a row here and the search behaves exactly as it always did.

    `owner_id` is denormalised off the resource. It is not used for
    authorisation -- `services/sharing.py` is still the only definition of who
    may see what, and scoring happens before that filter exactly as the
    full-text path does -- but it is what makes "rebuild this person's index"
    and "drop everything of theirs" one indexed query rather than four joins.

    No foreign key on `resource_id`, for the reason `Share.principal_id` has
    none: the column points at one of four tables depending on `resource_type`,
    which SQLite cannot express. `indexing.forget_resource` deletes the rows.
    """

    __tablename__ = "chunks"

    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # Where in the record this piece came from, so a set can be rebuilt in order
    # and a hit can say which part matched.
    ordinal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="")

    # float32, little-endian, packed. A BLOB rather than JSON because a 1024
    # dimension vector is 4KB packed and about 20KB as text, and every one of
    # them is read on every semantic search.
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # How many floats are in it. Stored rather than derived from the length so a
    # mismatch is a comparison this code refuses rather than one it gets wrong:
    # changing the embedding model changes the space, and vectors from two
    # spaces score against each other perfectly happily and mean nothing.
    dims: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Which model wrote it, for the same reason. A rebuild is what reconciles
    # them; until then the odd ones out are ignored rather than trusted.
    model_id: Mapped[str] = mapped_column(String(300), default="")
    # A hash of the text this set was built from. What makes re-indexing an
    # unchanged record free, and what makes "is this index current?" answerable
    # without re-embedding anything.
    source_hash: Mapped[str] = mapped_column(String(64), default="")

    def __repr__(self) -> str:
        return f"<Chunk {self.resource_type}:{self.resource_id}#{self.ordinal}>"


Index("ix_chunks_resource", Chunk.resource_type, Chunk.resource_id)
