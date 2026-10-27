"""The custom-tools admin screen."""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import CustomTool, Group, User
from lembas.security.passwords import hash_password
from lembas.services.crypto import UNCHANGED_SENTINEL, decrypt, encrypt
from tests.conftest import join


@pytest.fixture
def plain_user(client: TestClient, db, registered):
    """A second account, which is never an administrator."""
    client.post("/auth/logout")
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "correct horse battery"},
        follow_redirects=False,
    )
    user = db.scalar(select(User).where(User.email == "sam@shire.test"))
    user.role = "user"
    user.active = True
    db.commit()
    client.post(
        "/auth/login",
        data={"email": "sam@shire.test", "password": "correct horse battery"},
        follow_redirects=False,
    )
    return user


def _form(**overrides) -> dict:
    base = {
        "name": "Weather",
        "slug": "weather",
        "description": "Look up the weather for a city.",
        "parameters": json.dumps(
            {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
        ),
        "method": "GET",
        "url_template": "https://api.test/v1/{{city}}",
        "headers": "Accept: application/json",
        "secret_placement": "bearer",
        "secret_name": "Authorization",
        "response_mode": "json",
        "max_chars": "8000",
        "timeout": "20",
        "position": "0",
        "enabled": "true",
        "public": "true",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def _create(client: TestClient, **overrides):
    return client.post("/admin/tools", data=_form(**overrides), follow_redirects=False)


# --- Guards ------------------------------------------------------------------
def test_the_pages_are_refused_to_a_plain_user(client: TestClient, plain_user):
    assert client.get("/admin/tools").status_code == 403
    assert client.get("/admin/tools/new").status_code == 403
    assert client.post("/admin/tools", data=_form()).status_code == 403


def test_a_tool_that_does_not_exist_is_a_404(client: TestClient, registered):
    assert client.get("/admin/tools/nope/edit").status_code == 404


def test_new_is_not_parsed_as_a_tool_id(client: TestClient, registered):
    """FastAPI matches in registration order; /admin/models has been bitten by
    exactly this."""
    response = client.get("/admin/tools/new")
    assert response.status_code == 200
    assert "New tool" in response.text


# --- Creating and editing ----------------------------------------------------
def test_creating_a_tool_then_editing_it(client: TestClient, db, registered):
    _create(client, secret="s3cret")

    tool = db.scalar(select(CustomTool))
    assert tool.slug == "weather"
    assert tool.headers_json == {"Accept": "application/json"}
    assert tool.parameters_json["properties"]["city"] == {"type": "string"}
    assert decrypt(tool.secret_encrypted) == "s3cret"
    assert tool.enabled is True

    client.post(
        f"/admin/tools/{tool.id}",
        data=_form(name="Forecast", enabled=None, secret=UNCHANGED_SENTINEL),
        follow_redirects=False,
    )
    db.refresh(tool)
    assert tool.name == "Forecast"
    # An unticked checkbox is simply absent from the post, which is the signal.
    assert tool.enabled is False
    assert decrypt(tool.secret_encrypted) == "s3cret", "the dots must keep the secret"


def test_clearing_the_field_removes_the_secret(client: TestClient, db, registered):
    _create(client, secret="s3cret")
    tool = db.scalar(select(CustomTool))

    client.post(f"/admin/tools/{tool.id}", data=_form(secret=""), follow_redirects=False)
    db.refresh(tool)
    assert tool.secret_encrypted == ""


def test_a_secret_is_never_rendered_in_full(client: TestClient, db, registered):
    _create(client, secret="super-secret-value")
    tool = db.scalar(select(CustomTool))

    page = client.get(f"/admin/tools/{tool.id}/edit").text
    assert "super-secret-value" not in page
    assert UNCHANGED_SENTINEL in page


# --- Validation reports back into the form -----------------------------------
def test_bad_parameter_json_is_reported_not_a_422(client: TestClient, db, registered):
    response = _create(client, parameters="{not json")
    assert response.status_code == 200
    assert "not valid JSON" in response.text
    assert db.scalar(select(CustomTool)) is None


def test_parameters_that_are_not_an_object_are_refused(client: TestClient, db, registered):
    response = _create(client, parameters='{"type": "string"}')
    assert "must be a JSON object" in response.text
    assert db.scalar(select(CustomTool)) is None


def test_a_slug_colliding_with_a_builtin_is_refused(client: TestClient, db, registered):
    response = _create(client, slug="web_search")
    assert "built-in tool" in response.text
    assert db.scalar(select(CustomTool)) is None


def test_a_duplicate_slug_is_refused(client: TestClient, db, registered):
    _create(client)
    response = _create(client, name="Other")
    assert "already a tool" in response.text
    assert len(list(db.scalars(select(CustomTool)))) == 1


def test_a_url_whose_host_is_a_variable_is_refused(client: TestClient, db, registered):
    response = _create(client, url_template="https://{{city}}.test/x")
    assert "literal" in response.text
    assert db.scalar(select(CustomTool)) is None


def test_a_rejected_save_keeps_what_was_typed(client: TestClient, db, registered):
    _create(client)
    tool = db.scalar(select(CustomTool))

    response = client.post(
        f"/admin/tools/{tool.id}",
        data=_form(name="Renamed", parameters="{oops"),
        follow_redirects=False,
    )
    assert "Renamed" in response.text, "the form still holds the submitted name"
    db.refresh(tool)
    assert tool.name == "Weather", "and the stored row is untouched"


# --- Access ------------------------------------------------------------------
def test_making_a_tool_public_clears_its_groups(client: TestClient, db, registered):
    group = Group(name="Council")
    db.add(group)
    db.commit()

    _create(client, public=None, group_ids=group.id)
    tool = db.scalar(select(CustomTool))
    assert [g.name for g in tool.groups] == ["Council"]

    client.post(
        f"/admin/tools/{tool.id}",
        data={**_form(public="true"), "group_ids": group.id},
        follow_redirects=False,
    )
    db.refresh(tool)
    assert tool.groups == []


def test_a_restricted_tool_is_hidden_from_a_user_outside_its_groups(db, registered):
    from lembas.services import tool_access

    group = Group(name="Council")
    outsider = User(name="Sam", email="s@shire.test", password_hash=hash_password("x"))
    db.add_all([group, outsider])
    db.add(
        CustomTool(
            slug="weather", name="Weather", url_template="https://api.test/", public=False
        )
    )
    db.commit()

    tool = db.scalar(select(CustomTool))
    tool.groups = [group]
    db.commit()

    assert tool_access.visible_custom_tools(db, outsider) == []
    outsider.groups = [group]
    db.commit()
    assert len(tool_access.visible_custom_tools(db, outsider)) == 1


# --- Running one by hand -----------------------------------------------------
def test_the_test_button_shows_what_the_model_would_read(
    client: TestClient, db, registered, mock_http, monkeypatch
):
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    mock_http(lambda _r: httpx.Response(200, json={"summary": "Sunny"}))
    _create(client, response_mode="json", response_path="summary")
    tool = db.scalar(select(CustomTool))

    response = client.post(
        f"/admin/tools/{tool.id}/test", data={"arguments": '{"city": "Minas Tirith"}'}
    )
    assert response.status_code == 200
    assert "Sunny" in response.text


def test_a_failing_test_is_recorded_on_the_row(
    client: TestClient, db, registered, mock_http, monkeypatch
):
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    mock_http(lambda _r: httpx.Response(503, text="down"))
    _create(client)
    tool = db.scalar(select(CustomTool))

    client.post(f"/admin/tools/{tool.id}/test", data={"arguments": "{}"})
    db.refresh(tool)
    assert "503" in tool.last_error
    assert tool.last_checked_at is not None


def test_bad_test_arguments_are_reported(client: TestClient, db, registered):
    _create(client)
    tool = db.scalar(select(CustomTool))
    response = client.post(f"/admin/tools/{tool.id}/test", data={"arguments": "[1, 2]"})
    assert "JSON object" in response.text


# --- The list ----------------------------------------------------------------
def test_the_list_searches_and_filters(client: TestClient, db, registered):
    _create(client)
    _create(client, name="Tickets", slug="tickets", url_template="https://jira.test/{{city}}")
    tool = db.scalar(select(CustomTool).where(CustomTool.slug == "tickets"))
    tool.enabled = False
    db.commit()

    page = client.get("/admin/tools").text
    assert "Weather" in page and "Tickets" in page

    assert "Tickets" not in client.get("/admin/tools?filter=enabled").text
    assert "Weather" not in client.get("/admin/tools?q=tick").text


def test_deleting_a_tool_leaves_its_prompt_override_alone(client: TestClient, db, registered):
    """The override outlives the row, which is what lets a tool be recreated
    under the same slug without losing the wording somebody chose."""
    from lembas.services import prompts as prompts_service

    _create(client, guidance="- Default wording.")
    tool = db.scalar(select(CustomTool))
    prompts_service.save(db, {"tool.custom_weather": "- Edited wording."})

    client.post(f"/admin/tools/{tool.id}/delete", follow_redirects=False)
    assert db.scalar(select(CustomTool)) is None
    assert prompts_service.stored(db)["tool.custom_weather"] == "- Edited wording."


def test_the_secret_survives_a_round_trip_through_the_form(client: TestClient, db, registered):
    """A regression guard on the sentinel: the field is rendered with dots, and
    submitting the page unchanged must not overwrite the key with them."""
    db.add(
        CustomTool(
            slug="weather",
            name="Weather",
            url_template="https://api.test/",
            secret_encrypted=encrypt("s3cret"),
        )
    )
    db.commit()
    tool = db.scalar(select(CustomTool))

    client.post(
        f"/admin/tools/{tool.id}", data=_form(secret=UNCHANGED_SENTINEL), follow_redirects=False
    )
    db.refresh(tool)
    assert decrypt(tool.secret_encrypted) == "s3cret"


# --- MCP servers -------------------------------------------------------------
def _mcp_form(**overrides) -> dict:
    base = {
        "name": "GitHub",
        "slug": "github",
        "url": "https://mcp.test/rpc",
        "headers": "",
        "secret_placement": "none",
        "secret_name": "Authorization",
        "timeout": "30",
        "max_chars": "8000",
        "position": "0",
        "enabled": "true",
        "public": "true",
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


@pytest.fixture
def fake_mcp(mock_http, monkeypatch):
    """A server that answers initialize and tools/list.

    Invented hostnames resolve to a public address; a literal IP is handed back
    as itself, so a test about a private address is still testing one.
    """
    import ipaddress

    def resolve(host, *_args, **_kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return [(2, 1, 6, "", ("93.184.216.34", 80))]
        return [(2, 1, 6, "", (host, 80))]

    monkeypatch.setattr("socket.getaddrinfo", resolve)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        message = json.loads(request.content)
        if message.get("method") == "notifications/initialized":
            return httpx.Response(202)
        if message.get("method") == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "fake"}},
                },
                headers={"mcp-session-id": "s-1"},
            )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"tools": [{"name": "search", "description": "Search."}]},
            },
        )

    mock_http(handler)
    return handler


