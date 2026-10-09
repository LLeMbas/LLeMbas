"""What an agent chat needs from the machine it acts on.

One interface, currently one implementation. It exists as an interface anyway
because the *snapshot* is the load-bearing part: a generation outlives the
request that started it, so everything a runner needs -- the host, the decrypted
credential, the mode, the project directory -- has to be read while the session
is open and carried, not looked up later. That is the same reason `Endpoint` is
a frozen copy of a `Connection` and `ToolContext` holds an owner id rather than
a `User`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

# What a command may weigh before it is cut off. Per call; the reply also has a
# total, in policy.Limits.
DEFAULT_MAX_BYTES = 64 * 1024
DEFAULT_TIMEOUT = 60.0

# Terminal escape sequences, stripped from anything a command produced. They are
# inert in escaped HTML, but this text also re-enters the model's context, where
# they are a known way of hiding instructions, and it may end up in a log a
# person later cats, where they hijack the terminal.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


@dataclass(frozen=True)
class ExecRequest:
    """One command to run."""

    command: str
    cwd: str = ""
    timeout: float = DEFAULT_TIMEOUT
    max_bytes: int = DEFAULT_MAX_BYTES


@dataclass(frozen=True)
class ExecResult:
    """What running it produced.

    `output` is stdout and stderr interleaved, because a shell transcript is
    what the model needs to read and separating them loses the ordering that
    makes an error make sense.
    """

    exit_status: int
    output: str
    truncated: bool = False
    timed_out: bool = False
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_status == 0 and not self.timed_out


class ExecError(Exception):
    """Nothing could be run at all: the host refused, or the credential did.

    Distinct from a command that ran and failed -- that is an `ExecResult` with
    a non-zero status, which the model should read and react to. This is the
    reply not being able to act, which is a message for a person.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Target:
    """A machine an agent chat acts on, read while the session was open.

    Holds the decrypted credential and nothing else does. `generation` clears it
    when the reply ends, because a finished `Generation` lingers for five
    minutes so late followers get the final frames, and a private key should not
    linger with it.
    """

    kind: str
    label: str
    project_dir: str = ""
    spec: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteEntry:
    """One line of a directory listing, with enough to draw it.

    Separate from `list_dir`, which returns bare names and backs the
    `file_list` tool. That contract is a list of names and must not change
    under a model mid-conversation, so a picker -- which has to tell a
    directory from a file before it knows whether the row can be walked into
    -- gets its own method rather than a widened one.
    """

    name: str
    is_dir: bool
    size: int = 0
    modified: int = 0

    @property
    def is_hidden(self) -> bool:
        return self.name.startswith(".")


@dataclass(frozen=True)
class RemoteFile:
    """A file as somebody is about to edit it, rather than as a model reads it.

    Separate from what `read_file` returns for the same reason `RemoteEntry` is
    separate from `list_dir`: the model-facing contract is right for a model and
    wrong here. `read_file` runs its result through `clean_output`, which strips
    escape sequences and decodes with errors="replace" -- so a file opened
    through it and saved back would come out rewritten.

    `binary` means there is nothing safe to put in a textarea, and the tab opens
    read-only. `truncated` means the same for a different reason: saving back
    the first 256KB of a larger file is how the rest of it is deleted.
    """

    text: str
    size: int = 0
    mtime: int = 0
    truncated: bool = False
    binary: bool = False

    @property
    def revision(self) -> str:
        return revision_of(self.mtime, self.size)


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


class Executor(Protocol):
    """How a target is acted on. Nothing implements it on this machine: a linked
    device acts for itself, through `device_link`."""

    async def run(self, request: ExecRequest) -> ExecResult: ...

    async def read_file(self, path: str, *, max_bytes: int) -> str: ...

    async def write_file(self, path: str, text: str) -> int: ...

    async def read_text(self, path: str, *, max_bytes: int) -> RemoteFile: ...

    async def write_text(self, path: str, text: str, *, if_unchanged: str) -> RemoteFile: ...

    async def list_dir(self, path: str) -> list[str]: ...

    async def scan_dir(self, path: str) -> list[RemoteEntry]: ...


def clean_output(data: bytes | str, *, limit: int) -> tuple[str, bool]:
    """Decode, strip escape sequences, and cap. Returns (text, truncated)."""
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    text = _ANSI.sub("", text)
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + "\n… (truncated)", True


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_TIMEOUT",
    "ExecError",
    "ExecRequest",
    "ExecResult",
    "Executor",
    "RemoteEntry",
    "Target",
    "clean_output",
]
