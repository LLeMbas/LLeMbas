"""A stored document: its header, and its body in sections.

Skills and notes are Markdown files with a header between two `---` lines.
The header is read as single-line `key: value` fields at the top level;
everything else in it -- fields this server does not know, nested blocks,
comments -- is **kept byte for byte** when a field is set, because the same
files are read by other harnesses.

The body is addressed in **sections**: the text before the first heading
(`intro`), then one section per heading, each running to the next heading of
any level. Flat on purpose: replacing a section can never take a subsection
with it. Headings inside fenced code are not headings.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

INTRO = "intro"
_PLAIN = re.compile(r"^[A-Za-z0-9_./@+()-][A-Za-z0-9 _./@+():,-]*$")
_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^(```+|~~~+)")


def split(text: str) -> tuple[list[str] | None, str]:
    """(the header's lines without the `---`s, the body) -- None: no header."""
    text = (text or "").replace("\r\n", "\n").lstrip("﻿")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], "\n".join(lines[i + 1 :])
    return None, text


def _value(raw: str):
    raw = raw.strip()
    if raw[:1] == '"':
        try:
            return str(json.loads(raw))
        except ValueError:
            return raw.strip('"')
    if raw[:1] == "'" and raw[-1:] == "'" and len(raw) > 1:
        return raw[1:-1].replace("''", "'")
    if raw[:1] == "[" and raw[-1:] == "]":
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                return [str(item) for item in loaded]
        except ValueError:
            pass
        return [part.strip().strip("\"'") for part in raw[1:-1].split(",") if part.strip()]
    return raw


def fields(header: list[str] | None) -> dict[str, object]:
    """The header's top-level single-line fields (a list for `[a, b]`).
    **A field named twice is read once, and it is the first**: one fixed rule,
    so a line added further down can never change what a field says."""
    out: dict[str, object] = {}
    for line in header or []:
        if line[:1] in (" ", "\t", "#", "-"):
            continue
        found = _KEY.match(line)
        if found and found.group(1) not in out:
            out[found.group(1)] = _value(found.group(2))
    return out


def duplicates(header: list[str] | None) -> list[str]:
    """The fields a header names more than once (a file made elsewhere)."""
    seen: set[str] = set()
    twice: list[str] = []
    for line in header or []:
        if line[:1] in (" ", "\t", "#", "-"):
            continue
        found = _KEY.match(line)
        if not found:
            continue
        key = found.group(1)
        if key in seen and key not in twice:
            twice.append(key)
        seen.add(key)
    return twice


def plain_body(text: str) -> str:
    """Text that is a body and nothing else: the `---` lines it begins with
    are dropped, so nothing in it can stand where a header stands. What was
    between them stays, as the ordinary text it now is -- nothing a caller
    wrote is lost, and nothing a caller wrote says what a file's fields are."""
    lines = (text or "").replace("\r\n", "\n").lstrip("\ufeff").split("\n")
    while lines and lines[0].strip() in ("", "---"):
        lines.pop(0)
    return "\n".join(lines).strip("\n")


def compose(header: dict[str, object], body: str) -> str:
    """A file from fields the server names and a body that is only a body."""
    lines = [f"{key}: {scalar(value)}" for key, value in header.items() if value is not None]
    return "---\n" + "\n".join(lines) + "\n---\n\n" + plain_body(body) + "\n"


