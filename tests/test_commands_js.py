"""The keyboard, checked without a runtime.

There is no JavaScript test runner here and hard rule 1 keeps Node out of the
project, so the behaviour is driven by hand under a DOM stub before committing.
What can be pinned in the suite is the invariant the file states about itself:
`/help` reads `SHORTCUTS`, so a shortcut that is not in that list is a shortcut
nobody can discover. That is the direction this actually rots -- a key gets
added to the handler and the sheet is forgotten.
"""

from __future__ import annotations

import re
from pathlib import Path

import lembas

SOURCE = (
    Path(lembas.__file__).parent / "web/static/js/commands.js"
).read_text(encoding="utf-8")

# The declared list, up to where the command table starts.
SHORTCUTS = SOURCE[SOURCE.index("var SHORTCUTS") : SOURCE.index("/* --- The table")]


def test_every_letter_key_the_handlers_match_is_described():
    letters = {match[-1] for match in re.findall(r'event\.code === "Key([A-Z])"', SOURCE)}
    assert letters, "no letter shortcuts found at all, which means the regex is wrong"

    missing = [letter for letter in sorted(letters) if f"+ {letter}" not in SHORTCUTS]
    assert not missing, f"not in /help: {missing}"


def test_the_three_new_ones_are_there():
    """Named rather than only counted, because the point of them is being
    findable: Enter already sends *inside the box*, and dictation and read-aloud
    were click-only."""
    assert "Ctrl/⌘ + Enter" in SHORTCUTS
    assert "Alt + M" in SHORTCUTS
    assert "Alt + R" in SHORTCUTS


def test_dictation_is_not_bound_to_alt_d():
    """Alt+D is the address bar in Chrome and Firefox on Windows and Linux. A
    shortcut the browser wins is a shortcut that looks broken."""
    assert 'event.code === "KeyD"' not in SOURCE


def test_the_shortcuts_are_matched_on_the_physical_key():
    """The file's own stated rule: `event.code`, so a Dvorak or Slovak layout
    gets the same shortcuts rather than whichever letters sit there."""
    assert "event.key ===" not in SOURCE


def test_send_from_anywhere_never_means_stop():
    """Send and Stop are the same element. Ctrl+Enter reaching it while it is
    Stop would abandon a reply on a key people press to send -- and Esc already
    stops."""
    window = SOURCE[SOURCE.index('event.code === "Enter"') :][:600]
    assert 'composerAction === "send"' in window


def test_temp_keeps_what_the_new_chat_screen_already_chose():
    """`/temp` went to a bare `/chat?temporary=1`, so on a new-chat screen with
    a model picked it quietly swapped back to the default model."""
    window = SOURCE[SOURCE.index('name: "temp"') :][:600]
    assert 'window.location = "/chat?temporary=1"' not in window
    assert "window.location.search" in window
    assert 'query.set("temporary", "1")' in window


# --- A linked device's own commands -------------------------------------------------
def test_a_device_command_is_sent_as_the_message_it_is():
    """Chosen from the menu, `/name args` goes as the composer's own submit --
    queued behind a reply exactly as typed text is -- never as a click on the
    send button, which is the stop button while a reply is written."""
    send = SOURCE[SOURCE.index("function sendToDevice") :][:600]
    assert "requestSubmit()" in send and ".click()" not in send


def test_a_name_this_table_has_stays_this_table_s():
    """`/compact` here already compacts the device's session; a device entry of
    the same name would be one that never runs."""
    merged = SOURCE[SOURCE.index("function fromDevice") :][:400]
    assert "local.indexOf(command.name) === -1" in merged


def test_find_knows_only_this_table():
    """A device command typed in full is not intercepted: it is sent as written,
    and the device expands it."""
    find = SOURCE[SOURCE.index("function find(value)") :][:500]
    assert "fromDevice" not in find and "deviceCommands" not in find


def test_the_device_s_commands_are_marked_as_its_own():
    assert 'dataset.deviceLabel' in SOURCE and "data-command-input" in SOURCE
