"""What an account has spent, and whether it may spend more.

A row per user per period, not per reply. A per-reply ledger is what somebody
eventually wants for a bill; this exists to answer one question on the request
path — "has this account used its month?" — and that wants one indexed lookup
rather than a sum over ten thousand rows.

**Recorded even when the reply failed.** `generation._persist` is the single
writer for everything a reply produced, and it calls this whether the reply
finished, was stopped or errored: an endpoint charges for tokens it generated
regardless of whether anybody wanted them, and a quota that only counted happy
paths would be one somebody could avoid by pressing Stop.

**Never raises.** A quota that broke a reply because its own bookkeeping failed
would be worse than no quota. Everything here is best-effort and logs.

The period is UTC and the boundary is not the reader's midnight. A quota that
reset at a different instant for each member of a group is one nobody can reason
about, and nobody experiences a monthly allowance to the hour.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Attachment, Usage, User

log = logging.getLogger(__name__)


def period_of(moment: datetime | None = None) -> str:
    return (moment or datetime.now(tz=UTC)).astimezone(UTC).strftime("%Y-%m")


def row_for(db: DBSession, user_id: str, *, period: str = "") -> Usage:
    """This account's row for a period, made if it is not there yet."""
    period = period or period_of()
    row = db.scalar(select(Usage).where(Usage.user_id == user_id, Usage.period == period))
    if row is None:
        row = Usage(user_id=user_id, period=period)
        db.add(row)
        db.flush()
    return row


def record(
    db: DBSession,
    user_id: str,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    images: int = 0,
    replies: int = 1,
    audio_seconds: float = 0,
    speech_chars: int = 0,
) -> None:
    """Add what one reply spent -- or one use of speech or of
    pictures through the API, with `replies=0`. Best-effort, and never raises."""
    if not user_id:
        return
    try:
        row = row_for(db, user_id)
        row.prompt_tokens += max(0, int(prompt_tokens))
        row.completion_tokens += max(0, int(completion_tokens))
        row.images += max(0, int(images))
        row.replies += max(0, int(replies))
        row.audio_seconds = (row.audio_seconds or 0) + max(0, math.ceil(audio_seconds))
        row.speech_chars = (row.speech_chars or 0) + max(0, int(speech_chars))
    except Exception:  # bookkeeping must never break a reply
        log.debug("could not record usage for %s", user_id, exc_info=True)


def record_token(
    db: DBSession, token_id: str, *, prompt_tokens: int = 0, completion_tokens: int = 0
) -> None:
    """Add what one call spent to its token's own month. Best-effort,
    and never raises."""
    from lembas.db.models import ApiToken

    try:
        token = db.get(ApiToken, token_id)
        if token is None:
            return
        period = period_of()
        if token.usage_period != period:
            token.usage_period = period
            token.usage_prompt_tokens = 0
            token.usage_completion_tokens = 0
        token.usage_prompt_tokens += max(0, int(prompt_tokens))
        token.usage_completion_tokens += max(0, int(completion_tokens))
    except Exception:  # bookkeeping must never break a reply
        log.debug("could not record usage for token %s", token_id, exc_info=True)


def month_tokens(db: DBSession, user_id: str) -> int:
    row = db.scalar(select(Usage).where(Usage.user_id == user_id, Usage.period == period_of()))
    return int((row.prompt_tokens if row else 0) + (row.completion_tokens if row else 0))


def images_today(db: DBSession, user_id: str) -> int:
    """Pictures this account has made since midnight UTC.

    Counted off `Attachment` rather than kept as a counter, because there is a
    natural source of truth and a *daily* counter would need a second row shape
    and a second reset. The month's total on `Usage.images` is for the admin
    screen, where a number that is a day stale costs nothing.
    """
    start = datetime.combine(date.today(), datetime.min.time(), tzinfo=UTC)
    return int(
        db.scalar(
            select(func.count())
            .select_from(Attachment)
            .where(
                Attachment.user_id == user_id,
                Attachment.kind == "image",
                Attachment.created_at >= start,
            )
        )
        or 0
    )


# --- Asking permission ----------------------------------------------------------
def over_token_budget(db: DBSession, user: User | None) -> str:
    """Why this account may not start another reply, or "".

    Checked **before** a reply is built rather than while it streams. A quota
    that stopped a reply half way through would leave the reader with an answer
    that trails off, which is exactly what `_wrap_up` exists to prevent for every
    other budget here -- and unlike those, this one is knowable in advance.
    """
    from lembas.security import permissions

    if user is None:
        return ""
    ceiling = permissions.limit(db, user, "monthly_tokens")
    if ceiling <= 0:
        return ""
    spent = month_tokens(db, user.id)
    if spent < ceiling:
        return ""
    return (
        f"This account has used its {ceiling:,} tokens for the month "
        f"({spent:,} so far). It will reset on the first."
    )


def over_image_budget(db: DBSession, user: User | None) -> str:
    from lembas.security import permissions

    if user is None:
        return ""
    ceiling = permissions.limit(db, user, "images_per_day")
    if ceiling <= 0:
        return ""
    made = images_today(db, user.id)
    if made < ceiling:
        return ""
    return f"This account has made its {ceiling} image(s) for today."


def summary(db: DBSession, user: User) -> dict[str, int]:
    """This month's figures, for the admin screen."""
    row = db.scalar(select(Usage).where(Usage.user_id == user.id, Usage.period == period_of()))
    return {
        "prompt_tokens": int(row.prompt_tokens if row else 0),
        "completion_tokens": int(row.completion_tokens if row else 0),
        "tokens": month_tokens(db, user.id),
        "replies": int(row.replies if row else 0),
        "images": int(row.images if row else 0),
        "images_today": images_today(db, user.id),
    }


__all__ = [
    "images_today",
    "month_tokens",
    "over_image_budget",
    "over_token_budget",
    "period_of",
    "record",
    "record_token",
    "row_for",
    "summary",
]
