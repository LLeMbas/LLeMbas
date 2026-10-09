"""Custom HTTP tools: filling the template, and refusing to be pointed elsewhere.

Most of this file is about the second. The arguments come from a model, which
can be talked into things by whatever it just read, so an argument filling a
hole in a URL is the same kind of input as a URL typed by a stranger.
"""

from __future__ import annotations

import json

import httpx
import pytest

from lembas.db.models import (
    RESPONSE_JSON,
    RESPONSE_RAW,
    RESPONSE_TEXT,
    SECRET_BEARER,
    SECRET_HEADER,
    SECRET_NONE,
    CustomTool,
)
from lembas.services import custom_tools
from lembas.services.crypto import encrypt
from lembas.services.fetch import FetchError


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    """Resolve invented hostnames to a public address.

    `api.test` does not exist and `check_url` resolves for real, which is the
    whole point of it. A literal IP is handed back as itself, so the tests that
    check a private address are still checking one.
    """
    import ipaddress
    import socket

    def resolve(host, *_args, **_kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return [(2, 1, 6, "", ("93.184.216.34", 80))]
        return [(2, 1, 6, "", (host, 80))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def _spec(**overrides) -> custom_tools.HttpSpec:
    base = {
        "slug": "weather",
        "label": "Weather",
        "method": "GET",
        "url_template": "https://api.test/v1/city/{{city}}",
        "secret_placement": SECRET_NONE,
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}, "days": {"type": "integer"}},
            "required": ["city"],
        },
    }
    return custom_tools.HttpSpec(**{**base, **overrides})


def _ok(body: str = "sunny", *, status: int = 200, content_type: str = "text/plain"):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        seen.setdefault("urls", []).append(str(request.url))
        return httpx.Response(status, text=body, headers={"content-type": content_type})

    return handler, seen


# --- Filling the URL ---------------------------------------------------------
def test_an_argument_is_percent_encoded_into_the_url():
    url = custom_tools.fill_url(_spec(), {"city": "Minas Tirith"})
    assert url == "https://api.test/v1/city/Minas%20Tirith"


def test_an_argument_cannot_add_a_path_segment_or_a_query():
    """safe="" is the whole point. Everything structural encodes."""
    url = custom_tools.fill_url(_spec(), {"city": "../../admin?token=x#y"})
    assert "/admin" not in url
    assert "?" not in url and "#" not in url
    assert url.startswith("https://api.test/v1/city/")


def test_an_argument_cannot_reach_another_host():
    url = custom_tools.fill_url(_spec(), {"city": "evil.test/steal"})
    assert url.startswith("https://api.test/")


def test_an_undeclared_name_is_removed_rather_than_passed_through():
    """Unlike the prompt fragments, where an unrecognised {{x}} is left alone.
    A literal {{x}} in a URL is not a feature."""
    spec = _spec(url_template="https://api.test/{{city}}/{{unknown}}")
    assert custom_tools.fill_url(spec, {"city": "a", "unknown": "b"}) == "https://api.test/a/"


def test_an_argument_the_model_did_not_send_becomes_nothing():
    assert custom_tools.fill_url(_spec(), {}) == "https://api.test/v1/city/"


def test_a_template_whose_host_is_a_variable_is_refused():
    with pytest.raises(FetchError, match="literal"):
        custom_tools.fill_url(_spec(url_template="https://{{city}}.test/x"), {"city": "a"})


def test_a_template_that_is_not_http_is_refused():
    with pytest.raises(FetchError):
        custom_tools.fill_url(_spec(url_template="file:///etc/passwd"), {})


# --- Reaching the network ----------------------------------------------------
async def test_a_private_address_is_refused_unless_the_row_allows_it(mock_http):
    handler, _seen = _ok()
    mock_http(handler)

    spec = _spec(url_template="http://127.0.0.1:11434/api/tags", parameters={})
    refused = await custom_tools.call(spec, {})
    assert refused.event["status"] == "error"
    assert "private or local" in refused.event["error"]

    allowed = await custom_tools.call(
        _spec(
            url_template="http://127.0.0.1:11434/api/tags", parameters={}, allow_private=True
        ),
        {},
    )
    assert allowed.event["status"] == "ok"


