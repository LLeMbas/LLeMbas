"""What a folder carries, and what the chats inside it inherit from it."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import TOKEN_DEVICE, Chat, Connection, Folder, Model, User
from lembas.security import api_tokens
from lembas.services import chat as chat_service
from lembas.services import device_link, settings_store
from lembas.services.crypto import encrypt


def _add_connection(db) -> Connection:
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()
    return connection


def _folder(db, name: str, parent: Folder | None = None, **fields) -> Folder:
    user = db.scalars(select(User).order_by(User.created_at)).first()
    folder = Folder(
        user_id=user.id, name=name, parent_id=parent.id if parent else None, **fields
    )
    db.add(folder)
    db.commit()
    return folder


def _link_a_device(db) -> None:
    """A LLeMbas CLI dialled in for the first user, which is what makes the
    Agent fork of the new-chat screen offer anything. Nothing is ever
    sent to it here, so it answers nothing."""
    user = db.scalars(select(User).order_by(User.created_at)).first()
    token, _ = api_tokens.create(
        db, user, name="CLI on device-1", scopes=["models", "link"],
        kind=TOKEN_DEVICE, device_host="device-1",
    )

    async def nothing(*_a, **_k):
        return None

    device_link.LINKS[token.id] = device_link.Link(
        token_id=token.id, user_id=user.id, name="CLI on device-1",
        send_text=nothing, close=nothing,
        hello={"device": {"host": "device-1"}, "limits": {"roots": ["/srv/project"]}},
    )


# --- Naming and renaming -------------------------------------------------------
def test_a_folder_is_created_with_the_name_that_was_asked_for(
    client: TestClient, db, registered
):
    client.post("/api/folders", data={"name": "Isengard"})
    assert db.scalar(select(Folder)).name == "Isengard"


def test_a_folder_with_no_name_still_gets_one(client: TestClient, db, registered):
    """The button asks first, but a request that arrives without one must not
    produce a folder with a blank label nobody can click."""
    client.post("/api/folders", data={})
    assert db.scalar(select(Folder)).name == "New folder"


def test_renaming_a_folder(client: TestClient, db, registered):
    """PATCH has been able to do this since folders existed and nothing in the
    interface called it, so a folder could not be renamed at all."""
    folder = _folder(db, "Isengard")
    assert client.patch(f"/api/folders/{folder.id}", data={"name": "Orthanc"}).status_code == 204
    db.refresh(folder)
    assert folder.name == "Orthanc"


def test_a_blank_rename_is_ignored(client: TestClient, db, registered):
    """A folder nobody can see the name of is one nobody can find."""
    folder = _folder(db, "Isengard")
    client.patch(f"/api/folders/{folder.id}", data={"name": "   "})
    db.refresh(folder)
    assert folder.name == "Isengard"


# --- The system prompt ladder ---------------------------------------------------
def test_a_folder_prompt_reaches_a_chat_inside_it(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    folder = _folder(db, "Errands", system_prompt="Answer in the fewest words.")
    chat = db.get(Chat, make_chat())
    chat.folder_id = folder.id
    db.commit()

    assert chat_service.effective_system_prompt(db, chat) == "Answer in the fewest words."


def test_a_nested_folder_inherits_its_parents_prompt(
    client: TestClient, db, registered, make_chat
):
    """A project's prompt belongs on the project, not on each sub-folder of it."""
    _add_connection(db)
    top = _folder(db, "Project", system_prompt="Answer in the fewest words.")
    inner = _folder(db, "Notes", parent=top)
    chat = db.get(Chat, make_chat())
    chat.folder_id = inner.id
    db.commit()

    assert chat_service.effective_system_prompt(db, chat) == "Answer in the fewest words."


