"""Is a connection answering: one cheap request, timed, and kept on the row.

`check` asks the connection for its model list (llama-swap: its `/running`
list first, which also says whether a model is being swapped in) and records
`Connection.health_json`:

- `status`: `ok` (it answered), `loading` (llama-swap is starting a model -- a
  request now waits for it), `down` (unreachable or timed out), `error` (it
  answered with an error: a refused key, a 5xx);
- `latency_ms`, the time to the answer; `checked_at` (ISO, UTC); `error`, the
  sentence when there is one; `loading`, the model being started.

Honest rather than clever: "loading" is said only where the server says so
(llama-swap's `state`), never guessed from a slow answer. The ticker checks
every enabled connection every `INTERVAL` (`tick`); the admin's *Test* button
checks one at once. A check never raises.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from lembas.services.llm.openai_client import Endpoint, LLMError, list_models

log = logging.getLogger(__name__)

INTERVAL = 300.0  # seconds between the ticker's rounds
TIMEOUT = 10.0

STATUS_OK = "ok"
STATUS_LOADING = "loading"
STATUS_DOWN = "down"
STATUS_ERROR = "error"
STATUSES = (STATUS_OK, STATUS_LOADING, STATUS_DOWN, STATUS_ERROR)

# llama-swap's words for a model on its way in.
_STARTING = ("starting", "loading")


def _root(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


async def _llama_swap(endpoint: Endpoint) -> dict[str, Any] | None:
    """llama-swap's `/running`, when the connection is one: whether a model is
    being started. None for anything else."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            response = await client.get(
                f"{_root(endpoint.base_url)}/running",
                headers={k: v for k, v in endpoint.headers().items() if k != "Content-Type"},
            )
        payload = response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("running"), list):
        return None
    starting = [
        str(item.get("model") or "")
        for item in payload["running"]
        if isinstance(item, dict) and str(item.get("state") or "").lower() in _STARTING
    ]
    return {"loading": starting[0] if starting else ""}


async def probe(endpoint: Endpoint, server: str = "") -> dict[str, Any]:
    """The health of one endpoint, as `health_json` keeps it (less `balance`)."""
    started = time.monotonic()
    found: dict[str, Any] = {"checked_at": datetime.now(UTC).isoformat(timespec="seconds")}
    from lembas.services.llm import providers

    local = providers.get(endpoint.provider_type).local and not endpoint.is_anthropic
    swap = await _llama_swap(endpoint) if local and server in ("", "llama-swap") else None
    try:
        await list_models(endpoint)
    except LLMError as exc:
        found["latency_ms"] = round((time.monotonic() - started) * 1000)
        found["status"] = STATUS_DOWN if exc.status_code is None else STATUS_ERROR
        found["error"] = exc.message[:300]
        return found
    found["latency_ms"] = round((time.monotonic() - started) * 1000)
    if swap is not None and swap["loading"]:
        found["status"] = STATUS_LOADING
        found["loading"] = swap["loading"][:200]
    else:
        found["status"] = STATUS_OK
    found["error"] = ""
    return found


def keep(connection, found: dict[str, Any]) -> None:
    """A probe's answer onto the row, keeping what the probe does not own
    (`server`, `balance`)."""
    kept = {
        key: value
        for key, value in (connection.health_json or {}).items()
        if key in ("server", "balance")
    }
    connection.health_json = {**kept, **found}


async def check(db, connection) -> dict[str, Any]:
    """Probe one connection now and record it. Commits."""
    found = await probe(
        Endpoint.from_connection(connection), str((connection.health_json or {}).get("server", ""))
    )
    keep(connection, found)
    db.commit()
    return connection.health_json


_LAST_TICK = 0.0


async def tick(*, force: bool = False) -> int:
    """The ticker's round: every enabled connection, at most once an
    `INTERVAL`. Returns how many were checked. Never raises."""
    global _LAST_TICK
    now = time.monotonic()
    if not force and _LAST_TICK and now - _LAST_TICK < INTERVAL:
        return 0
    _LAST_TICK = now
    from sqlalchemy import select

    from lembas.db.models import Connection
    from lembas.db.session import session_scope

    checked = 0
    try:
        with session_scope() as db:
            targets = [
                (c.id, Endpoint.from_connection(c), str((c.health_json or {}).get("server", "")))
                for c in db.scalars(select(Connection).where(Connection.enabled.is_(True)))
            ]
        for connection_id, endpoint, server in targets:
            found = await probe(endpoint, server)
            with session_scope() as db:
                connection = db.get(Connection, connection_id)
                if connection is not None:
                    keep(connection, found)
                    db.commit()
                    checked += 1
    except Exception:  # health is a display; it must never stop the ticker
        log.exception("the connection health round raised")
    return checked


def reset() -> None:
    global _LAST_TICK
    _LAST_TICK = 0.0


__all__ = ["INTERVAL", "STATUSES", "check", "keep", "probe", "reset", "tick"]
