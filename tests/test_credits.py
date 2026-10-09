"""Credits: prices, the ledger, plans and their windows, the check and
the charge, the admin page and the API. Off by default; a free model is free."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from lembas.db.models import (
    MICRO,
    Connection,
    CreditAccount,
    CreditEntry,
    Model,
    Plan,
    User,
)
from lembas.security import api_tokens
from lembas.services import credits, settings_store
from lembas.services.crypto import encrypt


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
        pricing_json={"input": 2, "cached_input": 1, "output": 10} if priced else {},
    )
    db.add(model)
    db.commit()
    return model


def _on(db, grant: float = 0) -> None:
    settings_store.replace(
        db,
        {"enabled": True, "starting_grant": grant, "default_plan_id": "", "show_balance": True},
        key=settings_store.CREDITS,
    )


def _me(db) -> User:
    return db.scalar(select(User))


def _plain(db) -> User:
    """The registered account as an ordinary person. It registered first, so it
    is the administrator -- and an administrator is never refused or
    deducted, which is not what these tests are about."""
    user = _me(db)
    user.role = "user"
    db.commit()
    return user


# --- Arithmetic ------------------------------------------------------------------
def test_cost_is_per_million_and_rounds_up(db, registered):
    model = _model(db)
    # 1M fresh input at 2 credits, 1M cached at 1, 1M output at 10: 13 credits.
    assert (
        credits.cost(
            model, prompt_tokens=2_000_000, cached_tokens=1_000_000, completion_tokens=1_000_000
        )
        == 13 * MICRO
    )
    # One token of output is 10 micro-credits; a third of one rounds up.
    assert credits.cost(model, completion_tokens=1) == 10
    assert credits.cost(_model(db, priced=False), prompt_tokens=10**9) == 0


def test_show():
    assert credits.show(12_500_000) == "12.5"
    assert credits.show(3_100) == "0.0031"
    assert credits.show(1_204_000_000) == "1,204"


# --- Off by default -------------------------------------------------------------------
def test_off_nothing_is_checked_or_charged(db, registered):
    model = _model(db)
    assert credits.refusal(db, _me(db), model) == ""
    assert credits.charge(db, _me(db).id, model, usage={"prompt_tokens": 100}) == 0
    assert db.scalar(select(CreditEntry)) is None


def test_a_free_model_is_free_with_credits_on(db, registered):
    _on(db)
    model = _model(db, priced=False)
    assert credits.refusal(db, _me(db), model) == ""


# --- The ledger --------------------------------------------------------------------------
def test_the_starting_grant_once(db, registered):
    _on(db, grant=5)
    user = _me(db)
    assert credits.balance(db, user) == 5 * MICRO
    credits.ensure_started(db, user)
    assert credits.balance(db, user) == 5 * MICRO
    assert len(list(db.scalars(select(CreditEntry)))) == 1


def test_no_credits_refuses_and_says_so(db, registered):
    _on(db)
    _plain(db)
    model = _model(db)
    assert "no credits" in credits.refusal(db, _me(db), model)


def test_a_charge_moves_the_balance(db, registered):
    _on(db, grant=1)
    _plain(db)
    model = _model(db)
    user = _me(db)
    charged = credits.charge(db, user.id, model, usage={"completion_tokens": 50_000})
    assert charged == 500_000
    db.expire_all()
    assert db.get(CreditAccount, user.id).balance == MICRO - 500_000


def test_an_idempotency_key_makes_one_entry(db, registered):
    _on(db)
    user = _me(db)
    credits.ensure_started(db, user)
    credits.add(db, user.id, MICRO, "topup", idempotency_key="k1")
    credits.add(db, user.id, MICRO, "topup", idempotency_key="k1")
    db.expire_all()
    assert db.get(CreditAccount, user.id).balance == MICRO


# --- Plans --------------------------------------------------------------------------------
def test_a_reset_plan_expires_what_is_left(db, registered):
    _on(db, grant=3)
    user = _me(db)
    credits.ensure_started(db, user)
    plan = Plan(name="Basic", monthly=10 * MICRO, renewal="reset")
    db.add(plan)
    db.commit()
    credits.assign(db, user, plan.id)
    db.expire_all()
    assert db.get(CreditAccount, user.id).balance == 10 * MICRO


def test_an_add_plan_stops_at_its_cap(db, registered):
    _on(db, grant=8)
    user = _me(db)
    credits.ensure_started(db, user)
    plan = Plan(name="Pro", monthly=10 * MICRO, renewal="add", cap=12 * MICRO)
    db.add(plan)
    db.commit()
    credits.assign(db, user, plan.id)
    db.expire_all()
    assert db.get(CreditAccount, user.id).balance == 12 * MICRO


def test_renewal_when_the_month_is_up(db, registered):
    _on(db)
    user = _me(db)
    credits.ensure_started(db, user)
    plan = Plan(name="Basic", monthly=10 * MICRO, renewal="add")
    db.add(plan)
    db.commit()
    credits.assign(db, user, plan.id)
    user.plan_renews_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert credits.renew_due(db) == 1
    db.expire_all()
    assert db.get(CreditAccount, user.id).balance == 20 * MICRO
    renews = db.get(User, user.id).plan_renews_at
    assert (renews if renews.tzinfo else renews.replace(tzinfo=UTC)) > datetime.now(UTC)


def test_a_full_window_refuses_and_says_when(db, registered):
    _on(db, grant=100)
    _plain(db)
    model = _model(db)
    user = _me(db)
    plan = Plan(name="Capped", monthly=0, renewal="add", limit_5h=MICRO)
    db.add(plan)
    db.commit()
    credits.ensure_started(db, user)
    user.plan_id = plan.id
    db.commit()
    credits.charge(db, user.id, model, usage={"completion_tokens": 200_000})  # 2 credits
    reason = credits.refusal(db, user, model)
    assert "5 hours" in reason and "More are free from" in reason


def test_a_plan_may_name_its_models(db, registered):
    _on(db, grant=100)
    _plain(db)
    model = _model(db)
    user = _me(db)
    plan = Plan(name="Small", allowed_models=["tiny"])
    db.add(plan)
    db.commit()
    credits.ensure_started(db, user)
    user.plan_id = plan.id
    db.commit()
    assert "does not include" in credits.refusal(db, user, model)


# --- In a reply ---------------------------------------------------------------------------
def test_a_reply_is_refused_without_credits(client, db, registered, mock_http):
    _on(db)
    _plain(db)
    _model(db)
    mock_http(lambda _r: httpx.Response(500))
    client.post("/api/chats/start", data={"content": "hello", "model_id": "m"})
    import time

    from lembas.db.models import Message

    for _ in range(100):
        db.expire_all()
        reply = db.scalar(select(Message).where(Message.role == "assistant"))
        if reply is not None and reply.complete:
            break
        time.sleep(0.05)
    assert reply is not None and "no credits" in (reply.error or "")


def test_v1_charges_and_refuses(client, db, registered, mock_http):
    _on(db, grant=1)
    _plain(db)
    settings_store.update(db, {"default_permissions": {"api.use": True}})
    _model(db)
    _, raw = api_tokens.create(db, _me(db), name="t", scopes=["models"])

    def upstream(request):
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 100_000},
            },
        )

    mock_http(upstream)
    headers = {"Authorization": f"Bearer {raw}"}
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    assert client.post("/v1/chat/completions", headers=headers, json=body).status_code == 200
    db.expire_all()
    assert db.get(CreditAccount, _me(db).id).balance == 0
    second = client.post("/v1/chat/completions", headers=headers, json=body)
    assert second.status_code == 429 and "credits" in json.dumps(second.json())
    me = client.get("/api/v1/me/credits", headers=headers).json()
    assert me["balance"] == 0 and me["enabled"] is True


# --- The admin page and the API ------------------------------------------------------------
def test_the_admin_page_tops_up_and_audits(client, db, registered):
    _on(db)
    user = _me(db)
    page = client.get("/admin/credits")
    assert page.status_code == 200 and "People" in page.text
    client.post(f"/admin/credits/users/{user.id}", data={"action": "topup", "amount": "7.5"})
    db.expire_all()
    assert db.get(CreditAccount, user.id).balance == 7_500_000
    from lembas.db.models import AuditEvent

    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "credits.topup")) is not None


def test_an_adjustment_needs_a_note(client, db, registered):
    _on(db)
    user = _me(db)
    r = client.post(
        f"/admin/credits/users/{user.id}",
        data={"action": "adjust", "amount": "-1"},
        follow_redirects=False,
    )
    assert "error=" in r.headers["location"]


def test_the_credits_api_is_idempotent(client, db, registered):
    _on(db)
    user = _me(db)
    _, raw = api_tokens.create(db, user, name="t", scopes=["admin:credits"])
    headers = {"Authorization": f"Bearer {raw}", "Idempotency-Key": "abc"}
    for _ in range(2):
        r = client.post(
            f"/api/v1/admin/credits/{user.id}/topup", headers=headers, json={"amount": 3}
        )
        assert r.status_code == 200
    assert r.json()["balance"] == 3


def test_the_model_page_saves_prices(client, db, registered):
    model = _model(db, priced=False)
    client.post(
        f"/admin/models/{model.id}",
        data={"price_input": "1.5", "price_output": "x", "enabled": "true"},
    )
    db.expire_all()
    assert db.get(Model, model.id).pricing_json == {"input": 1.5, "unit": "money"}


def test_settings_show_the_balance(client, db, registered):
    _on(db, grant=2)
    _plain(db)
    page = client.get("/settings").text
    assert "Credits" in page and "2.00 · €0.02" in page
