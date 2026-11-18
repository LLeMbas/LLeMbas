"""Signing in to an MCP server with OAuth (the MCP authorization specification).

What happens, in the order it happens:

1. **Discovery.** The server is asked once without a credential. Its 401 names
   its protected resource metadata (RFC 9728), or the well-known addresses are
   tried; that document names the authorization server, whose own metadata
   (RFC 8414, then OpenID Connect discovery) names the endpoints. A server
   from before protected resource metadata is its own authorization server.
2. **A client.** One an administrator typed, else one registered here by
   itself (RFC 7591), kept on the server's row.
3. **The person's browser** goes to the provider with a PKCE challenge
   (S256), the `resource` the token is for (RFC 8707) and a `state`; it comes
   back to this instance's own address.
4. **The code is exchanged** for tokens, which are stored encrypted, renewed
   before they lapse and when the server refuses one, and never rendered.

Everything this module asks for goes through `fetch.check_url` with the
server row's own "may reach private addresses" switch, every hop -- the rule
the MCP client itself is held to. A token request is never redirected.

A token is only ever sent to the token endpoint that issued it (to renew or
revoke it) and, by `client.Session`, to the MCP server's own origin.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    AUTH_KINDS,
    AUTH_NONE,
    AUTH_OAUTH,
    AUTH_SECRET,
    SECRET_NONE,
    SIGNIN_NEEDS,
    SIGNIN_OK,
    McpOauthState,
    McpServer,
    McpSignin,
    User,
)
from lembas.db.session import session_scope
from lembas.services import fetch as fetch_service
from lembas.services.crypto import decrypt, encrypt
from lembas.services.fetch import FetchError
from lembas.services.mcp import protocol
from lembas.services.mcp.protocol import McpError

log = logging.getLogger(__name__)

# Where a provider sends the person back to, on this instance's own address.
# Not under /mcp: that prefix is this instance's own MCP server, a token door.
CALLBACK_PATH = "/oauth/mcp/callback"

STATE_LIFETIME = timedelta(minutes=10)
# A token this close to its end is renewed before it is used.
REFRESH_MARGIN = timedelta(seconds=60)

MAX_DOCUMENT_BYTES = 256 * 1024
TIMEOUT = 15.0
CLIENT_NAME = "LLeMbas"


class SigninNeeded(McpError):
    """Nobody is signed in to the server, or the sign-in is no longer honoured."""


class _Refused(Exception):
    """The authorization server said no to a token request, in its own words."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


# --- What a row says --------------------------------------------------------------
def kind_of(server: McpServer) -> str:
    """How a server is signed in to. A row from before the choice existed is
    a secret when it has one to send."""
    if server.auth_kind in AUTH_KINDS:
        return str(server.auth_kind)
    if server.secret_encrypted and server.secret_placement != SECRET_NONE:
        return AUTH_SECRET
    return AUTH_NONE


def is_oauth(server: McpServer) -> bool:
    return kind_of(server) == AUTH_OAUTH


def signin_for(db: DBSession, server: McpServer, user: User | None) -> McpSignin | None:
    """The sign-in a person's calls to this server go out with: the shared one
    where the server has that switch on, else the person's own."""
    if server.oauth_shared:
        owner = McpSignin.user_id.is_(None)
    elif user is not None:
        owner = McpSignin.user_id == user.id
    else:
        return None
    return db.scalar(select(McpSignin).where(McpSignin.server_id == server.id, owner))


def usable(row: McpSignin | None) -> bool:
    return row is not None and row.state == SIGNIN_OK and bool(row.access_encrypted)


def may_sign_in(server: McpServer, user: User) -> bool:
    """The shared sign-in is an administrator's to make: everyone acts as it."""
    return bool(user.is_admin) or not server.oauth_shared