def test_the_nearest_folder_prompt_wins(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    top = _folder(db, "Project", system_prompt="From the top.")
    inner = _folder(db, "Notes", parent=top, system_prompt="From the sub-folder.")
    chat = db.get(Chat, make_chat())
    chat.folder_id = inner.id
    db.commit()

    assert chat_service.effective_system_prompt(db, chat) == "From the sub-folder."


def test_the_chats_own_prompt_still_wins(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    folder = _folder(db, "Errands", system_prompt="From the folder.")
    chat = db.get(Chat, make_chat())
    chat.folder_id = folder.id
    chat.system_prompt = "From the chat."
    db.commit()

    assert chat_service.effective_system_prompt(db, chat) == "From the chat."


def test_a_folder_prompt_beats_the_models(client: TestClient, db, registered, make_chat):
    """The folder is the more specific statement: a model's prompt describes the
    model wherever it is used, a folder's describes this piece of work."""
    _add_connection(db)
    model = db.scalar(select(Model))
    model.system_prompt = "From the model."
    folder = _folder(db, "Errands", system_prompt="From the folder.")
    chat = db.get(Chat, make_chat())
    chat.folder_id = folder.id
    db.commit()

    assert chat_service.effective_system_prompt(db, chat) == "From the folder."


def test_an_empty_folder_prompt_falls_through(client: TestClient, db, registered, make_chat):
    _add_connection(db)
    model = db.scalar(select(Model))
    model.system_prompt = "From the model."
    folder = _folder(db, "Errands")
    chat = db.get(Chat, make_chat())
    chat.folder_id = folder.id
    db.commit()

    assert chat_service.effective_system_prompt(db, chat) == "From the model."


def test_the_panel_names_the_folder_as_the_source(client: TestClient, db, registered, make_chat):
    """The settings panel mirrors the ladder and has to keep mirroring it. One
    naming the wrong source is worse than one naming none, because it is
    believed."""
    _add_connection(db)
    folder = _folder(db, "Errands", system_prompt="Answer in the fewest words.")
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    chat.folder_id = folder.id
    db.commit()

    page = client.get(f"/chat/{chat_id}").text
    assert "folder prompt" in page or "the folder" in page


# --- Seeds ----------------------------------------------------------------------
def test_a_folder_seeds_a_new_chats_model(client: TestClient, db, registered):
    _add_connection(db)
    db.add(Model(connection_id=db.scalar(select(Connection)).id, model_id="other-model"))
    db.commit()
    folder = _folder(db, "Errands", model_id="other-model")

    client.post("/api/chats/start", data={"content": "Hello", "folder_id": folder.id})
    chat = db.scalar(select(Chat))
    assert chat.model_id == "other-model"


def test_an_explicit_choice_beats_the_folders_seed(client: TestClient, db, registered):
    """The folder says what this work usually needs; the screen in front of
    somebody says what they want this time."""
    _add_connection(db)
    db.add(Model(connection_id=db.scalar(select(Connection)).id, model_id="other-model"))
    db.commit()
    folder = _folder(db, "Errands", model_id="other-model")

    client.post(
        "/api/chats/start",
        data={"content": "Hello", "folder_id": folder.id, "model_id": "test-model"},
    )
    assert db.scalar(select(Chat)).model_id == "test-model"


def test_a_folder_belonging_to_someone_else_seeds_nothing(client: TestClient, db, registered):
    _add_connection(db)
    other = User(name="Sam", email="sam@shire.test", password_hash="x")
    db.add(other)
    db.commit()
    folder = Folder(user_id=other.id, name="Theirs", model_id="other-model")
    db.add(folder)
    db.commit()

    client.post("/api/chats/start", data={"content": "Hello", "folder_id": folder.id})
    chat = db.scalar(select(Chat))
    assert chat.model_id == "test-model"


def test_an_agent_folder_with_no_device_starts_a_plain_chat(
    client: TestClient, db, registered
):
    """A folder fixed to agent seeds the kind, not a machine. With no device
    linked there is nothing for an agent chat to run on, so `_device_target`
    refuses and the chat is an ordinary one rather than one pointed at nothing."""
    _add_connection(db)
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    folder = _folder(db, "Errands", kind="agent")

    response = client.post(
        "/api/chats/start", data={"content": "Hello", "folder_id": folder.id}
    )
    assert response.status_code == 204
    assert db.scalar(select(Chat)).kind == "chat"


def test_the_seeds_are_saved_and_cleared_through_one_route(client: TestClient, db, registered):
    """Every field clearable, which is what reading the raw form buys: with
    `Form(None)` an empty box and an absent one are the same request."""
    folder = _folder(db, "Errands")
    client.patch(
        f"/api/folders/{folder.id}",
        data={
            "description": "Work on the tower.",
            "system_prompt": "Answer in the fewest words.",
            "model_id": "test-model",
            "kind": "agent",
        },
    )
    db.refresh(folder)
    assert folder.description == "Work on the tower."
    assert folder.system_prompt == "Answer in the fewest words."
    assert folder.kind == "agent"

    client.patch(f"/api/folders/{folder.id}", data={"system_prompt": "", "kind": ""})
    db.refresh(folder)
    assert folder.system_prompt == ""
    assert folder.kind == ""
    # Untouched keys are left alone rather than blanked.
    assert folder.description == "Work on the tower."


def test_a_kind_that_is_not_a_kind_is_dropped(client: TestClient, db, registered):
    """A folder seeding a kind that is not a kind hands every chat a value
    `_new_chat` then has to ignore anyway."""
    folder = _folder(db, "Errands")
    client.patch(f"/api/folders/{folder.id}", data={"kind": "wizard"})
    db.refresh(folder)
    assert folder.kind == ""


# --- Getting into a folder at all -----------------------------------------------
def test_new_chat_here_files_the_chat(client: TestClient, db, registered):
    """`/api/chats/start` has accepted a folder_id since folders existed and
    nothing ever sent one."""
    _add_connection(db)
    folder = _folder(db, "Errands")

    page = client.get(f"/chat?folder={folder.id}").text
    assert f'name="folder_id" value="{folder.id}"' in page

    client.post("/api/chats/start", data={"content": "Hello", "folder_id": folder.id})
    assert db.scalar(select(Chat)).folder_id == folder.id


def test_a_folder_fixed_to_agent_opens_the_new_chat_screen_on_that_fork(
    client: TestClient, db, registered
):
    _add_connection(db)
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    _link_a_device(db)
    folder = _folder(db, "Errands", kind="agent")

    try:
        page = client.get(f"/chat?folder={folder.id}").text
    finally:
        device_link.reset()
    assert 'name="kind" value="agent"' in page


# --- The settings page ----------------------------------------------------------
def test_the_settings_page_renders_what_is_stored(client: TestClient, db, registered):
    _add_connection(db)
    folder = _folder(db, "Errands", system_prompt="Answer in the fewest words.")

    page = client.get(f"/folders/{folder.id}").text
    assert "Answer in the fewest words." in page
    assert f'hx-patch="/api/folders/{folder.id}"' in page


def test_the_settings_page_can_be_scrolled(client: TestClient, db, registered):
    """`.main` is a flex column with `min-height: 0`, so a `.page` dropped
    straight into it overflows the viewport with nothing to scroll and Save ends
    up below the bottom of the window. Every other page of this shape wraps its
    content in the scrolling container; this one did not."""
    folder = _folder(db, "Errands")
    page = client.get(f"/folders/{folder.id}").text
    assert 'class="admin-scroll"' in page
    assert page.index('class="admin-scroll"') < page.index('class="page"')


def test_the_settings_page_refuses_someone_elses_folder(client: TestClient, db, registered):
    other = User(name="Sam", email="sam@shire.test", password_hash="x")
    db.add(other)
    db.commit()
    folder = Folder(user_id=other.id, name="Theirs")
    db.add(folder)
    db.commit()

    assert client.get(f"/folders/{folder.id}").status_code == 404


def test_the_settings_form_posts_at_a_route_that_serves_patch(
    client: TestClient, db, registered
):
    """A control wired to a method its route does not serve fails silently --
    htmx surfaces nothing, so it looks exactly like a control that works."""
    folder = _folder(db, "Errands")
    assert client.post(f"/api/folders/{folder.id}", data={"name": "x"}).status_code == 405
    assert client.patch(f"/api/folders/{folder.id}", data={"name": "x"}).status_code == 204


# --- Nesting, which had no control ---------------------------------------------
def folders_service_create(client, name: str, *, parent: str = "") -> str:
    """Make a folder through the route and return its id."""
    data = {"name": name}
    if parent:
        data["parent_id"] = parent
    client.post("/api/folders", data=data)

    from lembas.db.session import session_scope

    with session_scope() as db:
        return db.scalar(select(Folder).where(Folder.name == name)).id


def test_a_folder_can_be_put_inside_another(client: TestClient, db, registered):
    """`parent_id` has been handled at the route since folders existed, with a
    cycle guard and a depth cap, and `partials/_folder.html` has always recursed
    to draw the tree. Nothing anywhere submitted it, so the README advertised
    "arbitrarily nested" folders that could not be nested."""
    outer = folders_service_create(client, "Outer")
    inner = folders_service_create(client, "Inner")

    client.patch(f"/api/folders/{inner}", data={"parent_id": outer})

    assert db.get(Folder, inner).parent_id == outer


def test_the_settings_page_offers_the_move(client: TestClient, db, registered):
    """And offers only moves the route will accept -- a picker listing a folder
    that would be refused is a control that looks like it works."""
    outer = folders_service_create(client, "Outer")
    inner = folders_service_create(client, "Inner")
    client.patch(f"/api/folders/{inner}", data={"parent_id": outer})

    page = client.get(f"/folders/{outer}").text
    assert 'name="parent_id"' in page
    # Its own child is not offered: that move is the cycle the route refuses.
    assert f'value="{inner}"' not in page
    assert f'value="{outer}"' not in page, "a folder cannot be its own parent"


def test_a_folder_cannot_be_moved_into_its_own_subtree(client: TestClient, db, registered):
    outer = folders_service_create(client, "Outer")
    inner = folders_service_create(client, "Inner")
    client.patch(f"/api/folders/{inner}", data={"parent_id": outer})

    refused = client.patch(f"/api/folders/{outer}", data={"parent_id": inner})
    assert refused.status_code == 400
    assert db.get(Folder, outer).parent_id is None


def test_moving_respects_the_depth_cap(client: TestClient, db, registered):
    """`create_folder` has always applied MAX_DEPTH; the move path never did, so
    a three-deep subtree could be dropped under a six-deep folder and build a
    tree the recursive sidebar template was never meant to draw. It went
    unnoticed because nothing could submit `parent_id` at all."""
    from lembas.api.folders import MAX_DEPTH

    chain = []
    parent = ""
    for index in range(MAX_DEPTH):
        made = folders_service_create(client, f"L{index}", parent=parent)
        chain.append(made)
        parent = made

    loose = folders_service_create(client, "Loose")
    child = folders_service_create(client, "LooseChild", parent=loose)
    assert db.get(Folder, child).parent_id == loose

    # `loose` is two tall; the deepest folder is already at the cap.
    refused = client.patch(f"/api/folders/{loose}", data={"parent_id": chain[-1]})
    assert refused.status_code == 400
    assert db.get(Folder, loose).parent_id is None


def test_a_chat_cannot_be_put_in_somebody_elses_folder(client: TestClient, db, registered):
    """A folder is not just a label. `effective_system_prompt` walks up from the
    chat through its folder and that folder's parents, so a chat attached to
    another account's folder would take their system prompt -- a setting read
    across an ownership boundary through a field that looks like a tag.

    Both paths had it. `_new_chat` resolved the folder, discarded it when it was
    not the caller's, and then stored the **raw id** anyway -- so the check
    governed which seeds were applied and not where the chat went.
    """
    from lembas.security.passwords import hash_password

    stranger = User(name="Sam", email="sam@shire.test", password_hash=hash_password("x"))
    db.add(stranger)
    db.commit()
    theirs = Folder(user_id=stranger.id, name="Private", system_prompt="Speak as Sam.")
    db.add(theirs)
    db.commit()

    mine = db.scalar(select(User).where(User.email != "sam@shire.test"))
    chat = Chat(user_id=mine.id, model_id="m")
    db.add(chat)
    db.commit()

    client.patch(f"/api/chats/{chat.id}", data={"folder_id": theirs.id})
    db.refresh(chat)
    assert chat.folder_id is None

    started = client.post(
        "/api/chats/start",
        data={"content": "hello", "folder_id": theirs.id},
        follow_redirects=False,
    )
    assert started.status_code in (200, 204, 303)
    made = db.scalars(select(Chat).where(Chat.id != chat.id)).all()
    assert all(c.folder_id != theirs.id for c in made)
