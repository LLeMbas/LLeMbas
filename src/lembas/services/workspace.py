"""What a chat's model is told about the files it works in (harness spec 3.0.0).

Two things, both from `filesystem.md`:

- **The view** -- `prompts/blocks/workspace.md`: where relative paths start and
  every path the session can reach with its level, so the model knows the full
  paths it may use (a granted directory under one that is not is reached only
  by its full path, and nothing lists it).
- **Instruction files, stacked** -- from the chat's boundary down to its start
  directory, outermost first, one per directory (`AGENTS.md`, else
  `CLAUDE.md`), none the session cannot read. The conformance area
  `instructions` holds the rule; `chain` is it, with no tree behind it.

Both are read through the chat's `fs.View`, so they say exactly what the
session's tools would find, and no more.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable

from lembas.db.models import KIND_FILE, KIND_MOUNT, LEVEL_COMMIT, LEVEL_RO, LEVEL_STAGE
from lembas.security import access
from lembas.services import fs

INSTRUCTION_NAMES = ("AGENTS.md", "CLAUDE.md")
# One instruction file's share of the prompt, as in the CLI.
MAX_INSTRUCTION_CHARS = 40_000
# How many paths the view names before it says there are more.
MAX_VIEW_PATHS = 30

LEVEL_WORDS = {LEVEL_RO: "read-only", LEVEL_STAGE: "stage", LEVEL_COMMIT: "commit"}


# --- Instruction files ----------------------------------------------------------------
def chain(boundary: str, start: str, readable: Callable[[str], bool]) -> list[str]:
    """The stacked instruction files from `boundary` down to `start`, outermost
    first: each directory gives the first of AGENTS.md, CLAUDE.md that
    `readable` accepts, never both; one it does not is passed over and the walk
    goes on below. A start outside the boundary is read as the boundary. The
    CLI's `instructionChain`, paths only."""
    top = access.normalise(boundary)
    at = access.normalise(start or top)
    if not access.is_within(at, top):
        at = top
    dirs = list(reversed(access.chain(at)))
    dirs = [d for d in dirs if access.is_within(d, top)]
    out: list[str] = []
    for directory in dirs:
        for name in INSTRUCTION_NAMES:
            candidate = posixpath.join(directory, name)
            if readable(candidate):
                out.append(candidate)
                break
    return out


def _readable(v: fs.View) -> Callable[[str], bool]:
    def readable(path: str) -> bool:
        if not v.resolver.visible(path):
            return False
        node = fs.lookup(v.db, v.org_id, path)
        return node is not None and node.kind == KIND_FILE

    return readable


def instruction_paths(v: fs.View) -> list[str]:
    boundary = (v.chat.boundary if v.chat is not None else "/") or "/"
    return chain(boundary, v.cwd, _readable(v))


def instruction_files(v: fs.View) -> list[tuple[str, str]]:
    """(path, text) of the chain's files, as the session can read them."""
    out = []
    for path in instruction_paths(v):
        try:
            text = fs.read(v, path).decode("utf-8", errors="replace")
        except fs.FsError:
            continue
        out.append((path, text))
    return out


def instructions_block(v: fs.View) -> str:
    """The chain's files for the system prompt, each under its path relative to
    the boundary -- the CLI's shape, a long one cut with a note."""
    boundary = access.normalise((v.chat.boundary if v.chat is not None else "/") or "/")
    sections = []
    for path, raw in instruction_files(v):
        text = raw.strip()
        if not text:
            continue
        if len(text) > MAX_INSTRUCTION_CHARS:
            text = (
                text[:MAX_INSTRUCTION_CHARS]
                + f"\n[…truncated: kept {MAX_INSTRUCTION_CHARS} of {len(raw)} characters; "
                f"read {path} for the rest]"
            )
        named = posixpath.relpath(path, boundary) if boundary != "/" else path.lstrip("/")
        sections.append(f"Instructions from {named}:\n<instructions>\n{text}\n</instructions>")
    return "\n\n".join(sections)


# --- The view -------------------------------------------------------------------------
def reachable(v: fs.View) -> list[tuple[str, str]]:
    """Every place the session can reach, as (path, level), outermost first: the
    chat's grants, the shares that reach the person, their home, the boundary
    -- each at the level the resolver gives it there, a place inside another
    at the same level left out. Paths a session cannot see are never named."""
    r = v.resolver
    candidates: set[str] = {r.boundary, "/"}
    candidates.update(r.grants or {})
    candidates.update(getattr(r, "_shares", {}) or {})
    candidates.update(r.floor or {})
    home = fs.home_of(v.db, v.user)
    if home is not None:
        candidates.add(home.path)
        candidates.add(posixpath.join(home.path, "skills"))
    if fs.lookup(v.db, v.org_id, fs.SKILLS) is not None:
        candidates.add(fs.SKILLS)
    kept: list[tuple[str, str]] = []
    for path in sorted(candidates, key=lambda p: (p.count("/") if p != "/" else 0, p)):
        if not access.is_within(path, r.boundary):
            continue
        if path != "/" and fs.lookup(v.db, v.org_id, path) is None:
            continue
        level = r.level(path)
        if not r.visible(path):
            continue
        covered = next(
            (lvl for p, lvl in reversed(kept) if access.is_within(path, p)), None
        )
        if covered == level:
            continue
        kept.append((path, level))
    return kept


def view_sentence(v: fs.View) -> str:
    """`{{view}}`: every reachable path with its level, in one sentence."""
    places = reachable(v)
    if not places:
        return ""
    parts = []
    for path, level in places[:MAX_VIEW_PATHS]:
        node = fs.lookup(v.db, v.org_id, path) if path != "/" else None
        shown = path if path == "/" else f"{path}/"
        words = LEVEL_WORDS.get(level, level)
        if node is not None and node.kind == KIND_MOUNT:
            mount = node.mount_json or {}
            repo = mount.get("repo") or node.name
            branch = mount.get("branch") or ""
            on = f" on branch {branch}" if branch else ""
            parts.append(f"`{shown}` (the repository {repo}{on}, {words})")
        else:
            parts.append(f"`{shown}` ({words})")
    more = len(places) - MAX_VIEW_PATHS
    tail = f", and {more} more" if more > 0 else ""
    return ", ".join(parts) + tail


def has_mount(v: fs.View) -> bool:
    for path, _level in reachable(v):
        node = fs.lookup(v.db, v.org_id, path) if path != "/" else None
        if node is not None and node.kind == KIND_MOUNT:
            return True
    return False


REPOSITORIES = (
    "In a repository from a forge, your changes collect in this session's working copy until "
    "you commit them; if the branch moved on the forge in the meantime, the commit is refused "
    "and shows you the difference. Merging, tagging and releasing always ask the user first."
)
SHOW = (
    "When the user should see a file itself — a page, a chart, a document you made — open it "
    "with show rather than pasting it into your answer."
)
PYTHON = (
    "Quick calculations and work on files can be done in Python with the python tool, which "
    "runs in the user's browser; it cannot run when no browser page of this chat is open."
)


def variables(v: fs.View, offered: set[str]) -> dict[str, str]:
    """The workspace block's variables (prompts/index.json, `blocks/workspace.md`)."""
    return {
        "start": f"`{v.cwd}`",
        "view": view_sentence(v),
        "repositories": REPOSITORIES if has_mount(v) else "",
        "show": SHOW if "show" in offered else "",
        "python": PYTHON if "python" in offered else "",
    }


__all__ = [
    "INSTRUCTION_NAMES",
    "chain",
    "instruction_files",
    "instruction_paths",
    "instructions_block",
    "reachable",
    "variables",
    "view_sentence",
]
