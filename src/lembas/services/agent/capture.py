"""One command and its output, kept so it can be handed to a model.

Bounded at both ends rather than only the front. A build that fails ten
megabytes in has the invocation and the configuration at the top and the error
at the bottom, and either half alone is the wrong half.

Raw bytes are kept and decoded only when somebody asks. Head/tail slicing
splits UTF-8 characters at will, and `base.clean_output` decodes with
`errors="replace"`, which is exactly the right handling -- decoding eagerly per
chunk would be the same mistake the terminal pump already avoids.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field

from lembas.services.agent.base import clean_output

# What one command's output may keep, at each end.
CAPTURE_HEAD_BYTES = 48 * 1024
CAPTURE_TAIL_BYTES = 16 * 1024
# The command line itself. Longer than any command and shorter than a paste.
CAPTURE_COMMAND_BYTES = 4 * 1024
# One line of output. A minified bundle on one line is not worth keeping whole.
MAX_LINE_CHARS = 2000

# C0 except tab and newline, and the C1 block. Not in `clean_output`, which
# `shell_run` shares: there a control character inside a file's contents is
# data. Here it is a terminal being driven.
_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def flatten(text: str) -> str:
    """What the screen would have shown, from what the wire carried.

    The highest-value transform here by a distance. A progress bar redraws
    itself by returning to the start of the line and writing again; keeping
    every state turns two megabytes of `pip install` into two megabytes of
    spinner in somebody's prompt. Only the last state of a line was ever
    visible, so only the last state is kept.
    """
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if "\r" in line:
            line = line.rsplit("\r", 1)[-1]
        lines.append(_CONTROLS.sub("", line)[:MAX_LINE_CHARS])
    return "\n".join(lines).strip("\n")


def fenced(text: str) -> str:
    """A fence long enough that the content cannot end it early.

    Output containing three backticks would otherwise break out, and everything
    after it would read to the model as prose rather than as what a machine
    printed. That is a real injection route and it costs one line to close.
    """
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}console\n{text}\n{ticks}"


@dataclass
class Capture:
    """A command, and as much of its output as is worth keeping."""

    seq: int = 0
    command: str = ""
    cwd: str = ""
    started: float = field(default_factory=time.monotonic)
    ended: float = 0.0
    exit_status: int | None = None  # None while it is still running

    head: bytearray = field(default_factory=bytearray)
    tail: deque[bytes] = field(default_factory=deque)
    tail_bytes: int = 0
    dropped: int = 0
    total: int = 0

    @property
    def running(self) -> bool:
        return self.exit_status is None

    @property
    def duration_ms(self) -> int:
        end = self.ended or time.monotonic()
        return int((end - self.started) * 1000)

    def absorb(self, chunk: bytes) -> None:
        """Keep the front, keep the back, count what fell out of the middle."""
        self.total += len(chunk)
        if len(self.head) < CAPTURE_HEAD_BYTES:
            take = CAPTURE_HEAD_BYTES - len(self.head)
            self.head += chunk[:take]
            chunk = chunk[take:]
            if not chunk:
                return
        self.tail.append(chunk)
        self.tail_bytes += len(chunk)
        while self.tail_bytes > CAPTURE_TAIL_BYTES and len(self.tail) > 1:
            gone = self.tail.popleft()
            self.tail_bytes -= len(gone)
            self.dropped += len(gone)

    def output(self) -> str:
        """The kept output as text, with the gap marked if there is one."""
        head = flatten(clean_output(bytes(self.head), limit=CAPTURE_HEAD_BYTES * 2)[0])
        if not self.dropped and not self.tail:
            return head
        tail = flatten(clean_output(b"".join(self.tail), limit=CAPTURE_TAIL_BYTES * 2)[0])
        if not self.dropped:
            return f"{head}\n{tail}" if tail else head
        gap = f"\n\n… {self.dropped / 1024:,.0f} KB dropped …\n\n"
        return f"{head}{gap}{tail}"

    def as_text(self, *, label: str) -> str:
        """The block that goes into a message, attribution and all.

        The sentence sits **outside** the fence and is written here, so nothing
        the far side printed can forge it, and the `$ ` line is synthesised
        rather than lifted from the shell -- what the shell echoed carries
        readline's editing escapes and is not the command.
        """
        where = f", in {self.cwd}" if self.cwd else ""
        if self.running:
            how = "still running"
        elif self.exit_status:
            how = f"exit {self.exit_status}"
        else:
            how = "succeeded"

        seconds = self.duration_ms / 1000
        took = f" after {seconds:.0f}s" if seconds >= 1 else ""
        body = f"$ {self.command}\n{self.output()}".rstrip()
        return (
            f"Ran in the terminal on {label}{where} — {how}{took}:\n\n{fenced(body)}"
        )

    def summary(self) -> str:
        """A short label for a chip, never rendered as markup."""
        command = self.command or "(no command)"
        if len(command) > 60:
            command = command[:57] + "…"
        if self.running:
            return f"{command} · running"
        return f"{command} · exit {self.exit_status}"


def trim_command(raw: str) -> str:
    text, _ = clean_output(raw, limit=CAPTURE_COMMAND_BYTES)
    return _CONTROLS.sub("", text).strip()


__all__ = [
    "CAPTURE_COMMAND_BYTES",
    "CAPTURE_HEAD_BYTES",
    "CAPTURE_TAIL_BYTES",
    "Capture",
    "fenced",
    "flatten",
    "trim_command",
]
