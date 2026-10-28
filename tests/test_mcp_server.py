"""`/mcp`: a person's memory over MCP, with a `memory` token (or `library`).

The tool is the chat's own `memory`; the permissions that gate it in a chat
gate it here; nothing else is reachable (spec 3.0.0: a device keeps notes,
skills and knowledge itself).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import Memory, User
from lembas.security import api_tokens
from lembas.services import settings_store


@pytest.fixture(autouse=True)
def _rates():
    api_tokens.reset_rates()
    yield


def _token(db, scopes=("memory",)) -> dict[str, str]:
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
    assert names == {"memory"}


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
        {
            "name": "memory",
            "arguments": {"target": "user", "action": "add", "content": "Prefers tabs."},
        },
    ).json()
    assert r["result"]["isError"] is False
    db.expire_all()
    assert db.scalar(select(Memory)).content == "Prefers tabs."


def test_a_former_name_is_the_call_it_means(client, db, registered):
    """A device that listed the tools before an update calls them by the names
    it was given then: `memory_add` is `memory` adding."""
    r = _rpc(
        client,
        _token(db),
        "tools/call",
        {"name": "memory_add", "arguments": {"content": "Prefers spaces."}},
    ).json()
    assert r["result"]["isError"] is False
    db.expire_all()
    assert db.scalar(select(Memory)).content == "Prefers spaces."


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
    # ... and with it, the memory permissions still decide.
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
    assert "memory" not in names
    r = _rpc(client, headers, "tools/call", {"name": "memory_add", "arguments": {"content": "x"}})
    assert "error" in r.json()
    arguments = {"target": "user", "action": "add", "content": "x"}
    r = _rpc(client, headers, "tools/call", {"name": "memory", "arguments": arguments})
    assert "error" in r.json()


def test_an_unknown_method(client, db, registered):
    assert _rpc(client, _token(db), "prompts/list").json()["error"]["code"] == -32601


def test_the_older_library_scope_counts_as_memory(client, db, registered):
    headers = _token(db, scopes=("library",))
    names = {t["name"] for t in _rpc(client, headers, "tools/list").json()["result"]["tools"]}
    assert names == {"memory"}
    # ... and is not offered for a new token any more.
    assert "library" not in api_tokens.grantable(db, db.scalar(select(User)))
    assert "memory" in api_tokens.grantable(db, db.scalar(select(User)))


def test_the_memory_resource_is_the_prompt_s_block(client, db, registered):
    from lembas.services import memories

    headers = _token(db)
    listed = _rpc(client, headers, "resources/list").json()["result"]["resources"]
    assert [r["uri"] for r in listed] == ["lembas://memory"]
    empty = _rpc(client, headers, "resources/read", {"uri": "lembas://memory"}).json()
    assert empty["result"]["contents"][0]["text"] == ""
    user = db.scalar(select(User))
    memories.add(db, owner=user, content="Prefers tabs.")
    memories.add(db, owner=user, content="Lives in Trnava.")
    read = _rpc(client, headers, "resources/read", {"uri": "lembas://memory"}).json()
    text = read["result"]["contents"][0]["text"]
    assert text == memories.block(db, user) == "- Prefers tabs.\n- Lives in Trnava."
    assert _rpc(client, headers, "resources/read", {"uri": "lembas://notes"}).json()["error"][
        "code"
    ] == -32602


def test_a_scope_that_is_not_this_device_s_session_is_refused(client, db, registered):
    headers = _token(db)
    meta = {"_meta": {"lembas": {"scope": "ses_nobody"}}}
    r = _rpc(client, headers, "resources/read", {"uri": "lembas://memory", **meta}).json()
    assert r["error"]["code"] == -32602
    r = _rpc(client, headers, "tools/call", {
        "name": "memory", "arguments": {"target": "user", "action": "add", "content": "x"},
        **meta,
    }).json()
    assert r["error"]["code"] == -32602


def test_the_resource_is_not_listed_without_the_memory_permission(client, db, registered):
    headers = _token(db)
    user = db.scalar(select(User))
    user.role = "user"
    db.commit()
    settings_store.update(db, {"default_permissions": {"api.use": True, "library.use": True,
                                                       "tools.memory": False}})
    assert _rpc(client, headers, "resources/list").json()["result"]["resources"] == []
    r = _rpc(client, headers, "resources/read", {"uri": "lembas://memory"}).json()
    assert r["error"]["code"] == -32602


def test_get_is_not_a_stream(client, db, registered):
    assert client.get("/mcp").status_code == 405

