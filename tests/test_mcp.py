"""The MCP client: framing, the session lifecycle, and what comes back.

The fake server below answers the way a real one does -- JSON-RPC over POST,
either as one JSON object or as an event stream, with a session id it expects
echoed. Everything is driven through `mock_http`, so no test touches a network.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from lembas.db.models import McpServer
from lembas.services.mcp import client, protocol, registry
from lembas.services.mcp.protocol import McpError


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    """`mcp.test` does not exist and check_url resolves for real."""
    import ipaddress
    import socket

    def resolve(host, *_args, **_kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return [(2, 1, 6, "", ("93.184.216.34", 80))]
        return [(2, 1, 6, "", (host, 80))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def _spec(**overrides) -> client.McpSpec:
    return client.McpSpec(
        **{"slug": "github", "name": "GitHub", "url": "https://mcp.test/rpc", **overrides}
    )


class FakeServer:
    """One server's worth of behaviour, as an httpx handler."""

    def __init__(self, *, tools=None, stream=False, session_id="s-1", pages=None):
        self.tools = tools if tools is not None else [
            {"name": "search", "description": "Search things.", "inputSchema": {"type": "object"}}
        ]
        self.stream = stream
        self.session_id = session_id
        self.pages = pages
        self.requests: list[dict] = []
        self.headers: list[httpx.Headers] = []
        self.result_for = {}

    def _frame(self, message: dict) -> httpx.Response:
        if self.stream:
            body = "".join(f"data: {line}\n" for line in json.dumps(message).splitlines()) + "\n"
            return httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        return httpx.Response(200, json=message)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(request.headers)
        if request.method == "DELETE":
            return httpx.Response(405)

        message = json.loads(request.content)
        self.requests.append(message)
        method, request_id = message.get("method"), message.get("id")

        if method == "notifications/initialized":
            return httpx.Response(202)

        if method == "initialize":
            headers = {"mcp-session-id": self.session_id} if self.session_id else {}
            response = self._frame(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "1"},
                    },
                }
            )
            return httpx.Response(
                200, content=response.content, headers={**dict(response.headers), **headers}
            )

        if method == "tools/list":
            if self.pages is not None:
                cursor = (message.get("params") or {}).get("cursor", "")
                index = int(cursor or 0)
                page = self.pages[index]
                result = {"tools": page}
                if index + 1 < len(self.pages):
                    result["nextCursor"] = str(index + 1)
                return self._frame({"jsonrpc": "2.0", "id": request_id, "result": result})
            return self._frame(
                {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.tools}}
            )

        if method == "tools/call":
            name = (message.get("params") or {}).get("name")
            result = self.result_for.get(
                name, {"content": [{"type": "text", "text": "It worked."}]}
            )
            return self._frame({"jsonrpc": "2.0", "id": request_id, "result": result})

        return self._frame(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Unknown"}}
        )


# --- The lifecycle -----------------------------------------------------------
async def test_initialize_is_followed_by_the_initialized_notification(mock_http):
    server = FakeServer()
    mock_http(server)

    tools, info, version = await client.list_tools(_spec())
    methods = [request.get("method") for request in server.requests]
    assert methods[:3] == ["initialize", "notifications/initialized", "tools/list"]
    assert info == {"name": "fake", "version": "1"}
    assert version == "2025-06-18"
    assert [tool["name"] for tool in tools] == ["search"]


async def test_the_session_id_is_echoed_on_every_later_request(mock_http):
    server = FakeServer(session_id="abc-123")
    mock_http(server)

    await client.list_tools(_spec())
    assert "mcp-session-id" not in server.headers[0]
    assert all(headers["mcp-session-id"] == "abc-123" for headers in server.headers[1:])
    assert all("mcp-protocol-version" in headers for headers in server.headers[1:])


async def test_a_server_that_issues_no_session_is_fine(mock_http):
    mock_http(FakeServer(session_id=""))
    tools, _info, _version = await client.list_tools(_spec())
    assert tools


