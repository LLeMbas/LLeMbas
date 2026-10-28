"""The sidebar's Chat/Agent switch: what it stores, and what it hides."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Folder, Model, User
from lembas.services import settings_store
from lembas.services.crypto import encrypt


def _enable_agents(db) -> None:
    """The split only applies when agent chats are possible.

    With them off the sidebar deliberately goes back to showing everything, so
    a test that did not do this would be asserting against the un-split
    behaviour and passing for the wrong reason.
    """
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)


def _add_connection(db) -> Connection:
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()
    return connection


def _chat(db, *, title: str, kind: str = "chat", folder: Folder | None = None) -> Chat:
    user = db.scalars(select(User).order_by(User.created_at)).first()
    chat = Chat(
        user_id=user.id,
        title=title,
        kind=kind,
        model_id="test-model",
        folder_id=folder.id if folder else None,
    )
    db.add(chat)
    db.commit()
    return chat


def _folder(db, name: str, parent: Folder | None = None) -> Folder:
    user = db.scalars(select(User).order_by(User.created_at)).first()
    folder = Folder(user_id=user.id, name=name, parent_id=parent.id if parent else None)
    db.add(folder)
    db.commit()
    return folder


# --- What the switch stores ---------------------------------------------------
def test_the_switch_stores_the_choice_and_returns_the_tree(client: TestClient, db, registered):
    response = client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    assert response.status_code == 200
    # The fragment, not a redirect and not a full page: the folder open/closed
    # state must survive a flick of the switch.
    assert 'id="sidebar-tree"' in response.text
    assert "<!doctype html>" not in response.text.lower()

    user = db.scalars(select(User).order_by(User.created_at)).first()
    db.refresh(user)
    assert user.settings_json["sidebar_kind"] == "agent"


def test_an_unknown_kind_is_refused_rather_than_stored(client: TestClient, db, registered):
    """`sidebar_kind` reads anything unrecognised back as "chat", so storing it
    would be a preference that silently does nothing."""
    assert client.post("/api/preferences/sidebar-kind", data={"kind": "wizard"}).status_code == 400

    user = db.scalars(select(User).order_by(User.created_at)).first()
    db.refresh(user)
    assert "sidebar_kind" not in (user.settings_json or {})


def test_the_plus_follows_the_switch(client: TestClient, db, registered):
    """"New chat" is a + beside the section heading, inside the tree the
    switch swaps -- so it cannot say one side while the other is listed, which
    is what the button above the list once did. The switch and New folder stay
    put: nothing in their row depends on the side, so the fragment carries no
    copy of it.

    Asserted on the *fragment*, not on a page load.
    """
    _add_connection(db)
    _enable_agents(db)

    response = client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    assert 'id="sidebar-actions"' not in response.text
    # No device linked: how to link one, not a + nobody could use.
    assert "lembas login" in response.text
    assert "data-new-chat" not in response.text

    response = client.post("/api/preferences/sidebar-kind", data={"kind": "chat"})
    assert "lembas login" not in response.text
    assert 'href="/chat" data-new-chat' in response.text


def test_a_page_load_carries_no_stray_out_of_band_row(
    client: TestClient, db, registered
):
    """`hx-swap-oob` on a full page load would be a duplicate element sitting in
    the tree, waiting to be swapped by the next unrelated request."""
    _add_connection(db)
    _enable_agents(db)

    page = client.get("/chat").text
    assert page.count('id="sidebar-actions"') == 1
    assert 'id="sidebar-actions"\n     hx-swap-oob' not in page
    assert "hx-swap-oob" not in page.split('id="sidebar-tree"')[1][:2000]


def test_the_switch_survives_a_page_load(client: TestClient, db, registered):
    _add_connection(db)
    _enable_agents(db)
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    page = client.get("/chat").text
    assert re.search(r'id="sidebar-kind-agent"\s+checked', page)
    # The Agent side is what the sidebar shows: with no device linked, how to
    # link one.
    assert "lembas login" in page


# --- What each side shows ------------------------------------------------------
def test_each_side_shows_only_its_own_kind(client: TestClient, db, registered):
    _add_connection(db)
    _enable_agents(db)
    _chat(db, title="An ordinary question")
    _chat(db, title="A machine errand", kind="agent")

    page = client.get("/chat").text
    assert "An ordinary question" in page
    assert "A machine errand" not in page

    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    page = client.get("/chat").text
    assert "A machine errand" in page
    assert "An ordinary question" not in page


def test_a_folder_holding_only_agent_chats_stays_on_the_chats_side(
    client: TestClient, db, registered
):
    """Folders are the Chats side's alone. With the Agents side showing no
    folders, hiding a folder whose chats are all agent chats here would hide it
    everywhere. So it is shown, empty of this side's kind -- and its agent chat
    is listed under its device on the other side."""
    _add_connection(db)
    _enable_agents(db)
    folder = _folder(db, "Errands")
    _chat(db, title="A machine errand", kind="agent", folder=folder)

    page = client.get("/chat").text
    assert "Errands" in page
    assert "A machine errand" not in page

    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    page = client.get("/chat").text
    assert "Errands" not in page
    assert "A machine errand" in page


def test_a_chat_three_levels_down_is_shown(client: TestClient, db, registered):
    """Every child folder is drawn on the Chats side, so a chat filed deep down
    is found where it was filed."""
    _add_connection(db)
    _enable_agents(db)
    top = _folder(db, "Top")
    middle = _folder(db, "Middle", parent=top)
    bottom = _folder(db, "Bottom", parent=middle)
    _chat(db, title="An ordinary question", folder=bottom)

    page = client.get("/chat").text
    assert "Top" in page
    assert "Middle" in page
    assert "Bottom" in page
    assert "An ordinary question" in page


def test_an_empty_folder_shows_on_the_chats_side_only(client: TestClient, db, registered):
    """A folder that was empty to begin with is a container somebody just made,
    so it must be findable to be filed into -- on the Chats side, which is the
    only side with folders."""
    _add_connection(db)
    _enable_agents(db)
    _folder(db, "Waiting")

    assert "Waiting" in client.get("/chat").text
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    assert "Waiting" not in client.get("/chat").text


def test_a_folder_holding_both_kinds_shows_the_right_half(client: TestClient, db, registered):
    _add_connection(db)
    _enable_agents(db)
    folder = _folder(db, "Mixed")
    _chat(db, title="An ordinary question", folder=folder)
    _chat(db, title="A machine errand", kind="agent", folder=folder)

    page = client.get("/chat").text
    assert "Mixed" in page
    assert "An ordinary question" in page
    assert "A machine errand" not in page

    # No folder on the Agents side; the filed agent chat is under its
    # device's section (here "device missing" -- it has none).
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    page = client.get("/chat").text
    assert "Mixed" not in page
    assert "A machine errand" in page
    assert "An ordinary question" not in page


# --- Whether the switch appears at all -----------------------------------------
def test_the_switch_is_absent_when_agent_chats_are_off(client: TestClient, db, registered):
    """A two-way switch with one useful side is worse than no switch: it offers
    a view that is empty by construction and cannot be made otherwise."""
    _add_connection(db)
    settings_store.update(db, {"enabled": False}, key=settings_store.AGENTS)

    page = client.get("/chat").text
    assert 'id="sidebar-kind-agent"' not in page


def test_the_switch_is_present_when_agent_chats_are_on(client: TestClient, db, registered):
    _add_connection(db)
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)

    page = client.get("/chat").text
    assert 'id="sidebar-kind-agent"' in page
    assert 'id="sidebar-kind-chat"' in page


# --- The row itself ------------------------------------------------------------
def test_an_agent_chat_is_marked_in_the_row(client: TestClient, db, registered):
    """Legible with the switch off too: a chat that can run commands should not
    look like one that cannot."""
    _add_connection(db)
    _enable_agents(db)
    _chat(db, title="A machine errand", kind="agent")
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})

    page = client.get("/chat").text
    assert "#i-terminal" in page


# --- The verb goes where the event does ----------------------------------------
def test_the_switch_posts_at_a_route_that_serves_post(client: TestClient, db, registered):
    """Asserted as a refusal as well as a success. A control wired to a method
    its route does not serve fails silently -- htmx surfaces nothing, so the
    interface looks exactly like one that works."""
    assert client.get("/api/preferences/sidebar-kind").status_code == 405
    assert client.post("/api/preferences/sidebar-kind", data={"kind": "chat"}).status_code == 200


# --- Pinned models ----------------------------------------------------------------
def _pin(db, model_id: str = "test-model") -> None:
    model = db.scalar(select(Model).where(Model.model_id == model_id))
    model.pinned = True
    db.commit()


def test_a_pinned_model_carries_the_side_the_switch_is_on(
    client: TestClient, db, registered
):
    """Picking a pinned model on the Agent side used to open an ordinary chat,
    which is the fork silently ignoring the one choice already made."""
    _add_connection(db)
    _enable_agents(db)
    _pin(db)

    page = client.get("/chat").text
    assert 'href="/chat?model=test-model"' in page

    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    page = client.get("/chat").text
    assert 'href="/chat?model=test-model&amp;kind=agent"' in page


def test_the_pinned_links_follow_the_switch_without_a_reload(
    client: TestClient, db, registered
):
    """They sit above the tree the switch swaps, so they arrive out of band --
    the same shape as the New chat button. Asserted on the *fragment*: a page
    load re-renders them anyway, which is how the New chat button went unnoticed.
    """
    _add_connection(db)
    _enable_agents(db)
    _pin(db)

    response = client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    assert 'id="sidebar-pinned"' in response.text
    assert "kind=agent" in response.text

    response = client.post("/api/preferences/sidebar-kind", data={"kind": "chat"})
    assert 'href="/chat?model=test-model"' in response.text


def test_the_pinned_group_is_always_there_to_be_swapped(
    client: TestClient, db, registered
):
    """Rendered even with nothing pinned. A block that vanished when the last
    model was unpinned would leave the out-of-band fragment with nowhere to
    land, and htmx says nothing at all when a target is missing."""
    _add_connection(db)
    _enable_agents(db)

    assert 'id="sidebar-pinned"' in client.get("/chat").text
    response = client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    assert 'id="sidebar-pinned"' in response.text
    # And it says nothing when there is nothing to say.
    assert "Pinned models" not in response.text


def test_an_empty_pinned_group_takes_no_room():
    """It is in the DOM whether or not it holds anything, so it has to collapse
    or every sidebar with nothing pinned gains a gap."""
    from pathlib import Path

    import lembas

    css = (Path(lembas.__file__).parent / "web/static/css/app.css").read_text(encoding="utf-8")
    assert ".nav-group--pinned:empty" in css


def test_the_shortcuts_are_on_every_page_that_shows_the_sidebar(
    client: TestClient, db, registered
):
    """They came from the chat page's own context, so other pages carrying the
    sidebar had it without them -- a shortcut that is
    there on one page and gone on the next."""
    _add_connection(db)
    _pin(db)

    for path in ("/chat", "/search", "/settings"):
        assert "Pinned models" in client.get(path).text, path
