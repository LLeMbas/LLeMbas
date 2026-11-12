"""One display in every client (harness spec 3.7.0, `display.md`).

What a tool call is called, which of four states it is in, how long it took,
the facts shown after its title, and the line that stays on a reply that did
not finish -- the same in the web UI, Desktop, the CLI and Android, because
each of them asks the same rule. The data is the spec's (`loop.json`
`display`); the cases that hold both implementations to it are
`conformance/display.json`, run by `tests/test_harness_spec.py`.

Everything here is pure: arguments, a diff, a number in; a string out. The
page reaches it through `services/tool_blocks.py` (how a call is drawn) and
`services/progress.py` (`what_of`); `/api/v2` through `serialise.tool_call`.
"""

from __future__ import annotations

import re
from typing import Any

from lembas.services import harness_spec

RUNNING = "running"
DONE = "done"
FAILED = "failed"
DENIED = "denied"


def spec() -> dict[str, Any]:
    return harness_spec.loop()["display"]


def _what_max() -> int:
    return int(harness_spec.loop()["progress"]["what_max"])


# --- §1. A call's one short title ----------------------------------------------------
def _patch_files(patch: str, shape: dict[str, Any]) -> list[str]:
    """The files a patch names: its `*** Add/Update/Delete File:` lines, else
    its `+++ ` lines without a leading `b/` (`/dev/null` left out)."""
    named: list[str] = []
    for line in patch.splitlines():
        for marker in shape["patch_files"]:
            if line.startswith(marker):
                path = line[len(marker) :].strip()
                if path and path not in named:
                    named.append(path)
    if named:
        return named
    for line in patch.splitlines():
        if not line.startswith("+++ "):
            continue
        path = line[4:].split("\t")[0].strip()
        if path.startswith("b/"):
            path = path[2:]
        if path and path != "/dev/null" and path not in named:
            named.append(path)
    return named


def title(arguments: Any) -> str:
    """A call's short title, from its arguments alone (§1), or "".

    The same string in the transcript, as `what` of `running_tool` and as the
    `title` the link carries: `questions` and `patch` first, then the first
    string with anything in it among the spec's keys -- what a call *does*
    before where it does it -- then its first line, cut to `what_max`."""
    if not isinstance(arguments, dict):
        return ""
    shape = spec()["title"]
    found = ""
    questions = arguments.get("questions")
    patch = arguments.get("patch")
    if isinstance(questions, list):
        headers = [
            str(q.get("header") or "").strip()
            for q in questions
            if isinstance(q, dict) and str(q.get("header") or "").strip()
        ]
        found = shape["questions_join"].join(headers)
    if not found and isinstance(patch, str):
        found = shape["patch_join"].join(_patch_files(patch, shape))
    if not found:
        for key in shape["keys"]:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                found = value
                break
    first = found.strip().split("\n")[0].strip()
    limit = _what_max()
    return first if len(first) <= limit else first[: limit - 1] + "…"


# --- §2. States, the duration, the facts ------------------------------------------------
def state(event: dict[str, Any]) -> str:
    """Which of the four states a stored or live call is in.

    A tool event's `status` is this server's own word: `running` while the
    call is out, `ok` when its result came, `denied`/`refused` when it was not
    run, anything else an error."""
    status = str(event.get("status") or "")
    if event.get("denied") or status in ("denied", "refused", "declined"):
        return DENIED
    if status in ("running", "pending"):
        return RUNNING
    if status in ("ok", "done", ""):
        return DONE
    return FAILED


