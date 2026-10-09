"""Talking to a remote MCP server over streamable HTTP.

One session per call, deliberately. A cached session would need an owner, a
lifetime, eviction, a lock -- a round runs its tools concurrently -- and a
shutdown hook, and the server may expire it underneath all of that anyway.
`ToolContext` is a session-free snapshot precisely so that nothing inside a tool
holds live state. The cost is one extra POST in front of a call that is already
a network round trip inside a reply taking seconds; the upgrade, if it is ever
worth it, is a dict in this module and invisible to everything else.

Redirects are followed by hand and every hop is re-checked, for the reason
`services/fetch.py` gives: an administrator can point this at any URL, and a
name resolving to 127.0.0.1 walks past any check that only reads the text.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from lembas.db.models import SECRET_BEARER, SECRET_HEADER, SECRET_QUERY, McpServer
from lembas.services import fetch as fetch_service
from lembas.services.crypto import decrypt
from lembas.services.mcp import protocol
from lembas.services.mcp.protocol import McpError

log = logging.getLogger(__name__)

# A JSON-RPC message is small; anything this large is a server misbehaving, and
# reading it into memory before parsing is the failure to avoid.
MAX_MESSAGE_BYTES = 4 * 1024 * 1024

# tools/list is paged. Both bounds exist because the whole list goes into every
# request as schema.
MAX_PAGES = 10
MAX_TOOLS = 100

MIN_TIMEOUT, MAX_TIMEOUT = 1, 120
MIN_CHARS, MAX_CHARS = 200, 40_000


@dataclass(frozen=True)
class McpSpec:
    """One server, read while the session was open. See `custom_tools.HttpSpec`."""

    slug: str
    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    secret: str = ""
    secret_placement: str = SECRET_BEARER
    secret_name: str = "Authorization"
    timeout: int = 30
    max_chars: int = 8000
    allow_private: bool = False


def spec_from(row: McpServer) -> McpSpec:
    return McpSpec(
        slug=row.slug,
        name=row.name or row.slug,
        url=row.url or "",
        headers=dict(row.headers_json or {}),
        secret=decrypt(row.secret_encrypted),
        secret_placement=row.secret_placement,
        secret_name=row.secret_name or "Authorization",
        timeout=min(max(int(row.timeout or 0), MIN_TIMEOUT), MAX_TIMEOUT),
        max_chars=min(max(int(row.max_chars or 0), MIN_CHARS), MAX_CHARS),
        allow_private=bool(row.allow_private),
    )


def _headers(spec: McpSpec) -> dict[str, str]:
    headers = {
        "User-Agent": fetch_service.USER_AGENT,
        "Content-Type": "application/json",
        # Both, because a server may answer either for the same request.
        "Accept": "application/json, text/event-stream",
        **{str(k): str(v) for k, v in spec.headers.items()},
    }
    if spec.secret:
        if spec.secret_placement == SECRET_BEARER:
            headers[spec.secret_name or "Authorization"] = f"Bearer {spec.secret}"
        elif spec.secret_placement == SECRET_HEADER:
            headers[spec.secret_name or "Authorization"] = spec.secret
    return headers


def _endpoint(spec: McpSpec) -> str:
    url = fetch_service.check_url(spec.url, allow_private=spec.allow_private)
    if spec.secret and spec.secret_placement == SECRET_QUERY:
        joiner = "&" if urlparse(url).query else "?"
        url = f"{url}{joiner}{quote(spec.secret_name)}={quote(spec.secret, safe='')}"
    return url


class Session:
    """One initialised conversation with a server."""

    def __init__(self, spec: McpSpec, client: httpx.AsyncClient) -> None:
        self.spec = spec
        self._client = client
        self._url = ""
        self._headers = _headers(spec)
        self._session_id = ""
        self._next_id = 0
        self.protocol_version = ""
        self.server_info: dict[str, Any] = {}

    # --- Transport -----------------------------------------------------------
    async def _post(self, message: dict[str, Any]) -> httpx.Response:
        current = self._url
        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version

        origin = (urlparse(current).scheme, urlparse(current).netloc)
        for _ in range(fetch_service.MAX_REDIRECTS + 1):
            try:
                response = await self._client.post(current, json=message, headers=headers)
            except httpx.RequestError as exc:
                raise McpError(f"Could not reach {self.spec.name}: {exc}") from exc

            if not response.is_redirect:
                return response

            location = response.headers.get("location", "")
            if not location:
                raise McpError(f"{self.spec.name} redirected to nowhere.")
            if response.status_code not in (307, 308):
                # 301, 302 and 303 turn a POST into a GET, which means nothing
                # to a JSON-RPC endpoint. Refused rather than guessed at.
                raise McpError(
                    f"{self.spec.name} answered {response.status_code}, which would "
                    "turn the request into a GET. Point the URL at the endpoint itself."
                )

            current = fetch_service.check_url(
                str(response.url.join(location)), allow_private=self.spec.allow_private
            )
            if (urlparse(current).scheme, urlparse(current).netloc) != origin:
                headers.pop(self.spec.secret_name or "Authorization", None)
                origin = (urlparse(current).scheme, urlparse(current).netloc)

        raise McpError(f"{self.spec.name} redirected too many times.")

    def _read(self, response: httpx.Response, *, request_id: int) -> dict[str, Any]:
        if response.status_code >= 400:
            raise McpError(f"{self.spec.name} returned HTTP {response.status_code}.")
        return protocol.result_of(
            response.content[:MAX_MESSAGE_BYTES],
            response.headers.get("content-type", ""),
            request_id=request_id,
        )

    # --- Lifecycle -----------------------------------------------------------
    async def open(self) -> None:
        self._url = _endpoint(self.spec)
        self._next_id += 1
        request_id = self._next_id

        response = await self._post(
            protocol.request(
                "initialize",
                {
                    "protocolVersion": protocol.PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": protocol.CLIENT_INFO,
                },
                request_id=request_id,
            )
        )
        # Captured before the body is read: a server that issues one expects it
        # on everything after this, including the initialized notification.
        self._session_id = response.headers.get("mcp-session-id", "")
        result = self._read(response, request_id=request_id)

        self.protocol_version = str(result.get("protocolVersion") or protocol.PROTOCOL_VERSION)
        info = result.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        if self.protocol_version != protocol.PROTOCOL_VERSION:
            log.info(
                "%s speaks MCP %s, we asked for %s",
                self.spec.name,
                self.protocol_version,
                protocol.PROTOCOL_VERSION,
            )

        await self._post(protocol.notification("notifications/initialized"))

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One request, re-initialising once if the session has expired."""
        self._next_id += 1
        request_id = self._next_id
        response = await self._post(protocol.request(method, params, request_id=request_id))

        if response.status_code == 404 and self._session_id:
            # The server dropped the session. One retry, then it is a failure
            # like any other -- a loop here would be a loop against a server
            # that has decided to forget us.
            log.info("%s expired its session; re-initialising", self.spec.name)
            self._session_id = ""
            await self.open()
            self._next_id += 1
            request_id = self._next_id
            response = await self._post(protocol.request(method, params, request_id=request_id))

        return self._read(response, request_id=request_id)

    async def close(self) -> None:
        """Best effort. A server with nothing to clean up answers 405."""
        if not self._session_id:
            return
        headers = {**self._headers, "Mcp-Session-Id": self._session_id}
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        with suppress(httpx.RequestError, McpError):
            await self._client.delete(self._url, headers=headers)