async def test_an_expired_session_is_reinitialised_once(mock_http):
    """A 404 on a request carrying a session id means the server forgot us."""
    server = FakeServer()
    calls = {"list": 0}
    inner = server.__call__

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            message = json.loads(request.content)
            if message.get("method") == "tools/list":
                calls["list"] += 1
                if calls["list"] == 1:
                    return httpx.Response(404, text="no such session")
        return inner(request)

    mock_http(handler)
    tools, _info, _version = await client.list_tools(_spec())
    assert tools, "the retry after re-initialising must succeed"
    assert [r.get("method") for r in server.requests].count("initialize") == 2


async def test_a_json_response_and_an_event_stream_parse_the_same(mock_http):
    plain = FakeServer(stream=False)
    mock_http(plain)
    from_json, _i, _v = await client.list_tools(_spec())

    streamed = FakeServer(stream=True)
    mock_http(streamed)
    from_stream, _i, _v = await client.list_tools(_spec())

    assert from_json == from_stream


async def test_tools_list_follows_the_cursor(mock_http):
    server = FakeServer(
        pages=[
            [{"name": "one", "inputSchema": {"type": "object"}}],
            [{"name": "two", "inputSchema": {"type": "object"}}],
        ]
    )
    mock_http(server)
    tools, _i, _v = await client.list_tools(_spec())
    assert [tool["name"] for tool in tools] == ["one", "two"]


async def test_a_jsonrpc_error_becomes_a_readable_message(mock_http):
    def handler(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        if message.get("method") == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32602, "message": "invalid params"},
            },
        )

    mock_http(handler)
    with pytest.raises(McpError, match="invalid params"):
        await client.list_tools(_spec())


async def test_an_http_error_is_reported_with_its_status(mock_http):
    mock_http(lambda _r: httpx.Response(502, text="bad gateway"))
    with pytest.raises(McpError, match="502"):
        await client.list_tools(_spec())


# --- Reaching the network ----------------------------------------------------
async def test_the_url_is_checked_before_the_first_request(mock_http):
    server = FakeServer()
    mock_http(server)
    from lembas.services.fetch import FetchError

    with pytest.raises(FetchError, match="private or local"):
        await client.list_tools(_spec(url="http://127.0.0.1:9000/rpc"))
    assert server.requests == [], "nothing may be sent before the address is checked"