def duration(milliseconds: Any, call_state: str = DONE) -> str:
    """A finished call's time, the one way it is written (§2): tenths under a
    minute (`0.4s`, `8.4s`), then `1m 05s`, then `2h 03m`. "" for a state that
    shows none (running, denied) and where no time was kept."""
    if call_state in spec()["no_duration"]:
        return ""
    if not isinstance(milliseconds, int | float) or isinstance(milliseconds, bool):
        return ""
    if milliseconds < 0:
        return ""
    tenths = int(milliseconds // 100 + (1 if (milliseconds % 100) >= 50 else 0))
    if tenths < int(spec()["duration"]["tenths_under_seconds"]) * 10:
        return f"{tenths // 10}.{tenths % 10}s"
    total = int(round(milliseconds / 1000))
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def change(diff: str) -> tuple[int, int]:
    """The lines a unified diff adds and removes, counted by its hunks (§2).

    After each `@@ -a,b +c,d @@` header the next lines that begin with `+`,
    `-` or a space are read until `b` old and `d` new lines are used up; a `+`
    there is one added, a `-` one removed. Nothing outside a hunk counts, so a
    file header never does -- whatever it looks like: `--- a.txt` with no
    `a/` was counted as a removed line when this went by line prefixes -- and
    a removed line that itself begins with `-- ` always does."""
    added = removed = 0
    old_left = new_left = 0
    for line in diff.splitlines():
        header = _HUNK.match(line)
        if header:
            old_left = int(header.group(1)) if header.group(1) is not None else 1
            new_left = int(header.group(2)) if header.group(2) is not None else 1
            continue
        if old_left <= 0 and new_left <= 0:
            continue
        if line.startswith("\\"):
            continue  # "\ No newline at end of file"
        if line.startswith("+"):
            added += 1
            new_left -= 1
        elif line.startswith("-"):
            removed += 1
            old_left -= 1
        elif line.startswith(" ") or line == "":
            old_left -= 1
            new_left -= 1
        else:
            # Not a line of this hunk: it ended short of its counts.
            old_left = new_left = 0
    return added, removed


def _fill(text: str, values: dict[str, Any]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def change_text(added: int, removed: int) -> str:
    return _fill(spec()["change"], {"added": added, "removed": removed})


def facts(block: str, *, diff: str = "", exit_code: Any = None, hits: Any = None) -> list[str]:
    """What a finished call shows after its title, in order (§2): an edit's
    `+N −M`, a command's `exit N` when it is not 0, a search's hits."""
    shape = spec()
    out: list[str] = []
    if block == "edit" and diff:
        out.append(change_text(*change(diff)))
    known = isinstance(exit_code, int) and not isinstance(exit_code, bool)
    if block == "shell" and known and exit_code != 0:
        out.append(_fill(shape["exit"], {"code": exit_code}))
    if block == "search" and isinstance(hits, int) and not isinstance(hits, bool):
        out.append(_fill(shape["hit"] if hits == 1 else shape["hits"], {"count": hits}))
    return out


# --- §3. What is drawn -----------------------------------------------------------------
def drawn(block: str | None) -> bool:
    """Whether a tool of this block kind is drawn as a call at all."""
    return (block or "tool") not in spec()["not_drawn"]


# --- §4. How a reply that did not finish ends ------------------------------------------
def end_line(progress: dict[str, Any] | None, shape: dict[str, Any] | None = None) -> str:
    """The line that stays on a finished reply: `Stopped: by you`, `Failed:
    …` -- the phase's text alone, no elapsed time -- or "" for a turn that
    finished normally."""
    from lembas.services import progress as progress_service

    if not isinstance(progress, dict) or progress.get("phase") not in spec()["end"]:
        return ""
    return progress_service.text(progress, int(progress.get("since") or 0), shape)


def stored_end(*, stopped: bool, error: str, reason: str = "") -> dict[str, Any] | None:
    """A stored reply's end as the progress it ended with (`stopped` with its
    reason where one was kept, `failed` with the error's first line), or None
    for one that finished. A reply that wrapped up at a limit is not marked
    stopped, and is known by its reason alone."""
    if error:
        first = str(error).strip().split("\n")[0][:300]
        return {"phase": "failed", "reason": first}
    if stopped or reason:
        return {"phase": "stopped", **({"reason": reason} if reason else {})}
    return None


def message_end(message: Any, shape: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """How a stored reply ended, for whoever draws it again: `{phase, reason,
    text}` -- `text` the line that stays on it, in `shape`'s language (the
    spec's English without) -- or None for a reply that finished or is still
    being written."""
    if not getattr(message, "complete", True) or getattr(message, "role", "") != "assistant":
        return None
    ended = stored_end(
        stopped=bool(getattr(message, "stopped", False)),
        error=str(getattr(message, "error", "") or ""),
        reason=str(getattr(message, "end_reason", "") or ""),
    )
    if ended is None:
        return None
    return {
        "phase": ended["phase"],
        "reason": ended.get("reason", ""),
        "text": end_line({**ended, "since": 0}, shape),
    }


__all__ = [
    "DENIED",
    "DONE",
    "FAILED",
    "RUNNING",
    "change",
    "change_text",
    "drawn",
    "duration",
    "end_line",
    "facts",
    "message_end",
    "spec",
    "state",
    "stored_end",
    "title",
]
