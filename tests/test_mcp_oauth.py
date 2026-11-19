"""Signing in to an MCP server with OAuth, against a provider made here.

One handler plays both sides a real sign-in talks to: an MCP server that
refuses a call without a token it knows, and an authorization server with
metadata, registration, a token endpoint that checks PKCE, and revocation.
`authorize` stands in for the person's browser at the provider. Everything
goes through `mock_http`; nothing leaves this process, and no real provider
is asked -- what one does differently, these tests cannot know.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx
import pytest
from sqlalchemy import select

from lembas.db.models import (
    AUTH_OAUTH,
    SIGNIN_NEEDS,
    McpOauthState,
    McpServer,
    McpSignin,
    User,
)
from lembas.services.crypto import decrypt, encrypt
from lembas.services.mcp import client as mcp_client
from lembas.services.mcp import oauth, registry
from lembas.services.mcp.protocol import McpError
from tests.conftest import join

MCP = "https://mcp.test/rpc"
ISSUER = "https://auth.test"
BASE = "http://testserver"
SAM = {"name": "Sam", "email": "sam@shire.test", "password": "po-ta-toes-boil-em"}


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    """The made-up hosts resolve to a public address; `check_url` resolves for real."""
    import ipaddress
    import socket

    def resolve(host, *_args, **_kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if host == "inside.test":
                return [(2, 1, 6, "", ("172.16.1.5", 80))]
            return [(2, 1, 6, "", ("93.184.216.34", 80))]
        return [(2, 1, 6, "", (host, 80))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    oauth._locks.clear()


class Provider:
    """An MCP server and its authorization server, as an httpx handler."""

    def __init__(self, **options):
        self.registers = options.get("registers", True)
        self.header = options.get("header", True)
        self.pkce = options.get("pkce", ["S256"])
        self.issuer_said = options.get("issuer_said", ISSUER)
        self.resource_said = options.get("resource_said", MCP)
        self.rotates = options.get("rotates", True)
        self.lasts = options.get("lasts", 3600)
        self.refresh_refused = False
        self.token_down = False
        self.seen: list[httpx.Request] = []
        self.registered: list[dict] = []
        self.asked: list[dict] = []  # what each authorization request carried
        self.codes: dict[str, dict] = {}
        self.access: set[str] = set()
        self.refresh: dict[str, str] = {}
        self.revoked: list[str] = []
        self.token_forms: list[dict] = []
        self.count = 0
        self.id_token = options.get("id_token", "")

    # --- the browser at the provider -------------------------------------------------
    def authorize(self, url: str) -> dict[str, str]:
        """The person approves. Returns what the provider sends them back with."""
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{ISSUER}/authorize"
        asked = dict(parse_qsl(parts.query))
        self.asked.append(asked)
        self.count += 1
        code = f"code-{self.count}"
        self.codes[code] = asked
        return {"code": code, "state": asked["state"]}

    # --- the handler ---------------------------------------------------------------------
    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        host, path = request.url.host, request.url.path
        if host == "mcp.test":
            return self._mcp(request, path)
        if host == "auth.test":
            return self._auth(request, path)
        return httpx.Response(404)

    def _mcp(self, request, path):
        if path == "/.well-known/oauth-protected-resource/rpc":
            return httpx.Response(200, json={
                "resource": self.resource_said,
                "authorization_servers": [ISSUER],
                "scopes_supported": ["read", "write"],
            })
        if path != "/rpc":
            return httpx.Response(404)
        if request.method == "DELETE":
            return httpx.Response(405)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.access:
            said = 'Bearer error="invalid_token"'
            if self.header:
                said = ('Bearer resource_metadata='
                        '"https://mcp.test/.well-known/oauth-protected-resource/rpc"')
            return httpx.Response(401, headers={"www-authenticate": said})
        message = json.loads(request.content)
        method, request_id = message.get("method"), message.get("id")
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fake", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "search", "description": "Search things.",
                                 "inputSchema": {"type": "object"}}]}
        else:
            result = {"content": [{"type": "text", "text": f"called as {token}"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, "result": result})

    def _issue(self) -> dict:
        self.count += 1
        access, refresh = f"at-{self.count}", f"rt-{self.count}"
        self.access.add(access)
        self.refresh[refresh] = access
        answer = {"access_token": access, "token_type": "Bearer", "expires_in": self.lasts,
                  "refresh_token": refresh, "scope": "read write"}
        if self.id_token:
            answer["id_token"] = self.id_token
        return answer

    def _auth(self, request, path):
        if path == "/.well-known/oauth-authorization-server":
            document = {
                "issuer": self.issuer_said,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "revocation_endpoint": f"{ISSUER}/revoke",
                "response_types_supported": ["code"],
            }
            if self.pkce is not None:
                document["code_challenge_methods_supported"] = self.pkce
            if self.registers:
                document["registration_endpoint"] = f"{ISSUER}/register"
            return httpx.Response(200, json=document)
        if path == "/register" and self.registers:
            self.registered.append(json.loads(request.content))
            return httpx.Response(201, json={"client_id": f"dyn-{len(self.registered)}"})
        if path == "/revoke":
            self.revoked.append(parse_qs(request.content.decode())["token"][0])
            return httpx.Response(200)
        if path != "/token":
            return httpx.Response(404)
        if self.token_down:
            return httpx.Response(503)
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_forms.append(form)
        if form.get("grant_type") == "authorization_code":
            asked = self.codes.pop(form.get("code", ""), None)
            if asked is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            made = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if (
                made != asked["code_challenge"]
                or form.get("redirect_uri") != asked["redirect_uri"]
                or form.get("resource") != asked["resource"]
                or self._client_of(request, form) != asked["client_id"]
            ):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json=self._issue())
        if form.get("grant_type") == "refresh_token":
            old = form.get("refresh_token", "")
            if self.refresh_refused or old not in self.refresh:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.access.discard(self.refresh[old])
            if self.rotates:
                del self.refresh[old]
                return httpx.Response(200, json=self._issue())
            answer = self._issue()
            del self.refresh[answer.pop("refresh_token")]
            self.refresh[old] = answer["access_token"]
            return httpx.Response(200, json=answer)
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    @staticmethod
    def _client_of(request, form) -> str:
        basic = request.headers.get("authorization", "")
        if basic.startswith("Basic "):
            return base64.b64decode(basic[6:]).decode().split(":", 1)[0]
        return form.get("client_id", "")


def _server(db, **overrides) -> McpServer:
    row = McpServer(**{
        "slug": "mail", "name": "Mail", "url": MCP, "auth_kind": AUTH_OAUTH,
        "tools_json": [{"name": "search", "offer_name": "mail_search",
                        "description": "Search things.",
                        "schema": {"type": "object", "properties": {}}}],
        **overrides,
    })
    db.add(row)
    db.commit()
    return row


def _admin(db) -> User:
    return db.scalar(select(User).where(User.email == "frodo@shire.test"))


def _start(client, server, **kwargs) -> httpx.Response:
    return client.post(f"/settings/mcp/{server.id}/sign-in",
                       headers={"HX-Request": "true"}, follow_redirects=False, **kwargs)


def _sign_in(client, provider, server) -> httpx.Response:
    """The whole round trip as a browser makes it."""
    started = _start(client, server)
    assert started.status_code == 204, started.text
    back = provider.authorize(started.headers["HX-Redirect"])
    return client.get(oauth.CALLBACK_PATH, params=back, follow_redirects=False)


def _signin(db, server, user=None) -> McpSignin | None:
    db.expire_all()
    owner = McpSignin.user_id.is_(None) if user is None else McpSignin.user_id == user.id
    return db.scalar(select(McpSignin).where(McpSignin.server_id == server.id, owner))


# --- Addresses, as the specifications give them ------------------------------------------
def test_the_well_known_addresses_are_tried_in_the_order_given():
    assert oauth.resource_metadata_urls("https://example.org/public/mcp") == [
        "https://example.org/.well-known/oauth-protected-resource/public/mcp",
        "https://example.org/.well-known/oauth-protected-resource",
    ]
    assert oauth.issuer_metadata_urls("https://auth.example.org/tenant1") == [
        "https://auth.example.org/.well-known/oauth-authorization-server/tenant1",
        "https://auth.example.org/.well-known/openid-configuration/tenant1",
        "https://auth.example.org/tenant1/.well-known/openid-configuration",
    ]
    assert oauth.issuer_metadata_urls("https://auth.example.org") == [
        "https://auth.example.org/.well-known/oauth-authorization-server",
        "https://auth.example.org/.well-known/openid-configuration",
    ]


def test_a_resource_covers_only_its_own_origin_and_path():
    assert oauth.covers("https://mcp.test", "https://mcp.test/rpc")
    assert oauth.covers("https://mcp.test/rpc", "https://MCP.test/rpc/")
    assert not oauth.covers("https://mcp.test/other", "https://mcp.test/rpc")
    assert not oauth.covers("https://mcp.test/rp", "https://mcp.test/rpc")
    assert not oauth.covers("https://elsewhere.test/rpc", "https://mcp.test/rpc")
    assert not oauth.covers("http://mcp.test/rpc", "https://mcp.test/rpc")
    assert oauth.canonical("HTTPS://MCP.test/rpc/?x=1#frag") == "https://mcp.test/rpc"


def test_a_challenge_header_is_read():
    said = oauth.challenge('Bearer resource_metadata="https://a.test/x", scope="a b", error=nope')
    assert said == {"resource_metadata": "https://a.test/x", "scope": "a b", "error": "nope"}
    assert oauth.challenge('Basic realm="x"') == {}


# --- Discovery ---------------------------------------------------------------------------
async def test_discovery_follows_the_401_to_the_provider(db, mock_http):
    provider = Provider()
    mock_http(provider)
    found = await oauth.discover(_server(db))
    assert found.issuer == ISSUER and found.token_endpoint == f"{ISSUER}/token"
    assert found.resource == MCP and found.scope == "read write"
    # The first request carried no credential of any kind.
    assert "authorization" not in provider.seen[0].headers


async def test_discovery_without_a_header_uses_the_well_known_address(db, mock_http):
    provider = Provider(header=False)
    mock_http(provider)
    assert (await oauth.discover(_server(db))).issuer == ISSUER


async def test_metadata_naming_another_issuer_is_refused(db, mock_http):
    mock_http(Provider(issuer_said="https://evil.test"))
    with pytest.raises(McpError, match="another issuer"):
        await oauth.discover(_server(db))


async def test_metadata_describing_another_server_is_refused(db, mock_http):
    mock_http(Provider(resource_said="https://elsewhere.test/rpc"))
    with pytest.raises(McpError, match="another server"):
        await oauth.discover(_server(db))


@pytest.mark.parametrize("offered", [None, ["plain"]])
async def test_a_provider_without_s256_is_refused(db, mock_http, offered):
    mock_http(Provider(pkce=offered))
    with pytest.raises(McpError, match="PKCE"):
        await oauth.discover(_server(db))


async def test_a_provider_on_a_private_address_is_refused_unless_the_row_allows_it(
    db, mock_http
):
    """The server row's own rule, kept for everything a sign-in asks."""
    from lembas.services.fetch import FetchError

    def handler(request):
        if request.url.host == "mcp.test" and request.url.path == "/rpc":
            return httpx.Response(401, headers={
                "www-authenticate": 'Bearer resource_metadata="https://inside.test/prm"'})
        return httpx.Response(200, json={"resource": MCP,
                                         "authorization_servers": ["https://inside.test"]})

    mock_http(handler)
    with pytest.raises(FetchError, match="private or local"):
        await oauth.discover(_server(db))