async def test_a_302_is_refused_and_a_307_is_followed_and_checked(mock_http):
    """301, 302 and 303 turn a POST into a GET, which means nothing to a
    JSON-RPC endpoint."""
    inner = FakeServer()

    def redirect_302(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://elsewhere.test/rpc"})

    mock_http(redirect_302)
    with pytest.raises(McpError, match="turn the request into a GET"):
        await client.list_tools(_spec())

    def redirect_307(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mcp.test":
            return httpx.Response(307, headers={"location": "https://elsewhere.test/rpc"})
        return inner(request)

    mock_http(redirect_307)
    tools, _i, _v = await client.list_tools(_spec())
    assert tools


async def test_a_redirect_to_a_private_address_is_refused(mock_http, monkeypatch):
    def resolve(host, *_args, **_kwargs):
        if host == "inside.test":
            return [(2, 1, 6, "", ("192.168.1.5", 80))]
        return [(2, 1, 6, "", ("93.184.216.34", 80))]

    monkeypatch.setattr("socket.getaddrinfo", resolve)
    mock_http(lambda _r: httpx.Response(307, headers={"location": "https://inside.test/rpc"}))

    from lembas.services.fetch import FetchError

    with pytest.raises(FetchError, match="private or local"):
        await client.list_tools(_spec())


async def test_the_secret_reaches_the_server(mock_http):
    server = FakeServer()
    mock_http(server)
    await client.list_tools(_spec(secret="tok", secret_placement="bearer"))
    assert server.headers[0]["authorization"] == "Bearer tok"


# --- Reading a tool result ---------------------------------------------------
async def test_text_blocks_are_joined(mock_http):
    server = FakeServer()
    server.result_for["search"] = {
        "content": [{"type": "text", "text": "one"}, {"type": "text", "text": "two"}]
    }
    mock_http(server)
    text, failed = await client.call_tool(_spec(), "search", {})
    assert text == "one\n\ntwo"
    assert failed is False


async def test_an_image_block_is_described_rather_than_forwarded(mock_http):
    """A tool turn is a string, images only reach models marked as having
    vision, and base64 in a tool result fills a window with nothing."""
    server = FakeServer()
    server.result_for["search"] = {
        "content": [{"type": "image", "mimeType": "image/png", "data": "A" * 400}]
    }
    mock_http(server)
    text, _failed = await client.call_tool(_spec(), "search", {})
    assert "A" * 40 not in text
    assert "image/png" in text
    assert "not shown to the model" in text


async def test_a_resource_block_with_text_keeps_it(mock_http):
    server = FakeServer()
    server.result_for["search"] = {
        "content": [
            {"type": "resource", "resource": {"uri": "file:///a.txt", "text": "contents"}}
        ]
    }
    mock_http(server)
    text, _failed = await client.call_tool(_spec(), "search", {})
    assert "file:///a.txt" in text and "contents" in text


async def test_a_resource_block_with_no_text_names_its_type(mock_http):
    server = FakeServer()
    server.result_for["search"] = {
        "content": [
            {"type": "resource", "resource": {"uri": "file:///a.bin", "mimeType": "application/x"}}
        ]
    }
    mock_http(server)
    text, _failed = await client.call_tool(_spec(), "search", {})
    assert "application/x" in text


async def test_an_unknown_block_type_degrades_to_a_note(mock_http):
    server = FakeServer()
    server.result_for["search"] = {"content": [{"type": "hologram"}]}
    mock_http(server)
    text, _failed = await client.call_tool(_spec(), "search", {})
    assert "hologram" in text


async def test_structured_content_is_used_when_there_is_no_text(mock_http):
    server = FakeServer()
    server.result_for["search"] = {"content": [], "structuredContent": {"count": 3}}
    mock_http(server)
    text, _failed = await client.call_tool(_spec(), "search", {})
    assert json.loads(text) == {"count": 3}


async def test_is_error_is_reported_as_a_failure(mock_http):
    server = FakeServer()
    server.result_for["search"] = {
        "content": [{"type": "text", "text": "no such repo"}],
        "isError": True,
    }
    mock_http(server)
    _text, failed = await client.call_tool(_spec(), "search", {})
    assert failed is True


# --- Bounding what a server can say ------------------------------------------
def test_a_giant_description_is_truncated():
    cleaned = protocol.clean_tool({"name": "x", "description": "d" * 9000})
    assert len(cleaned["description"]) == protocol.MAX_DESCRIPTION


def test_a_schema_that_is_not_an_object_is_replaced():
    cleaned = protocol.clean_tool({"name": "x", "inputSchema": "nope"})
    assert cleaned["schema"] == {"type": "object", "properties": {}}


def test_a_giant_schema_is_replaced():
    huge = {"type": "object", "properties": {f"p{i}": {"type": "string"} for i in range(1000)}}
    cleaned = protocol.clean_tool({"name": "x", "inputSchema": huge})
    assert cleaned["schema"] == {"type": "object", "properties": {}}


def test_an_entry_with_no_name_is_dropped():
    assert protocol.clean_tool({"description": "orphan"}) is None
    assert protocol.clean_tool("not a dict") is None


# --- Namespacing -------------------------------------------------------------
def test_tool_names_are_namespaced_and_within_the_charset():
    taken: set[str] = set()
    assert registry.offer_name("github", "search", taken=taken) == "github_search"
    assert registry.offer_name("github", "Create Issue!", taken=taken) == "github_create_issue"
    assert all(registry.FUNCTION_NAME.match(name) for name in taken)


def test_a_name_colliding_with_a_builtin_is_renamed():
    from lembas.services import tools as tools_service

    taken = set(tools_service.REGISTRY)
    name = registry.offer_name("", "web_search", taken=taken)
    assert name != "web_search"
    assert name not in tools_service.REGISTRY


def test_a_truncation_collision_is_disambiguated():
    """Two long names can collide once cut to 64 characters where the full ones
    would not have."""
    taken: set[str] = set()
    first = registry.offer_name("srv", "a" * 200, taken=taken)
    second = registry.offer_name("srv", "a" * 201, taken=taken)
    assert first != second
    assert len(first) <= 64 and len(second) <= 64


# --- Rows become tools -------------------------------------------------------
def _server(db, **overrides) -> McpServer:
    row = McpServer(
        **{
            "slug": "github",
            "name": "GitHub",
            "url": "https://mcp.test/rpc",
            "tools_json": [
                {
                    "name": "search",
                    "offer_name": "github_search",
                    "description": "Search things.",
                    "schema": {"type": "object", "properties": {}},
                }
            ],
            **overrides,
        }
    )
    db.add(row)
    db.commit()
    return row


def test_a_cached_tool_becomes_a_definition(db, user_id):
    from lembas.db.models import User

    _server(db)
    defs = registry.tool_defs(db, db.get(User, user_id))
    assert [tool.name for tool in defs] == ["github_search"]
    assert defs[0].family == "mcp:github"


def test_a_tool_switched_off_is_not_offered(db, user_id):
    from lembas.db.models import User

    _server(db, tool_overrides_json={"search": False})
    assert registry.tool_defs(db, db.get(User, user_id)) == []


def test_a_tool_absent_from_the_overrides_is_on(db, user_id):
    """Absent means on, the rule the model capability flags follow."""
    from lembas.db.models import User

    _server(db, tool_overrides_json={"something_else": False})
    assert len(registry.tool_defs(db, db.get(User, user_id))) == 1


async def test_refresh_caches_the_tools_and_clears_the_last_error(db, mock_http):
    mock_http(FakeServer())
    server = _server(db, tools_json=[], last_error="it was broken")

    count, error = await registry.refresh(db, server)
    assert (count, error) == (1, "")
    db.refresh(server)
    assert server.tools_json[0]["offer_name"] == "github_search"
    assert server.last_error == ""
    assert server.protocol_version == "2025-06-18"
    assert server.server_info["name"] == "fake"


async def test_a_failed_refresh_records_it_and_keeps_the_cached_tools(db, mock_http):
    mock_http(lambda _r: httpx.Response(500, text="down"))
    server = _server(db)

    count, error = await registry.refresh(db, server)
    assert count == 0 and "500" in error
    db.refresh(server)
    assert server.last_error
    assert server.last_checked_at is not None
    assert len(server.tools_json) == 1, "a bad refresh must not empty the list"


async def test_a_refresh_keeps_a_choice_about_a_tool_that_survives(db, mock_http):
    mock_http(FakeServer(tools=[{"name": "search"}, {"name": "gone"}]))
    server = _server(db, tool_overrides_json={"search": False, "vanished": False})
    await registry.refresh(db, server)

    db.refresh(server)
    assert server.tool_overrides_json == {"search": False}


async def test_a_failing_tool_call_reaches_the_model_as_words(db, user_id, mock_http):
    from lembas.db.models import User

    mock_http(lambda _r: httpx.Response(500, text="down"))
    _server(db)
    tool = registry.tool_defs(db, db.get(User, user_id))[0]

    outcome = await tool.run(None, {})
    assert outcome.event["status"] == "error"
    assert "GitHub" in outcome.content


def test_two_servers_exposing_the_same_tool_do_not_collide(db, user_id):
    from lembas.db.models import User

    _server(db)
    _server(db, slug="gitlab", name="GitLab", tools_json=[{"name": "search", "schema": {}}])
    names = [tool.name for tool in registry.tool_defs(db, db.get(User, user_id))]
    assert len(names) == len(set(names)) == 2


def test_a_disabled_server_offers_nothing(db, user_id):
    from lembas.db.models import User

    _server(db, enabled=False)
    assert registry.tool_defs(db, db.get(User, user_id)) == []
    assert db.scalar(select(McpServer)) is not None