async def test_a_hostname_resolving_to_loopback_is_refused(mock_http, monkeypatch):
    """A name pointing at 127.0.0.1 walks past any check that only reads the
    text of the URL."""
    handler, _seen = _ok()
    mock_http(handler)
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 80))],
    )

    outcome = await custom_tools.call(_spec(parameters={}), {})
    assert outcome.event["status"] == "error"
    assert "private or local" in outcome.event["error"]


async def test_every_redirect_hop_is_checked(mock_http, monkeypatch):
    """httpx's own following would validate the first address and then happily
    land on localhost."""
    hops = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        if request.url.host == "api.test":
            return httpx.Response(302, headers={"location": "http://inside.test/secrets"})
        return httpx.Response(200, text="secrets")

    mock_http(handler)

    def resolve(host, *_args, **_kwargs):
        if host == "inside.test":
            return [(2, 1, 6, "", ("192.168.1.5", 80))]
        return [(2, 1, 6, "", ("93.184.216.34", 80))]

    monkeypatch.setattr("socket.getaddrinfo", resolve)

    outcome = await custom_tools.call(_spec(parameters={}), {})
    assert outcome.event["status"] == "error"
    assert len(hops) == 1, "the second hop must never be requested"


async def test_the_secret_is_dropped_on_a_cross_host_redirect(mock_http):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "api.test":
            return httpx.Response(302, headers={"location": "https://elsewhere.test/x"})
        return httpx.Response(200, text="ok")

    mock_http(handler)

    outcome = await custom_tools.call(
        _spec(parameters={}, secret="s3cret", secret_placement=SECRET_BEARER), {}
    )
    assert outcome.event["status"] == "ok"
    assert seen[0] == ("api.test", "Bearer s3cret")
    assert seen[1] == ("elsewhere.test", None)


async def test_a_bearer_secret_reaches_the_request_and_never_the_event(mock_http):
    handler, seen = _ok()
    mock_http(handler)

    outcome = await custom_tools.call(
        _spec(parameters={}, secret="s3cret", secret_placement=SECRET_BEARER), {}
    )
    assert seen["request"].headers["authorization"] == "Bearer s3cret"
    assert "s3cret" not in json.dumps(outcome.event)


async def test_a_header_secret_uses_the_name_it_was_given(mock_http):
    handler, seen = _ok()
    mock_http(handler)

    await custom_tools.call(
        _spec(
            parameters={},
            secret="k",
            secret_placement=SECRET_HEADER,
            secret_name="X-Api-Key",
        ),
        {},
    )
    assert seen["request"].headers["x-api-key"] == "k"


async def test_a_header_value_cannot_carry_a_newline(mock_http):
    handler, seen = _ok()
    mock_http(handler)

    await custom_tools.call(
        _spec(headers={"X-Trace": "{{city}}"}), {"city": "a\r\nX-Admin: yes"}
    )
    assert "\n" not in seen["request"].headers["x-trace"]


async def test_a_body_argument_cannot_end_the_json_string(mock_http):
    handler, seen = _ok()
    mock_http(handler)

    await custom_tools.call(
        _spec(
            method="POST",
            url_template="https://api.test/v1/ask",
            body_template='{"city": "{{city}}"}',
        ),
        {"city": 'x", "admin": "yes'},
    )
    body = json.loads(seen["request"].content)
    assert set(body) == {"city"}


# --- Reading the response ----------------------------------------------------
async def test_a_json_response_is_narrowed_by_the_path(mock_http):
    def handler(_request):
        return httpx.Response(
            200,
            json={"data": {"items": [{"title": "Mallorn"}, {"title": "Elanor"}]}},
        )

    mock_http(handler)
    outcome = await custom_tools.call(
        _spec(parameters={}, response_mode=RESPONSE_JSON, response_path="data.items.0.title"), {}
    )
    assert outcome.content == "Mallorn"


async def test_a_path_that_leads_nowhere_yields_the_whole_document(mock_http):
    mock_http(lambda _r: httpx.Response(200, json={"a": 1}))
    outcome = await custom_tools.call(
        _spec(parameters={}, response_mode=RESPONSE_JSON, response_path="nope.nope"), {}
    )
    assert json.loads(outcome.content) == {"a": 1}


