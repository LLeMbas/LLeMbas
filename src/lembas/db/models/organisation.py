"""Organisations, and the invitations that bring people into one.

Every account belongs to exactly one organisation. The organisation owns the
file tree (`nodes`), its groups and its data groups; connections and models stay
the instance's, and an organisation sees every enabled model unless its admins
narrow the list (`org_models`).

The first organisation has the fixed id ``"default"``, the way the first data
group does: it is made when the schema is (``migrations.ensure_default_org``),
and every account or group created without saying otherwise lands in it, so
code that predates organisations keeps working unchanged.

Open sign-up is gone. An account is made by an administrator, or from an invite:
a single-use, expiring token shown once, of which only the hash is stored.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, new_id

DEFAULT_ORG = "default"
DEFAULT_ORG_NAME = "Default"

ORG_OWNER = "owner"
ORG_ADMIN = "admin"
ORG_MEMBER = "member"
ORG_ROLES = (ORG_OWNER, ORG_ADMIN, ORG_MEMBER)
# The roles that manage an organisation: its directories, groups and invites.
ORG_MANAGERS = (ORG_OWNER, ORG_ADMIN)


class Organisation(Timestamps, Base):
    __tablename__ = "organisations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # A blob in `lembas-blobs.db`; counted in the blob's refcount.
    logo_blob_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<Organisation {self.slug}>"


class Invite(Timestamps, Base):
    """One invitation into an organisation.

    `token_hash` is SHA-256 of the token the link carries; the token itself is
    returned once by `services/organisations.create_invite` and never stored.
    `email` set means only that address may accept it. `used_at` is set in the
    same transaction that creates the account, by a conditional UPDATE, so two
    people racing on one link cannot both get in.
    """

    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("organisations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    org_role: Mapped[str] = mapped_column(String(16), default=ORG_MEMBER, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Who accepted it, for the organisation's list. Plain id, validated on read.
    used_by: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def __repr__(self) -> str:
        return f"<Invite {self.id} org={self.org_id}>"


# The models an organisation may use. Empty for an organisation means all the
# instance's enabled models; a row narrows it. Keyed by `Model.model_id` with the
# connection beside it rather than `Model.id`, because "Test & refresh" recreates
# the row (the same reason `Chat.model_id` is a text id).
org_models = Table(
    "org_models",
    Base.metadata,
    Column(
        "org_id",
        String(32),
        ForeignKey("organisations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("model_id", String(300), primary_key=True),
    Column("connection_id", String(32), primary_key=True, default=""),
)
