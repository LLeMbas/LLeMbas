"""What a tool call looks like in the transcript.

Every tool call used to render as the same block: a label, a one-line query,
and the output behind a click -- with `device-1:/home/dev/project` printed above
a command nobody could see. The CLI shows each kind of call the way that kind
is read, and so does this now:

- **shell** -- the command itself is the block's title; its output is behind
  the click; the exit code is a chip. No host or directory line: the chat says
  where it works, and a `cd` is in the command.
- **edit** -- a write, an edit or a patch shows `+N −M` and the change itself,
  open by default and limited to the hunks that changed most; the whole change
  is one click further.
- **read** -- just the file (and the lines) that were read.
- **search** -- the pattern and how many hits; the hits behind the click.
- **web** -- the query or the address; the results behind the click.
- **task** -- the job handed to a helper, and what it came back with.
- anything else -- the generic block it always was.

The kind comes from the harness spec where the tool is in it (`block` in
`harness_spec/tools/<name>.json`, the one definition the LLeMbas CLI's tools follow
too), and from the table below for this instance's own tools and for names
transcripts already carry. Pure: no database, called once per rendered event.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from typing import Any

SHELL = "shell"
EDIT = "edit"
READ = "read"
SEARCH = "search"
WEB = "web"
TASK = "task"
HIDDEN = "hidden"
TOOL = "tool"

KINDS = (SHELL, EDIT, READ, SEARCH, WEB, TASK, HIDDEN, TOOL)

# Names that are not in the harness spec -- this instance's own tools, and the
# names stored transcripts carry from before the spec's.
_LOCAL: dict[str, str] = {
    "shell_run": SHELL,
    "job_output": SHELL,
    "file_read": READ,
    "file_write": EDIT,
    "file_edit": EDIT,
    "file_list": SEARCH,
    "fetch": WEB,
    "notes_get": READ,
    "notes_search": SEARCH,
    "report_get": READ,
    "report_search": SEARCH,
    "skill_get": READ,
    "subagent_run": TASK,
}

# How many changed lines an edit shows before "the whole change".
PREVIEW_LINES = 12


@cache
def _spec_kinds() -> dict[str, str]:
    """`block` of every tool in the vendored harness spec, by name and alias."""
    from importlib import resources

    kinds: dict[str, str] = {}
    try:
        folder = resources.files("lembas.harness_spec") / "tools"
        for entry in folder.iterdir():
            if not entry.name.endswith(".json"):
                continue
            data = json.loads(entry.read_text(encoding="utf-8"))
            block = data.get("block")
            if block not in KINDS:
                continue
            kinds[str(data.get("name") or entry.name[:-5])] = block
            for names in (data.get("aliases") or {}).values():
                for alias in names if isinstance(names, list) else [names]:
                    if isinstance(alias, str) and alias:
                        kinds.setdefault(alias, block)
    except (FileNotFoundError, OSError, ValueError):
        pass
    return kinds


def kind_of(name: str) -> str:
    return _spec_kinds().get(name) or _LOCAL.get(name) or TOOL


@dataclass(frozen=True)
class Block:
    kind: str
    # What the collapsed block says: the command, the file, the pattern.
    title: str = ""
    # Small facts beside it: `exit 1`, `+12 −3`, `14 hits`.
    chips: tuple[str, ...] = ()
    # An edit's most-changed lines, as (class, text): shown open.
    preview: tuple[tuple[str, str], ...] = ()
    # Whether there is more of the change than the preview shows.
    more: bool = False
    added: int = 0
    removed: int = 0
    extra: dict = field(default_factory=dict)


def _args(event: dict) -> dict:
    args = event.get("args")
    return args if isinstance(args, dict) else {}


def _first(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _line_class(line: str) -> str:
    if line.startswith(("@@", "--- a/", "+++ b/", "diff ", "--- /dev/null", "+++ /dev/null")):
        return "meta"
    if line.startswith("+"):
        return "add"
    if line.startswith("-"):
        return "del"
    return "ctx"


def diff_counts(diff: str) -> tuple[int, int]:
    added = removed = 0
    for line in diff.splitlines():
        cls = _line_class(line)
        added += cls == "add"
        removed += cls == "del"
    return added, removed


def _hunks(diff: str) -> list[list[str]]:
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in diff.splitlines():
        if line.startswith("@@"):
            current = [line]
            hunks.append(current)
        elif current is not None and _line_class(line) != "meta":
            current.append(line)
    return hunks


def preview(diff: str, budget: int = PREVIEW_LINES) -> tuple[tuple[tuple[str, str], ...], bool]:
    """The hunks that changed most, in file order, up to `budget` changed lines;
    and whether anything was left out."""
    hunks = _hunks(diff)
    if not hunks:
        return (), False
    scored = sorted(
        range(len(hunks)),
        key=lambda i: -sum(1 for line in hunks[i] if _line_class(line) in ("add", "del")),
    )
    chosen: set[int] = set()
    used = 0
    for i in scored:
        changed = sum(1 for line in hunks[i] if _line_class(line) in ("add", "del"))
        if chosen and used + changed > budget:
            continue
        chosen.add(i)
        used += changed
        if used >= budget:
            break
    lines: list[tuple[str, str]] = []
    shown = 0
    cut = False
    for i in sorted(chosen):
        for line in hunks[i]:
            cls = _line_class(line)
            if cls in ("add", "del"):
                if shown >= budget:
                    cut = True
                    continue
                shown += 1
            lines.append((cls, line))
    while lines and lines[-1][0] == "ctx" and not lines[-1][1].strip():
        lines.pop()
    more = cut or len(chosen) < len(hunks)
    return tuple(lines), more


def block_for(event: dict) -> Block:
    """How one tool event is shown."""
    name = str(event.get("name") or "")
    kind = kind_of(name)
    args = _args(event)
    query = str(event.get("query") or "")
    if kind == SHELL:
        command = _first(args.get("command"), event.get("command"), query)
        chips = []
        exit_code = event.get("exit")
        if isinstance(exit_code, int):
            chips.append(f"exit {exit_code}")
        return Block(SHELL, title=command, chips=tuple(chips))
    if kind == EDIT:
        diff = str(event.get("diff") or "")
        path = _first(
            args.get("path"), args.get("file_path"), event.get("path"), query.split(" ")[0]
        )
        added, removed = diff_counts(diff) if diff else (0, 0)
        lines, more = preview(diff) if diff else ((), False)
        chips = (f"+{added}", f"−{removed}") if diff else ()
        return Block(
            EDIT, title=path, chips=chips, preview=lines, more=more, added=added, removed=removed
        )
    if kind == READ:
        return Block(
            READ,
            title=_first(
                event.get("path"),
                query,
                args.get("path"),
                args.get("file_path"),
                args.get("id"),
                name,
            ),
        )
    if kind == SEARCH:
        title = _first(args.get("pattern"), args.get("query"), args.get("path"), query)
        hits = event.get("hits")
        if not isinstance(hits, int) and event.get("results"):
            hits = len(event["results"])
        chips = (f"{hits} hits",) if isinstance(hits, int) else ()
        return Block(SEARCH, title=title, chips=chips)
    if kind == WEB:
        return Block(WEB, title=_first(args.get("query"), args.get("url"), query))
    if kind == TASK:
        return Block(TASK, title=_first(args.get("description"), args.get("prompt"), query))
    return Block(kind, title=query)


__all__ = ["KINDS", "Block", "block_for", "diff_counts", "kind_of", "preview"]
