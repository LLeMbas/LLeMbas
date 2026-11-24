"""The store on disk: a git repository in the data folder.

    <data>/skills-store/
        skills/<name>/SKILL.md     one folder per skill (other files in it are kept)
        skills/main/SKILL.md       the main skill: its prose
        notes/<name>.md            one note a file (folders below notes/ are read too)

Every change is one commit, made here and nowhere else, carrying who made it
as trailers (`Account`, `Harness`, `Model`, `Session`, `Origin`, `Reason`).
The files are the truth; the database index beside them is rebuilt from them
(`catalog.py`).

**The remote**, when one is named: fetch, then one of four things and never a
fifth -- nothing (the same), push (ours is ahead), fast-forward (theirs is
ahead, after `check_tree` passed what arrived), or **stop** (the two have
diverged; said on the page, never merged). A remote that shares no history is
*adopted* once, when it is connected: the store moves onto the remote's
history, and what this instance had that the remote lacks is committed on top.
"""

from __future__ import annotations

import fcntl
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from lembas.config import get_settings
from lembas.services.skillstore import gitrun

DIR_NAME = "skills-store"
SKILLS = "skills"
NOTES = "notes"
SKILL_FILE = "SKILL.md"
MAIN = "main"
BRANCH = "main"
REMOTE_REF = "refs/remotes/origin/sync"

TRAILERS = ("Account", "Account-Id", "Harness", "Model", "Session", "Origin", "Reason",
            "Approved-By")

_LOCK = threading.RLock()
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,119}$")

MAIN_SKILL = """---
name: main
description: Read before working with skills or notes here - how they are found, read and changed.
---

# Skills and notes here

This instance keeps one shared collection of skills and a store of notes.

## Finding and reading

- The list below names the skills, or the categories they are in. A line says
  when a skill is meant to be used. Before starting a task, check whether one
  fits.
- Read a skill in parts: its outline first, then only the section you need.
- Before working on something, look up the notes on it by subject. A note may
  be old: its header says when it was last confirmed.

## Changing

- Improve a skill when you find it wrong or incomplete. Change one section at
  a time and say why. Whether your change applies at once or waits for
  approval is this instance's policy; ask what you may do before you rely on it.
- Skills are general and every agent here reads them. Never put into a skill
  anything private, secret, or belonging to one person's data: that goes in a
  note, or nowhere.
- Write a note for what you found out that is not a procedure: a plan, an
  incident, a finding. Give it subjects somebody would look it up by. Add to a
  note freely; confirm one you checked and found still true.
"""


class StoreError(Exception):
    """Something the store refuses or could not do, in words for a page."""


def root() -> Path:
    return get_settings().data_dir / DIR_NAME


_DEPTH = 0


@contextmanager
def locked() -> Iterator[None]:
    """One writer: this process's threads, and any other worker's. Re-entrant
    in a thread (the file lock is taken once, by the outermost)."""
    global _DEPTH
    with _LOCK:
        if _DEPTH:
            _DEPTH += 1
            try:
                yield
            finally:
                _DEPTH -= 1
            return
        path = root()
        path.mkdir(parents=True, exist_ok=True)
        handle = open(path.parent / (DIR_NAME + ".lock"), "w")  # noqa: SIM115
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            _DEPTH = 1
            yield
        finally:
            _DEPTH = 0
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()


def _git(args: list[str], **kwargs) -> tuple[int, str]:
    return gitrun.run(args, cwd=root(), **kwargs)


def _must(args: list[str], **kwargs) -> str:
    code, out = _git(args, **kwargs)
    if code != 0:
        raise StoreError(f"git {args[0]}: {out[:300]}")
    return out


# --- Paths ----------------------------------------------------------------------
def check_rel(rel: str) -> str:
    """A path inside the store as the store takes one, or `StoreError`."""
    rel = (rel or "").replace("\\", "/").strip("/")
    parts = rel.split("/")
    if not rel or parts[0] not in (SKILLS, NOTES) or len(parts) < 2 or len(parts) > 6:
        raise StoreError("That is not a path in the store.")
    for part in parts:
        if part in (".", "..") or part.startswith(".") or not _SEGMENT.match(part):
            raise StoreError("That is not a path in the store.")
    return rel


def _inside(rel: str) -> Path:
    """The file for a checked path, refusing anything a symlink would move."""
    base = root()
    current = base
    for part in check_rel(rel).split("/"):
        current = current / part
        if current.is_symlink():
            raise StoreError("That path is a link, which the store does not follow.")
    if not str(current.resolve()).startswith(str(base.resolve()) + os.sep):
        raise StoreError("That is not a path in the store.")
    return current


def skill_rel(name: str) -> str:
    return f"{SKILLS}/{name}/{SKILL_FILE}"