def _aware(moment: datetime | None) -> datetime | None:
    # SQLite hands a stored moment back without its offset.
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def view(db: DBSession, server: McpServer, user: User | None) -> dict[str, Any]:
    """What a page or an app is told about a server's sign-in. No secret."""
    kind = kind_of(server)
    out: dict[str, Any] = {
        "kind": kind,
        "state": "ready",
        "shared": False,
        "account": "",
        "by": "",
        "expires_at": None,
        "renews": False,
        "can_sign_in": False,
        "error": "",
    }
    if kind != AUTH_OAUTH:
        return out
    row = signin_for(db, server, user)
    out["shared"] = bool(server.oauth_shared)
    out["can_sign_in"] = user is not None and may_sign_in(server, user)
    if row is None:
        out["state"] = "signed_out"
        return out
    if not usable(row):
        out["state"] = "needs_signin"
        out["error"] = row.last_error or ""
        return out
    renews = bool(row.refresh_encrypted)
    by = db.get(User, row.signed_in_by) if row.signed_in_by else None
    out.update(
        state="signed_in",
        account=row.account or "",
        by=(by.email if by is not None else ""),
        expires_at=None if renews else _aware(row.expires_at),
        renews=renews,
    )
    return out


def forget(
    db: DBSession, server: McpServer, *, client: bool = False, shared_only: bool = False
) -> None:
    """Drop a server's sign-ins (and, with `client`, the client registered for
    it): what they were issued for has changed. The caller commits."""
    rows = delete(McpSignin).where(McpSignin.server_id == server.id)
    if shared_only:
        rows = rows.where(McpSignin.user_id.is_(None))
    db.execute(rows)
    db.execute(delete(McpOauthState).where(McpOauthState.server_id == server.id))
    if client:
        server.oauth_client_encrypted = ""


# --- Addresses --------------------------------------------------------------------
def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    port = parts.port or (443 if scheme == "https" else 80)
    return scheme, (parts.hostname or "").lower(), port


def same_origin(first: str, second: str) -> bool:
    return _origin(first) == _origin(second)


def canonical(url: str) -> str:
    """A server's resource identifier (RFC 8707, as the MCP specification
    forms it): lower-case scheme and host, no fragment, no query, and no
    trailing slash."""
    parts = urlsplit(url.strip())
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


def covers(resource: str, url: str) -> bool:
    """Whether a metadata document's `resource` is the server at `url`: the
    same origin, and the server's path at or under the resource's. A document
    naming another origin describes another server, and a token asked for
    with it would be one for that server."""
    if not same_origin(resource, url):
        return False
    base = urlsplit(resource).path.rstrip("/")
    path = urlsplit(url).path.rstrip("/")
    return path == base or path.startswith(base + "/")


def _well_known(base: str, name: str, *, after: bool = False) -> str:
    parts = urlsplit(base)
    path = parts.path.rstrip("/")
    where = f"{path}/.well-known/{name}" if after else f"/.well-known/{name}{path}"
    return urlunsplit((parts.scheme, parts.netloc, where, "", ""))


def resource_metadata_urls(server_url: str) -> list[str]:
    """RFC 9728's well-known addresses for a server, the one for its path first."""
    urls = [_well_known(server_url, "oauth-protected-resource")]
    root = _well_known(urlunsplit((*urlsplit(server_url)[:2], "", "", "")),
                       "oauth-protected-resource")
    if root not in urls:
        urls.append(root)
    return urls


def issuer_metadata_urls(issuer: str) -> list[str]:
    """Where an authorization server's metadata may be, in the order the MCP
    specification gives: RFC 8414, then OpenID Connect discovery."""
    urls = [
        _well_known(issuer, "oauth-authorization-server"),
        _well_known(issuer, "openid-configuration"),
    ]
    if urlsplit(issuer).path.rstrip("/"):
        urls.append(_well_known(issuer, "openid-configuration", after=True))
    return urls


_CHALLENGE = re.compile(r'([a-zA-Z_]+)\s*=\s*(?:"([^"]*)"|([^\s,]+))')


