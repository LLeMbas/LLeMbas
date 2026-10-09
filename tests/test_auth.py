"""Registration, sign-in, and the route guards."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import ROLE_ADMIN, ROLE_USER, User


def test_empty_instance_sends_you_to_registration(client: TestClient):
    response = client.get("/auth/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/register"


def test_first_account_becomes_admin(client: TestClient, db, registered):
    user = db.scalar(select(User).where(User.email == registered["email"]))
    assert user.role == ROLE_ADMIN


def test_second_account_is_an_ordinary_user(client: TestClient, db, registered):
    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    user = db.scalar(select(User).where(User.email == "sam@shire.test"))
    assert user.role == ROLE_USER


def test_registration_signs_you_in(client: TestClient, registered):
    assert client.get("/chat").status_code == 200


def test_duplicate_email_is_rejected(client: TestClient, registered):
    client.post("/auth/logout", follow_redirects=False)
    response = client.post("/auth/register", data=registered, follow_redirects=False)
    assert response.status_code == 400
    assert "already exists" in response.text


def test_short_password_is_rejected(client: TestClient):
    response = client.post(
        "/auth/register",
        data={"name": "A", "email": "a@b.test", "password": "short"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "8 characters" in response.text


def test_sign_in_and_out(client: TestClient, registered):
    client.post("/auth/logout", follow_redirects=False)
    assert client.get("/chat", follow_redirects=False).status_code == 303

    response = client.post(
        "/auth/login",
        data={"email": registered["email"], "password": registered["password"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/chat").status_code == 200


def test_wrong_password_does_not_reveal_whether_the_account_exists(
    client: TestClient, registered
):
    client.post("/auth/logout", follow_redirects=False)
    wrong = client.post(
        "/auth/login",
        data={"email": registered["email"], "password": "wrong-password-here"},
        follow_redirects=False,
    )
    missing = client.post(
        "/auth/login",
        data={"email": "nobody@nowhere.test", "password": "wrong-password-here"},
        follow_redirects=False,
    )
    assert wrong.status_code == missing.status_code == 401
    assert "do not match" in wrong.text
    # Identical wording is the whole point: the form must not be usable to
    # enumerate which addresses are registered.
    assert ("do not match" in missing.text) == ("do not match" in wrong.text)


def test_logout_revokes_the_session_immediately(client: TestClient, registered):
    client.post("/auth/logout", follow_redirects=False)
    assert client.get("/chat", follow_redirects=False).status_code == 303


def test_signed_out_pages_redirect_to_login(client: TestClient, registered):
    client.post("/auth/logout", follow_redirects=False)
    for path in ("/", "/chat", "/settings", "/admin/connections"):
        assert client.get(path, follow_redirects=False).status_code == 303, path


def test_htmx_requests_get_a_redirect_header_not_a_login_page(
    client: TestClient, registered
):
    """An htmx request must never swap a login form into a fragment of the UI."""
    client.post("/auth/logout", follow_redirects=False)
    response = client.post(
        "/api/chats/start", data={"content": "hi"}, headers={"HX-Request": "true"}
    )
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/auth/login"


def test_admin_area_is_closed_to_ordinary_users(client: TestClient, registered):
    client.post("/auth/logout", follow_redirects=False)
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    assert client.get("/admin/connections", follow_redirects=False).status_code == 403


def test_login_next_parameter_cannot_be_used_for_an_open_redirect(
    client: TestClient, registered
):
    client.post("/auth/logout", follow_redirects=False)
    response = client.post(
        "/auth/login",
        data={
            "email": registered["email"],
            "password": registered["password"],
            "next": "https://evil.example.com/steal",
        },
        follow_redirects=False,
    )
    assert response.headers["location"] == "/"