async def test_an_endpoint_that_is_not_https_is_refused(db, mock_http):
    provider = Provider()

    def handler(request):
        response = provider(request)
        if request.url.path == "/.well-known/oauth-authorization-server":
            document = response.json() | {"token_endpoint": "http://auth.test/token"}
            return httpx.Response(200, json=document)
        return response

    mock_http(handler)
    with pytest.raises(McpError, match="not https"):
        await oauth.discover(_server(db))


# --- The whole sign-in -------------------------------------------------------------------
def test_a_person_signs_in_and_the_tool_runs_as_them(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    admin = _admin(db)
    assert registry.tool_defs(db, admin) == []  # nobody signed in: not offered

    landed = _sign_in(client, provider, server)
    assert landed.status_code == 303
    assert landed.headers["location"] == f"/settings/mcp/{server.id}/sign-in?done=1"

    # Registered by itself, for this instance's own address.
    assert provider.registered[0]["redirect_uris"] == [BASE + oauth.CALLBACK_PATH]
    asked = provider.asked[0]
    assert asked["client_id"] == "dyn-1" and asked["response_type"] == "code"
    assert asked["code_challenge_method"] == "S256" and asked["resource"] == MCP
    assert asked["scope"] == "read write"
    exchange = provider.token_forms[0]
    assert exchange["resource"] == MCP and exchange["redirect_uri"] == asked["redirect_uri"]
    assert oauth.challenge_of(exchange["code_verifier"]) == asked["code_challenge"]

    row = _signin(db, server, admin)
    assert row is not None and decrypt(row.access_encrypted) == "at-2"
    assert "at-2" not in row.access_encrypted and "rt-2" not in row.refresh_encrypted
    assert db.scalar(select(McpOauthState)) is None  # used once, gone

    defs = registry.tool_defs(db, admin)
    assert [tool.name for tool in defs] == ["mail_search"]


async def test_the_offered_tool_calls_with_the_token(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    row = _signin(db, server, _admin(db))
    text, failed = await mcp_client.call_tool(
        mcp_client.spec_from(server, signin_id=row.id), "search", {}
    )
    assert (text, failed) == ("called as at-2", False)


def test_a_typed_client_is_used_and_nothing_is_registered(client, db, registered, mock_http):
    provider = Provider(registers=False)
    mock_http(provider)
    server = _server(db, oauth_client_id="typed-id",
                     oauth_client_secret_encrypted=encrypt("typed-secret"),
                     oauth_scopes="only this")
    assert _sign_in(client, provider, server).status_code == 303
    assert provider.registered == []
    assert provider.asked[0]["client_id"] == "typed-id"
    assert provider.asked[0]["scope"] == "only this"
    token_request = next(r for r in provider.seen if r.url.path == "/token")
    # The secret goes to the token endpoint, as HTTP Basic, and nowhere else.
    assert base64.b64decode(token_request.headers["authorization"][6:]) == b"typed-id:typed-secret"
    assert "typed-secret" not in " ".join(str(r.url) for r in provider.seen)
    assert _signin(db, server, _admin(db)) is not None


def test_a_provider_that_registers_nobody_says_what_to_type(client, db, registered, mock_http):
    mock_http(Provider(registers=False))
    page = client.post(f"/settings/mcp/{_server(db).id}/sign-in")
    assert page.status_code == 200
    assert "does not register applications by itself" in page.text
    assert BASE + oauth.CALLBACK_PATH in page.text


def test_a_start_that_fails_under_htmx_comes_back_to_the_page_with_the_reason(
    client, db, registered, mock_http
):
    mock_http(Provider(pkce=None))
    server = _server(db)
    answer = _start(client, server)
    assert answer.status_code == 204
    page = client.get(answer.headers["HX-Redirect"])
    assert 'data-testid="mcp-signin-error"' in page.text and "PKCE" in page.text


def test_without_htmx_the_page_links_to_the_provider(client, db, registered, mock_http):
    """A form may not leave this origin (`form-action 'self'`), so the plain
    answer is a page with a link, never a redirect."""
    mock_http(Provider())
    page = client.post(f"/settings/mcp/{_server(db).id}/sign-in", follow_redirects=False)
    assert page.status_code == 200 and 'data-testid="mcp-signin-go"' in page.text
    assert f'href="{ISSUER}/authorize?' in page.text


# --- State -------------------------------------------------------------------------------
def test_a_state_is_used_once(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    started = _start(client, server)
    back = provider.authorize(started.headers["HX-Redirect"])
    assert client.get(oauth.CALLBACK_PATH, params=back, follow_redirects=False).status_code == 303
    exchanges = len(provider.token_forms)
    again = client.get(oauth.CALLBACK_PATH, params=back, follow_redirects=False)
    assert again.status_code == 400 and "not a sign-in this instance is waiting for" in again.text
    assert len(provider.token_forms) == exchanges  # nothing was asked of the provider


def test_an_unknown_state_is_refused(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    answer = client.get(oauth.CALLBACK_PATH, params={"code": "x", "state": "made-up"})
    assert answer.status_code == 400 and provider.token_forms == []


def test_another_persons_state_is_refused_and_signs_nobody_in(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    back = provider.authorize(_start(client, server).headers["HX-Redirect"])

    join(client, SAM)  # the same browser is now somebody else
    answer = client.get(oauth.CALLBACK_PATH, params=back, follow_redirects=False)
    assert answer.status_code == 400 and "started by another account" in answer.text
    assert provider.token_forms == []
    assert db.scalar(select(McpSignin)) is None


def test_a_state_that_took_too_long_is_refused(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    back = provider.authorize(_start(client, server).headers["HX-Redirect"])
    row = db.scalar(select(McpOauthState))
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    answer = client.get(oauth.CALLBACK_PATH, params=back, follow_redirects=False)
    assert answer.status_code == 400 and "took too long" in answer.text
    assert provider.token_forms == []


def test_the_callback_needs_the_instances_own_session(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    back = provider.authorize(_start(client, _server(db)).headers["HX-Redirect"])
    client.cookies.clear()
    answer = client.get(oauth.CALLBACK_PATH, params=back, follow_redirects=False)
    assert answer.status_code in (302, 303, 307) and "/auth/login" in answer.headers["location"]
    assert provider.token_forms == []


def test_a_wrong_verifier_would_be_refused_by_the_provider(client, db, registered, mock_http):
    """The fake checks PKCE: with the stored verifier changed, no sign-in."""
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    back = provider.authorize(_start(client, server).headers["HX-Redirect"])
    row = db.scalar(select(McpOauthState))
    row.verifier_encrypted = encrypt("not-the-verifier")
    db.commit()
    answer = client.get(oauth.CALLBACK_PATH, params=back, follow_redirects=False)
    assert answer.status_code == 400 and "refused the sign-in" in answer.text
    assert db.scalar(select(McpSignin)) is None


def test_an_answer_from_another_issuer_is_refused(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    back = provider.authorize(_start(client, _server(db)).headers["HX-Redirect"])
    answer = client.get(oauth.CALLBACK_PATH, params={**back, "iss": "https://evil.test"})
    assert answer.status_code == 400 and provider.token_forms == []


# --- Renewing ----------------------------------------------------------------------------
def _lapse(db, row):
    row.expires_at = datetime.now(UTC) - timedelta(seconds=5)
    db.commit()


async def test_a_token_about_to_lapse_is_renewed_first(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    row = _signin(db, server, _admin(db))
    _lapse(db, row)
    assert await oauth.token_for(row.id) == "at-3"
    row = _signin(db, server, _admin(db))
    assert decrypt(row.refresh_encrypted) == "rt-3"  # the rotated one is kept
    assert provider.token_forms[-1]["resource"] == MCP


async def test_a_refused_token_is_renewed_and_the_call_made_again(
    client, db, registered, mock_http
):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    row = _signin(db, server, _admin(db))
    provider.access.clear()  # the server forgot the token before its time
    text, _ = await mcp_client.call_tool(
        mcp_client.spec_from(server, signin_id=row.id), "search", {}
    )
    assert text == "called as at-3"


async def test_two_calls_at_once_renew_once(client, db, registered, mock_http):
    import asyncio

    provider = Provider()

    async def slow(request):
        # A real endpoint takes a moment, and the second call arrives in it.
        if request.url.path == "/token":
            await asyncio.sleep(0.02)
        return provider(request)

    mock_http(slow)
    server = _server(db)
    _sign_in(client, provider, server)
    row = _signin(db, server, _admin(db))
    _lapse(db, row)
    before = len(provider.token_forms)
    tokens = await asyncio.gather(*(oauth.token_for(row.id) for _ in range(4)))
    assert set(tokens) == {"at-3"}
    assert len(provider.token_forms) == before + 1


async def test_a_failed_renewal_needs_a_sign_in_and_the_tools_go(
    client, db, registered, mock_http
):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    admin = _admin(db)
    row = _signin(db, server, admin)
    _lapse(db, row)
    provider.refresh_refused = True

    with pytest.raises(oauth.SigninNeeded):
        await oauth.token_for(row.id)
    row = _signin(db, server, admin)
    assert row.state == SIGNIN_NEEDS and row.access_encrypted == "" == row.refresh_encrypted
    assert oauth.view(db, server, admin)["state"] == "needs_signin"
    assert registry.tool_defs(db, admin) == []

    # A tool built before it lapsed answers in words, not with a raw error.
    run = registry._runner(mcp_client.spec_from(server, signin_id=row.id), "search", "mail_search")
    outcome = await run(None, {})
    assert "Nobody is signed in to Mail" in outcome.content


async def test_a_provider_that_is_down_does_not_end_the_sign_in(
    client, db, registered, mock_http
):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    row = _signin(db, server, _admin(db))
    _lapse(db, row)
    provider.token_down = True
    with pytest.raises(McpError) as caught:
        await oauth.token_for(row.id)
    assert not isinstance(caught.value, oauth.SigninNeeded)
    assert oauth.usable(_signin(db, server, _admin(db)))


async def test_a_token_refused_twice_ends_the_sign_in(client, db, registered, mock_http):
    provider = Provider()
    real = provider._mcp
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    row = _signin(db, server, _admin(db))
    # The server honours nothing any more, whatever the provider issues.
    provider._mcp = lambda request, path: (
        httpx.Response(401) if path == "/rpc" else real(request, path)
    )
    with pytest.raises(oauth.SigninNeeded):
        await mcp_client.call_tool(mcp_client.spec_from(server, signin_id=row.id), "search", {})
    assert _signin(db, server, _admin(db)).state == SIGNIN_NEEDS


# --- Where a token goes ------------------------------------------------------------------
async def test_a_token_is_not_carried_to_another_origin(client, db, registered, mock_http):
    provider = Provider()
    elsewhere: list[httpx.Request] = []

    def handler(request):
        if request.url.host == "other.test":
            elsewhere.append(request)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if (
            request.url.host == "mcp.test"
            and request.url.path == "/rpc"
            and request.headers.get("authorization", "").startswith("Bearer at-")
        ):
            return httpx.Response(307, headers={"location": "https://other.test/rpc"})
        return provider(request)

    mock_http(handler)
    # A header name left over from a secret must not be what saves the token.
    server = _server(db, headers_json={"X-Team": "shire"}, secret_name="X-Api-Key")
    _sign_in(client, provider, server)
    row = _signin(db, server, _admin(db))
    spec = mcp_client.spec_from(server, signin_id=row.id)
    async with mcp_client.session_for(spec):
        pass
    assert elsewhere and all("authorization" not in r.headers for r in elsewhere)


def test_tokens_reach_only_the_server_and_the_token_endpoint(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    for request in provider.seen:
        carried = request.headers.get("authorization", "") + request.content.decode()
        where = (request.url.host, request.url.path)
        if "at-" in carried or "rt-" in carried:
            assert where in {("mcp.test", "/rpc"), ("auth.test", "/token")}, where
        assert "at-" not in str(request.url) and "rt-" not in str(request.url)


def test_an_oauth_server_sends_no_stored_secret(db):
    server = _server(db, secret_encrypted=encrypt("old-secret"),
                     headers_json={"authorization": "Bearer typed-by-hand"})
    spec = mcp_client.spec_from(server)
    assert spec.oauth and spec.secret == ""
    assert "authorization" not in {k.lower() for k in mcp_client._headers(spec)}


# --- Whose sign-in -----------------------------------------------------------------------
def test_each_person_has_their_own_sign_in(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    admin = _admin(db)

    join(client, SAM)
    sam = db.scalar(select(User).where(User.email == SAM["email"]))
    # Signed in by somebody else is not signed in.
    assert registry.tool_defs(db, sam) == []
    assert oauth.view(db, server, sam)["state"] == "signed_out"

    assert _sign_in(client, provider, server).status_code == 303
    mine, theirs = _signin(db, server, sam), _signin(db, server, admin)
    assert mine.id != theirs.id
    assert decrypt(mine.access_encrypted) != decrypt(theirs.access_encrypted)
    assert [t.name for t in registry.tool_defs(db, sam)] == ["mail_search"]


def test_a_shared_sign_in_is_an_administrators_and_serves_everyone(
    client, db, registered, mock_http
):
    provider = Provider()
    mock_http(provider)
    server = _server(db, oauth_shared=True)

    join(client, SAM)
    sam = db.scalar(select(User).where(User.email == SAM["email"]))
    assert _start(client, server).status_code == 403
    assert registry.tool_defs(db, sam) == []
    page = client.get(f"/settings/mcp/{server.id}/sign-in")
    assert "which an administrator makes" in page.text

    client.post("/auth/login", data={"email": registered["email"],
                                     "password": registered["password"]})
    assert _sign_in(client, provider, server).status_code == 303
    shared = _signin(db, server)
    assert shared is not None and shared.signed_in_by == _admin(db).id
    assert [t.name for t in registry.tool_defs(db, sam)] == ["mail_search"]
    view = oauth.view(db, server, sam)
    assert view["state"] == "signed_in" and view["shared"] and not view["can_sign_in"]

    # And only an administrator ends it.
    client.post("/auth/login", data={"email": SAM["email"], "password": SAM["password"]})
    assert client.post(f"/settings/mcp/{server.id}/sign-out").status_code == 403
    assert _signin(db, server) is not None


def test_a_server_a_person_may_not_use_cannot_be_signed_in_to(client, db, registered, mock_http):
    mock_http(Provider())
    server = _server(db, public=False)
    join(client, SAM)
    assert client.get(f"/settings/mcp/{server.id}/sign-in").status_code == 404
    assert _start(client, server).status_code == 404


def test_changing_the_address_signs_everyone_out(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    form = {"name": "Mail", "slug": "mail", "url": "https://mcp.test/moved",
            "auth_kind": "oauth", "enabled": "true", "public": "true"}
    assert client.post(f"/admin/mcp/{server.id}", data=form,
                       follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.scalar(select(McpSignin)) is None
    assert not db.get(McpServer, server.id).oauth_client_encrypted


# --- Signing out -------------------------------------------------------------------------
def test_signing_out_revokes_and_forgets(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    answer = client.post(f"/settings/mcp/{server.id}/sign-out", follow_redirects=False)
    assert answer.status_code == 303 and "withdrew" in answer.headers["location"]
    assert sorted(provider.revoked) == ["at-2", "rt-2"]
    assert _signin(db, server, _admin(db)) is None
    assert registry.tool_defs(db, _admin(db)) == []


# --- Nothing secret is shown or written down ---------------------------------------------
def test_no_token_or_secret_is_in_a_page_or_a_log_line(
    client, db, registered, mock_http, caplog
):
    provider = Provider(registers=False)
    mock_http(provider)
    server = _server(db, oauth_client_id="typed-id",
                     oauth_client_secret_encrypted=encrypt("typed-secret"))
    with caplog.at_level(logging.DEBUG):
        _sign_in(client, provider, server)
        token = _signin(db, server, _admin(db))
        app_token = _app_token(db)
        pages = [
            client.get("/settings?tab=security").text,
            client.get("/admin/mcp").text,
            client.get(f"/admin/mcp/{server.id}/edit").text,
            client.get(f"/settings/mcp/{server.id}/sign-in?done=1").text,
            client.post(f"/admin/mcp/{server.id}/test").text,
            client.get("/api/v2/mcp", headers={"Authorization": f"Bearer {app_token}"}).text,
        ]
    secrets = ["at-2", "rt-2", "typed-secret", decrypt(token.access_encrypted),
               token.access_encrypted, token.refresh_encrypted,
               provider.token_forms[0]["code_verifier"]]
    for text in [*pages, caplog.text]:
        for secret in secrets:
            assert secret not in text
    assert 'data-testid="mcp-signins"' in pages[0] and "signed in" in pages[0]


def _app_token(db) -> str:
    from lembas.security import api_tokens

    _, raw = api_tokens.create(db, _admin(db), name="app", scopes=["app"])
    return raw


# --- What the apps are told --------------------------------------------------------------
def test_the_apps_see_the_state_and_where_to_sign_in(client, db, registered, mock_http):
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    db.add(McpServer(slug="open", name="Open", url="https://open.test/rpc", tools_json=[]))
    db.commit()
    headers = {"Authorization": f"Bearer {_app_token(db)}"}

    got = {s["slug"]: s for s in client.get("/api/v2/mcp", headers=headers).json()["servers"]}
    assert got["open"]["auth"] == {"kind": "none", "state": "ready", "shared": False,
                                   "account": "", "expires_at": None, "renews": False,
                                   "signin_url": None}
    assert got["mail"]["auth"]["state"] == "signed_out"
    assert got["mail"]["auth"]["signin_url"] == f"{BASE}/settings/mcp/{server.id}/sign-in"

    _sign_in(client, provider, server)
    mail = client.get("/api/v2/mcp", headers=headers).json()["servers"][0]
    assert mail["auth"]["state"] == "signed_in" and mail["auth"]["renews"]
    assert mail["auth"]["signin_url"] is None and mail["tools"] == 1

    want = json.loads(
        (__import__("pathlib").Path(__file__).parent / "fixtures/api_v2/mcp_servers.json")
        .read_text(encoding="utf-8")
    )["servers"][0]
    assert set(mail) == set(want) and set(mail["auth"]) == set(want["auth"])


def test_who_signed_in_comes_from_the_provider_and_is_shown_escaped(
    client, db, registered, mock_http
):
    claims = base64.urlsafe_b64encode(
        json.dumps({"email": "<b>frodo@example.org</b>"}).encode()
    ).rstrip(b"=").decode()
    provider = Provider(id_token=f"e30.{claims}.sig")
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    assert _signin(db, server, _admin(db)).account == "<b>frodo@example.org</b>"
    page = client.get("/settings?tab=security").text
    assert "&lt;b&gt;frodo@example.org&lt;/b&gt;" in page and "<b>frodo@" not in page


# --- Review fixes ---------------------------------------------------------------------------
class Strict(Provider):
    """A provider whose registered clients have secrets, and which checks them."""

    def __init__(self, **options):
        super().__init__(**options)
        self.secrets: dict[str, str] = {}
        self.meanwhile = None  # called once, while a renewal is being answered

    def _auth(self, request, path):
        if path == "/register":
            self.registered.append(json.loads(request.content))
            number = len(self.registered)
            self.secrets[f"dyn-{number}"] = f"sec-{number}"
            return httpx.Response(201, json={
                "client_id": f"dyn-{number}", "client_secret": f"sec-{number}",
                "token_endpoint_auth_method": "client_secret_basic"})
        if path == "/token":
            basic = request.headers.get("authorization", "")
            name, _, secret = (
                base64.b64decode(basic[6:]).decode().partition(":")
                if basic.startswith("Basic ") else ("", "", "")
            )
            if self.secrets.get(name) != secret:
                return httpx.Response(401, json={"error": "invalid_client"})
            if self.meanwhile is not None and b"refresh_token" in request.content:
                hook, self.meanwhile = self.meanwhile, None
                hook()
        return super()._auth(request, path)


def test_signing_in_from_another_address_keeps_the_first_sign_ins_client(
    client, db, registered, mock_http
):
    """One client per redirect address, kept side by side: a sign-in started
    from another hostname must not take the first one's client secret away."""
    provider = Strict()
    mock_http(provider)
    server = _server(db)
    assert _sign_in(client, provider, server).status_code == 303
    first = _signin(db, server, _admin(db))
    assert first.meta_json["client_id"] == "dyn-1"

    join(client, SAM)
    started = client.post(f"/settings/mcp/{server.id}/sign-in", follow_redirects=False,
                          headers={"HX-Request": "true", "Host": "other.test"})
    assert started.status_code == 204
    assert provider.registered[1]["redirect_uris"] == ["http://other.test" + oauth.CALLBACK_PATH]
    db.expire_all()
    kept = oauth._registered(db.get(McpServer, server.id))
    assert [c["client_id"] for c in kept] == ["dyn-1", "dyn-2"]

    # The first sign-in still renews: with its own client and that client's secret.
    _lapse(db, first)
    token = asyncio.new_event_loop().run_until_complete(oauth.token_for(first.id, "Mail"))
    assert token.startswith("at-")
    row = _signin(db, server, _admin(db))
    assert row.state != SIGNIN_NEEDS and decrypt(row.access_encrypted) == token


def test_a_configured_public_address_is_where_every_sign_in_comes_back_to(
    client, db, registered, mock_http, monkeypatch
):
    from lembas.config import settings

    monkeypatch.setattr(settings, "public_url", "https://chat.example.org/")
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    started = client.post(f"/settings/mcp/{server.id}/sign-in", follow_redirects=False,
                          headers={"HX-Request": "true", "Host": "anything.test"})
    assert started.status_code == 204
    wanted = "https://chat.example.org" + oauth.CALLBACK_PATH
    assert provider.registered[0]["redirect_uris"] == [wanted]
    assert dict(parse_qsl(urlsplit(started.headers["HX-Redirect"]).query))["redirect_uri"] == wanted


def test_only_so_many_clients_are_registered_and_none_is_dropped_to_make_room(
    client, db, registered, mock_http, monkeypatch
):
    monkeypatch.setattr(oauth, "MAX_CLIENTS", 2)
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    for host in ("one.test", "two.test"):
        assert client.post(f"/settings/mcp/{server.id}/sign-in", follow_redirects=False,
                           headers={"HX-Request": "true", "Host": host}).status_code == 204
    third = client.post(f"/settings/mcp/{server.id}/sign-in", follow_redirects=False,
                        headers={"HX-Request": "true", "Host": "three.test"})
    assert "error=" in third.headers["HX-Redirect"] and len(provider.registered) == 2
    db.expire_all()
    assert len(oauth._registered(db.get(McpServer, server.id))) == 2


class Moved(Provider):
    """The MCP server now names a provider of its own (`evil.test`)."""

    moved = False

    def __call__(self, request):
        if request.url.host == "evil.test":
            self.seen.append(request)
            if request.url.path == "/.well-known/oauth-authorization-server":
                return httpx.Response(200, json={
                    "issuer": "https://evil.test",
                    "authorization_endpoint": "https://evil.test/authorize",
                    "token_endpoint": "https://evil.test/token",
                    "response_types_supported": ["code"],
                    "code_challenge_methods_supported": ["S256"]})
            return httpx.Response(200, json={"access_token": "stolen", "token_type": "Bearer"})
        return super().__call__(request)

    def _mcp(self, request, path):
        if self.moved and path == "/.well-known/oauth-protected-resource/rpc":
            self.seen.append(request)
            return httpx.Response(200, json={"resource": MCP,
                                             "authorization_servers": ["https://evil.test"]})
        return super()._mcp(request, path)


def test_a_typed_client_is_pinned_to_its_provider_and_its_secret_goes_nowhere_else(
    client, db, registered, mock_http
):
    provider = Moved(registers=False)
    mock_http(provider)
    server = _server(db, oauth_client_id="typed-id",
                     oauth_client_secret_encrypted=encrypt("typed-secret"))
    assert _sign_in(client, provider, server).status_code == 303
    db.expire_all()
    assert db.get(McpServer, server.id).oauth_issuer == ISSUER
    # The form shows what it is pinned to.
    page = client.get(f"/admin/mcp/{server.id}/edit")
    assert 'data-testid="mcp-pinned-issuer"' in page.text and ISSUER in page.text

    # The server changes hands and names a provider of its own.
    provider.moved = True
    started = _start(client, server)
    assert started.status_code == 204
    assert "names%20another%20sign-in%20provider" in started.headers["HX-Redirect"]
    well_known = "/.well-known/oauth-authorization-server"
    assert not any(r.url.host == "evil.test" and r.url.path != well_known
                   for r in provider.seen)
    assert db.scalar(select(McpOauthState)) is None
    # The existing sign-in is untouched, and the secret is found for its own provider only.
    assert _signin(db, server, _admin(db)) is not None
    row = db.get(McpServer, server.id)
    assert oauth._client_secret(row, "typed-id", ISSUER) == "typed-secret"
    assert oauth._client_secret(row, "typed-id", "https://evil.test") == ""

    # An administrator who expects the change re-pins: everyone signs in again.
    assert client.post(f"/admin/mcp/{server.id}/repin",
                       follow_redirects=False).status_code == 303
    db.expire_all()
    assert not db.get(McpServer, server.id).oauth_issuer and db.scalar(select(McpSignin)) is None
    go = _start(client, server).headers["HX-Redirect"]
    assert go.startswith("https://evil.test/authorize?")
    db.expire_all()
    assert db.get(McpServer, server.id).oauth_issuer == "https://evil.test"


def test_a_registered_clients_secret_is_found_by_its_provider_not_by_its_id_alone(db):
    server = _server(db)
    server.oauth_client_encrypted = encrypt(json.dumps({"clients": [
        {"issuer": ISSUER, "redirect_uri": "x", "client_id": "same", "client_secret": "real"},
        {"issuer": "https://evil.test", "redirect_uri": "x", "client_id": "same",
         "client_secret": ""},
    ]}))
    db.commit()
    assert oauth._client_secret(server, "same", ISSUER) == "real"
    assert oauth._client_secret(server, "same", "https://evil.test") == ""
    assert oauth._client_secret(server, "same", "") == ""
    # A row from before there could be several clients still reads.
    server.oauth_client_encrypted = encrypt(json.dumps(
        {"issuer": ISSUER, "redirect_uri": "x", "client_id": "old", "client_secret": "s"}))
    assert oauth._client_secret(server, "old", ISSUER) == "s"


async def test_a_metadata_document_is_not_read_past_its_cap(db, mock_http):
    pulled = {"chunks": 0}

    async def endless():
        for _ in range(4096):
            pulled["chunks"] += 1
            yield b" " * 4096

    def handle(request):
        return httpx.Response(200, content=endless())

    mock_http(handle)
    reach = oauth._Reach("Mail", MCP, False)
    assert await oauth._get(reach, "https://mcp.test/.well-known/big") is None
    # 16 MB were on offer; reading stopped at the cap (plus the chunk that crossed it).
    assert pulled["chunks"] <= oauth.MAX_DOCUMENT_BYTES // 4096 + 2
    with pytest.raises(McpError) as caught:
        await oauth._post(reach, "https://auth.test/token", form={"a": "b"})
    assert "more than this reads" in caught.value.message


@pytest.mark.parametrize("refused", [False, True])
async def test_a_renewal_in_flight_does_not_write_over_a_newer_sign_in(
    client, db, registered, mock_http, refused
):
    """The same person signs in again while a renewal of the old sign-in is
    on its way. The row then holds the new sign-in; what the renewal brings
    back -- tokens of the old one, or a refusal of it -- is not put over it."""
    from lembas.db.session import session_scope

    provider = Strict()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    row = _signin(db, server, _admin(db))
    _lapse(db, row)
    signin_id = row.id

    def signs_in_again():
        provider.access.add("at-new")
        with session_scope() as other:
            fresh = other.get(McpSignin, signin_id)
            fresh.access_encrypted, fresh.refresh_encrypted = encrypt("at-new"), encrypt("rt-new")
            fresh.expires_at = datetime.now(UTC) + timedelta(hours=1)
        provider.refresh_refused = refused

    provider.meanwhile = signs_in_again
    assert await oauth.token_for(signin_id, "Mail") == "at-new"
    row = _signin(db, server, _admin(db))
    assert row.state != SIGNIN_NEEDS
    assert (decrypt(row.access_encrypted), decrypt(row.refresh_encrypted)) == ("at-new", "rt-new")


def _collect_revocations(monkeypatch) -> list:
    held: list = []
    monkeypatch.setattr(oauth, "revoke_later", lambda leaving: held.extend(leaving))
    return held


def test_deleting_a_server_or_changing_its_address_withdraws_its_sign_ins_at_the_provider(
    client, db, registered, mock_http, monkeypatch
):
    held = _collect_revocations(monkeypatch)
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    form = {"name": "Mail", "slug": "mail", "url": "https://mcp.test/moved",
            "auth_kind": "oauth", "enabled": "true", "public": "true"}
    assert client.post(f"/admin/mcp/{server.id}", data=form,
                       follow_redirects=False).status_code == 303
    assert asyncio.new_event_loop().run_until_complete(oauth.revoke_all(held)) == 1
    assert sorted(provider.revoked) == ["at-2", "rt-2"]

    held.clear()
    provider.revoked.clear()
    other = _server(db, slug="notes", name="Notes")
    _sign_in(client, provider, other)
    assert client.post(f"/admin/mcp/{other.id}/delete",
                       follow_redirects=False).status_code == 303
    asyncio.new_event_loop().run_until_complete(oauth.revoke_all(held))
    assert len(provider.revoked) == 2 and db.scalar(select(McpSignin)) is None


def test_switching_the_shared_sign_in_on_lets_go_of_each_persons_own(
    client, db, registered, mock_http, monkeypatch
):
    held = _collect_revocations(monkeypatch)
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    _sign_in(client, provider, server)
    assert _signin(db, server, _admin(db)) is not None
    form = {"name": "Mail", "slug": "mail", "url": MCP, "auth_kind": "oauth",
            "enabled": "true", "public": "true", "oauth_shared": "true"}
    assert client.post(f"/admin/mcp/{server.id}", data=form,
                       follow_redirects=False).status_code == 303
    db.expire_all()
    # Not kept unused where its person cannot see or end it: gone, and withdrawn.
    assert db.scalar(select(McpSignin)) is None
    asyncio.new_event_loop().run_until_complete(oauth.revoke_all(held))
    assert sorted(provider.revoked) == ["at-2", "rt-2"]


def test_a_person_can_always_end_their_own_sign_in_even_on_a_shared_server(
    client, db, registered, mock_http
):
    """A personal sign-in left from before the server was shared."""
    provider = Provider()
    mock_http(provider)
    server = _server(db)
    join(client, SAM)
    sam = db.scalar(select(User).where(User.email == SAM["email"]))
    assert _sign_in(client, provider, server).status_code == 303
    db.get(McpServer, server.id).oauth_shared = True
    db.commit()
    assert _signin(db, server, sam) is not None
    answer = client.post(f"/settings/mcp/{server.id}/sign-out", follow_redirects=False)
    assert answer.status_code == 303
    assert _signin(db, server, sam) is None and provider.revoked


def test_the_resource_asked_for_is_the_server_itself_whatever_wider_thing_is_named(
    client, db, registered, mock_http
):
    """A document may name the whole origin. A token asked for with that
    would be good for every other path there; what is asked for is this
    server's own address."""
    provider = Provider(resource_said="https://mcp.test")
    mock_http(provider)
    server = _server(db)
    assert _sign_in(client, provider, server).status_code == 303
    assert provider.asked[0]["resource"] == MCP
    assert provider.token_forms[0]["resource"] == MCP
    assert _signin(db, server, _admin(db)).meta_json["resource"] == MCP


def test_the_instances_tool_list_is_not_set_by_whoever_signs_in_first(
    client, db, registered, mock_http
):
    provider = Provider()
    mock_http(provider)
    server = _server(db, tools_json=[])
    join(client, SAM)
    assert _sign_in(client, provider, server).status_code == 303
    db.expire_all()
    # Sam is signed in; what *his* account is offered there is not everybody's list.
    assert db.get(McpServer, server.id).tools_json == []
    client.post("/auth/login", data={"email": registered["email"],
                                     "password": registered["password"]})
    assert _sign_in(client, provider, server).status_code == 303
    db.expire_all()
    assert [t["name"] for t in db.get(McpServer, server.id).tools_json] == ["search"]


def test_a_sign_in_from_before_the_pin_still_renews_and_is_what_the_client_is_pinned_to(
    client, db, registered, mock_http
):
    """An instance upgraded with a typed client and people signed in: the
    row has no pin yet. They are not signed out by the upgrade, and the pin
    is the provider they signed in at -- not whatever the server names now."""
    provider = Moved(registers=False)
    mock_http(provider)
    server = _server(db, oauth_client_id="typed-id",
                     oauth_client_secret_encrypted=encrypt("typed-secret"))
    _sign_in(client, provider, server)
    row = db.get(McpServer, server.id)
    row.oauth_issuer = None  # as the release before left it
    db.commit()
    signin = _signin(db, server, _admin(db))
    _lapse(db, signin)
    token = asyncio.new_event_loop().run_until_complete(oauth.token_for(signin.id, "Mail"))
    assert token.startswith("at-")
    renewal = [r for r in provider.seen if r.url.path == "/token"][-1]
    assert base64.b64decode(renewal.headers["authorization"][6:]) == b"typed-id:typed-secret"
    provider.moved = True
    assert "names%20another" in _start(client, server).headers["HX-Redirect"]
    db.expire_all()
    assert db.get(McpServer, server.id).oauth_issuer == ISSUER
