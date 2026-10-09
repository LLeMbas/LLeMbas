"""Panel widths: three numbers per panel, in three files, that must agree.

`set_layout` drops a CSS variable it does not recognise, and it drops it
silently -- an older browser sending a key a newer release removed must not fail
the whole request. The cost of that kindness is that a panel whose width is
missing from `LAYOUT_BOUNDS` is one whose drag handle appears to work, moves the
edge, and forgets by the next page load. Nothing anywhere says so.

So the three are pinned here: the allowlist entry, the `data-resize-min` on the
handle, and the `--*-width-min` token the CSS clamps with.
"""

from __future__ import annotations

import re
from pathlib import Path

import lembas
from lembas.api.preferences import LAYOUT_BOUNDS

ROOT = Path(lembas.__file__).parent
TOKENS = (ROOT / "web/static/css/tokens.css").read_text(encoding="utf-8")
TEMPLATES = ROOT / "web/templates"

# The panels with a drag handle, and the template each handle lives in.
PANELS = {
    "--terminal-width": "chat/_terminal.html",
    "--canvas-width": "chat/_canvas.html",
    "--prototype-width": "chat/_prototype.html",
}

# 1rem, everywhere in this application.
REM = 16


def _resize_min(template: str) -> int:
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    found = re.search(r'data-resize-min="(\d+)"', text)
    assert found, f"{template} has a resize handle with no minimum"
    return int(found.group(1))


def _token_min(name: str) -> int:
    found = re.search(rf"{re.escape(name)}-min:\s*([\d.]+)rem", TOKENS)
    assert found, f"{name}-min is not declared in tokens.css"
    return int(float(found.group(1)) * REM)


def test_every_dragged_panel_is_in_the_allowlist():
    """Without the entry the drag is silently discarded on the way to the
    account, so the width survives in one browser and vanishes in the next."""
    missing = [name for name in PANELS if name not in LAYOUT_BOUNDS]
    assert not missing, f"not in LAYOUT_BOUNDS: {missing}"


def test_the_three_minimums_agree():
    for name, template in PANELS.items():
        assert LAYOUT_BOUNDS[name][0] == _resize_min(template) == _token_min(name), name


def test_no_bound_lets_a_panel_become_unreachable():
    """A width outside these is a panel somebody cannot see well enough to drag
    back, which is the other half of what the allowlist is for."""
    for name, (low, high) in LAYOUT_BOUNDS.items():
        assert 0 < low < high, name


def test_the_canvas_starts_wider_than_the_terminal():
    """A source line is longer than eighty columns once nothing is re-wrapping
    it, and this one holds prose as well."""
    widths = {
        name: float(re.search(rf"{re.escape(name)}:\s*([\d.]+)rem", TOKENS).group(1))
        for name in PANELS
    }
    assert widths["--canvas-width"] > widths["--terminal-width"]


# --- Breakpoints -------------------------------------------------------------
# A media query cannot read a custom property, so the three widths this
# application breaks at are literals in three stylesheets with nothing tying
# them to the tokens that name them. Which is fine until somebody adds a fourth
# in passing, and then there are four breakpoints and a comment describing
# three.
def _breakpoints_used() -> set[str]:
    """Widths that appear in an `@media` condition, and nowhere else.

    Scoped to the condition on purpose: `max-width` is also an ordinary
    declaration -- `.composer__dir` is capped at 11rem, `.picker__menu` at
    14rem -- and a pattern that reads every one of them calls two dozen
    component caps "breakpoints" and fails on all of them.
    """
    import re

    used: set[str] = set()
    for name in ("app.css", "chat.css", "admin.css"):
        text = (ROOT / "web/static/css" / name).read_text(encoding="utf-8")
        for condition in re.findall(r"@media([^{]*)\{", text):
            used.update(re.findall(r"max-width:\s*([\d.]+rem)", condition))
    return used


def test_every_breakpoint_is_one_of_the_declared_ones():
    import re

    declared = set(re.findall(r"--bp-[a-z]+:\s*([\d.]+rem)", TOKENS))
    assert declared, "no --bp-* tokens declared"

    used = _breakpoints_used()
    assert used <= declared, (
        f"breakpoints used but not declared in tokens.css: {sorted(used - declared)}"
    )


def test_no_breakpoint_is_declared_and_never_used():
    """The other direction: a token naming a width nothing breaks at is the
    same clutter as a colour nothing paints with."""
    import re

    declared = set(re.findall(r"--bp-[a-z]+:\s*([\d.]+rem)", TOKENS))
    assert declared <= _breakpoints_used(), (
        f"declared and unused: {sorted(declared - _breakpoints_used())}"
    )


