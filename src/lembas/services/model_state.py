"""Which models are loaded right now, where the endpoint is able to say.

llama-swap holds one model at a time and reports which, inside the ordinary
`GET /v1/models` answer: every entry carries `"status": {"value": "loaded"}`
or `"unloaded"`. Choosing a model that is not loaded costs a load (seconds for
a small one, most of a minute for the 26B), so the model menu shows a dot on
the one that is ready.

**Only what an endpoint states, and nothing inferred.** The OpenAI spec has
no such field. A hosted API such as DeepSeek leaves it out because nothing is
ever unloaded there, so its models get no state and no dot, rather than a
guess dressed up as a reading. The same shape covers the next runner that
reports it: `status` as an object with `value`, or as a bare string.

**Cheap by construction**, because the menu asks every time it opens:

- one `/v1/models` per *connection*, not per model, all at once;
- a short timeout, because a slow endpoint must never hold up a menu;
- five seconds of cache per connection, so opening the menu repeatedly costs
  one request;
- and ten minutes for a connection that said nothing about state, so a hosted
  API is not asked for its model list on every click only to answer nothing
  again.

Process-level, like the branding cache. With several workers each keeps its
own, which costs at most one extra request each and cannot be wrong for longer
than the TTL.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from lembas.services.llm.openai_client import Endpoint, list_models

log = logging.getLogger(__name__)

TIMEOUT = 3.0
TTL = 5.0
TTL_SILENT = 600.0

LOADED = "loaded"
LOADING = "loading"
UNLOADED = "unloaded"

_LOADED_WORDS = frozenset({"loaded", "ready", "running"})
_LOADING_WORDS = frozenset({"loading", "starting"})

# connection id -> (monotonic time read, TTL, {model_id: state})
_CACHE: dict[str, tuple[float, float, dict[str, str]]] = {}


def state_of(entry: dict[str, Any]) -> str:
    """One `/v1/models` entry's state, or "" when it states none."""
    status = entry.get("status")
    value = status.get("value") if isinstance(status, dict) else status
    if not isinstance(value, str) or not value.strip():
        return ""
    word = value.strip().lower()
    if word in _LOADED_WORDS:
        return LOADED
    if word in _LOADING_WORDS:
        return LOADING
    return UNLOADED


async def _read(connection) -> dict[str, str]:
    now = time.monotonic()
    cached = _CACHE.get(connection.id)
    if cached and now - cached[0] < cached[1]:
        return cached[2]
    try:
        entries = await asyncio.wait_for(
            list_models(Endpoint.from_connection(connection)), TIMEOUT
        )
    except Exception:  # an unreachable endpoint has no state, not an error page
        log.debug("model state unavailable for %s", connection.name, exc_info=True)
        # Not cached: the next open asks again, which is right for an endpoint
        # that is merely starting up.
        return {}
    states = {entry["id"]: state for entry in entries if (state := state_of(entry))}
    _CACHE[connection.id] = (now, TTL if states else TTL_SILENT, states)
    return states


async def states_for(models) -> dict[str, str]:
    """`{model_id: state}` for the models whose endpoint reports one.

    Models without a stated state are absent, not `""`, so the page can treat
    "no key" as "draw nothing".
    """
    connections = {}
    for model in models:
        connection = getattr(model, "connection", None)
        if connection is not None and connection.enabled:
            connections[connection.id] = connection
    if not connections:
        return {}
    results = await asyncio.gather(*(_read(c) for c in connections.values()))
    by_connection = dict(zip(connections, results, strict=True))
    out: dict[str, str] = {}
    for model in models:
        state = by_connection.get(model.connection_id, {}).get(model.model_id)
        if state:
            out[model.model_id] = state
    return out


def forget() -> None:
    """Drop the cache. For tests."""
    _CACHE.clear()
