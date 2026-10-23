"""Dictation and read-aloud, checked without a runtime.

There is no JavaScript test runner here and hard rule 1 keeps Node out of the
project, so the behaviour is driven by hand under a DOM stub before committing.
Both halves of this file are progressive -- without it the composer and the
bubbles still work, they simply have two buttons that do nothing -- which is
also the shape of every way it can break: a microphone left open, a button left
disabled, a reply read out twice. None of them raises anything.

What the suite can pin is that there is one recording state machine and one
`<audio>` element, that every path out of a recording puts the button back, and
that writing a transcript into the composer goes through the same two calls
every other outside writer makes.
"""

from __future__ import annotations

import re

from .conftest import js_code, js_function, js_says, script

SOURCE = script("audio.js")
CODE = js_code(SOURCE)
COMMANDS = js_code(script("commands.js"))


# --- One state machine --------------------------------------------------------
def test_there_is_one_delegated_listener_for_both_buttons():
    """Delegated, because a message bubble is rendered from four places and
    swapped by htmx constantly -- a listener bound per button would miss every
    reply that arrived after load, which is all of them. One listener, and the
    two buttons told apart by `closest`."""
    assert CODE.count('document.addEventListener("click"') == 1
    click = CODE[CODE.index('document.addEventListener("click"') :]
    assert 'closest("[data-mic]")' in click
    assert 'closest("[data-speak]")' in click


def test_the_shortcuts_press_the_button_rather_than_repeating_the_machine():
    """`Alt+M` and `Alt+R` dispatch by finding the existing control and calling
    `.click()`, so this file keeps its one delegated listener and there is no
    second copy of the recording state machine to fall out of step with it.

    Asserted from commands.js, which is where the second copy would appear.
    """
    assert js_says(COMMANDS, "[data-mic]", "mic.click()")
    assert js_says(COMMANDS, "[data-speak]", ".click()")

    # Nothing here is exported, so there is nothing for a shortcut to reach for
    # instead -- which is what keeps that true rather than merely current.
    assert not re.search(r"window\.lembas\.\w+\s*=", CODE)
    assert "MediaRecorder" not in COMMANDS


def test_the_microphone_is_released_the_moment_recording_stops():
    """Leaving the track live keeps the browser's recording indicator on long
    after anyone is talking -- a self-hosted tool that appears to still be
    listening is the one bug here nobody would forgive. Released in the `stop`
    handler, before the upload, because the upload is a network round trip."""
    start = js_function(SOURCE, "startRecording")
    stop_handler = start[start.index('addEventListener("stop"') :]
    assert "track.stop()" in stop_handler
    assert stop_handler.index("track.stop()") < stop_handler.index("upload(")


def test_a_recording_that_captured_nothing_does_not_upload():
    """An empty blob is a press and an immediate second press. Uploading it
    costs a round trip to be told nothing was heard, and leaves the button
    disabled while that happens."""
    start = js_function(SOURCE, "startRecording")
    assert js_says(start, "blob.size", "upload(", "else", 'setMicState(button, "idle")')


def test_every_way_out_of_an_upload_puts_the_button_back():
    """`working` is the only state that disables the button, and a disabled
    microphone that never comes back is a feature somebody has to reload the
    page to use again. Both arms of the promise reset it -- and the error arm is
    the one that gets forgotten, because it is the one nobody exercises."""
    upload = js_function(SOURCE, "upload")
    assert 'setMicState(button, "working")' in upload
    assert upload.count('setMicState(button, "idle")') == 2, "a path out leaves it disabled"
    assert ".catch(" in upload

    state = js_function(SOURCE, "setMicState")
    assert js_says(state, "button.disabled", '"working"')


def test_the_label_says_what_the_next_press_will_do():
    """The button is a toggle with one icon slot, so the accessible name is the
    only thing saying which half of the toggle it is in."""
    state = js_function(SOURCE, "setMicState")
    assert '"aria-label"' in state
    assert '"Stop recording"' in state and '"Dictate a message"' in state
    assert js_says(state, "button.title", 'getAttribute("aria-label")')


