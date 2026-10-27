"""The file tree: one per organisation, kept in the database.

A node is a directory, a file or a mount (a directory that is a repository on a
forge). `path` is denormalised -- `'/a/b/c'`, the root itself is implicit and
has no row -- so a lookup by path is one indexed query and a subtree is one
`LIKE`. A rename or a move rewrites the subtree's paths in one transaction
(`services/fs.py`).

A file's bytes are not here: `blob_hash` names a blob in `lembas-blobs.db`
(`services/blobs.py`), and every write appends a `NodeVersion` saying which
chat and which message made it, which is what "undo the agent's changes in
this session" reads.

Who may do what to a node is `security/access.py`'s alone: the owner of a node
or of any directory above it, a share on it or above it, and in a chat that
chat's grants and its data group's floor.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey, utcnow
from lembas.db.types import JSONDict

KIND_DIR = "dir"
KIND_FILE = "file"
KIND_MOUNT = "mount"
NODE_KINDS = (KIND_DIR, KIND_FILE, KIND_MOUNT)

# Access levels, in order. `security/access.py` compares them by position.
LEVEL_NONE = "none"
LEVEL_RO = "ro"
LEVEL_STAGE = "stage"
LEVEL_COMMIT = "commit"
LEVELS = (LEVEL_NONE, LEVEL_RO, LEVEL_STAGE, LEVEL_COMMIT)

GRANTEE_USER = "user"
GRANTEE_GROUP = "group"
# Everybody in the organisation: "share this directory with the organisation".
GRANTEE_ORG = "org"
GRANTEE_KINDS = (GRANTEE_USER, GRANTEE_GROUP, GRANTEE_ORG)


class Node(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "nodes"

    org_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # One path segment: no '/', never '.' or '..', NFC, case-preserving.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # NULL is the organisation's own: `/`'s top directories such as `/home` and
    # `/skills`, managed by the organisation's owners and admins.
    owner_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    blob_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    mime: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # kind == mount: forge, repo, branch (later).
    mount_json: Mapped[dict[str, Any] | None] = mapped_column(JSONDict, nullable=True)
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def __repr__(self) -> str:
        return f"<Node {self.kind} {self.path}>"


# A path is unique among the live nodes of an organisation; a trashed node keeps
# its old path (for restore) without blocking a new one in its place.
Index(
    "uq_nodes_org_path_live",
    Node.org_id,
    Node.path,
    unique=True,
    sqlite_where=text("trashed_at IS NULL"),
)
Index("ix_nodes_org_path", Node.org_id, Node.path)


class NodeVersion(UUIDPrimaryKey, Base):
    """One write of one file: the content it left, who, and from which chat."""

    __tablename__ = "node_versions"

    node_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    blob_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    by_user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Plain ids, not foreign keys: a version outlives the chat that wrote it.
    chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class NodeShare(UUIDPrimaryKey, Base):
    """A node shared with a user, a group or the whole organisation.

    Covers everything below the node. The highest level among the shares that
    reach a person on the nearest shared node is theirs (`security/access.py`).
    """

    __tablename__ = "shares"

    node_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grantee_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # Points at users, groups or organisations depending on grantee_kind.
    grantee_id: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


Index("ix_node_shares_grantee", NodeShare.grantee_kind, NodeShare.grantee_id)


class SessionGrant(Base):
    """A chat's own grant on a path, once it has been customised.

    While `Chat.grants_customised` is false a chat's grants are its data group's
    preset rules and these rows are not read.
    """

    __tablename__ = "session_grants"

    chat_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True
    )
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
