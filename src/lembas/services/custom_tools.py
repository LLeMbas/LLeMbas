"""Running the HTTP tools an administrator defined.

A row in `custom_tools` becomes a `ToolDef` like any built-in: same schema in
the same array, same `ToolOutcome` back. What is different is that the arguments
come from a model and the destination comes from a template, so two things have
to hold.

**An argument may fill a hole; it may not move the target.** The scheme and host
of the template are literal, checked when the row is saved and again here in
case a row predates the check, and every value is escaped for the position it
lands in -- percent-encoded with nothing safe in a URL, JSON-escaped in a body,
stripped of line breaks in a header. `quote(value, safe="")` is what stops a
value adding a path segment, a query parameter or a fragment; pinning the origin
afterwards is what catches anything that got past it.

**Every hop is checked.** This is the same request-forgery problem
`services/fetch.py` exists to solve, and the same answer: resolve and check the
address, follow redirects by hand, refuse private ranges unless this particular
row was allowed them. `fetch.fetch` itself cannot be reused -- it is GET-only,
has no body, and refuses any content type that is not HTML or text, which is
every JSON API there is -- so its redirect loop is deliberately copied rather
than the module bent into a general HTTP client.

The secret is decrypted into the snapshot and goes nowhere else: not into the
event, not into a log line, and not across a redirect that leaves the origin it
was issued for.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    RESPONSE_JSON,
    RESPONSE_RAW,
    RESPONSE_TEXT,
    SECRET_BEARER,
    SECRET_HEADER,
    SECRET_QUERY,
    CustomTool,
    User,
)
from lembas.services import fetch as fetch_service
from lembas.services import tool_access
from lembas.services.crypto import decrypt
from lembas.services.prompts import VARIABLE_PATTERN
from lembas.services.tools import RISK_READ, RISK_WRITE, ToolContext, ToolDef, ToolOutcome

log = logging.getLogger(__name__)

# What a response may weigh before it is cut off. Well below the page fetcher's
# ceiling, because this is text that will be sent back to a model rather than
# stored for a person to read.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

# How much of the response is kept on the message row for the transcript. Capped
# separately from `max_chars`: what the model reads is spent once, what the event
# holds is stored on every message forever.
MAX_EVENT_CHARS = 2000

# How much of the arguments the transcript summarises.
MAX_SUMMARY_CHARS = 200

ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# Bounds an administrator's number is clamped into. A tool that may return
# 400 000 characters is a tool that can fill the context window in one call.
MIN_CHARS, MAX_CHARS = 200, 40_000
MIN_TIMEOUT, MAX_TIMEOUT = 1, 120


@dataclass(frozen=True)
class HttpSpec:
    """Everything one custom tool needs, read while the session was open.

    A frozen snapshot rather than the row, for the reason `Endpoint` is one: a
    generation outlives the request that started it, and a detached SQLAlchemy
    instance is a trap. The decrypted secret lives here and nowhere else.
    """

    slug: str
    label: str
    method: str
    url_template: str
    body_template: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    secret: str = ""
    secret_placement: str = SECRET_BEARER
    secret_name: str = "Authorization"
    response_mode: str = RESPONSE_TEXT
    response_path: str = ""
    max_chars: int = 8000
    timeout: int = 20
    allow_private: bool = False
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def secret_header(self) -> str:
        """The header the secret rides in, if it rides in one."""
        if not self.secret or self.secret_placement not in (SECRET_BEARER, SECRET_HEADER):
            return ""
        return self.secret_name or "Authorization"


def spec_from(row: CustomTool) -> HttpSpec:
    """Snapshot a row, decrypting its secret. Call this with a session open."""
    return HttpSpec(
        slug=row.slug,
        label=row.name or row.slug,
        method=(row.method or "GET").upper(),
        url_template=row.url_template or "",
        body_template=row.body_template or "",
        headers=dict(row.headers_json or {}),
        secret=decrypt(row.secret_encrypted),
        secret_placement=row.secret_placement,
        secret_name=row.secret_name or "Authorization",
        response_mode=row.response_mode,
        response_path=row.response_path or "",
        max_chars=min(max(int(row.max_chars or 0), MIN_CHARS), MAX_CHARS),
        timeout=min(max(int(row.timeout or 0), MIN_TIMEOUT), MAX_TIMEOUT),
        allow_private=bool(row.allow_private),
        parameters=dict(row.parameters_json or {}),
    )


def tool_defs(
    db: DBSession, user: User | None, *, everything: bool = False
) -> list[ToolDef]:
    """One `ToolDef` per custom tool this user may be offered."""
    return [
        ToolDef(
            name=row.slug,
            family=f"custom:{row.slug}",
            description=row.description or f"Call the {row.name} tool.",
            parameters=_schema_of(row),
            run=_runner(spec_from(row)),
            risk=_risk_of(row),
        )
        for row in tool_access.visible_custom_tools(db, user, everything=everything)
    ]


def _risk_of(row: CustomTool) -> str:
    """What calling this tool does to the world, as far as the method says.

    The method is all there is to go on, and it is a reasonable proxy: GET and
    HEAD are defined to be safe, and everything else is a request to change
    something. Guessing wrong in the cautious direction only means an agent
    chat asks about a call it need not have.
    """
    return RISK_READ if (row.method or "GET").upper() in ("GET", "HEAD") else RISK_WRITE


def _schema_of(row: CustomTool) -> dict[str, Any]:
    schema = dict(row.parameters_json or {})
    if schema.get("type") != "object":
        # An endpoint expects an object here; anything else it will reject
        # outright, which fails the whole request rather than the one tool.
        return {"type": "object", "properties": {}}
    return schema


def _runner(spec: HttpSpec):
    async def run(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return await call(spec, args)

    return run


# --- Filling the template ----------------------------------------------------
def _scalar(value: Any) -> str:
    """One argument as text, before it is escaped for wherever it is going."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _for_url(value: str) -> str:
    # safe="" is the whole point: an argument must not be able to introduce a
    # path segment, a query separator or a fragment.
    return quote(value, safe="")


