"""Bearer tokens for programs: personal tokens and device logins.

**Two doors.** API routes take `Authorization: Bearer lmb_…` and nothing else;
`require_token` never reads the cookie, and the UI's `get_current_user` never
reads this header. See `db/models/api.py`.

**Scopes are re-checked on every request** against what the holder may do *now*:
a token made with `models` by somebody who has since lost `api.use` is refused,
with nothing to revoke. Admin scopes need the holder to still be an
administrator.

| Scope | Allows | Needs |
|---|---|---|
| `models` | `/v1`: list the holder's models, chat with them, embeddings | `api.use` |
| `library` | `/mcp`: the holder's memory, notes, skills and knowledge | `api.use` |
| `link` | being driven from this instance as a linked device | `api.use` |
| `admin:read` | users and usage, read only | administrator |
| `admin:audit` | the audit log | administrator, or `audit.read` |
| `admin:credits` | top-ups, adjustments and plans | administrator, or `credits.grant` |
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db
from lembas.db.models import TOKEN_PERSONAL, ApiToken, User

PREFIX = "lmb_"
TOKEN_BYTES = 32
SHOWN_PREFIX = 10  # "lmb_" + six characters

SCOPE_MODELS = "models"
SCOPE_LIBRARY = "library"
SCOPE_LINK = "link"
SCOPE_ADMIN_READ = "admin:read"
SCOPE_ADMIN_AUDIT = "admin:audit"
SCOPE_ADMIN_CREDITS = "admin:credits"

USER_SCOPES = (SCOPE_MODELS, SCOPE_LIBRARY, SCOPE_LINK)
ADMIN_SCOPES = (SCOPE_ADMIN_READ, SCOPE_ADMIN_AUDIT, SCOPE_ADMIN_CREDITS)
SCOPES = (*USER_SCOPES, *ADMIN_SCOPES)

def scope_label(scope: str) -> str:
    """What a scope allows, in the reader's language. A function rather than a
    table so each sentence is a literal `t()` the translation check can see."""
    from lembas.web.i18n import t

    return {
        SCOPE_MODELS: t("Use your models, and spend your usage"),
        SCOPE_LIBRARY: t("Read and write your memory, notes, skills and knowledge"),
        SCOPE_LINK: t("Accept work from this instance as a linked device"),
        SCOPE_ADMIN_READ: t("Read every account and its usage"),
        SCOPE_ADMIN_AUDIT: t("Read the audit log"),
        SCOPE_ADMIN_CREDITS: t("Top up and adjust credits, and choose plans"),
    }.get(scope, scope)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_raw() -> str:
    return PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def may_hold(db: DBSession, user: User, scope: str) -> bool:
    """Whether this person may hold a token with this scope, right now."""
    from lembas.security import permissions

    if not user.active:
        return False
    if scope in USER_SCOPES:
        return permissions.has(db, user, "api.use")
    if scope == SCOPE_ADMIN_AUDIT:
        return user.is_admin or permissions.has(db, user, "audit.read")
    if scope == SCOPE_ADMIN_CREDITS:
        return user.is_admin or permissions.has(db, user, "credits.grant")
    if scope == SCOPE_ADMIN_READ:
        return user.is_admin
    return False


def grantable(db: DBSession, user: User) -> list[str]:
    return [scope for scope in SCOPES if may_hold(db, user, scope)]


def create(
    db: DBSession,
    user: User,
    *,
    name: str,
    scopes: list[str],
    kind: str = TOKEN_PERSONAL,
    expires_at: datetime | None = None,
    device_host: str = "",
    device_platform: str = "",
    client: str = "",
) -> tuple[ApiToken, str]:
    """Make a token and return it with its raw value, which is never stored.

    Scopes the person may not hold are dropped rather than refused: the form
    only offers what they may, so a scope arriving anyway is a crafted post.
    """
    wanted = [scope for scope in dict.fromkeys(scopes) if scope in SCOPES]
    kept = [scope for scope in wanted if may_hold(db, user, scope)]
    raw = new_raw()
    token = ApiToken(
        user_id=user.id,
        name=(name or "").strip()[:120] or "Token",
        kind=kind,
        prefix=raw[:SHOWN_PREFIX],
        token_hash=hash_token(raw),
        scopes_json=kept,
        expires_at=expires_at,
        device_host=device_host[:200],
        device_platform=device_platform[:64],
        client=client[:64],
    )
    db.add(token)
    db.commit()
    return token, raw


def revoke(db: DBSession, token: ApiToken) -> None:
    """Revoking deletes: a revoked token is kept nowhere and shown
    nowhere -- the audit log is what remembers that it existed."""
    _WINDOWS.pop(token.id, None)
    forget_device(db, [token.id])
    db.delete(token)
    db.commit()


def forget_device(db: DBSession, token_ids: list[str]) -> None:
    """A device token going: the deletes still waiting for that device go with
    it -- it will never link again to be told, and the table has no
    foreign key to cascade them (`DeviceSessionDelete`)."""
    from sqlalchemy import delete

    from lembas.db.models import DeviceSessionDelete

    if token_ids:
        db.execute(delete(DeviceSessionDelete).where(DeviceSessionDelete.token_id.in_(token_ids)))


def live(token: ApiToken | None, *, now: datetime | None = None) -> bool:
    if token is None or token.revoked_at is not None:
        return False
    if token.expires_at is not None:
        expires = token.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires <= (now or datetime.now(UTC)):
            return False
    return True


def resolve(db: DBSession, raw: str) -> tuple[ApiToken, User] | None:
    if not raw or not raw.startswith(PREFIX):
        return None
    token = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(raw)))
    if not live(token):
        return None
    user = db.get(User, token.user_id)
    if user is None or not user.active:
        return None
    return token, user


# --- Rate limit ----------------------------------------------------------------
# Per token, a sliding minute. In memory: one worker serves every request, and a
# restart forgetting the window lets a program make one minute's worth of extra
# requests, which is not what this is for -- it is for a loop gone wrong.
_WINDOWS: dict[str, deque[float]] = {}


def _per_minute(db: DBSession) -> int:
    from lembas.services import settings_store

    try:
        return max(0, int(settings_store.get(db, "api_requests_per_minute")))
    except (TypeError, ValueError):
        return 120


def over_rate(db: DBSession, token: ApiToken, *, now: float | None = None) -> int:
    """Seconds to wait when this token is over its rate, else 0. Counts the call."""
    limit = _per_minute(db)
    if not limit:
        return 0
    moment = now if now is not None else time.monotonic()
    window = _WINDOWS.setdefault(token.id, deque())
    while window and window[0] <= moment - 60:
        window.popleft()
    if len(window) >= limit:
        return max(1, int(60 - (moment - window[0])) + 1)
    window.append(moment)
    return 0


def reset_rates() -> None:
    _WINDOWS.clear()


# --- The dependency --------------------------------------------------------------
@dataclass(frozen=True)
class Caller:
    """Who is calling, through which token."""

    user: User
    token: ApiToken


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": 'Bearer realm="lembas"'},
    )


def require_token(scope: str):
    """Dependency factory: the caller, holding a live token with `scope`.

        @router.get("/models")
        async def models(caller: Annotated[Caller, Depends(require_token("models"))]): …
    """

    def guard(request: Request, db: Db) -> Caller:
        raw = _bearer(request)
        if not raw:
            raise _unauthorized("An API token is required: Authorization: Bearer lmb_…")
        found = resolve(db, raw)
        if found is None:
            raise _unauthorized("That token is not valid, has expired or was revoked.")
        token, user = found
        if scope not in (token.scopes_json or []):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"This token was not given the {scope} scope."
            )
        if not may_hold(db, user, scope):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"The account behind this token may no longer use the {scope} scope.",
            )
        wait = over_rate(db, token)
        if wait:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests with this token. Slow down.",
                headers={"Retry-After": str(wait)},
            )
        # Last use, at most once a minute, so a busy program is not a write per
        # request.
        now = datetime.now(UTC)
        last = token.last_used_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last is None or (now - last).total_seconds() > 60:
            token.last_used_at = now
            token.last_used_ip = (request.client.host if request.client else "")[:45]
            db.commit()
        request.state.api_caller = Caller(user=user, token=token)
        return Caller(user=user, token=token)

    return guard


def require_live_token():
    """Dependency factory: the caller, holding any live token of theirs
    -- a personal one or a device's, whatever its scopes. For questions about
    the account itself (`/v1/usage`, `/v1/me/*`), which every program signed in
    as the person may ask."""

    def guard(request: Request, db: Db) -> Caller:
        raw = _bearer(request)
        if not raw:
            raise _unauthorized("An API token is required: Authorization: Bearer lmb_…")
        found = resolve(db, raw)
        if found is None:
            raise _unauthorized("That token is not valid, has expired or was revoked.")
        token, user = found
        wait = over_rate(db, token)
        if wait:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests with this token. Slow down.",
                headers={"Retry-After": str(wait)},
            )
        request.state.api_caller = Caller(user=user, token=token)
        return Caller(user=user, token=token)

    return guard


LiveCaller = Annotated[Caller, Depends(require_live_token())]
ModelsCaller = Annotated[Caller, Depends(require_token(SCOPE_MODELS))]
AdminReadCaller = Annotated[Caller, Depends(require_token(SCOPE_ADMIN_READ))]
AuditCaller = Annotated[Caller, Depends(require_token(SCOPE_ADMIN_AUDIT))]
CreditsCaller = Annotated[Caller, Depends(require_token(SCOPE_ADMIN_CREDITS))]


__all__ = [
    "ADMIN_SCOPES",
    "PREFIX",
    "SCOPES",
    "SCOPE_ADMIN_AUDIT",
    "SCOPE_ADMIN_READ",
    "SCOPE_LIBRARY",
    "SCOPE_LINK",
    "SCOPE_MODELS",
    "USER_SCOPES",
    "AdminReadCaller",
    "LiveCaller",
    "AuditCaller",
    "CreditsCaller",
    "SCOPE_ADMIN_CREDITS",
    "Caller",
    "ModelsCaller",
    "create",
    "grantable",
    "hash_token",
    "live",
    "may_hold",
    "over_rate",
    "require_token",
    "reset_rates",
    "resolve",
    "revoke",
    "scope_label",
]
