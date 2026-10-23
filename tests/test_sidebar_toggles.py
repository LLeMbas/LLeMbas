"""Every sidebar section folds from its heading, and the agent
composer's connection chooser opens.

- `[ PINNED MODELS ]`, `[ CHATS ]`, each folder, each device on the Agents side
  and Archived each have one button -- the name and the chevron together -- that
  folds the section. A root folder's chevron drawn *beside* its button is an
  arrow, the one part that says "this folds", that does nothing when pressed.
- What somebody folded is kept on the account and rendered from it, so neither
  a reload nor an out-of-band swap of the tree undoes it.
- Folders are listed below `[ CHATS ]`, not above it.
- The connection chooser's menu, opened inside `.composer__context`, which
  scrolls sideways and therefore clips in both axes, would be there and
  invisible. It is placed against the window.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lembas
from lembas.services import device_link
from tests.test_schedules_ui import _as_stranger
from tests.test_sidebar_search import (
    _chat,
    _device,
    _enable_agents,
    _folder,
    _owner,
    _pin_a_model,
    _tree,
)

WEB = Path(lembas.__file__).parent / "web"


@pytest.fixture(autouse=True)
def _no_links():
    yield
    device_link.LINKS.clear()


def _toggles(html: str) -> list[str]:
    """Every section-heading button in the page, whole."""
    return re.findall(r"<button\b[^>]*\bdata-section=\"[^\"]*\".*?</button>", html, re.S)


def _toggle(html: str, key: str) -> str:
    for button in _toggles(html):
        if f'data-section="{key}"' in button:
            return button
    raise AssertionError(f"no toggle for {key}")


def _state(html: str, key: str) -> tuple[bool, bool]:
    """(aria-expanded, body shown) for one section, as the server drew it."""
    button = _toggle(html, key)
    expanded = 'aria-expanded="true"' in button
    body_id = re.search(r'aria-controls="([^"]+)"', button).group(1)
    opening = re.search(rf'<[^>]*\bid="{re.escape(body_id)}"[^>]*>', html).group(0)
    return expanded, not re.search(r"\shidden\b", opening)


def _post(client: TestClient, key: str, state: str) -> int:
    return client.post("/api/preferences/section", data={"key": key, "state": state}).status_code


# --- The arrow is part of the control ---------------------------------------------
def test_every_heading_carries_its_chevron_inside_its_toggle(
    client: TestClient, db, registered
):
    _pin_a_model(db)
    owner = _owner(db)
    top = _folder(db, owner, "Quest")
    _folder(db, owner, "Side quest", parent=top)
    _chat(db, owner, "Put away", archived=True)

    html = client.get("/chat").text
    keys = {re.search(r'data-section="([^"]+)"', b).group(1) for b in _toggles(html)}
    assert {"pinned", "chats", "archived", f"folder:{top.id}"} <= keys
    for button in _toggles(html):
        # A real button, announced, pointing at what it folds -- and the arrow
        # inside it, so pressing the arrow is pressing the button.
        assert button.startswith("<button") and 'type="button"' in button
        assert re.search(r'aria-expanded="(true|false)"', button)
        assert 'aria-controls="section-' in button
        assert "chevron" in button
        # Nothing else that acts lives inside it: the +, rename, filter... are
        # siblings, so pressing them never folds anything.
        assert "<a " not in button and "hx-" not in button and 'href="/' not in button
        assert button.count("<button") == 1

    # The old shape, a chevron span after the button, is gone.
    assert not re.search(r"</button>\s*<span class=\"folder__chevron\"", html)


def test_a_device_heading_folds_and_its_filter_and_plus_are_outside_it(
    client: TestClient, db, registered
):
    _enable_agents(db)
    device_1 = _device(db, "device-1")
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    tree = _tree(client.get("/chat").text)
    button = _toggle(tree, f"device:{device_1.id}")
    assert ">device-1</span>" in button
    assert "data-picker-toggle" not in button
    assert 'href="/' not in button
    head = tree[tree.index(button):]
    head = head[:head.index("</div>\n    <div class=\"nav-group__body\"")]
    assert "data-picker-toggle" in head and f"device={device_1.id}" in head


def test_the_hover_state_and_rotation_are_styled_off_the_button():
    css = (WEB / "static/css/app.css").read_text(encoding="utf-8")
    assert ".nav-group__toggle:hover" in css
    assert '.nav-group__toggle[aria-expanded="false"] .nav-group__chevron' in css
    assert ".nav-group__toggle:focus-visible" in css


# --- Remembered, per section --------------------------------------------------------
@pytest.mark.parametrize("key, default", [("chats", True), ("pinned", True), ("archived", False)])
def test_each_named_section_remembers_its_state(
    client: TestClient, db, registered, key, default
):
    _pin_a_model(db)
    owner = _owner(db)
    _chat(db, owner, "Loose")
    _chat(db, owner, "Put away", archived=True)

    assert _state(client.get("/chat").text, key) == (default, default)
    assert _post(client, key, "1" if not default else "0") == 204
    flipped = not default
    assert _state(client.get("/chat").text, key) == (flipped, flipped)
    db.refresh(owner)
    assert owner.settings_json["sections"] == {key: flipped}

    # The tree the Chats/Agents switch sends back says the same.
    _enable_agents(db)
    fragment = client.post("/api/preferences/sidebar-kind", data={"kind": "chat"}).text
    assert _state(fragment, key) == (flipped, flipped)

    # Back to the default is forgotten rather than stored.
    assert _post(client, key, "1" if default else "0") == 204
    db.refresh(owner)
    assert owner.settings_json["sections"] == {}


def test_a_folder_remembers_on_its_own_row(client: TestClient, db, registered):
    owner = _owner(db)
    top = _folder(db, owner, "Quest")
    child = _folder(db, owner, "Side quest", parent=top)

    for folder in (top, child):
        assert _post(client, f"folder:{folder.id}", "0") == 204
        db.refresh(folder)
        assert folder.collapsed is True
        assert _state(_tree(client.get("/chat").text), f"folder:{folder.id}") == (False, False)
        assert _post(client, f"folder:{folder.id}", "1") == 204
        db.refresh(folder)
        assert folder.collapsed is False


def test_a_device_section_remembers_and_survives_the_trees_own_swaps(
    client: TestClient, db, registered
):
    _enable_agents(db)
    owner = _owner(db)
    device_1 = _device(db, "device-1")
    _chat(db, owner, "On device-1", kind="agent", device=device_1)
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})

    key = f"device:{device_1.id}"
    assert _state(client.get("/chat").text, key) == (True, True)
    assert _post(client, key, "0") == 204
    assert _state(client.get("/chat").text, key) == (False, False)
    # A device's filter re-renders the tree; the fold must come back with it.
    fragment = client.post(
        "/api/preferences/agent-filter", data={"device": device_1.id, "show": "all"}
    ).text
    assert _state(fragment, key) == (False, False)
    # And the "device missing" section has a key of its own.
    assert _post(client, "device:", "0") == 204


def test_a_key_for_something_not_yours_is_refused(client: TestClient, db, registered):
    owner = _owner(db)
    mine = _folder(db, owner, "Mine")
    device_1 = _device(db, "device-1")
    for key in ("", "nonsense", "folder:", "folder:nope", "device:nope"):
        assert _post(client, key, "0") == 400, key

    _as_stranger(client, db)
    assert _post(client, f"folder:{mine.id}", "0") == 400
    assert _post(client, f"device:{device_1.id}", "0") == 400
    db.refresh(mine)
    assert mine.collapsed is False


def test_the_unread_poll_tree_keeps_what_was_folded(client: TestClient, db, registered):
    """A session shared from a terminal brings the whole tree along out of band
    (`/api/chats/unread`). That tree is rendered from the stored state."""
    _enable_agents(db)
    owner = _owner(db)
    device_1 = _device(db, "device-1")
    client.post("/api/preferences/sidebar-kind", data={"kind": "agent"})
    _post(client, f"device:{device_1.id}", "0")
    shared = _chat(db, owner, "From the terminal", kind="agent", device=device_1)
    shared.device_session_id = "s-1"
    shared.unread = True
    db.commit()

    fragment = client.get("/api/chats/unread").text
    if 'id="sidebar-tree" hx-swap-oob' not in fragment:
        pytest.skip("this poll did not bring the tree along")
    assert _state(fragment, f"device:{device_1.id}") == (False, False)


def test_app_js_reapplies_this_tabs_choices_after_a_swap():
    js = (WEB / "static/js/app.js").read_text(encoding="utf-8")
    at = js.index("function reapply(")
    # Every settle and out-of-band swap except a reply's stream frames, which
    # carry no section toggles (and come a dozen times a second).
    for event in ("htmx:afterSettle", "htmx:oobAfterSwap"):
        assert (
            f'addEventListener("{event}", function (event) {{\n'
            "    if (!isStreamFrame(event)) reapply(document);"
        ) in js
    assert "folded[key]" in js[at:]


# --- Folders below `[ CHATS ]` ------------------------------------------------------
def test_folders_come_after_chats_and_before_archived(client: TestClient, db, registered):
    owner = _owner(db)
    _folder(db, owner, "Quest")
    _chat(db, owner, "Loose")
    _chat(db, owner, "Put away", archived=True)
    tree = _tree(client.get("/chat").text)
    assert tree.index('data-section="chats"') < tree.index(">Quest<") < tree.index(
        'data-section="archived"'
    )


# --- The connection chooser ---------------------------------------------------------
def test_the_connection_chooser_lists_every_linked_device(client: TestClient, db, registered):
    _enable_agents(db)
    _pin_a_model(db)  # a composer is only drawn with a model to send to
    for host in ("device-1", "device-2", "device-3"):
        _device(db, host)
    _device(db, "old-laptop", linked=False)
    html = client.get("/chat?kind=agent").text
    select = html[html.index("composer__connection"):]
    select = select[:select.index("</select>")]
    assert "data-upward" in select
    names = re.findall(r">([\w-]+)</option>", select)
    assert sorted(names) == ["device-1", "device-2", "device-3"]


def test_the_upward_menu_is_placed_against_the_window():
    """The root cause, pinned: `.composer__context` scrolls sideways, so it clips
    in both axes, and a menu absolutely placed above the row inside it is cut
    away whole. `place()` takes it out of that box with `position: fixed`."""
    chat_css = (WEB / "static/css/chat.css").read_text(encoding="utf-8")
    context = re.search(r"\.composer__context \{([^}]*)\}", chat_css).group(1)
    assert "overflow-x: auto" in context  # why the menu has to escape
    floating = re.search(r"\.picker--up \.upward__menu--floating \{([^}]*)\}", chat_css)
    assert floating and "position: fixed" in floating.group(1)
    # The button keeps the select's floor, or a phone squeezes it to a caret.
    assert re.search(r"\.composer__context \.upward \{[^}]*min-width: 6rem", chat_css)

    ui = (WEB / "static/js/ui.js").read_text(encoding="utf-8")
    builder = ui[ui.index("function build(select)"):]
    builder = builder[:builder.index("function scan(root)")]
    opening = builder[builder.index("function open()"):builder.index("function close()")]
    assert "place();" in opening
    assert 'classList.add("upward__menu--floating")' in builder
    assert "preventScroll: true" in opening


# --- The same, in a browser --------------------------------------------------------
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")

PROBE = """
<style>
  /* The drawer slides in; under --virtual-time-budget a transition does not
     run on the clock the probe's timers do, so it would be measured mid-way. */
  *, *::before, *::after { transition: none !important; }
