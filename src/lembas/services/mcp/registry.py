"""Turning MCP servers into tools a chat can be offered.

Two names per tool. The server has its own, which is what `tools/call` must be
given; we have an *offered* name, which is what goes in the schema the endpoint
sees. They differ because two servers both exposing `search` would collide, a
server exposing `notes_delete` would shadow a built-in, and endpoints accept a
narrower character set than MCP does. The rename never leaves this module: the
runner closes over the server's own name.

The advertised list is cached on the row and refreshed by a button, the same
shape as discovering a connection's models. A server is contacted when an
administrator asks, not when a chat starts -- a slow server must not be able to
delay every reply.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import McpServer, User
from lembas.services import tool_access
from lembas.services.fetch import FetchError
from lembas.services.mcp import client
from lembas.services.mcp.protocol import McpError
from lembas.services.tools import FAMILY_MCP, RISK_WRITE, ToolContext, ToolDef, ToolOutcome

log = logging.getLogger(__name__)

# What an endpoint will accept as a function name.
FUNCTION_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
MAX_NAME = 64

# How much of a reply is kept on the message row for the transcript.
MAX_EVENT_CHARS = 2000
MAX_SUMMARY_CHARS = 200


def offer_name(server_slug: str, tool_name: str, *, taken: set[str]) -> str:
    """A name the endpoint will accept, unique across everything offered.

    Truncation can collide where the full names would not, so a numeric suffix
    is appended until it does not. Deterministic given a stable iteration order,
    which is why rows are walked in (position, slug) order everywhere.
    """
    combined = f"{server_slug}_{tool_name}".lower()
    cleaned = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_-]", "_", combined)).strip("_")
    candidate = (cleaned or "tool")[:MAX_NAME]

    suffix = 2
    while candidate in taken:
        tail = f"_{suffix}"
        candidate = f"{(cleaned or 'tool')[: MAX_NAME - len(tail)]}{tail}"
        suffix += 1

    taken.add(candidate)
    return candidate


def _summary(arguments: dict[str, Any]) -> str:
    parts = [f"{name}={value!r}" for name, value in arguments.items()]
    text = ", ".join(parts)
    return text[:MAX_SUMMARY_CHARS]


def _runner(spec: client.McpSpec, tool_name: str, offered: str):
    async def run(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        event = {
            "name": offered,
            "kind": "mcp",
            "label": f"{spec.name} · {tool_name}",
            "query": _summary(args),
            "detail": spec.name,
            "results": [],
        }
        try:
            text, failed = await client.call_tool(spec, tool_name, args)
        except (McpError, FetchError) as exc:
            message = exc.message
            log.info("mcp %s/%s failed: %s", spec.slug, tool_name, message)
            return ToolOutcome(
                f"The {tool_name} tool on {spec.name} failed: {message}",
                {**event, "status": "error", "error": message[:200]},
            )

        text = text.strip()[: spec.max_chars]
        if failed:
            return ToolOutcome(
                f"The tool reported an error:\n{text}" if text else "The tool reported an error.",
                {**event, "status": "error", "error": text[:200] or "The tool reported an error."},
            )
        if not text:
            return ToolOutcome(
                f"{tool_name} returned nothing.", {**event, "status": "ok", "text": ""}
            )
        return ToolOutcome(text, {**event, "status": "ok", "text": text[:MAX_EVENT_CHARS]})

    return run


def _offered_tools(server: McpServer) -> list[dict[str, Any]]:
    """The cached entries this server is currently willing to offer."""
    overrides = server.tool_overrides_json or {}
    # Absent means on, the rule the model capability flags follow: a tool that
    # appeared in the last refresh should work rather than silently do nothing.
    return [
        entry
        for entry in (server.tools_json or [])
        if isinstance(entry, dict) and overrides.get(entry.get("name"), True)
    ]


def tool_defs(
    db: DBSession, user: User | None, *, everything: bool = False, taken: set[str] | None = None
) -> list[ToolDef]:
    """One `ToolDef` per offerable tool across every visible server."""
    claimed = taken if taken is not None else set()
    out: list[ToolDef] = []

    for server in tool_access.visible_mcp_servers(db, user, everything=everything):
        spec = client.spec_from(server)
        for entry in _offered_tools(server):
            name = str(entry.get("name") or "")
            offered = str(entry.get("offer_name") or "") or offer_name(
                server.slug, name, taken=claimed
            )
            claimed.add(offered)
            if not FUNCTION_NAME.match(offered):
                continue
            out.append(
                ToolDef(
                    name=offered,
                    family=f"{FAMILY_MCP}:{server.slug}",
                    description=entry.get("description") or f"{name}, from {server.name}.",
                    parameters=entry.get("schema") or {"type": "object", "properties": {}},
                    run=_runner(spec, name, offered),
                    # Conservative, because nothing in tools/list says. A server
                    # calling something `search` may still be filing a ticket
                    # with it, and the cost of being wrong this way is a
                    # question nobody needed to answer.
                    risk=RISK_WRITE,
                )
            )

    return out


async def refresh(db: DBSession, server: McpServer) -> tuple[int, str]:
    """Contact a server and cache what it advertises. Returns (count, error).

    Shaped like `api/admin.py:_refresh_models`, including writing `last_error`
    and `last_checked_at` on both paths so the row says what happened rather
    than only whether it worked.
    """
    spec = client.spec_from(server)
    try:
        advertised, info, version = await client.list_tools(spec)
    except (McpError, FetchError) as exc:
        server.last_error = exc.message
        server.last_checked_at = datetime.now(UTC)
        db.commit()
        return 0, exc.message

    taken: set[str] = set()
    for entry in advertised:
        entry["offer_name"] = offer_name(server.slug, entry["name"], taken=taken)

    # Choices about tools that are still advertised survive; ones about tools
    # that have gone are dropped rather than left to accumulate.
    names = {entry["name"] for entry in advertised}
    server.tool_overrides_json = {
        name: on for name, on in (server.tool_overrides_json or {}).items() if name in names
    }

    server.tools_json = advertised
    server.server_info = info
    server.protocol_version = version
    server.last_error = ""
    server.last_checked_at = datetime.now(UTC)
    db.commit()

    log.info("mcp %s advertised %d tool(s)", server.slug, len(advertised))
    return len(advertised), ""


__all__ = ["offer_name", "refresh", "tool_defs"]
