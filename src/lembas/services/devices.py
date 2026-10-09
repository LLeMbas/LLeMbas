"""Device login (RFC 8628): a program on another machine signs in as a person.

The flow, as the LLeMbas CLI's `/login` drives it:

1. The device posts to `/api/device/code` with what it is (client, a name, its
   host and platform) and the scopes it wants. It gets a `device_code` to poll
   with and a short `user_code` to show.
2. The person opens `/device` on this instance, signed in, types the code, and
   sees exactly what is asking -- the device's name, host and platform, the
   address the request came from, and each scope in words -- and approves or
   denies.
3. The device polls `/api/device/token` every `interval` seconds. Until a
   decision it hears `authorization_pending`; polling faster than asked earns
   `slow_down`; a denial is `access_denied`; a code past its life is
   `expired_token`. After approval the next poll is answered with a **device
   token** (`ApiToken.kind == "device"`) -- once. A second poll with the same
   code is `invalid_grant`.

The device code is stored as its hash, like every other secret here; the user
code is short and readable on purpose, and is good for ten minutes and one
decision.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import TOKEN_DEVICE, ApiToken, DeviceCode, User
from lembas.security import api_tokens

LIFETIME = timedelta(minutes=10)
INTERVAL = 5
# No vowels (no words), no 0/O, 1/I/L, 2/Z, 5/S, 8/B.
ALPHABET = "CDFGHJKMNPQRTVWXY"
CODE_LENGTH = 8

PENDING = "authorization_pending"
SLOW_DOWN = "slow_down"
DENIED = "access_denied"
EXPIRED = "expired_token"
INVALID = "invalid_grant"


def _as_utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def normalise(user_code: str) -> str:
    """What a person typed, as stored: upper case, no dash or spaces."""
    return "".join(ch for ch in (user_code or "").upper() if ch.isalnum())


def shown(user_code: str) -> str:
    code = normalise(user_code)
    return f"{code[:4]}-{code[4:]}" if len(code) == CODE_LENGTH else code


def _new_user_code(db: DBSession) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        if db.scalar(select(DeviceCode.id).where(DeviceCode.user_code == code)) is None:
            return code
    raise RuntimeError("could not find a free device code")  # pragma: no cover


def start(
    db: DBSession,
    *,
    client: str,
    device_name: str,
    device_host: str = "",
    device_platform: str = "",
    scopes: list[str] | None = None,
    ip: str = "",
    now: datetime | None = None,
) -> tuple[DeviceCode, str]:
    """Open a device login. Returns the row and the raw device code."""
    now = now or datetime.now(UTC)
    sweep(db, now=now)
    wanted = [s for s in dict.fromkeys(scopes or [api_tokens.SCOPE_MODELS])
              if s in api_tokens.USER_SCOPES]
    raw = secrets.token_urlsafe(32)
    row = DeviceCode(
        device_code_hash=api_tokens.hash_token(raw),
        user_code=_new_user_code(db),
        client=(client or "")[:64],
        device_name=(device_name or "")[:120] or "A device",
        device_host=(device_host or "")[:200],
        device_platform=(device_platform or "")[:64],
        scopes_json=wanted or [api_tokens.SCOPE_MODELS],
        ip=(ip or "")[:45],
        expires_at=now + LIFETIME,
        interval=INTERVAL,
    )
    db.add(row)
    db.commit()
    return row, raw


def pending(db: DBSession, user_code: str, *, now: datetime | None = None) -> DeviceCode | None:
    """The login a person typed the code of, if it is still waiting."""
    code = normalise(user_code)
    if len(code) != CODE_LENGTH:
        return None
    row = db.scalar(select(DeviceCode).where(DeviceCode.user_code == code))
    if row is None or row.approved_by or row.denied or row.token_id:
        return None
    if _as_utc(row.expires_at) <= (now or datetime.now(UTC)):
        return None
    return row


def approve(db: DBSession, row: DeviceCode, user: User) -> None:
    row.approved_by = user.id
    db.commit()


def deny(db: DBSession, row: DeviceCode) -> None:
    row.denied = True
    db.commit()


@dataclass(frozen=True)
class Poll:
    """What one poll got: an error code from RFC 8628, or a token."""

    error: str = ""
    token: ApiToken | None = None
    raw: str = ""
    interval: int = INTERVAL


def poll(db: DBSession, raw_device_code: str, *, now: datetime | None = None) -> Poll:
    now = now or datetime.now(UTC)
    row = db.scalar(
        select(DeviceCode).where(
            DeviceCode.device_code_hash == api_tokens.hash_token(raw_device_code or "")
        )
    )
    if row is None or row.token_id:
        return Poll(error=INVALID)
    if _as_utc(row.expires_at) <= now:
        return Poll(error=EXPIRED)
    if row.denied:
        return Poll(error=DENIED)

    last = _as_utc(row.last_polled_at)
    row.last_polled_at = now
    if last is not None and (now - last).total_seconds() < row.interval - 0.5:
        # RFC 8628 3.5: slow down, and add five seconds to the interval from now on.
        row.interval = min(row.interval + 5, 60)
        db.commit()
        return Poll(error=SLOW_DOWN, interval=row.interval)

    if not row.approved_by:
        db.commit()
        return Poll(error=PENDING, interval=row.interval)

    user = db.get(User, row.approved_by)
    if user is None or not user.active:
        db.commit()
        return Poll(error=DENIED)
    token, raw = api_tokens.create(
        db,
        user,
        name=row.device_name,
        scopes=list(row.scopes_json or []),
        kind=TOKEN_DEVICE,
        device_host=row.device_host,
        device_platform=row.device_platform,
        client=row.client,
    )
    row.token_id = token.id
    db.commit()
    return Poll(token=token, raw=raw, interval=row.interval)


def sweep(db: DBSession, *, now: datetime | None = None) -> int:
    """Forget logins an hour past their life; the codes are not worth keeping."""
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=1)
    removed = db.execute(delete(DeviceCode).where(DeviceCode.expires_at < cutoff)).rowcount
    if removed:
        db.commit()
    return int(removed or 0)


def devices_of(db: DBSession, user: User) -> list[ApiToken]:
    """This person's device tokens, live ones first, newest first."""
    rows = list(
        db.scalars(
            select(ApiToken)
            .where(ApiToken.user_id == user.id, ApiToken.kind == TOKEN_DEVICE)
            .order_by(ApiToken.created_at.desc())
        )
    )
    return sorted(rows, key=lambda t: t.revoked_at is not None)


__all__ = [
    "DENIED",
    "EXPIRED",
    "INTERVAL",
    "INVALID",
    "LIFETIME",
    "PENDING",
    "SLOW_DOWN",
    "Poll",
    "approve",
    "deny",
    "devices_of",
    "normalise",
    "pending",
    "poll",
    "shown",
    "start",
    "sweep",
]
