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
    # Through a gate, though -- see the test below.
    for event in ("htmx:afterSwap", "htmx:afterSettle"):
        assert re.search(rf'addEventListener\("{event}",\s*paintAfterSwap\)', CODE), event
    assert "paint()" in js_function(SOURCE, "paintAfterSwap")


def test_a_swap_elsewhere_does_not_repaint_the_mirror():
    """`paint` rebuilds the mirror and reads the textarea's geometry -- a forced
    layout -- and it ran after every swap anywhere, twice, so on every frame of
    a streaming reply. That was the lag in typing while a reply was written.
    Only a swap inside the composer, or one that replaces something holding it,
    repaints now."""
    gate = js_function(SOURCE, "paintAfterSwap")
    assert js_says(gate, "if (involvesComposer(event))", "paint()")
    involves = js_function(SOURCE, "involvesComposer")
    assert 'closest(".composer")' in involves
    assert "contains(input)" in involves
    # Both the element the event fired on and the request's target: an
    # out-of-band swap into the composer carries the request's own target.
    assert "event.target" in involves and "event.detail.target" in involves


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
    # The Markdown colouring is four more functions between the box and the
    # mirror, and every one of them is held to the same rule.
    for name in ("paint", "line", "inline", "piece", "add", "wrap"):
        assert "innerHTML" not in js_function(SOURCE, name), name
        assert "insertAdjacentHTML" not in js_function(SOURCE, name), name
    add = js_function(SOURCE, "add")
    assert "createTextNode" in add
    assert "textContent" in add


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
    # A ligature makes three characters one glyph; a tab's width and a
    # scrollbar's gutter move every character after them. Each on both or the
    # caret drifts off the text.
    "font-variant-ligatures",
    "font-feature-settings",
    "font-synthesis",
    "tab-size",
    "scrollbar-gutter",
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
    """The mirror's colour and font are inherited values, and any a span
    declares itself beats them. The transcript's rule used to do exactly that
    from across the file, painting the token in accent-coloured monospace at
    0.95em: shifted from that point on, because the metrics differ. The mirror
    is the visible text now, so the token keeps the text's colour."""
    tokens = _rule(CSS, ".composer__mirror .tok-mention,\n.composer__mirror .tok-command {")
    assert "color: inherit" in tokens
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
    stop = choose.index("if (byPath) return;")
    assert choose.index("replaceToken(token)") < stop
    assert stop < choose.index("/api/files/from-attachment")
    # A file in the tree is the same: its path, read by the model under the
    # chat's access, never copied in as an attachment.
    by_path = choose[choose.index("var byPath"):].split(";", 1)[0]
    assert "data-mention-device" in by_path and "data-mention-tree" in by_path


def test_a_device_path_is_escaped_the_way_the_cli_reads_it():
    """A space ends an `@` mention in the CLI and `#` starts a line range, so a
    device path carries both escaped."""
    choose = js_function(SOURCE, "choose")
    assert 'token.replace(/([\\\\ #])/g, "\\\\$1")' in choose


def test_a_device_command_that_takes_an_argument_is_completed_not_sent():
    choose = js_function(SOURCE, "choose")
    assert 'option.hasAttribute("data-command-input")' in choose


# --- The mirror is the visible text -------------------------------------------
def _mirror_rules() -> list[tuple[str, str]]:
    """Every rule whose selector styles something inside the mirror."""
    rules = []
    for found in re.finditer(r"([^{}]+)\{([^}]*)\}", CSS):
        selector, body = found.group(1), found.group(2)
        selector = re.sub(r"/\*[\s\S]*?\*/", "", selector).strip()
        if ".composer__mirror ." in selector:
            rules.append((selector, body))
    return rules


def test_nothing_in_the_mirror_moves_a_glyph():
    """The textarea's caret sits over the mirror's characters. A span that
    changes a glyph's size, face, slant, spacing or box puts every character
    after it somewhere the caret is not -- the doubled-letter failure, back.
    Colour, background, shadow, underline and weight only (the face is
    monospaced at every weight, and `font-synthesis: none` stops a fake bold)."""
    rules = _mirror_rules()
    assert any(".md-heading" in selector for selector, _ in rules), "no Markdown styles found"
    forbidden = re.compile(
        r"(?:^|;|\s)(font-size|font-family|font-style|letter-spacing|word-spacing|"
        r"display|position|line-height|border(?:-(?:top|right|bottom|left|width))?)\s*:"
    )
    for selector, body in rules:
        declarations = re.sub(r"/\*[\s\S]*?\*/", "", body)
        found = forbidden.search(declarations)
        assert not found, f"{selector} sets {found.group(1)}"
        for box in ("padding", "margin"):
            for value in re.findall(rf"(?:^|;|\s){box}\s*:\s*([^;]+)", declarations):
                assert value.strip() == "0", f"{selector} sets {box}: {value}"
        for value in re.findall(r"(?:^|;|\s)font\s*:\s*([^;]+)", declarations):
            assert value.strip() == "inherit", f"{selector} sets font: {value}"


