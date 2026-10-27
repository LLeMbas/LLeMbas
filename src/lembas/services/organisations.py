"""Organisations and invites.

There is no open sign-up. The first visitor of an empty instance sets it up
(the instance admin and the first organisation); after that an account is made
by an administrator, or from an invite into an organisation. An invite is a
single-use, expiring token shown once; only its SHA-256 is stored, and
accepting it marks it used in the same transaction that makes the account.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    DEFAULT_ORG,
    ORG_MEMBER,
    ORG_ROLES,
    ROLE_USER,
    Invite,
    Organisation,
    User,
)
from lembas.security.passwords import hash_password, validate_password

log = logging.getLogger(__name__)

INVITE_PREFIX = "lmi_"
INVITE_BYTES = 32
DEFAULT_INVITE_TTL = timedelta(days=7)


class InviteError(ValueError):
    """An invite that cannot be used: unknown, expired, used, or for someone else."""


class AccountError(ValueError):
    """Account details that cannot be accepted (name, address, password)."""


# --- Organisations ------------------------------------------------------------------
_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return _SLUG.sub("-", plain.lower()).strip("-")[:48] or "org"


def get(db: DBSession, org_id: str | None) -> Organisation | None:
    return db.get(Organisation, org_id) if org_id else None


def default(db: DBSession) -> Organisation:
    """The first organisation. `sync_schema` makes it; this is the fallback."""
    org = db.get(Organisation, DEFAULT_ORG)
    if org is None:
        org = Organisation(id=DEFAULT_ORG, name="Default", slug=DEFAULT_ORG)
        db.add(org)
        db.flush()
    return org


def create(db: DBSession, name: str, *, slug: str = "") -> Organisation:
    """A new organisation, with its tree's layout (`/home/`, `/skills/`)."""
    name = name.strip()[:120]
    if not name:
        raise ValueError("An organisation needs a name.")
    base = slugify(slug or name)
    candidate, n = base, 1
    while db.scalar(select(Organisation.id).where(Organisation.slug == candidate)):
        n += 1
        candidate = f"{base}-{n}"
    org = Organisation(name=name, slug=candidate)
    db.add(org)
    db.flush()
    from lembas.services import fs

    fs.seed_org(db, org.id)
    return org


def members(db: DBSession, org_id: str) -> list[User]:
    return list(db.scalars(select(User).where(User.org_id == org_id).order_by(User.name)))


# --- Accounts -----------------------------------------------------------------------
def create_account(
    db: DBSession,
    *,
    name: str,
    email: str,
    password: str,
    org_id: str = DEFAULT_ORG,
    org_role: str = ORG_MEMBER,
    role: str = ROLE_USER,
) -> User:
    """Make one account in an organisation, after checking what was typed.

    Raises `AccountError` with a sentence for the person. Flushes; the caller
    commits (an invite's acceptance must commit with it).
    """
    from lembas.web.i18n import t

    name = name.strip()[:120]
    email = email.strip().lower()
    if not name:
        raise AccountError(t("Please enter a name."))
    if "@" not in email or "." not in email.split("@")[-1]:
        raise AccountError(t("Please enter a valid email address."))
    if (problem := validate_password(password)) is not None:
        raise AccountError(problem)
    if db.scalar(select(User.id).where(func.lower(User.email) == email)) is not None:
        raise AccountError(t("An account with that email already exists."))
    user = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role=role,
        org_id=org_id,
        org_role=org_role if org_role in ORG_ROLES else ORG_MEMBER,
    )
    db.add(user)
    db.flush()
    from lembas.services import fs

    fs.ensure_home(db, user)
    return user


# --- Invites ------------------------------------------------------------------------
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NewInvite:
    invite: Invite
    token: str  # shown once; never stored


def create_invite(
    db: DBSession,
    org_id: str,
    *,
    created_by: User | None,
    email: str | None = None,
    org_role: str = ORG_MEMBER,
    ttl: timedelta = DEFAULT_INVITE_TTL,
) -> NewInvite:
    """An invite into `org_id`. Returns the token once; commits."""
    if db.get(Organisation, org_id) is None:
        raise InviteError("No such organisation.")
    if org_role not in ORG_ROLES:
        raise InviteError("No such role.")
    token = INVITE_PREFIX + secrets.token_urlsafe(INVITE_BYTES)
    invite = Invite(
        org_id=org_id,
        email=(email or "").strip().lower() or None,
        org_role=org_role,
        token_hash=_hash(token),
        created_by=created_by.id if created_by else None,
        expires_at=datetime.now(UTC) + ttl,
    )
    db.add(invite)
    db.commit()
    log.info("invite %s into %s made", invite.id, org_id)
    return NewInvite(invite=invite, token=token)


def _aware(moment: datetime) -> datetime:
    # SQLite drops the offset; everything stored here is UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def find_invite(db: DBSession, token: str, *, now: datetime | None = None) -> Invite:
    """The usable invite a token names, or `InviteError` saying why not."""
    invite = db.scalar(select(Invite).where(Invite.token_hash == _hash(token or "")))
    if invite is None:
        raise InviteError("This invite link is not valid.")
    if invite.used_at is not None:
        raise InviteError("This invite has already been used.")
    if _aware(invite.expires_at) <= (now or datetime.now(UTC)):
        raise InviteError("This invite has expired.")
    return invite


def accept_invite(
    db: DBSession,
    token: str,
    *,
    name: str,
    email: str,
    password: str,
    now: datetime | None = None,
) -> User:
    """Make the account an invite promises, and spend the invite. Commits.

    The invite is marked used by a conditional UPDATE in the same transaction
    as the account: of two acceptances racing on one link, the second updates
    nothing and is refused, and its account is rolled back.
    """
    now = now or datetime.now(UTC)
    invite = find_invite(db, token, now=now)
    if invite.email and invite.email != email.strip().lower():
        raise InviteError("This invite is for another email address.")
    try:
        user = create_account(
            db,
            name=name,
            email=email,
            password=password,
            org_id=invite.org_id,
            org_role=invite.org_role,
        )
        spent = db.execute(
            update(Invite)
            .where(Invite.id == invite.id, Invite.used_at.is_(None))
            .values(used_at=now, used_by=user.id)
        ).rowcount
        if spent != 1:
            raise InviteError("This invite has already been used.")
        db.commit()
    except Exception:
        db.rollback()
        raise
    log.info("invite %s accepted by %s", invite.id, user.email)
    return user


def revoke_invite(db: DBSession, invite_id: str) -> bool:
    invite = db.get(Invite, invite_id)
    if invite is None or invite.used_at is not None:
        return False
    db.delete(invite)
    db.commit()
    return True


def pending_invites(db: DBSession, org_id: str) -> list[Invite]:
    return list(
        db.scalars(
            select(Invite)
            .where(Invite.org_id == org_id, Invite.used_at.is_(None))
            .order_by(Invite.created_at.desc())
        )
    )


__all__ = [
    "AccountError",
    "InviteError",
    "NewInvite",
    "accept_invite",
    "create",
    "create_account",
    "create_invite",
    "default",
    "find_invite",
    "get",
    "members",
    "pending_invites",
    "revoke_invite",
    "slugify",
]