def note_rel(ident: str) -> str:
    return f"{NOTES}/{ident}.md"


def read(rel: str) -> str | None:
    try:
        path = _inside(rel)
    except StoreError:
        return None
    if not path.is_file() or path.stat().st_size > gitrun.MAX_FILE_BYTES:
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def files() -> list[str]:
    """Every skill's SKILL.md and every note, as paths in the store."""
    base = root()
    out: list[str] = []
    skills = base / SKILLS
    if skills.is_dir() and not skills.is_symlink():
        for entry in sorted(skills.iterdir()):
            rel = f"{SKILLS}/{entry.name}/{SKILL_FILE}"
            if entry.is_dir() and not entry.is_symlink() and read(rel) is not None:
                out.append(rel)
    notes = base / NOTES
    if notes.is_dir() and not notes.is_symlink():
        for dirpath, dirnames, filenames in os.walk(notes, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".") and not os.path.islink(os.path.join(dirpath, d))
            )
            for name in sorted(filenames):
                if not name.endswith(".md"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), base).replace(os.sep, "/")
                if read(rel) is not None:
                    out.append(rel)
    return out


# --- The repository ---------------------------------------------------------------
def exists() -> bool:
    return (root() / ".git").is_dir()


def ensure() -> bool:
    """The repository and its layout, made the first time anything asks.
    True when it was made now."""
    if exists():
        return False
    with locked():
        if exists():
            return False
        base = root()
        base.mkdir(parents=True, exist_ok=True)
        _must(["init", "--quiet", "--initial-branch", BRANCH])
        (base / SKILLS / MAIN).mkdir(parents=True, exist_ok=True)
        (base / NOTES).mkdir(exist_ok=True)
        (base / SKILLS / MAIN / SKILL_FILE).write_text(MAIN_SKILL, encoding="utf-8")
        (base / NOTES / ".gitkeep").write_text("", encoding="utf-8")
        _must(["add", "--", f"{SKILLS}/{MAIN}/{SKILL_FILE}", f"{NOTES}/.gitkeep"])
        _commit_staged("The store's layout", "LLeMbas", {"Origin": "setup"})
        return True


def head() -> str:
    code, out = _git(["rev-parse", "--verify", "--quiet", "HEAD"])
    return out.strip() if code == 0 else ""


