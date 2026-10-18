"""The sidebar's Search, and what moved in the sidebar with it.

- `/search`: one box over chats and agent sessions, the library, reports and
  memories -- only what the reader may open, only their own chats and
  memories, never another person's, and every snippet escaped;
- `[ PINNED MODELS ]` folds away, remembered on the account;
- folders belong to the Chats side: none on the Agents side, where a filed
  agent chat is listed under its device;
- each root folder is a section `[ NAME ]`; New folder is in `[ CHATS ]`;
- each device section filters its sessions: active, archived, or all.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from markupsafe import Markup
from sqlalchemy import select

from lembas.db.models import (
    TOKEN_DEVICE,
    ApiToken,
    Chat,
    Folder,
    Memory,
    Message,
    User,
)
from lembas.services import device_link, settings_store
from lembas.services.library import notes as notes_service
from tests.test_schedules_ui import _as_stranger


def _owner(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _chat(
    db,
    user: User,
    title: str,
    *,
    kind: str = "chat",
    said: str = "",
    archived: bool = False,
    folder: Folder | None = None,
    device: ApiToken | None = None,
    ago: int = 0,
) -> Chat:
    chat = Chat(
        user_id=user.id, title=title, kind=kind, archived=archived,
        folder_id=folder.id if folder else None,
        device_token_id=device.id if device else None,
    )
    db.add(chat)
    db.commit()
    if said:
        db.add(Message(chat_id=chat.id, role="user", content=said))
    chat.updated_at = datetime.now(UTC) - timedelta(minutes=ago)
    db.commit()
    return chat


def _folder(db, user: User, name: str, parent: Folder | None = None) -> Folder:
    folder = Folder(user_id=user.id, name=name, parent_id=parent.id if parent else None)
    db.add(folder)
    db.commit()
    return folder


def _device(db, host: str, *, linked: bool = True) -> ApiToken:
    owner = _owner(db)
    token = ApiToken(
        user_id=owner.id, name=f"LLeMbas CLI on {host}", kind=TOKEN_DEVICE,
        prefix=host[:8], token_hash=host.ljust(64, "0"), device_host=host,
    )
    db.add(token)
    db.commit()
    if linked:
        device_link.LINKS[token.id] = device_link.Link(
            token_id=token.id, user_id=owner.id, name=token.name,
            send_text=None, close=None, hello={"device": {"host": host}},
        )
    return token


@pytest.fixture(autouse=True)
def _no_links():
    yield
    device_link.LINKS.clear()


def _enable_agents(db) -> None:
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)


def _tree(html: str) -> str:
    return html[html.index('id="sidebar-tree"'):html.index('class="sidebar__footer"')]


def _results(html: str) -> str:
    """The page's own column, not the sidebar beside it (which lists chats
    too, and would make every "not on the page" assertion meaningless)."""
    return html[html.index('<main class="main">'):]


# --- The way in ----------------------------------------------------------------
def test_search_is_in_the_header_rail_at_every_width(client: TestClient, db, registered):
    html = client.get("/chat").text
    header = html[html.index('class="sidebar__header"'):html.index("</header>")]
    rail = header[header.index('class="sidebar__actions-rail"'):]
    assert 'href="/search"' in rail
    # The close button stays phone-only; the rail itself is never hidden.
    from pathlib import Path

    import lembas

    css = (Path(lembas.__file__).parent / "web/static/css/app.css").read_text(encoding="utf-8")
    assert ".sidebar__search" in css
    rail_rule = css[css.index(".sidebar__actions-rail {"):]
    assert "display: none" not in rail_rule[:rail_rule.index("}")]


def test_the_admin_sidebar_has_no_search(client: TestClient, db, registered):
    html = client.get("/admin").text
    assert 'href="/search"' not in html


def test_an_empty_query_says_what_to_do(client: TestClient, db, registered):
    _chat(db, _owner(db), "Second breakfast")
    page = _results(client.get("/search").text)
    assert "Type a word or a phrase" in page
    assert "Second breakfast" not in page
    assert "autofocus" in page


# --- What is found, and whose ---------------------------------------------------
def test_chats_match_on_title_or_on_what_was_said(client: TestClient, db, registered):
    owner = _owner(db)
    by_title = _chat(db, owner, "Elevenses plans")
    by_message = _chat(db, owner, "Untitled", said="We ate elevenses at the Green Dragon")
    _chat(db, owner, "Nothing here", said="Only taters")

    page = _results(client.get("/search?q=elevenses").text)
    assert f'href="/chat/{by_title.id}"' in page
    assert f'href="/chat/{by_message.id}"' in page
    assert "Nothing here" not in page
    # The message hit quotes the message, with the match marked.
    assert "<mark>elevenses</mark>" in page


def test_archived_and_agent_chats_are_found_and_marked(client: TestClient, db, registered):
    owner = _owner(db)
    _chat(db, owner, "Old mushroom map", archived=True)
    _chat(db, owner, "Mushroom deploy", kind="agent")

    page = _results(client.get("/search?q=mushroom").text)
    assert "Old mushroom map" in page and "Mushroom deploy" in page
    assert "archived" in page
    assert "#i-terminal" in page


def test_temporary_and_task_chats_are_not_found(client: TestClient, db, registered):
    owner = _owner(db)
    temporary = _chat(db, owner, "Fleeting pipeweed")
    temporary.temporary = True
    db.commit()
    _chat(db, owner, "Scheduled pipeweed", kind="task")

    page = _results(client.get("/search?q=pipeweed").text)
    assert "Fleeting pipeweed" not in page
    assert "Scheduled pipeweed" not in page
    assert "Nothing found" in page


def test_another_persons_chats_notes_and_memories_never_appear(
    client: TestClient, db, registered
):
    frodo = _owner(db)
    _chat(db, frodo, "The ring secret", said="the ring is in my pocket")
    notes_service.create(db, owner=frodo, title="Ring note", body="the ring must go")
    db.add(Memory(owner_id=frodo.id, content="Frodo keeps the ring close"))
    db.commit()

    _as_stranger(client, db)
    page = _results(client.get("/search?q=ring").text)
    assert "The ring secret" not in page
    assert "Ring note" not in page
    assert "keeps the ring" not in page
    assert "Nothing found" in page


def test_a_snippet_is_escaped_and_only_the_mark_is_markup(client: TestClient, db, registered):
    owner = _owner(db)
    _chat(db, owner, "Untitled", said='<script>alert("x")</script> the needle <b>here</b>')

    page = _results(client.get("/search?q=needle").text)
    assert "<script>alert" not in page
    assert "&lt;script&gt;" in page
    assert "<b>here</b>" not in page
    assert "<mark>needle</mark>" in page


def test_a_match_inside_markup_is_escaped_too(client: TestClient, db, registered):
    """The needle itself may be the dangerous part."""
    owner = _owner(db)
    _chat(db, owner, "Untitled", said="before <img src=x onerror=alert(1)> after")

    page = _results(client.get("/search", params={"q": "<img src=x"}).text)
    assert "<img src=x" not in page
    assert "<mark>&lt;img src=x</mark>" in page


def test_the_snippet_function_builds_markup_from_escaped_pieces():
    from lembas.api.search import snippet

    out = snippet("x" * 300 + " <i>needle</i> " + "y" * 300, "needle")
    assert isinstance(out, Markup)
    assert "<mark>needle</mark>" in out
    assert "&lt;i&gt;" in out
    assert out.startswith("…") and out.endswith("…")
    # Roughly the width asked for, plus the two ellipses and the tags.
    assert len(str(out).replace("<mark>", "").replace("</mark>", "")) < 220


def test_like_wildcards_are_taken_literally(client: TestClient, db, registered):
    owner = _owner(db)
    _chat(db, owner, "Fifty percent")
    _chat(db, owner, "Exactly 50% off")

    page = _results(client.get("/search", params={"q": "50%"}).text)
    assert "Exactly 50% off" in page
    assert "Fifty percent" not in page
    page = _results(client.get("/search", params={"q": "_"}).text)
    assert "Fifty percent" not in page


def test_memories_are_the_readers_own_and_lead_to_settings(client: TestClient, db, registered):
    owner = _owner(db)
    db.add(Memory(owner_id=owner.id, content="Prefers lembas to cram"))
    db.commit()

    page = _results(client.get("/search?q=cram").text)
    assert "Memory" in page
    assert 'href="/settings?tab=memory"' in page
    assert "<mark>cram</mark>" in page


def test_notes_are_found_and_linked(client: TestClient, db, registered):
    note = notes_service.create(
        db, owner=_owner(db), title="Coney recipe", body="Brace of coneys, stewed"
    )
    page = _results(client.get("/search?q=coneys").text)
    assert f'href="/library/notes/{note.id}"' in page


# --- Filters --------------------------------------------------------------------
def test_all_groups_by_type_with_up_to_five_and_a_show_all(client: TestClient, db, registered):
    owner = _owner(db)
    for n in range(7):
        _chat(db, owner, f"Barrel ride {n}", ago=n)
    notes_service.create(db, owner=owner, title="Barrel notes", body="barrel ride down the river")

    page = _results(client.get("/search?q=barrel").text)
    assert "Chats &amp; agent sessions" in page
    assert page.count('class="search-hit ') == 5 + 1
    assert 'href="/search?in=chats&amp;q=barrel"' in page
    # Newest first.
    assert page.index("Barrel ride 0") < page.index("Barrel ride 4")
    assert "Barrel ride 6" not in page


def test_a_filtered_view_shows_one_type_and_the_rest(client: TestClient, db, registered):
    owner = _owner(db)
    for n in range(7):
        _chat(db, owner, f"Barrel ride {n}", ago=n)
    notes_service.create(db, owner=owner, title="Barrel notes", body="barrel ride")

    page = _results(client.get("/search?in=chats&q=barrel").text)
    assert "Barrel ride 6" in page
    assert "Barrel notes" not in page
    assert "Show all" not in page
    assert re.search(r'class="filter-tab is-active"\s+href="/search\?in=chats', page)
    # And a search from here searches the same filter again.
    assert 'name="in" value="chats"' in page


def test_filters_follow_the_permissions(client: TestClient, db, registered):
    owner = _owner(db)
    notes_service.create(db, owner=owner, title="Shared lore", body="lore of the shire")
    settings_store.update(
        db, {"default_permissions": {"library.use": False, "reports.use": False}}
    )
    stranger = _as_stranger(client, db)
    _chat(db, stranger, "Lore chat")

    page = _results(client.get("/search?q=lore").text)
    assert "?in=chats" in page
    for kind in ("knowledge", "notes", "skills", "reports", "memories"):
        assert f"?in={kind}" not in page, kind
    assert "Lore chat" in page

    # Asking for a type by URL that is not offered searches "All" instead.
    page = _results(client.get("/search?in=notes&q=lore").text)
    assert "Lore chat" in page
    assert "Shared lore" not in page


def test_reports_are_offered_with_reports_use_alone(client: TestClient, db, registered):
    settings_store.update(
        db, {"default_permissions": {"library.use": False, "reports.use": True}}
    )
    _as_stranger(client, db)
    page = _results(client.get("/search?q=x").text)
    assert "?in=reports" in page
    assert "?in=notes" not in page


def test_no_hits_is_the_empty_block(client: TestClient, db, registered):
    page = _results(client.get("/search?q=balrog").text)
    assert 'class="empty"' in page
    assert "Nothing found" in page


# --- API-only --------------------------------------------------------------------
def test_api_only_refuses_the_page_and_hides_the_icon(client: TestClient, db, registered):
    from lembas.services import instance_mode

    assert instance_mode.classify("/search") == "page"
    response = client.post(
        "/admin/general", data={"instance_mode": "api"}, follow_redirects=False
    )
    assert response.status_code == 303
    try:
        response = client.get("/search?q=x", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == instance_mode.NOTICE_URL
        assert 'href="/search"' not in client.get("/settings").text
    finally:
        client.post("/admin/general", data={"instance_mode": "full"}, follow_redirects=False)


# --- Pinned models ------------------------------------------------------------------
def _pin_a_model(db) -> None:
    from lembas.db.models import Connection, Model

    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", pinned=True))
    db.commit()


def _pinned_head(html: str) -> str:
    group = html[html.index('id="sidebar-pinned"'):]
    return group[:group.index("</button>")]


def test_pinned_models_fold_and_remember_it(client: TestClient, db, registered):
    _pin_a_model(db)
    html = client.get("/chat").text
    assert 'aria-expanded="true"' in _pinned_head(html)
    assert '<div class="nav-group__body" id="section-pinned">' in html

    assert client.post(
        "/api/preferences/section", data={"key": "pinned", "state": "0"}
    ).status_code == 204
    user = _owner(db)
    db.refresh(user)
    assert user.settings_json["sections"] == {"pinned": False}

    html = client.get("/chat").text
    assert 'aria-expanded="false"' in _pinned_head(html)
    assert '<div class="nav-group__body" id="section-pinned" hidden>' in html

    # And the out-of-band copy the switch brings says the same.
    _enable_agents(db)
    fragment = client.post("/api/preferences/sidebar-kind", data={"kind": "agent"}).text
    assert 'aria-expanded="false"' in _pinned_head(fragment)

    # Back to the default is removed rather than stored.
    client.post("/api/preferences/section", data={"key": "pinned", "state": "1"})
    db.refresh(user)
    assert user.settings_json["sections"] == {}


def test_the_old_pinned_key_is_still_read_and_retired_on_the_next_write(
    client: TestClient, db, registered
):
    """The pinned group's state lives in `sections`, not the older `pinned_open`
    key. A choice stored under the old key is honoured until the first write
    to the map, which retires it. (The old key's own route is gone: nothing
    has posted to it since 1.2.0.)"""
    _pin_a_model(db)
    user = _owner(db)
    user.settings_json = {**(user.settings_json or {}), "pinned_open": False}
    db.commit()
    assert 'aria-expanded="false"' in _pinned_head(client.get("/chat").text)

    posted = client.post("/api/preferences/section", data={"key": "pinned", "state": "1"})
    assert posted.status_code == 204
    db.refresh(user)
    assert "pinned_open" not in user.settings_json
    assert 'aria-expanded="true"' in _pinned_head(client.get("/chat").text)


def test_every_section_toggle_is_posted_from_app_js():
    from pathlib import Path

    import lembas

    js = (Path(lembas.__file__).parent / "web/static/js/app.js").read_text(encoding="utf-8")
    at = js.index('"/api/preferences/section"')
    handler = js[js.rindex('addEventListener("click"', 0, at):at]
    assert 'closest("[data-section]")' in handler
    assert "aria-expanded" in handler


# --- Folders ------------------------------------------------------------------------
def test_no_folders_on_the_agents_side_and_filed_agent_chats_in_their_device(
    client: TestClient, db, registered
):
    _enable_agents(db)
    owner = _owner(db)
    device_1 = _device(db, "device-1")
    folder = _folder(db, owner, "Errands")
    _chat(db, owner, "Filed errand", kind="agent", folder=folder, device=device_1)
    _chat(db, owner, "Loose errand", kind="agent", device=device_1)

    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    tree = _tree(client.get("/chat").text)
    assert "Errands" not in tree
    assert 'class="folder' not in tree
    section = tree[tree.index(">device-1</span>"):]
    assert "Filed errand" in section and "Loose errand" in section


def test_folders_on_the_chats_side_hold_only_chats(client: TestClient, db, registered):
    _enable_agents(db)
    owner = _owner(db)
    folder = _folder(db, owner, "Mixed")
    _chat(db, owner, "Ordinary filed", folder=folder)
    _chat(db, owner, "Agent filed", kind="agent", folder=folder)

    tree = _tree(client.get("/chat").text)
    assert "Ordinary filed" in tree
    assert "Agent filed" not in tree


def test_a_root_folder_is_a_section_heading(client: TestClient, db, registered):
    owner = _owner(db)
    top = _folder(db, owner, "Quest")
    _folder(db, owner, "Side quest", parent=top)

    tree = _tree(client.get("/chat").text)
    assert ">Folders<" not in tree
    # `[ QUEST ]`: the heading's own label, a button that folds it, and its
    # actions in the heading's boxes.
    head = tree[tree.index('class="nav-group__head folder__head"'):]
    head = head[:head.index('class="folder__contents')]
    assert re.search(
        r'<button class="nav-group__label nav-group__toggle folder__heading"[^>]*>\s*'
        r'<span class="nav-group__bracketed"><span class="nav-group__name">Quest</span>',
        head,
    )
    assert f'href="/chat?folder={top.id}"' in head
    assert "nav-group__add" in head
    assert f'hx-delete="/api/folders/{top.id}"' in head
    # Below `[ CHATS ]`, and the child is a row inside it.
    assert tree.index("Quest") > tree.index(">Chats<")
    assert 'class="nav-item folder__row"' in tree
    assert tree.index("Side quest") > tree.index("Quest")


def test_a_collapsed_root_folder_starts_closed(client: TestClient, db, registered):
    owner = _owner(db)
    folder = _folder(db, owner, "Shut")
    folder.collapsed = True
    db.commit()
    tree = _tree(client.get("/chat").text)
    assert (
        f'data-section="folder:{folder.id}" aria-expanded="false"\n'
        f'        aria-controls="section-folder-{folder.id}"'
    ) in tree
    assert f'id="section-folder-{folder.id}" hidden>' in tree


def test_new_folder_is_in_the_chats_heading_left_of_the_plus(
    client: TestClient, db, registered
):
    _enable_agents(db)
    html = client.get("/chat").text
    actions = html[html.index('id="sidebar-actions"'):html.index('id="sidebar-tree"')]
    assert 'hx-post="/api/folders"' not in actions

    tree = _tree(html)
    head = tree[tree.index(">Chats<"):]
    head = head[:head.index("data-new-chat") + len("data-new-chat")]
    assert 'hx-post="/api/folders"' in head
    assert "data-prompt-field=\"name\"" in head
    assert head.index('hx-post="/api/folders"') < head.index('href="/chat" data-new-chat')
    assert 'class="btn btn--icon btn--sm nav-group__add" type="button"' in head


def test_new_folder_is_in_the_heading_without_the_switch(client: TestClient, db, registered):
    settings_store.update(db, {"enabled": False}, key=settings_store.AGENTS)
    html = client.get("/chat").text
    assert 'id="sidebar-actions"' not in html
    tree = _tree(html)
    head = tree[tree.index(">Chats<"):]
    assert 'hx-post="/api/folders"' in head[:head.index("data-new-chat")]


def test_new_folder_follows_folder_manage(client: TestClient, db, registered):
    settings_store.update(db, {"default_permissions": {"folder.manage": False}})
    _as_stranger(client, db)
    assert 'hx-post="/api/folders"' not in client.get("/chat").text


# --- The per-device filter -------------------------------------------------------------
def _agents_side(client, db):
    _enable_agents(db)
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})


def test_a_device_filters_active_by_default(client: TestClient, db, registered):
    owner = _owner(db)
    device_1 = _device(db, "device-1")
    _chat(db, owner, "Live one", kind="agent", device=device_1)
    _chat(db, owner, "Put away", kind="agent", device=device_1, archived=True)
    _agents_side(client, db)

    tree = _tree(client.get("/chat").text)
    assert "Live one" in tree
    assert "Put away" not in tree
    # The Archived group is gone from this side.
    assert "nav-group--archived" not in tree
    # The filter button sits left of the +, not accented, with three choices.
    head = tree[tree.index(">device-1</span>"):]
    head = head[:head.index('class="nav-item')]
    assert head.index("data-picker-toggle") < head.index(f"device={device_1.id}")
    assert "is-filtered" not in head
    for label in ("All sessions", "Active sessions", "Archived sessions"):
        assert label in head
    assert re.search(r'class="picker__option is-selected"[^>]*aria-checked="true"', head)


def test_the_filter_all_lists_archived_after_active_dimmed(
    client: TestClient, db, registered
):
    owner = _owner(db)
    device_1 = _device(db, "device-1")
    _chat(db, owner, "Old archived", kind="agent", device=device_1, archived=True, ago=1)
    _chat(db, owner, "Older active", kind="agent", device=device_1, ago=5)
    _agents_side(client, db)

    response = client.post(
        "/api/preferences/agent-filter", data={"device": device_1.id, "show": "all"}
    )
    assert response.status_code == 200
    assert 'id="sidebar-tree"' in response.text
    tree = response.text
    assert tree.index("Older active") < tree.index("Old archived")
    row = tree[tree.rindex('<div class="nav-item', 0, tree.index("Old archived")):]
    assert "nav-item--archived" in row[:row.index(">")]
    assert "is-filtered" in tree

    user = _owner(db)
    db.refresh(user)
    assert user.settings_json["agent_filter"] == {device_1.id: "all"}


def test_the_filter_archived_lists_only_archived(client: TestClient, db, registered):
    owner = _owner(db)
    device_1 = _device(db, "device-1")
    _chat(db, owner, "Live one", kind="agent", device=device_1)
    _chat(db, owner, "Put away", kind="agent", device=device_1, archived=True)
    _agents_side(client, db)
    client.post("/api/preferences/agent-filter", data={"device": device_1.id, "show": "archived"})

    tree = _tree(client.get("/chat").text)
    assert "Put away" in tree
    assert "Live one" not in tree

    # Back to the default, which is forgotten rather than stored.
    client.post("/api/preferences/agent-filter", data={"device": device_1.id, "show": "active"})
    user = _owner(db)
    db.refresh(user)
    assert user.settings_json["agent_filter"] == {}


def test_a_device_with_only_archived_chats_still_has_its_section(
    client: TestClient, db, registered
):
    owner = _owner(db)
    laptop = _device(db, "laptop", linked=False)
    _chat(db, owner, "Long gone", kind="agent", device=laptop, archived=True)
    _chat(db, owner, "Orphaned", kind="agent", archived=True)
    _agents_side(client, db)

    tree = _tree(client.get("/chat").text)
    # Listed (offline, and "device missing"), each saying it has no active
    # sessions rather than none at all, and each offering the filter.
    assert ">laptop</span>" in tree
    assert "device missing" in tree
    assert tree.count("No active sessions.") == 2
    assert tree.count("data-picker-toggle") == 2

    client.post("/api/preferences/agent-filter", data={"device": "", "show": "archived"})
    tree = _tree(client.get("/chat").text)
    assert "Orphaned" in tree
    assert "Long gone" not in tree


def test_the_filter_refuses_what_it_cannot_show(client: TestClient, db, registered):
    _agents_side(client, db)
    device_1 = _device(db, "device-1")
    assert client.post(
        "/api/preferences/agent-filter", data={"device": device_1.id, "show": "everything"}
    ).status_code == 400
    assert client.post(
        "/api/preferences/agent-filter", data={"device": "not-a-token", "show": "all"}
    ).status_code == 400
    user = _owner(db)
    db.refresh(user)
    assert "agent_filter" not in (user.settings_json or {})


def test_the_chats_side_archive_holds_only_chats(client: TestClient, db, registered):
    owner = _owner(db)
    _chat(db, owner, "Archived question", archived=True)
    _chat(db, owner, "Archived errand", kind="agent", archived=True)
    _enable_agents(db)

    tree = _tree(client.get("/chat").text)
    archived = tree[tree.index("nav-group--archived"):]
    assert "Archived question" in archived
    assert "Archived errand" not in archived


def test_an_archived_row_is_dimmed_wherever_it_is():
    from pathlib import Path

    import lembas

    css = (Path(lembas.__file__).parent / "web/static/css/app.css").read_text(encoding="utf-8")
    assert ".nav-item--archived { opacity" in css
    assert ".nav-group__add.is-filtered" in css
