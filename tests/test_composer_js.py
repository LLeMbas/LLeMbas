"""The composer's typing affordances, checked without a runtime.

There is no JavaScript test runner here and hard rule 1 keeps Node out of the
project, so the behaviour is driven by hand under a DOM stub before committing.
That is not a nicety, and this file is the reason it is written down: `refresh()`
wrote to `list` *before* anything had built it, so the first `/` or `@` ever
typed threw on a null and took the whole handler with it. The menu never
appeared, in any browser, for the entire life of the feature, and `node --check`
parses that file happily.

What the suite can pin are the properties that failure depended on, and the ones
beside it whose absence is equally silent: a listener registered in a phase the
event does not reach, a repaint bound to the htmx event that fires too early,
and a mirror sized differently from the box it sits behind. Every one of those
looks exactly like working.
"""

from __future__ import annotations

import re
from pathlib import Path

import lembas

from .conftest import js_code, js_function, js_says, script

SOURCE = script("composer.js")
CODE = js_code(SOURCE)
CSS = (Path(lembas.__file__).parent / "web/static/css/chat.css").read_text(encoding="utf-8")


def _rule(css: str, opening: str) -> str:
    """One declaration block, by the text that opens it.

    The match has to *start* a rule: `.composer__mirror .tok-command {` appears
    twice, once as the tail of a grouped selector, and reading the group's block
    when the single one was asked for is a test that answers a question nobody
    put.
    """
    at = -1
    while True:
        at = css.index(opening, at + 1)
        if not css[:at].rstrip().endswith(","):
            return css[at : css.index("}", at)]


# --- The menu, and the null it used to throw on -------------------------------
def test_the_menu_is_built_before_anything_writes_to_it():
    """The bug this module exists for. `refresh()` sets `list.innerHTML` and
    appends to it, and `list` is null until `build()` has run -- so with the
    build left to `show()`, which is called *after* those writes, the first
    keystroke threw and the menu never opened for anybody.

    Asserted as the order inside `refresh`, with comments stripped: the file
    explains this at length in prose, and prose satisfies a substring test.
    """
    refresh = js_function(SOURCE, "refresh")

    assert "build()" in refresh, "refresh no longer builds the menu"
    assert refresh.index("build()") < refresh.index("list"), (
        "something reads or writes `list` before the menu exists"
    )


def test_building_it_twice_costs_nothing():
    """Which is what lets `refresh` and `show` both call it unconditionally.
    Without the guard, every keystroke would insert another menu."""
    assert js_function(SOURCE, "build").index("if (menu) return;") < 40


def test_the_late_mention_reply_checks_the_menu_is_still_there():
    """A mention list is a round trip late, and the caret may have moved off the
    token -- or the menu may have been dismissed, which drops `list`. Writing
    into it anyway is the same null a second time, on a slower path."""
    load = js_function(SOURCE, "loadMentions")
    assert load.index("!list") < load.index("list.innerHTML")
    # And the kind, or a reply for `@` lands in a menu now showing commands.
    assert 'kind !== "@"' in load


def test_choosing_with_the_mouse_holds_the_caret():
    """`mousedown` is cancelled inside the menu. Without it the composer loses
    focus before the click lands, and the caret position the choice is about to
    be written at is already gone -- so the token is replaced at the wrong
    offset, or not at all."""
    build = js_function(SOURCE, "build")
    assert '"mousedown"' in build
    assert build.index('"mousedown"') < build.index('"click"')
    assert "preventDefault" in build


# --- Listeners, and the phases they must be in --------------------------------
def test_the_key_listener_is_registered_in_the_capture_phase():
    """app.js already has a document-level Enter handler that submits the form,
    and listeners on the same element in the same phase fire in registration
    order -- app.js loads first. In the bubble phase this one would never get to
    say "that Enter chose a menu item, it did not send the message", so choosing
    from the menu would send the half-typed line as well.

    The property rather than the markup, for the reason `tests/test_ui_js.py`
    gives: a trigger bound where the event does not go is silent.
    """
    found = re.search(r'"keydown",[\s\S]*?\n\s*(true|false)\n\s*\);', SOURCE)
    assert found, "the keydown listener is gone"
    assert found.group(1) == "true"


def test_the_scroll_listener_is_too():
    """`scroll` does not bubble. Registered without the third argument this is
    never called, and the mirror stops following the textarea the moment the box
    grows past `data-max-height` -- the rectangles then sit over the wrong
    words, which is the one failure the mirror exists to avoid."""
    found = re.search(r'"scroll",[\s\S]*?\n\s*(true|false)\n\s*\);', SOURCE)
    assert found, "the scroll listener is gone"
    assert found.group(1) == "true"