def challenge(header: str) -> dict[str, str]:
    """The parameters of a `WWW-Authenticate: Bearer …` header."""
    if not header.strip().lower().startswith("bearer"):
        return {}
    return {
        name.lower(): quoted or bare
        for name, quoted, bare in _CHALLENGE.findall(header.strip()[len("bearer"):])
    }


# --- Requests ---------------------------------------------------------------------
@dataclass(frozen=True)
class _Reach:
    """Whose rules a request is held to: the server row's."""

    name: str
    server_url: str
    allow_private: bool

    def check(self, url: str) -> str:
        """An address this sign-in may ask: https -- or plain http only where
        the server itself is plain http -- with no fragment, and past the same
        guard as the server's own URL."""
        parts = urlsplit(url)
        if parts.scheme == "http" and urlsplit(self.server_url).scheme != "http":
            raise McpError(f"{self.name}'s sign-in names an address that is not https.")
        if parts.fragment:
            raise McpError(f"{self.name}'s sign-in names an address with a fragment.")
        return fetch_service.check_url(url, allow_private=self.allow_private)


def _json(response: httpx.Response) -> dict[str, Any] | None:
    try:
        parsed = json.loads(response.content[:MAX_DOCUMENT_BYTES])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _get(reach: _Reach, url: str) -> dict[str, Any] | None:
    """A JSON document, or None where there is none. Redirects by hand, every
    hop checked."""
    current = reach.check(url)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            for _ in range(fetch_service.MAX_REDIRECTS + 1):
                response = await client.get(
                    current,
                    headers={"Accept": "application/json",
                             "User-Agent": fetch_service.USER_AGENT},
                )
                if not response.is_redirect:
                    break
                location = response.headers.get("location", "")
                if not location:
                    return None
                current = reach.check(str(response.url.join(location)))
            else:
                return None
    except httpx.RequestError as exc:
        raise McpError(f"Could not reach {urlsplit(current).netloc}: {exc}") from exc
    if response.status_code != 200:
        return None
    return _json(response)


