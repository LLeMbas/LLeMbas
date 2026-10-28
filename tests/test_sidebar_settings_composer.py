"""The sidebar, settings and composer: what moved, and that nothing was lost.

- the sidebar: the Chats/Agents switch on top, a + beside the heading, a
  section per device on the Agents side, no empty Pinned heading;
- Settings -> Devices (was Connections) and its old address;
- Settings -> Models: a person's own tools and effort per model, which only
  narrow and which a chat's own setting still beats;
- the composer: one settings control (effort slider, More settings), and the
  header's overflow menu gone with everything it held still reachable.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import (
    TOKEN_DEVICE,
    ApiToken,
    Chat,
    Connection,
    Model,
    User,
    UserModelSetting,
)
from lembas.services import device_link, model_prefs, settings_store
from lembas.services import tools as tools_service
from tests.test_schedules_ui import _as_stranger


def _owner(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _model(db, model_id="m", **capabilities) -> Model:
    connection = db.scalar(select(Connection))
    if connection is None:
        connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
        db.add(connection)
        db.commit()
    model = Model(
        connection_id=connection.id,
        model_id=model_id,
        capabilities_json={"tools": True, **capabilities},
    )
    db.add(model)
    db.commit()
    return model


def _conn(db) -> str:
    return db.scalar(select(Model)).connection_id


def _enable_agents(db) -> None:
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)


def _device(db, host: str, *, linked: bool) -> ApiToken:
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


def _agent_chat(db, token: ApiToken | None, title: str, *, ago: int = 0) -> Chat:
    model = db.scalar(select(Model)) or _model(db)
    chat = Chat(
        user_id=_owner(db).id, kind="agent", title=title, model_id=model.model_id,
        connection_id=model.connection_id, device_token_id=token.id if token else None,
        project_dir="/srv/x",
    )
    db.add(chat)
    db.commit()
    chat.updated_at = datetime.now(UTC) - timedelta(minutes=ago)
    db.commit()
    return chat


@pytest.fixture(autouse=True)
def _no_links():
    yield
    device_link.LINKS.clear()


def _tree(html: str) -> str:
    return html[html.index('id="sidebar-tree"'):html.index('class="sidebar__footer"')]


# --- The sidebar -----------------------------------------------------------------
def test_the_switch_is_on_top_where_new_chat_was(client: TestClient, db, registered):
    _model(db)
    _enable_agents(db)
    html = client.get("/chat").text

    actions = html[html.index('id="sidebar-actions"'):html.index('id="sidebar-tree"')]
    assert 'class="kind-switch"' in actions
    assert 'type="radio" name="sidebar_kind"' in actions
    assert "btn--grow" not in html
    # Above the pinned models and the tree, not inside the tree.
    assert html.index('class="kind-switch"') < html.index('id="sidebar-pinned"')
    assert 'class="kind-switch"' not in _tree(html)


def test_the_switch_is_square_and_wears_the_primary_accent():
    from pathlib import Path

    import lembas

    css = (Path(lembas.__file__).parent / "web/static/css/app.css").read_text(encoding="utf-8")
    block = css[css.index(".kind-switch {"):css.index("}", css.index(".kind-switch {"))]
    assert "var(--accent)" in block
    assert "var(--radius-none)" in block
    checked = css.index(".kind-switch__option > input:checked + span")
    assert "background: var(--accent)" in css[checked:css.index("}", checked)]
    # And a tap target: each half is `--control-h`, 44px on a phone.
    span = css.index(".kind-switch__option > span {")
    assert "height: var(--control-h)" in css[span:css.index("}", span)]


def test_the_chats_side_has_a_plus_beside_its_heading(client: TestClient, db, registered):
    _model(db)
    html = client.get("/chat").text
    tree = _tree(html)

    start = tree.index('class="nav-group__head"')
    head = tree[start:tree.index("</a>", start) + 4]
    assert "Chats" in head
    assert 'href="/chat" data-new-chat' in head
    assert 'aria-keyshortcuts="Alt+N"' in head
    assert 'aria-label="New chat"' in head


def test_alt_n_follows_the_plus():
    from pathlib import Path

    import lembas

    js = (Path(lembas.__file__).parent / "web/static/js/commands.js").read_text(encoding="utf-8")
    at = js.index('event.code === "KeyN"')
    assert "[data-new-chat]" in js[at:at + 400]


def test_the_agents_side_has_a_section_per_device(client: TestClient, db, registered):
    _model(db)
    _enable_agents(db)
    first = _device(db, "device-1", linked=True)
    second = _device(db, "device-2", linked=True)
    laptop = _device(db, "laptop", linked=False)
    _device(db, "unused", linked=False)
    _agent_chat(db, first, "On device-1")
    _agent_chat(db, laptop, "On the laptop")
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})

    tree = _tree(client.get("/chat").text)

    # Linked first (by name), then offline ones with chats; a device with
    # neither a link nor a chat is not listed.
    order = [tree.index(f">{name}</span>") for name in ("device-1", "device-2", "laptop")]
    assert order == sorted(order)
    assert ">unused<" not in tree
    assert "Agent chats" not in tree
    # Each linked one has its own +, with the device chosen; the offline one has
    # none, is marked and dimmed.
    assert f'href="/chat?kind=agent&amp;device={first.id}"' in tree
    assert f'href="/chat?kind=agent&amp;device={second.id}"' in tree
    assert f"device={laptop.id}" not in tree
    offline = tree[tree.rindex("nav-group--offline"):]
    assert "offline" in offline and "On the laptop" in offline
    # A linked device with no chats says so rather than vanishing.
    empty_section = tree[tree.index(">device-2</span>"):tree.index(">laptop</span>")]
    assert "No agent chats yet" in empty_section
    # And no instructions while something is linked.
    assert "lembas login" not in tree


def test_the_agents_side_with_nothing_linked_says_how(client: TestClient, db, registered):
    _model(db)
    _enable_agents(db)
    laptop = _device(db, "laptop", linked=False)
    _agent_chat(db, laptop, "Still here")
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})

    tree = _tree(client.get("/chat").text)
    assert "lembas login http://testserver" in tree
    assert 'href="/settings?tab=devices"' in tree
    # The chats on a device that is not linked are still listed under it.
    assert "Still here" in tree


def test_a_plus_opens_the_new_agent_chat_with_its_device_chosen(
    client: TestClient, db, registered
):
    _model(db)
    _enable_agents(db)
    _device(db, "device-1", linked=True)
    second = _device(db, "device-2", linked=True)

    html = client.get(f"/chat?kind=agent&device={second.id}").text
    picker = html[html.index('name="ssh_profile_id"'):]
    picker = picker[:picker.index("</select>")]
    assert re.search(rf'value="device:{second.id}"[^>]*\bselected', picker)
    assert picker.count("selected") == 1
    # The directory is still chosen on the screen, as before.
    assert "data-dir-browse" in html

    # A device that is not one of the linked ones chooses nothing.
    html = client.get("/chat?kind=agent&device=nonsense").text
    picker = html[html.index('name="ssh_profile_id"'):]
    assert "selected" not in picker[:picker.index("</select>")]


def test_reports_and_scheduled_have_left_the_sidebar(client: TestClient, db, registered):
    _model(db)
    html = client.get("/chat").text
    sidebar = html[html.index('<aside class="sidebar"'):html.index("</aside>")]
    assert 'href="/reports"' not in sidebar
    assert 'href="/scheduled"' not in sidebar
    # And the Library went with the library (2.0): files are the file tree's.
    assert 'href="/library/' not in sidebar


def test_no_pinned_heading_without_a_pinned_model(client: TestClient, db, registered):
    """The group is always in the DOM, for the switch's out-of-band swap -- and
    it used to keep the newlines Jinja left in it, so `:empty` never matched and
    an instance with nothing pinned had a gap where the heading would be."""
    model = _model(db)
    html = client.get("/chat").text
    assert "Pinned models" not in html
    # Folded by the heading button every section uses.
    assert '<div class="nav-group nav-group--pinned" id="sidebar-pinned" hidden></div>' in html

    model.pinned = True
    db.commit()
    html = client.get("/chat").text
    assert "Pinned models" in html
    group = html[html.index('id="sidebar-pinned"'):]
    opening = group[:group.index(">")]
    # Shown, and open, which is the default.
    assert "hidden" not in opening
    assert 'data-section="pinned" aria-expanded="true"' in group

    # And the swap after the switch says the same.
    _enable_agents(db)
    model.pinned = False
    db.commit()
    fragment = client.post("/api/preferences/sidebar-kind", data={"kind": "agent"}).text
    assert 'id="sidebar-pinned" hx-swap-oob="true" hidden></div>' in fragment


# --- Settings -> Devices -------------------------------------------------------------
def test_devices_is_the_tab_and_no_longer_a_sidebar_link(client: TestClient, db, registered):
    """Devices is not an entry in the sidebar's footer: it would be a second
    door into one tab of Settings. The tab is there and `?tab=devices` opens
    on it."""
    _enable_agents(db)
    html = client.get("/settings?tab=devices").text
    assert re.search(r'id="tab-devices"\s+checked', html)
    assert ">Devices</" in html.replace(" ", "").replace("\n", "") or "Devices" in html
    assert "CLI connections" not in html
    footer = html[html.index('class="sidebar__footer"'):html.index("</aside>")]
    assert 'href="/settings?tab=devices"' not in footer
    assert 'href="/settings"' in footer


def test_the_old_connections_tab_address_still_lands(client: TestClient, db, registered):
    html = client.get("/settings?tab=connections").text
    assert re.search(r'id="tab-devices"\s+checked', html)
    assert 'id="tab-connections"' not in html


def test_scheduled_is_a_settings_tab(client: TestClient, db, registered):
    html = client.get("/settings?tab=scheduled").text
    assert re.search(r'id="tab-scheduled"\s+checked', html)
    # The tabs and the panels are matched by position (admin.css), and Settings
    # has nine of them for an administrator now: the CSS must reach the ninth.
    tabs = re.findall(r'name="settings-tab" id="(tab-[a-z]+)"', html)
    panels = re.findall(r'data-tab="(tab-[a-z]+)"', html)
    assert tabs == panels
    from pathlib import Path

    import lembas

    css = (Path(lembas.__file__).parent / "web/static/css/admin.css").read_text(encoding="utf-8")
    assert f"input:nth-of-type({len(tabs)}):checked" in css


# --- API-only mode ---------------------------------------------------------------
def test_api_only_hides_and_refuses_the_moved_pages(client: TestClient, db, registered):
    settings_store.update(db, {"instance_mode": "api"})
    html = client.get("/settings?tab=scheduled").text
    assert 'id="tab-scheduled"' not in html
    assert 'href="/library/' not in html[html.index('<aside class="sidebar"'):]
    for path in ("/scheduled",):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/settings?saved=api-only"), path


# --- Settings -> Models: a person's own defaults ---------------------------------
def test_the_models_tab_has_a_section_per_model(client: TestClient, db, registered):
    _model(db, "m", reasoning=True)
    _model(db, "plain")
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)

    html = client.get("/settings?tab=models").text
    card = html[html.index('id="model-defaults"'):]
    assert card.count('action="/api/preferences/models"') == 2
    assert 'name="tools_on" value="web_search"' in card
    # Effort only on the reasoning model, with only its levels plus off.
    first = card[:card.index('name="model_id" value="plain"')]
    assert 'name="reasoning_effort"' in first
    plain = card[card.index('name="model_id" value="plain"'):]
    assert 'name="reasoning_effort"' not in plain[:plain.index("</form>")]


def test_switching_a_tool_off_for_a_model_narrows_every_chat_with_it(
    client: TestClient, db, registered
):
    _model(db, "m")
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    owner = _owner(db)
    chat = Chat(user_id=owner.id, model_id="m", connection_id=_conn(db))
    db.add(chat)
    db.commit()
    assert "web_search" in tools_service.resolve_tools(db, chat, owner).by_name

    offered = {tool["gate"] for tool in model_prefs.offered(db, owner, db.scalar(select(Model)))}
    response = client.post(
        "/api/preferences/models",
        data={"model_id": "m", "tools_on": sorted(offered - {"web_search"})},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "tab=models" in response.headers["location"]

    db.expire_all()
    assert "web_search" not in tools_service.resolve_tools(db, chat, owner).by_name
    # Shown as off in the chat's own switches too.
    html = client.get(f"/chat/{chat.id}").text
    assert re.search(
        r'<input type="checkbox" name="on" value="true"\s+hx-post="[^"]+/scope"[^>]*'
        r'"name": "web_search"', html
    )


def test_a_person_cannot_switch_on_what_the_instance_does_not_offer(
    client: TestClient, db, registered
):
    """Narrowing only: a gate the instance has off is not listed, and naming it
    in a crafted POST neither offers it nor stores anything."""
    _model(db, "m")
    owner = _owner(db)
    offered = {tool["gate"] for tool in model_prefs.offered(db, owner, db.scalar(select(Model)))}
    assert "web_search" not in offered  # search is off on this instance

    client.post(
        "/api/preferences/models",
        data={"model_id": "m", "tools_on": [*offered, "web_search"]},
    )
    chat = Chat(user_id=owner.id, model_id="m", connection_id=_conn(db))
    db.add(chat)
    db.commit()
    assert "web_search" not in tools_service.resolve_tools(db, chat, owner).by_name
    # Everything ticked means nothing stored.
    assert db.scalar(select(UserModelSetting)) is None


def test_another_persons_default_does_not_reach_you(client: TestClient, db, registered):
    _model(db, "m")
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    owner = _owner(db)
    db.add(UserModelSetting(user_id=owner.id, model_id="m", tools_off_json=["web_search"]))
    db.commit()
    stranger = _as_stranger(client, db)
    chat = Chat(user_id=stranger.id, model_id="m", connection_id=_conn(db))
    db.add(chat)
    db.commit()
    without = set(tools_service.resolve_tools(db, chat, stranger, user_defaults=False).by_name)
    assert "web_search" in without
    assert set(tools_service.resolve_tools(db, chat, stranger).by_name) == without


def test_a_chat_switching_a_tool_back_on_beats_the_default(
    client: TestClient, db, registered
):
    _model(db, "m")
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    owner = _owner(db)
    db.add(UserModelSetting(user_id=owner.id, model_id="m", tools_off_json=["web_search"]))
    chat = Chat(user_id=owner.id, model_id="m", connection_id=_conn(db))
    db.add(chat)
    db.commit()
    assert "web_search" not in tools_service.resolve_tools(db, chat, owner).by_name

    client.post(
        f"/api/chats/{chat.id}/scope",
        data={"kind": "family", "name": "web_search", "on": "true"},
    )
    db.expire_all()
    chat = db.get(Chat, chat.id)
    assert chat.scope_json["families"]["web_search"] is True
    assert "web_search" in tools_service.resolve_tools(db, chat, owner).by_name

    # Off again in the chat is off, whatever the default.
    client.post(f"/api/chats/{chat.id}/scope", data={"kind": "family", "name": "web_search"})
    db.expire_all()
    assert "web_search" not in tools_service.resolve_tools(db, db.get(Chat, chat.id), owner).by_name


def test_ticking_a_defaulted_tool_on_the_new_chat_screen_wins(
    client: TestClient, db, registered
):
    _model(db, "m")
    settings_store.update(db, {"enabled": True}, key=settings_store.SEARCH)
    owner = _owner(db)
    db.add(UserModelSetting(user_id=owner.id, model_id="m", tools_off_json=["web_search"]))
    db.commit()

    # The prospective switch shows the default...
    html = client.get("/chat").text
    assert re.search(r'name="scope_on" value="web_search" form="composer-form"\s+>', html)
    # ...and ticking it anyway is stored as an explicit on.
    client.post(
        "/api/chats/start",
        data={"content": "hi", "model_id": "m", "scope_all": ["web_search"],
              "scope_on": ["web_search"]},
    )
    chat = db.scalars(select(Chat).order_by(Chat.created_at.desc())).first()
    assert chat.scope_json["families"]["web_search"] is True
    assert "web_search" in tools_service.resolve_tools(db, chat, owner).by_name


def test_the_effort_default_seeds_a_new_chat_and_the_chat_wins(
    client: TestClient, db, registered
):
    model = _model(db, "m", reasoning=True)
    model.params_json = {"reasoning_effort": "high"}
    db.commit()

    client.post("/api/preferences/models", data={"model_id": "m", "reasoning_effort": "low",
                                                 "tools_on": []})
    owner = _owner(db)
    assert model_prefs.effort_choice(db, owner, model) == "low"

    # The new-chat screen starts the slider there.
    html = client.get("/chat").text
    assert re.search(r'<option value="low" selected>', html)

    # A chat started without choosing starts at the person's default...
    client.post("/api/chats/start", data={"content": "hi", "model_id": "m"})
    chat = db.scalars(select(Chat).order_by(Chat.created_at.desc())).first()
    assert chat.params_json["reasoning_effort"] == "low"

    # ...and one that chose wins over it.
    client.post("/api/chats/start", data={"content": "hi", "model_id": "m",
                                          "reasoning_effort": "medium"})
    chat = db.scalars(select(Chat).order_by(Chat.created_at.desc())).first()
    assert chat.params_json["reasoning_effort"] == "medium"

    # "off" as a default sends none.
    client.post("/api/preferences/models", data={"model_id": "m", "reasoning_effort": "off"})
    client.post("/api/chats/start", data={"content": "hi", "model_id": "m"})
    chat = db.scalars(select(Chat).order_by(Chat.created_at.desc())).first()
    assert "reasoning_effort" not in chat.params_json


def test_an_effort_the_model_does_not_take_is_not_stored(client: TestClient, db, registered):
    model = _model(db, "m", reasoning=True)
    model.reasoning_efforts = ["low", "medium", "xhigh"]
    _model(db, "plain")
    db.commit()

    owner = _owner(db)
    # The form posts every tool still ticked; all of them, here.
    ticked = {
        name: [t["gate"] for t in model_prefs.offered(db, owner, row)]
        for name, row in (
            ("m", model), ("plain", db.scalar(select(Model).filter_by(model_id="plain")))
        )
    }
    client.post("/api/preferences/models",
                data={"model_id": "m", "reasoning_effort": "high", "tools_on": ticked["m"]})
    client.post("/api/preferences/models",
                data={"model_id": "plain", "reasoning_effort": "low", "tools_on": ticked["plain"]})
    assert db.scalar(select(UserModelSetting)) is None

    client.post("/api/preferences/models",
                data={"model_id": "m", "reasoning_effort": "xhigh", "tools_on": ticked["m"]})
    assert db.scalar(select(UserModelSetting)).reasoning_effort == "xhigh"


def test_a_model_one_cannot_reach_is_refused(client: TestClient, db, registered):
    response = client.post(
        "/api/preferences/models", data={"model_id": "nope"}, follow_redirects=False
    )
    assert "error=" in response.headers["location"]
    assert db.scalar(select(UserModelSetting)) is None


def test_switching_model_seeds_the_persons_default(client: TestClient, db, registered):
    _model(db, "a", reasoning=True)
    b = _model(db, "b", reasoning=True)
    b.params_json = {"reasoning_effort": "high"}
    db.commit()
    owner = _owner(db)
    db.add(UserModelSetting(user_id=owner.id, model_id="b", reasoning_effort="low"))
    chat = Chat(user_id=owner.id, model_id="a", connection_id=b.connection_id, params_json={})
    db.add(chat)
    db.commit()

    client.patch(f"/api/chats/{chat.id}", data={"model_id": "b"})
    db.expire_all()
    assert db.get(Chat, chat.id).params_json["reasoning_effort"] == "low"


# --- The composer and the header -------------------------------------------------
def test_the_composer_popover_is_an_effort_slider_and_more_settings(
    client: TestClient, db, registered, make_chat
):
    _model(db, "m", reasoning=True)
    chat_id = make_chat()
    html = client.get(f"/chat/{chat_id}").text
    form_at = html.index('id="composer-form"')
    composer = html[html.index('class="composer"'):html.index("</form>", form_at)]

    menu = composer[composer.index("picker__menu--settings"):]
    assert 'type="range"' in menu
    assert 'data-levels=\'["off", "low", "medium", "high"]\'' in menu
    assert 'min="0" max="3"' in menu
    assert "aria-valuetext=" in menu
    assert ">More settings<" in menu
    assert 'data-toggle="#chat-settings"' in menu
    # The state is still the one select the slash command and htmx use.
    assert menu.count('data-effort ') + menu.count('data-effort\n') >= 1
    assert 'hx-patch="/api/chats/' in menu
    # Nothing else in it: no tool switches, no skills.
    assert '"kind": "family"' not in menu
    # The old tools button is gone.
    assert 'aria-label="Toggle"' not in html


def test_without_a_reasoning_model_the_button_opens_the_panel(
    client: TestClient, db, registered, make_chat
):
    _model(db, "m")
    chat_id = make_chat()
    html = client.get(f"/chat/{chat_id}").text
    assert 'type="range"' not in html
    assert re.search(
        r'<button class="btn btn--icon composer__btn" type="button"\s+'
        r'data-toggle="#chat-settings" data-toggle-group="side"', html
    )


def test_the_header_has_no_settings_button_and_nothing_was_lost(
    client: TestClient, db, registered
):
    """The overflow menu held Keep this chat, Chat settings and Compact. All
    three are in the settings panel now, and the prompt, knowledge and sampling
    fields it opened are there too."""
    from lembas.db.models import Message

    _model(db, "m")
    owner = _owner(db)
    chat = Chat(user_id=owner.id, model_id="m", connection_id=_conn(db),
                temporary=True)
    db.add(chat)
    db.commit()
    db.add(Message(chat_id=chat.id, role="user", content="hello"))
    db.commit()

    html = client.get(f"/chat/{chat.id}").text
    header = html[html.index('<header class="topbar">'):html.index("</header>")]
    assert 'data-icon="dots"' not in header and "#i-dots" not in header
    assert 'data-toggle="#chat-settings"' not in header
    assert 'aria-label="More"' not in header

    panel = html[html.index('<aside class="chat-settings" id="chat-settings"'):]
    panel = panel[:panel.index("</aside>")]
    for needed in (f"/api/chats/{chat.id}/keep", 'name="system_prompt"', 'name="temperature"',
                   'name="top_p"', 'name="max_tokens"', "run('compact')"):
        assert needed in panel, needed
    # And on a phone the window switch reaches it.
    assert 'data-window="#chat-settings"' in html


def test_the_panel_is_in_the_side_group(client: TestClient, db, registered, make_chat):
    """One side panel at a time: the composer's toggles carry the group."""
    _model(db, "m", reasoning=True)
    chat_id = make_chat()
    html = client.get(f"/chat/{chat_id}").text
    assert 'data-toggle="#chat-settings" data-toggle-group="side"' in html


def test_every_script_scope_that_says_words_defines_t():
    """Two scopes in ui.js called `t()` with no `t` in them -- the notifications
    switch and the agent picker's directory label threw a ReferenceError, and
    the picker's error took the new-agent-chat directory step with it."""
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).parent / "web/static/js"
    for path in sorted(root.glob("*.js")):
        source = path.read_text(encoding="utf-8")
        for scope in re.split(r"\n\(function \(\) \{", source)[1:]:
            body = scope.split("\n})();", 1)[0]
            if re.search(r"[^\w.]t\(\s*[\"']", body):
                assert re.search(r"\bvar t = ", body), (path.name, body[:120])