def scalar(value) -> str:
    """A value as one header line holds it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return json.dumps([str(v) for v in value], ensure_ascii=False)
    text = " ".join(str(value).split())
    if (
        _PLAIN.match(text)
        and ": " not in text
        and not text.endswith(":")
        and text.lower() not in ("true", "false", "null", "yes", "no", "~")
    ):
        return text
    return json.dumps(text, ensure_ascii=False)


def set_fields(text: str, changes: dict[str, object]) -> str:
    """The document with these header fields set (None: removed) and every
    other line of the header as it was. A document without a header gets one."""
    header, body = split(text)
    lines = list(header or [])
    pending = dict(changes)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        found = _KEY.match(line) if line[:1] not in (" ", "\t", "#", "-") else None
        if found and found.group(1) in changes:
            # Every occurrence goes: the first becomes the value, a later one
            # is removed, so a field that is set is said exactly once.
            if found.group(1) in pending:
                value = pending.pop(found.group(1))
                if value is not None:
                    out.append(f"{found.group(1)}: {scalar(value)}")
            i += 1
            # What belonged to the replaced field (a nested block) goes with it.
            while i < len(lines) and lines[i][:1] in (" ", "\t"):
                i += 1
            continue
        out.append(line)
        i += 1
    for key, value in pending.items():
        if value is not None:
            out.append(f"{key}: {scalar(value)}")
    body = body if header is not None else (text or "").replace("\r\n", "\n")
    return "---\n" + "\n".join(out) + "\n---\n" + ("\n" + body.lstrip("\n") if body.strip() else "")


def body_of(text: str) -> str:
    return split(text)[1].strip("\n")


def with_body(text: str, body: str) -> str:
    """The document with its body replaced and its header untouched."""
    header, _old = split(text)
    body = body.strip("\n")
    if header is None:
        return body + "\n"
    return "---\n" + "\n".join(header) + "\n---\n\n" + body + "\n"


@dataclass(frozen=True)
class Section:
    id: str
    level: int
    title: str
    start: int  # line index, inclusive
    end: int  # line index, exclusive
    size: int  # characters


def slug(title: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return cleaned[:60].strip("-") or "section"


def sections(body: str) -> list[Section]:
    lines = (body or "").split("\n")
    marks: list[tuple[int, int, str]] = []
    fence = ""
    for i, line in enumerate(lines):
        opened = _FENCE.match(line.strip())
        if opened:
            token = opened.group(1)
            if not fence:
                fence = token[0] * 3
            elif token.startswith(fence):
                fence = ""
            continue
        if fence:
            continue
        found = _HEADING.match(line)
        if found:
            marks.append((i, len(found.group(1)), found.group(2).strip()))
    out: list[Section] = []
    seen: dict[str, int] = {}
    first = marks[0][0] if marks else len(lines)
    if "\n".join(lines[:first]).strip():
        out.append(Section(INTRO, 0, "", 0, first, len("\n".join(lines[:first]).strip())))
        seen[INTRO] = 1
    for n, (at, level, title) in enumerate(marks):
        end = marks[n + 1][0] if n + 1 < len(marks) else len(lines)
        ident = slug(title)
        seen[ident] = seen.get(ident, 0) + 1
        if seen[ident] > 1:
            ident = f"{ident}-{seen[ident]}"
        out.append(
            Section(ident, level, title, at, end, len("\n".join(lines[at:end]).strip()))
        )
    return out


def find_section(body: str, ident: str) -> Section | None:
    wanted = (ident or "").strip().lower()
    found = sections(body)
    return next((s for s in found if s.id == wanted), None) or next(
        (s for s in found if slug(s.title) == slug(wanted) and s.id != INTRO), None
    )


def section_text(body: str, section: Section) -> str:
    return "\n".join(body.split("\n")[section.start : section.end]).strip("\n")


def replace_section(body: str, section: Section, text: str) -> str:
    """The body with one section's text replaced. A replacement that does not
    begin with a heading keeps the section's own; an empty one removes it."""
    lines = body.split("\n")
    new = (text or "").strip("\n")
    if new and section.level and not _HEADING.match(new.split("\n", 1)[0]):
        new = lines[section.start] + "\n\n" + new
    middle = (new.split("\n") + [""]) if new else []
    merged = lines[: section.start] + middle + lines[section.end :]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(merged)).strip("\n")


def append(body: str, text: str) -> str:
    return (body.strip("\n") + "\n\n" + (text or "").strip("\n")).strip("\n")


def outline(body: str) -> list[dict[str, object]]:
    return [
        {"id": s.id, "level": s.level, "title": s.title, "size": s.size}
        for s in sections(body)
    ]


def title_of(body: str) -> str:
    return next((s.title for s in sections(body) if s.level == 1), "")


__all__ = [
    "INTRO",
    "Section",
    "append",
    "body_of",
    "compose",
    "duplicates",
    "plain_body",
    "fields",
    "find_section",
    "outline",
    "replace_section",
    "scalar",
    "section_text",
    "sections",
    "set_fields",
    "slug",
    "split",
    "title_of",
    "with_body",
]