async def _post(
    reach: _Reach,
    url: str,
    *,
    form: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    basic: tuple[str, str] | None = None,
) -> httpx.Response:
    """One POST to an authorization server. Never redirected: a redirect
    would carry a code, a verifier or a token somewhere nobody named."""
    target = reach.check(url)
    headers = {"Accept": "application/json", "User-Agent": fetch_service.USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            response = await client.post(
                target, data=form, json=body, headers=headers, auth=basic
            )
    except httpx.RequestError as exc:
        raise McpError(f"Could not reach {urlsplit(target).netloc}: {exc}") from exc
    if response.is_redirect:
        raise McpError(f"{urlsplit(target).netloc} answered a sign-in request with a redirect.")
    return response


# --- Discovery --------------------------------------------------------------------
@dataclass(frozen=True)
class Provider:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    revocation_endpoint: str
    auth_methods: tuple[str, ...]
    resource: str
    scope: str
    iss_supported: bool


async def _probe(reach: _Reach) -> dict[str, str]:
    """Ask the server without a credential and read what its 401 says."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            response = await client.post(
                fetch_service.check_url(reach.server_url, allow_private=reach.allow_private),
                json=protocol.request(
                    "initialize",
                    {
                        "protocolVersion": protocol.PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": protocol.CLIENT_INFO,
                    },
                    request_id=1,
                ),
                headers={
                    "Accept": "application/json, text/event-stream",
                    "User-Agent": fetch_service.USER_AGENT,
                },
            )
    except httpx.RequestError as exc:
        raise McpError(f"Could not reach {reach.name}: {exc}") from exc
    if response.status_code != 401:
        return {}
    return challenge(response.headers.get("www-authenticate", ""))


def _url(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text if urlsplit(text).scheme in ("http", "https") and urlsplit(text).netloc else ""


async def discover(server: McpServer) -> Provider:
    """Everything a sign-in needs to know about a server's provider."""
    reach = _Reach(server.name or server.slug, server.url or "", bool(server.allow_private))
    said = await _probe(reach)

    # The protected resource: the address the server gave, else the well-known ones.
    named = _url(said.get("resource_metadata"))
    document = None
    for candidate in [named] if named else resource_metadata_urls(reach.server_url):
        document = await _get(reach, candidate)
        if document is not None:
            break

    resource = canonical(reach.server_url)
    issuer = ""
    offered_scopes = ""
    if document is not None:
        told = _url(document.get("resource"))
        if told:
            if not covers(told, reach.server_url):
                raise McpError(
                    f"{reach.name}'s sign-in metadata describes another server ({told})."
                )
            resource = told
        servers = document.get("authorization_servers")
        issuer = next((u for u in map(_url, servers if isinstance(servers, list) else []) if u), "")
        scopes = document.get("scopes_supported")
        if isinstance(scopes, list):
            offered_scopes = " ".join(str(s) for s in scopes if isinstance(s, str) and s)
    if not issuer:
        # A server from before protected resource metadata is its own
        # authorization server, at its origin.
        issuer = urlunsplit((*urlsplit(reach.server_url)[:2], "", "", ""))

    metadata = None
    mismatched = False
    for candidate in issuer_metadata_urls(issuer):
        found = await _get(reach, candidate)
        if found is None:
            continue
        # RFC 8414 §3.3: the issuer in the document must be the one asked for,
        # or the document is somebody else's.
        if found.get("issuer") != issuer:
            mismatched = True
            continue
        metadata = found
        break
    if metadata is None:
        if mismatched:
            raise McpError(f"{reach.name}'s sign-in provider answers as another issuer.")
        raise McpError(
            f"{reach.name} did not say how to sign in: no OAuth metadata was found for it."
        )

    authorize = _url(metadata.get("authorization_endpoint"))
    token = _url(metadata.get("token_endpoint"))
    if not authorize or not token:
        raise McpError(f"{reach.name}'s sign-in provider names no place to sign in.")
    kinds = metadata.get("response_types_supported")
    if isinstance(kinds, list) and "code" not in kinds:
        raise McpError(f"{reach.name}'s sign-in provider does not offer the code flow.")
    methods = metadata.get("code_challenge_methods_supported")
    if not isinstance(methods, list) or "S256" not in methods:
        # Absent means no PKCE, and the specification says not to go on.
        raise McpError(f"{reach.name}'s sign-in provider does not offer PKCE (S256).")

    registration = _url(metadata.get("registration_endpoint"))
    revocation = _url(metadata.get("revocation_endpoint"))
    for endpoint in (authorize, token, registration, revocation):
        if endpoint:
            reach.check(endpoint)

    auth_methods = metadata.get("token_endpoint_auth_methods_supported")
    return Provider(
        issuer=issuer,
        authorization_endpoint=authorize,
        token_endpoint=token,
        registration_endpoint=registration,
        revocation_endpoint=revocation,
        auth_methods=tuple(str(m) for m in auth_methods) if isinstance(auth_methods, list) else (),
        resource=resource,
        scope=(server.oauth_scopes or "").strip() or said.get("scope", "") or offered_scopes,
        iss_supported=bool(metadata.get("authorization_response_iss_parameter_supported")),
    )


# --- The client -------------------------------------------------------------------
def _secret_method(offered: tuple[str, ...]) -> str:
    """How a client with a secret shows it. Basic is RFC 8414's default."""
    if offered and "client_secret_basic" not in offered and "client_secret_post" in offered:
        return "client_secret_post"
    return "client_secret_basic"


def _registered(server: McpServer) -> dict[str, Any]:
    try:
        stored = json.loads(decrypt(server.oauth_client_encrypted or "") or "{}")
    except ValueError:
        return {}
    return stored if isinstance(stored, dict) else {}


async def _client(
    db: DBSession, server: McpServer, provider: Provider, redirect_uri: str
) -> dict[str, str]:
    """The client to sign in as: {client_id, method}. Its secret stays on the row."""
    typed = (server.oauth_client_id or "").strip()
    if typed:
        has_secret = bool(decrypt(server.oauth_client_secret_encrypted or ""))
        return {
            "client_id": typed,
            "method": _secret_method(provider.auth_methods) if has_secret else "none",
        }

    stored = _registered(server)
    if (
        stored.get("client_id")
        and stored.get("issuer") == provider.issuer
        and stored.get("redirect_uri") == redirect_uri
    ):
        return {
            "client_id": str(stored["client_id"]),
            "method": str(stored.get("method") or "none"),
        }

    if not provider.registration_endpoint:
        raise McpError(
            f"{server.name}'s sign-in provider does not register applications by itself. "
            "An administrator has to register this instance there and type the client ID "
            f"into the server's form; the redirect address to register is {redirect_uri}"
        )

    reach = _Reach(server.name or server.slug, server.url or "", bool(server.allow_private))
    wanted = "none"
    if provider.auth_methods and "none" not in provider.auth_methods:
        wanted = _secret_method(provider.auth_methods)
    asked: dict[str, Any] = {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": wanted,
    }
    if provider.scope:
        asked["scope"] = provider.scope
    response = await _post(reach, provider.registration_endpoint, body=asked)
    answer = _json(response) or {}
    client_id = answer.get("client_id")
    if response.status_code not in (200, 201) or not isinstance(client_id, str) or not client_id:
        raise McpError(
            f"{server.name}'s sign-in provider would not register this instance "
            f"(HTTP {response.status_code})."
        )
    secret = answer.get("client_secret") if isinstance(answer.get("client_secret"), str) else ""
    method = str(answer.get("token_endpoint_auth_method") or "")
    if secret and method not in ("client_secret_basic", "client_secret_post"):
        method = "client_secret_basic"
    if not secret:
        method = "none"
    server.oauth_client_encrypted = encrypt(
        json.dumps(
            {
                "issuer": provider.issuer,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": secret,
                "method": method,
            }
        )
    )
    db.commit()
    log.info("registered this instance as a client of mcp %s's provider", server.slug)
    return {"client_id": client_id, "method": method}


def _client_secret(server: McpServer, client_id: str) -> str:
    if (server.oauth_client_id or "").strip() == client_id:
        return decrypt(server.oauth_client_secret_encrypted or "")
    stored = _registered(server)
    if stored.get("client_id") == client_id:
        return str(stored.get("client_secret") or "")
    return ""


# --- Leaving for the provider -----------------------------------------------------
def _hash(state: str) -> str:
    return hashlib.sha256(state.encode()).hexdigest()


def challenge_of(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def start(
    db: DBSession, server: McpServer, user: User, *, base: str
) -> str:
    """Begin a sign-in. Returns the provider's page to send the browser to.

    `base` is this instance's address as the person's browser reached it; the
    provider sends them back to it.
    """
    if not is_oauth(server):
        raise McpError(f"{server.name} is not signed in to with OAuth.")
    provider = await discover(server)
    redirect_uri = base.rstrip("/") + CALLBACK_PATH
    client = await _client(db, server, provider, redirect_uri)

    now = datetime.now(UTC)
    db.execute(delete(McpOauthState).where(McpOauthState.expires_at < now))
    # One sign-in on its way per person and server: starting again drops the last.
    db.execute(
        delete(McpOauthState).where(
            McpOauthState.server_id == server.id, McpOauthState.user_id == user.id
        )
    )
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    db.add(
        McpOauthState(
            state_hash=_hash(state),
            server_id=server.id,
            user_id=user.id,
            shared=bool(server.oauth_shared),
            verifier_encrypted=encrypt(verifier),
            redirect_uri=redirect_uri,
            meta_json={
                "issuer": provider.issuer,
                "token_endpoint": provider.token_endpoint,
                "revocation_endpoint": provider.revocation_endpoint,
                "resource": provider.resource,
                "client_id": client["client_id"],
                "method": client["method"],
                "iss_supported": provider.iss_supported,
            },
            expires_at=now + STATE_LIFETIME,
        )
    )
    db.commit()

    asked = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge_of(verifier),
        "code_challenge_method": "S256",
        "resource": provider.resource,
    }
    if provider.scope:
        asked["scope"] = provider.scope
    parts = urlsplit(provider.authorization_endpoint)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in asked]
    return urlunsplit((*parts[:3], urlencode([*query, *asked.items()]), ""))


