"""The load-state dot in the model menu: only what an endpoint states.

llama-swap reports `"status": {"value": "loaded" | "unloaded"}` on every entry
of `GET /v1/models`, verified against a live one. A hosted API
such as DeepSeek has no such field, so its models must get no state at all --
not "unloaded", which would be a claim nobody made.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from lembas.db.models import Connection, Model
from lembas.services import model_state

LLAMA_SWAP = [
    {"id": "bonsai", "status": {"value": "loaded"}},
    {"id": "gpt-oss", "status": {"value": "unloaded"}},
    {"id": "qwen36", "status": {"value": "starting"}},
]
HOSTED = [{"id": "deepseek-flash", "object": "model"}]


@pytest.fixture(autouse=True)
def _fresh_cache():
    model_state.forget()
    yield
    model_state.forget()


@pytest.mark.parametrize(
    ("entry", "state"),
    [
        ({"status": {"value": "loaded"}}, "loaded"),
        ({"status": {"value": "ready"}}, "loaded"),
        ({"status": "loaded"}, "loaded"),
        ({"status": {"value": "starting"}}, "loading"),
        ({"status": {"value": "unloaded"}}, "unloaded"),
        ({"status": {"value": "stopped"}}, "unloaded"),
        ({}, ""),
        ({"status": {}}, ""),
        ({"status": 3}, ""),
    ],
)
def test_state_is_read_from_the_entry_or_not_at_all(entry, state):
    assert model_state.state_of({"id": "x", **entry}) == state


def _two_connections(db):
    local = Connection(name="llama", base_url="http://llama.test/v1", api_key_encrypted="")
    hosted = Connection(name="deepseek", base_url="http://hosted.test/v1", api_key_encrypted="")
    db.add_all([local, hosted])
    db.commit()
    served = ((local, ("bonsai", "gpt-oss", "qwen36")), (hosted, ("deepseek-flash",)))
    for connection, ids in served:
        for model_id in ids:
            db.add(Model(connection_id=connection.id, model_id=model_id))
    db.commit()
    return local, hosted


def _fake_endpoints(monkeypatch, calls):
    async def fake(endpoint):
        calls.append(endpoint.base_url)
        return LLAMA_SWAP if "llama" in endpoint.base_url else HOSTED

    monkeypatch.setattr(model_state, "list_models", fake)


def test_only_models_whose_endpoint_states_one_get_a_state(
    client: TestClient, db, registered, monkeypatch
):
    _two_connections(db)
    calls: list[str] = []
    _fake_endpoints(monkeypatch, calls)

    states = client.get("/api/models/state").json()["states"]

    assert states == {"bonsai": "loaded", "gpt-oss": "unloaded", "qwen36": "loading"}
    assert "deepseek-flash" not in states
    # One request per connection, not per model.
    assert sorted(calls) == ["http://hosted.test/v1", "http://llama.test/v1"]


def test_a_silent_endpoint_is_not_asked_again_on_every_open(
    client: TestClient, db, registered, monkeypatch
):
    """A hosted API answers with no state every time. Asking it on each click
    only to hear nothing again is a request to a third party for no reason."""
    _two_connections(db)
    calls: list[str] = []
    _fake_endpoints(monkeypatch, calls)

    client.get("/api/models/state")
    client.get("/api/models/state")

    assert calls.count("http://hosted.test/v1") == 1


def test_an_unreachable_endpoint_is_a_menu_without_dots(
    client: TestClient, db, registered, monkeypatch
):
    _two_connections(db)

    async def broken(endpoint):
        raise OSError("connection refused")

    monkeypatch.setattr(model_state, "list_models", broken)
    response = client.get("/api/models/state")

    assert response.status_code == 200
    assert response.json() == {"states": {}}


def test_a_slow_endpoint_cannot_hold_the_menu(db, monkeypatch):
    connection = Connection(name="slow", base_url="http://slow.test/v1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m"))
    db.commit()

    async def slow(endpoint):
        await asyncio.sleep(10)
        return LLAMA_SWAP

    monkeypatch.setattr(model_state, "list_models", slow)
    monkeypatch.setattr(model_state, "TIMEOUT", 0.05)
    models = db.query(Model).all()

    assert asyncio.run(model_state.states_for(models)) == {}


def test_the_menu_has_a_slot_for_every_model(client: TestClient, db, registered):
    """Every option emits the slot, whatever its endpoint says: the dot is
    placed by ui.js after the menu opens, so a model with no slot could never
    show one."""
    _two_connections(db)
    html = client.get("/chat").text

    for model_id in ("bonsai", "gpt-oss", "qwen36", "deepseek-flash"):
        start = html.index(f'data-model-id="{model_id}"')
        option = html[start : html.index("</button>", start)]
        assert 'data-model-state=""' in option
    assert "data-label-loaded=" in html


def test_the_state_needs_a_signed_in_reader(client: TestClient):
    response = client.get("/api/models/state", follow_redirects=False)
    assert response.status_code in (401, 303, 307)