def test_the_box_hides_its_own_text_only_once_the_mirror_shows_it():
    """Without JavaScript the mirror is empty, and a transparent textarea would
    be a box nobody can read. So the only `color: transparent` on the textarea
    is behind `.is-painted`, and `paint` is what sets it."""
    rules = [
        (re.sub(r"/\*[\s\S]*?\*/", "", m.group(1)).strip(), m.group(2))
        for m in re.finditer(r"([^{}]+)\{([^}]*)\}", CSS)
    ]
    hiding = [
        selector
        for selector, body in rules
        if ".composer__input" in selector
        and "::selection" not in selector
        and re.search(r"(?:^|;|\s)color\s*:\s*transparent", body)
    ]
    assert hiding == [".composer__field.is-painted .composer__input"], hiding
    assert js_says(js_function(SOURCE, "paint"), 'classList.add("is-painted")')


def test_selection_and_placeholder_survive_a_transparent_box():
    """The selection's glyphs would be drawn over the mirror's in another
    colour, doubled; Firefox's placeholder takes the element's colour, which is
    transparent once painted."""
    selection = _rule(CSS, ".composer__input::selection {")
    assert "color: transparent" in selection
    assert "background: var(--" in selection
    placeholder = _rule(CSS, ".composer__input::placeholder {")
    assert "color: var(--" in placeholder
    assert "opacity: 1" in placeholder
    assert "caret-color: var(--" in _rule(CSS, "\n.composer__input {")


def test_no_regex_in_the_composer_uses_a_lookbehind():
    """An older Safari refuses to parse one, and a regex that does not parse is
    a SyntaxError that takes the whole file down -- the menu, the mirror, the
    commands."""
    assert "(?<" not in CODE


def test_every_write_to_the_box_repaints_it():
    """The mirror is what is on screen, so a write that skips the repaint shows
    the old text over the new caret. Several paths assign `input.value`
    directly; rather than trusting each to call `paint`, the instance's own
    `value` setter does it, and `paint` installs that on whichever box it finds
    (a swap can bring a new one)."""
    watch = js_function(SOURCE, "watchValue")
    assert "Object.defineProperty(input, \"value\"" in watch
    assert js_says(watch, "set:", "NATIVE_VALUE.set.call(this, text)", "paint()")
    assert "NATIVE_VALUE.get.call(this)" in watch
    assert 'getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")' in CODE
    assert "watchValue(input)" in js_function(SOURCE, "paint")
    # And the writes that do not go through the property.
    assert re.search(r'addEventListener\("reset",\s*repaintSoon\)', CODE)
    assert re.search(r'addEventListener\("pageshow",\s*paint\)', CODE)


def test_the_mirror_keeps_every_character_once():
    """Markdown is coloured, never rendered: each line goes through `line`, the
    newlines between them are added back one for one, and a trailing one keeps
    the last empty line as tall as the textarea's."""
    paint = js_function(SOURCE, "paint")
    assert 'split("\\n")' in paint
    assert js_says(paint, "if (i) add(mirror, \"\\n\")", "line(mirror, lines[i]")
    assert 'add(mirror, "\\n");' in paint
    assert "mirror.scrollTop = input.scrollTop" in paint
    inline = js_function(SOURCE, "inline")
    # Every rule consumes something, or the scan never ends.
    assert "at += 1" in inline
    assert "add(parent, text.slice(at))" in inline


def test_the_whole_interface_turns_ligatures_off():
    """JetBrains Mono's contextual alternates merge `==`, `->`, `...` into one
    glyph. On body, so every code block and every label inherits it."""
    app = (Path(lembas.__file__).parent / "web/static/css/app.css").read_text(encoding="utf-8")
    body = _rule(app, "\nbody {")
    assert "font-variant-ligatures: none" in body
    assert 'font-feature-settings: "liga" 0, "calt" 0' in body
    shared = _rule(CSS, ".composer__input,\n.composer__mirror {")
    assert "font-variant-ligatures: none" in shared
