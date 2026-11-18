"""Provider types: what a connection is, and everything that follows from it.

A connection has a **type** (`Connection.provider_type`) instead of a dialect,
so the differences between providers are picked up rather than configured:
which protocol it speaks, where it lives by default, how it reports tokens and
cache hits, how a reasoning effort is asked for, where its balance is read,
and whether it reports what a reply cost. The first set was five; Alibaba
Cloud Model Studio's two came next.

| type | protocol | reasoning effort sent as | balance | cost in `usage` |
|---|---|---|---|---|
| `openai_compatible` | OpenAI chat | `reasoning_effort` and `chat_template_kwargs` | -- | -- |
| `openai` | OpenAI chat | `reasoning_effort` | costs API, admin key | -- |
| `anthropic` | Anthropic messages | thinking (`anthropic_client`) | cost report, admin key | -- |
| `deepseek` | OpenAI chat | -- (the model decides) | `GET /user/balance` | -- |
| `openrouter` | OpenAI chat | `reasoning: {effort}` | `GET /api/v1/credits` | `usage.cost` |
| `alibaba` | OpenAI chat | `enable_thinking` and `reasoning_effort` | -- | -- |
| `alibaba_plan` | OpenAI chat | the same | -- | -- (a subscription) |
| `comfyui` | -- (no chat: pictures) | -- | -- | -- |
| `speech` | -- (no chat: OpenAI audio routes) | -- | -- | -- |

`openai_compatible` is everything local or self-hosted -- llama.cpp,
llama-swap, vLLM, Ollama, LM Studio -- and any cloud that copies OpenAI's API
without being one of the others. It is what an older row (`kind = openai`)
becomes, and the default.

**Alibaba Cloud Model Studio** (Qwen and the models it resells) is two types
because it is two products with keys and addresses that do not cross:
`alibaba` is pay-as-you-go (the DashScope hosts and the workspace hosts,
`…/compatible-mode/v1`), `alibaba_plan` is a subscription -- Token Plan
(`token-plan.<region>.maas.aliyuncs.com`) or Coding Plan -- whose keys start
`sk-sp-`. Both speak OpenAI chat with a few fields of their own:

- thinking is switched with a **top-level** `enable_thinking`, where llama.cpp
  reads `chat_template_kwargs.enable_thinking`; an effort is `reasoning_effort`
  on the models that take one. Reasoning comes back as `reasoning_content`.
- cached prompt tokens are `usage.prompt_tokens_details.cached_tokens`, and
  tokens written to an explicit cache `…cache_creation_input_tokens`
  (`cache_write_tokens` on the newest models).
- neither has a balance to read with a model key, and neither says what a
  reply cost.

A plan is **interactive only** by its terms: no automated service, batch
script or scheduled background job. That is a property of the type
(`interactive_only`), read through `interactive_only()` -- which also knows a
plan's host under any other type, so the Anthropic-shaped address of a plan
(`…/apps/anthropic`) is held to the same rule. `services/interactive.py` is
what acts on it. A plan is also a **subscription**: its replies are counted
and never priced.
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
ALIBABA = "alibaba"
ALIBABA_PLAN = "alibaba_plan"
# Two types that serve no chat model at all: a ComfyUI (pictures, by workflow
# and checkpoint) and an OpenAI-compatible speech server (transcriptions and
# speech). They are connections like the rest -- an address, a key, health --
# and what they offer are models with a capability (`services/media/`).
COMFYUI = "comfyui"
SPEECH = "speech"

# How a reasoning effort reaches the provider.
EFFORT_BOTH = "both"  # `reasoning_effort` and `chat_template_kwargs.reasoning_effort`
EFFORT_FIELD = "field"  # `reasoning_effort` only
EFFORT_NONE = "none"  # not sent: the model decides (DeepSeek's reasoner)
EFFORT_OPENROUTER = "openrouter"  # `reasoning: {"effort": …}`
EFFORT_ANTHROPIC = "anthropic"  # thinking, translated by `anthropic_client`
EFFORT_ALIBABA = "alibaba"  # top-level `enable_thinking`, and `reasoning_effort`

# Where a balance or a spend is read.
BALANCE_NONE = ""
BALANCE_DEEPSEEK = "deepseek"  # GET /user/balance, the ordinary key
BALANCE_OPENROUTER = "openrouter"  # GET /api/v1/credits, the ordinary key
BALANCE_OPENAI_COSTS = "openai_costs"  # GET /v1/organization/costs, an admin key
BALANCE_ANTHROPIC_COSTS = "anthropic_costs"  # GET /v1/organizations/cost_report, admin key


@dataclass(frozen=True)
class Region:
    """One address a type is reached at, for the form's region choice. A
    `base_url` with `{WorkspaceId}` in it is a pattern the administrator
    completes; it is refused as it stands."""

    id: str
    label: str
    base_url: str


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
    # The provider's terms allow a person's own turns and nothing else: no
    # scheduled run, no background call. Read through `interactive_only()`.
    interactive_only: bool = False
    # Paid for as a subscription, not per token: replies are never priced.
    subscription: bool = False
    # The addresses the form offers; the first is `default_base_url`.
    regions: tuple[Region, ...] = ()
    # What this type's keys start with, and what they never start with: the
    # form warns on a key that looks like another product's.
    key_prefix: str = ""
    key_not_prefix: str = ""
    # Whether the type serves chat models. False for the ComfyUI and the
    # speech server: nothing lists `/models` on them, no chat can start on
    # them, and the health round asks them their own question.
    chat: bool = True


# Alibaba Cloud Model Studio. The addresses are the provider's own (its "Base
# URL overview"); a key works only with the address of its own product and
# region. Frankfurt and Tokyo have no shared host: only the workspace's own.
PLAN_KEY_PREFIX = "sk-sp-"
_COMPATIBLE = "/compatible-mode/v1"
ALIBABA_HOSTS = (
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
    "dashscope-us.aliyuncs.com",
    "maas.aliyuncs.com",
    # The provider's newer international name for the same service.
    "maas.qwencloudapi.com",
)
ALIBABA_REGIONS = (
    Region("ap-southeast-1", N_("Singapore (international)"),
           f"https://dashscope-intl.aliyuncs.com{_COMPATIBLE}"),
    Region("cn-beijing", N_("China (Beijing)"),
           f"https://dashscope.aliyuncs.com{_COMPATIBLE}"),
    Region("us-east-1", N_("US (Virginia)"),
           f"https://dashscope-us.aliyuncs.com{_COMPATIBLE}"),
    Region("cn-hongkong", N_("China (Hong Kong)"),
           f"https://cn-hongkong.dashscope.aliyuncs.com{_COMPATIBLE}"),
    Region("eu-central-1", N_("Germany (Frankfurt), your workspace's host"),
           "https://{WorkspaceId}.eu-central-1.maas.aliyuncs.com" + _COMPATIBLE),
    Region("ap-northeast-1", N_("Japan (Tokyo), your workspace's host"),
           "https://{WorkspaceId}.ap-northeast-1.maas.aliyuncs.com" + _COMPATIBLE),
    # QwenCloud: the provider's newer console and names for the same service,
    # one host per region. A key from one region does not work in another.
    Region("qwencloud-ap-southeast-1", N_("QwenCloud, Singapore"),
           f"https://ap-southeast-1.maas.qwencloudapi.com{_COMPATIBLE}"),
    Region("qwencloud-us-east-1", N_("QwenCloud, US (Virginia)"),
           f"https://us-east-1.maas.qwencloudapi.com{_COMPATIBLE}"),
    Region("qwencloud-eu-central-1", N_("QwenCloud, Germany (Frankfurt)"),
           f"https://eu-central-1.maas.qwencloudapi.com{_COMPATIBLE}"),
    Region("qwencloud-ap-northeast-1", N_("QwenCloud, Japan (Tokyo)"),
           f"https://ap-northeast-1.maas.qwencloudapi.com{_COMPATIBLE}"),
    Region("qwencloud-cn-hongkong", N_("QwenCloud, China (Hong Kong)"),
           f"https://cn-hongkong.maas.qwencloudapi.com{_COMPATIBLE}"),
    Region("qwencloud-cn-beijing", N_("QwenCloud, China (Beijing)"),
           f"https://cn-beijing.maas.qwencloudapi.com{_COMPATIBLE}"),
)
ALIBABA_PLAN_REGIONS = (
    Region("ap-southeast-1", N_("Token Plan, Singapore"),
           f"https://token-plan.ap-southeast-1.maas.aliyuncs.com{_COMPATIBLE}"),
    Region("cn-beijing", N_("Token Plan, China (Beijing)"),
           f"https://token-plan.cn-beijing.maas.aliyuncs.com{_COMPATIBLE}"),
    # QwenCloud's Token Plan: one host, with no region in it.
    Region("qwencloud", N_("Token Plan, QwenCloud"),
           f"https://token-plan.maas.qwencloudapi.com{_COMPATIBLE}"),
    Region("coding-intl", N_("Coding Plan (international)"),
           "https://coding-intl.dashscope.aliyuncs.com/v1"),
)
# The hosts of a plan that is not Token Plan's `token-plan.<region>` pattern.
_PLAN_HOSTS = ("coding-intl.dashscope.aliyuncs.com", "coding.dashscope.aliyuncs.com")


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
        ProviderType(
            ALIBABA, N_("Alibaba Cloud Model Studio (pay-as-you-go)"), PROTOCOL_OPENAI,
            ALIBABA_REGIONS[0].base_url, ALIBABA_HOSTS, EFFORT_ALIBABA, BALANCE_NONE,
            False, False, False,
            regions=ALIBABA_REGIONS, key_not_prefix=PLAN_KEY_PREFIX,
        ),
        ProviderType(
            ALIBABA_PLAN, N_("Alibaba Cloud Model Studio (Token Plan, Coding Plan)"),
            PROTOCOL_OPENAI, ALIBABA_PLAN_REGIONS[0].base_url, (), EFFORT_ALIBABA,
            BALANCE_NONE, False, False, False,
            interactive_only=True, subscription=True,
            regions=ALIBABA_PLAN_REGIONS, key_prefix=PLAN_KEY_PREFIX,
        ),
        ProviderType(
            COMFYUI, "ComfyUI", PROTOCOL_OPENAI, "", (), EFFORT_NONE, BALANCE_NONE,
            False, False, True, chat=False,
        ),
        ProviderType(
            SPEECH, N_("Speech server (OpenAI-compatible transcriptions and speech)"),
            PROTOCOL_OPENAI, "", (), EFFORT_NONE, BALANCE_NONE, False, False, True,
            chat=False,
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


def serves_chat(type_id: str | None) -> bool:
    """Whether a connection of this type has chat models at all."""
    return get(type_id).chat


def _host(url: str | None) -> str:
    try:
        # A trailing dot is the same host, spelled out to its root.
        return (urlsplit((url or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def plan_host(url: str | None) -> bool:
    """Whether a URL is on a subscription plan's host (Alibaba's Token Plan or
    Coding Plan), whichever of the plan's two API shapes its path is."""
    host = _host(url)
    return host in _PLAN_HOSTS or (
        host.startswith("token-plan.")
        # `token-plan.<region>.maas.aliyuncs.com`, and the newer name's one
        # host with no region in it: `token-plan.maas.qwencloudapi.com`.
        and host.endswith((".maas.aliyuncs.com", ".maas.qwencloudapi.com"))
    )


