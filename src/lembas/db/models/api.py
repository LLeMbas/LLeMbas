"""API tokens, device logins and the audit log.

**Two doors, no crossing.** A browser is signed in by the session cookie and
reaches the UI's routes; a program holds an `ApiToken` and reaches `/v1`,
`/api/v1` and the device routes, by `Authorization: Bearer` and nothing else. A
token is never accepted on a UI route and the cookie is never accepted on an API
one, so a token pasted into a page cannot bypass SameSite, and a page cannot spend
a token.

A token is stored as its SHA-256, exactly as a session is: the raw value is shown
once, at creation, and a dump of this table cannot be replayed. What is kept in
the clear is a short prefix, so a person can tell their tokens apart.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.types import JSONDict, JSONList

# What a token was made for. A personal token is made by its holder in
# Settings; a device token by approving a device login, and it names the machine.
TOKEN_PERSONAL = "personal"
TOKEN_DEVICE = "device"
TOKEN_KINDS = (TOKEN_PERSONAL, TOKEN_DEVICE)

# Who did something, in the audit log.
ACTOR_USER = "user"
ACTOR_TOKEN = "token"
ACTOR_SYSTEM = "system"
ACTOR_ANONYMOUS = "anonymous"


class ApiToken(UUIDPrimaryKey, Timestamps, Base):
    """A bearer token a program uses as its holder.

    Its scopes are re-checked against the holder's *current* permissions on
    every request (`security/api_tokens.py`), so demoting a person narrows every
    token they hold at once, with nothing to revoke.
    """

    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=TOKEN_PERSONAL)
    # The first characters of the raw token, for telling them apart.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes_json: Mapped[list[str]] = mapped_column(JSONList, default=list)

    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str] = mapped_column(String(45), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # A device token's machine, as the device described itself when it asked
    # and the person saw it when they approved. Empty on a personal token.
    device_host: Mapped[str] = mapped_column(String(200), default="")
    device_platform: Mapped[str] = mapped_column(String(64), default="")
    # The program and its version: "lembas 1.0.0".
    client: Mapped[str] = mapped_column(String(64), default="")

    # What this token spent this month, for `GET /v1/usage`'s `device`
    # share: the account's month is one row in `usage`, which cannot say which
    # of its tokens spent what. Reset when the month (`usage_period`) moves on.
    usage_period: Mapped[str] = mapped_column(String(7), default="")
    usage_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    usage_completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<ApiToken {self.kind} {self.prefix}… {self.name!r}>"


class DeviceCode(UUIDPrimaryKey, Timestamps, Base):
    """A device login in progress (RFC 8628).

    The device holds `device_code` and polls with it; the person types
    `user_code` into the instance, sees what is asking, and approves or denies.
    The token is created when the device next polls after approval, and its raw
    value goes to the device once -- this row never holds it.
    """

    __tablename__ = "device_codes"

    device_code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Eight letters from an alphabet without look-alikes, shown as ABCD-EFGH.
    user_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    client: Mapped[str] = mapped_column(String(64), default="")
    device_name: Mapped[str] = mapped_column(String(120), default="")
    device_host: Mapped[str] = mapped_column(String(200), default="")
    device_platform: Mapped[str] = mapped_column(String(64), default="")
    scopes_json: Mapped[list[str]] = mapped_column(JSONList, default=list)
    # Where the request for a code came from, shown on the approval page so a
    # code typed in from somebody else's message stands out.
    ip: Mapped[str] = mapped_column(String(45), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    approved_by: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE")
    )
    denied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Set once the token has been handed over, so a code is good for one token.
    token_id: Mapped[str | None] = mapped_column(String(32))


class AuditEvent(UUIDPrimaryKey, Base):
    """One thing that happened that somebody may later need to account for.

    No foreign keys on purpose: an audit row outlives the account, the token and
    the record it names, or it would vanish exactly when it is wanted.
    """

    __tablename__ = "audit_events"

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(String(32))
    actor_token_id: Mapped[str | None] = mapped_column(String(32))
    # user | token | system | anonymous
    actor_kind: Mapped[str] = mapped_column(String(16), default=ACTOR_SYSTEM, nullable=False)
    # Kept beside the id so a deleted account still reads as somebody.
    actor_label: Mapped[str] = mapped_column(String(200), default="")
    # Dotted and stable, for filtering: "auth.login", "token.create", "admin.user.delete".
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), default="")
    target_id: Mapped[str] = mapped_column(String(64), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    ip: Mapped[str] = mapped_column(String(45), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")


Index("ix_audit_events_at", AuditEvent.at)
Index("ix_audit_events_action", AuditEvent.action)
Index("ix_audit_events_actor", AuditEvent.actor_user_id)


__all__ = [
    "ACTOR_ANONYMOUS",
    "ACTOR_SYSTEM",
    "ACTOR_TOKEN",
    "ACTOR_USER",
    "TOKEN_DEVICE",
    "TOKEN_KINDS",
    "TOKEN_PERSONAL",
    "ApiToken",
    "AuditEvent",
    "DeviceCode",
]


class DeviceSessionDelete(UUIDPrimaryKey, Timestamps, Base):
    """A device chat deleted here whose session the device has not yet deleted.

    Deleting a device chat asks the device to delete its session too
    (`_lembas/session/delete`), but only a device linked *at that moment* can be
    asked. An in-memory tombstone alone is not enough: a chat deleted while the
    device was off, or while it refused, would leave the session on the device
    for good, and after a restart of this instance nothing would even remember
    it. This row is
    that request, kept until the device has answered it: written in the same
    transaction that deletes the chat, sent again each time the device links,
    and gone once the device says it is done (or after `MAX_DELETE_ATTEMPTS`
    refusals -- `services/device_link.py`).

    No foreign key to `api_tokens` on purpose: it names a device the way a
    tombstone does, and a revoked token's rows are dropped with it rather than
    by a cascade nobody would see.
    """

    __tablename__ = "device_session_deletes"
    __table_args__ = (
        Index("ix_device_session_deletes_pair", "token_id", "session_id", unique=True),
    )

    token_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # Answers that were not "done": a refusal, a timeout, a dropped link.
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # What the device said the last time it did not delete it, for the log
    # and for whoever looks at this table.
    last_error: Mapped[str] = mapped_column(Text, default="")
