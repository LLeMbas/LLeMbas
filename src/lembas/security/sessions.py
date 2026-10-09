"""Login session lifecycle.

The browser holds an opaque random token in an httpOnly cookie. The database
stores only its SHA-256, so a dump of the sessions table cannot be replayed as
a live login. Tokens are compared by hash lookup, and revoking is a DELETE.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.config import settings
from lembas.db.models import Session as SessionRow
from lembas.db.models import User

COOKIE_NAME = "lembas_session"
TOKEN_BYTES = 32


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    db: DBSession,
    user: User,
    *,
    user_agent: str = "",
    ip_address: str = "",
) -> str:
    """Open a session for a user and return the raw token for the cookie.

    The raw token is returned exactly once and never persisted.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    row = SessionRow(
        user_id=user.id,
        token_hash=_hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_ttl),
        user_agent=user_agent[:500],
        ip_address=ip_address[:45],
    )
    db.add(row)
    user.last_login_at = datetime.now(UTC)
    db.commit()
    return token


def resolve_session(db: DBSession, token: str | None) -> User | None:
    """Return the signed-in user for a cookie value, or None.

    Expired and orphaned sessions are cleaned up as they are encountered, which
    keeps the table tidy without needing a scheduled job.
    """
    if not token:
        return None

    row = db.scalar(select(SessionRow).where(SessionRow.token_hash == _hash_token(token)))
    if row is None:
        return None

    # SQLite hands back naive datetimes even for timezone-aware columns.
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if expires_at < datetime.now(UTC):
        db.delete(row)
        db.commit()
        return None

    user = db.get(User, row.user_id)
    if user is None or not user.active:
        db.delete(row)
        db.commit()
        return None

    return user


def revoke_session(db: DBSession, token: str | None) -> None:
    if not token:
        return
    row = db.scalar(select(SessionRow).where(SessionRow.token_hash == _hash_token(token)))
    if row is not None:
        db.delete(row)
        db.commit()


def revoke_all_for_user(db: DBSession, user: User) -> None:
    """Sign a user out everywhere. Used when deactivating or changing a password."""
    for row in db.scalars(select(SessionRow).where(SessionRow.user_id == user.id)):
        db.delete(row)
    db.commit()
