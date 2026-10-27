"""What a reply cost, kept on the message; usage counted per model, never refused.

Credits, plans and the monthly token budget are gone (2.0). A model's price list
is money; a reply's cost is worked out when it is written and stored with the
price applied, so a later price change never rewrites it.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from sqlalchemy import inspect, select

from lembas.db.models import Connection, Message, Model, Usage, User
from lembas.db.session import get_engine
from lembas.security import api_tokens, permissions
from lembas.services import pricing, settings_store
from lembas.services import usage as usage_service
from lembas.services.crypto import encrypt

PRICE = {"input": 2, "cached_input": 1, "output": 10}


@pytest.fixture(autouse=True)
def _rates():
    api_tokens.reset_rates()
    yield


def _model(db, *, priced: bool = True) -> Model:
    connection = Connection(name="c", base_url="http://up.test/v1", api_key_encrypted=encrypt(""))
    db.add(connection)
    db.commit()
    model = Model(
        connection_id=connection.id,
        model_id="m",
        capabilities_json={"tools": True},
        pricing_json=dict(PRICE) if priced else {},
    )
    db.add(model)
    db.commit()
    return model


def _me(db) -> User:
    return db.scalar(select(User))


def _reply(db) -> Message:
    for _ in range(200):
        db.expire_all()
        reply = db.scalar(select(Message).where(Message.role == "assistant"))
        if reply is not None and reply.complete:
            return reply
        time.sleep(0.05)
    raise AssertionError("the reply never finished")


def _upstream(prompt: int, completion: int, cached: int = 0):
    """A server answering "ok", streamed for a chat and whole for `/v1`."""
    usage = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "prompt_tokens_details": {"cached_tokens": cached},
    }

    def answer(request):
        if not request.url.path.endswith("/chat/completions"):
            return httpx.Response(404)
        if json.loads(request.content or b"{}").get("stream"):
            frames = (
                {"choices": [{"delta": {"content": "ok"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": usage},
            )
            text = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        body = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": usage,
        }
        return httpx.Response(200, json=body)

    return answer


# --- Arithmetic ------------------------------------------------------------------------
def test_cost_is_per_million_and_rounds_up():
    # 1M fresh input at 2, 1M cached at 1, 1M output at 10: 13 in money.
    assert (
        pricing.cost_micro(
            PRICE, prompt_tokens=2_000_000, cached_tokens=1_000_000, completion_tokens=1_000_000
        )
        == 13 * pricing.MICRO
    )
    # One token of output at 10 a million is 10 micro-units; never rounded to nothing.
    assert pricing.cost_micro(PRICE, completion_tokens=1) == 10
    assert pricing.cost_micro({"input": 0.3}, prompt_tokens=1) == 1
    assert pricing.cost_micro({}, prompt_tokens=10**9) == 0


def test_reasoning_cache_writes_and_a_per_reply_price():
    price = {"input": 1, "output": 4, "reasoning": 8, "per_request": 0.01}
    cost = pricing.cost_micro(
        price,
        prompt_tokens=1_000_000,
        cache_write_tokens=1_000_000,
        completion_tokens=1_000_000,
        reasoning_tokens=500_000,
    )
    # 1M written to the cache at 1.25x input, half the output at 4 and half at 8, plus a cent.
    assert cost == int((1.25 + 2 + 4 + 0.01) * pricing.MICRO)


def test_money_reads_as_the_language_writes_it():
    assert pricing.format_money(1_230_000, "EUR", "en") == "€1.23"
    assert pricing.format_money(1_230_000, "EUR", "sk") == "1,23 €"
    assert pricing.format_money(3_100, "USD", "en") == "$0.0031"


def test_the_currency_is_the_instance_s_and_validated(db):
    assert settings_store.currency(db) == "USD"
    settings_store.update(db, {"currency": "eur"})
    assert settings_store.currency(db) == "EUR"
    settings_store.update(db, {"currency": "XYZ"})
    assert settings_store.currency(db) == "USD"


# --- Nothing left of credits ---------------------------------------------------------------
def test_no_credit_tables_or_columns(db):
    tables = set(inspect(get_engine()).get_table_names())
    assert not tables & {"plans", "credit_entries", "credit_accounts"}
    columns = {c["name"] for c in inspect(get_engine()).get_columns("users")}
    assert not columns & {"plan_id", "plan_renews_at"}
    assert "monthly_tokens" not in permissions.LIMIT_KEYS
    assert "credits.grant" not in permissions.PERMISSION_KEYS
    assert "admin:credits" not in api_tokens.SCOPES


def test_the_credit_pages_are_gone(client, db, registered):
    assert client.get("/admin/credits").status_code == 404
    assert "/admin/credits" not in client.get("/admin/general").text


# --- A reply ------------------------------------------------------------------------------
def test_a_reply_keeps_its_cost_and_the_price_it_was_given(client, db, registered, mock_http):
    model = _model(db)
    mock_http(_upstream(2_000_000, 1_000_000, cached=1_000_000))
    client.post("/api/chats/start", data={"content": "hello", "model_id": "m"})
    reply = _reply(db)
    assert reply.cost_micro == 13 * pricing.MICRO
    assert reply.currency == "USD"
    assert reply.priced_json["output"] == 10 and reply.priced_json["model_id"] == "m"

    # A new price does not rewrite it.
    model.pricing_json = {"input": 100, "output": 100}
    db.commit()
    db.expire_all()
    assert db.get(Message, reply.id).cost_micro == 13 * pricing.MICRO

    # Counted per user, month and model.
    row = db.get(Usage, (_me(db).id, usage_service.period_of(), "m"))
    assert (row.tokens_in, row.tokens_cached, row.tokens_out, row.requests) == (
        2_000_000, 1_000_000, 1_000_000, 1,
    )
    assert row.cost_micro == 13 * pricing.MICRO

    # And the chat's usage panel says what it cost.
    page = client.get(f"/api/chats/{reply.chat_id}/usage")
    assert "$13.00" in page.text


def test_a_free_model_leaves_the_reply_unpriced(client, db, registered, mock_http):
    _model(db, priced=False)
    mock_http(_upstream(100, 100))
    client.post("/api/chats/start", data={"content": "hello", "model_id": "m"})
    reply = _reply(db)
    assert reply.cost_micro is None and reply.currency is None and reply.priced_json is None
    assert db.get(Usage, (_me(db).id, usage_service.period_of(), "m")).cost_micro == 0


def test_an_ordinary_person_is_never_refused_for_what_they_used(client, db, registered, mock_http):
    """No budget: a month of tokens far past anything is not a reason to stop."""
    me = _me(db)
    me.role = "user"
    db.commit()
    _model(db)
    usage_service.record(db, me.id, model_id="m", prompt_tokens=10**12, cost_micro=10**15)
    db.commit()
    mock_http(_upstream(10, 10))
    client.post("/api/chats/start", data={"content": "hello", "model_id": "m"})
    reply = _reply(db)
    assert not reply.error and reply.content == "ok"


# --- /v1 -------------------------------------------------------------------------------------
def test_v1_counts_and_prices_and_never_refuses(client, db, registered, mock_http):
    me = _me(db)
    me.role = "user"
    db.commit()
    settings_store.update(db, {"default_permissions": {"api.use": True}})
    _model(db)
    _, raw = api_tokens.create(db, me, name="t", scopes=["models"])
    mock_http(_upstream(0, 100_000))
    headers = {"Authorization": f"Bearer {raw}"}
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    for _ in range(3):
        assert client.post("/v1/chat/completions", headers=headers, json=body).status_code == 200
    db.expire_all()
    row = db.get(Usage, (me.id, usage_service.period_of(), "m"))
    assert row.requests == 3 and row.tokens_out == 300_000
    assert row.cost_micro == 3 * pricing.MICRO  # 100k output at 10 a million, three times
    usage = client.get("/v1/usage", headers=headers).json()
    assert usage["month"]["tokens_out"] == 300_000 and usage["month"]["cost_money"] == 3.0
    assert usage["unit"] == "money" and usage["balance"] is None and usage["windows"] == []


# --- The admin's side -------------------------------------------------------------------------
def test_the_model_page_saves_prices_as_plain_money(client, db, registered):
    model = _model(db, priced=False)
    client.post(
        f"/admin/models/{model.id}",
        data={"price_input": "1.5", "price_output": "x", "enabled": "true"},
    )
    db.expire_all()
    assert db.get(Model, model.id).pricing_json == {"input": 1.5}


def test_an_old_unit_mark_is_ignored(db):
    model = Model(model_id="old", pricing_json={"input": 3, "unit": "money"})
    assert pricing.prices(model) == {"input": 3.0}


def test_speech_and_pictures_are_counted_under_their_service(db, registered):
    from lembas.services import audio

    settings_store.update(
        db,
        {"stt_per_minute": 0.006, "stt_model": "whisper-1", "tts_per_1m_chars": 15},
        key=settings_store.AUDIO,
    )
    me = _me(db)
    cost = audio.record_transcription(db, me.id, 120)
    assert cost == 12_000  # two minutes at 0.006
    rows = {r.model_id: r for r in usage_service.by_model(db, me.id)}
    assert any(key.startswith("stt") for key in rows)
    stt = next(r for k, r in rows.items() if k.startswith("stt"))
    assert stt.audio_seconds == 120 and stt.cost_micro == 12_000


def test_the_admin_usage_api_is_per_model(client, db, registered):
    me = _me(db)
    usage_service.record(db, me.id, model_id="a", prompt_tokens=5, cost_micro=1_500_000)
    usage_service.record(db, me.id, model_id="b", completion_tokens=7)
    db.commit()
    _, raw = api_tokens.create(db, me, name="t", scopes=["admin:read"])
    data = client.get("/api/v1/admin/usage", headers={"Authorization": f"Bearer {raw}"}).json()
    by_model = {row["model_id"]: row for row in data["data"]}
    assert by_model["a"]["prompt_tokens"] == 5 and by_model["a"]["cost_money"] == 1.5
    assert by_model["b"]["completion_tokens"] == 7