# --- Tokens -----------------------------------------------------------------------
async def _token_request(
    reach: _Reach, meta: dict[str, Any], secret: str, form: dict[str, str]
) -> dict[str, Any]:
    """Ask the token endpoint. `_Refused` when it says no; `McpError` when it
    could not be asked, which says nothing about the sign-in."""
    client_id, method = str(meta.get("client_id") or ""), str(meta.get("method") or "none")
    form = dict(form)
    basic = None
    if method == "client_secret_basic" and secret:
        basic = (client_id, secret)
    else:
        form["client_id"] = client_id
        if method == "client_secret_post" and secret:
            form["client_secret"] = secret
    response = await _post(reach, str(meta.get("token_endpoint") or ""), form=form, basic=basic)
    answer = _json(response) or {}
    if response.status_code in (400, 401, 403):
        raise _Refused(str(answer.get("error") or f"HTTP {response.status_code}")[:80])
    if response.status_code != 200:
        raise McpError(
            f"{reach.name}'s sign-in provider answered HTTP {response.status_code}."
        )
    kind = answer.get("token_type")
    if not isinstance(answer.get("access_token"), str) or not answer["access_token"]:
        raise _Refused("no access token in the answer")
    if isinstance(kind, str) and kind and kind.lower() != "bearer":
        raise _Refused("a token of a kind this cannot use")
    return answer


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _account(answer: dict[str, Any]) -> str:
    """Who the provider says signed in, from an OpenID id_token when it sent
    one. Read for display only -- its signature is not checked, and nothing
    is decided by it -- so it is a stranger's text: bounded, shown escaped."""
    token = answer.get("id_token")
    if not isinstance(token, str) or token.count(".") != 2:
        return ""
    try:
        middle = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(claims, dict):
        return ""
    for key in ("email", "preferred_username", "name", "sub"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return _CONTROL.sub("", value).strip()[:200]
    return ""


def _keep(row: McpSignin, answer: dict[str, Any], *, now: datetime) -> None:
    row.access_encrypted = encrypt(str(answer["access_token"]))
    fresh = answer.get("refresh_token")
    if isinstance(fresh, str) and fresh:
        # A provider that rotates sends a new one; one that does not, none.
        row.refresh_encrypted = encrypt(fresh)
    lasts = answer.get("expires_in")
    try:
        seconds = int(lasts) if lasts is not None else 0
    except (TypeError, ValueError):
        seconds = 0
    row.expires_at = now + timedelta(seconds=seconds) if seconds > 0 else None
    if isinstance(answer.get("scope"), str):
        row.scope = answer["scope"][:1000]
    account = _account(answer)
    if account:
        row.account = account
    row.state = SIGNIN_OK
    row.last_error = ""


async def finish(db: DBSession, user: User, params: dict[str, str]) -> McpServer:
    """The browser came back. Exchange the code and keep the sign-in."""
    state = params.get("state", "")
    row = db.scalar(select(McpOauthState).where(McpOauthState.state_hash == _hash(state)))
    if not state or row is None:
        raise McpError("This is not a sign-in this instance is waiting for. Start it again.")
    if row.user_id != user.id:
        # Somebody else's sign-in: finishing it here would put their account
        # at the provider behind this person's chats.
        raise McpError("This sign-in was started by another account. Start your own.")

    # Used once: gone before anything is asked of anybody.
    taken = db.execute(delete(McpOauthState).where(McpOauthState.id == row.id)).rowcount
    snapshot = {
        "server_id": row.server_id,
        "shared": bool(row.shared),
        "verifier": decrypt(row.verifier_encrypted),
        "redirect_uri": row.redirect_uri,
        "meta": dict(row.meta_json or {}),
        "expires_at": _aware(row.expires_at),
    }
    db.commit()
    if taken != 1:
        raise McpError("This sign-in was used already. Start it again.")

    server = db.get(McpServer, snapshot["server_id"])
    if server is None or not is_oauth(server):
        raise McpError("That server no longer exists.")
    if snapshot["expires_at"] < datetime.now(UTC):
        raise McpError("This sign-in took too long. Start it again.")
    if snapshot["shared"] != bool(server.oauth_shared) or not may_sign_in(server, user):
        raise McpError("Whose sign-in this server uses changed meanwhile. Start it again.")

    meta = snapshot["meta"]
    refused = params.get("error", "")
    if refused:
        detail = _CONTROL.sub("", params.get("error_description", ""))[:200]
        raise McpError(
            f"The provider did not sign you in: {_CONTROL.sub('', refused)[:80]}"
            + (f" — {detail}" if detail else "")
        )
    # RFC 9207: an answer from another issuer than the one asked is a mix-up.
    came_from = params.get("iss", "")
    if (came_from and came_from != meta.get("issuer")) or (
        meta.get("iss_supported") and not came_from
    ):
        raise McpError("The answer came from another provider than the one asked.")
    code = params.get("code", "")
    if not code:
        raise McpError("The provider sent no authorisation code.")

    reach = _Reach(server.name or server.slug, server.url or "", bool(server.allow_private))
    try:
        answer = await _token_request(
            reach,
            meta,
            _client_secret(server, str(meta.get("client_id") or "")),
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": snapshot["redirect_uri"],
                "code_verifier": snapshot["verifier"],
                "resource": str(meta.get("resource") or ""),
            },
        )
    except _Refused as exc:
        raise McpError(f"The provider refused the sign-in: {exc.error}") from exc

    owner = None if snapshot["shared"] else user.id
    signin = db.scalar(
        select(McpSignin).where(
            McpSignin.server_id == server.id,
            McpSignin.user_id.is_(None) if owner is None else McpSignin.user_id == owner,
        )
    )
    if signin is None:
        signin = McpSignin(server_id=server.id, user_id=owner)
        db.add(signin)
    signin.signed_in_by = user.id
    signin.refresh_encrypted = ""
    signin.account = ""
    signin.scope = ""
    signin.meta_json = {k: v for k, v in meta.items() if k != "iss_supported"}
    _keep(signin, answer, now=datetime.now(UTC))
    db.commit()
    log.info("mcp %s: signed in (%s)", server.slug, "shared" if owner is None else "personal")
    return server