async def test_an_html_response_becomes_text(mock_http):
    handler, _seen = _ok(
        "<html><head><title>T</title></head><body><p>Hello</p></body></html>",
        content_type="text/html",
    )
    mock_http(handler)
    outcome = await custom_tools.call(_spec(parameters={}, response_mode=RESPONSE_TEXT), {})
    assert outcome.content == "Hello"


async def test_a_raw_response_is_left_alone(mock_http):
    handler, _seen = _ok("<p>kept</p>", content_type="text/html")
    mock_http(handler)
    outcome = await custom_tools.call(_spec(parameters={}, response_mode=RESPONSE_RAW), {})
    assert outcome.content == "<p>kept</p>"


async def test_the_response_is_capped_and_says_so(mock_http):
    handler, _seen = _ok("x" * 5000)
    mock_http(handler)
    outcome = await custom_tools.call(_spec(parameters={}, max_chars=500), {})
    assert len(outcome.content) < 600
    assert outcome.content.endswith("(truncated)")


async def test_the_event_preview_is_capped_independently(mock_http):
    """`max_chars` is spent once; the event is stored on the message row."""
    handler, _seen = _ok("x" * 30_000)
    mock_http(handler)
    outcome = await custom_tools.call(_spec(parameters={}, max_chars=20_000), {})
    assert len(outcome.event["text"]) <= custom_tools.MAX_EVENT_CHARS
    assert len(outcome.content) > custom_tools.MAX_EVENT_CHARS


async def test_an_http_error_becomes_an_explanation_not_an_exception(mock_http):
    handler, _seen = _ok("no such city", status=404)
    mock_http(handler)
    outcome = await custom_tools.call(_spec(parameters={}), {})
    assert outcome.event["status"] == "error"
    assert "404" in outcome.content
    assert "no such city" in outcome.content


async def test_a_transport_failure_is_reported_to_the_model(mock_http):
    def handler(request):
        raise httpx.ConnectTimeout("too slow", request=request)

    mock_http(handler)
    outcome = await custom_tools.call(_spec(parameters={}), {})
    assert outcome.event["status"] == "error"
    assert "Could not reach" in outcome.content


async def test_the_event_names_the_host_and_not_the_filled_url(mock_http):
    """A path segment carries an argument, and the event is rendered and kept."""
    handler, _seen = _ok()
    mock_http(handler)
    outcome = await custom_tools.call(_spec(), {"city": "Minas Tirith"})
    assert outcome.event["detail"] == "GET api.test"
    assert "Minas" not in outcome.event["detail"]
    assert "Minas Tirith" in outcome.event["query"]


# --- Turning rows into tools -------------------------------------------------
def _row(db, **overrides) -> CustomTool:
    row = CustomTool(
        **{
            "slug": "weather",
            "name": "Weather",
            "description": "Look up the weather.",
            "url_template": "https://api.test/{{city}}",
            "parameters_json": {"type": "object", "properties": {"city": {"type": "string"}}},
            **overrides,
        }
    )
    db.add(row)
    db.commit()
    return row


def test_a_row_becomes_a_tool_definition(db, user_id):
    from lembas.db.models import User

    _row(db)
    defs = custom_tools.tool_defs(db, db.get(User, user_id))
    assert [tool.name for tool in defs] == ["weather"]
    assert defs[0].family == "custom:weather"
    assert defs[0].schema["function"]["description"] == "Look up the weather."


def test_a_disabled_row_is_not_offered(db, user_id):
    from lembas.db.models import User

    _row(db, enabled=False)
    assert custom_tools.tool_defs(db, db.get(User, user_id)) == []


def test_a_schema_that_is_not_an_object_is_replaced(db, user_id):
    """An endpoint rejects the whole request over a malformed tools array, so
    one bad row must not take the reply with it."""
    from lembas.db.models import User

    _row(db, parameters_json={"type": "string"})
    defs = custom_tools.tool_defs(db, db.get(User, user_id))
    assert defs[0].parameters == {"type": "object", "properties": {}}


def test_the_secret_is_decrypted_into_the_snapshot_and_nowhere_else(db):
    row = _row(db, secret_encrypted=encrypt("s3cret"))
    spec = custom_tools.spec_from(row)
    assert spec.secret == "s3cret"
    assert "s3cret" not in row.secret_encrypted
