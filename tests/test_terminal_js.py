"""The terminal panel, checked without a runtime.

There is no JavaScript test runner here and hard rule 1 keeps Node out of the
project, so the behaviour is driven by hand under a DOM stub before committing.
Almost everything this file gets wrong is silent: `fit()` measures `offsetWidth`,
which is 0 inside a `[hidden]` ancestor, so fitting while closed does nothing at
all and leaves an 80-column shell in a 34rem panel; a window `resize` event does
not fire when the sidebar is toggled, which is the commonest way the panel
changes size; and a handshake against the app root fails into `onerror` and
blames the proxy for a connection the reader simply has not chosen yet.

What the suite can pin are the guards those three depend on, and the properties
around them whose absence looks exactly like working.
"""

from __future__ import annotations

from pathlib import Path

import lembas

from .conftest import js_code, js_function, js_says, script

SOURCE = script("terminal.js")
CODE = js_code(SOURCE)
TEMPLATES = Path(lembas.__file__).parent / "web/templates"


def _after(text: str, marker: str, span: int = 500) -> str:
    """The code just after a marker -- for a handler with no name to ask for."""
    return text[text.index(marker) :][:span]


# --- Connecting ---------------------------------------------------------------
def test_connect_refuses_a_panel_with_no_target():
    """With no connection chosen `data-url` is empty, and the URL below then
    becomes `ws://host?cols=80&rows=24` -- a WebSocket handshake against the
    application root. That fails into `onerror`, whose message guesses at a
    proxy not passing upgrades through: a confident, wrong explanation for
    something the reader has simply not chosen yet.

    The guard has to come before the socket, which is the whole assertion.
    """
    connect = js_function(SOURCE, "connect")

    assert "!panel.dataset.url" in connect, "the empty-target guard is gone"
    assert connect.index("!panel.dataset.url") < connect.index("new WebSocket"), (
        "a panel with no target still opens a socket"
    )
    # And says so, rather than leaving the panel silent.
    assert "say(" in connect[: connect.index("new WebSocket")]


def test_the_socket_url_is_built_from_the_panel_s_own_path():
    """The path is server-rendered onto the panel, and `draft.js` rewrites it
    before a chat exists. Assembling one here from a chat id would be a second
    place for it to be got subtly differently, and a wrong one connects to
    another machine's shell rather than failing."""
    connect = js_function(SOURCE, "connect")

    assert "location.host + panel.dataset.url" in connect
    # A page served over TLS must not open a plain socket; browsers block it,
    # which reads as the terminal never connecting.
    assert js_says(connect, 'location.protocol === "https:"', '"wss://"', '"ws://"')


def test_a_deliberate_close_is_flagged_before_it_is_made():
    """Both reconnect paths close a socket on purpose -- the server disconnecting
    a window that stopped reading, and the composer changing which machine the
    panel points at. The flag is what stops "Disconnected. Close and reopen to
    reconnect." being reported for a connection nobody lost, so it has to be set
    before the close, not after it."""
    for marker in ('payload.t === "behind"', "repointTerminal = function"):
        window = _after(CODE, marker)
        assert window.index("closedOnPurpose = true") < window.index("socket.close()"), marker


def test_a_malformed_control_frame_is_ignored():
    """Text frames carry JSON and PTY bytes do not, and the two share one
    `onmessage`. An exception here kills the handler for the rest of the
    session, so the shell keeps running and the panel stops showing it."""
    control = js_function(SOURCE, "control")
    assert "try {" in control
    assert control.index("JSON.parse") < control.index("catch")


def test_bytes_go_through_to_xterm_undecoded():
    """xterm's decoder is stateful across calls, so a multi-byte character split
    across two frames still lands correctly -- which is exactly why the server
    never decodes either. Decoding here would break the same character in the
    other direction."""
    assert js_says(CODE, "opened.binaryType", '"arraybuffer"')
    assert js_says(CODE, "term.write(", "new Uint8Array(event.data)")
    assert "TextDecoder" not in CODE


# --- Sizing -------------------------------------------------------------------
def test_a_hidden_panel_is_never_fitted():
    """`fit()` measures `offsetWidth`, which is 0 inside a `[hidden]` ancestor.
    Fitting while closed therefore succeeds, silently, at nothing -- and leaves
    an 80-column terminal in a panel that is not 80 columns wide, which reads as
    xterm being broken rather than as a call made too early."""
    refit = js_function(SOURCE, "refit")
    assert refit.index("visible()") < refit.index("fit.fit()")

    visible = js_function(SOURCE, "visible")
    assert 'hasAttribute("hidden")' in visible
    assert "offsetWidth > 0" in visible, "an ancestor's `hidden` measures as zero, not as hidden"