def test_the_mcp_pages_are_refused_to_a_plain_user(client: TestClient, plain_user):
    assert client.get("/admin/mcp").status_code == 403
    assert client.get("/admin/mcp/new").status_code == 403
    assert client.post("/admin/mcp", data=_mcp_form()).status_code == 403


def test_mcp_new_is_not_parsed_as_a_server_id(client: TestClient, registered):
    response = client.get("/admin/mcp/new")
    assert response.status_code == 200
    assert "New MCP server" in response.text


def test_adding_a_server_discovers_its_tools(client: TestClient, db, registered, fake_mcp):
    from lembas.db.models import McpServer

    client.post("/admin/mcp", data=_mcp_form(), follow_redirects=False)

    server = db.scalar(select(McpServer))
    assert server.slug == "github"
    assert [entry["name"] for entry in server.tools_json] == ["search"]
    assert server.tools_json[0]["offer_name"] == "github_search"
    assert server.protocol_version == "2025-06-18"


def test_refreshing_a_server_swaps_its_row(client: TestClient, db, registered, fake_mcp):
    from lembas.db.models import McpServer

    client.post("/admin/mcp", data=_mcp_form(), follow_redirects=False)
    server = db.scalar(select(McpServer))

    response = client.post(f"/admin/mcp/{server.id}/test")
    assert response.status_code == 200
    assert "found 1 tool." in response.text
    assert f'id="mcp-{server.id}"' in response.text, "the fragment must render standalone"