# --- A minimum wider than the screen -----------------------------------------
#
# A panel's `--*-width-min` is there so a dragged edge cannot be pulled to
# nothing on a desktop. On a phone it was the bug: `min-width` is resolved after
# `width` and `max-width` and **wins over both** -- CSS clamps width to max-width
# and then raises the result to min-width -- so
# `.canvas { width: min(var(--canvas-width), 100vw) }` inside the narrow query was
# simply overruled by `min-width: 24rem`, and both side panels were 384px wide on
# every screen narrower than that. `.inspector` had no cap at all, and its width
# is a *preference* somebody can drag to 2400px.
#
# Nothing scrolled sideways, because all three are `position: fixed` and fixed
# overflow does not extend the scrollable area. So the symptom was content off
# the edge of the screen and unreachable, which is exactly what a pass looking
# for sideways scrolling does not find.
#
# This is the tree's standing rule in another shape: a track's minimum wider than
# the viewport is the bug, and the minimum is the thing that has to give.
APP_CSS = (ROOT / "web/static/css/app.css").read_text(encoding="utf-8")

# Every panel that becomes an overlay rather than a column on a small screen.
OVERLAY_PANELS = (".inspector", ".terminal", ".canvas", ".proto-panel")


def _media_body(css: str, condition: str) -> str:
    """The contents of every `@media` block whose condition matches, joined.

    Braces are balanced rather than split on, because taking everything after
    `@media` gives the rest of the file -- a test written that way asserts about
    the whole stylesheet while appearing to be about one query.
    """
    bodies = []
    for start in (i for i in range(len(css)) if css.startswith("@media", i)):
        opened = css.index("{", start)
        if condition not in css[start:opened]:
            continue
        depth, cursor = 0, opened
        while cursor < len(css):
            if css[cursor] == "{":
                depth += 1
            elif css[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        bodies.append(css[opened + 1 : cursor])
    return "\n".join(bodies)


def test_the_scan_finds_both_queries():
    """A blindness guard: if either breakpoint is renamed, the two tests below
    would pass by asserting about an empty string."""
    assert _media_body(APP_CSS, "64rem").strip()
    assert _media_body(APP_CSS, "48rem").strip()


def test_no_overlay_panel_keeps_a_minimum_once_it_is_an_overlay():
    """The fix, stated as the property rather than as the declaration: inside the
    query where these become fixed overlays, nothing may hold them wider than the
    screen. `min-width: 0` is how that is written."""
    body = _media_body(APP_CSS, "64rem")
    assert "min-width: 0" in body, (
        "the overlay panels have no `min-width: 0`, so `--*-width-min` wins again "
        "and a panel is wider than a narrow screen"
    )
    for panel in OVERLAY_PANELS:
        assert panel in body, f"{panel} is no longer part of the overlay query"


def test_every_overlay_panel_is_full_width_on_a_phone():
    """A 20% sliver of conversation behind a sheet is not a view of anything, so
    below the phone breakpoint the panels take the whole width. The tablet keeps
    its column, which is why this is asserted on the 48rem query and not the
    64rem one."""
    body = _media_body(APP_CSS, "48rem")
    for panel in OVERLAY_PANELS:
        assert panel in body, f"{panel} is not sized on a phone"
    assert "width: 100vw" in body
    assert "max-width: 100vw" in body


def test_the_desktop_minimum_is_still_declared():
    """The other direction. Removing the minimum altogether would let a drag
    handle pull a panel to nothing on the machine where dragging exists."""
    for token in ("--terminal-width-min", "--canvas-width-min", "--prototype-width-min"):
        assert f"{token}:" in TOKENS
        assert f"var({token})" in APP_CSS


def test_the_topbar_model_menu_belongs_to_the_bar_on_a_phone():
    """Anchored to the picker, the menu would open `right: 0` of a button that
    sits mid-bar with the panel buttons to its right, so on a 390px phone a 24rem
    menu would start 132px left of the screen and cut off every model's name.

    Below the phone breakpoint the picker gives up `position`, which makes the bar
    the containing block, and the menu is pinned between the bar's two edges."""
    body = _media_body(APP_CSS, "48rem")
    assert re.search(r"\.topbar\s*\{\s*position:\s*relative", body)
    assert re.search(r"\.topbar__actions \.picker\s*\{\s*position:\s*static", body)
    menu = re.search(r"\.topbar__actions \.picker__menu\s*\{([^}]*)\}", body)
    assert menu, "the topbar's menu is not placed on a phone"
    for declaration in ("left:", "right:", "width: auto"):
        assert declaration in menu.group(1), f"{declaration} missing from the phone menu"
