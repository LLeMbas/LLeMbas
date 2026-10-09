"""The Scheduled section: making one, changing it, and the strip on its chat.

The failures worth pinning here are the ones that look like working software:
a control wired to a method its route does not serve, a form that quietly
creates something which can never fire, and a task chat that still has a way to
send a message into it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import KIND_TASK, TARGET_REPORT, Chat, Schedule, User
from lembas.security import permissions
from lembas.services import schedules as schedules_service
from lembas.services import settings_store
from lembas.services.schedule import clock


@pytest.fixture(autouse=True)
def scheduling_allowed(db, registered):
    """`schedule.use` is off by default, deliberately. Granted here so the tests
    are about the feature rather than about the gate — which has its own test.

    A model is configured too, and that is not scaffolding: with none, the chat
    page renders its "no models yet" branch instead of the conversation, and
    every assertion about what the composer area does or does not contain passes
    for the wrong reason. This file caught exactly that.
    """
    from lembas.db.models import Connection, Model
    from lembas.services.crypto import encrypt

    settings_store.update(db, {"enabled": True}, key=settings_store.SCHEDULES)
    settings_store.update(
        db, {"default_permissions": {"schedule.use": True, "reports.use": True}}
    )
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()
    return None


def _user(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _make(client: TestClient, **overrides) -> None:
    data = {
        "title": "Monday build check",
        "instruction": "Check the build and say what broke.",
        "target": "chat",
        "repeat": "calendar",
        "weekdays": "0",
        "times": "15:00",
        "count": "0",
        **overrides,
    }
    return client.post("/api/schedules", data=data, follow_redirects=False)


# --- Creating -------------------------------------------------------------------
def test_creating_a_schedule_makes_its_chat_too(client: TestClient, db, registered):
    """The one place "chats are created lazily" is bent, and on purpose: the
    first firing may be days away with nobody present to make one."""
    response = _make(client)
    assert response.status_code == 303

    schedule = db.scalars(select(Schedule)).one()
    chat = db.get(Chat, schedule.chat_id)
    assert chat is not None
    assert chat.kind == KIND_TASK
    assert response.headers["location"] == f"/chat/{chat.id}"


def test_a_new_schedule_has_a_next_run(client: TestClient, db, registered):
    """A schedule that can never fire looks exactly like a working one on the
    list page. This is the invariant that stops one being written at all."""
    _make(client)
    schedule = db.scalars(select(Schedule)).one()

    assert schedule.next_fire_at is not None
    assert clock.as_utc(schedule.next_fire_at) > datetime.now(tz=UTC)
    assert schedule.enabled is True


def test_a_rule_that_means_nothing_is_refused_with_a_reason(
    client: TestClient, db, registered
):
    """Not a 400 nobody can act on: back to the form, with the reason. A silent
    refusal here would be a "Schedule it" button that appears to do nothing."""
    response = _make(client, repeat="once", start_date="", start_time="")

    assert response.status_code == 303
    assert "/scheduled/new?error=" in response.headers["location"]
    assert db.scalars(select(Schedule)).all() == []


def test_a_one_shot_in_the_past_is_refused(client: TestClient, db, registered):
    response = _make(client, repeat="once", start_date="2020-01-01", start_time="09:00")

    assert "error=" in response.headers["location"]
    assert db.scalars(select(Schedule)).all() == []


def test_the_form_and_the_engine_agree_about_what_was_stored(
    client: TestClient, db, registered
):
    """The edit screen is derived from the *normalised* rule, so a form showing
    something other than what runs is impossible rather than merely unlikely."""
    _make(client, repeat="every", every_amount="6", every_unit="hours")
    schedule = db.scalars(select(Schedule)).one()

    page = client.get(f"/scheduled/{schedule.id}/edit").text
    assert 'value="every"\n           checked' in page or 'value="every" checked' in page
    assert 'value="6"' in page


def test_a_per_user_ceiling_is_enforced(client: TestClient, db, registered):
    settings_store.update(db, {"max_per_user": 1}, key=settings_store.SCHEDULES)
    _make(client)

    response = _make(client, title="A second one")

    assert "error=" in response.headers["location"]
    assert len(db.scalars(select(Schedule)).all()) == 1


# --- The gate --------------------------------------------------------------------
def test_scheduling_is_off_unless_granted(client: TestClient, db, registered):
    """`schedule.use` defaults to False: this spends model time with nobody at
    the keyboard, which is a capability chosen on purpose."""
    assert permissions.DEFAULT_PERMISSIONS["schedule.use"] is False


def _as_stranger(client: TestClient, db, *, role: str = "user") -> User:
    """Sign in as somebody who is not the administrator.

    Necessary for any test about a permission: `permissions.resolve` gives an
    admin everything, so asking Frodo whether a gate works answers a different
    question and answers it yes.
    """
    from lembas.security.passwords import hash_password

    stranger = User(
        email="sam@shire.test",
        name="Sam",
        password_hash=hash_password("gardening-is-hard"),
        role=role,
    )
    db.add(stranger)
    db.commit()
    client.post("/auth/logout")
    client.post(
        "/auth/login", data={"email": "sam@shire.test", "password": "gardening-is-hard"}
    )
    return stranger


def test_the_settings_tab_follows_the_permission(client: TestClient, db, registered):
    """Scheduled is a tab of Settings, not a sidebar section."""
    _as_stranger(client, db)
    assert 'href="/scheduled"' not in client.get("/chat").text
    page = client.get("/settings?tab=scheduled").text
    assert 'id="tab-scheduled"' in page
    assert re.search(r'id="tab-scheduled"\s+checked', page)
    assert 'href="/scheduled/new"' in page

    settings_store.update(db, {"default_permissions": {"schedule.use": False}})
    page = client.get("/settings?tab=scheduled").text
    assert 'id="tab-scheduled"' not in page
    # A link to a tab this account has not got lands on Account, not on nothing.
    assert re.search(r'id="tab-account"\s+checked', page)


def test_the_old_list_address_lands_on_the_tab(client: TestClient, db, registered):
    response = client.get("/scheduled", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=scheduled"


def test_the_tab_lists_the_schedules(client: TestClient, db, registered):
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    db.add(
        Schedule(user_id=owner.id, title="Morning digest", rule_json={"at": {"times": ["09:00"]}})
    )
    db.commit()
    page = client.get("/settings?tab=scheduled").text
    assert "Morning digest" in page


def test_a_non_admin_without_the_permission_cannot_reach_it(
    client: TestClient, db, registered
):
    settings_store.update(db, {"default_permissions": {"schedule.use": False}})
    _as_stranger(client, db)

    assert client.get("/scheduled", follow_redirects=False).status_code in (302, 303, 403)
    assert client.post("/api/schedules", data={}, follow_redirects=False).status_code in (
        302,
        303,
        403,
    )


# --- The task chat ----------------------------------------------------------------
def test_a_task_chat_has_no_composer(client: TestClient, db, registered):
    """Suppressed by absence, not by hiding: `chat/_composer.html` is the only
    thing that posts a message, so its absence is the guarantee. A hidden one
    would still be a form anybody could post to."""
    _make(client)
    schedule = db.scalars(select(Schedule)).one()

    body = client.get(f"/chat/{schedule.chat_id}").text
    assert "composer__form" not in body
    assert 'name="content"' not in body
    # And the controls that do apply are there instead.
    assert f"/api/schedules/{schedule.id}/run" in body
    assert f"/api/schedules/{schedule.id}/toggle" in body


def test_the_strip_survives_its_schedule_being_removed(
    client: TestClient, db, registered
):
    """Removing a schedule keeps its chat by default. The chat becomes an
    ordinary one, so it is reachable — a KIND_TASK chat with no schedule behind
    it would be in no list at all."""
    _make(client)
    schedule = db.scalars(select(Schedule)).one()
    chat_id = schedule.chat_id

    client.post(f"/api/schedules/{schedule.id}/delete", data={"keep_chat": "1"})

    chat = db.get(Chat, chat_id)
    db.refresh(chat)
    assert chat is not None
    assert chat.kind == "chat"
    assert client.get(f"/chat/{chat_id}").status_code == 200


def test_removing_a_schedule_can_take_its_chat(client: TestClient, db, registered):
    _make(client)
    schedule = db.scalars(select(Schedule)).one()
    chat_id = schedule.chat_id

    client.post(f"/api/schedules/{schedule.id}/delete", data={"keep_chat": "0"})

    db.expunge_all()
    assert db.get(Chat, chat_id) is None


# --- Controls that write -----------------------------------------------------------
def test_pausing_and_resuming_move_the_row(client: TestClient, db, registered):
    """Asserted on the row rather than on the response: a control wired to a
    method its route does not serve returns 405 and looks exactly like working
    software, which cost the agent-mode select an entire release."""
    _make(client)
    schedule = db.scalars(select(Schedule)).one()

    client.post(f"/api/schedules/{schedule.id}/toggle", data={"enabled": "0"})
    db.refresh(schedule)
    assert schedule.enabled is False

    client.post(f"/api/schedules/{schedule.id}/toggle", data={"enabled": "1"})
    db.refresh(schedule)
    assert schedule.enabled is True


def test_resuming_recomputes_from_now(client: TestClient, db, registered):
    """A schedule paused for a month must not come back owing a month of runs.
    Without this it fires the instant it is switched on."""
    _make(client, repeat="every", every_amount="1", every_unit="hours")
    schedule = db.scalars(select(Schedule)).one()

    schedule.enabled = False
    schedule.next_fire_at = datetime(2020, 1, 1, tzinfo=UTC)
    db.commit()

    schedules_service.set_enabled(db, schedule, owner=_user(db), enabled=True)

    assert clock.as_utc(schedule.next_fire_at) > datetime.now(tz=UTC)


def test_editing_the_rule_restarts_the_count(client: TestClient, db, registered):
    """An edited schedule is a new intention. Carrying the old `fired_count`
    into a new `count` would spend most of it before the first run."""
    _make(client)
    schedule = db.scalars(select(Schedule)).one()
    schedule.fired_count = 7
    db.commit()

    client.post(
        f"/api/schedules/{schedule.id}",
        data={
            "title": "Changed",
            "instruction": "Something else.",
            "target": "report",
            "repeat": "every",
            "every_amount": "2",
            "every_unit": "hours",
            "count": "3",
        },
    )

    db.refresh(schedule)
    assert schedule.fired_count == 0
    assert schedule.title == "Changed"
    assert schedule.target == TARGET_REPORT


def test_the_routes_refuse_the_wrong_verb(client: TestClient, db, registered):
    """The other half of the agent-mode lesson: assert the wrong method is
    *refused*, because only that half would have failed throughout."""
    _make(client)
    schedule = db.scalars(select(Schedule)).one()

    assert client.get(f"/api/schedules/{schedule.id}/toggle").status_code == 405
    assert client.get(f"/api/schedules/{schedule.id}/run").status_code == 405
    assert client.patch(f"/api/schedules/{schedule.id}/delete").status_code == 405


def test_one_persons_schedule_is_not_anothers(client: TestClient, db, registered):
    from lembas.security.passwords import hash_password

    _make(client)
    schedule = db.scalars(select(Schedule)).one()

    stranger = User(
        email="sam@shire.test",
        name="Sam",
        password_hash=hash_password("gardening-is-hard"),
        role="admin",
    )
    db.add(stranger)
    db.commit()

    assert schedules_service.get(db, schedule.id, stranger) is None


# --- The admin page ----------------------------------------------------------------
def test_the_admin_page_saves_and_clamps(client: TestClient, db, registered):
    """Clamped on read as well as here, for the reason `agents` and `images`
    give: a value stored by an earlier release, or edited into the database by
    hand, has to be survivable too. What this asserts is that neither half has
    been quietly dropped."""
    response = client.post(
        "/admin/schedules",
        data={
            "enabled": "true",
            "tick_seconds": "1",
            "max_per_user": "9999",
            "max_concurrent": "0",
            "min_interval_seconds": "1",
            "max_queued": "500",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    values = settings_store.schedules(db)
    assert values["enabled"] is True
    assert values["tick_seconds"] == 5        # floor: a busy loop otherwise
    assert values["max_per_user"] == 200      # ceiling
    # 0 falls back to the default rather than clamping to 1, because the
    # accessor reads `int(stored or default)` -- the same shape `agents` and
    # `images` use. There is no reading of "no runs at once" that anybody wants:
    # it would be a ticker that claims work and never does it.
    assert values["max_concurrent"] == 3
    assert values["min_interval_seconds"] == 60
    assert values["max_queued"] == 50


def test_the_switch_actually_stops_firing(client: TestClient, db, registered):
    """A switch that only greys something out is the failure. The sweep reads
    it, so a schedule created while it was on stops when it is turned off."""
    import anyio

    from lembas.services.schedule import ticker

    _make(client)
    client.post("/admin/schedules", data={"enabled": ""}, follow_redirects=False)

    assert anyio.run(ticker.sweep) == 0


def test_the_admin_page_is_admin_only(client: TestClient, db, registered):
    _as_stranger(client, db)
    assert client.get("/admin/schedules", follow_redirects=False).status_code in (
        302, 303, 403, 404,
    )
