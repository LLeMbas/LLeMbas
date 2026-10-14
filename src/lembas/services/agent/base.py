"""What the agent package shares: file revisions and the errors.

Nothing runs on this machine: a linked device acts for itself, through
`device_link`. What is left here is what the terminal and the canvas have in
common -- `revision_of` and `Conflict` for a file edited from a page, and
`ExecError` for when nothing can act at all.
"""

from __future__ import annotations


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


__all__ = [
    "Conflict",
    "ExecError",
    "revision_of",
]
