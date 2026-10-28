"""Settings as a window, its tabs down the side, and what is not in the sidebar.

- `/settings?embed=1` (or any request a browser makes for a frame) is the same
  page without the shell, for the settings window ui.js opens on a desktop;
  `/settings` on its own is still the full page;
- the sidebar's footer has no Devices entry and the Library no Memory tab --
  both would be second doors into a tab of Settings;
- the Library's tab bar starts where its column does, as Settings' does;
- Settings' tabs are a list down the side wherever its own width allows;
- the composer's settings button is an icon button, the attach button's twin.

There is no JavaScript runner here (hard rule 1 keeps Node out of the project),
so the window's behaviour was driven by hand under a DOM stub before
committing, and what the suite pins is the shape of the code that carries it.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

import lembas

from .conftest import js_code, script
from .test_sidebar_settings_composer import _enable_agents, _model

ROOT = Path(lembas.__file__).parent / "web"
ADMIN_CSS = (ROOT / "static/css/admin.css").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static/css/app.css").read_text(encoding="utf-8")
UI = js_code(script("ui.js"))


def _window_code() -> str:
    """The settings window's IIFE, comments blanked."""
    start = UI.index('var PHONE = "(max-width: 48rem)"')
    return UI[start : UI.index("})();", start)]


# --- The page, and the page in a frame ------------------------------------------
def test_the_full_page_still_has_the_shell(client: TestClient, registered):
    html = client.get("/settings").text
    assert '<aside class="sidebar"' in html
    assert 'class="topbar"' in html
    assert "data-settings-embed" not in html


def test_embed_renders_without_the_sidebar_or_topbar(client: TestClient, registered):
    html = client.get("/settings?embed=1&tab=appearance").text
    assert '<aside class="sidebar"' not in html
    assert 'class="topbar"' not in html
    assert "data-settings-embed" in html
    # Still the whole of Settings: the tabs, the chosen one, the forms.
    assert re.search(r'id="tab-appearance"\s+checked', html)
    assert 'action="/api/preferences/language"' in html


def test_a_frame_gets_the_slim_page_without_asking(client: TestClient, registered):
    """Every settings form redirects to a plain `/settings?saved=…`. The
    browser's `Sec-Fetch-Dest: iframe` on that redirect is what keeps the
    window's frame slim without threading `embed` through every route."""
    html = client.get("/settings?saved=x", headers={"Sec-Fetch-Dest": "iframe"}).text
    assert '<aside class="sidebar"' not in html
    assert "data-settings-embed" in html
    page = client.get("/settings?saved=x", headers={"Sec-Fetch-Dest": "document"}).text
    assert '<aside class="sidebar"' in page


def test_same_origin_framing_is_allowed_and_nothing_else_is(client: TestClient, registered):
    response = client.get("/settings?embed=1")
    assert "frame-ancestors 'self'" in response.headers["content-security-policy"]
    assert "x-frame-options" not in {key.lower() for key in response.headers}


def test_forms_that_answer_with_another_page_leave_the_frame(client: TestClient, registered):
    """Signing out, making a token and entering a device code answer with a
    page that is not Settings; in the window that page belongs in the whole
    window. Everything else redirects back to Settings and stays in the frame."""
    html = client.get("/settings?embed=1").text
    assert re.search(r'<form method="post" action="/auth/logout" target="_top">', html)
    for form in re.findall(r"<form[^>]*>", html):
        if 'action="/api/preferences' in form or 'action="/api/memories' in form:
            assert "target=" not in form


# --- What left the sidebar and the Library --------------------------------------
def test_the_sidebar_footer_has_no_devices_link(client: TestClient, db, registered):
    _enable_agents(db)
    html = client.get("/chat").text
    footer = html[html.index('class="sidebar__footer"') : html.index("</aside>")]
    assert "tab=devices" not in footer
    assert ">Devices<" not in footer
    # The reader's own entry is still there, and is a plain link to /settings.
    assert 'href="/settings"' in footer


def test_the_library_bar_starts_at_its_column():
    """One rule for both bars that sit directly in `.main`: Settings' inside
    `.tabs`, the Library's on its own. The inset is computed from the column's
    own measure and padding, and both scrollers reserve the gutter on both
    sides so the column is centred on the line the bar measures from."""
    start = ADMIN_CSS.index(".main > .tabs > .tabs__bar,")
    rule = ADMIN_CSS[start : ADMIN_CSS.index("}", start)]
    assert ".main > .tabs__bar {" in rule
    assert "var(--thread-max-width)" in rule
    assert "padding-inline: var(--tabs-inset)" in rule
    gutter = ADMIN_CSS[ADMIN_CSS.index(".main > .tabs > .tabs__body,") :]
    gutter = gutter[: gutter.index("}")]
    assert ".main > .tabs__bar + .admin-scroll" in gutter
    assert "scrollbar-gutter: stable both-edges" in gutter


# --- Tabs down the side ---------------------------------------------------------
def test_settings_has_the_side_tab_list(client: TestClient, registered):
    html = client.get("/settings").text
    assert 'class="main main--settings"' in html
    assert 'class="tabs tabs--side"' in html
    # Same radios and labels as the strip: the panel-by-position rule still
    # applies, whichever way the list runs.
    bar = html[html.index('class="tabs__bar"') : html.index('class="tabs__body"')]
    assert bar.count('type="radio" name="settings-tab"') >= 4


