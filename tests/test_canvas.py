"""The canvas panel: tabs, sources, saving, and what a model may move."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import (
    Attachment,
    Chat,
    Connection,
    Model,
    ScratchDoc,
    User,
)
from lembas.services import canvas as canvas_service
from lembas.services import scratch as scratch_service
from lembas.services.crypto import encrypt
from lembas.services.library import documents as documents_service
from lembas.services.library import notes as notes_service


def _page(db, user, base, text: str):
    """A knowledge document, without going near the network."""
    from lembas.services.fetch import Fetched

    return documents_service.store_page(
        db,
        owner=user,
        base=base,
        page=Fetched(url="http://example.test/terms", title="Terms", text=text),
    )


def _add_connection(db) -> Connection:
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()
    return connection


# --- Tab bookkeeping, with no HTTP in the way ---------------------------------
def test_a_key_with_a_colon_in_the_path_survives():
    """`split`, not `str.split`: a key that lost half its path would silently
    open a different file."""
    assert canvas_service.split("agent:/srv/a:b.py") == ("agent", "/srv/a:b.py")


def test_one_file_has_one_key():
    """A tab a model opened and a tab a person opened must be one tab, or the
    panel shows the same file twice and only one is the one being saved."""
    assert (
        canvas_service.path_key("/srv/app", "./main.py")
        == canvas_service.path_key("/srv/app", "main.py")
        == canvas_service.path_key("/srv/app", "/srv/app/main.py")
    )


def test_opening_the_same_key_twice_is_one_tab():
    state: dict = {}
    canvas_service.open_tab(state, {"key": "note:1", "title": "A"})
    canvas_service.open_tab(state, {"key": "note:1", "title": "A"})
    assert len(state["tabs"]) == 1
    assert state["active"] == "note:1"


def test_a_model_opening_a_tab_does_not_take_the_screen():
    """An agent reads forty files in a long reply. If each one took the panel,
    somebody reading the third would be dragged through the other
    thirty-seven -- and anybody halfway through an edit would lose it."""
    state: dict = {}
    canvas_service.open_tab(state, {"key": "note:1", "title": "Mine"})
    canvas_service.open_tab(state, {"key": "agent:/a.py", "title": "Theirs"}, activate=False)

    assert state["active"] == "note:1"
    assert [t["key"] for t in state["tabs"]] == ["note:1", "agent:/a.py"]


def test_the_first_tab_is_activated_even_by_a_model():
    """Otherwise a panel full of tabs would have nothing in front, which reads
    as a panel that failed to load."""
    state: dict = {}
    canvas_service.open_tab(state, {"key": "agent:/a.py"}, activate=False)
    assert state["active"] == "agent:/a.py"


def test_eviction_never_closes_the_tab_in_front():
    state: dict = {}
    canvas_service.open_tab(state, {"key": "note:keep"})
    for index in range(canvas_service.MAX_TABS + 4):
        canvas_service.open_tab(state, {"key": f"agent:/f{index}.py"}, activate=False)

    keys = [t["key"] for t in state["tabs"]]
    assert len(keys) == canvas_service.MAX_TABS
    assert "note:keep" in keys
    assert state["active"] == "note:keep"


def test_closing_the_active_tab_moves_to_another():
    state: dict = {}
    canvas_service.open_tab(state, {"key": "note:1"})
    canvas_service.open_tab(state, {"key": "note:2"})
    canvas_service.close_tab(state, "note:2")
    assert state["active"] == "note:1"


def test_closing_the_last_tab_leaves_nothing_active():
    state: dict = {}
    canvas_service.open_tab(state, {"key": "note:1"})
    canvas_service.close_tab(state, "note:1")
    assert state["active"] == ""
    assert state["tabs"] == []


def test_merge_keeps_a_tab_opened_during_the_reply():
    """`_persist` is the single writer and its snapshot was seeded when the
    reply began, so overwriting would drop what somebody opened since."""
    stored = {"tabs": [{"key": "note:mine", "title": "Mine"}], "active": "note:mine"}
    live = {"tabs": [{"key": "agent:/a.py", "title": "Theirs"}], "active": "agent:/a.py"}

    merged = canvas_service.merge(stored, live)
    assert {t["key"] for t in merged["tabs"]} == {"note:mine", "agent:/a.py"}
    # And a reply finishing ten minutes later must not move what is in front.
    assert merged["active"] == "note:mine"


# --- Through the routes --------------------------------------------------------
def test_the_panel_opens_empty(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    response = client.get(f"/api/chats/{chat_id}/canvas")
    assert response.status_code == 200
    assert "Nothing open" in response.text


def test_someone_elses_chat_is_a_404(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    other = User(name="Sam", email="sam@shire.test", password_hash="x")
    db.add(other)
    db.commit()
    chat = Chat(user_id=other.id, model_id="test-model")
    db.add(chat)
    db.commit()

    assert client.get(f"/api/chats/{chat.id}/canvas").status_code == 404
    assert (
        client.post(f"/api/chats/{chat.id}/canvas/tabs", data={"key": "note:1"}).status_code
        == 404
    )


def test_a_get_never_opens_a_tab(client: TestClient, db, registered, make_chat):
    """There is no CSRF token here and the cookie is SameSite Lax, so a
    state-changing GET is a link somebody can be made to follow."""
    _add_connection(db)
    chat_id = make_chat()
    client.get(f"/api/chats/{chat_id}/canvas?key=scratch:{chat_id}")
    assert not (db.get(Chat, chat_id).canvas_json or {}).get("tabs")


def test_opening_and_closing_the_scratch_document(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()

    opened = client.post(
        f"/api/chats/{chat_id}/canvas/tabs", data={"key": f"scratch:{chat_id}"}
    )
    assert opened.status_code == 200
    db.expire_all()
    assert (db.get(Chat, chat_id).canvas_json or {})["active"] == f"scratch:{chat_id}"

    client.post(f"/api/chats/{chat_id}/canvas/tabs/close", data={"key": f"scratch:{chat_id}"})
    db.expire_all()
    assert (db.get(Chat, chat_id).canvas_json or {})["tabs"] == []


def test_a_scratch_key_naming_another_chat_is_refused(
    client: TestClient, db, registered, make_chat
):
    """A forged key must not reach another conversation's pad."""
    _add_connection(db)
    mine = make_chat()
    theirs = make_chat()

    response = client.post(f"/api/chats/{mine}/canvas/tabs", data={"key": f"scratch:{theirs}"})
    assert "another chat" in response.text
    db.expire_all()
    assert not (db.get(Chat, mine).canvas_json or {}).get("tabs")