def test_a_server_that_cannot_be_reached_says_so(
    client: TestClient, db, registered, mock_http, monkeypatch
):
    from lembas.db.models import McpServer

    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 80))]
    )
    mock_http(lambda _r: httpx.Response(500, text="down"))

    response = client.post("/admin/mcp", data=_mcp_form(), follow_redirects=False)
    assert response.status_code == 303
    server = db.scalar(select(McpServer))
    assert server is not None, "the row is still saved so the URL can be corrected"
    assert server.last_error


def test_a_private_url_is_not_contacted_unless_the_box_is_ticked(
    client: TestClient, db, registered, fake_mcp
):
    """The row still saves, so the URL can be corrected -- but the discovery
    that runs straight after it is refused, and the row says why."""
    from lembas.db.models import McpServer

    client.post(
        "/admin/mcp", data=_mcp_form(url="http://127.0.0.1:9000/rpc"), follow_redirects=False
    )
    server = db.scalar(select(McpServer))
    assert server.allow_private is False
    assert "private or local" in server.last_error
    assert server.tools_json == []

    client.post(
        f"/admin/mcp/{server.id}",
        data=_mcp_form(url="http://127.0.0.1:9000/rpc", allow_private="true"),
        follow_redirects=False,
    )
    client.post(f"/admin/mcp/{server.id}/test")
    db.refresh(server)
    assert server.last_error == ""
    assert [entry["name"] for entry in server.tools_json] == ["search"]


