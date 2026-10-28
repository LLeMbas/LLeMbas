"""Provider types: what a connection is, and everything that follows from it.

A connection has a **type** (`Connection.provider_type`) instead of a dialect,
so the differences between providers are picked up rather than configured:
which protocol it speaks, where it lives by default, how it reports tokens and
cache hits, how a reasoning effort is asked for, where its balance is read,
and whether it reports what a reply cost. The first set is five; more types
later are a feature release.

| type | protocol | reasoning effort sent as | balance | cost in `usage` |
|---|---|---|---|---|
| `openai_compatible` | OpenAI chat | `reasoning_effort` and `chat_template_kwargs` | -- | -- |
| `openai` | OpenAI chat | `reasoning_effort` | costs API, admin key | -- |
| `anthropic` | Anthropic messages | thinking (`anthropic_client`) | cost report, admin key | -- |
| `deepseek` | OpenAI chat | -- (the model decides) | `GET /user/balance` | -- |
| `openrouter` | OpenAI chat | `reasoning: {effort}` | `GET /api/v1/credits` | `usage.cost` |

`openai_compatible` is everything local or self-hosted -- llama.cpp,
llama-swap, vLLM, Ollama, LM Studio -- and any cloud that copies OpenAI's API
without being one of the others. It is what an older row (`kind = openai`)
becomes, and the default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from lembas.web.i18n import N_

PROTOCOL_OPENAI = "openai"
PROTOCOL_ANTHROPIC = "anthropic"

OPENAI_COMPATIBLE = "openai_compatible"
OPENAI = "openai"
ANTHROPIC = "anthropic"
DEEPSEEK = "deepseek"
OPENROUTER = "openrouter"

# How a reasoning effort reaches the provider.
EFFORT_BOTH = "both"  # `reasoning_effort` and `chat_template_kwargs.reasoning_effort`
EFFORT_FIELD = "field"  # `reasoning_effort` only
EFFORT_NONE = "none"  # not sent: the model decides (DeepSeek's reasoner)
EFFORT_OPENROUTER = "openrouter"  # `reasoning: {"effort": …}`
EFFORT_ANTHROPIC = "anthropic"  # thinking, translated by `anthropic_client`

# Where a balance or a spend is read.
BALANCE_NONE = ""
BALANCE_DEEPSEEK = "deepseek"  # GET /user/balance, the ordinary key
BALANCE_OPENROUTER = "openrouter"  # GET /api/v1/credits, the ordinary key
BALANCE_OPENAI_COSTS = "openai_costs"  # GET /v1/organization/costs, an admin key
BALANCE_ANTHROPIC_COSTS = "anthropic_costs"  # GET /v1/organizations/cost_report, admin key


@dataclass(frozen=True)
class ProviderType:
    id: str
    # Shown through `t()`: the generic type's label is a sentence, the others
    # are names and stay as they are.
    label: str
    protocol: str
    default_base_url: str
    # Hosts that are this provider and nothing else: a pasted URL on one of
    # them is this type without a request being made.
    hosts: tuple[str, ...]
    effort: str
    balance: str
    # Whether the balance needs a separate admin ("billing") key.
    balance_needs_billing_key: bool
    # Whether `usage` carries what the reply cost (OpenRouter's `usage.cost`).
    reports_cost: bool
    # Whether the endpoint can be asked about its server and models without a
    # key (a local server), so capabilities are probed beyond `/v1/models`.
    local: bool


TYPES: dict[str, ProviderType] = {
    t.id: t
    for t in (
        ProviderType(
            OPENAI_COMPATIBLE,
            N_("OpenAI-compatible (llama.cpp, llama-swap, vLLM, Ollama, LM Studio)"),
            PROTOCOL_OPENAI, "", (), EFFORT_BOTH, BALANCE_NONE, False, False, True,
        ),
        ProviderType(
            OPENAI, "OpenAI", PROTOCOL_OPENAI, "https://api.openai.com/v1",
            ("api.openai.com",), EFFORT_FIELD, BALANCE_OPENAI_COSTS, True, False, False,
        ),
        ProviderType(
            ANTHROPIC, "Anthropic", PROTOCOL_ANTHROPIC, "https://api.anthropic.com",
            ("api.anthropic.com",), EFFORT_ANTHROPIC, BALANCE_ANTHROPIC_COSTS, True, False,
            False,
        ),
        ProviderType(
            DEEPSEEK, "DeepSeek", PROTOCOL_OPENAI, "https://api.deepseek.com",
            ("api.deepseek.com",), EFFORT_NONE, BALANCE_DEEPSEEK, False, False, False,
        ),
        ProviderType(
            OPENROUTER, "OpenRouter", PROTOCOL_OPENAI, "https://openrouter.ai/api/v1",
            ("openrouter.ai",), EFFORT_OPENROUTER, BALANCE_OPENROUTER, False, True, False,
        ),
    )
}
DEFAULT = OPENAI_COMPATIBLE
TYPE_IDS = tuple(TYPES)


def get(type_id: str | None) -> ProviderType:
    """The type, or the default for anything this release does not know."""
    return TYPES.get((type_id or "").strip().lower(), TYPES[DEFAULT])


def known(type_id: str | None) -> str:
    return get(type_id).id


def protocol_of(type_id: str | None) -> str:
    return get(type_id).protocol


def by_host(url: str) -> ProviderType | None:
    """The type a URL names by its host alone (`api.deepseek.com`), or None."""
    try:
        host = (urlsplit(url.strip()).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    for provider in TYPES.values():
        if any(host == known or host.endswith("." + known) for known in provider.hosts):
            return provider
    return None


def from_legacy_kind(kind: str | None, base_url: str = "") -> str:
    """A row from before types: `anthropic` stays Anthropic, a known cloud host
    is its own type, everything else is OpenAI-compatible."""
    if (kind or "") == PROTOCOL_ANTHROPIC:
        return ANTHROPIC
    found = by_host(base_url)
    return found.id if found is not None else OPENAI_COMPATIBLE


# --- The request ---------------------------------------------------------------
def shape_request(type_id: str | None, body: dict[str, Any]) -> dict[str, Any]:
    """The OpenAI-shaped body as this provider wants it. Callers build one shape
    (`chat.apply_effort` writes the effort in both of its forms); this moves the
    effort to where the provider reads it and drops what it would refuse.
    Returns a new dict; the caller's is not touched."""
    provider = get(type_id)
    if provider.protocol != PROTOCOL_OPENAI or provider.effort == EFFORT_BOTH:
        return body
    out = dict(body)
    kwargs = dict(out.get("chat_template_kwargs") or {})
    effort = out.get("reasoning_effort") or kwargs.get("reasoning_effort")
    # The template's copy is llama.cpp's; nobody else reads it, and OpenAI
    # refuses an argument it does not know.
    kwargs.pop("reasoning_effort", None)
    if kwargs:
        out["chat_template_kwargs"] = kwargs
    else:
        out.pop("chat_template_kwargs", None)
    if provider.effort == EFFORT_NONE:
        out.pop("reasoning_effort", None)
    elif provider.effort == EFFORT_OPENROUTER:
        out.pop("reasoning_effort", None)
        if effort:
            out["reasoning"] = {**(out.get("reasoning") or {}), "effort": effort}
    if provider.reports_cost:
        # OpenRouter's usage accounting: the reply's cost in the last chunk.
        out["usage"] = {**(out.get("usage") or {}), "include": True}
    return out


