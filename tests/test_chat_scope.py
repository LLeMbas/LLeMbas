"""What one chat may use, and the rule that it can only ever be less.

The security-shaped test here is `test_a_chat_cannot_widen_what_it_was_not_given`.
The scope is applied inside `resolve_tools` *after* the model's capabilities,
the reader's permissions and the instance configuration, so a crafted POST
turning something on reaches a tool those gates have already removed. Asserting
that against the UI path alone would prove nothing, so it is asserted against a
directly-written column.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Model, User
from lembas.services import fs, settings_store, skill_files
from lembas.services import skill_format as fmt
from lembas.services import tools as tools_service


@pytest.fixture
def chat(db, user_id):
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    row = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(row)
    db.commit()
    return row


def _names(db, chat, user) -> set[str]:
    return set(tools_service.resolve_tools(db, chat, user).by_name)


# --- The route ------------------------------------------------------------------
def test_switching_a_family_off_writes_it_to_the_row(client: TestClient, db, chat, registered):
    response = client.post(
        f"/api/chats/{chat.id}/scope", data={"kind": "family", "name": "web_search"}
    )
    assert response.status_code == 204

    db.expire_all()
    assert db.get(Chat, chat.id).scope_json["families"]["web_search"] is False


def test_switching_it_back_on_removes_the_key(client: TestClient, db, chat, registered):
    """On is stored by *removing* the key, so absent stays the single
    representation of on and the column cannot grow a row per family per chat."""
    client.post(f"/api/chats/{chat.id}/scope", data={"kind": "family", "name": "notes"})
    client.post(
        f"/api/chats/{chat.id}/scope",
        data={"kind": "family", "name": "notes", "on": "true"},
    )

    db.expire_all()
    assert "families" not in db.get(Chat, chat.id).scope_json


def test_the_route_refuses_a_kind_it_does_not_know(client: TestClient, chat, registered):
    response = client.post(
        f"/api/chats/{chat.id}/scope", data={"kind": "everything", "name": "x"}
    )
    assert response.status_code == 400


def test_the_route_refuses_the_wrong_verb(client: TestClient, chat, registered):
    """The half of `tests/test_agent_mode.py`'s lesson that actually caught the
    bug: a control wired to a method a route does not serve fails silently."""
    assert client.get(f"/api/chats/{chat.id}/scope").status_code == 405


def test_somebody_elses_chat_is_not_reachable(client: TestClient, db, chat, registered):
    from lembas.security.passwords import hash_password

    other = User(name="Sam", email="s@shire.test", password_hash=hash_password("secret123"))
    db.add(other)
    db.commit()
    chat.user_id = other.id
    db.commit()

    response = client.post(
        f"/api/chats/{chat.id}/scope", data={"kind": "family", "name": "notes"}
    )
    assert response.status_code == 404


# --- What it does to the offer ------------------------------------------------------
def test_a_family_switched_off_is_not_offered(db, chat, user_id):
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    user = db.get(User, user_id)
    assert "web_search" in _names(db, chat, user)

    chat.scope_json = {"families": {"web_search": False}}
    db.commit()

    assert "web_search" not in _names(db, chat, user)


def test_switching_a_gate_off_takes_every_tool_in_it(db, chat, user_id):
    """A gate is one switch, not five. `notes` covers search, get, create, edit
    and delete -- which is the same reasoning the per-model capability
    checkboxes carry."""
    user = db.get(User, user_id)
    chat.scope_json = {"families": {"notes": False}}
    db.commit()

    offered = _names(db, chat, user)
    assert not [name for name in offered if name.startswith("notes_")]


def test_a_chat_cannot_widen_what_it_was_not_given(db, chat, user_id):
    """The one that matters. Scope is applied AFTER the gates and never instead
    of them, so writing `True` into the column reaches a tool the model's
    capabilities had already removed."""
    user = db.get(User, user_id)
    model = db.scalar(tools_service.select(Model))
    model.capabilities_json = {"tools": True, "tool_notes": False}
    chat.scope_json = {"families": {"notes": True}}
    db.commit()

    assert "notes_search" not in _names(db, chat, user)


def test_an_unknown_family_in_the_column_changes_nothing(db, chat, user_id):
    user = db.get(User, user_id)
    before = _names(db, chat, user)
    chat.scope_json = {"families": {"not-a-family": False}}
    db.commit()

    assert _names(db, chat, user) == before


# --- Skills -------------------------------------------------------------------------
@pytest.fixture
def skill(db, user_id):
    """A skill file in the person's own skills directory (skills are files)."""
    user = db.get(User, user_id)
    home = fs.ensure_home(db, user).path
    fs.write(
        fs.view(db, user),
        f"{home}/skills/weekly-report/SKILL.md",
        fmt.compose("weekly-report", "When asked for the weekly report.", "Do the thing.").encode(),
    )
    db.commit()
    return "weekly-report"


