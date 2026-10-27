"""Prompt suggestions: seeding, administration, and the cards."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Connection, Model, Suggestion, User
from lembas.services import settings_store
from lembas.services import suggestions as suggestions_service
from lembas.services.crypto import encrypt
from tests.conftest import join


@pytest.fixture
def clean(client: TestClient, db, registered):
    """Start from no suggestions.

    The app's lifespan seeds the three defaults, so any test that goes through
    `client` already has them. Tests about seeding want that; tests about the
    admin screen want to count their own rows.
    """
    for suggestion in suggestions_service.all_of_them(db):
        db.delete(suggestion)
    db.commit()


@pytest.fixture
def plain_user(client: TestClient, db, registered) -> User:
    client.post("/auth/logout", follow_redirects=False)
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    return db.scalar(select(User).where(User.email == "sam@shire.test"))


def _connection(db) -> None:
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()


# --- Seeding ------------------------------------------------------------------
def test_seeding_writes_the_defaults_once(db):
    assert suggestions_service.seed_defaults(db) == len(suggestions_service.DEFAULTS)
    assert suggestions_service.seed_defaults(db) == 0
    assert len(suggestions_service.all_of_them(db)) == len(suggestions_service.DEFAULTS)


def test_deleting_every_suggestion_does_not_bring_them_back(db):
    """The guard is a flag, not "is the table empty". Otherwise an administrator
    who decided against them gets them back on every restart."""
    suggestions_service.seed_defaults(db)
    for suggestion in suggestions_service.all_of_them(db):
        db.delete(suggestion)
    db.commit()

    assert suggestions_service.seed_defaults(db) == 0
    assert suggestions_service.all_of_them(db) == []


def test_the_seed_flag_lives_in_settings(db):
    suggestions_service.seed_defaults(db)
    assert settings_store.get(db, suggestions_service.SEEDED_KEY) is True


def test_every_default_prompt_stands_on_its_own():
    """A card is sent the instant it is clicked, with nothing added. One that
    trails off waiting for material the person has not given is a prompt the
    model has to guess at."""
    for name, _description, prompt in suggestions_service.DEFAULTS:
        assert prompt == prompt.strip(), f"{name} has stray whitespace"
        # Any terminal punctuation, not a full stop. The point is that the
        # prompt finishes rather than trails off, and a card that asks the model
        # a direct question ends with "?" -- which this asserted was malformed.
        assert prompt[-1] in ".?!", f"{name} does not end as a complete sentence"


# --- What is shown ------------------------------------------------------------
def test_only_enabled_ones_are_shown(db):
    suggestions_service.create(db, name="Shown", prompt="a")
    hidden = suggestions_service.create(db, name="Hidden", prompt="b")
    hidden.enabled = False
    db.commit()

    assert [s.name for s in suggestions_service.visible(db)] == ["Shown"]


def test_position_decides_the_order(db):
    first = suggestions_service.create(db, name="Zebra", prompt="a")
    second = suggestions_service.create(db, name="Antelope", prompt="b")
    first.position, second.position = 5, 1
    db.commit()

    assert [s.name for s in suggestions_service.visible(db)] == ["Antelope", "Zebra"]


def test_no_more_than_the_cap_is_shown(db):
    for index in range(suggestions_service.MAX_SHOWN + 3):
        suggestions_service.create(db, name=f"One {index}", prompt="x")
    assert len(suggestions_service.visible(db)) == suggestions_service.MAX_SHOWN


def test_new_rows_land_at_the_end(db):
    suggestions_service.create(db, name="First", prompt="a")
    assert suggestions_service.create(db, name="Second", prompt="b").position == 1


# --- The cards ----------------------------------------------------------------
def test_the_cards_appear_on_the_new_chat_screen(client: TestClient, db, registered):
    _connection(db)
    suggestions_service.create(db, name="Explain this", description="Plain language.", prompt="Go ")

    page = client.get("/chat").text
    assert "Explain this" in page
    assert "Plain language." in page
    assert 'data-suggestion="Go "' in page


def test_the_cards_do_not_appear_in_an_existing_empty_chat(
    client: TestClient, db, registered, make_chat
):
    """A chat someone opened on purpose already has a model and a prompt
    chosen; the cards are for the screen where nothing has been decided."""
    _connection(db)
    suggestions_service.create(db, name="Explain this", prompt="Go ")
    chat_id = make_chat()

    assert "data-suggestion" not in client.get(f"/chat/{chat_id}").text


def test_a_prompt_with_quotes_is_escaped_in_the_attribute(client: TestClient, db, registered):
    _connection(db)
    suggestions_service.create(db, name="Tricky", prompt='say "<script>alert(1)</script>"')

    page = client.get("/chat").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# --- Administration -----------------------------------------------------------
def test_the_admin_page_is_refused_to_a_plain_user(client: TestClient, plain_user):
    assert client.get("/admin/suggestions").status_code == 403
    assert client.post("/admin/suggestions", data={"name": "x"}).status_code == 403


def test_an_admin_can_create_edit_and_delete(client: TestClient, db, registered, clean):
    client.post(
        "/admin/suggestions",
        data={"name": "Explain this", "description": "Plain language.", "prompt": "Go "},
        follow_redirects=False,
    )
    suggestion = db.scalar(select(Suggestion))
    assert suggestion.name == "Explain this"
    assert suggestion.enabled is True

    client.post(
        f"/admin/suggestions/{suggestion.id}",
        data={"name": "Renamed", "description": "", "prompt": "New ", "position": "3"},
        follow_redirects=False,
    )
    db.refresh(suggestion)
    assert suggestion.name == "Renamed"
    assert suggestion.prompt == "New "
    # An unticked checkbox is simply absent from the post, which is the signal.
    assert suggestion.enabled is False
    assert suggestion.position == 2

    client.post(f"/admin/suggestions/{suggestion.id}/delete", follow_redirects=False)
    assert db.scalar(select(Suggestion)) is None


def test_a_nameless_suggestion_is_refused(client: TestClient, db, registered, clean):
    client.post("/admin/suggestions", data={"name": "  "}, follow_redirects=False)
    assert db.scalar(select(Suggestion)) is None


def test_the_limit_is_enforced(client: TestClient, db, registered, clean):
    for index in range(suggestions_service.MAX_SUGGESTIONS):
        suggestions_service.create(db, name=f"One {index}", prompt="x")

    client.post("/admin/suggestions", data={"name": "One too many"}, follow_redirects=False)
    assert len(suggestions_service.all_of_them(db)) == suggestions_service.MAX_SUGGESTIONS


def test_editing_something_that_is_gone_is_a_404(client: TestClient, registered):
    assert client.post("/admin/suggestions/nope", data={"name": "x"}).status_code == 404