def _clean(value: str, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _commit_staged(subject: str, author: str, trailers: dict[str, str]) -> str:
    message = _clean(subject, 120) or "Change"
    lines = [f"{key}: {_clean(trailers[key])}" for key in TRAILERS if trailers.get(key)]
    if lines:
        message += "\n\n" + "\n".join(lines)
    name = re.sub(r"[<>\n\r]", "", _clean(author, 80)) or "LLeMbas"
    env = {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": "store@lembas.invalid",
        "GIT_COMMITTER_NAME": "LLeMbas",
        "GIT_COMMITTER_EMAIL": "store@lembas.invalid",
    }
    _must(
        ["commit", "--quiet", "--no-verify", "--allow-empty", "--cleanup=verbatim", "-F", "-"],
        env=env,
        stdin=message + "\n",
    )
    return head()


def commit(
    changes: dict[str, str | None],
    *,
    subject: str,
    author: str,
    trailers: dict[str, str] | None = None,
) -> str:
    """Write these files (None: remove) as one commit; the new head.
    A change that changes nothing makes no commit and answers ''."""
    ensure()
    with locked():
        staged: list[str] = []
        for rel, text in changes.items():
            path = _inside(rel)
            if text is None:
                if path.is_file():
                    path.unlink()
                    staged.append(rel)
                continue
            if len(text.encode("utf-8")) > gitrun.MAX_FILE_BYTES:
                raise StoreError("That is more than one file in the store may hold.")
            if path.is_file() and path.read_text(encoding="utf-8", errors="replace") == text:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            staged.append(rel)
        if not staged:
            return ""
        _must(["add", "-A", "--", *staged])
        return _commit_staged(subject, author, trailers or {})


@dataclass(frozen=True)
class Commit:
    sha: str
    at: str
    author: str
    subject: str
    trailers: dict[str, str] = field(default_factory=dict)

    @property
    def origin(self) -> str:
        return self.trailers.get("Origin", "remote")


_SEP = "\x1e"
_FIELD = "\x1f"


def _parse_log(out: str) -> list[Commit]:
    commits: list[Commit] = []
    for chunk in out.split(_SEP):
        # (An empty body's separator is whitespace to `strip`, and goes with it.)
        parts = (chunk.strip("\n").split(_FIELD) + [""])[:5]
        if len(parts) < 5 or not parts[0] or not parts[1]:
            continue
        trailers: dict[str, str] = {}
        for line in parts[4].split("\n"):
            key, sep, value = line.partition(":")
            if sep and key in TRAILERS:
                trailers[key] = value.strip()
        commits.append(Commit(parts[0], parts[1], parts[2], parts[3], trailers))
    return commits


_FORMAT = f"--format={_SEP}%H{_FIELD}%aI{_FIELD}%an{_FIELD}%s{_FIELD}%b"


def history(rel: str | None = None, *, limit: int = 30, rev: str = "HEAD") -> list[Commit]:
    """The commits that touched a path (or all), newest first."""
    if not exists() or not head():
        return []
    args = ["log", f"--max-count={max(1, min(limit, 500))}", _FORMAT, rev]
    if rel is not None:
        args += ["--", check_rel(rel)]
    code, out = _git(args)
    return _parse_log(out) if code == 0 else []


def at_commit(sha: str, rel: str) -> str | None:
    """A file as a commit left it (None: not there then)."""
    if not re.fullmatch(r"[0-9a-f]{7,64}", sha or ""):
        return None
    code, out = _git(["show", f"{sha}:{check_rel(rel)}"])
    return out + "\n" if code == 0 else None


def changed_paths(old: str, new: str) -> list[str]:
    if not old:
        return files()
    code, out = _git(["diff", "--name-only", "--no-renames", old, new])
    return [line for line in out.split("\n") if line] if code == 0 else files()


# --- The remote -----------------------------------------------------------------
SYNC_OFF = "off"
SYNC_OK = "ok"
SYNC_DIVERGED = "diverged"
SYNC_REFUSED = "refused"
SYNC_ERROR = "error"


@dataclass
class Synced:
    state: str
    message: str = ""
    # (old head, new head) when the remote's commits were taken.
    pulled: tuple[str, str] | None = None
    pushed: bool = False
    # Files this instance had under another content when a remote was adopted.
    conflicts: dict[str, str] = field(default_factory=dict)
    adopted: bool = False


def check_tree(rev: str) -> None:
    """Whether what a remote sent may become the store's files, or
    `StoreError` naming what may not: a link, a repository inside the
    repository, a path that leaves the store, too much."""
    code, out = _git(["ls-tree", "-r", "-l", "-z", "--full-tree", rev])
    if code != 0:
        raise StoreError("What the remote sent could not be read.")
    entries = [entry for entry in out.split("\0") if entry]
    if len(entries) > gitrun.MAX_FILES:
        raise StoreError("The remote holds more files than this store takes.")
    for entry in entries:
        meta, _tab, path = entry.partition("\t")
        mode, kind, _sha, size = (meta.split() + ["", "", "", ""])[:4]
        shown = path[:120]
        if mode == "120000":
            raise StoreError(f"The remote holds a link ({shown}); links are not taken.")
        if mode == "160000" or kind == "commit":
            raise StoreError(f"The remote holds a repository inside it ({shown}); not taken.")
        parts = path.split("/")
        if (
            path.startswith("/")
            or "\\" in path
            or any(p in ("", ".", "..") or p.lower() == ".git" for p in parts)
            or any(ord(c) < 32 for c in path)
        ):
            raise StoreError(f"The remote holds a path that is not taken ({shown!r}).")
        if parts[0] in (SKILLS, NOTES) and size.isdigit() and int(size) > gitrun.MAX_FILE_BYTES:
            raise StoreError(f"A file in the remote is larger than this store takes ({shown}).")


def _is_ancestor(old: str, new: str) -> bool:
    return _git(["merge-base", "--is-ancestor", old, new])[0] == 0


def _snapshot() -> dict[str, str]:
    return {rel: read(rel) or "" for rel in files()}


def sync(
    address: str,
    credentials: gitrun.Credentials | None = None,
    *,
    branch: str = BRANCH,
    adopt: bool = False,
) -> Synced:
    """Bring the store and its remote together, by the four outcomes above.
    `adopt`: a remote that shares no history may be taken on (asked for when a
    remote is connected, never on an ordinary sync)."""
    ensure()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", branch or "") or ".." in branch:
        return Synced(SYNC_ERROR, "That is not a branch name.")
    try:
        remote = gitrun.Remote(address, credentials)
    except gitrun.BadAddress as exc:
        return Synced(SYNC_ERROR, str(exc))
    with locked(), remote:
        code, out = remote.run(
            ["ls-remote", "--heads", "--", remote.address, f"refs/heads/{branch}"], cwd=root()
        )
        if code != 0:
            return Synced(SYNC_ERROR, out or "The remote did not answer.")
        local = head()
        if not out.strip():
            # Nothing there yet: the store's history becomes the remote's.
            return _push(remote, branch)
        code, out = remote.run(
            [
                "fetch", "--quiet", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head",
                "--", remote.address, f"+refs/heads/{branch}:{REMOTE_REF}",
            ],
            cwd=root(),
            grow_limit=gitrun.MAX_FETCH_BYTES,
        )
        if code != 0:
            if code == 125:
                # What did arrive is not kept under a name, so nothing reads it.
                _git(["update-ref", "-d", REMOTE_REF])
            return Synced(SYNC_ERROR, out or "The remote could not be fetched.")
        theirs = _must(["rev-parse", "--verify", REMOTE_REF]).strip()
        if theirs == local:
            return Synced(SYNC_OK)
        if _is_ancestor(theirs, local):
            return _push(remote, branch)
        try:
            check_tree(theirs)
        except StoreError as exc:
            return Synced(SYNC_REFUSED, str(exc))
        if _is_ancestor(local, theirs):
            _must(["merge", "--quiet", "--ff-only", "--no-verify", theirs])
            return Synced(SYNC_OK, pulled=(local, theirs))
        related = _git(["merge-base", local, theirs])[0] == 0
        if related or not adopt:
            return Synced(
                SYNC_DIVERGED,
                "This store and its remote have both changed since they last agreed. "
                "Nothing is merged; syncing has stopped.",
            )
        # Adoption: onto the remote's history, then what only we had on top.
        ours = _snapshot()
        _must(["reset", "--quiet", "--hard", theirs])
        _must(["clean", "--quiet", "-fd", "--", SKILLS, NOTES])
        result = Synced(SYNC_OK, pulled=("", theirs), adopted=True)
        missing: dict[str, str] = {}
        for rel, text in ours.items():
            there = read(rel)
            if there is None:
                missing[rel] = text
            elif there != text:
                result.conflicts[rel] = text
        if missing:
            commit(
                missing,
                subject="What this instance held before the remote was connected",
                author="LLeMbas",
                trailers={"Origin": "import"},
            )
            pushed = _push(remote, branch)
            if pushed.state != SYNC_OK:
                result.state, result.message = pushed.state, pushed.message
            result.pushed = pushed.pushed
        return result


def _push(remote: gitrun.Remote, branch: str) -> Synced:
    code, out = remote.run(
        ["push", "--quiet", "--no-verify", "--", remote.address, f"HEAD:refs/heads/{branch}"],
        cwd=root(),
    )
    if code != 0:
        if "non-fast-forward" in out or "fetch first" in out or "rejected" in out:
            return Synced(SYNC_DIVERGED, "The remote changed while this store was pushing.")
        return Synced(SYNC_ERROR, out or "The push was refused.")
    _git(["update-ref", REMOTE_REF, "HEAD"])
    return Synced(SYNC_OK, pushed=True)


def take_remote(
    address: str, credentials: gitrun.Credentials | None = None, *, branch: str = BRANCH
) -> Synced:
    """Out of a divergence, by a person's word: this store's side is pushed to
    a branch of its own in the remote (nothing is lost), and the store moves
    onto the remote's history."""
    try:
        remote = gitrun.Remote(address, credentials)
    except gitrun.BadAddress as exc:
        return Synced(SYNC_ERROR, str(exc))
    with locked(), remote:
        local = head()
        code, out = _git(["rev-parse", "--verify", "--quiet", REMOTE_REF])
        if code != 0 or not local:
            return Synced(SYNC_ERROR, "There is nothing fetched from the remote yet.")
        theirs = out.strip()
        try:
            check_tree(theirs)
        except StoreError as exc:
            return Synced(SYNC_REFUSED, str(exc))
        kept = f"lembas/kept-{local[:10]}"
        code, out = remote.run(
            ["push", "--quiet", "--no-verify", "--", remote.address,
             f"{local}:refs/heads/{kept}"],
            cwd=root(),
        )
        if code != 0:
            return Synced(SYNC_ERROR, out or "This store's side could not be kept in the remote.")
        _must(["reset", "--quiet", "--hard", theirs])
        _must(["clean", "--quiet", "-fd", "--", SKILLS, NOTES])
        return Synced(SYNC_OK, f"This store's side is kept as the branch {kept}.",
                      pulled=("", theirs))


__all__ = [
    "BRANCH",
    "MAIN",
    "NOTES",
    "SKILLS",
    "SKILL_FILE",
    "SYNC_DIVERGED",
    "SYNC_ERROR",
    "SYNC_OFF",
    "SYNC_OK",
    "SYNC_REFUSED",
    "Commit",
    "StoreError",
    "Synced",
    "at_commit",
    "changed_paths",
    "check_rel",
    "check_tree",
    "commit",
    "ensure",
    "exists",
    "files",
    "head",
    "history",
    "locked",
    "note_rel",
    "read",
    "root",
    "skill_rel",
    "sync",
    "take_remote",
]