def _for_body(value: str) -> str:
    # The inside of a JSON string, so a quote or a backslash in an argument
    # cannot end it early and add a field of its own.
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _for_header(value: str) -> str:
    # A newline in a header value is header injection. Other control characters
    # go with it; none of them mean anything in a header.
    return "".join(character for character in value if character.isprintable())


def _substitute(template: str, spec: HttpSpec, args: dict[str, Any], escape) -> str:
    """Fill `{{name}}` from the call's arguments.

    Not `prompts.substitute`, though the grammar is shared. The rules differ,
    and the differences are the point: a name the tool does not declare never
    substitutes, an unrecognised one becomes nothing rather than passing through
    verbatim -- a literal `{{x}}` in a URL is not a feature -- and every value
    is escaped for where it lands.
    """
    declared = set(spec.parameters.get("properties") or {})

    def swap(match) -> str:
        name = match.group(1)
        if name not in declared:
            return ""
        return escape(_scalar(args.get(name)))

    return VARIABLE_PATTERN.sub(swap, template)


def _origin(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise fetch_service.FetchError("A tool's URL must start with http:// or https://")
    if not parsed.netloc:
        raise fetch_service.FetchError("A tool's URL has no host.")
    return parsed.scheme, parsed.netloc


def fill_url(spec: HttpSpec, args: dict[str, Any]) -> str:
    """Fill the URL template, refusing anything that moved the host.

    Checked twice over: the template's own scheme and authority must be literal,
    and the filled URL must still point at them. The first check is what stops
    `https://{{host}}/x` from ever being saved; the second is what catches a row
    that predates it, or an escaping mistake.
    """
    template = spec.url_template.strip()
    scheme, netloc = _origin(template)
    if VARIABLE_PATTERN.search(f"{scheme}://{netloc}"):
        raise fetch_service.FetchError(
            "A tool's scheme and host must be literal, not filled from an argument."
        )

    filled = _substitute(template, spec, args, _for_url)
    if _origin(filled) != (scheme, netloc):
        raise fetch_service.FetchError("That call would have pointed somewhere else.")
    return filled


def _prepare(spec: HttpSpec, args: dict[str, Any]) -> tuple[str, dict[str, str], bytes | None]:
    """The URL, headers and body for one call, secret included."""
    url = fill_url(spec, args)
    headers = {
        "User-Agent": fetch_service.USER_AGENT,
        "Accept": "application/json, text/*;q=0.9, */*;q=0.5",
    }
    for name, value in spec.headers.items():
        clean = _for_header(str(name)).strip()
        if clean:
            headers[clean] = _substitute(str(value), spec, args, _for_header)

    body: bytes | None = None
    if spec.body_template.strip() and spec.method != "GET":
        body = _substitute(spec.body_template, spec, args, _for_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    if spec.secret:
        if spec.secret_placement == SECRET_BEARER:
            headers[spec.secret_name or "Authorization"] = f"Bearer {spec.secret}"
        elif spec.secret_placement == SECRET_HEADER:
            headers[spec.secret_name or "Authorization"] = spec.secret
        elif spec.secret_placement == SECRET_QUERY:
            # Only on the URL this call starts at. A redirect's Location
            # replaces the query, so the credential does not travel on by
            # itself -- which is the behaviour wanted anyway.
            joiner = "&" if urlparse(url).query else "?"
            url = f"{url}{joiner}{quote(spec.secret_name)}={quote(spec.secret, safe='')}"

    return url, headers, body


# --- Reading the response ----------------------------------------------------
def _narrow(payload: Any, path: str) -> Any:
    """Walk a dotted path into a decoded JSON document.

    Integer segments index a list, so "data.0.title" works. A path that does not
    lead anywhere yields the whole document rather than nothing: an unhelpful
    answer beats a silent empty one when the model has to explain itself.
    """
    current = payload
    for segment in [part for part in path.split(".") if part]:
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.lstrip("-").isdigit():
            try:
                current = current[int(segment)]
            except IndexError:
                return payload
        else:
            return payload
    return current


def _decode(payload: bytes, response: httpx.Response) -> str:
    return payload.decode(response.encoding or "utf-8", "replace")


def _as_text(spec: HttpSpec, payload: bytes, response: httpx.Response) -> str:
    content_type = response.headers.get("content-type", "")

    if spec.response_mode == RESPONSE_JSON:
        try:
            document = json.loads(_decode(payload, response))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Falling back rather than failing: a JSON API answering with an
            # HTML error page is a thing the model can report usefully.
            return _decode(payload, response)
        value = _narrow(document, spec.response_path)
        if isinstance(value, str):
            return value
        return json.dumps(value, indent=2, ensure_ascii=False)

    if spec.response_mode == RESPONSE_RAW:
        return _decode(payload, response)

    text = _decode(payload, response)
    if "html" in content_type or text.lstrip()[:1] == "<":
        _, text = fetch_service.html_to_text(text)
    return text


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… (truncated)"


def _summary(args: dict[str, Any]) -> str:
    """What the transcript shows the tool was asked for."""
    parts = [f"{name}={_scalar(value)!r}" for name, value in args.items()]
    return _clip(", ".join(parts), MAX_SUMMARY_CHARS)


def _event(spec: HttpSpec, args: dict[str, Any], *, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": spec.slug,
        "kind": "custom",
        "label": spec.label,
        "query": _summary(args),
        # The host, never the filled URL: a path or query segment can carry an
        # argument, and the event is rendered and stored.
        "detail": f"{spec.method} {urlparse(spec.url_template).netloc}",
        "status": status,
        "results": [],
        **extra,
    }


# --- Making the call ---------------------------------------------------------
async def call(spec: HttpSpec, args: dict[str, Any]) -> ToolOutcome:
    """Run one custom tool. Reports its own failures rather than raising."""
    try:
        url, headers, body = _prepare(spec, args)
        current = fetch_service.check_url(url, allow_private=spec.allow_private)
        origin = _origin(current)
        response = await _send(spec, current, headers, body, origin)
    except fetch_service.FetchError as exc:
        return ToolOutcome(
            f"The {spec.label} tool could not be called: {exc.message}",
            _event(spec, args, status="error", error=exc.message),
        )
    except httpx.RequestError as exc:
        message = f"Could not reach the {spec.label} tool: {exc}"
        return ToolOutcome(message, _event(spec, args, status="error", error=str(exc)[:200]))

    payload = response.content[:MAX_RESPONSE_BYTES]
    text = _clip(_as_text(spec, payload, response).strip(), spec.max_chars)

    if response.status_code >= 400:
        note = f"{spec.label} returned HTTP {response.status_code}."
        return ToolOutcome(
            f"{note}\n\n{text}" if text else note,
            _event(
                spec,
                args,
                status="error",
                error=f"HTTP {response.status_code}",
                text=text[:MAX_EVENT_CHARS],
            ),
        )

    if not text:
        return ToolOutcome(
            f"{spec.label} returned nothing.",
            _event(spec, args, status="ok", text=""),
        )

    return ToolOutcome(text, _event(spec, args, status="ok", text=text[:MAX_EVENT_CHARS]))


async def _send(
    spec: HttpSpec,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    origin: tuple[str, str],
) -> httpx.Response:
    """Send the request, following redirects by hand so each hop is checked."""
    current = url
    async with httpx.AsyncClient(timeout=spec.timeout, follow_redirects=False) as client:
        for _ in range(fetch_service.MAX_REDIRECTS + 1):
            response = await client.request(
                spec.method, current, headers=headers, content=body
            )
            if not response.is_redirect:
                return response

            location = response.headers.get("location", "")
            if not location:
                raise fetch_service.FetchError("That tool redirected to nowhere.")
            current = fetch_service.check_url(
                str(response.url.join(location)), allow_private=spec.allow_private
            )
            if _origin(current) != origin:
                # A server that can redirect us anywhere must not be able to
                # redirect us at somebody else carrying the key.
                if spec.secret_header:
                    headers.pop(spec.secret_header, None)
                origin = _origin(current)

    raise fetch_service.FetchError("That tool redirected too many times.")


__all__ = ["HttpSpec", "call", "fill_url", "spec_from", "tool_defs"]