def test_the_mirror_repaints_after_the_request_as_well_as_after_the_swap():
    """htmx fires afterSwap and afterSettle *before* afterRequest, and the
    composer empties itself from `hx-on::after-request` -- so every repaint
    bound to the first two ran while the box still held the message, and the
    highlighting sat over an empty field until the next keystroke.

    Both late events go through the same deferred helper, and that is the whole
    assertion: `paint` bound directly to `reset` reads the value in the same
    turn the event fires, which is *before* a form's fields are actually
    cleared, so it paints the text that is about to vanish.
    """
    after = re.search(r'addEventListener\("htmx:afterRequest",\s*(\w+)\)', CODE)
    reset = re.search(r'addEventListener\("reset",\s*(\w+)\)', CODE)
    assert after and reset, "the late repaints are gone"
    assert after.group(1) == reset.group(1), "the two late events repaint differently"
    assert after.group(1) != "paint", "a repaint in the same turn paints the old value"

    deferred = js_function(SOURCE, after.group(1))
    assert "requestAnimationFrame" in deferred
    assert "paint" in deferred

    # The early ones stay: a swap that brings a new composer in has to paint it.
    for event in ("htmx:afterSwap", "htmx:afterSettle"):
        assert re.search(rf'addEventListener\("{event}",\s*paint\)', CODE), event


# --- What may become markup ---------------------------------------------------
def test_nothing_typed_into_the_box_can_become_markup():
    """The mirror holds the one string a person controls exactly, character for
    character. Hard rule 6 covers model output; this is the same rule pointed
    the other way, and it is the only place in the application where somebody's
    raw keystrokes are re-rendered as they type.

    `list.innerHTML` in the mention path is deliberately not covered: that is a
    server-rendered fragment, escaped where it was built.
    """
    paint = js_function(SOURCE, "paint")
    assert "innerHTML" not in paint
    assert "replaceChildren" in paint
    assert "createTextNode" in paint
    assert "textContent" in js_function(SOURCE, "token")


# --- The mirror and the box are one shape -------------------------------------
METRICS = (
    "font-family",
    "font-size",
    "line-height",
    "letter-spacing",
    "padding",
    "white-space",
    "overflow-wrap",
    "word-break",
    "width",
)


def test_the_mirror_and_the_box_are_sized_by_one_rule():
    """A textarea cannot style its own contents, so the mirror sits behind it
    holding the same text with every character transparent. Every property that
    decides where a character lands has to be declared once, for both: a single
    difference and the rectangles slide off the words they are marking, and the
    drift grows with the length of the line rather than showing up as an obvious
    misalignment at the start.

    So the shared rule is asserted to carry all of them, and neither of the
    per-element rules to redeclare any -- which is the version of this that
    breaks silently, because a second declaration looks like a local tweak.
    """
    shared = _rule(CSS, ".composer__input,\n.composer__mirror {")
    for property_ in METRICS:
        assert f"{property_}:" in shared, f"{property_} is not shared"

    for selector in ("\n.composer__input {", "\n.composer__mirror {"):
        own = _rule(CSS, selector)
        for property_ in METRICS:
            assert f"{property_}:" not in own, f"{selector.strip()} redeclares {property_}"


def test_a_token_restates_what_it_would_otherwise_inherit():
    """`color: transparent` on the mirror is an inherited value, and any colour
    a span declares itself beats it. The transcript's rule used to do exactly
    that from across the file, painting the token in accent-coloured monospace
    at 0.95em on top of the textarea's own text: doubled, and shifted from that
    point on because the metrics differ."""
    tokens = _rule(CSS, ".composer__mirror .tok-mention,\n.composer__mirror .tok-command {")
    assert "color: transparent" in tokens
    assert "font: inherit" in tokens


def test_a_token_bleeds_with_a_shadow_rather_than_with_padding():
    """The rectangle has to be a little wider than the text it sits behind.
    Padding and a negative margin do that by *moving the glyph*, which is the
    one thing nothing in the mirror is allowed to do -- a shadow cannot."""
    tokens = _rule(CSS, ".composer__mirror .tok-mention,\n.composer__mirror .tok-command {")
    assert "padding: 0" in tokens
    assert "margin: 0" in tokens

    for selector in (".composer__mirror .tok-mention {", ".composer__mirror .tok-command {"):
        rule = _rule(CSS, selector)
        assert "box-shadow" in rule, f"{selector} does not bleed"
        assert "padding" not in rule, f"{selector} moves the glyph"
        assert "margin" not in rule, f"{selector} moves the glyph"


