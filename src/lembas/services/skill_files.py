"""Skills as files in the tree (harness spec 3.0.0, `filesystem.md` *Skills*).

A skill is `<skills dir>/<name>/SKILL.md`. A chat finds them in the `skills/`
directories along its instruction chain -- from its start directory up to its
boundary, the nearest first -- then in the person's own
`/home/<user>/skills/`, then in the organisation's `/skills/` (the skills
repository's mount, or a plain directory in its place). The most specific
place wins a name, as in the CLI, where a project's skill shadows a global one:
the root's own `skills/` is the organisation's and so comes last.

Only what the chat's session can read is a skill there: every lookup goes
through its `fs.View`, so a skill in a directory the chat was not granted does
not exist for it, and one in a read-only place can be read but not changed.
New skills go to the person's own skills directory. `skills_list`,
`skill_view` and `skill_manage` (`services/tools/skills.py`) are their meaning;
`edit` works on the file too, and every write is a version, which is what
`skill_manage`'s revert reads.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable
from dataclasses import dataclass

from lembas.db.models import KIND_DIR, KIND_FILE, KIND_MOUNT, LEVEL_STAGE
from lembas.security import access
from lembas.services import fs
from lembas.services.skill_format import (
    FILE_NAME,
    MAX_INDEX_SKILLS,
    loadable_name,
    lookup_key,
    parse,
)

SKILLS_DIR = "skills"
# How much of a SKILL.md the index reads: the frontmatter is at the top.
HEAD_BYTES = 4000


@dataclass(frozen=True)
class SkillFile:
    name: str
    description: str
    dir: str
    path: str
    writable: bool


def roots(v: fs.View) -> list[str]:
    """Where this chat looks for skills, in order of precedence."""
    boundary = access.normalise((v.chat.boundary if v.chat is not None else "/") or "/")
    out: list[str] = []
    for directory in access.chain(v.cwd):
        if not access.is_within(directory, boundary):
            break
        if directory == "/":
            # The root's `skills/` is the organisation's, and it comes last.
            break
        out.append(posixpath.join(directory, SKILLS_DIR))
    home = fs.home_of(v.db, v.user)
    if home is not None:
        out.append(posixpath.join(home.path, SKILLS_DIR))
    out.append(fs.SKILLS)
    seen: set[str] = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def home_root(v: fs.View) -> str:
    """Where a new skill is made: the person's own skills directory."""
    home = fs.home_of(v.db, v.user) or fs.ensure_home(v.db, v.user)
    return posixpath.join(home.path, SKILLS_DIR)


def _head(v: fs.View, path: str) -> str:
    parts = fs.read_stream(v, path)
    first = next(iter(parts), b"")
    return first[:HEAD_BYTES].decode("utf-8", errors="replace")


def _skills_in(v: fs.View, root: str) -> Iterable[SkillFile]:
    if not v.resolver.visible(root):
        return
    node = fs.lookup(v.db, v.org_id, root)
    if node is None or node.kind not in (KIND_DIR, KIND_MOUNT):
        return
    try:
        entries = fs.list_dir(v, root)
    except fs.FsError:
        return
    for entry in entries:
        if entry.kind not in (KIND_DIR, KIND_MOUNT):
            continue
        path = posixpath.join(entry.path, FILE_NAME)
        if not v.resolver.visible(path):
            continue
        file = fs.lookup(v.db, v.org_id, path)
        if file is None or file.kind != KIND_FILE:
            continue
        parsed = parse(_head(v, path))
        if parsed is None:
            continue
        meta, _body = parsed
        # Lenient (`filesystem.md` *Skills*): a SKILL.md made elsewhere is offered
        # under any name that is one line of at most 64 characters.
        name = loadable_name(meta.get("name") or entry.name)
        if name is None:
            continue
        yield SkillFile(
            name=name,
            description=" ".join(str(meta.get("description") or "").split()),
            dir=entry.path,
            path=path,
            writable=access.at_least(v.level(path), LEVEL_STAGE),
        )


def all_skills(v: fs.View, *, exclude: Iterable[str] = ()) -> list[SkillFile]:
    """Every skill this chat can read, a name's first claim winning, by name."""
    hidden = {lookup_key(name) for name in exclude}
    found: dict[str, SkillFile] = {}
    for root in roots(v):
        for skill in _skills_in(v, root):
            key = lookup_key(skill.name)
            if key not in found and key not in hidden:
                found[key] = skill
    return sorted(found.values(), key=lambda s: s.name)


def find(v: fs.View, name: str, *, exclude: Iterable[str] = ()) -> SkillFile | None:
    """A skill by name: case ignored, `_` and spaces read as `-`."""
    skills = all_skills(v, exclude=exclude)
    exact = (name or "").strip().lower()
    wanted = lookup_key(name)
    return next((s for s in skills if s.name.lower() == exact), None) or next(
        (s for s in skills if lookup_key(s.name) == wanted), None
    )


def count(v: fs.View, *, exclude: Iterable[str] = ()) -> int:
    return len(all_skills(v, exclude=exclude))


def index_block(v: fs.View, *, exclude: Iterable[str] = ()) -> str:
    """The one-line-per-skill listing that goes into the prompt."""
    skills = all_skills(v, exclude=exclude)[:MAX_INDEX_SKILLS]
    return "\n".join(f"- {skill.name}: {skill.description}" for skill in skills)


def stray_note(path: str) -> str:
    """The note a write of a SKILL.md where no skill is loaded from adds
    (`context.md` 5): an ordinary file, until it is under a skills directory."""
    if posixpath.basename(path) != FILE_NAME:
        return ""
    if posixpath.basename(posixpath.dirname(posixpath.dirname(path))) == SKILLS_DIR:
        return ""
    return (
        "\nNote: a SKILL.md here is an ordinary file — LLeMbas does not load it as a skill. "
        "To save a skill, use skill_manage (create)."
    )


__all__ = [
    "SkillFile",
    "all_skills",
    "count",
    "find",
    "home_root",
    "index_block",
    "roots",
    "stray_note",
]