def test_a_duplicate_mcp_slug_is_refused(client: TestClient, db, registered, fake_mcp):
    from lembas.db.models import McpServer

    client.post("/admin/mcp", data=_mcp_form(), follow_redirects=False)
    response = client.post("/admin/mcp", data=_mcp_form(name="Other"), follow_redirects=False)
    assert "already a server" in response.text
    assert len(list(db.scalars(select(McpServer)))) == 1


def test_a_url_that_is_not_http_is_refused(client: TestClient, db, registered):
    from lembas.db.models import McpServer

    response = client.post(
        "/admin/mcp", data=_mcp_form(url="ftp://mcp.test/rpc"), follow_redirects=False
    )
    assert "http" in response.text
    assert db.scalar(select(McpServer)) is None


def test_unticking_a_tool_withholds_it(client: TestClient, db, registered, fake_mcp):
    from lembas.db.models import McpServer, User
    from lembas.services.mcp import registry as mcp_registry

    client.post("/admin/mcp", data=_mcp_form(), follow_redirects=False)
    server = db.scalar(select(McpServer))

    client.post(
        f"/admin/mcp/{server.id}",
        data={**_mcp_form(), "tool_choices": "1", "tool_names": "search"},
        follow_redirects=False,
    )
    db.refresh(server)
    assert server.tool_overrides_json == {"search": False}

    owner = db.scalar(select(User))
    assert mcp_registry.tool_defs(db, owner) == []


def test_deleting_a_server_removes_it(client: TestClient, db, registered, fake_mcp):
    from lembas.db.models import McpServer

    client.post("/admin/mcp", data=_mcp_form(), follow_redirects=False)
    server = db.scalar(select(McpServer))

    client.post(f"/admin/mcp/{server.id}/delete", follow_redirects=False)
    assert db.scalar(select(McpServer)) is None