def test_the_side_list_is_a_grid_with_a_known_track():
    """Decided by the settings area's own width (a size container), because the
    page beside a sidebar and the frame in the window differ at one window
    width. The nav is a fixed track; the body takes the rest and stays bounded."""
    main = ADMIN_CSS[ADMIN_CSS.index(".main--settings {") :]
    main = main[: main.index("}")]
    assert "container-type: inline-size" in main
    assert "--settings-nav-width:" in main

    block = ADMIN_CSS[ADMIN_CSS.index("@container (min-width: 40rem)") :]
    grid = block[block.index(".main--settings > .tabs--side {") :]
    grid = grid[: grid.index("}")]
    assert "display: grid" in grid
    assert "grid-template-columns: var(--settings-nav-width) minmax(0, 1fr)" in grid
    assert "grid-template-rows: minmax(0, 1fr)" in grid

    bar = block[block.index(".main--settings > .tabs--side > .tabs__bar {") :]
    bar = bar[: bar.index("}")]
    assert "flex-direction: column" in bar
    # The active tab carries the sidebar's accent bar.
    active = block[block.index("input:checked + .tabs__tab {") :]
    active = active[: active.index("}")]
    assert "inset var(--border-w-accent) 0 0 var(--accent)" in active


# --- The window -----------------------------------------------------------------
def test_the_window_is_a_native_modal_dialog_of_a_known_size():
    code = _window_code()
    assert 'createElement("dialog")' in code
    assert "showModal()" in code
    assert '"dialog settings-window"' in code
    assert 'createElement("iframe")' in code
    assert 'searchParams.set("embed", "1")' in code

    rule = APP_CSS[APP_CSS.index(".settings-window {") :]
    rule = rule[: rule.index("}")]
    assert "--settings-window-w: min(" in rule
    assert "--settings-window-h: min(" in rule
    assert "border-radius: 0" in rule
    # Only an open dialog is a grid: a bare display would show it shut.
    assert ".settings-window[open] {" in APP_CSS
    assert "\n.settings-window {\n  display" not in APP_CSS


def test_settings_links_open_the_window_only_on_a_plain_desktop_click():
    """Desktop opens the window; a phone, a modified or middle click, a link
    with a target or `data-settings-page`, and /settings itself navigate."""
    code = _window_code()
    click = code[code.rindex('document.addEventListener("click"') :]
    click = click[: click.index("});")]
    assert "event.button !== 0" in click
    for key in ("metaKey", "ctrlKey", "shiftKey", "altKey"):
        assert key in click
    assert "link.target" in click
    assert 'hasAttribute("data-settings-page")' in click
    assert "event.preventDefault()" in click

    opener = code[code.index("function openSettings(") :]
    opener = opener[: opener.index("function closeSettings(")]
    assert "window.matchMedia(PHONE).matches" in opener
    assert 'window.location.pathname === "/settings"' in opener
    assert "return false" in opener


def test_the_window_reloads_what_is_under_it_after_a_save():
    code = _window_code()
    arrived = code[code.index("function arrived(") : code.index("function closed(")]
    assert "saved=" in arrived
    assert "changed = true" in arrived
    # A page that came back with its shell is asked for again, slim.
    assert 'hasAttribute("data-settings-embed")' in arrived
    closed = code[code.index("function closed(") : code.index("function openSettings(")]
    assert "window.location.reload()" in closed
    # And the theme applies at once, from the frame's localStorage write.
    assert '"storage"' in code and '"lembas-theme"' in code


def test_in_the_frame_links_out_go_to_the_whole_window_and_escape_closes():
    code = _window_code()
    frame = code[: code.index("var dialog = null")]
    assert 'link.target = "_top"' in frame
    assert '"Escape"' in frame
    assert "closeSettings()" in frame


def test_the_page_links_are_marked_to_stay_pages():
    """Cancel on a scheduled task's form and Done after a token are a way back
    to the page, not a window over the page being left."""
    for name in ("schedules/new.html", "schedules/edit.html", "token_created.html", "device.html"):
        text = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert re.search(r'href="/settings[^"]*" data-settings-page', text), name


def test_the_slash_commands_use_the_window_too():
    commands = js_code(script("commands.js"))
    go = commands[commands.index("function go(url)") :]
    go = go[: go.index("};")]
    assert "openSettings(url)" in go
    assert "window.location = url" in go


# --- The composer's settings button ---------------------------------------------
def test_the_composer_settings_button_is_the_attach_buttons_twin(
    client: TestClient, db, registered, make_chat
):
    _model(db, "m", reasoning=True)
    chat_id = make_chat()
    html = client.get(f"/chat/{chat_id}").text

    def toggle_classes(label: str) -> tuple[str, str]:
        at = html.index(f'aria-label="{label}" title="{label}"')
        tag = html[html.rindex("<button", 0, at) : html.index(">", at)]
        inner = html[html.index(">", at) + 1 : html.index("</button>", at)]
        return re.search(r'class="([^"]*)"', tag).group(1), inner

    attach_classes, attach_inner = toggle_classes("Attach")
    settings_classes, settings_inner = toggle_classes("Chat settings")
    assert settings_classes == attach_classes == "btn btn--icon composer__btn"
    # An icon and nothing else: no effort level, short or long.
    assert "data-effort-short" not in html
    assert "composer__effort-short" not in html
    assert re.sub(r"<svg.*?</svg>", "", settings_inner, flags=re.S).strip() == ""
    assert "#i-sliders" in settings_inner
    assert attach_inner.count("<svg") == settings_inner.count("<svg") == 1
    # The same icon size as attach's: no `icon--sm` on one and not the other.
    def svg_class(inner: str) -> str:
        return re.search(r'<svg class="([^"]*)"', inner).group(1).strip()

    assert svg_class(settings_inner) == svg_class(attach_inner)
