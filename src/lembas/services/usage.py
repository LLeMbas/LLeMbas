"""What an account used: counting only, per user, month and model.

A row per user, period and model (`db/models/user.py:Usage`) for the usage
page; a reply's own cost is on its message. **Never a quota**: nothing here
refuses anything, and there is no monthly token budget -- LLeMbas counts and
prices, it does not ration. (The one limit left that reads a count is the
pictures-a-day permission, and it counts `Attachment` rows, not this.)

**Recorded even when the reply failed.** `generation._persist` is the single
writer for everything a reply produced, and it calls this whether the reply
finished, was stopped or errored: an endpoint charges for tokens it generated
regardless of whether anybody wanted them.

**Never raises.** Bookkeeping that broke a reply would be worse than none.

The period is UTC.
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


def row_for(db: DBSession, user_id: str, model_id: str = "", *, period: str = "") -> Usage:
    """This account's row for a model and a period, made if it is not there."""
    period = period or period_of()
    row = db.get(Usage, (user_id, period, model_id or ""))
    if row is None:
        row = Usage(user_id=user_id, period=period, model_id=model_id or "")
        for name in (
            "tokens_in", "tokens_cached", "tokens_out", "requests", "cost_micro", "images",
            "audio_seconds", "speech_chars",
        ):
            setattr(row, name, 0)
        db.add(row)
        db.flush()
    return row


def record(
    db: DBSession,
    user_id: str,
    *,
    model_id: str = "",
    prompt_tokens: int = 0,
    cached_tokens: int = 0,
    completion_tokens: int = 0,
    requests: int = 1,
    cost_micro: int | None = 0,
    images: int = 0,
    audio_seconds: float = 0,
    speech_chars: int = 0,
) -> None:
    """Add one reply, call or use of a service. Best-effort; never raises."""
    if not user_id:
        return
    try:
        row = row_for(db, user_id, model_id)
        row.tokens_in += max(0, int(prompt_tokens))
        row.tokens_cached += max(0, int(cached_tokens))
        row.tokens_out += max(0, int(completion_tokens))
        row.requests += max(0, int(requests))
        row.cost_micro += max(0, int(cost_micro or 0))
        row.images += max(0, int(images))
        row.audio_seconds += max(0, math.ceil(audio_seconds))
        row.speech_chars += max(0, int(speech_chars))
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


def by_model(db: DBSession, user_id: str, *, period: str = "") -> list[Usage]:
    """This account's rows for a month, the most used first."""
    return list(
        db.scalars(
            select(Usage)
            .where(Usage.user_id == user_id, Usage.period == (period or period_of()))
            .order_by((Usage.tokens_in + Usage.tokens_out).desc(), Usage.model_id)
        )
    )


def summary(db: DBSession, user: User) -> dict[str, int]:
    """This month's figures across every model, for the admin screen."""
    rows = by_model(db, user.id)
    prompt = sum(r.tokens_in for r in rows)
    completion = sum(r.tokens_out for r in rows)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "tokens": prompt + completion,
        "replies": sum(r.requests for r in rows),
        "images": sum(r.images for r in rows),
        "images_today": images_today(db, user.id),
        "cost_micro": sum(r.cost_micro for r in rows),
    }


__all__ = [
    "by_model",
    "images_today",
    "over_image_budget",
    "period_of",
    "record",
    "record_token",
    "row_for",
    "summary",
]
