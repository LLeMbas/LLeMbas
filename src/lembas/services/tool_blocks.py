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
- **plan** (the spec's word) -- not drawn at all: the plan card above the
  composer refreshes instead, so it is `hidden` here.
- anything else -- the generic block it always was.

The kind comes from the harness spec where the tool is in it (`block` in
`harness_spec/tools/<name>.json`, the one definition the LLeMbas CLI's tools follow
too), and from the table below for this instance's own tools and for names
transcripts already carry. Pure: no database, called once per rendered event.
"""

from __future__ import annotations

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
    "file_read": READ,
    "file_write": EDIT,
    "file_edit": EDIT,
    "file_list": SEARCH,
    "fetch": WEB,
    "notes_get": READ,
    "notes_search": SEARCH,
    "skill_get": READ,
    "subagent_run": TASK,
}

# How many changed lines an edit shows before "the whole change".
PREVIEW_LINES = 12


@cache
def _spec_kinds() -> dict[str, str]:
    """`block` of every tool in the harness spec, by name and alias -- the
    vendored copy, with the 3.0.0 draft's tools while it is read
    (`harness_spec.tools`)."""
    from lembas.services import harness_spec

    kinds: dict[str, str] = {}
    try:
        specs = harness_spec.tools()
    except (FileNotFoundError, OSError, ValueError):
        return kinds
    for name, data in specs.items():
        block = data.get("block")
        if block == "plan":
            block = HIDDEN
        if block not in KINDS:
            continue
        kinds[str(data.get("name") or name)] = block
        for names in (data.get("aliases") or {}).values():
            for alias in names if isinstance(names, list) else [names]:
                if isinstance(alias, str) and alias:
                    kinds.setdefault(alias, block)
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
    # Spec 3.7.0 (display.md §2): `running`, `done`, `failed` or `denied`,
    # and how long a finished call took, as it is written ("" where none is
    # shown or none was kept).
    state: str = "done"
    duration: str = ""


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
    """The lines a diff adds and removes, counted by its hunks (spec 3.7.0,
    `display.change`). It went by line prefixes, and knew a file header only
    as `--- a/` and `+++ b/`: a header written without them -- as the LLeMbas
    CLI writes its own patches -- was one line removed and one added."""
    from lembas.services import display

    return display.change(diff)


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


def _title(event: dict, *fallbacks: Any) -> str:
    """A call's one short title (spec 3.7.0, display.md §1): from its
    arguments by the spec's rule -- the same string the progress line and the
    link carry. A stored call that kept no arguments (an older row, a device's
    before it sent them) falls back to what its event did keep."""
    from lembas.services import display

    return display.title(_args(event)) or _first(*fallbacks)


def block_for(event: dict) -> Block:
    """How one tool event is shown: its kind, its title, the facts after the
    title, and -- from spec 3.7.0 -- the state it is in and how long it took
    (`services/display.py`)."""
    from lembas.services import display

    name = str(event.get("name") or "")
    kind = kind_of(name)
    args = _args(event)
    query = str(event.get("query") or "")
    state = display.state(event)
    took = display.duration(event.get("ms"), state)
    diff = str(event.get("diff") or "")
    exit_code = event.get("exit")
    hits = event.get("hits")
    if kind == SEARCH and not isinstance(hits, int) and event.get("results"):
        hits = len(event["results"])
    facts = tuple(
        display.facts(kind, diff=diff, exit_code=exit_code, hits=hits)
        if state != display.RUNNING else ()
    )
    common = {"state": state, "duration": took}
    if kind == SHELL:
        command = _title(event, args.get("command"), event.get("command"), query)
        return Block(SHELL, title=command, chips=facts, **common)
    if kind == EDIT:
        path = _title(
            event, args.get("path"), args.get("file_path"), event.get("path"),
            query.split(" ")[0],
        )
        added, removed = diff_counts(diff) if diff else (0, 0)
        lines, more = preview(diff) if diff else ((), False)
        return Block(
            EDIT, title=path, chips=facts, preview=lines, more=more, added=added,
            removed=removed, **common,
        )
    if kind == READ:
        # What was read, as its result names it (the path with its lines)
        # where it does: more than the arguments say.
        return Block(
            READ,
            title=_first(event.get("path")) or _title(event, query, name),
            **common,
        )
    if kind == SEARCH:
        return Block(SEARCH, title=_title(event, query), chips=facts, **common)
    if kind == WEB:
        return Block(WEB, title=_title(event, query), **common)
    if kind == TASK:
        return Block(
            TASK, title=_title(event, args.get("prompt"), query), **common
        )
    return Block(kind, title=_title(event, query), **common)


__all__ = ["KINDS", "Block", "block_for", "diff_counts", "kind_of", "preview"]
