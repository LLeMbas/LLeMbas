"""A skill's file: `SKILL.md`, its frontmatter, its name and its limits.

A skill is a directory with a `SKILL.md` -- frontmatter with a `name` and a
`description`, then the instructions -- the layout the LLeMbas CLI, Claude Code
and Hermes read (harness spec 3.0.0, `filesystem.md` *Skills*). This module is
the format alone, no storage: what `skill_manage` checks before it writes, and
what the index reads back. The checks are the ones the library's skills had
(1.x `services/library/skills.py`), kept so a skill that was valid stays valid.
"""

from __future__ import annotations

import json
import re

# A name `skill_manage` makes (harness spec 3.0.0, `filesystem.md` *Skills*; the
# Agent Skills format's rule): lowercase letters, digits and -, starting with a
# letter or a digit, at most 64 characters.
MAX_NAME_CHARS = 64
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

MAX_DESCRIPTION_CHARS = 400
MAX_BODY_CHARS = 20_000

# The index goes into every request, so it has a ceiling like memory does.
MAX_INDEX_SKILLS = 60

FILE_NAME = "SKILL.md"

_SHAPE = (
    "It must look like this:\n---\nname: {name}\ndescription: Use when …\n---\n\n"
    "The instructions.\n(Or send description and instructions instead of content.)"
)


def slugify(name: str) -> str:
    """A name as `skill_manage` would make it: lowercase, every run of anything
    else as one `-`, at most 64 characters."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return cleaned[:MAX_NAME_CHARS].rstrip("-")


def loadable_name(raw: str) -> str | None:
    """The name a `SKILL.md` made elsewhere is offered under, or None when it is
    not one: loading is lenient (`filesystem.md` *Skills*) -- any name that is
    one line of at most 64 characters, whitespace runs as one space."""
    name = " ".join(str(raw or "").split())
    if not name or len(name) > MAX_NAME_CHARS or "<" in name or ">" in name:
        return None
    return name


def lookup_key(name: str) -> str:
    """What a name is looked up by: case ignored, `_` and spaces read as `-`
    (the CLI's `slug`)."""
    key = re.sub(r"[\s_]+", "-", (name or "").strip().lower())
    key = re.sub(r"[^\w-]", "", key)
    return re.sub(r"-+", "-", key)


def compose(name: str, description: str, instructions: str) -> str:
    """A SKILL.md from its parts, as the LLeMbas CLI writes one (`composeSkill`)."""
    one_line = " ".join(description.split())
    return (
        f"---\nname: {name}\ndescription: {json.dumps(one_line, ensure_ascii=False)}\n---\n\n"
        f"{instructions.strip()}\n"
    )


def parse(text: str) -> tuple[dict[str, str], str] | None:
    """(frontmatter, body) of a SKILL.md, or None when it has no frontmatter.
    The values a skill needs are single lines -- a name and a description --
    so this reads `key: value`, with the value as JSON when it is quoted."""
    lines = text.replace("\r\n", "\n").lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    meta: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if value[:1] == '"':
            try:
                value = str(json.loads(value))
            except ValueError:
                value = value.strip('"')
        elif value[:1] == "'" and value[-1:] == "'":
            value = value[1:-1].replace("''", "'")
        meta[key.strip()] = value
    return meta, "\n".join(lines[end + 1 :]).strip()


def check_name(raw: str) -> str:
    """The skill's name as stored, or ValueError saying what a name must be."""
    name = slugify(raw)
    if not SKILL_NAME_PATTERN.match(name):
        raise ValueError(
            f"{raw!r} is not a valid skill name: lowercase letters, digits and -, starting "
            f"with a letter or digit, up to {MAX_NAME_CHARS}, such as 'weekly-report'."
        )
    return name


def validate(text: str, name: str) -> tuple[str, str]:
    """(description, body) of a whole SKILL.md for `name`, or ValueError saying
    what is wrong with it -- the CLI's `validateSkill`, against this server's
    limits."""
    parsed = parse(text)
    if parsed is None:
        raise ValueError(
            "SKILL.md must start with frontmatter between two --- lines. "
            + _SHAPE.format(name=name)
        )
    meta, body = parsed
    if slugify(meta.get("name", "")) != name:
        raise ValueError(f'The frontmatter name must be "{name}".')
    description = meta.get("description", "").strip()
    if not description:
        raise ValueError(
            "The frontmatter needs a description: one sentence saying when to use the "
            "skill. " + _SHAPE.format(name=name)
        )
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(
            f"The description is {len(description)} characters; the limit is "
            f"{MAX_DESCRIPTION_CHARS}."
        )
    if not body:
        raise ValueError(
            "SKILL.md needs instructions after the closing --- line. " + _SHAPE.format(name=name)
        )
    if len(body) > MAX_BODY_CHARS:
        raise ValueError(
            f"The instructions are {len(body):,} characters; the limit is {MAX_BODY_CHARS:,}."
        )
    return description, body


__all__ = [
    "FILE_NAME",
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_INDEX_SKILLS",
    "MAX_NAME_CHARS",
    "SKILL_NAME_PATTERN",
    "check_name",
    "compose",
    "loadable_name",
    "lookup_key",
    "parse",
    "slugify",
    "validate",
]
