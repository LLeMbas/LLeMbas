"""The security pass and the audit log.

Sign-in throttling, the `Secure` cookie behind HTTPS, the Origin check on
cookie-carrying writes, the headers, and the audit log's recording, filtering,
export and retention.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from lembas.db.models import AuditEvent, User
from lembas.security import hardening
from lembas.services import audit, settings_store

PASSWORD = "speak-friend-and-enter"


@pytest.fixture(autouse=True)
def _fresh():
    hardening.reset_throttles()
    yield
    hardening.reset_throttles()


def _actions(db) -> list[str]:
    db.expire_all()
    return [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.at))]


# --- Sign-in ----------------------------------------------------------------------
def test_failed_sign_ins_are_throttled_then_forgiven(client, db, registered):
    client.post("/auth/logout")
    for _ in range(hardening.FREE_FAILURES):
        r = client.post("/auth/login", data={"email": "frodo@shire.test", "password": "nope"})
        assert r.status_code == 401
    r = client.post("/auth/login", data={"email": "frodo@shire.test", "password": "nope"})
    assert r.status_code == 401  # the sixth failure is counted, and sets the wait
    r = client.post("/auth/login", data={"email": "frodo@shire.test", "password": PASSWORD})
    assert r.status_code == 429 and "Try again in" in r.text
    assert "auth.login_throttled" in _actions(db)

    hardening.reset_throttles()
    r = client.post(
        "/auth/login", data={"email": "frodo@shire.test", "password": PASSWORD},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_sign_in_and_out_are_recorded(client, db, registered):
    client.post("/auth/logout")
    client.post("/auth/login", data={"email": "frodo@shire.test", "password": PASSWORD})
    actions = _actions(db)
    assert {"auth.register", "auth.logout", "auth.login"} <= set(actions)


def test_the_cookie_is_secure_behind_https(client, db, registered):
    client.post("/auth/logout")
    r = client.post(
        "/auth/login",
        data={"email": "frodo@shire.test", "password": PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert "secure" in r.headers["set-cookie"].lower()
    client.post("/auth/logout")
    r = client.post(
        "/auth/login",
        data={"email": "frodo@shire.test", "password": PASSWORD},
        follow_redirects=False,
    )
    assert "secure" not in r.headers["set-cookie"].lower()


# --- The Origin check -----------------------------------------------------------------
def test_a_write_from_another_site_is_refused(client, db, registered):
    r = client.post(
        "/api/preferences/timezone", data={"timezone": "UTC"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


def test_a_write_from_this_site_passes(client, db, registered):
    r = client.post(
        "/api/preferences/timezone", data={"timezone": "UTC"},
        headers={"Origin": "http://testserver"}, follow_redirects=False,
    )
    assert r.status_code != 403


def test_behind_a_proxy_the_forwarded_host_counts(client, db, registered):
    r = client.post(
        "/api/preferences/timezone", data={"timezone": "UTC"},
        headers={"Origin": "https://example.test", "X-Forwarded-Host": "example.test"},
        follow_redirects=False,
    )
    assert r.status_code != 403


def test_without_the_cookie_there_is_nothing_to_check(client, db, registered):
    client.cookies.clear()
    r = client.post(
        "/api/device/code", json={"device_name": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 200


# --- Headers ---------------------------------------------------------------------------
def test_pages_carry_the_policy_and_the_headers(client, db, registered):
    r = client.get("/settings")
    assert r.headers["x-content-type-options"] == "nosniff"
    policy = r.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in policy and "frame-src 'self'" in policy
    assert "default-src 'self'" in policy


# The prototype route keeping its own sealed policy is asserted where the route
# is: tests/test_prototypes.py::test_the_route_serves_the_page_sealed reads the
# policy off the response, which passes through the middleware.


# --- The audit log ----------------------------------------------------------------------
def test_an_admin_change_is_recorded_without_its_values(client, db, registered):
    client.post("/admin/general", data={"system_prompt": "secret words"})
    db.expire_all()
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "admin.change"))
    assert event is not None and event.summary == "POST /admin/general"
    assert "secret" not in str(event.detail_json) and "secret" not in event.summary


def test_the_audit_page_and_its_export(client, db, registered):
    client.post("/admin/general", data={})
    page = client.get("/admin/audit?action=admin")
    assert page.status_code == 200 and "POST /admin/general" in page.text
    csv_text = client.get("/admin/audit.csv").text
    assert csv_text.splitlines()[0].startswith("at,action,")


def test_the_audit_page_needs_the_permission(client, db, registered):
    user = db.scalar(select(User))
    user.role = "user"
    db.commit()
    assert client.get("/admin/audit").status_code == 403


def test_a_formula_cannot_ride_the_export(db):
    audit.record("test.x", summary="=HYPERLINK(\"http://evil\")", actor_label="+1")
    text = audit.to_csv(audit.query(db))
    assert "'=HYPERLINK" in text and "'+1" in text


def test_retention(db):
    audit.record("old.thing")
    db.query(AuditEvent).update({"at": datetime.now(UTC) - timedelta(days=400)})
    db.commit()
    audit.record("new.thing")
    assert audit.sweep(db) == 1
    settings_store.update(db, {"audit_retention_days": 0})
    db.query(AuditEvent).update({"at": datetime.now(UTC) - timedelta(days=4000)})
    db.commit()
    assert audit.sweep(db) == 0


def test_starting_over_is_recorded(client, db, registered):
    client.post("/api/preferences/reset/memory")
    assert "reset.memory" in _actions(db)


def test_the_admin_token_page(client, db, registered):
    """An account's tokens on its own page (Admin → Users → {user}),
    revoked from there and audited as an administrator's act."""
    from lembas.security import api_tokens

    user = db.scalar(select(User))
    token, _ = api_tokens.create(db, user, name="script", scopes=["models"])
    page = client.get(f"/admin/users/{user.id}")
    assert page.status_code == 200 and token.prefix in page.text
    token_id, model = token.id, type(token)
    client.post(f"/admin/tokens/{token_id}/revoke")
    db.expire_all()
    # Revoking deletes the row.
    assert db.get(model, token_id) is None
    assert "admin.token.revoke" in _actions(db)
