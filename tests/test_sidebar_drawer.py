"""The sidebar as a drawer: closed by default where it covers the page.

Below the phone breakpoint the sidebar is a fixed 280px overlay. It was
rendered with no `hidden` attribute at any width and nothing ever set one on
load, so on a 390px phone it covered the page from first paint -- with the only
control that could close it, the topbar's toggle, underneath it. And that toggle
existed on `/chat` alone: the seven other pages carrying the sidebar had no
dismiss control of any kind.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import lembas

ROOT = Path(lembas.__file__).parent
TEMPLATES = ROOT / "web/templates"
APP_CSS = (ROOT / "web/static/css/app.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")

# Every page that renders the sidebar.
CARRIERS = [
    "settings.html",
    "chat/index.html",
    # reports/_layout.html extends the library's.
    "schedules/_layout.html",
    "library/_layout.html",
    "folders/edit.html",
]


def test_every_page_with_a_sidebar_has_a_way_to_close_it():
    """`grep -rn 'data-toggle="#sidebar"'` returned exactly one hit, and the
    other seven pages were unusable on a phone because of it."""
    missing = [
        name
        for name in CARRIERS
        if "partials/_sidebar_toggle.html" not in (TEMPLATES / name).read_text(encoding="utf-8")
    ]
    assert not missing, f"no sidebar toggle on: {missing}"


def test_the_toggle_is_one_partial_and_not_eight_copies():
    """The next control added to a topbar should not have to be added eight
    times, which is how the first one came to exist once."""
    toggle = (TEMPLATES / "partials/_sidebar_toggle.html").read_text(encoding="utf-8")
    assert 'data-toggle="#sidebar"' in toggle
    # The label, however it is spelled. It was written inline until the interface
    # became translatable, and it is now `aria-label="{{ t('Toggle sidebar') }}"` --
    # the same rename `asset()` put this suite through once already. Asserting the
    # old spelling here would fail on a change that broke nothing.
    assert "aria-label=" in toggle
    assert "Toggle sidebar" in toggle


def test_the_toggle_does_not_claim_to_be_open():
    """It rendered `aria-expanded="true"` from the template -- a fact nobody
    checked and one that was false on every phone. `syncToggles` writes it."""
    toggle = (TEMPLATES / "partials/_sidebar_toggle.html").read_text(encoding="utf-8")
    # The element, not the file: the comment above it explains at length why
    # the attribute is absent, and a test reading the file fails on the
    # explanation for the fix.
    button = toggle.split("<button", 1)[1].split(">", 1)[0]
    assert "aria-expanded" not in button
    assert 'syncToggles("#sidebar"' in APP_JS


def test_the_drawer_is_closed_by_default_only_where_it_is_a_drawer():
    """Three states, and the third is the one that matters: absent means
    "follow the width", which is what the server renders because the server
    does not know the width."""
    assert 'data-sidebar="open"' in APP_CSS
    assert 'data-sidebar="closed"' in APP_CSS
    assert 'return !window.matchMedia(NARROW).matches;' in APP_JS


def test_the_close_button_is_reachable_inside_the_open_drawer():
    """`.sidebar__close` is `display: none` at width and turned back on inside
    the media query. Both rules are one class deep, so the order decides -- and
    written the other way round the button is invisible at every width,
    including inside the drawer it exists for."""
    base = APP_CSS.index("\n.sidebar__close {")
    inside = APP_CSS.index("  .sidebar__close {")
    assert base < inside, "the base rule must come first or it wins everywhere"


def test_nothing_behind_the_drawer_can_be_tabbed_into():
    assert 'toggleAttribute("inert"' in APP_JS


def test_inert_is_never_left_behind_on_a_widened_window():
    """An `inert` left on a window somebody widened is a page that has stopped
    responding, which is worse than the bug it is here to fix."""
    assert 'matchMedia(NARROW).addEventListener("change"' in APP_JS


def test_the_drawer_is_dismissible_without_finding_a_button():
    """The scrim is a partial because there are two sidebars, so the markup is
    asserted where it is defined and its *inclusion* is asserted per sidebar by
    `test_every_sidebar_carries_the_way_out_and_the_scrim`."""
    scrim = (TEMPLATES / "partials/_sidebar_scrim.html").read_text(encoding="utf-8")
    assert 'class="sidebar-scrim"' in scrim
    assert 'data-toggle="#sidebar"' in scrim
    assert ".sidebar-scrim" in APP_CSS


def test_the_sidebar_does_not_go_through_setpanel():
    """The other three panels use the `hidden` attribute, which is one value
    for both widths -- the thing this panel cannot use."""
    assert 'if (selector === "#sidebar") return setSidebar(open);' in APP_JS


def test_the_toggle_is_a_real_target(client: TestClient, registered):
    """44px comes from `--control-h` under the coarse-pointer block, so this
    only holds while `.btn--icon` keeps taking its size from that token."""
    tokens = (ROOT / "web/static/css/tokens.css").read_text(encoding="utf-8")
    assert "--tap-min: 2.75rem" in tokens
    assert "--control-h: var(--tap-min)" in tokens


# --- Any sidebar, not only the one this was written for ----------------------
def _sidebar_templates() -> list[str]:
    """Every template that renders a sidebar of its own, found rather than
    listed -- the admin one was missed precisely because it was not on a list."""
    return [
        str(p.relative_to(TEMPLATES))
        for p in TEMPLATES.rglob("*.html")
        if '<aside class="sidebar"' in p.read_text(encoding="utf-8")
    ]


def test_every_sidebar_is_one_the_toggle_can_find():
    """`data-toggle="#sidebar"` resolves by id, and below the phone breakpoint
    `.sidebar` is a fixed overlay that starts closed. A sidebar without that id
    is one nothing can open: an admin area built that way is reachable on a
    phone, and unnavigable the moment you arrive."""
    without = [
        name
        for name in _sidebar_templates()
        if '<aside class="sidebar" id="sidebar"' not in (TEMPLATES / name).read_text(
            encoding="utf-8"
        )
    ]
    assert not without, f"sidebar with no id, so nothing can open it: {without}"


def test_every_sidebar_carries_the_way_out_and_the_scrim():
    missing = []
    for name in _sidebar_templates():
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        if "partials/_sidebar_close.html" not in text:
            missing.append(f"{name}: no close button")
        if "partials/_sidebar_scrim.html" not in text:
            missing.append(f"{name}: no scrim")
    assert not missing, missing


def test_the_admin_area_can_be_navigated_on_a_phone(client: TestClient, registered):
    """The whole of administration is in that nav and nowhere else."""
    page = client.get("/admin/models").text
    assert '<aside class="sidebar" id="sidebar"' in page
    assert 'data-toggle="#sidebar"' in page
    assert "sidebar-scrim" in page


# --- Controls do not shrink below their own size -----------------------------
def test_an_icon_button_keeps_its_size_in_a_tight_row():
    """`.btn--icon` sets a width and, without `flex: none`, a row that runs out
    of room shrinks it instead of the text beside it -- the sidebar toggle
    measured 18px across on a 390px chat, well under half its target."""
    rule = APP_CSS.split(".btn--icon {", 1)[1].split("}", 1)[0]
    assert "flex: none" in rule


def test_the_topbar_can_give_somewhere(client: TestClient, registered):
    """`.topbar__where` was the designated shrinker in that row and it is
    `display: none` below 64rem, so on a phone the group went rigid and the
    title -- which is `flex: 1` -- was squeezed to exactly zero width."""
    actions = APP_CSS.split(".topbar__actions {", 1)[1].split("}", 1)[0]
    assert "flex: 0 1 auto" in actions
    assert "min-width: 0" in actions
    assert "min-width" in APP_CSS.split(".topbar__title {", 1)[1].split("}", 1)[0]
