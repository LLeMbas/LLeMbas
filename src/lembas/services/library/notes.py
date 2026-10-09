"""Notes: what the model wrote down, and what a person wrote for it.

Longer and more specific than a memory, and not injected. A dozen notes would
fill a context window on their own, so the model searches for the one it needs
-- which is also why a note has a title worth reading: it is what a search
result shows.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import AUTHOR_MODEL, AUTHOR_USER, CHUNK_NOTE, DEFAULT_GROUP, Note, User
from lembas.services import sharing
from lembas.services.library import retrieval

log = logging.getLogger(__name__)

INDEX = "notes_fts"

MAX_TITLE_CHARS = 300
MAX_BODY_CHARS = 40_000
SNIPPET_CHARS = 800


def visible(db: DBSession, user: User | None, group: str | None = None):
    """Notes this user may see; `group` narrows to one data group, for a model."""
    return select(Note).where(sharing.visible_to(Note, user, group))


def get(
    db: DBSession, note_id: str, user: User | None, group: str | None = None
) -> Note | None:
    note = db.get(Note, note_id)
    if note is None or not sharing.can_read(db, note, user, group):
        return None
    return note


def recent(
    db: DBSession, user: User | None, *, limit: int = 20, group: str | None = None
) -> list[Note]:
    return list(
        db.scalars(visible(db, user, group).order_by(Note.updated_at.desc()).limit(limit))
    )


def search(
    db: DBSession,
    user: User | None,
    needle: str,
    *,
    limit: int = 10,
    vector: list[float] | None = None,
    group: str | None = None,
) -> list[Note]:
    """Notes matching `needle` that this user may see, best match first.

    `vector` is the query already embedded, or None. It comes from the caller
    rather than being worked out here because this is synchronous and embedding
    is an HTTP request -- see `services/library/retrieval.py`. None means the
    keyword search exactly as it always was.
    """
    hits = retrieval.search(db, INDEX, needle, kind=CHUNK_NOTE, vector=vector, limit=limit * 4)
    if not hits:
        return []
    order = {hit.id: position for position, hit in enumerate(hits)}
    rows = list(db.scalars(visible(db, user, group).where(Note.id.in_(list(order)))))
    rows.sort(key=lambda note: order.get(note.id, len(order)))
    return rows[:limit]


def create(
    db: DBSession,
    *,
    owner: User,
    title: str,
    body: str,
    author: str = AUTHOR_USER,
    group: str = DEFAULT_GROUP,
) -> Note:
    note = Note(
        owner_id=owner.id,
        data_group_id=group or DEFAULT_GROUP,
        title=(title.strip() or "Untitled")[:MAX_TITLE_CHARS],
        body=body.strip()[:MAX_BODY_CHARS],
        author=author if author in (AUTHOR_USER, AUTHOR_MODEL) else AUTHOR_USER,
    )
    db.add(note)
    db.commit()
    return note


def update(db: DBSession, note: Note, *, title: str | None = None, body: str | None = None) -> Note:
    """Change a note. Absent arguments are left alone, which is what lets a tool
    edit only the body without having to send the title back."""
    if title is not None and title.strip():
        note.title = title.strip()[:MAX_TITLE_CHARS]
    if body is not None:
        note.body = body.strip()[:MAX_BODY_CHARS]
    db.commit()
    return note


def delete(db: DBSession, note: Note) -> None:
    sharing.forget_resource(db, note)
    db.delete(note)
    db.commit()


def snippet(note: Note) -> str:
    text = (note.body or "").strip()
    if len(text) <= SNIPPET_CHARS:
        return text
    return text[:SNIPPET_CHARS].rstrip() + "…"