def test_a_skill_switched_off_leaves_the_index(db, chat, user_id, skill):
    user = db.get(User, user_id)
    v = fs.view(db, user, chat)
    assert "weekly-report" in skill_files.index_block(v)
    assert "weekly-report" not in skill_files.index_block(v, exclude=["weekly-report"])


def test_a_skill_switched_off_cannot_be_fetched_anyway(db, chat, user_id, skill):
    """Without this the narrowing is advisory: a model can name a skill it was
    never shown -- from an earlier turn, from a note -- and the runner would
    happily fetch it. Same rule as "what may be run is what was offered"."""
    import asyncio

    user = db.get(User, user_id)
    chat.scope_json = {"skills": {"weekly-report": False}}
    db.commit()

    context = tools_service.context_for(db, user, chat)
    outcome = asyncio.run(
        tools_service.run_tool(context, "skill_view", '{"name": "weekly-report"}')
    )
    assert outcome.event["status"] == "error"


def test_the_last_skill_switched_off_withdraws_skill_view(db, chat, user_id, skill):
    user = db.get(User, user_id)
    assert {"skill_view", "skills_list"} <= _names(db, chat, user)

    chat.scope_json = {"skills": {"weekly-report": False}}
    db.commit()

    offered = _names(db, chat, user)
    assert "skill_view" not in offered and "skills_list" not in offered
    assert "skill_manage" in offered, "writing the first one is still possible"


# --- The zero-skills asymmetry --------------------------------------------------------
def test_with_no_skills_nothing_tells_the_model_to_read_one(db, chat, user_id):
    """The complaint this fixes. `tool.skills` was gated on the family alone, so
    a person with no skills got "read the full instructions with skill_view"
    above a list that was not there -- and got skill_view in the tools array, so
    the model spent a round finding out."""
    from lembas.services import harness

    user = db.get(User, user_id)
    offered = tools_service.resolve_tools(db, chat, user).schemas
    text = harness.compose(db, user, offered, chat)

    assert "skill_view" not in _names(db, chat, user)
    assert "skill_view" not in text
    assert "Skills available" not in text
    # The half that is most useful with none: you can save the first one.
    assert "save it with skill_manage" in text


def test_with_a_skill_the_reading_guidance_comes_back(db, chat, user_id, skill):
    from lembas.services import harness

    user = db.get(User, user_id)
    offered = tools_service.resolve_tools(db, chat, user).schemas
    text = harness.compose(db, user, offered, chat)

    assert "skill_view" in text
    assert "weekly-report" in text
    assert "save it with skill_manage" in text


# --- The tool list --------------------------------------------------------------------
def test_the_model_is_told_what_it_actually_has(db, chat, user_id):
    """`tool_names` was resolved and documented with no fragment reading it. A
    model that has to discover its own list by calling something and being told
    it does not exist spends a round finding out -- and with one round, that is
    the whole reply."""
    from lembas.services import harness

    user = db.get(User, user_id)
    offered = tools_service.resolve_tools(db, chat, user).schemas
    text = harness.compose(db, user, offered, chat)

    assert "The tools you have on this request are:" in text
    for name in tools_service.resolve_tools(db, chat, user).by_name:
        assert name in text


