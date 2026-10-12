"""What the agent package shares: output cleaning, file revisions, and the errors.

Nothing runs on this machine: a linked device acts for itself, through
`device_link`. What is left here is what the terminal, the canvas and the
capture of command output have in common -- `clean_output` for anything a
command produced, `revision_of` and `Conflict` for a file edited from a page,
and `ExecError` for when nothing can act at all.
"""

from __future__ import annotations

import re

# Terminal escape sequences, stripped from anything a command produced. They are
# inert in escaped HTML, but this text also re-enters the model's context, where
# they are a known way of hiding instructions, and it may end up in a log a
# person later cats, where they hijack the terminal.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


class ExecError(Exception):
    """Nothing could be run at all: the device is not linked, or it refused.

    Distinct from a command that ran and failed -- that is output with a
    non-zero status, which the model should read and react to. This is not
    being able to act, which is a message for a person.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def revision_of(mtime: int, size: int) -> str:
    """An opaque token saying which version of a file was read.

    Round-tripped through a hidden field and compared on the way back in. Not a
    hash: hashing means reading the whole file again on every save, and this
    catches the case it exists for -- somebody else's editor, a build, a
    checkout -- without it.
    """
    return f"{mtime}:{size}"


class Conflict(Exception):
    """The file moved between being opened and being saved.

    Carries the revision found instead, so the card offering Overwrite has
    something to compare against.
    """

    def __init__(self, found: str = "") -> None:
        super().__init__("That file changed after it was opened.")
        self.found = found


def clean_output(data: bytes | str, *, limit: int) -> tuple[str, bool]:
    """Decode, strip escape sequences, and cap. Returns (text, truncated)."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    text = _ANSI.sub("", text)
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + "\n… (truncated)", True


__all__ = [
    "Conflict",
    "ExecError",
    "clean_output",
    "revision_of",
]
