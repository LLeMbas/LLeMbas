"""`/mcp`: a person's memory, served to a logged-in device over MCP.

Memory is records the server keeps for both sides (harness spec 3.0.0,
`filesystem.md` *Memory*): the LLeMbas CLI -- or any MCP client -- reads and
writes the holder's here, with a token carrying the `memory` scope (or
`library`, its older name from when notes, skills and knowledge were served too,
which counts as `memory`; a device keeps those locally now). Two things and
nothing else: the tool `memory` -- LLeMbas's own, the same definition and runner
a chat uses, so a model sees the same tool either way -- and the resource
`lembas://memory`, the entries as a system prompt's memory block shows them,
which a device reads as a session starts.

**What a person may do in a chat is what a device may do here**: the tool is
gated by the same permissions (`library.use`, `tools.memory`, and the split
write permission), checked on every call. The data group is the person's
default one -- a device is not a chat, and has no connection to pick a group by
-- unless the call names the session it is made for (`_meta.lembas.scope`,
harness spec 1.1.0): then it is answered within that chat's data group,
exactly as the chat loop's own tools are. The scope rides on a resource read
the same way.

Streamable HTTP in its plainest form: one JSON-RPC message per POST, answered
with one JSON response (no server-to-client stream, so `GET` is 405). Methods:
`initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`,
`resources/list`, `resources/read`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from lembas import __version__
from lembas.api.deps import Db
from lembas.security.api_tokens import SCOPE_LIBRARY, SCOPE_MEMORY, Caller, require_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSION = "2025-06-18"
LIBRARY_FAMILIES = ("memory",)
MEMORY_URI = "lembas://memory"

_library = require_token(SCOPE_MEMORY, SCOPE_LIBRARY)


def memory_readable(db, user) -> bool:
    """Whether this person may read their memory here: what offers the memory
    tool in a chat, less the write switch, which a read does not need."""
    from lembas.security import permissions

    allowed = permissions.resolve(db, user)
    return bool(allowed.get("library.use") and allowed.get("tools.memory"))


def memory_text(db, user, chat) -> str:
    """`lembas://memory`'s text: the entries exactly as the memory block of a
    system prompt shows them, in the data group the scope lands in (empty:
    none)."""
    from lembas.services import memories as memories_service
    from lembas.services import tools as tools_service

    group = tools_service.context_for(db, user, chat).data_group
    return memories_service.block(db, user, group)


def library_tools(db, user) -> dict[str, Any]:
    """The tools this person may use here now (memory), by name."""
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


def device_chat(db, token_id: str, session_id: str, user_id: str | None = None):
    """The chat a device's session id names, for this device's token and no
    other: a session id is the device's, so one device's `scope` can never
    reach a chat another device runs -- not even one of the same account's."""
    from sqlalchemy import select

    from lembas.db.models import Chat

    if not token_id or not session_id:
        return None
    wanted: list[Any] = [Chat.device_token_id == token_id, Chat.device_session_id == session_id]
    if user_id:
        wanted.append(Chat.user_id == user_id)
    return db.scalars(select(Chat).where(*wanted)).first()


def _reply(
    message_id: Any, result: Any = None, error: tuple[int, str] | None = None
) -> JSONResponse:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id}
    if error is not None:
        body["error"] = {"code": error[0], "message": error[1]}
    else:
        body["result"] = result
    return JSONResponse(body)


def _scoped_chat(db, caller: Caller, params: dict[str, Any]):
    """The chat a call's `_meta.lembas.scope` names, and the error when it
    names a session that is not this device's (None, None: no scope)."""
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    lembas = meta.get("lembas") if isinstance(meta.get("lembas"), dict) else {}
    scope = str(lembas.get("scope") or "")
    if not scope:
        return None, None
    chat = device_chat(db, caller.token.id, scope, caller.user.id)
    if chat is None:
        return None, (-32602, "not this device's session")
    return chat, None


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
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False, "subscribe": False},
                },
                "serverInfo": {"name": "lembas", "version": __version__},
                "instructions": (
                    "The memory of the person signed in: the memory tool, and "
                    f"{MEMORY_URI} for its entries."
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
    if method == "resources/list":
        listed = (
            [{
                "uri": MEMORY_URI,
                "name": "memory",
                "description": "The memory entries, as a system prompt shows them.",
                "mimeType": "text/markdown",
            }]
            if memory_readable(db, caller.user)
            else []
        )
        return _reply(message_id, {"resources": listed})
    if method == "resources/read":
        uri = str(params.get("uri") or "")
        if uri != MEMORY_URI or not memory_readable(db, caller.user):
            return _reply(message_id, error=(-32602, f"There is no resource {uri!r} here."))
        chat, refused = _scoped_chat(db, caller, params)
        if refused:
            return _reply(message_id, error=refused)
        return _reply(message_id, {"contents": [{
            "uri": MEMORY_URI,
            "mimeType": "text/markdown",
            "text": memory_text(db, caller.user, chat),
        }]})
    if method == "tools/call":
        from lembas.services import audit
        from lembas.services import tools as tools_service

        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        tools = library_tools(db, caller.user)
        # A former name (`memory_add`, `notes_create`, ...) is the call it
        # means, as in the chat loop: a device that listed the tools before an
        # update still calls them by the names it was given then.
        if isinstance(arguments, dict):
            name, arguments = tools_service.resolve_alias(name, arguments, tools)
        if name not in tools:
            return _reply(
                message_id, error=(-32602, f"There is no tool {name!r} for this account.")
            )
        # The scope: a call made for a session this instance opened on the
        # device names it, and is answered within that chat's data group, as
        # the chat loop's own tools would be -- by the same builder the loop
        # uses. A session that is not this device's is refused, whoever's it
        # is. Without a scope, the account's default group, as before.
        chat, refused = _scoped_chat(db, caller, params)
        if refused:
            return _reply(message_id, error=refused)
        context = tools_service.context_for(db, caller.user, chat)
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


__all__ = [
    "LIBRARY_FAMILIES",
    "MEMORY_URI",
    "device_chat",
    "library_tools",
    "memory_readable",
    "memory_text",
    "router",
]
