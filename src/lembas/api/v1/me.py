"""What a program signed in as a person asks about that person.

`GET /v1/usage` -- the month's tokens and what they cost, and this token's own
share -- and `GET`/`PUT /v1/me/personalization`,
the instructions and personality a LLeMbas CLI signed in here uses in place of
its own local keys. Any live token of the person may ask (`LiveCaller`): a
device token as much as a personal one, whatever its scopes, since both are the
person and neither question reaches anybody else's data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from lembas.api.deps import Db
from lembas.db.models import TOKEN_DEVICE
from lembas.security.api_tokens import LiveCaller
from lembas.services import personalization, pricing, settings_store
from lembas.services import usage as usage_service

router = APIRouter(prefix="/v1", tags=["me"])

# A scope this instance does not grant yet: the contract names an `api`
# scope as the other kind of token allowed to write personalization. None
# carries it today, so in practice only a device token writes.
SCOPE_API = "api"


@router.get("/usage")
async def my_usage(db: Db, caller: LiveCaller) -> dict[str, Any]:
    """The month so far, for the person and for this token. Counting only.

    `month.tokens_in`/`tokens_out` across every model, and `month.cost_money`,
    what the month's priced use cost in `currency` (0 when nothing was
    priced). `device` is this token's own tokens. There are no credits, plans
    or budgets any more: `plan`, `balance`, `balance_money`, `month.cost`,
    `device.cost` and `device.cost_money` stay in the answer as null and
    `windows` as empty, so a client written for 1.x reads "nothing to show"
    rather than failing; `unit` is `"money"`."""
    user, token = caller.user, caller.token
    now = datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    period = usage_service.period_of(now)
    rows = usage_service.by_model(db, user.id, period=period)
    this_month = token.usage_period == period
    return {
        "plan": None,
        "balance": None,
        "balance_money": None,
        "month": {
            "from": start.isoformat(),
            "tokens_in": sum(r.tokens_in for r in rows),
            "tokens_out": sum(r.tokens_out for r in rows),
            "cost": None,
            "cost_money": round(pricing.micro_to_money(sum(r.cost_micro for r in rows)), 6),
        },
        "device": {
            "tokens_in": int(token.usage_prompt_tokens or 0) if this_month else 0,
            "tokens_out": int(token.usage_completion_tokens or 0) if this_month else 0,
            "cost": None,
            "cost_money": None,
        },
        "windows": [],
        "currency": settings_store.currency(db),
        "admin": bool(user.is_admin),
        "unit": "money",
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
