"""Archiving a chat, which the column has been filtered on and never written.

`Chat.archived` is read in four places, always `is_(False)`, and was set to True
by nothing anywhere in `src/` -- so the hiding shipped and the archiving did
not, and the column read as a built feature to anybody who grepped for it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lembas.db.models import Chat


def test_a_chat_can_be_archived(client: TestClient, db, registered, make_chat):
    chat_id = make_chat()
    response = client.patch(f"/api/chats/{chat_id}", data={"archived": "1"})
    assert response.status_code == 200
    db.expire_all()
    assert db.get(Chat, chat_id).archived is True


def test_archiving_answers_with_a_sidebar_the_browser_can_swap_in(
    client: TestClient, db, registered, make_chat
):
    """`update_chat` answers 204 for everything else, and htmx's own config is
    `{code: "204", swap: false}` -- so a button aimed at `#sidebar-tree` set the
    column and then did visibly nothing until the next page load.

    Asserted on the *response*, because the obvious test -- archive, then load
    the page, then look -- passes against both versions. It is the control doing
    nothing that has to be caught, not the column failing to change.
    """
    chat_id = make_chat()
    response = client.patch(f"/api/chats/{chat_id}", data={"archived": "1"})

    assert response.status_code == 200
    assert 'id="sidebar-tree"' in response.text
    assert "nav-group--archived" in response.text
    assert chat_id in response.text


def test_a_patch_that_changes_nothing_still_answers_204(
    client: TestClient, db, registered, make_chat
):
    """Re-rendering the sidebar for every PATCH would put the tree in the reply
    to a rename, a folder move and a model change as well -- none of which
    asked for it, and one of which already answers with its own fragment."""
    chat_id = make_chat()
    assert client.patch(
        f"/api/chats/{chat_id}", data={"archived": "0"}
    ).status_code == 204
    assert client.patch(
        f"/api/chats/{chat_id}", data={"model_id": ""}
    ).status_code == 204


def test_it_can_be_put_back(client: TestClient, db, registered, make_chat):
    """An archive with no way out is a delete that lies about itself."""
    chat_id = make_chat()
    client.patch(f"/api/chats/{chat_id}", data={"archived": "1"})
    client.patch(f"/api/chats/{chat_id}", data={"archived": "0"})
    db.expire_all()
    assert db.get(Chat, chat_id).archived is False


def test_leaving_the_field_out_leaves_it_alone(client: TestClient, db, registered, make_chat):
    """`update_chat` reads the raw form precisely so that absent and empty are
    different things, and every other field there honours it."""
    chat_id = make_chat()
    client.patch(f"/api/chats/{chat_id}", data={"archived": "1"})
    client.patch(f"/api/chats/{chat_id}", data={"title": "Still here"})
    db.expire_all()
    chat = db.get(Chat, chat_id)
    assert chat.archived is True
    assert chat.title == "Still here"


def test_an_archived_chat_leaves_the_list_and_joins_the_other_one(
    client: TestClient, db, registered, make_chat
):
    chat_id = make_chat()
    page = client.get("/chat").text
    assert chat_id in page

    client.patch(f"/api/chats/{chat_id}", data={"archived": "1"})
    page = client.get("/chat").text
    # Still reachable -- in the Archived group, which is the whole difference
    # between archiving and deleting.
    assert "nav-group--archived" in page
    assert chat_id in page


def test_the_group_is_absent_when_nothing_is_in_it(client: TestClient, registered, make_chat):
    make_chat()
    assert "nav-group--archived" not in client.get("/chat").text


def test_nobody_else_may_archive_your_chat(client: TestClient, db, registered, make_chat):
    """`_owned_chat` is what stops it, and this is the test that says so."""
    chat_id = make_chat()
    client.cookies.clear()
    # `follow_redirects=False`, or the redirect to the sign-in page is followed
    # and the 200 that comes back reads as the request having succeeded.
    response = client.patch(
        f"/api/chats/{chat_id}", data={"archived": "1"}, follow_redirects=False
    )
    assert response.status_code in (401, 403, 404, 303, 307)
    db.expire_all()
    assert db.get(Chat, chat_id).archived is False
