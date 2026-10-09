"""Dialogs and toasts, checked without a runtime.

There is no JavaScript test runner here and hard rule 1 keeps Node out of the
project, so the behaviour is driven by hand under a DOM stub before committing.
What can be pinned in the suite are the invariants the file states about itself
-- and in particular the one that would look like an improvement to somebody
tidying up later.
"""

from __future__ import annotations

import re
from pathlib import Path

import lembas

ROOT = Path(lembas.__file__).parent
SOURCE = (ROOT / "web/static/js/ui.js").read_text(encoding="utf-8")
TEMPLATES = ROOT / "web/templates"


def test_the_dialogs_never_fall_back_to_the_browsers_own():
    """`window.confirm` and `window.prompt` cannot be styled, ignore the theme
    and block the tab. Putting one back is the thing this module exists to
    prevent."""
    assert "window.confirm(" not in SOURCE
    assert "window.prompt(" not in SOURCE


def test_no_template_uses_htmx_s_own_prompt():
    """htmx's hx-prompt calls the browser's prompt() *synchronously* and only
    then fires htmx:prompt with the answer already in hand -- so intercepting
    the event cannot supply a different one, and the native box appears
    regardless. `data-prompt` exists because of that, and an hx-prompt slipped
    in later would summon the grey box back with nothing to catch it.

    hx-confirm is fine and is used widely: that one fires *before*, and ui.js
    intercepts it.
    """
    # The attribute, not the word: the comment beside `data-prompt` names
    # hx-prompt in order to say why it is not being used.
    offenders = [
        path.relative_to(TEMPLATES)
        for path in TEMPLATES.rglob("*.html")
        if 'hx-prompt="' in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"hx-prompt summons window.prompt: {offenders}"


def test_the_prompt_answer_is_json_encoded_rather_than_concatenated():
    """A folder called `"` would otherwise produce hx-vals that does not parse,
    and htmx would send the request with the field missing rather than with the
    name -- a rename that silently does nothing."""
    start = SOURCE.index("[data-prompt]")
    block = SOURCE[start : SOURCE.index("data-confirm", start)]
    assert "JSON.stringify" in block


def test_every_prompt_button_names_a_field_or_takes_the_default():
    """The field name is what the route reads. A button with a field the route
    does not look at posts nothing and looks exactly like one that works."""
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if "data-prompt=" not in text:
            continue
        # Either an explicit field, or the "name" default the handler applies.
        assert "data-prompt-field" in text or "/api/folders" in text, path


# --- The transcript's open blocks, and the scroll that used to chase them ------
STEPS = (ROOT / "web/static/js/steps.js").read_text(encoding="utf-8")
APP = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")


def test_the_toggle_listener_is_registered_in_the_capture_phase():
    """`toggle` does not bubble. Registered without the third argument the
    listener is never called, in every browser, with nothing anywhere to say so
    -- the same shape as a trigger bound where the event does not go, which cost
    two selects an entire release.

    Asserted as the property rather than as the markup, for that reason.
    """
    found = re.search(r'"toggle",[\s\S]{0,600}?\n\s*(true|false)\n\s*\);', APP)
    assert found, "the toggle listener is gone"
    assert found.group(1) == "true"


def test_the_scroll_listener_is_too():
    """A scroll event does not bubble either, and this one is on the thread
    rather than on the document it is registered against."""
    found = re.search(r'"scroll",[\s\S]{0,600}?\n\s*(true|false)\n\s*\);', APP)
    assert found, "the scroll listener is gone"
    assert found.group(1) == "true"


def test_the_stream_no_longer_scrolls_for_every_frame():
    """`metrics`, `status`, `ask` and `canvas` all arrive on htmx:sseMessage,
    and none of them changes the height of the thread. Scrolling for all of them
    is what made an opened block impossible to keep on screen."""
    found = re.search(r'htmx:sseMessage", function \(event\) \{([\s\S]{0,400}?)\n  \}\);', APP)
    assert found, "the handler is gone"
    assert "thread-scroll" in found.group(1)


def test_the_open_state_is_recorded_before_the_swap_and_restored_after():
    """Both halves, or it is a module that does nothing. The container is
    replaced with innerHTML twelve times a second, so anything not written down
    first is gone by the time there is somewhere to put it back."""
    assert "htmx:sseBeforeMessage" in STEPS
    assert "htmx:sseMessage" in STEPS
    assert "htmx:afterSwap" in STEPS


def test_it_never_cancels_the_frame_it_is_listening_to():
    """htmx:sseBeforeMessage is cancellable -- the extension reads what handlers
    return to decide whether to swap at all. Cancelling here would freeze the
    transcript with no error anywhere."""
    assert "preventDefault" not in STEPS


def test_an_id_is_escaped_rather_than_concatenated_into_a_selector():
    """The same rule `data-prompt` follows for hx-vals. An id is a message id
    and two integers today; a selector assembled by hand is how that stops being
    true safely."""
    assert "CSS.escape" in STEPS


def test_the_finished_bubble_repeats_the_live_container_s_id():
    """That is the whole mechanism for carrying an opened block across the
    `done` frame, which replaces the entire article: same id on the container,
    same ids inside it, because both come from the mark index."""
    message = (TEMPLATES / "chat/_message.html").read_text(encoding="utf-8")

    assert message.count('id="steps-{{ message.id }}" data-steps') == 2


def test_switching_a_tab_puts_its_body_back_at_the_top():
    """A tab is a radio and a panel is shown by CSS, so switching one changes
    nothing about the container that scrolls. Read half way down a long panel,
    switch to a short one, and the browser clamps the kept scrollTop to that
    panel's bottom: what lands on screen is the end of it above a screen of
    nothing, which reads as a page that failed to load.

    Nothing in CSS can reset a scroll position. Driven under a DOM stub before
    committing; what is pinned here is that the listener is delegated and keyed
    on the class rather than on one page's ids, because the next tabbed screen
    would otherwise have the same bug and no sign of it.
    """
    assert '.closest(".tabs__bar")' in SOURCE or 'closest(".tabs__bar")' in SOURCE
    assert 'querySelector(".tabs__body")' in SOURCE
    assert "scrollTop = 0" in SOURCE


def test_the_tab_reset_finds_the_container_that_actually_scrolls():
    """Resetting `.tabs__body` was a no-op on /admin/prompts for the whole life
    of the fix, because there the tabs sit inside `.admin-scroll > .admin-page`
    -- a plain block -- so the body has `height: auto` and the page is what
    scrolls. Setting scrollTop on an element that does not scroll says nothing
    and fails nothing.

    So the handler walks up for the first ancestor that can scroll, and the CSS
    stops claiming `.tabs__body` is one everywhere. Both halves are pinned:
    either alone leaves the bug.
    """
    admin = (ROOT / "web/static/css/admin.css").read_text(encoding="utf-8")
    app = (ROOT / "web/static/css/app.css").read_text(encoding="utf-8")

    # The scroller rule names where the tabs must be, not just the class. Which
    # stylesheet it is written in is not the point and is not asserted -- the
    # four declarations that make something a scroller now live once, in
    # app.css, and this selector is listed there with the rest.
    assert ".main > .tabs > .tabs__body" in admin + app
    assert "\n.tabs__body {" not in admin
    assert "\n.tabs__body {" not in app

    assert "overflowY" in SOURCE
    assert "scrollHeight > " in SOURCE


def test_a_tab_switch_moves_only_the_container_that_scrolls():
    """Switching a tab on /admin/prompts lifted the whole shell 56px, with the
    topbar gone off the top and a strip of bare background under everything.

    Two halves, and either one alone is enough to bring it back. The prompt
    cards' `.visually-hidden` labels are `position: absolute`. With no
    positioned ancestor they were placed against the page and stretched the
    document to 6771px behind an `overflow: hidden` root. And the handler used
    `scrollIntoView`, which scrolls every ancestor that can scroll, the root
    included. Measured in headless Chromium at 1640x930 before and after.
    """
    app = (ROOT / "web/static/css/app.css").read_text(encoding="utf-8")
    start = app.index(".scroll-region,")
    rule = app[start : app.index("}", start)]
    assert ".admin-scroll" in rule
    assert "position: relative" in rule

    code = re.sub(r"/\*.*?\*/", "", SOURCE, flags=re.S)
    start = code.index('closest(".tabs__bar")')
    handler = code[start : code.index("})();", start)]
    assert "scrollIntoView" not in handler
    assert "outer.scrollTop" in handler


def test_the_tab_bar_edge_fade_is_covered_when_nothing_overflows():
    """The covers were as wide as the shadows and solid for only 40% of that,
    so 60% of each shadow showed through with nothing to scroll to. On Shire
    that was a grey sliver at both ends of every tab bar. A cover has to be
    solid across the whole shadow. Measured in headless Chromium on
    /admin/prompts and /settings in both themes before and after.
    """
    admin = (ROOT / "web/static/css/admin.css").read_text(encoding="utf-8")
    start = admin.index("background-attachment: local, local, scroll, scroll")
    block = admin[admin.rindex(".tabs__bar {", 0, start) : start]
    cover = re.search(r"linear-gradient\(to right, var\(--bg\) (\d+)%, transparent\)", block)
    shadow = re.search(r"var\(--scrim\), transparent ([\d.]+)rem", block)
    sizes = re.search(r"background-size: ([\d.]+)rem 100%, [\d.]+rem 100%, ([\d.]+)rem 100%", block)
    assert cover and shadow and sizes, "the edge-fade rule changed shape; re-check it by eye"
    solid = float(sizes.group(1)) * int(cover.group(1)) / 100
    assert solid >= float(shadow.group(1)) == float(sizes.group(2))


def test_the_composer_is_as_wide_as_its_column_not_its_hint():
    """With `max-width` and auto margins alone, the box was as wide as its
    widest content inside a flex column, so the vision hint under it decided:
    768px for GPT-OSS ("has no vision, so images will not be sent") and 538px
    for a model that sees images. Reported from the live instance with four
    screenshots."""
    chat = (ROOT / "web/static/css/chat.css").read_text(encoding="utf-8")
    start = chat.index(".composer__inner {")
    rule = chat[start : chat.index("}", start)]
    assert "width: 100%" in rule
    assert "max-width: var(--thread-max-width)" in rule


def test_the_two_ends_of_the_shell_stay_level():
    """The sidebar footer and the composer sit either side of the same vertical
    edge and are both content-sized, so without a common floor they end at
    different heights: the footer's depends on which entries a reader's
    permissions allow, and the composer's on how much has been typed.

    They used to carry a top border each, which is how the misalignment was
    noticed. The borders are gone -- content scrolls under both edges, and an
    undrawn edge reads better than one that has to be aligned -- and the shared
    `min-height` stays, because two ends at different heights is visible without
    a border to prove it.
    """
    tokens = (ROOT / "web/static/css/tokens.css").read_text(encoding="utf-8")
    app = (ROOT / "web/static/css/app.css").read_text(encoding="utf-8")
    chat = (ROOT / "web/static/css/chat.css").read_text(encoding="utf-8")

    assert "--footer-height:" in tokens
    # A calc of the pieces the footer is built from, not a measured constant --
    # a number would stop being true the moment `--control-h` moved.
    assert "var(--control-h)" in tokens.split("--footer-height:")[1].split(";")[0]
    assert "min-height: var(--footer-height)" in app
    assert "min-height: var(--footer-height)" in chat

    # And neither draws the line again. Scoped to each rule's own block, so an
    # unrelated border elsewhere in either file does not fail this.
    footer = app.split(".sidebar__footer {")[1].split("}")[0]
    composer = chat.split(".composer {")[1].split("}")[0]
    assert "border-top" not in footer
    assert "border-top" not in composer


def test_the_panel_buttons_wait_for_a_connection_before_they_appear():
    """The canvas and the terminal both need an agent chat on a chosen
    connection. Before a chat exists both of those live in the composer -- a
    radio group and a select -- so the server cannot answer, and answering with
    the first profile anyway offered a terminal on an ordinary chat with nothing
    selected. Pressing it opened a panel that could not work.

    So the buttons render hidden and follow the event the composer already
    dispatches. Driven under a DOM stub; pinned here is that they are keyed on
    an attribute rather than on their own ids -- a third panel would otherwise
    need this written a third time.
    """
    index = (TEMPLATES / "chat/index.html").read_text(encoding="utf-8")

    # Four: the topbar's canvas and terminal buttons, and their entries in the
    # phone's window switch -- which carry no `data-toggle`, so the
    # handler below hides them and leaves closing the panel to the topbar's.
    assert index.count("{% if not chat %}data-agent-only hidden{% endif %}") == 4
    assert "[data-agent-only]" in SOURCE
    assert "lembas:agent-target" in SOURCE
    # An open panel pointed at a connection that has gone is closed, or it shows
    # one machine's files under a heading naming another.
    assert "setPanel" in SOURCE


def test_the_file_picker_asks_for_files():
    """One listing serves the directory picker and the file picker, because a
    second copy is a second place for the path arithmetic to be got subtly
    differently — and getting it differently means a file that opens to the
    wrong path, or to nothing."""
    app = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")

    assert "pick=file" in app
    assert "data-file-open" in app or "dataset.fileOpen" in app
    # Directories stay a step in file mode, or a file two folders down is
    # unreachable.
    assert app.count("[data-dir-open]") >= 2


def test_a_notification_is_never_shown_to_somebody_looking_at_the_page():
    """Three channels carry one arrival and they must not all fire at once: the
    toast is for somebody watching, the tab-title count for somebody in another
    tab, and the system notification for somebody elsewhere entirely.

    The service worker is the only place that can tell -- the server cannot see
    whether a window is focused, and the page cannot see a push it did not
    receive. Driven under a DOM stub; what is pinned here is that both halves
    of the decision exist.
    """
    worker = (ROOT / "web/static/js/sw.js").read_text(encoding="utf-8")

    # The page half: nothing but a toast while it is being looked at.
    assert "document.hidden" in SOURCE
    # The worker half: no notification when one of its own windows has focus.
    assert "clients[i].focused" in worker
    assert "showNotification" in worker


def test_the_tab_title_count_re_reads_its_base():
    """The title is rewritten by navigation and by a rename arriving out of
    band, so a base captured once would pin the old name until a reload."""
    assert "replace(/^\\(\\d+\\)\\s*/" in SOURCE


def test_permission_is_only_ever_asked_from_a_gesture():
    """`requestPermission` is refused outside one, silently. A checkbox restored
    on load and acted upon would look exactly like a switch that does nothing,
    which is the failure this codebase keeps cataloguing."""
    settings = (TEMPLATES / "settings.html").read_text(encoding="utf-8")

    # A button, not an input whose `change` writes.
    assert "data-notify-toggle" in settings
    assert '<button class="btn" type="button" data-notify-toggle' in settings
    assert "requestPermission" in SOURCE


def test_turning_notifications_off_also_drops_the_registration():
    """Leaving it would mean this server going on POSTing to a third-party push
    service for something the reader has switched off."""
    assert "/api/push/unsubscribe" in SOURCE
    assert "pushManager" in SOURCE


def test_no_page_loads_a_script_that_the_base_template_already_loads():
    """`/messages` listed `composer.js` and `commands.js` in its `scripts`
    block, and `base.html` already loads both on every page -- so that screen
    ran each of them twice.

    Each is an IIFE with its own state, and `stopPropagation()` does not stop a
    second listener already bound to the same node. Two composer menus stacked
    on each other; one Enter on a highlighted `/` item ran the command twice, so
    `/help` opened two dialogs and `/image` posted the message twice; an `@`
    mention attached its file twice; and `Alt+B`, `Alt+E`, `Alt+T` and `Alt+I`
    each toggled their panel twice, which is to say did nothing at all.

    None of that looks like a script loaded twice, which is why this is a sweep
    over every template rather than a note about one of them.
    """
    import re
    from pathlib import Path

    templates = Path(__file__).resolve().parents[1] / "src/lembas/web/templates"
    # Both spellings, because the way a static URL is written has changed once
    # already: `url_for('static', path='js/x.js')` became `asset('js/x.js')`
    # when assets started carrying the release. The assertion below that the set
    # is non-empty is what turned that rename into a loud failure rather than a
    # sweep that silently stopped sweeping -- keep it.
    pattern = re.compile(r"(?:path=|asset\()'js/([a-z_]+\.js)'")
    always = set(pattern.findall((templates / "base.html").read_text()))
    assert always, "base.html stopped loading any script; this test is now blind"

    for template in templates.rglob("*.html"):
        if template.name == "base.html":
            continue
        again = set(pattern.findall(template.read_text())) & always
        assert not again, f"{template.name} re-loads {sorted(again)}, which base.html has"
