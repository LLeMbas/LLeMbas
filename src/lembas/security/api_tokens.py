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
| `library` | `/mcp`: the holder's memory | `api.use` |
| `link` | being driven from this instance as a linked device | `api.use` |
| `app` | `/api/v2`: the holder's chats, as the web UI shows them -- the app's door | `api.use` |
| `admin:read` | users and usage, read only | administrator |
| `admin:audit` | the audit log | administrator, or `audit.read` |
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
# The account's memory over `/mcp` (harness spec 3.0.0, acp.md *Memory*).
# `library` is its older name -- from when notes, skills and knowledge were
# served too -- and still counts as `memory`; it is no longer offered for a new
# token, but a device that asks for it gets it.
SCOPE_MEMORY = "memory"
SCOPE_LIBRARY = "library"
SCOPE_LINK = "link"
SCOPE_APP = "app"
SCOPE_ADMIN_READ = "admin:read"
SCOPE_ADMIN_AUDIT = "admin:audit"

USER_SCOPES = (SCOPE_MODELS, SCOPE_MEMORY, SCOPE_LIBRARY, SCOPE_LINK, SCOPE_APP)
LEGACY_SCOPES = (SCOPE_LIBRARY,)
# The scopes a token may hold to reach the memory: the new name and the old.
MEMORY_SCOPES = (SCOPE_MEMORY, SCOPE_LIBRARY)
ADMIN_SCOPES = (SCOPE_ADMIN_READ, SCOPE_ADMIN_AUDIT)
SCOPES = (*USER_SCOPES, *ADMIN_SCOPES)

def scope_label(scope: str) -> str:
    """What a scope allows, in the reader's language. A function rather than a
    table so each sentence is a literal `t()` the translation check can see."""
    from lembas.web.i18n import t

    return {
        SCOPE_MODELS: t("Use your models, and spend your usage"),
        SCOPE_MEMORY: t("Read and write your memory"),
        SCOPE_LIBRARY: t("Read and write your memory"),
        SCOPE_LINK: t("Accept work from this instance as a linked device"),
        SCOPE_APP: t("Read and write your chats, as the web interface does"),
        SCOPE_ADMIN_READ: t("Read every account and its usage"),
        SCOPE_ADMIN_AUDIT: t("Read the audit log"),
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
    if scope == SCOPE_ADMIN_READ:
        return user.is_admin
    return False


def grantable(db: DBSession, user: User) -> list[str]:
    """What a new personal token may be given: never an older name of a scope."""
    return [
        scope for scope in SCOPES if scope not in LEGACY_SCOPES and may_hold(db, user, scope)
    ]


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


def hand_over(db: DBSession, old: ApiToken, new: ApiToken) -> int:
    """A device logged in again and is replacing its old token with `new`: the
    old one's agent chats, and the deletes still waiting for it, become the new
    one's. The sessions are the device's, on its disk, so the new token's link
    holds them. Only between two tokens of one account; the number of chats moved."""
    from sqlalchemy import update

    from lembas.db.models import Chat, DeviceSessionDelete

    if old.id == new.id or old.user_id != new.user_id:
        return 0
    chats = update(Chat).where(Chat.device_token_id == old.id).values(device_token_id=new.id)
    moved = db.execute(chats).rowcount
    deletes = update(DeviceSessionDelete).where(DeviceSessionDelete.token_id == old.id)
    db.execute(deletes.values(token_id=new.id))
    db.commit()
    return moved or 0


def forget_device(db: DBSession, token_ids: list[str]) -> None:
    """A device token going: the deletes still waiting for that device go with
    it -- it will never link again to be told, and the table has no
    foreign key to cascade them (`DeviceSessionDelete`). So does any push
    registration the app made under it: a signed-out phone is not told."""
    from sqlalchemy import delete

    from lembas.db.models import DeviceSessionDelete, PushSubscription

    if token_ids:
        db.execute(delete(DeviceSessionDelete).where(DeviceSessionDelete.token_id.in_(token_ids)))
        db.execute(delete(PushSubscription).where(PushSubscription.token_id.in_(token_ids)))


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


def require_token(scope: str, *also: str):
    """Dependency factory: the caller, holding a live token with `scope` --
    or with one of `also`, the older names that count as it.

        @router.get("/models")
        async def models(caller: Annotated[Caller, Depends(require_token("models"))]): …
    """
    accepted = (scope, *also)

    def guard(request: Request, db: Db) -> Caller:
        raw = _bearer(request)
        if not raw:
            raise _unauthorized("An API token is required: Authorization: Bearer lmb_…")
        found = resolve(db, raw)
        if found is None:
            raise _unauthorized("That token is not valid, has expired or was revoked.")
        token, user = found
        held = next((s for s in accepted if s in (token.scopes_json or [])), None)
        if held is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"This token was not given the {scope} scope."
            )
        if not may_hold(db, user, held):
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
AppCaller = Annotated[Caller, Depends(require_token(SCOPE_APP))]
AdminReadCaller = Annotated[Caller, Depends(require_token(SCOPE_ADMIN_READ))]
AuditCaller = Annotated[Caller, Depends(require_token(SCOPE_ADMIN_AUDIT))]


__all__ = [
    "ADMIN_SCOPES",
    "PREFIX",
    "SCOPES",
    "SCOPE_ADMIN_AUDIT",
    "SCOPE_ADMIN_READ",
    "SCOPE_APP",
    "SCOPE_LIBRARY",
    "SCOPE_MEMORY",
    "LEGACY_SCOPES",
    "MEMORY_SCOPES",
    "SCOPE_LINK",
    "SCOPE_MODELS",
    "USER_SCOPES",
    "AdminReadCaller",
    "AppCaller",
    "LiveCaller",
    "AuditCaller",
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