# One renewal at a time per sign-in: two calls in one round would otherwise
# both spend the same refresh token, and a provider that rotates them takes
# the second for a stolen one and ends the sign-in.
_locks: dict[str, asyncio.Lock] = {}


def _mark_needed(signin_id: str, why: str) -> None:
    with session_scope() as db:
        row = db.get(McpSignin, signin_id)
        if row is None:
            return
        row.state = SIGNIN_NEEDS
        row.access_encrypted = ""
        row.refresh_encrypted = ""
        row.expires_at = None
        row.last_error = why[:300]
    log.info("mcp sign-in %s needs signing in again: %s", signin_id, why)


def _needed(name: str) -> SigninNeeded:
    return SigninNeeded(
        f"Nobody is signed in to {name} at the moment. It is signed in to under "
        "Settings › Security, and its tools come back once it is."
    )


async def token_for(signin_id: str, name: str = "", *, rejected: str = "") -> str:
    """The access token to send, renewed first when it is about to lapse or
    when `rejected` -- the one the server just refused -- is still the stored
    one. Raises `SigninNeeded` when there is none to be had."""
    if not signin_id:
        raise _needed(name or "this server")
    lock = _locks.setdefault(signin_id, asyncio.Lock())
    async with lock:
        with session_scope() as db:
            row = db.get(McpSignin, signin_id)
            server = db.get(McpServer, row.server_id) if row is not None else None
            if row is None or server is None or not usable(row):
                raise _needed(name or (server.name if server else "this server"))
            access = decrypt(row.access_encrypted)
            ends = _aware(row.expires_at)
            due = ends is not None and ends - datetime.now(UTC) <= REFRESH_MARGIN
            if access and not due and access != rejected:
                return access
            refresh = decrypt(row.refresh_encrypted)
            meta = dict(row.meta_json or {})
            reach = _Reach(server.name or server.slug, server.url or "",
                           bool(server.allow_private))
            secret = _client_secret(server, str(meta.get("client_id") or ""))

        if not refresh:
            _mark_needed(signin_id, "the sign-in ended and the provider gave no way to renew it")
            raise _needed(reach.name)
        try:
            answer = await _token_request(
                reach,
                meta,
                secret,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "resource": str(meta.get("resource") or ""),
                },
            )
        except _Refused as exc:
            _mark_needed(signin_id, f"the provider would not renew it ({exc.error})")
            raise _needed(reach.name) from exc

        with session_scope() as db:
            row = db.get(McpSignin, signin_id)
            if row is None:
                raise _needed(reach.name)
            _keep(row, answer, now=datetime.now(UTC))
        return str(answer["access_token"])