# --- What a reply says it used and cost ----------------------------------------------
def cached_tokens(raw_usage: dict[str, Any]) -> int | None:
    """Prompt tokens served from the provider's cache, wherever it puts them:
    OpenAI's `prompt_tokens_details.cached_tokens`, the same at the top level,
    DeepSeek's `prompt_cache_hit_tokens`. None when the usage does not say."""
    details = raw_usage.get("prompt_tokens_details")
    for value in (
        details.get("cached_tokens") if isinstance(details, dict) else None,
        raw_usage.get("cached_tokens"),
        raw_usage.get("prompt_cache_hit_tokens"),
    ):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return int(value)
    return None


def reported_cost_micro(raw_usage: dict[str, Any]) -> int | None:
    """What the provider says the reply cost, in micro-units of its currency
    (OpenRouter: `usage.cost`, in US dollars). None when it does not say."""
    value = raw_usage.get("cost")
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return round(float(value) * 1_000_000)
    return None


# The currency a provider reports its own cost in, when it does.
REPORTED_CURRENCY = {OPENROUTER: "USD"}


__all__ = [
    "ANTHROPIC",
    "DEEPSEEK",
    "DEFAULT",
    "OPENAI",
    "OPENAI_COMPATIBLE",
    "OPENROUTER",
    "PROTOCOL_ANTHROPIC",
    "PROTOCOL_OPENAI",
    "REPORTED_CURRENCY",
    "TYPES",
    "TYPE_IDS",
    "ProviderType",
    "by_host",
    "cached_tokens",
    "from_legacy_kind",
    "get",
    "known",
    "protocol_of",
    "reported_cost_micro",
    "shape_request",
]