def interactive_only(type_id: str | None, base_url: str | None = "") -> bool:
    """Whether a connection may serve a person's own turns and nothing else.
    The type says so; so does a plan's host under another type (the plan's
    Anthropic-shaped address, set up as an Anthropic connection)."""
    return get(type_id).interactive_only or plan_host(base_url)


def subscription(type_id: str | None, base_url: str | None = "") -> bool:
    """Whether a connection is paid for as a subscription, so that pricing a
    reply per token would invent a figure."""
    return get(type_id).subscription or plan_host(base_url)


def key_mismatch(type_id: str | None, api_key: str | None) -> bool:
    """Whether a key plainly belongs to another product than this type: a
    plan's key on the pay-as-you-go type, or the other way round. An empty key
    is not a mismatch."""
    key = (api_key or "").strip()
    provider = get(type_id)
    if not key:
        return False
    if provider.key_prefix and not key.startswith(provider.key_prefix):
        return True
    return bool(provider.key_not_prefix) and key.startswith(provider.key_not_prefix)


def base_url_for(provider: ProviderType, url: str) -> str:
    """The base URL a pasted address means for a type with regions: the address
    itself with its path, or with the type's path when only a host was pasted."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    if not path:
        path = "/v1" if parts.hostname in _PLAN_HOSTS else _COMPATIBLE
    return f"{parts.scheme}://{parts.netloc}{path}"


def by_host(url: str) -> ProviderType | None:
    """The type a URL names by its host alone (`api.deepseek.com`), or None."""
    host = _host(url)
    if not host:
        return None
    if plan_host(url):
        return TYPES[ALIBABA_PLAN]
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
    if provider.effort == EFFORT_ALIBABA:
        # Model Studio reads the thinking switch at the top level of the body,
        # not in the template's arguments, and an effort where a model takes
        # one. An effort is a request to think, so it says so: a model whose
        # thinking is off by default would otherwise ignore it.
        switch = kwargs.get("enable_thinking")
        out.pop("chat_template_kwargs", None)
        if effort:
            out["reasoning_effort"] = effort
            out["enable_thinking"] = True
        elif isinstance(switch, bool):
            out["enable_thinking"] = switch
        return out
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
    OpenAI's `prompt_tokens_details.cached_tokens` (Alibaba's too), the same at
    the top level, DeepSeek's `prompt_cache_hit_tokens`. None when the usage
    does not say."""
    details = raw_usage.get("prompt_tokens_details")
    for value in (
        details.get("cached_tokens") if isinstance(details, dict) else None,
        raw_usage.get("cached_tokens"),
        raw_usage.get("prompt_cache_hit_tokens"),
    ):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return int(value)
    return None