def test_a_family_switched_off_disappears_from_the_list_too(db, chat, user_id):
    from lembas.services import harness

    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    user = db.get(User, user_id)
    chat.scope_json = {"families": {"web_search": False}}
    db.commit()

    offered = tools_service.resolve_tools(db, chat, user).schemas
    text = harness.compose(db, user, offered, chat)

    assert "web_search" not in text


def test_no_tools_means_no_list(db, chat, user_id):
    from lembas.services import harness

    text = harness.compose(db, db.get(User, user_id), [])
    assert "The tools you have on this request" not in text


def test_every_gate_that_can_be_offered_has_a_name_of_its_own(db):
    """A gate covers several tools, so no single tool's label is the right name
    for one -- and the fallback is the *first* tool's label, which is a noun
    phrase describing something that happened rather than a switch. Four gates
    had shipped that way: the canvas switch read "Canvas written" and the report
    one "Report filed". This is the direction it rots, because a new family
    passes every other test with no label at all.
    """
    gates = {tools_service.gate_of(family) for family in tools_service.FAMILIES}
    missing = sorted(gates - set(tools_service.GATE_LABELS))
    assert not missing, f"no menu label for {missing}"


# --- The control that writes ------------------------------------------------------------
def test_the_verb_is_on_every_checkbox(client: TestClient, db, chat, registered):
    """The element carrying `name` has to be the element carrying the request.
    Two selects lost an entire release to getting this wrong -- their verb was
    on a form the event never reached, and the tests passed throughout because
    they asserted the markup rather than the property.

    `conftest.control_named` is the helper for this and wants exactly one match;
    there is one checkbox per family here, so the same check is made over all of
    them, which is the stronger claim anyway.
    """
    from html.parser import HTMLParser

    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    html = client.get(f"/chat/{chat.id}").text

    found: list[dict[str, str]] = []

    class Finder(HTMLParser):
        def handle_starttag(self, tag, attrs):
            got = {key: (value or "") for key, value in attrs}
            if got.get("name") == "on":
                found.append(got)

    Finder().feed(html)

    assert found, "the scope menu rendered no switches"
    for box in found:
        assert box.get("hx-post") == f"/api/chats/{chat.id}/scope"
        assert "kind" in box.get("hx-vals", ""), "and says which thing it is"


def test_the_switches_are_in_the_chat_settings_panel(client: TestClient, db, chat, registered):
    """The composer has no tools menu ("Toggle"). A chat's switches are in
    its settings panel with every other tweak, reached from the composer's
    settings; the defaults are per model, in Settings -> Models."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    html = client.get(f"/chat/{chat.id}").text

    assert 'aria-label="Toggle"' not in html
    panel = html[html.index('id="chat-settings"'):]
    assert '"kind": "family"' in panel
    assert 'data-toggle="#chat-settings"' in html


def test_the_menu_no_longer_offers_to_type_an_at_sign(client: TestClient, db, chat, registered):
    """A menu you open in order to insert one character is a longer way round
    than the character. Typing `@` is untouched and is asserted elsewhere."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    html = client.get(f"/chat/{chat.id}").text

    assert "data-mention-open" not in html
    assert "Mention a file or a document" not in html


def test_with_nothing_to_narrow_there_is_no_button_at_all(
    client: TestClient, db, chat, registered
):
    """The guard is not `has_scope or can upload`: with no mention row to show
    when there is no scope, that guard would open an empty menu, which is worse
    than no button.

    A model with no `tools` capability is offered nothing, so there is nothing
    to switch off -- the honest way to reach an empty scope. That also takes
    prototypes being off for the instance: they need no tools, so a tool-less
    model still has them to switch off.
    """
    from lembas.services import settings_store

    model = db.query(Model).filter_by(model_id="m").one()
    model.capabilities_json = {}
    settings_store.update(db, {"prototypes_enabled": False})
    db.commit()

    html = client.get(f"/chat/{chat.id}").text

    assert '"kind": "family"' not in html


def test_a_tool_less_model_still_offers_the_prototype_switch(
    client: TestClient, db, chat, registered
):
    """A prototype is written, not called, so a model with no tools can make one
    -- and a chat must be able to say "not here" to it all the same."""
    model = db.query(Model).filter_by(model_id="m").one()
    model.capabilities_json = {}
    db.commit()

    html = client.get(f"/chat/{chat.id}").text

    assert 'id="chat-settings"' in html
    assert '"name": "prototypes"' in html


