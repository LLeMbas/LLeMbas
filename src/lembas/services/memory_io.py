"""A person's memories out and in: the `lembas-export` file, kind `memory`, version 1.

The format is the one the 1.7 LTS line writes (`LLeMbas.wiki/Export-format.md`);
2.0 writes the same, so a file exported here can be imported anywhere that
reads version 1, including here again.

**Importing names and drops, it never fails whole.** The header is checked
first -- not `lembas-export`, a newer `format_version`, or another `kind` is
refused with the reason -- and then every item on its own: a type that is not
`memory`, an empty text, a repeat of a memory already there, or the 200-record
limit each skip that item and say why. What could be taken is taken.

**Where an imported memory goes.** Into the data group of the same name when
this person may use one; otherwise into the group they chose on the form. A
memory is only ever written into the importing person's own account.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from lembas import __version__
from lembas.db.models import AUTHOR_MODEL, AUTHOR_USER, DEFAULT_GROUP, Memory, User
from lembas.services import data_groups
from lembas.services import memories as memories_service
from lembas.web.i18n import t

FORMAT = "lembas-export"
VERSION = 1
MAX_FILE = 5 * 1024 * 1024


class Refused(ValueError):
    """The file as a whole is not one to import, in the reader's language."""


def _stamp(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def export(db: DBSession, user: User, group: str | None = None) -> dict[str, Any]:
    """The document: every memory of `user`, or only those of one data group."""
    rows = memories_service.all_for(db, user, group or None)
    names = {g.id: g.name for g in data_groups.all_groups(db)}
    items = []
    for row in rows:
        group_id = row.data_group_id or DEFAULT_GROUP
        items.append(
            {
                "type": "memory",
                "ref": row.id,
                "name": row.content[:60],
                "data": {
                    "content": row.content,
                    "author": row.author,
                    "data_group": names.get(group_id, ""),
                    "data_group_ref": group_id,
                    "created_at": _stamp(row.created_at),
                    "updated_at": _stamp(row.updated_at),
                },
            }
        )
    document: dict[str, Any] = {
        "format": FORMAT,
        "format_version": VERSION,
        "kind": "memory",
        "app_version": __version__,
        "created_at": _stamp(datetime.now(UTC)),
        "owner": {"name": user.name, "email": user.email},
        "counts": {"memory": len(items)},
        "items": items,
    }
    if group:
        document["data_group"] = names.get(group, "")
    return document


def load(raw: bytes) -> dict[str, Any]:
    """The header checked, or `Refused` saying why not."""
    if len(raw) > MAX_FILE:
        raise Refused(t("That file is too large to be a memory export."))
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as problem:
        raise Refused(t("That file is not an export: it is not JSON.")) from problem
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        raise Refused(t("That file is not a LLeMbas export."))
    version = document.get("format_version")
    if not isinstance(version, int) or version > VERSION:
        raise Refused(
            t(
                "That export is format version %(theirs)s; this server reads version %(ours)s.",
                theirs=version,
                ours=VERSION,
            )
        )
    if document.get("kind") != "memory":
        raise Refused(
            t("That is not a memory export; an administrator's export is imported elsewhere.")
        )
    if not isinstance(document.get("items"), list):
        raise Refused(t("That export has no items."))
    return document


@dataclass
class Report:
    taken: list[str] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)  # (name, why)


def import_into(db: DBSession, user: User, document: dict[str, Any], *, into: str) -> Report:
    """Take what can be taken from a loaded document; name what cannot."""
    report = Report()
    usable = {g.id: g for g in data_groups.usable(db, user)}
    by_name = {g.name.casefold(): g.id for g in usable.values()}
    for item in document.get("items", []):
        if not isinstance(item, dict):
            report.dropped.append(("?", t("not an item")))
            continue
        name = str(item.get("name") or item.get("ref") or "?")[:80]
        if item.get("type") != "memory":
            report.dropped.append((name, t("not a memory (%(type)s)", type=item.get("type"))))
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        content = " ".join(str(data.get("content") or "").split())
        if not content:
            report.dropped.append((name, t("empty")))
            continue
        if into in usable:
            group = into
        else:
            # The same group when this instance has one by that name; the
            # default otherwise. An id from another instance means nothing here.
            group = by_name.get(str(data.get("data_group") or "").casefold(), DEFAULT_GROUP)
            if str(data.get("data_group_ref") or "") == DEFAULT_GROUP:
                group = DEFAULT_GROUP
        author = data.get("author")
        if author not in (AUTHOR_USER, AUTHOR_MODEL):
            author = AUTHOR_USER
        before = db.scalar(
            select(Memory.id).where(
                Memory.owner_id == user.id,
                Memory.content == content[: memories_service.MAX_MEMORY_CHARS],
                data_groups.condition(Memory, group),
            )
        )
        if before is not None:
            report.dropped.append((name, t("already remembered")))
            continue
        try:
            memories_service.add(db, owner=user, content=content, author=author, group=group)
        except memories_service.Refused as refused:
            report.dropped.append((name, refused.translated()))
            continue
        report.taken.append(name)
    return report


def purge(db: DBSession, user: User, group: str) -> int:
    """Forget every memory of `user` in one data group. Returns how many."""
    found = db.execute(
        delete(Memory).where(Memory.owner_id == user.id, data_groups.condition(Memory, group))
    )
    db.commit()
    return found.rowcount or 0


__all__ = ["FORMAT", "VERSION", "Refused", "Report", "export", "import_into", "load", "purge"]