def test_opening_waits_a_frame_before_fitting():
    """The panel has just had `hidden` removed and has no measured width yet, so
    a fit in the same turn is the silent no-op above."""
    opener = js_function(SOURCE, "open")
    assert opener.index("requestAnimationFrame") < opener.index("refit()")


def test_the_panel_watches_its_own_size():
    """A window `resize` event does not fire when the sidebar is toggled or a
    panel opens beside this one, and those are by far the commonest ways this
    panel changes size. Without the observer the terminal keeps the width it had
    when it was opened and wraps every line at the wrong column."""
    build = js_function(SOURCE, "build")
    assert "ResizeObserver" in build
    assert "observe(panel)" in build


def test_a_resize_is_told_to_the_far_side():
    """Fitting changes what this end draws; the PTY on the other end keeps its
    old window size until it is told, so a full-screen program like `vim` or
    `htop` paints to a geometry that is no longer there."""
    refit = js_function(SOURCE, "refit")
    assert js_says(refit, 't: "resize"', "cols: term.cols", "rows: term.rows")


def test_closing_the_panel_leaves_the_session_alone():
    """`write()` is queued internally, so disposing mid-output drops it; keeping
    the object is what makes reopening instant; and the shell on the far side
    outlives this panel by design. Closing must therefore do nothing at all."""
    toggle = CODE[CODE.index('"lembas:toggle"') : CODE.index('panel.addEventListener("click"')]
    assert "open()" in toggle
    assert "dispose" not in toggle
    assert ".close(" not in toggle
    # A command's exit mark is a decoration and is disposed with its command;
    # the terminal itself never is.
    assert "term.dispose" not in CODE and "fit.dispose" not in CODE, (
        "nothing here may dispose the terminal"
    )


# --- The theme ----------------------------------------------------------------
def test_the_theme_is_pushed_in_when_it_changes():
    """xterm holds colours as values, not as variables, so it never notices a
    `data-theme` swap. Without this, switching to `shire` leaves a black
    rectangle in a light interface."""
    assert '"lembas:theme"' in CODE
    assert js_says(CODE, "term.options.theme", "readTheme()")


def test_a_colour_slot_with_no_token_is_left_off_entirely():
    """xterm treats `undefined` as a colour and renders it black, so a missing
    ANSI token would not fall back -- it would paint that slot black on a light
    theme, which is one unreadable colour rather than a visible failure."""
    read = js_function(SOURCE, "readTheme")
    assert js_says(read, "if (value)", "theme[name]", "value")


# --- Handing a command to the chat --------------------------------------------
def test_a_command_line_is_never_rendered_as_markup():
    """Everything shown here came off somebody's machine: a command line, a
    working directory, a message from the far side. Hard rule 6 again."""
    assert "innerHTML" not in CODE
    assert "textContent" in js_function(SOURCE, "say")
    assert "textContent" in js_function(SOURCE, "showLast")


def test_the_button_puts_it_in_the_box_rather_than_sending_it():
    """What a machine printed is exactly the sort of text somebody should read
    before a model does, and the box is where that happens. Only the `auto:
    send` mode skips that, and only because it was chosen."""
    send = js_function(SOURCE, "sendToChat")
    assert "intoComposer" in send
    assert "sendStraightToChat" not in send


def test_writing_into_the_composer_repaints_its_mirror():
    """The mirror is only repainted on `input` and after a swap, and this is
    neither -- so without the call the highlighting sits over the previous text
    until the next keystroke, shifted by however much was just inserted."""
    into = js_function(SOURCE, "intoComposer")
    assert "paintComposer" in into
    assert "autosize" in into


def test_the_automatic_path_never_steals_focus():
    """It fires while somebody is typing in the terminal, and yanking the caret
    out of a shell mid-command is the sort of thing that gets a feature switched
    off for good."""
    into = js_function(SOURCE, "intoComposer")
    assert js_says(into, "!quiet", "input.focus()")
    # A finished mark is the automatic path, and it is the quiet one.
    assert "intoComposer(text, true)" in js_function(SOURCE, "commandDone")


def test_a_screen_scrape_says_that_is_what_it_is():
    """Without shell integration this is the last forty rows as they appeared,
    wraps and all. Presenting that as a command and its output would put a
    precise-looking block in front of a model that is not one."""
    capture = js_function(SOURCE, "capture")
    assert "scraped()" in capture
    assert "say(" in capture[capture.index("scraped()") :]


def test_auto_is_disabled_rather_than_degraded():
    """Forty arbitrary lines attached to every message is worse than nothing
    attached at all, so the control is switched off where it cannot work -- and
    switched back off if it was already on when the markers went away."""
    apply_ = js_function(SOURCE, "applyIntegration")
    assert "var usable = osc" in apply_
    assert js_says(apply_, "auto.disabled", "!usable")
    assert js_says(apply_, "!usable", 'autoMode !== "off"', 'setAuto("off")')