async def reject(signin_id: str, name: str, token: str) -> SigninNeeded:
    """The server refused a token that had just been renewed: the sign-in is
    not one it honours. Returns what to raise."""
    lock = _locks.setdefault(signin_id, asyncio.Lock())
    async with lock:
        with session_scope() as db:
            row = db.get(McpSignin, signin_id)
            current = decrypt(row.access_encrypted) if row is not None else ""
        if current == token:
            _mark_needed(signin_id, "the server refused the sign-in")
    return _needed(name)


async def sign_out(db: DBSession, signin: McpSignin) -> bool:
    """Forget a sign-in, asking the provider to revoke it first where it has
    a place for that (RFC 7009). Returns whether it was asked and agreed."""
    server = db.get(McpServer, signin.server_id)
    meta = dict(signin.meta_json or {})
    tokens = [
        (decrypt(signin.refresh_encrypted), "refresh_token"),
        (decrypt(signin.access_encrypted), "access_token"),
    ]
    signin_id = signin.id
    db.delete(signin)
    db.commit()
    _locks.pop(signin_id, None)

    endpoint = str(meta.get("revocation_endpoint") or "")
    if server is None or not endpoint:
        return False
    reach = _Reach(server.name or server.slug, server.url or "", bool(server.allow_private))
    client_id, method = str(meta.get("client_id") or ""), str(meta.get("method") or "none")
    secret = _client_secret(server, client_id)
    revoked = False
    for token, hint in tokens:
        if not token:
            continue
        form = {"token": token, "token_type_hint": hint}
        basic = None
        if method == "client_secret_basic" and secret:
            basic = (client_id, secret)
        else:
            form["client_id"] = client_id
            if method == "client_secret_post" and secret:
                form["client_secret"] = secret
        try:
            response = await _post(reach, endpoint, form=form, basic=basic)
        except (McpError, FetchError) as exc:
            log.info("mcp %s: could not revoke a sign-in: %s", server.slug, exc.message)
            return False
        revoked = revoked or response.status_code == 200
    return revoked


__all__ = [
    "CALLBACK_PATH",
    "Provider",
    "SigninNeeded",
    "canonical",
    "challenge",
    "challenge_of",
    "covers",
    "discover",
    "finish",
    "forget",
    "is_oauth",
    "issuer_metadata_urls",
    "kind_of",
    "may_sign_in",
    "reject",
    "resource_metadata_urls",
    "same_origin",
    "sign_out",
    "signin_for",
    "start",
    "token_for",
    "usable",
    "view",
]
