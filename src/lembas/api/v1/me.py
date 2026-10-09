"""What a program signed in as a person asks about that person.

`GET /v1/usage` -- the month's tokens and credits, the account's plan and
balance, and this token's own share -- and `GET`/`PUT /v1/me/personalization`,
the instructions and personality a LLeMbas CLI signed in here uses in place of
its own local keys. Any live token of the person may ask (`LiveCaller`): a
device token as much as a personal one, whatever its scopes, since both are the
person and neither question reaches anybody else's data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from lembas.api.deps import Db
from lembas.db.models import MICRO, TOKEN_DEVICE, Plan, Usage
from lembas.security.api_tokens import LiveCaller
from lembas.services import credits as credits_service
from lembas.services import personalization, settings_store
from lembas.services import usage as usage_service

router = APIRouter(prefix="/v1", tags=["me"])

# A scope this instance does not grant yet: the contract names an `api`
# scope as the other kind of token allowed to write personalization. None
# carries it today, so in practice only a device token writes.
SCOPE_API = "api"


def _credits(micro: int | None) -> float | None:
    """Micro-credits as the number of credits a client shows."""
    return None if micro is None else round(int(micro) / MICRO, 6)


@router.get("/usage")
async def my_usage(db: Db, caller: LiveCaller) -> dict[str, Any]:
    """The month so far, for the person and for this token. Credit figures are
    null on an instance that does not use credits, and `plan` is null for an
    account without one -- which is what tells a client to show no balance.

    Also `currency` and `credit_value` (what one credit is worth in it),
    `balance_money`, `month.cost_money` and `device.cost_money` -- the same
    figures in money -- `admin`, true for an administrator, whose use is recorded and never
    deducted (a client shows "unlimited" rather than a balance), and `windows`,
    the plan's rolling limits as `/api/v1/me/credits` gives them. The money
    figures and `windows` follow the credit ones: null (and empty) where
    credits are off. `month.cost` and `device.cost` are what the use cost,
    taken or not -- for an administrator, what it would have cost."""
    user, token = caller.user, caller.token
    now = datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    period = usage_service.period_of(now)
    row = db.scalar(select(Usage).where(Usage.user_id == user.id, Usage.period == period))
    values = settings_store.credits(db)
    on = bool(values.get("enabled"))
    value = float(values["credit_value"])

    def money(micro: int | None) -> float | None:
        return None if micro is None else round(credits_service.micro_to_money(micro, value), 6)

    plan_row = db.get(Plan, user.plan_id) if on and getattr(user, "plan_id", None) else None
    balance = credits_service.balance(db, user) if on else None
    month_cost = credits_service.spent_since(db, user.id, start) if on else None
    device_cost = (
        credits_service.spent_since(db, user.id, start, token_id=token.id) if on else None
    )
    windows = (
        credits_service.windows(db, user) if on and plan_row is not None and not user.is_admin
        else []
    )
    this_month = token.usage_period == period
    return {
        "plan": {"name": plan_row.name, "credits_per_month": _credits(plan_row.monthly)}
        if plan_row is not None
        else None,
        "balance": _credits(balance),
        "balance_money": money(balance),
        "month": {
            "from": start.isoformat(),
            "tokens_in": int(row.prompt_tokens if row else 0),
            "tokens_out": int(row.completion_tokens if row else 0),
            "cost": _credits(month_cost),
            "cost_money": money(month_cost),
        },
        "device": {
            "tokens_in": int(token.usage_prompt_tokens or 0) if this_month else 0,
            "tokens_out": int(token.usage_completion_tokens or 0) if this_month else 0,
            "cost": _credits(device_cost),
            "cost_money": money(device_cost),
        },
        "windows": [
            {
                "window": w.key,
                "limit": _credits(w.limit),
                "spent": _credits(w.spent),
                "frees_at": w.frees_at.isoformat() if w.frees_at else None,
            }
            for w in windows
        ],
        "currency": values["currency"],
        "credit_value": value,
        "admin": bool(user.is_admin),
        "unit": "credits",
    }


def _personalization(db, user) -> dict[str, Any]:
    chosen = personalization.of(user)
    available = personalization.enabled(db)
    return {
        "enabled": available and chosen["on"],
        "available": available,
        "personality": chosen["personality"],
        "personality_custom": chosen["custom"],
        "instructions": chosen["instructions"],
    }


@router.get("/me/personalization")
async def my_personalization(db: Db, caller: LiveCaller) -> dict[str, Any]:
    """`enabled`: offered here and switched on by the person; `available`:
    offered here at all (an administrator's choice)."""
    return _personalization(db, caller.user)


@router.put("/me/personalization")
async def set_my_personalization(
    request: Request, db: Db, caller: LiveCaller
) -> dict[str, Any]:
    """The same fields back. A field left out keeps what is stored, so a client
    that changes the personality does not wipe the instructions.

    Only a LLeMbas CLI's device token writes (reading stays open to any live
    token): what is written here goes to every model this person talks to, so
    a token handed to a third-party chat client, or a log shipper's, must not be
    able to plant instructions in all of them."""
    token = caller.token
    if token.kind != TOKEN_DEVICE and SCOPE_API not in (token.scopes_json or []):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only a LLeMbas CLI signed in as you may change your personalization; "
            "change it in Settings.",
        )
    if not personalization.enabled(db):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Personalization is not offered on this instance."
        )
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The body is not JSON.") from exc
    if not isinstance(body, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The body must be a JSON object.")
    user = caller.user
    now = personalization.of(user)
    # Partial on purpose: the CLI sends `enabled` and the one field that
    # changed. A field left out keeps what is stored; a preset this release
    # does not list is kept as the CLI named it (`personalization._preset`).
    preset = body.get("personality", now["personality"])
    if not isinstance(preset, str):
        raise HTTPException(422, "personality is text: a preset's id, custom, or empty.")
    text = {
        key: body.get(key, now[stored])
        for key, stored in (("instructions", "instructions"), ("personality_custom", "custom"))
    }
    if not all(isinstance(value, str) for value in text.values()):
        raise HTTPException(422, "instructions and personality_custom are text.")
    enabled = body.get("enabled", now["on"])
    if not isinstance(enabled, bool):
        raise HTTPException(422, "enabled is true or false.")
    user.settings_json = {
        **(user.settings_json or {}),
        "personalization": personalization.cleaned(
            text["instructions"],
            preset,
            text["personality_custom"],
            on=enabled,
        ),
    }
    db.commit()
    return _personalization(db, user)


__all__ = ["router"]