def test_auto_is_not_remembered_between_page_loads():
    """A switch that forwards every command you run to a model is not something
    to inherit from last week."""
    assert "localStorage" not in CODE
    assert "sessionStorage" not in CODE


# --- Where it is loaded -------------------------------------------------------
def test_xterm_is_loaded_only_where_a_shell_can_be_opened():
    """xterm is 280KB, more than everything else vendored here together. Hard
    rule 1 allows it exactly once, and only on a chat that can open a terminal
    -- an unconditional script tag would put it on every page in the
    application."""
    page = (TEMPLATES / "chat/index.html").read_text(encoding="utf-8")

    guarded = ""
    at = 0
    while (at := page.find("{% if terminal_enabled %}", at)) != -1:
        guarded += page[at : page.index("{% endif %}", at)]
        at += 1

    for asset in ("vendor/xterm.js", "vendor/xterm-addon-fit.js", "js/terminal.js"):
        assert page.count(asset) == 1, f"{asset} is referenced more than once"
        assert asset in guarded, f"{asset} is loaded outside the terminal_enabled guard"


def test_a_handler_only_touches_the_connection_it_belongs_to():
    """`close()` queues its event rather than firing it, and both reconnect
    paths close the old socket and immediately open a new one. So the old
    socket's `close` arrives *after* the new one is assigned -- and a handler
    reading the module-level `socket` reached past its own connection into the
    live one and set it to null.

    Output kept arriving, because `onmessage` is bound to the object. But
    `send`, `term.onData` and the paste handler all gate on
    `socket && readyState === OPEN`, so every keystroke was dropped and no
    resize was ever sent again -- on a panel that looked perfectly healthy. And
    `closedOnPurpose` had been cleared for the new connection, so it announced
    "Disconnected" about a shell that had just reconnected successfully.
    """
    code = js_code(script("terminal.js"))

    assert "var opened = new WebSocket(url)" in code
    # Every handler compares itself against the current connection first.
    for handler in ("onmessage", "onclose", "onerror"):
        body = code.split(f"opened.{handler} = function", 1)[1]
        guard = body[: body.index("{", body.index(")")) + 200]
        assert "socket !== opened" in guard, handler
    assert "socket.onclose = function" not in code, "a handler bound to the shared variable"


# --- A device shell's own marks -----------------------------------------------
def test_osc_133_and_7_are_read_in_the_browser():
    """The device's shell marks its commands; the panel registers handlers for
    both codes on xterm's parser, and the server parses nothing."""
    build = js_function(SOURCE, "build")
    assert "registerOscHandler(133, onCommandMark)" in build
    assert "registerOscHandler(7, onWhere)" in build


def test_the_handlers_act_only_when_the_device_said_so():
    """A handler that returns false leaves the sequence to xterm, as before --
    every shell that is not a device's with its integration on."""
    for name in ("onCommandMark", "onWhere"):
        body = js_function(SOURCE, name)
        assert "if (!osc" in body and "return false" in body


def test_the_ready_frame_decides():
    ready = _after(CODE, 'payload.t === "ready"', 900)
    assert "osc = !!payload.osc" in ready


def test_the_cwd_is_written_as_text():
    assert js_says(js_function(SOURCE, "onWhere"), "where.textContent = path")


def test_previous_and_next_are_offered_only_with_marks():
    show = js_function(SOURCE, "showMarkControls")
    assert "button.hidden = !osc" in show
    template = (TEMPLATES / "chat/_terminal.html").read_text(encoding="utf-8")
    assert "data-terminal-prev hidden" in template and "data-terminal-next hidden" in template


def test_marks_are_dropped_with_the_buffer_they_point_into():
    """`behind` and repointing reset the screen; markers into the old buffer
    would visit lines that are not there."""
    assert "forgetMarks();" in _after(CODE, 'payload.t === "behind"', 600)
    assert "forgetMarks();" in _after(CODE, "repointTerminal = function", 400)


def test_the_server_parses_no_marks_for_a_device_shell():
    """The device's marks pass through to the panel untouched, and the server
    keeps no command of its own: there is no `/terminal/last` and no
    `command` frame any more."""
    from lembas.services.agent.terminal import DeviceSession

    session = DeviceSession("c", token_id="t", owner_id="u", label="device-1")
    marked = b"\x1b]133;A\x07\x1b]133;C\x07out\x1b]133;D;0\x07"
    session.feed(marked)
    assert b"".join(session._scrollback) == marked
    assert not hasattr(session, "latest") and not hasattr(session, "integration")
    assert "/last" not in CODE and '"command"' not in CODE
