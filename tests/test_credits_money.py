"""Credits worth money: prices in money and the one-time conversion,
administrators unlimited but recorded, renewals only from an administrator or the
API, and every usage figure in both units."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

from lembas.db.models import (
    MICRO,
    AuditEvent,
    Chat,
    Connection,
    CreditAccount,
    CreditEntry,
    Group,
    Message,
    Model,
    Plan,
    User,
)
from lembas.db.session import get_engine
from lembas.security import api_tokens
from lembas.services import credits, settings_store
from lembas.services.crypto import encrypt
from lembas.web import i18n


@pytest.fixture(autouse=True)
def _rates():
    api_tokens.reset_rates()
    yield


def _model(db, pricing: dict | None = None, model_id: str = "m") -> Model:
    connection = db.scalar(select(Connection))
    if connection is None:
        connection = Connection(
            name="c", base_url="http://up.test/v1", api_key_encrypted=encrypt("")
        )
        db.add(connection)
        db.commit()
    model = Model(connection_id=connection.id, model_id=model_id, pricing_json=pricing or {})
    db.add(model)
    db.commit()
    return model


def _on(db, grant: float = 0, **extra) -> None:
    settings_store.replace(
        db,
        {"enabled": True, "starting_grant": grant, "default_plan_id": "", "show_balance": True,
         **extra},
        key=settings_store.CREDITS,
    )


def _me(db) -> User:
    return db.scalar(select(User).order_by(User.created_at))


def _plain(db) -> User:
    user = _me(db)
    user.role = "user"
    db.commit()
    return user


# --- Money helpers ------------------------------------------------------------------
def test_money_and_credits_read_as_the_language_writes_them():
    assert credits.format_money(1.234, "EUR", "en") == "€1.23"
    assert credits.format_money(1.234, "EUR", "sk") == "1,23 €"
    assert credits.format_money(1234.5, "USD", "de") == "1.234,50 $"
    # Below a cent, four places rather than a rounded nothing.
    assert credits.format_money(0.0031, "GBP", "en") == "£0.0031"
    assert credits.format_credits(123_450_000, "en") == "123.45"
    assert credits.format_credits(1_204_000_000, "sk") == "1 204,00"
    assert credits.format_credits(3_100, "cs") == "0,0031"


def test_money_becomes_credits_rounding_up():
    assert credits.money_to_micro(0.01, 0.01) == MICRO
    assert credits.money_to_micro(0.0000001, 0.01) == 10
    assert credits.money_to_micro(0.0000000001, 0.01) == 1  # never free by rounding
    assert credits.micro_to_money(2 * MICRO, 0.01) == pytest.approx(0.02)


def test_a_money_price_is_turned_into_credits_at_the_credit_s_value(db, registered):
    model = _model(db, {"input": 0.02, "output": 0.1, "unit": "money"})
    # 0.02 a million at 0.01 a credit: 2 credits a million.
    assert credits.cost(model, prompt_tokens=1_000_000, value=0.01) == 2 * MICRO
    assert credits.cost(model, completion_tokens=1_000_000, value=0.01) == 10 * MICRO
    # A credit worth twice as much: half the credits.
    assert credits.cost(model, prompt_tokens=1_000_000, value=0.02) == MICRO


def test_an_unmarked_price_list_is_credits_whatever_the_value(db, registered):
    model = _model(db, {"input": 2})
    assert credits.price_unit(model) == "credits"
    assert credits.cost(model, prompt_tokens=1_000_000, value=0.5) == 2 * MICRO


def test_changing_the_credit_s_value_changes_what_a_reply_costs(db, registered):
    _on(db, grant=100)
    user = _plain(db)
    model = _model(db, {"output": 1.0, "unit": "money"})
    first = credits.charge(db, user.id, model, usage={"completion_tokens": 1_000_000})
    settings_store.update(db, {"credit_value": 0.1}, key=settings_store.CREDITS)
    second = credits.charge(db, user.id, model, usage={"completion_tokens": 1_000_000})
    assert (first, second) == (100 * MICRO, 10 * MICRO)


def test_the_credit_settings_are_clamped_on_read(db, registered):
    settings_store.update(db, {"currency": "XYZ", "credit_value": -3}, key=settings_store.CREDITS)
    values = settings_store.credits(db)
    assert values["currency"] == "EUR" and values["credit_value"] == 0.01


def test_the_admin_page_sets_the_currency_and_the_value(client, db, registered):
    client.post(
        "/admin/credits/settings",
        data={"enabled": "true", "starting_grant": "0", "currency": "GBP", "credit_value": "0.05"},
    )
    values = settings_store.credits(db)
    assert values["currency"] == "GBP" and values["credit_value"] == 0.05
    # Junk keeps what was there rather than failing the form.
    client.post(
        "/admin/credits/settings",
        data={"enabled": "true", "starting_grant": "0", "currency": "??", "credit_value": "x"},
    )
    db.expire_all()
    values = settings_store.credits(db)
    assert values["currency"] == "GBP" and values["credit_value"] == 0.05
    page = client.get("/admin/credits").text
    assert 'name="credit_value"' in page and 'value="GBP" selected' in page
    summary = db.scalars(select(AuditEvent.summary).where(AuditEvent.action == "credits.settings"))
    assert any("0.05 GBP" in line for line in summary)


def test_the_model_page_shows_old_credit_prices_as_money(client, db, registered):
    _on(db)
    model = _model(db, {"input": 3})
    page = client.get(f"/admin/models/{model.id}/edit").text
    assert 'value="0.03"' in page and "(€)" in page
    assert 'name="kind"' in page


# --- The conversion -----------------------------------------------------------------
def _raw_pricing(model_id: str) -> dict:
    with get_engine().begin() as connection:
        raw = connection.execute(
            text("SELECT pricing_json FROM models WHERE id = :i"), {"i": model_id}
        ).scalar()
    return json.loads(raw) if isinstance(raw, str) else raw


def _set_raw_pricing(model_id: str, pricing: dict) -> None:
    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE models SET pricing_json = :p WHERE id = :i"),
            {"p": json.dumps(pricing), "i": model_id},
        )


def test_the_conversion_scales_once_by_the_value_at_the_time(db, registered):
    from lembas.db.migrations import prices_to_money

    settings_store.update(db, {"credit_value": 0.02}, key=settings_store.CREDITS)
    priced = _model(db)
    free = _model(db, model_id="free")
    _set_raw_pricing(priced.id, {"input": 2, "output": 10, "per_request": 0.5})
    _set_raw_pricing(free.id, {})

    assert prices_to_money(get_engine())
    converted = _raw_pricing(priced.id)
    assert converted == {"input": 0.04, "output": 0.2, "per_request": 0.01, "unit": "money"}
    assert _raw_pricing(free.id) == {}
    # Twice is once: the mark stops it.
    assert prices_to_money(get_engine()) == []
    assert _raw_pricing(priced.id) == converted
    # And the model costs what it did in credits.
    db.expire_all()
    model = db.get(Model, priced.id)
    # 2 credits for the million, and half a credit for the reply.
    assert credits.cost(model, prompt_tokens=1_000_000, value=0.02) == 2_500_000


def test_the_conversion_defaults_to_a_cent_a_credit(db, registered):
    from lembas.db.migrations import prices_to_money

    model = _model(db)
    _set_raw_pricing(model.id, {"input": 5})
    prices_to_money(get_engine())
    assert _raw_pricing(model.id) == {"input": 0.05, "unit": "money"}


def test_old_usage_entries_learn_their_cost(db, registered):
    from lembas.db.migrations import backfill_entry_costs

    user = _me(db)
    credits.add(db, user.id, -7 * MICRO, "usage")
    credits.add(db, user.id, 5 * MICRO, "topup")
    with get_engine().begin() as connection:
        connection.execute(text("UPDATE credit_entries SET cost_micro = 0"))
    assert backfill_entry_costs(get_engine())
    assert backfill_entry_costs(get_engine()) == []
    db.expire_all()
    costs = {e.kind: e.cost_micro for e in db.scalars(select(CreditEntry))}
    assert costs == {"usage": 7 * MICRO, "topup": 0}


# --- Administrators: unlimited, and still recorded ------------------------------------
def test_an_administrator_is_never_refused(db, registered):
    _on(db)
    admin = _me(db)
    model = _model(db, {"input": 1, "unit": "money"})
    plan = Plan(name="Tiny", allowed_models=["other"], limit_5h=1)
    db.add(plan)
    db.commit()
    admin.plan_id = plan.id
    db.commit()
    assert credits.balance(db, admin) == 0
    assert credits.refusal(db, admin, model) == ""
    assert credits.service_refusal(db, admin, 1.0) == ""


def test_an_administrator_s_use_is_recorded_and_not_deducted(db, registered):
    _on(db, grant=1)
    admin = _me(db)
    model = _model(db, {"output": 0.1, "unit": "money"})
    cost = credits.charge(db, admin.id, model, usage={"completion_tokens": 1_000_000})
    assert cost == 10 * MICRO
    db.expire_all()
    entry = db.scalar(select(CreditEntry).where(CreditEntry.kind == "usage"))
    assert entry.delta == 0 and entry.cost_micro == 10 * MICRO
    assert db.get(CreditAccount, admin.id).balance == MICRO
    assert credits.spent_since(db, admin.id, credits.month_start()) == 10 * MICRO


def test_everybody_else_s_cost_is_what_was_taken(db, registered):
    _on(db, grant=50)
    user = _plain(db)
    model = _model(db, {"output": 0.1, "unit": "money"})
    credits.charge(db, user.id, model, usage={"completion_tokens": 1_000_000})
    entry = db.scalar(select(CreditEntry).where(CreditEntry.kind == "usage"))
    assert entry.delta == -10 * MICRO and entry.cost_micro == 10 * MICRO


def test_the_group_limits_and_the_image_budget_skip_an_administrator(db, registered):
    from lembas.security import permissions
    from lembas.services import usage as usage_service

    admin = _me(db)
    group = Group(name="Few pictures", limits_json={"images_per_day": 1, "monthly_tokens": 1})
    db.add(group)
    admin.groups.append(group)
    db.commit()
    usage_service.record(db, admin.id, prompt_tokens=10)
    db.commit()
    assert permissions.limit(db, admin, "images_per_day") == 0
    assert usage_service.over_image_budget(db, admin) == ""
    assert usage_service.over_token_budget(db, admin) == ""


# --- Renewals: an administrator's, or the API's ---------------------------------------
def _ordinary_person(db) -> User:
    person = User(email="sam@shire.test", name="Sam", password_hash="x", role="user")
    db.add(person)
    db.commit()
    return person


def test_nobody_without_the_grant_moves_credits_or_plans(client, db, registered):
    """Every route that adds credits, renews or chooses a plan, as a person who
    is neither an administrator nor holds *Grant credits*."""
    _on(db)
    plan = Plan(name="Basic", monthly=MICRO)
    db.add(plan)
    db.commit()
    me = _plain(db)
    settings_store.update(db, {"default_permissions": {"api.use": True}})
    for path, data in (
        ("/admin/credits/settings", {"enabled": "true"}),
        ("/admin/credits/plans", {"name": "Mine", "monthly": "1000"}),
        (f"/admin/credits/plans/{plan.id}/delete", {}),
        (f"/admin/credits/users/{me.id}", {"action": "topup", "amount": "5"}),
        (f"/admin/credits/users/{me.id}", {"action": "adjust", "amount": "5", "note": "x"}),
        (f"/admin/credits/users/{me.id}", {"action": "plan", "plan_id": plan.id}),
        (f"/admin/credits/users/{me.id}", {"action": "renew"}),
        (f"/admin/credits/users/{me.id}", {"action": "renews_at", "renews_at": "2030-01-01"}),
    ):
        assert client.post(path, data=data, follow_redirects=False).status_code == 403, path
    assert client.get("/admin/credits").status_code == 403

    # A token cannot be given the scope, and one made before the person lost the
    # right is refused on every call.
    assert "admin:credits" not in api_tokens.grantable(db, me)
    token, raw = api_tokens.create(db, me, name="old", scopes=["models"])
    token.scopes_json = ["models", "admin:credits"]
    db.commit()
    auth = {"Authorization": f"Bearer {raw}"}
    for method, path, body in (
        ("post", f"/api/v1/admin/credits/{me.id}/topup", {"amount": 5}),
        ("post", f"/api/v1/admin/credits/{me.id}/adjust", {"amount": 5, "note": "x"}),
        ("put", f"/api/v1/admin/credits/{me.id}/plan", {"plan_id": plan.id}),
        ("post", f"/api/v1/admin/credits/{me.id}/renew", {}),
        ("post", "/api/v1/admin/plans", {"name": "Mine"}),
        ("put", f"/api/v1/admin/plans/{plan.id}", {"monthly": 99}),
        ("delete", f"/api/v1/admin/plans/{plan.id}", None),
    ):
        kwargs = {"headers": auth}
        if body is not None:
            kwargs["json"] = body
        assert getattr(client, method)(path, **kwargs).status_code == 403, path
    db.expire_all()
    assert db.get(CreditAccount, me.id) is None or db.get(CreditAccount, me.id).balance == 0
    assert db.get(User, me.id).plan_id is None


def _admin_token(db) -> dict[str, str]:
    _, raw = api_tokens.create(db, _me(db), name="ops", scopes=["admin:credits"])
    return {"Authorization": f"Bearer {raw}"}


def test_renewing_through_the_api_is_once_a_period_unless_forced(client, db, registered):
    _on(db)
    auth = _admin_token(db)
    person = _ordinary_person(db)
    plan = Plan(name="Add", monthly=10 * MICRO, renewal="add")
    db.add(plan)
    db.commit()
    assigned = client.put(
        f"/api/v1/admin/credits/{person.id}/plan", headers=auth,
        json={"plan_id": plan.id, "renews_at": "2031-02-03"},
    ).json()
    assert assigned["balance"] == 10 and assigned["renews_at"].startswith("2031-02-03")

    # Assigned this month: renewing now is refused, and nothing moves.
    again = client.post(f"/api/v1/admin/credits/{person.id}/renew", headers=auth).json()
    assert again["renewed"] is False and again["balance"] == 10
    # Forced, with a key: once, however often it is sent.
    keyed = {**auth, "Idempotency-Key": "r1"}
    for _ in range(2):
        forced = client.post(
            f"/api/v1/admin/credits/{person.id}/renew", headers=keyed, json={"force": True}
        ).json()
    assert forced["balance"] == 20
    renews = datetime.fromisoformat(forced["renews_at"])
    assert renews > datetime.now(UTC) + timedelta(days=25)
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "credits.renew")) is not None


def test_renewing_a_month_after_the_last_needs_no_force(db, registered):
    _on(db)
    person = _ordinary_person(db)
    plan = Plan(name="Add", monthly=MICRO, renewal="add")
    db.add(plan)
    db.commit()
    credits.assign(db, person, plan.id)
    # The month the assignment gave is over: its entries are forty days old.
    for entry in db.scalars(select(CreditEntry).where(CreditEntry.kind == "renewal")):
        entry.at = datetime.now(UTC) - timedelta(days=40)
    db.commit()
    assert credits.renew_now(db, person) is True
    assert credits.renew_now(db, person) is False


def test_plans_are_made_changed_and_deleted_by_an_administrator(client, db, registered):
    _on(db)
    auth = _admin_token(db)
    made = client.post(
        "/api/v1/admin/plans", headers=auth,
        json={"name": "Team", "monthly": 500, "renewal": "add", "cap": 800, "limit_day": 50,
              "allowed_models": ["llama/bonsai"]},
    )
    assert made.status_code == 201
    plan_id = made.json()["id"]
    plan = db.get(Plan, plan_id)
    assert plan.monthly == 500 * MICRO and plan.cap == 800 * MICRO and plan.renewal == "add"

    changed = client.put(f"/api/v1/admin/plans/{plan_id}", headers=auth, json={"monthly": 600})
    assert changed.json()["monthly"] == 600 and changed.json()["name"] == "Team"
    assert client.put(
        f"/api/v1/admin/plans/{plan_id}", headers=auth, json={"renewal": "never"}
    ).status_code == 400

    person = _ordinary_person(db)
    person.plan_id = plan_id
    db.commit()
    gone = client.delete(f"/api/v1/admin/plans/{plan_id}", headers=auth)
    assert gone.json() == {"id": plan_id, "deleted": True, "people": 1}
    db.expire_all()
    assert db.get(Plan, plan_id) is None and db.get(User, person.id).plan_id is None
    actions = set(db.scalars(select(AuditEvent.action)))
    assert {"credits.plan", "credits.plan_delete"} <= actions


def test_a_grant_holder_chooses_plans_but_does_not_define_them(client, db, registered):
    _on(db)
    plan = Plan(name="Basic", monthly=MICRO)
    db.add(plan)
    db.commit()
    lead = _plain(db)
    settings_store.update(db, {"default_permissions": {"credits.grant": True}})
    _, raw = api_tokens.create(db, lead, name="lead", scopes=["admin:credits"])
    auth = {"Authorization": f"Bearer {raw}"}
    person = _ordinary_person(db)
    assert client.put(
        f"/api/v1/admin/credits/{person.id}/plan", headers=auth, json={"plan_id": plan.id}
    ).status_code == 200
    assert client.get("/api/v1/admin/plans", headers=auth).status_code == 200
    assert client.post("/api/v1/admin/plans", headers=auth, json={"name": "x"}).status_code == 403
    assert client.delete(f"/api/v1/admin/plans/{plan.id}", headers=auth).status_code == 403


def test_the_page_renews_now_and_sets_the_date(client, db, registered):
    _on(db)
    person = _ordinary_person(db)
    plan = Plan(name="Reset", monthly=3 * MICRO)
    db.add(plan)
    db.commit()
    credits.assign(db, person, plan.id)
    page = client.get("/admin/credits").text
    assert "Renew now" in page and 'name="renews_at"' in page

    refused = client.post(
        f"/admin/credits/users/{person.id}", data={"action": "renew"}, follow_redirects=False
    )
    assert "error=" in refused.headers["location"]
    client.post(
        f"/admin/credits/users/{person.id}",
        data={"action": "renews_at", "renews_at": "2032-05-06"},
    )
    db.expire_all()
    renews = db.get(User, person.id).plan_renews_at
    assert renews.strftime("%Y-%m-%d") == "2032-05-06"
    actions = set(db.scalars(select(AuditEvent.action)))
    assert "credits.renews_at" in actions


# --- Both units, everywhere usage is shown ----------------------------------------------
def test_v1_usage_says_it_in_money_too(client, db, registered):
    _on(db, credit_value=0.05, currency="USD")
    settings_store.update(db, {"default_permissions": {"api.use": True}})
    user = _plain(db)
    model = _model(db, {"input": 0.1, "unit": "money"})  # 2 credits a million
    plan = Plan(name="Basic", monthly=10 * MICRO, renewal="reset", limit_day=5 * MICRO)
    db.add(plan)
    db.commit()
    credits.assign(db, user, plan.id)
    token, raw = api_tokens.create(db, user, name="cli", scopes=["models"])
    credits.charge(db, user.id, model, usage={"prompt_tokens": 1_000_000}, token_id=token.id)
    body = client.get("/v1/usage", headers={"Authorization": f"Bearer {raw}"}).json()
    assert body["currency"] == "USD" and body["credit_value"] == 0.05 and body["admin"] is False
    assert body["balance"] == 8.0 and body["balance_money"] == pytest.approx(0.4)
    assert body["month"]["cost"] == 2.0 and body["month"]["cost_money"] == pytest.approx(0.1)
    assert body["device"]["cost"] == 2.0 and body["device"]["cost_money"] == pytest.approx(0.1)
    assert body["windows"] == [
        {"window": "limit_day", "limit": 5.0, "spent": 2.0, "frees_at": None}
    ]
    assert body["unit"] == "credits" and body["plan"]["credits_per_month"] == 10.0


def test_v1_usage_for_an_administrator_counts_what_was_not_taken(client, db, registered):
    _on(db, grant=1)
    admin = _me(db)
    model = _model(db, {"input": 0.01, "unit": "money"})
    token, raw = api_tokens.create(db, admin, name="cli", scopes=["models"])
    credits.charge(db, admin.id, model, usage={"prompt_tokens": 1_000_000}, token_id=token.id)
    body = client.get("/v1/usage", headers={"Authorization": f"Bearer {raw}"}).json()
    assert body["admin"] is True and body["balance"] == 1.0
    assert body["month"]["cost"] == 1.0 and body["windows"] == []


def test_the_chip_is_unlimited_for_an_administrator_and_respects_the_switch(
    client, db, registered
):
    _on(db, grant=5)
    assert "Unlimited" in client.get("/chat").text
    settings_store.update(db, {"show_balance": False}, key=settings_store.CREDITS)
    assert "Unlimited" not in client.get("/chat").text


def test_the_chip_in_slovak_writes_numbers_the_slovak_way(client, db, registered):
    _on(db, grant=1234.5)
    user = _plain(db)
    user.settings_json = {**(user.settings_json or {}), "language": "sk"}
    db.commit()
    i18n.forget()
    page = client.get("/chat").text
    assert "1 234,50 kr. · 12,35 €" in page


def test_the_settings_card_shows_spend_per_model_and_the_cost_column(client, db, registered):
    _on(db, grant=10)
    user = _plain(db)
    model = _model(db, {"output": 0.01, "unit": "money"}, model_id="pricey")
    credits.charge(db, user.id, model, usage={"completion_tokens": 2_000_000})
    page = client.get("/settings").text
    assert "Model or service" in page and "pricey" in page
    assert "8.00 · €0.08" in page  # the balance
    assert "2.00 · €0.02" in page  # this month


def test_a_chat_s_usage_says_what_it_cost(client, db, registered):
    _on(db, grant=10)
    admin = _me(db)
    model = _model(db, {"output": 0.01, "unit": "money"})
    chat = Chat(user_id=admin.id, model_id="m", connection_id=model.connection_id)
    db.add(chat)
    db.commit()
    reply = Message(chat_id=chat.id, role="assistant", content="hi")
    db.add(reply)
    db.commit()
    credits.charge(db, admin.id, model, usage={"completion_tokens": 3_000_000},
                   message_id=reply.id)
    page = client.get(f"/api/chats/{chat.id}/usage").text
    assert "3.00 credits · €0.03" in page
    settings_store.update(db, {"enabled": False}, key=settings_store.CREDITS)
    assert "credits ·" not in client.get(f"/api/chats/{chat.id}/usage").text