</style>
<script>
window.addEventListener('load', function () {
  var out = {sections: []};
  /* On a phone the sidebar is a drawer: opened for the headings, and shut
     again before the composer is pressed, as a person would. */
  var drawer = document.querySelector('.sidebar-toggle');
  var narrow = window.innerWidth < 1024 && drawer;
  function hitAt(el) {
    var r = el.getBoundingClientRect();
    return document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
  }
  function state(button) {
    var body = document.getElementById(button.getAttribute('aria-controls'));
    return [button.getAttribute('aria-expanded'), !!body && !body.hidden];
  }
  function press(el) {
    el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
  }
  function headings() {
    /* Each heading: press its chevron, then its name -- where a hand goes. */
    document.querySelectorAll('[data-section]').forEach(function (button) {
      var row = {key: button.dataset.section, before: state(button)};
      var hit = hitAt(button.querySelector('.nav-group__chevron, .folder__chevron'));
      row.chevronHitsButton = !!hit && button.contains(hit);
      row.hit = hit ? hit.tagName + "." + (hit.getAttribute("class") || "") : null;
      if (hit) press(hit);
      row.afterChevron = state(button);
      var name = hitAt(button.querySelector('.nav-group__name, .nav-item__label'));
      if (name) press(name);
      row.afterName = state(button);
      out.sections.push(row);
    });
  }
  function chooser() {
    var button = document.querySelector('.upward__button');
    if (button) {
      press(hitAt(button));
      var menu = button.parentNode.querySelector('.upward__menu');
      var r = menu.getBoundingClientRect();
      var hit = document.elementFromPoint(r.x + r.width / 2, r.y + 8);
      out.chooser = {open: !menu.hidden, width: r.width,
                     buttonWidth: button.getBoundingClientRect().width,
                     visible: !!hit && menu.contains(hit),
                     items: menu.querySelectorAll('.picker__option').length};
    }
    window.__probe = out;
  }
  setTimeout(function () {
    if (narrow) drawer.click();
    setTimeout(function () {
      headings();
      if (narrow) drawer.click();
      setTimeout(chooser, 400);
    }, 400);
  }, 200);
});
</script>
"""


def _run_in_chromium(client: TestClient, path: str, width: int, height: int, tmp: Path) -> dict:
    spec = importlib.util.spec_from_file_location(
        "shoot", Path(__file__).resolve().parent.parent / "scripts/shoot.py"
    )
    shoot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shoot)

    response = client.get(path)
    assert response.status_code == 200
    assets = tmp / "assets"
    assets.mkdir(exist_ok=True)
    html = shoot.rewrite(response.text, client, assets).replace("</body>", PROBE + "</body>", 1)
    page = tmp / f"page-{width}.html"
    page.write_text(html, encoding="utf-8")
    # Inside an iframe of the exact size: headless Chromium will not make a
    # window narrower than ~500px (see `shoot.shoot`).
    frame = tmp / f"frame-{width}.html"
    frame.write_text(
        "<!doctype html><meta charset=utf-8><style>html,body{margin:0}"
        f"iframe{{width:{width}px;height:{height}px;border:0;display:block}}</style>"
        f'<iframe id="f" src="{page.name}"></iframe><pre id="out"></pre><script>'
        "window.addEventListener('load',function(){setTimeout(function(){"
        "document.getElementById('out').textContent="
        "JSON.stringify(document.getElementById('f').contentWindow.__probe||null);"
        "},2500);});</script>",
        encoding="utf-8",
    )
    dom = subprocess.run(
        [CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu",
         "--allow-file-access-from-files", f"--window-size={max(width, 520)},{height + 40}",
         "--virtual-time-budget=8000", "--dump-dom", f"file://{frame.resolve()}"],
        capture_output=True, text=True, timeout=120,
    ).stdout
    found = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    assert found and found.group(1).strip() not in ("", "null"), "the probe did not report"
    return json.loads(found.group(1).replace("&amp;", "&").replace("&quot;", '"'))


@pytest.mark.skipif(not CHROMIUM, reason="no chromium on this machine")
@pytest.mark.parametrize("width, height", [(1440, 900), (390, 844)])
def test_in_a_browser_the_arrow_and_the_name_fold_and_the_chooser_opens(
    client: TestClient, db, registered, tmp_path, width, height
):
    _enable_agents(db)
    _pin_a_model(db)
    owner = _owner(db)
    top = _folder(db, owner, "Quest")
    _folder(db, owner, "Side quest", parent=top)
    _chat(db, owner, "Put away", archived=True)
    _device(db, "device-1")
    _device(db, "device-2")

    result = _run_in_chromium(client, "/chat?kind=agent", width, height, tmp_path)
    assert {row["key"] for row in result["sections"]} >= {"pinned", "chats", "archived"}
    for row in result["sections"]:
        assert row["chevronHitsButton"], row
        assert row["afterChevron"][0] != row["before"][0], row
        assert row["afterChevron"][1] != row["before"][1], row
        assert row["afterName"] == row["before"], row

    chooser = result["chooser"]
    assert chooser["open"] and chooser["items"] == 2
    assert chooser["visible"], "the menu opened but nothing of it can be seen"
    assert chooser["buttonWidth"] >= 90
