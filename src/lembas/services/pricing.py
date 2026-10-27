"""What a reply cost, in money, worked out when it is written down.

There are no credits, plans or budgets: LLeMbas counts and prices, it never
refuses. A model's price list (`Model.pricing_json`) is money per million
tokens -- `input`, `cached_input`, `output`, `reasoning` (the output price when
unset) -- and `per_request` per reply, in the instance's currency
(`settings_store.currency`). A model with no prices is free and its replies are
unpriced (`cost_micro` NULL), which is what a local model usually is.

The cost is **stored on the message** with the price that was applied
(`Message.cost_micro`, `currency`, `priced_json`), so changing a price later
never rewrites what an old reply cost. Amounts are integer **micro-units** of
the currency (a millionth), so a price per million tokens times a token count
divides exactly and no float is ever summed; rounded up, so a priced reply
never costs nothing by rounding.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session as DBSession

MICRO = 1_000_000
PRICE_KEYS = ("input", "cached_input", "output", "reasoning", "per_request")
# What writing a prompt to the cache costs against ordinary input (Anthropic's
# five-minute cache).
CACHE_WRITE_FACTOR = 1.25


def prices(model: Any) -> dict[str, float]:
    """The model's non-zero prices. Anything else in the dict (an old
    `"unit"` mark) is ignored."""
    raw = (getattr(model, "pricing_json", None) or {}) if model is not None else {}
    out: dict[str, float] = {}
    for key in PRICE_KEYS:
        try:
            value = float(raw.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            out[key] = value
    return out


def free(model: Any) -> bool:
    return not prices(model)


def cost_micro(
    price: dict[str, float],
    *,
    prompt_tokens: int = 0,
    cached_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_tokens: int = 0,
    cache_write_tokens: int = 0,
    requests: int = 1,
) -> int:
    """Micro-units of money for one reply at `price`.

    Cached tokens are part of the prompt count and priced at `cached_input`
    (the input price when unset); tokens written to the cache are part of it
    too, at `CACHE_WRITE_FACTOR` times input. Reasoning tokens are part of the
    completion and priced at `reasoning` (the output price when unset).
    """
    if not price:
        return 0
    prompt = max(0, prompt_tokens)
    cached = min(max(0, cached_tokens), prompt)
    written = min(max(0, cache_write_tokens), prompt - cached)
    fresh = prompt - cached - written
    completion = max(0, completion_tokens)
    reasoning = min(max(0, reasoning_tokens), completion)
    answer = completion - reasoning
    per_million = (
        fresh * price.get("input", 0.0)
        + cached * price.get("cached_input", price.get("input", 0.0))
        + written * price.get("input", 0.0) * CACHE_WRITE_FACTOR
        + answer * price.get("output", 0.0)
        + reasoning * price.get("reasoning", price.get("output", 0.0))
    )
    # money per million tokens * tokens == micro-units of money.
    total = per_million + requests * price.get("per_request", 0.0) * MICRO
    return math.ceil(round(total, 6))


def price_reply(db: DBSession, model: Any, usage: dict[str, int]) -> dict[str, Any] | None:
    """`cost_micro`, `currency` and `priced_json` for a message, or None for an
    unpriced (free) model."""
    from lembas.services import settings_store

    price = prices(model)
    if not price:
        return None
    return {
        "cost_micro": cost_micro(
            price,
            prompt_tokens=usage.get("prompt_tokens", 0),
            cached_tokens=usage.get("cached_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            reasoning_tokens=usage.get("reasoning_tokens", 0),
            cache_write_tokens=usage.get("cache_write_tokens", 0),
        ),
        "currency": settings_store.currency(db),
        "priced_json": {**price, "model_id": getattr(model, "model_id", "")},
    }


def service_cost_micro(price: float, quantity: float) -> int:
    """Micro-units of money for `quantity` units of a service at `price` each:
    minutes of speech, millions of characters, pictures."""
    amount = max(0.0, float(price or 0)) * max(0.0, float(quantity or 0))
    return math.ceil(round(amount * MICRO, 6)) if amount > 0 else 0


def micro_to_money(micro: int) -> float:
    return int(micro) / MICRO


def format_money(micro: int, currency_code: str, code: str | None = None) -> str:
    """An amount with its sign, in the reader's language: `€1.23` in English,
    `1,23 €` in Slovak. Four places below a cent, where two would round a
    reply's cost to nothing."""
    from lembas.services.settings_store import CURRENCIES
    from lembas.web import i18n

    amount = micro_to_money(micro)
    sign = CURRENCIES.get(currency_code, currency_code)
    places = 4 if amount and abs(amount) < 0.01 else 2
    text = i18n.number(abs(amount), places, code)
    minus = "-" if amount < 0 else ""
    if i18n.sign_first(code):
        return f"{minus}{sign}{text}"
    return f"{minus}{text} {sign}"


__all__ = [
    "CACHE_WRITE_FACTOR",
    "MICRO",
    "PRICE_KEYS",
    "cost_micro",
    "format_money",
    "free",
    "micro_to_money",
    "price_reply",
    "prices",
    "service_cost_micro",
]