# --- Before the chat exists -------------------------------------------------------
def test_the_menu_is_there_before_the_first_message(
    client: TestClient, db, chat, registered
):
    """The bug this section exists for.

    The harness puts a tool's guidance in front of the model the moment the tool
    is offered — so a menu that only appeared once a chat existed was one you
    could not reach until after the model had been told how to keep notes and
    been handed the tools to do it. Switching it off then does not un-send that
    turn.
    """
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    html = client.get("/chat").text

    # In the settings panel, outside the composer's form and carried by it.
    assert 'id="chat-settings"' in html
    assert 'id="composer-form"' in html
    assert 'name="scope_all" value="web_search" form="composer-form"' in html
    assert 'name="scope_all"' in html
    assert 'name="scope_on"' in html
    # And it posts nothing on its own: there is no row to post to yet.
    assert "/scope" not in html


def test_the_prospective_switches_ride_with_the_first_message(
    client: TestClient, db, chat, registered
):
    """A browser submits only the ticked boxes, so "which were unticked" needs
    the hidden mirror. This asserts the pair exists per gate rather than that
    the markup looks a certain way."""
    from html.parser import HTMLParser

    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    html = client.get("/chat").text

    named: dict[str, list[str]] = {"scope_all": [], "scope_on": []}

    class Finder(HTMLParser):
        def handle_starttag(self, tag, attrs):
            got = {key: (value or "") for key, value in attrs}
            if got.get("name") in named:
                named[got["name"]].append(got.get("value", ""))

    Finder().feed(html)

    assert named["scope_all"], "the prospective menu rendered no gates"
    # Every gate offered has both halves, or one of them can never be turned off.
    assert set(named["scope_all"]) == set(named["scope_on"])


def test_unticking_before_sending_writes_it_to_the_new_chat(
    client: TestClient, db, chat, registered
):
    """End to end: what the menu was set to is what the row is created with, so
    the very first request is already narrowed."""
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)

    client.post(
        "/api/chats/start",
        data={
            "content": "hello",
            "model_id": "m",
            "scope_all": ["notes", "memory", "web_search"],
            # `notes` left out: unticked.
            "scope_on": ["memory", "web_search"],
        },
    )

    fresh = db.scalars(
        select(Chat).where(Chat.user_id == chat.user_id).order_by(Chat.created_at.desc())
    ).first()
    assert tools_service.scoped_off(fresh) == frozenset({"notes"})
    assert "notes_search" not in _names(db, fresh, db.get(User, chat.user_id))


def test_leaving_everything_ticked_writes_nothing(client: TestClient, db, chat, registered):
    """Absent means on, and there is one representation of it. A row full of
    `True`s would be a second one, and "why is this off?" would have two
    answers."""
    client.post(
        "/api/chats/start",
        data={
            "content": "hello",
            "model_id": "m",
            "scope_all": ["notes", "memory"],
            "scope_on": ["notes", "memory"],
        },
    )

    fresh = db.scalars(
        select(Chat).where(Chat.user_id == chat.user_id).order_by(Chat.created_at.desc())
    ).first()
    assert fresh.scope_json == {}


def test_starting_a_chat_cannot_widen_through_the_menu(
    client: TestClient, db, chat, registered
):
    """The security-shaped half, from the new-chat side. `scope_json` narrows
    inside `resolve_tools` *after* every gate, so naming a gate that was never
    offered switches off something that was not on — which is nothing. A
    crafted POST cannot turn anything on, because there is no representation
    for "on" to send."""
    client.post(
        "/api/chats/start",
        data={
            "content": "hello",
            "model_id": "m",
            "scope_all": ["agent", "made_up"],
            "scope_on": ["agent", "made_up"],
        },
    )

    fresh = db.scalars(
        select(Chat).where(Chat.user_id == chat.user_id).order_by(Chat.created_at.desc())
    ).first()
    assert "shell_run" not in _names(db, fresh, db.get(User, chat.user_id))
