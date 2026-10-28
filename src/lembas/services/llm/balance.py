"""What a provider says is left on the account, or was spent -- beside LLeMbas's own figures.

Per provider type (`providers.ProviderType.balance`), read on request (the
connection page's *Check balance*) and kept in `health_json["balance"]`:

- DeepSeek: `GET /user/balance` with the connection's key -- the balance per
  currency (total, granted, topped up).
- OpenRouter: `GET /api/v1/credits` with the connection's key -- credits
  bought less used, in US dollars.
- OpenAI: `GET /v1/organization/costs` with an **admin** key (`billing_key`) --
  the spend over the last 30 days; OpenAI has no balance to read.
- Anthropic: `GET /v1/organizations/cost_report` with an **admin** key -- the
  spend over the last 30 days; there is no balance either.

What comes back is shown as the provider's own figure, labelled which kind it
is (`kind`: `balance` or `spent_30d`), never mixed into LLeMbas's estimate.
**Nothing here is on a reply's path**: a reply never waits for, or fails on,
any of it. Never raises; a failure is `{"ok": false, "error": …}`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from lembas.services.llm import providers
from lembas.services.llm.openai_client import Endpoint
from lembas.web.i18n import N_

log = logging.getLogger(__name__)

TIMEOUT = 15.0
KIND_BALANCE = "balance"
KIND_SPENT = "spent_30d"


def _root(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _failed(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message[:300]}


async def _get(url: str, headers: dict[str, str], params: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        response = await client.get(url, headers=headers, params=params or {})
    if response.status_code in (401, 403):
        raise PermissionError(N_("The key was refused for this."))
    response.raise_for_status()
    return response.json()


async def _deepseek(endpoint: Endpoint, _billing: str) -> dict[str, Any]:
    payload = await _get(f"{_root(endpoint.base_url)}/user/balance",
                         {"Authorization": f"Bearer {endpoint.api_key}"})
    items = []
    for info in (payload or {}).get("balance_infos") or []:
        if not isinstance(info, dict):
            continue
        total = _number(info.get("total_balance"))
        if total is None:
            continue
        items.append({
            "currency": str(info.get("currency") or "").upper()[:3],
            "amount": total,
            "granted": _number(info.get("granted_balance")),
            "topped_up": _number(info.get("topped_up_balance")),
        })
    return {"ok": True, "kind": KIND_BALANCE, "items": items,
            "available": bool((payload or {}).get("is_available", bool(items)))}


async def _openrouter(endpoint: Endpoint, _billing: str) -> dict[str, Any]:
    payload = await _get(f"{_root(endpoint.base_url)}/v1/credits",
                         {"Authorization": f"Bearer {endpoint.api_key}"})
    data = (payload or {}).get("data") or {}
    total = _number(data.get("total_credits"))
    used = _number(data.get("total_usage"))
    if total is None:
        return _failed(N_("OpenRouter's answer had no credits in it."))
    return {"ok": True, "kind": KIND_BALANCE,
            "items": [{"currency": "USD", "amount": round(total - (used or 0.0), 6),
                       "bought": total, "used": used}]}


def _since() -> datetime:
    return datetime.now(UTC) - timedelta(days=30)


async def _openai_costs(endpoint: Endpoint, billing: str) -> dict[str, Any]:
    payload = await _get(
        f"{_root(endpoint.base_url)}/v1/organization/costs",
        {"Authorization": f"Bearer {billing}"},
        {"start_time": int(_since().timestamp()), "limit": 31},
    )
    totals: dict[str, float] = {}
    for bucket in (payload or {}).get("data") or []:
        for result in (bucket or {}).get("results") or []:
            amount = (result or {}).get("amount") or {}
            value = _number(amount.get("value"))
            if value is not None:
                code = str(amount.get("currency") or "usd").upper()[:3]
                totals[code] = totals.get(code, 0.0) + value
    return {"ok": True, "kind": KIND_SPENT,
            "items": [{"currency": c, "amount": round(v, 6)} for c, v in sorted(totals.items())]}


async def _anthropic_costs(endpoint: Endpoint, billing: str) -> dict[str, Any]:
    from lembas.services.llm.anthropic_client import ANTHROPIC_VERSION

    payload = await _get(
        f"{_root(endpoint.base_url)}/v1/organizations/cost_report",
        {"x-api-key": billing, "anthropic-version": ANTHROPIC_VERSION},
        {"starting_at": _since().strftime("%Y-%m-%dT00:00:00Z")},
    )
    totals: dict[str, float] = {}
    for bucket in (payload or {}).get("data") or []:
        for result in (bucket or {}).get("results") or []:
            # The cost report's amounts are decimal strings in cents.
            value = _number((result or {}).get("amount"))
            if value is not None:
                code = str((result or {}).get("currency") or "USD").upper()[:3]
                totals[code] = totals.get(code, 0.0) + value / 100
    return {"ok": True, "kind": KIND_SPENT,
            "items": [{"currency": c, "amount": round(v, 6)} for c, v in sorted(totals.items())]}


READERS = {
    providers.BALANCE_DEEPSEEK: _deepseek,
    providers.BALANCE_OPENROUTER: _openrouter,
    providers.BALANCE_OPENAI_COSTS: _openai_costs,
    providers.BALANCE_ANTHROPIC_COSTS: _anthropic_costs,
}


def available(provider_type: str | None) -> bool:
    return providers.get(provider_type).balance in READERS


async def read(endpoint: Endpoint, billing_key: str = "") -> dict[str, Any]:
    """The provider's own figure for this connection, with `checked_at`."""
    provider = providers.get(endpoint.provider_type)
    reader = READERS.get(provider.balance)
    if reader is None:
        found = _failed(N_("This provider has no balance to read."))
    elif provider.balance_needs_billing_key and not billing_key:
        found = _failed(N_("This needs an admin key from the provider (the billing key)."))
    else:
        try:
            found = await reader(endpoint, billing_key)
        except PermissionError as exc:
            found = _failed(str(exc))
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            log.info("balance for %s: %s", endpoint.base_url, exc)
            found = _failed(N_("The provider did not answer with a balance."))
    found["checked_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return found


__all__ = ["KIND_BALANCE", "KIND_SPENT", "available", "read"]