def cache_write_tokens(raw_usage: dict[str, Any]) -> int | None:
    """Prompt tokens written to the provider's cache, which cost more than
    ordinary input: `cache_write_tokens` (the Anthropic translation, and
    Alibaba's newest models) or Alibaba's `cache_creation_input_tokens`, in
    `prompt_tokens_details` or at the top level. The two names are one figure
    and are never added. None when the usage does not say."""
    details = raw_usage.get("prompt_tokens_details")
    sources = [details] if isinstance(details, dict) else []
    sources.append(raw_usage)
    said = None
    for source in sources:
        for key in ("cache_write_tokens", "cache_creation_input_tokens"):
            value = source.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                # The first that says a number above nothing: a zero in one
                # place must not hide the figure in another.
                if value > 0:
                    return int(value)
                said = 0
    return said


def quota_exhausted(
    type_id: str | None, base_url: str | None, status_code: int | None, body: Any
) -> bool:
    """Whether an error says a **subscription's** quota is used up: a 429 whose
    code is `insufficient_quota`, from a plan. Only from a plan: pay-as-you-go
    uses the same code for an ordinary rate limit, which passes."""
    if status_code != 429 or not subscription(type_id, base_url):
        return False
    error = body.get("error") if isinstance(body, dict) else None
    fields = [body] if isinstance(body, dict) else []
    if isinstance(error, dict):
        fields.append(error)
    for source in fields:
        for key in ("code", "type", "message"):
            if "insufficient_quota" in str(source.get(key) or ""):
                return True
    return False


# Shown through `t()` where it is raised.
QUOTA_EXHAUSTED = N_(
    "The plan's quota is used up. The provider does not fall back to pay-as-you-go: "
    "wait for the plan's next month, add to the plan, or choose a model on another "
    "connection."
)


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
    "ALIBABA",
    "ALIBABA_PLAN",
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
    "QUOTA_EXHAUSTED",
    "Region",
    "base_url_for",
    "by_host",
    "cache_write_tokens",
    "cached_tokens",
    "from_legacy_kind",
    "get",
    "interactive_only",
    "key_mismatch",
    "known",
    "plan_host",
    "protocol_of",
    "quota_exhausted",
    "reported_cost_micro",
    "shape_request",
    "subscription",
]