def test_plain_http_is_explained_rather_than_silently_doing_nothing():
    """`getUserMedia` is undefined on plain http, which a self-hosted install on
    a LAN address often is. Without the guard the button does nothing at all,
    forever, with nothing in the console a reader would find -- and the fix is
    not something the page can apply for them."""
    start = js_function(SOURCE, "startRecording")
    guard = start[: start.index("navigator.mediaDevices.getUserMedia({")]
    assert "!navigator.mediaDevices" in guard
    assert 'typeof MediaRecorder === "undefined"' in guard
    assert "HTTPS" in guard


def test_a_refused_microphone_is_reported():
    """Permission can be blocked at the site or at the system, and both reject
    the promise. Left uncaught it is a button press that does nothing."""
    start = js_function(SOURCE, "startRecording")
    assert ".catch(" in start
    assert "microphoneError(error)" in start[start.index(".catch(") :]
    # Which refusal it was is test_stream_browser_js.py's business.


def test_dictation_audio_is_never_stored():
    """It has no owner, no row, and nothing would ever sweep it. It goes to the
    transcription endpoint and nowhere else -- an attachment path would give a
    voice note a permanent home nobody asked for."""
    assert "/api/audio/transcribe" in CODE
    assert "/api/files" not in CODE


# --- What reaches the composer ------------------------------------------------
def test_the_transcript_is_appended_rather_than_replacing_what_was_typed():
    """Dictation is usually finishing a thought that was already half typed.
    Overwriting is the complaint the terminal's Send was rewritten to fix, and
    it would be the same complaint here."""
    insert = js_function(SOURCE, "insertTranscript")
    assert js_says(insert, "existing", "input.value", "existing"), "the transcript replaces the box"
    assert "input.value.length" in insert, "the caret is left at the end"


def test_writing_into_the_composer_repaints_its_mirror():
    """The mirror repaints on `input` and after a swap, and this is neither -- so
    without the call the highlighting sits over the previous text, shifted by
    however much was just dictated, until the next keystroke."""
    insert = js_function(SOURCE, "insertTranscript")
    assert "paintComposer" in insert
    assert "autosize" in insert


# --- Reading a reply aloud ----------------------------------------------------
def test_there_is_one_audio_element_for_the_page():
    """Two replies talking over each other is never what was wanted, and a
    shared element makes that impossible rather than merely unlikely."""
    player = js_function(SOURCE, "audioPlayer")
    assert "if (!player)" in player
    assert CODE.count("new Audio()") == 1


def test_a_second_press_stops_it():
    """The button is the only control there is -- there is no separate stop --
    so pressing the one that is speaking has to mean stop rather than start
    again from the beginning."""
    speak = js_function(SOURCE, "speak")
    assert "if (speaking === button)" in speak
    assert speak.index("element.pause()") < speak.index("element.src")


def test_a_refused_autoplay_is_not_reported_as_a_failure():
    """Autoplay policies reject a `play()` the reader did not ask for. That is
    the browser working as intended, and an error toast for it would fire on
    every page load where a reply had just finished."""
    speak = js_function(SOURCE, "speak")
    tail = speak[speak.index("element.play()") :]
    assert "markSpeaking(null)" in tail
    assert "notify" not in tail


def test_a_finished_reply_is_read_out_once():
    """`data-speak-auto` is set on one frame only, and any swap can bring it in
    -- so this watches them all. The attribute is cleared *before* the reply is
    played, or a later swap of the same bubble starts it again, and the
    transcript reads itself out from the top."""
    arrivals = js_function(SOURCE, "playArrivals")
    assert arrivals.index('removeAttribute("data-speak-auto")') < arrivals.index("speak(button)")
    assert '"htmx:afterSettle"' in CODE


def test_the_microphone_leaves_idle_before_it_waits_for_permission():
    """The dispatcher starts a recording whenever the button reads `idle`, and
    the state only moved once `getUserMedia` had resolved. The browser's
    permission prompt sits in between, so every click while it was up opened
    another stream and another MediaRecorder -- and only the last was kept, so
    stopping released one and left the rest live. The browser's recording
    indicator then stayed on until the tab was closed.
    """
    code = js_code(script("audio.js"))
    body = js_function(code, "startRecording")

    # Against the *call*, not the capability check above it, which also names
    # getUserMedia and legitimately comes first.
    call = body.index("getUserMedia({ audio: true })")
    assert body.index('setMicState(button, "working")') < call
    # And a refusal has to put it back, or the button can never be pressed again
    # -- including by somebody who has just gone and allowed it.
    catch = body[body.index(".catch(") :]
    assert 'setMicState(button, "idle")' in catch