def test_every_token_style_names_where_it_applies():
    """A rule written for one context matches every context. `.tok-mention`
    unscoped was written for the transcript and also hit the mirror's spans,
    which is the bug above. Both places qualify their selectors now, and an
    unqualified one added later would do it again with nothing to notice."""
    for line in CSS.splitlines():
        if ".tok-" not in line:
            continue
        selector = line.strip()
        assert selector.startswith((".msg ", ".composer__mirror ")), selector


# --- Slash commands, from the composer's side ---------------------------------
def test_an_unrecognised_slash_is_never_swallowed():
    """Eating somebody's message because it began with a slash is a far worse
    failure than an unknown command. Enter only cancels the send when the value
    resolves to a command -- so the `preventDefault` has to sit *inside* that
    test, not beside it."""
    window = CODE[CODE.index('event.key === "Enter" && !event.shiftKey') :][:600]
    assert window.index("commandIn(") < window.index("preventDefault")
    assert "if (command) {" in window


def test_choosing_a_command_clears_the_box_before_running_it():
    """A command is the whole message, never part of one. `/help` left sitting
    in the box after running means the next Enter runs it again -- which is what
    driving this under a DOM stub found, along with Tab not completing."""
    choose = js_function(SOURCE, "choose")
    assert js_says(choose, "input.value", '""', "runCommand")


def test_tab_completes_and_enter_runs():
    """The difference matters for a command that takes an argument: Tab writes
    `/mode ` and leaves the caret after it, Enter runs what is there. Both are
    one call, so the key is what decides -- and `choose` has to branch on it
    before it reaches the run."""
    assert js_says(CODE, "choose(all[active]", 'event.key === "Tab"')
    choose = js_function(SOURCE, "choose")
    assert choose.index("if (complete") < choose.index("runCommand")


def test_the_at_menu_works_on_a_page_without_commands_js():
    """`@` and `/` share one menu but not one dependency. commands.js is absent
    from some pages and may fail to parse on any of them, and either would
    otherwise take the mention picker down with it."""
    fallback = js_function(SOURCE, "commands")
    assert "window.lembasCommands ||" in fallback
    for method in ("list:", "find:", "run:"):
        assert method in fallback, f"the fallback has no {method}"

    # Read at call time rather than captured once, or a page whose commands.js
    # loads second gets the fallback forever.
    for helper in ("commandList", "commandIn", "runCommand"):
        assert "commands()." in js_function(SOURCE, helper)


def test_the_directory_is_read_from_the_field_that_is_submitted():
    """The chip beside the connection shows the directory's own name so it stops
    eating the composer's one row; the whole path lives in the hidden field. A
    mention picker reading the label would ask the server about `worker` rather
    than about `/srv/projects/…/worker` -- shortening a label must never shorten
    a value, on this path either."""
    context = js_function(SOURCE, "context")
    assert "[data-dir-value]" in context
    assert "data-dir-label" not in context


# --- A linked device's files and commands ------------------------------------------
def test_a_device_chat_asks_the_device_for_files_beside_the_library():
    """`@` in a device chat asks the device's own index (`/api/devices/<id>/files`)
    as well as the library, and lists the device's first."""
    load = js_function(SOURCE, "loadMentions")
    assert "/api/devices/" in load and "/files?q=" in load
    assert "asked.unshift" in load


def test_a_device_file_is_only_the_reference():
    """Nothing is copied: the device reads the file when the prompt arrives, so
    choosing one writes `@path` and returns before any attach route is called."""
    choose = js_function(SOURCE, "choose")
    stop = choose.index('if (option.hasAttribute("data-mention-device")) return;')
    assert choose.index("replaceToken(token)") < stop
    assert stop < choose.index("/api/files/from-project")


def test_a_device_path_is_escaped_the_way_the_cli_reads_it():
    """A space ends an `@` mention in the CLI and `#` starts a line range, so a
    device path carries both escaped."""
    choose = js_function(SOURCE, "choose")
    assert 'token.replace(/([\\\\ #])/g, "\\\\$1")' in choose


def test_a_device_command_that_takes_an_argument_is_completed_not_sent():
    choose = js_function(SOURCE, "choose")
    assert 'option.hasAttribute("data-command-input")' in choose
