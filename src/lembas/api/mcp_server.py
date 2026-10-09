"""`/mcp`: a person's library, served to a logged-in device over MCP.

The LLeMbas CLI's `library: lembas` -- or any MCP client -- reaches the
holder's memory, notes, skills and knowledge here, with a token carrying the
`library` scope. The tools are LLeMbas's own library tools, the same definitions
and the same runners a chat uses, so a model sees the same tools either way and
a fix to one is a fix to both.

**What a person may do in a chat is what a device may do here**: the families are
gated by the same permissions (`library.use`, `tools.<family>`, and the split
write permissions), checked on every call. The data group is the person's
default one -- a device is not a chat, and has no connection to pick a group by.

Streamable HTTP in its plainest form: one JSON-RPC message per POST, answered
with one JSON response (no server-to-client stream, so `GET` is 405). Methods:
`initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from lembas import __version__
from lembas.api.deps import Db
from lembas.security.api_tokens import SCOPE_LIBRARY, Caller, require_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSION = "2025-06-18"
LIBRARY_FAMILIES = ("memory", "notes", "skills", "knowledge")

_library = require_token(SCOPE_LIBRARY)


def library_tools(db, user) -> dict[str, Any]:
    """The library tools this person may use now, by name."""
    from lembas.security import permissions
    from lembas.services import tools as tools_service

    allowed = permissions.resolve(db, user)
    if not allowed.get("library.use"):
        return {}
    out = {}
    for name, tool in tools_service.REGISTRY.items():
        gate = tools_service.gate_of(tool.family)
        if gate not in LIBRARY_FAMILIES or not allowed.get(f"tools.{gate}"):
            continue
        if (
            tool.risk == tools_service.RISK_WRITE
            and gate in permissions.SPLIT_GATES
            and not allowed.get(f"tools.{gate}.write", True)
        ):
            continue
        out[name] = tool
    return out


def _reply(
    message_id: Any, result: Any = None, error: tuple[int, str] | None = None
) -> JSONResponse:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        body["error"] = {"code": error[0], "message": error[1]}
    else:
        body["result"] = result
    return JSONResponse(body)


@router.get("/mcp")
async def no_stream() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp")
async def mcp(request: Request, db: Db) -> Response:
    caller: Caller = _library(request, db)
    try:
        message = await request.json()
    except ValueError:
        return _reply(None, error=(-32700, "Parse error"))
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _reply(None, error=(-32600, "Invalid request"))
    method = str(message.get("method") or "")
    message_id = message.get("id")
    params = message.get("params") or {}

    if message_id is None:  # a notification: nothing to answer
        return Response(status_code=202)
    if method == "initialize":
        return _reply(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "lembas", "version": __version__},
                "instructions": (
                    "The memory, notes, skills and knowledge of the person signed in, "
                    "in their default data group."
                ),
            },
        )
    if method == "ping":
        return _reply(message_id, {})
    if method == "tools/list":
        tools = library_tools(db, caller.user)
        return _reply(
            message_id,
            {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.parameters,
                        "annotations": {"readOnlyHint": tool.risk != "write"},
                    }
                    for tool in tools.values()
                ]
            },
        )
    if method == "tools/call":
        from lembas.services import audit
        from lembas.services import tools as tools_service

        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        tools = library_tools(db, caller.user)
        if name not in tools:
            return _reply(
                message_id, error=(-32602, f"There is no tool {name!r} for this account.")
            )
        context = tools_service.context_for(db, caller.user, None)
        context.tools = tools
        outcome = await tools_service.run_tool(
            context, name, json.dumps(arguments), parsed=dict(arguments)
        )
        failed = (outcome.event or {}).get("status") == "error"
        if tools[name].risk == "write":
            audit.record(
                "library.write",
                user=caller.user,
                token=caller.token,
                request=request,
                summary=f"{name} from {caller.token.name}",
            )
        return _reply(
            message_id,
            {"content": [{"type": "text", "text": outcome.content}], "isError": failed},
        )
    return _reply(message_id, error=(-32601, f"Method not found: {method}"))


__all__ = ["LIBRARY_FAMILIES", "library_tools", "router"]