@asynccontextmanager
async def session_for(spec: McpSpec) -> AsyncIterator[Session]:
    """An initialised session, closed afterwards whatever happened."""
    client = httpx.AsyncClient(timeout=spec.timeout, follow_redirects=False)
    session = Session(spec, client)
    try:
        await session.open()
        yield session
    finally:
        await session.close()
        await client.aclose()


async def list_tools(spec: McpSpec) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Every tool a server advertises, plus what it said about itself.

    Returns (tools, serverInfo, protocolVersion). Bounded at MAX_TOOLS: the list
    is sent as schema on every request, so a server offering two hundred is a
    server that would fill the window before anything was asked.
    """
    tools: list[dict[str, Any]] = []
    async with session_for(spec) as session:
        cursor = ""
        for _ in range(MAX_PAGES):
            result = await session.call("tools/list", {"cursor": cursor} if cursor else {})
            for entry in result.get("tools") or []:
                cleaned = protocol.clean_tool(entry)
                if cleaned is None:
                    log.info("%s advertised an unusable tool entry", spec.name)
                elif len(tools) < MAX_TOOLS:
                    tools.append(cleaned)
            cursor = str(result.get("nextCursor") or "")
            if not cursor or len(tools) >= MAX_TOOLS:
                break
        return tools, session.server_info, session.protocol_version


async def call_tool(spec: McpSpec, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    """Run one tool. Returns (text, is_error)."""
    async with session_for(spec) as session:
        result = await session.call("tools/call", {"name": name, "arguments": arguments})
        return protocol.content_to_text(result), bool(result.get("isError"))


__all__ = ["McpError", "McpSpec", "Session", "call_tool", "list_tools", "spec_from"]
