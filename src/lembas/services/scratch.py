"""A chat's own working surface.

One text artefact per chat, written by the model through `scratch_write` and by
the person through the canvas panel, and handed to a message as an ordinary
attachment when it is ready.

Not a note. A note is a durable artefact of the reader's that outlives the chat
and is searchable; this is the chat's own record of what it is working on, which
is the line `plan_update` sits on rather than `notes_edit`. It is deliberately
not injected as context every turn either -- that is what a memory is for, and a
working document injected whole on every request is how a window fills up.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import AUTHOR_USER, Chat, ScratchDoc

# Between a note's 40k and an attachment's 120k. Large enough to hold a draft
# somebody is actually working on, small enough that attaching one does not
# quietly cost most of a context window.
MAX_BODY_CHARS = 100_000
MAX_TITLE_CHARS = 300


def get(db: DBSession, chat: Chat) -> ScratchDoc | None:
    """The chat's pad, or None. No side effect.

    Separate from `for_chat` because the harness and the mention picker ask
    whether there is one, and a question must not create the thing it asks
    about -- otherwise every chat ever opened acquires an empty row.
    """
    return db.scalar(select(ScratchDoc).where(ScratchDoc.chat_id == chat.id))


def for_chat(db: DBSession, chat: Chat) -> ScratchDoc:
    """The chat's pad, made if it is not there yet."""
    existing = get(db, chat)
    if existing is not None:
        return existing
    doc = ScratchDoc(chat_id=chat.id, user_id=chat.user_id)
    db.add(doc)
    db.commit()
    return doc


def update(
    db: DBSession,
    doc: ScratchDoc,
    *,
    body: str | None = None,
    title: str | None = None,
    author: str = AUTHOR_USER,
) -> ScratchDoc:
    """Replace what is in the pad. Absent arguments are left alone.

    The body is **not** stripped, unlike a note's. This is a document somebody
    is editing, and trailing whitespace they typed is theirs -- a save that
    silently trims the line you are standing on is the kind of thing that makes
    an editor feel broken.
    """
    if title is not None and title.strip():
        doc.title = title.strip()[:MAX_TITLE_CHARS]
    if body is not None:
        doc.body = body[:MAX_BODY_CHARS]
    doc.author = author
    db.commit()
    return doc


def append(db: DBSession, doc: ScratchDoc, text: str, *, author: str) -> ScratchDoc:
    """Add to the end, with a blank line between what was there and what is new.

    Its own function rather than the caller reading and concatenating, because
    two calls in one round would otherwise each read the same body and the
    second would drop the first -- the same lost update `plan_update` documents.
    """
    existing = doc.body or ""
    joined = f"{existing.rstrip()}\n\n{text}" if existing.strip() else text
    return update(db, doc, body=joined, author=author)