def test_an_unknown_source_says_so_rather_than_500ing(
    client: TestClient, db, registered, make_chat
):
    """An exception page swapped into a side panel is a blank side panel."""
    _add_connection(db)
    chat_id = make_chat()
    response = client.post(f"/api/chats/{chat_id}/canvas/tabs", data={"key": "wizard:1"})
    assert response.status_code == 200
    assert "nothing to open" in response.text.lower()


def test_a_tab_whose_row_was_deleted_renders_an_error(
    client: TestClient, db, registered, make_chat
):
    _add_connection(db)
    chat_id = make_chat()
    user = db.get(User, db.get(Chat, chat_id).user_id)
    note = notes_service.create(db, owner=user, title="Gone", body="soon")
    client.post(f"/api/chats/{chat_id}/canvas/tabs", data={"key": f"note:{note.id}"})
    notes_service.delete(db, note)

    response = client.get(f"/api/chats/{chat_id}/canvas")
    assert response.status_code == 200
    assert "not there any more" in response.text


# --- Saving --------------------------------------------------------------------
def test_a_note_is_saved_through_the_canvas(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    user = db.get(User, db.get(Chat, chat_id).user_id)
    note = notes_service.create(db, owner=user, title="Errands", body="alpha")

    doc = canvas_service._stamp(note, note.body)
    response = client.post(
        f"/api/chats/{chat_id}/canvas/save",
        data={"key": f"note:{note.id}", "text": "beta", "revision": doc},
    )
    assert response.status_code == 200
    db.expire_all()
    assert db.get(type(note), note.id).body == "beta"


def test_a_stale_revision_writes_nothing(client: TestClient, db, registered, make_chat):
    """Never save silently over somebody else's change, and never discard what
    was typed here either -- the card carries both."""
    _add_connection(db)
    chat_id = make_chat()
    user = db.get(User, db.get(Chat, chat_id).user_id)
    note = notes_service.create(db, owner=user, title="Errands", body="alpha")

    response = client.post(
        f"/api/chats/{chat_id}/canvas/save",
        data={"key": f"note:{note.id}", "text": "beta", "revision": "0:999"},
    )
    assert response.status_code == 200
    assert "changed after you opened it" in response.text
    # What was typed comes back in the box, so Overwrite is one click.
    assert "beta" in response.text
    db.expire_all()
    assert db.get(type(note), note.id).body == "alpha"


def test_an_empty_revision_overwrites(client: TestClient, db, registered, make_chat):
    """Which is exactly what Overwrite on the conflict card sends: somebody has
    been shown both versions and chosen."""
    _add_connection(db)
    chat_id = make_chat()
    user = db.get(User, db.get(Chat, chat_id).user_id)
    note = notes_service.create(db, owner=user, title="Errands", body="alpha")

    client.post(
        f"/api/chats/{chat_id}/canvas/save",
        data={"key": f"note:{note.id}", "text": "beta", "revision": ""},
    )
    db.expire_all()
    assert db.get(type(note), note.id).body == "beta"


def test_someone_elses_note_cannot_be_saved(client: TestClient, db, registered, make_chat):
    """Sharing grants reading only."""
    _add_connection(db)
    chat_id = make_chat()
    other = User(name="Sam", email="sam@shire.test", password_hash="x")
    db.add(other)
    db.commit()
    note = notes_service.create(db, owner=other, title="Theirs", body="alpha")

    response = client.post(
        f"/api/chats/{chat_id}/canvas/save",
        data={"key": f"note:{note.id}", "text": "beta", "revision": ""},
    )
    db.expire_all()
    assert db.get(type(note), note.id).body == "alpha"
    assert "not there any more" in response.text or "not yours" in response.text


def test_an_attachment_has_no_save_path(client: TestClient, db, registered, make_chat):
    """`DELETE /api/files/{id}` already refuses once an attachment has been sent
    because it would rewrite a message somebody read. Editing is the same act
    with a quieter failure."""
    _add_connection(db)
    chat_id = make_chat()
    attachment = Attachment(
        user_id=db.get(Chat, chat_id).user_id,
        chat_id=chat_id,
        filename="notes.txt",
        stored_name="x.txt",
        media_type="text/plain",
        kind="text",
        extracted_text="alpha",
    )
    db.add(attachment)
    db.commit()

    response = client.post(
        f"/api/chats/{chat_id}/canvas/save",
        data={"key": f"file:{attachment.id}", "text": "beta", "revision": ""},
    )
    assert "only be read" in response.text
    db.expire_all()
    assert db.get(Attachment, attachment.id).extracted_text == "alpha"


def test_an_attachment_from_another_chat_is_refused(
    client: TestClient, db, registered, make_chat
):
    """A canvas must not browse another conversation's files by id."""
    _add_connection(db)
    mine = make_chat()
    theirs = make_chat()
    attachment = Attachment(
        user_id=db.get(Chat, theirs).user_id,
        chat_id=theirs,
        filename="notes.txt",
        stored_name="x.txt",
        media_type="text/plain",
        kind="text",
        extracted_text="alpha",
    )
    db.add(attachment)
    db.commit()

    response = client.post(
        f"/api/chats/{mine}/canvas/tabs", data={"key": f"file:{attachment.id}"}
    )
    assert "another chat" in response.text


# --- A project file, now that there is no machine ---------------------------------
def test_a_project_file_key_opens_nothing(client: TestClient, db, registered, make_chat):
    """There is no `agent:` source. A key naming it -- a tab stored by an
    older release, a hand-made request -- is refused like any
    unknown source, and no tab is kept for it."""
    _add_connection(db)
    chat_id = make_chat()
    response = client.post(
        f"/api/chats/{chat_id}/canvas/tabs", data={"key": "agent:/etc/passwd"}
    )
    assert "nothing to open" in response.text
    db.expire_all()
    assert not (db.get(Chat, chat_id).canvas_json or {}).get("tabs")


# --- The document source ------------------------------------------------------------
def test_a_documents_text_can_be_replaced(client: TestClient, db, registered, make_chat):
    """Replacing a failed extraction by hand is the main reason to want this."""
    _add_connection(db)
    chat_id = make_chat()
    user = db.get(User, db.get(Chat, chat_id).user_id)
    base = documents_service.create_base(db, owner=user, name="Contracts")
    document = _page(db, user, base, "alpha")
    document.extraction_error = "Could not read this."
    db.commit()

    documents_service.set_text(db, document, "beta")
    db.expire_all()
    refreshed = documents_service.get(db, document.id, user)
    assert refreshed.extracted_text == "beta"
    # The old apology beside the new text would be the page contradicting itself.
    assert refreshed.extraction_error == ""


def test_editing_a_document_does_not_change_a_transcript(db, registered, make_chat):
    """`files.copy_document` copies the text when a document is attached, so an
    edit only changes what future searches find."""
    from lembas.services import files as files_service

    _add_connection(db)
    chat_id = make_chat()
    user = db.get(User, db.get(Chat, chat_id).user_id)
    base = documents_service.create_base(db, owner=user, name="Contracts")
    document = _page(db, user, base, "alpha")
    attachment = files_service.copy_document(
        db, user_id=user.id, chat_id=chat_id, document=document
    )

    documents_service.set_text(db, document, "beta")
    db.expire_all()
    assert db.get(Attachment, attachment.id).extracted_text == "alpha"


# --- The scratch document -----------------------------------------------------------
def test_the_pad_is_made_once_per_chat(db, registered, make_chat):
    _add_connection(db)
    chat = db.get(Chat, make_chat())

    first = scratch_service.for_chat(db, chat)
    second = scratch_service.for_chat(db, chat)
    assert first.id == second.id
    assert db.scalars(select(ScratchDoc)).all() == [first]


def test_asking_whether_there_is_one_does_not_make_one(db, registered, make_chat):
    """Otherwise every chat ever opened acquires an empty row."""
    _add_connection(db)
    chat = db.get(Chat, make_chat())
    assert scratch_service.get(db, chat) is None
    assert db.scalars(select(ScratchDoc)).all() == []


def test_appending_twice_keeps_both(db, registered, make_chat):
    """A read-and-concatenate at the call site would let two calls in one round
    each read the same body, and the second would drop the first."""
    _add_connection(db)
    chat = db.get(Chat, make_chat())
    doc = scratch_service.for_chat(db, chat)

    scratch_service.append(db, doc, "first", author="model")
    scratch_service.append(db, doc, "second", author="model")
    assert "first" in doc.body
    assert "second" in doc.body


def test_the_pad_keeps_trailing_whitespace(db, registered, make_chat):
    """A save that silently trims the line you are standing on is the kind of
    thing that makes an editor feel broken."""
    _add_connection(db)
    chat = db.get(Chat, make_chat())
    doc = scratch_service.for_chat(db, chat)
    scratch_service.update(db, doc, body="a line   \n")
    assert doc.body == "a line   \n"


def test_attaching_the_pad_copies_it(client: TestClient, db, registered, make_chat):
    """The pad goes on being written after the message is sent, by both sides."""
    _add_connection(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    doc = scratch_service.for_chat(db, chat)
    scratch_service.update(db, doc, body="the draft")

    response = client.post("/api/files/from-scratch", data={"chat_id": chat_id})
    assert response.status_code == 200

    scratch_service.update(db, doc, body="changed since")
    db.expire_all()
    attachment = db.scalar(select(Attachment))
    assert attachment.extracted_text == "the draft"


def test_an_empty_pad_is_not_worth_attaching(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    chat_id = make_chat()
    response = client.post("/api/files/from-scratch", data={"chat_id": chat_id})
    assert "not available" in response.text
    assert db.scalar(select(Attachment)) is None


def test_the_pad_of_another_chat_cannot_be_attached(
    client: TestClient, db, registered, make_chat
):
    _add_connection(db)
    other = User(name="Sam", email="sam@shire.test", password_hash="x")
    db.add(other)
    db.commit()
    chat = Chat(user_id=other.id, model_id="test-model")
    db.add(chat)
    db.commit()
    scratch_service.update(db, scratch_service.for_chat(db, chat), body="theirs")

    response = client.post("/api/files/from-scratch", data={"chat_id": chat.id})
    assert "not available" in response.text
    assert db.scalar(select(Attachment)) is None


# --- Where the panel appears ---------------------------------------------------------
def test_the_panel_is_on_a_chat_and_not_on_the_new_chat_screen(
    client: TestClient, db, registered, make_chat
):
    """Absent before there is a row, for the reason the scope menu is: there is
    nothing to hang a tab on yet."""
    _add_connection(db)
    assert 'id="canvas"' not in client.get("/chat").text
    assert 'id="canvas"' in client.get(f"/chat/{make_chat()}").text


def test_the_panel_shares_one_slot_with_the_others(
    client: TestClient, db, registered, make_chat
):
    """At 1280px the sidebar plus two panels leaves about seventy pixels of
    conversation, so only one of the three is ever open."""
    _add_connection(db)
    page = client.get(f"/chat/{make_chat()}").text
    assert 'data-toggle="#canvas" data-toggle-group="side"' in page


@pytest.mark.parametrize("verb", ["get"])
def test_the_tab_routes_refuse_the_wrong_method(
    client: TestClient, db, registered, make_chat, verb
):
    """A control wired to a method its route does not serve fails silently."""
    _add_connection(db)
    chat_id = make_chat()
    assert getattr(client, verb)(f"/api/chats/{chat_id}/canvas/tabs").status_code == 405
    assert getattr(client, verb)(f"/api/chats/{chat_id}/canvas/save").status_code == 405


# --- Opening a file by looking rather than by spelling --------------------------
def test_a_chat_with_no_machine_is_offered_no_file_dialog(
    client: TestClient, db, registered, make_chat
):
    """Project files are not a canvas source, so the panel offers
    no file dialog at all."""
    _add_connection(db)
    chat_id = make_chat()

    body = client.get(f"/api/chats/{chat_id}/canvas").text

    assert "data-canvas-open" not in body
    # Scratch is still there: it needs no machine.
    assert "scratch:" in body


def test_the_new_chat_screen_leaves_no_empty_htmx_verb(
    client: TestClient, db, registered
):
    """`hx-get=""` is not "fetch nothing" -- htmx looks for the attribute, not
    for a value (`if(s(t,"hx-"+r))` is hasAttribute), so an empty one is a real
    request for the empty path. The browser resolves that against the current
    document, so opening the canvas on the new-chat screen fetched the new-chat
    screen and swapped the whole site into the panel.

    Asserted over the entire page rather than over the canvas markup: the same
    `{{ ... if chat else "" }}` shape would do the same thing anywhere, and the
    failure looks like a rendering bug rather than a request.
    """
    _add_connection(db)

    body = client.get("/chat").text

    for verb in ("get", "post", "patch", "put", "delete"):
        assert f'hx-{verb}=""' not in body
