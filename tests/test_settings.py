"""Instance settings and changing your own password."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Session as SessionRow
from lembas.db.models import User
from lembas.services import settings_store
from tests.conftest import join


def test_ordinary_users_cannot_change_instance_settings(client: TestClient, db, registered):
    client.post("/auth/logout", follow_redirects=False)
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    assert (
        client.post(
            "/admin/general", data={"system_prompt": "Pwned", "instance_name": "Pwned"}
        ).status_code
        == 403
    )
    assert settings_store.get(db, "system_prompt") == ""
    # The instance's name is set on the same form, and is guarded with it.
    assert settings_store.get(db, "instance_name") == ""


# --- Password change ---------------------------------------------------------
def test_password_change_works(client: TestClient, db, registered):
    response = client.post(
        "/api/preferences/password",
        data={
            "current_password": registered["password"],
            "new_password": "a-much-better-password",
            "confirm_password": "a-much-better-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "saved=" in response.headers["location"]

    client.post("/auth/logout", follow_redirects=False)
    assert (
        client.post(
            "/auth/login",
            data={"email": registered["email"], "password": "a-much-better-password"},
            follow_redirects=False,
        ).status_code
        == 303
    )


def test_old_password_stops_working(client: TestClient, db, registered):
    client.post(
        "/api/preferences/password",
        data={
            "current_password": registered["password"],
            "new_password": "a-much-better-password",
            "confirm_password": "a-much-better-password",
        },
        follow_redirects=False,
    )
    client.post("/auth/logout", follow_redirects=False)
    assert (
        client.post(
            "/auth/login",
            data={"email": registered["email"], "password": registered["password"]},
            follow_redirects=False,
        ).status_code
        == 401
    )


def test_wrong_current_password_is_refused(client: TestClient, db, registered):
    response = client.post(
        "/api/preferences/password",
        data={
            "current_password": "not-my-password",
            "new_password": "a-much-better-password",
            "confirm_password": "a-much-better-password",
        },
        follow_redirects=False,
    )
    assert "error=" in response.headers["location"]

    user = db.scalar(select(User).where(User.email == registered["email"]))
    db.refresh(user)
    from lembas.security.passwords import verify_password

    assert verify_password(registered["password"], user.password_hash)


def test_mismatched_confirmation_is_refused(client: TestClient, db, registered):
    response = client.post(
        "/api/preferences/password",
        data={
            "current_password": registered["password"],
            "new_password": "a-much-better-password",
            "confirm_password": "a-different-password",
        },
        follow_redirects=False,
    )
    assert "do%20not%20match" in response.headers["location"]


def test_short_new_password_is_refused(client: TestClient, db, registered):
    response = client.post(
        "/api/preferences/password",
        data={
            "current_password": registered["password"],
            "new_password": "short",
            "confirm_password": "short",
        },
        follow_redirects=False,
    )
    assert "8%20characters" in response.headers["location"]


def test_other_sessions_are_revoked_but_this_one_survives(client: TestClient, db, registered):
    """If the reason for changing a password is that someone else knows it,
    leaving their session alive defeats the point."""
    other = TestClient(client.app)
    other.post(
        "/auth/login",
        data={"email": registered["email"], "password": registered["password"]},
        follow_redirects=False,
    )
    assert other.get("/chat", follow_redirects=False).status_code == 200
    assert db.scalar(select(SessionRow)) is not None

    client.post(
        "/api/preferences/password",
        data={
            "current_password": registered["password"],
            "new_password": "a-much-better-password",
            "confirm_password": "a-much-better-password",
        },
        follow_redirects=False,
    )

    # The other browser is out...
    assert other.get("/chat", follow_redirects=False).status_code == 303
    # ...and the tab that made the change is still in.
    assert client.get("/chat", follow_redirects=False).status_code == 200


# --- Adding a column to a table that already has rows -------------------------
def test_a_nullable_column_is_added_without_a_default(db):
    """Backfilling one would give existing rows a value the model does not treat
    as absent: an added foreign key arrives as "" rather than NULL, and every
    "is this set?" check downstream is then wrong about the old rows."""
    from sqlalchemy import Column, String

    from lembas.db.migrations import _add_column_sql
    from lembas.db.models import Document

    sql = _add_column_sql(
        Document.__table__, Column("later", String(32), nullable=True), db.bind.dialect
    )
    assert "DEFAULT" not in sql


def test_a_not_null_column_still_gets_one(db):
    """SQLite refuses NOT NULL with no default, so there it is unavoidable."""
    from sqlalchemy import Column, String

    from lembas.db.migrations import _add_column_sql
    from lembas.db.models import Document

    sql = _add_column_sql(
        Document.__table__,
        Column("later", String(32), nullable=False, default=""),
        db.bind.dialect,
    )
    assert "NOT NULL" in sql and "DEFAULT" in sql
