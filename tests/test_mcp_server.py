"""`/mcp`: a person's library over MCP, with a `library` token.

The tools are the chat's own library tools; the permissions that gate them in a
chat gate them here; nothing outside the library is reachable.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import Memory, Note, User
from lembas.security import api_tokens
from lembas.services import settings_store


@pytest.fixture(autouse=True)
def _rates():
    api_tokens.reset_rates()
    yield


def _token(db, scopes=("library",)) -> dict[str, str]:
    _, raw = api_tokens.create(db, db.scalar(select(User)), name="dev", scopes=list(scopes))
    return {"Authorization": f"Bearer {raw}"}


def _rpc(client, headers, method, params=None, id_=1):
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", headers=headers, json=body)


def test_initialize_and_list(client, db, registered):
    headers = _token(db)
    init = _rpc(client, headers, "initialize", {"protocolVersion": "2025-06-18"}).json()
    assert init["result"]["serverInfo"]["name"] == "lembas"
    names = {t["name"] for t in _rpc(client, headers, "tools/list").json()["result"]["tools"]}
    assert {"memory_add", "notes_search", "knowledge_search"} <= names
    # Nothing that is not the library.
    assert not names & {
        "bash", "fetch", "web_fetch", "web_search", "image_generate", "schedule_create"
    }


def test_a_notification_is_accepted_without_an_answer(client, db, registered):
    r = client.post(
        "/mcp", headers=_token(db), json={"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert r.status_code == 202


def test_a_call_writes_to_the_holders_library(client, db, registered):
    headers = _token(db)
    r = _rpc(
        client,
        headers,
        "tools/call",
        {"name": "memory_add", "arguments": {"content": "Prefers tabs."}},
    ).json()
    assert r["result"]["isError"] is False
    db.expire_all()
    assert db.scalar(select(Memory)).content == "Prefers tabs."


def test_reading_a_note(client, db, registered):
    from lembas.services.library import notes

    notes.create(db, owner=db.scalar(select(User)), title="Map", body="There and back again.")
    r = _rpc(
        client,
        _token(db),
        "tools/call",
        {"name": "notes_search", "arguments": {"query": "back again"}},
    ).json()
    assert "Map" in r["result"]["content"][0]["text"]


def test_the_scope_is_required(client, db, registered):
    assert _rpc(client, _token(db, scopes=("models",)), "tools/list").status_code == 403
    client.cookies.clear()
    assert (
        client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    )


def test_the_permissions_gate_it(client, db, registered):
    headers = _token(db)
    user = db.scalar(select(User))
    user.role = "user"
    db.commit()
    # A plain account may not use the API at all by default ...
    assert _rpc(client, headers, "tools/list").status_code == 403
    # ... and with it, the library permissions still decide.
    settings_store.update(
        db,
        {
            "default_permissions": {
                "api.use": True,
                "library.use": True,
                "tools.memory.write": False,
            }
        },
    )
    names = {t["name"] for t in _rpc(client, headers, "tools/list").json()["result"]["tools"]}
    assert "memory_add" not in names and "notes_search" in names
    r = _rpc(client, headers, "tools/call", {"name": "memory_add", "arguments": {"content": "x"}})
    assert "error" in r.json()


def test_an_unknown_method(client, db, registered):
    assert _rpc(client, _token(db), "resources/list").json()["error"]["code"] == -32601


def test_get_is_not_a_stream(client, db, registered):
    assert client.get("/mcp").status_code == 405


def test_notes_created_over_mcp_are_the_persons(client, db, registered):
    r = _rpc(
        client,
        _token(db),
        "tools/call",
        {"name": "notes_create", "arguments": {"title": "From device-1", "body": "Hello."}},
    ).json()
    assert r["result"]["isError"] is False
    db.expire_all()
    assert db.scalar(select(Note)).owner_id == db.scalar(select(User)).id
