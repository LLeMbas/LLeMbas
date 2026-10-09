"""The MCP wire format: JSON-RPC 2.0, and what comes back from a tool call.

Written out rather than taken from the reference SDK. The client is a few
hundred lines of framing, and the SDK's transport does its own connecting --
which would mean the one thing that must not be bypassed, `fetch.check_url` on
every hop, being bypassed. Owning the transport is the point; owning the framing
beside it is the small part.

A response arrives either as one JSON object or as an event stream carrying
several messages, and a server may choose either for the same request. Both are
read here so `client.py` does not have to care which it got.
"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

from lembas import __version__

# What we tell a server we speak. A server answering an older version is not
# refused: several in the wild still answer 2024-11-05 and work perfectly.
PROTOCOL_VERSION = "2025-06-18"

CLIENT_INFO = {"name": "LLeMbas", "version": __version__}

# A tool's metadata is sent to the model as instructions, so it is bounded here
# rather than trusted. A server advertising a 40 KB description would spend the
# context window before the conversation started.
MAX_DESCRIPTION = 1000
MAX_SCHEMA_BYTES = 8192
MAX_NAME = 64

# Content that is not text is described rather than forwarded. A tool turn is a
# string, images only reach models marked as having vision, and base64 in a tool
# result is the fastest way to fill a window with nothing.
UNSUPPORTED = "[{kind}: {detail} — not shown to the model]"


class McpError(Exception):
    """A failed exchange, with a message fit to show an administrator.

    Same contract as `LLMError`, `SearchError` and `FetchError`: the message is
    the whole error, and is safe to render.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def request(method: str, params: dict[str, Any] | None, *, request_id: int) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def _messages(body: bytes, content_type: str) -> list[dict[str, Any]]:
    """Every JSON-RPC message in a response body, whichever framing was used."""
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return []

    if "text/event-stream" not in content_type.lower():
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpError(f"That server did not answer with JSON: {exc}") from exc
        return document if isinstance(document, list) else [document]

    # Frames are `data:` lines gathered until a blank line -- the same framing
    # services/sse.py writes. Read here rather than shared, because that module
    # is a writer and this is a reader with a size cap.
    out: list[dict[str, Any]] = []
    data: list[str] = []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line.strip() and data:
            # A frame that is not JSON is a comment or a keep-alive, not a
            # message; the stream carries both.
            with suppress(json.JSONDecodeError):
                out.append(json.loads("\n".join(data)))
            data = []
    return out


def result_of(body: bytes, content_type: str, *, request_id: int) -> dict[str, Any]:
    """The result for one request, out of whatever the server sent back.

    Raises `McpError` on a JSON-RPC error member, because that is a failure the
    administrator needs the words of -- "Unknown tool" and "invalid params" are
    the two that actually happen.
    """
    for message in _messages(body, content_type):
        if not isinstance(message, dict) or message.get("id") != request_id:
            continue
        if "error" in message:
            error = message["error"] or {}
            code = error.get("code", "")
            text = str(error.get("message") or "The server reported an error.")
            raise McpError(f"{text}{f' (code {code})' if code != '' else ''}")
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    raise McpError("That server did not answer the request.")


# --- Tool metadata -----------------------------------------------------------
def clean_tool(entry: Any) -> dict[str, Any] | None:
    """One advertised tool, bounded. None if there is nothing usable here."""
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or "").strip()
    if not name or len(name) > MAX_NAME:
        return None

    schema = entry.get("inputSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        schema = {"type": "object", "properties": {}}
    elif len(json.dumps(schema)) > MAX_SCHEMA_BYTES:
        # Kept callable rather than dropped: a model can still be told the tool
        # exists, and an argument it guesses is no worse than not offering it.
        schema = {"type": "object", "properties": {}}

    return {
        "name": name,
        "title": str(entry.get("title") or "")[:MAX_NAME],
        "description": str(entry.get("description") or "")[:MAX_DESCRIPTION],
        "schema": schema,
    }


# --- Tool results ------------------------------------------------------------
def _block_text(block: Any) -> str:
    if not isinstance(block, dict):
        return ""
    kind = str(block.get("type") or "")

    if kind == "text":
        return str(block.get("text") or "")

    if kind in ("image", "audio"):
        size = len(str(block.get("data") or ""))
        detail = f"{block.get('mimeType') or 'unknown type'}, about {size * 3 // 4} bytes"
        return UNSUPPORTED.format(kind=kind, detail=detail)

    if kind == "resource":
        resource = block.get("resource")
        if not isinstance(resource, dict):
            return ""
        uri = str(resource.get("uri") or "")
        if isinstance(resource.get("text"), str):
            return f"{uri}\n{resource['text']}" if uri else str(resource["text"])
        detail = f"{uri or 'unnamed'}, {resource.get('mimeType') or 'unknown type'}"
        return UNSUPPORTED.format(kind="resource", detail=detail)

    if kind == "resource_link":
        return f"{block.get('name') or 'resource'} ({block.get('uri') or ''})".strip()

    # An addition to the protocol degrades to a note rather than to silence:
    # a model told nothing came back will say nothing came back.
    return UNSUPPORTED.format(kind="content", detail=kind or "no type")


def content_to_text(result: dict[str, Any]) -> str:
    """A `tools/call` result as the flat text a tool turn carries."""
    blocks = result.get("content")
    parts = [text for block in (blocks or []) if (text := _block_text(block).strip())]

    if not parts and isinstance(result.get("structuredContent"), dict | list):
        return json.dumps(result["structuredContent"], indent=2, ensure_ascii=False)

    return "\n\n".join(parts)


__all__ = [
    "CLIENT_INFO",
    "PROTOCOL_VERSION",
    "McpError",
    "clean_tool",
    "content_to_text",
    "notification",
    "request",
    "result_of",
]
