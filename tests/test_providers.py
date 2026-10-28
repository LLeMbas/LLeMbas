"""Provider types (WP13): the registry, what a connection's type changes, detection
from a pasted URL, capabilities detected under the administrator's word, health,
the provider's balance, the connection's currency, and opt-in fallback.

No network: every provider is faked (`mock_http`).
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from sqlalchemy import select, text

from lembas.db.models import Connection, Message, Model
from lembas.db.session import get_engine
from lembas.services import pricing
from lembas.services.crypto import decrypt, encrypt
from lembas.services.llm import balance, capabilities, detect, health, providers
from lembas.services.llm.openai_client import Endpoint, LLMError, StreamError, chunk_usage


# --- The registry -------------------------------------------------------------------
def test_five_types_and_what_a_host_names():
    assert providers.TYPE_IDS == (
        "openai_compatible", "openai", "anthropic", "deepseek", "openrouter"
    )
    assert providers.by_host("https://api.deepseek.com/v1").id == "deepseek"
    assert providers.by_host("https://openrouter.ai/api/v1").id == "openrouter"
    assert providers.by_host("https://api.openai.com/v1").id == "openai"
    assert providers.by_host("https://api.anthropic.com").id == "anthropic"
    assert providers.by_host("http://localhost:8080/v1") is None
    # Not a suffix match on a lookalike.
    assert providers.by_host("https://evilopenrouter.ai/v1") is None
    assert providers.get("nonsense").id == "openai_compatible"
    assert providers.protocol_of("anthropic") == "anthropic"
    assert providers.protocol_of("deepseek") == "openai"


def test_an_older_kind_becomes_its_type():
    assert providers.from_legacy_kind("anthropic") == "anthropic"
    assert providers.from_legacy_kind("openai", "https://api.deepseek.com") == "deepseek"
    assert providers.from_legacy_kind("openai", "http://gpu.local:8080") == "openai_compatible"
    assert providers.from_legacy_kind(None) == "openai_compatible"


def _effort_body():
    return {
        "model": "m", "messages": [],
        "reasoning_effort": "high",
        "chat_template_kwargs": {"reasoning_effort": "high", "enable_thinking": True},
    }


def test_the_effort_goes_where_each_type_reads_it():
    body = _effort_body()
    # Generic: both forms, untouched (llama.cpp reads the template's).
    assert providers.shape_request("openai_compatible", body) is body
    openai = providers.shape_request("openai", body)
    assert openai["reasoning_effort"] == "high"
    assert openai["chat_template_kwargs"] == {"enable_thinking": True}
    deepseek = providers.shape_request("deepseek", body)
    assert "reasoning_effort" not in deepseek
    router = providers.shape_request("openrouter", body)
    assert router["reasoning"] == {"effort": "high"} and "reasoning_effort" not in router
    assert router["usage"] == {"include": True}
    # The caller's body is never changed.
    assert body == _effort_body()
    bare = providers.shape_request("openai", {"model": "m", "messages": []})
    assert "chat_template_kwargs" not in bare and "reasoning_effort" not in bare


def test_usage_reads_deepseek_cache_hits_and_openrouter_cost():
    deepseek = chunk_usage({"choices": [], "usage": {
        "prompt_tokens": 100, "completion_tokens": 10,
        "prompt_cache_hit_tokens": 60, "prompt_cache_miss_tokens": 40}})
    assert deepseek["cached_tokens"] == 60
    router = chunk_usage({"choices": [], "usage": {
        "prompt_tokens": 100, "completion_tokens": 10, "cost": 0.0012,
        "prompt_tokens_details": {"cached_tokens": 5}}})
    assert router["cached_tokens"] == 5 and router["reported_cost_micro"] == 1200
    plain = chunk_usage({"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    assert "reported_cost_micro" not in plain


# --- The connection's type ---------------------------------------------------------
def test_kind_follows_the_type_and_an_older_kind_still_means_its_type(db):
    typed = Connection(name="DS", base_url="https://api.deepseek.com", provider_type="deepseek")
    legacy = Connection(name="Claude", base_url="http://proxy.test/anthropic", kind="anthropic")
    plain = Connection(name="Local", base_url="http://127.0.0.1:8080/v1")
    db.add_all([typed, legacy, plain])
    db.commit()
    assert (typed.provider_type, typed.kind) == ("deepseek", "openai")
    assert (legacy.provider_type, legacy.kind) == ("anthropic", "anthropic")
    assert (plain.provider_type, plain.kind, plain.currency) == (
        "openai_compatible", "openai", "USD"
    )
    typed.provider_type = "anthropic"
    db.commit()
    assert typed.kind == "anthropic" and typed.is_anthropic
    # A `kind` changed on its own (an older caller) moves the type with it.
    legacy.kind = "openai"
    db.commit()
    assert legacy.provider_type == "openai_compatible"
    assert Endpoint.from_connection(typed).provider_type == "anthropic"


def test_the_upgrade_step_gives_old_rows_their_type(db):
    from lembas.db.migrations import provider_types

    db.add_all([
        Connection(name="a", base_url="https://api.anthropic.com", provider_type="anthropic"),
        Connection(name="b", base_url="https://openrouter.ai/api/v1"),
        Connection(name="c", base_url="http://127.0.0.1:1/v1"),
    ])
    db.commit()
    engine = get_engine()
    # As the column's backfill leaves them: every row generic, the dialect kept.
    with engine.begin() as connection:
        connection.execute(text("UPDATE connections SET provider_type = 'openai_compatible'"))
    done = provider_types(engine)
    db.expire_all()
    rows = {c.name: (c.provider_type, c.kind) for c in db.scalars(select(Connection))}
    assert rows == {"a": ("anthropic", "anthropic"), "b": ("openrouter", "openai"),
                    "c": ("openai_compatible", "openai")}
    assert len(done) == 2


# --- Detection from a pasted URL ------------------------------------------------------
def _server(routes: dict[str, object], asked: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if asked is not None:
            asked.append((request.method, str(request.url), dict(request.headers)))
        for path, body in routes.items():
            if request.url.path == path:
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": "not here"})
    return handler


def test_a_cloud_host_is_known_without_asking(mock_http):
    asked: list = []
    mock_http(_server({}, asked))
    found = asyncio.run(detect.detect("https://api.deepseek.com/v1", "sk-secret"))
    assert found["ok"] and found["provider_type"] == "deepseek"
    assert found["base_url"] == "https://api.deepseek.com"
    assert asked == []


@pytest.mark.parametrize(("routes", "server", "suggest"), [
    ({"/running": {"running": []}, "/v1/models": {"data": [{"id": "a"}, {"id": "b"}]}},
     "llama-swap", {"one_model_at_a_time": True, "unload_url": "http://gpu.test:8080/unload"}),
    ({"/props": {"default_generation_settings": {"n_ctx": 8192}},
      "/v1/models": {"data": [{"id": "m"}]}}, "llama.cpp", {}),
    ({"/api/version": {"version": "0.9.0"}, "/v1/models": {"data": [{"id": "qwen"}]}},
     "ollama", {}),
    ({"/api/v0/models": {"data": [{"id": "x", "max_context_length": 4096}]},
      "/v1/models": {"data": [{"id": "x"}]}}, "lmstudio", {}),
    ({"/v1/models": {"data": [{"id": "x", "owned_by": "vllm", "max_model_len": 32768}]}},
     "vllm", {}),
    ({"/v1/models": {"data": [{"id": "x"}]}}, "openai-compatible", {}),
])
def test_a_local_server_is_recognised(mock_http, routes, server, suggest):
    asked: list = []
    mock_http(_server(routes, asked))
    found = asyncio.run(detect.detect("http://gpu.test:8080/v1", "local-key"))
    assert found["ok"] and found["provider_type"] == "openai_compatible"
    assert found["server"] == server and found["suggest"] == suggest
    assert found["base_url"] == "http://gpu.test:8080/v1"
    # Only the pasted host is asked, and the key goes only with the model list.
    assert {url.split("/")[2] for _, url, _ in asked} == {"gpu.test:8080"}
    keyed = [url for _, url, headers in asked if "authorization" in headers]
    assert keyed == ["http://gpu.test:8080/v1/models"]


def test_nothing_there_says_so(mock_http):
    mock_http(_server({}))
    found = asyncio.run(detect.detect("http://nothing.test:9/v1"))
    assert not found["ok"] and "answered" in found["error"]
    assert not asyncio.run(detect.detect("ftp://x"))["ok"]


def test_the_detect_route_answers_json(client, db, registered, mock_http):
    mock_http(_server({"/api/version": {"version": "1"}, "/v1/models": {"data": []}}))
    body = client.post("/admin/connections/detect",
                       data={"base_url": "http://box.test:11434"}).json()
    assert body["provider_type"] == "openai_compatible" and body["server"] == "ollama"
    assert body["label"].startswith("OpenAI-compatible")


# --- Capabilities: detected, and the administrator's word over it ------------------------
def _local(db, **kwargs) -> tuple[Connection, Model]:
    connection = Connection(name="box", base_url="http://box.test:8080/v1", **kwargs)
    db.add(connection)
    db.commit()
    model = Model(connection_id=connection.id, model_id="m")
    db.add(model)
    db.commit()
    return connection, model


def test_llama_cpp_props_fill_in_what_the_model_can_do(client, db, registered, mock_http):
    connection, _ = _local(db)
    mock_http(_server({
        "/v1/models": {"data": [{"id": "m"}]},
        "/props": {"default_generation_settings": {"n_ctx": 16384},
                   "modalities": {"vision": True}, "chat_template": "{% if tools %}x{% endif %}"},
    }))
    client.post(f"/admin/connections/{connection.id}/test")
    db.expire_all()
    model = db.scalar(select(Model))
    assert model.capabilities_detected_json == {
        "context_length": 16384, "vision": True, "tools": True, "source": "/props"
    }
    assert model.context_length == 16384
    assert model.capabilities_json["vision"] is True and model.capabilities_json["tools"] is True


def test_ollama_show_and_openrouter_models_are_read(mock_http):
    def handler(request):
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.9"})
        if request.url.path == "/api/show":
            assert json.loads(request.content)["model"] == "qwen3"
            return httpx.Response(200, json={
                "model_info": {"qwen3.context_length": 40960},
                "capabilities": ["completion", "tools", "thinking"]})
        return httpx.Response(404)
    mock_http(handler)
    found = asyncio.run(detect.model_capabilities(
        Endpoint("http://box.test:11434/v1", "", {}), [{"id": "qwen3"}]))
    assert found["qwen3"] == ({"context_length": 40960, "vision": False, "tools": True,
                               "reasoning": True, "embeddings": False}, "/api/show")
    router = asyncio.run(detect.model_capabilities(
        Endpoint("https://openrouter.ai/api/v1", "k", {}, provider_type="openrouter"),
        [{"id": "a/b", "context_length": 200000,
          "architecture": {"input_modalities": ["text", "image"]},
          "supported_parameters": ["tools", "reasoning"]}]))
    assert router["a/b"][0] == {"context_length": 200000, "vision": True, "tools": True,
                                "reasoning": True}


def test_an_override_always_wins_and_auto_follows_detection(client, db, registered):
    _, model = _local(db)
    capabilities.record_detected(model, {"vision": True, "tools": True,
                                         "context_length": 8192}, "/props")
    db.commit()
    form = {"enabled": "true", "public": "true", "cap_vision": "no", "cap_tools": "auto",
            "cap_reasoning": "auto", "cap_embeddings": "auto", "context_length": "4096"}
    assert client.post(f"/admin/models/{model.id}", data=form).status_code in (200, 303)
    db.expire_all()
    model = db.get(Model, model.id)
    assert model.capabilities_override_json == {"vision": False, "context_length": 4096}
    assert model.capabilities_json["vision"] is False and model.capabilities_json["tools"] is True
    assert model.context_length == 4096
    # A later refresh changes the detection, never the override.
    capabilities.record_detected(model, {"vision": True, "tools": False,
                                         "context_length": 32768}, "/props")
    db.commit()
    assert model.capabilities_json["vision"] is False and model.capabilities_json["tools"] is False
    assert model.context_length == 4096
    page = client.get(f"/admin/models/{model.id}/edit").text
    assert 'data-testid="model-capabilities"' in page and "Detected: yes" in page
    # Emptied, the length follows the endpoint again.
    client.post(f"/admin/models/{model.id}", data={**form, "context_length": ""})
    db.expire_all()
    assert db.get(Model, model.id).context_length == 32768


# --- Health ------------------------------------------------------------------------
def test_health_is_ok_loading_down_or_error(db, mock_http):
    connection, _ = _local(db)
    mock_http(_server({"/v1/models": {"data": []},
                       "/running": {"running": [{"model": "big", "state": "starting"}]}}))
    found = asyncio.run(health.check(db, connection))
    assert found["status"] == "loading" and found["loading"] == "big"
    assert isinstance(found["latency_ms"], int) and found["checked_at"]

    mock_http(_server({"/v1/models": {"data": []}}))
    assert asyncio.run(health.check(db, connection))["status"] == "ok"

    mock_http(lambda request: httpx.Response(500, json={"error": {"message": "boom"}}))
    assert asyncio.run(health.check(db, connection))["status"] == "error"

    def unreachable(request):
        raise httpx.ConnectError("refused", request=request)
    mock_http(unreachable)
    found = asyncio.run(health.check(db, connection))
    assert found["status"] == "down" and "Could not reach" in found["error"]


def test_the_ticker_round_checks_enabled_connections_and_keeps_the_balance(db, mock_http):
    connection, _ = _local(db)
    off = Connection(name="off", base_url="http://off.test/v1", enabled=False)
    db.add(off)
    connection.health_json = {"server": "llama.cpp", "balance": {"ok": True}}
    db.commit()
    health.reset()
    mock_http(_server({"/v1/models": {"data": []}}))
    assert asyncio.run(health.tick(force=True)) == 1
    db.expire_all()
    kept = db.get(Connection, connection.id).health_json
    assert kept["status"] == "ok" and kept["server"] == "llama.cpp" and kept["balance"]
    assert db.get(Connection, off.id).health_json == {}


def test_the_connections_page_shows_the_dot_and_never_a_key(client, db, registered):
    connection = Connection(name="OR", base_url="https://openrouter.ai/api/v1",
                            provider_type="openai", api_key_encrypted=encrypt("sk-or-secret"),
                            billing_key_encrypted=encrypt("sk-admin-secret"),
                            health_json={"status": "loading", "loading": "big", "latency_ms": 5})
    db.add(connection)
    db.commit()
    page = client.get("/admin/connections").text
    assert "sk-or-secret" not in page and "sk-admin-secret" not in page
    assert 'data-status="loading"' in page and "is-wait" in page
    assert 'name="billing_key"' in page and 'value="openai" selected' in page
    assert 'data-testid="connection-balance"' in page


def test_saving_keeps_the_billing_key_behind_the_dots(client, db, registered):
    from lembas.services.crypto import UNCHANGED_SENTINEL

    connection = Connection(name="O", base_url="https://api.openai.com/v1", provider_type="openai",
                            billing_key_encrypted=encrypt("sk-admin"))
    db.add(connection)
    db.commit()
    form = {"name": "O", "base_url": "https://api.openai.com/v1", "enabled": "true",
            "provider_type": "openai", "currency": "EUR", "billing_key": UNCHANGED_SENTINEL}
    client.post(f"/admin/connections/{connection.id}", data=form)
    db.expire_all()
    saved = db.get(Connection, connection.id)
    assert decrypt(saved.billing_key_encrypted) == "sk-admin" and saved.currency == "EUR"
    client.post(f"/admin/connections/{connection.id}", data={**form, "billing_key": ""})
    db.expire_all()
    assert db.get(Connection, connection.id).billing_key_encrypted == ""


def test_adding_takes_the_type_s_address_and_what_detection_suggested(
    client, db, registered, mock_http
):
    mock_http(_server({"/user/balance": {}, "/models": {"data": [{"id": "deepseek-chat"}]}}))
    client.post("/admin/connections", data={
        "name": "DeepSeek", "provider": "deepseek", "provider_type": "deepseek",
        "base_url": "", "api_key": "sk-ds", "currency": "EUR"})
    connection = db.scalar(select(Connection))
    assert connection.base_url == "https://api.deepseek.com" and connection.currency == "EUR"
    mock_http(_server({"/v1/models": {"data": []}}))
    client.post("/admin/connections", data={
        "name": "Swap", "provider": "swap", "base_url": "http://gpu.test:8080/v1",
        "one_model_at_a_time": "true", "unload_url": "http://gpu.test:8080/unload",
        "unload_method": "GET", "server": "llama-swap"})
    swap = db.scalar(select(Connection).where(Connection.name == "Swap"))
    assert swap.one_model_at_a_time and swap.unload_method == "GET"
    assert swap.health_json["server"] == "llama-swap"


# --- Balance --------------------------------------------------------------------------
def test_deepseek_and_openrouter_balances(mock_http):
    mock_http(_server({"/user/balance": {"is_available": True, "balance_infos": [
        {"currency": "CNY", "total_balance": "110.00", "granted_balance": "10.00",
         "topped_up_balance": "100.00"}]}}))
    found = asyncio.run(balance.read(Endpoint("https://api.deepseek.com", "k", {},
                                              provider_type="deepseek")))
    assert found["ok"] and found["kind"] == "balance"
    assert found["items"] == [{"currency": "CNY", "amount": 110.0, "granted": 10.0,
                               "topped_up": 100.0}]
    mock_http(_server({"/api/v1/credits": {"data": {"total_credits": 20, "total_usage": 7.5}}}))
    found = asyncio.run(balance.read(Endpoint("https://openrouter.ai/api/v1", "k", {},
                                              provider_type="openrouter")))
    assert found["items"][0]["amount"] == 12.5 and found["items"][0]["currency"] == "USD"


def test_openai_and_anthropic_need_the_billing_key_and_report_spend(mock_http):
    asked: list = []
    mock_http(_server({"/v1/organization/costs": {"data": [
        {"results": [{"amount": {"value": 1.5, "currency": "usd"}}]},
        {"results": [{"amount": {"value": 0.25, "currency": "usd"}}]}]}}, asked))
    endpoint = Endpoint("https://api.openai.com/v1", "sk-normal", {}, provider_type="openai")
    assert not asyncio.run(balance.read(endpoint))["ok"]
    found = asyncio.run(balance.read(endpoint, "sk-admin"))
    assert found["kind"] == "spent_30d" and found["items"] == [{"currency": "USD",
                                                                "amount": 1.75}]
    assert asked[-1][2]["authorization"] == "Bearer sk-admin"
    mock_http(_server({"/v1/organizations/cost_report": {"data": [
        {"results": [{"amount": "250.00", "currency": "USD"}]}]}}, asked))
    found = asyncio.run(balance.read(
        Endpoint("https://api.anthropic.com", "k", {}, kind="anthropic",
                 provider_type="anthropic"), "sk-ant-admin"))
    assert found["items"] == [{"currency": "USD", "amount": 2.5}]
    assert asked[-1][2]["x-api-key"] == "sk-ant-admin"


def test_a_refused_or_absent_balance_is_said_not_raised(mock_http):
    mock_http(lambda request: httpx.Response(401, json={}))
    found = asyncio.run(balance.read(Endpoint("https://api.deepseek.com", "k", {},
                                              provider_type="deepseek")))
    assert not found["ok"] and "refused" in found["error"]
    found = asyncio.run(balance.read(Endpoint("http://box/v1", "", {})))
    assert not found["ok"] and "no balance" in found["error"]


def test_the_balance_button_keeps_it_on_the_row(client, db, registered, mock_http):
    connection = Connection(name="DS", base_url="https://api.deepseek.com",
                            provider_type="deepseek", api_key_encrypted=encrypt("k"))
    db.add(connection)
    db.commit()
    mock_http(_server({"/user/balance": {"balance_infos": [
        {"currency": "USD", "total_balance": "3.20"}]}}))
    row = client.post(f"/admin/connections/{connection.id}/balance").text
    assert "3.2 USD" in row
    db.expire_all()
    assert db.get(Connection, connection.id).health_json["balance"]["ok"] is True


# --- Currency and the provider's own cost ------------------------------------------------
def test_a_reply_is_priced_in_its_connection_s_currency(db):
    connection = Connection(name="c", base_url="http://x/v1", currency="EUR")
    db.add(connection)
    db.commit()
    model = Model(connection_id=connection.id, model_id="m", pricing_json={"output": 10})
    db.add(model)
    db.commit()
    priced = pricing.price_reply(db, model, {"completion_tokens": 1_000_000})
    assert priced["currency"] == "EUR" and priced["cost_micro"] == 10 * pricing.MICRO
    assert priced["priced_json"]["source"] == "list"


def test_the_provider_s_reported_cost_is_preferred_and_labelled(db):
    connection = Connection(name="or", base_url="https://openrouter.ai/api/v1",
                            provider_type="openrouter", currency="EUR")
    db.add(connection)
    db.commit()
    model = Model(connection_id=connection.id, model_id="m", pricing_json={"output": 10})
    db.add(model)
    db.commit()
    priced = pricing.price_reply(db, model, {"completion_tokens": 1_000_000},
                                 reported_micro=1234)
    assert priced == {"cost_micro": 1234, "currency": "USD",
                      "priced_json": {"output": 10.0, "model_id": "m", "source": "provider"}}


# --- Fallback --------------------------------------------------------------------------
def _reply(db) -> Message:
    for _ in range(200):
        db.expire_all()
        reply = db.scalar(select(Message).where(Message.role == "assistant"))
        if reply is not None and reply.complete:
            return reply
        time.sleep(0.05)
    raise AssertionError("the reply never finished")


def _two_models(db, *, fallback: bool = True) -> tuple[Model, Model]:
    first = Connection(name="first", base_url="http://first.test/v1")
    second = Connection(name="second", base_url="http://second.test/v1", currency="EUR")
    db.add_all([first, second])
    db.commit()
    backup = Model(connection_id=second.id, model_id="backup", display_name="Backup",
                   pricing_json={"output": 10})
    db.add(backup)
    db.commit()
    main = Model(connection_id=first.id, model_id="m", display_name="Main",
                 fallback_model_ids=[backup.id] if fallback else [])
    db.add(main)
    db.commit()
    return main, backup


def _upstream(first_status: int | None):
    """`first.test` fails with `first_status` (None: unreachable); `second.test` answers."""
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "first.test":
            if first_status is None:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(first_status, json={"error": {"message": "nope"}})
        frames = ({"choices": [{"delta": {"content": "from backup"}}]},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                  {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1_000_000}})
        body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    return handler, seen


@pytest.mark.parametrize("status", [None, 503])
def test_a_failed_connection_falls_back_and_says_so(client, db, registered, mock_http, status):
    _, backup = _two_models(db)
    handler, seen = _upstream(status)
    mock_http(handler)
    client.post("/api/chats/start", data={"content": "hello", "model_id": "m"})
    reply = _reply(db)
    assert reply.content.endswith("from backup")
    assert "Main did not answer" in reply.content and "Backup answered instead" in reply.content
    # The row records who answered, and that model's price, in its currency.
    assert (reply.model_id, reply.connection_id) == ("backup", backup.connection_id)
    assert reply.currency == "EUR" and reply.cost_micro == 10 * pricing.MICRO
    assert "second.test" in seen


@pytest.mark.parametrize("status", [400, 401, 422])
def test_a_refused_request_never_falls_back(client, db, registered, mock_http, status):
    _two_models(db)
    handler, seen = _upstream(status)
    mock_http(handler)
    client.post("/api/chats/start", data={"content": "hello", "model_id": "m"})
    reply = _reply(db)
    assert reply.error and reply.model_id == "m" and "second.test" not in seen


def test_with_no_fallback_listed_nothing_changes(client, db, registered, mock_http):
    _two_models(db, fallback=False)
    handler, seen = _upstream(503)
    mock_http(handler)
    client.post("/api/chats/start", data={"content": "hello", "model_id": "m"})
    reply = _reply(db)
    assert reply.error and "second.test" not in seen


def test_eligible_is_the_connection_s_failure_only():
    from lembas.services import fallback

    assert fallback.eligible(LLMError("x"))
    assert fallback.eligible(LLMError("x", status_code=502))
    assert not fallback.eligible(LLMError("x", status_code=429))
    # An error inside a 200 stream is the request's unless the server says 5xx.
    assert not fallback.eligible(StreamError("context too long"))
    assert fallback.eligible(StreamError("model unloaded", status_code=503))


def test_the_model_page_keeps_the_fallback_order(client, db, registered):
    main, backup = _two_models(db, fallback=False)
    third = Model(connection_id=backup.connection_id, model_id="third")
    db.add(third)
    db.commit()
    page = client.get(f"/admin/models/{main.id}/edit").text
    assert 'data-testid="fallback-list"' in page and "Backup" in page
    base = {"enabled": "true", "public": "true", "fallback_submitted": "1"}
    client.post(f"/admin/models/{main.id}",
                data={**base, "fallback_model_ids": [third.id, backup.id, "nope", main.id]})
    db.expire_all()
    assert db.get(Model, main.id).fallback_model_ids == [third.id, backup.id]
    client.post(f"/admin/models/{main.id}", data=base)
    db.expire_all()
    assert db.get(Model, main.id).fallback_model_ids == []
