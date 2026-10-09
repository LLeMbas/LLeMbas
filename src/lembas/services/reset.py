"""Start over: a person empties their own data, at one of three levels.

"Sessions" means chats, and a full reset keeps the account and its settings.

- `chats`: every chat the person owns -- archived, temporary and task chats
  too -- their folders, and every file those chats' attachments named.
- `memory`: what models remember about them.
- `all`: both, plus notes, skills, knowledge bases with their documents and
  files, reports, schedules, scratch documents, and the shares of all of it.

What a full reset keeps: the account, its password and settings (its
per-model defaults too, `UserModelSetting`, which are settings), group
memberships, the person's own data groups (settings, not data), the aggregate
`Usage` rows, push subscriptions, and the session it was asked from.

**Files go before rows**, the order `chat.delete_chats` keeps and for the same
reason: the rows are what say which files they are. **Shares go before their
resources** (`sharing.forget_owner`), because `Share.resource_id` has no foreign
key and a grant left behind names a row that no longer exists. Chunks are the
other column with no foreign key to its record; `indexing.sweep_orphans` drops
them once the records are gone.

Bulk `DELETE`s rather than a loop of `db.delete`: the database's own cascades
(foreign keys are on, `db/session.py`) and FTS triggers do the rest, and a
person with ten thousand memories should not wait on ten thousand flushes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    Chat,
    Document,
    Folder,
    KnowledgeBase,
    Memory,
    Message,
    Note,
    Report,
    Schedule,
    ScratchDoc,
    Skill,
    User,
)
from lembas.web.i18n import t

log = logging.getLogger(__name__)

LEVEL_CHATS = "chats"
LEVEL_MEMORY = "memory"
LEVEL_ALL = "all"
LEVELS = (LEVEL_CHATS, LEVEL_MEMORY, LEVEL_ALL)

# What a person types to confirm a full reset, besides their password. Compared
# after `strip().upper()`, and the translated word is accepted beside it, so a
# Slovak reader is not asked to type English.
CONFIRM_WORD = "RESET"


class ResetRefused(Exception):
    """Something of theirs is still running; the message says what."""


@dataclass
class Counts:
    """How many of each thing a level deletes, in the order they are listed."""

    rows: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, n: int) -> None:
        if n:
            self.rows[name] = self.rows.get(name, 0) + int(n)

    @property
    def total(self) -> int:
        return sum(self.rows.values())


def _count(db: DBSession, model, column, user_id: str) -> int:
    return int(db.scalar(select(func.count()).select_from(model).where(column == user_id)) or 0)


def preview(db: DBSession, user: User) -> dict[str, Counts]:
    """What each level would delete for this person, for the confirmation."""
    chats = Counts()
    chats.add("chats", _count(db, Chat, Chat.user_id, user.id))
    chats.add(
        "messages",
        int(
            db.scalar(
                select(func.count())
                .select_from(Message)
                .join(Chat, Chat.id == Message.chat_id)
                .where(Chat.user_id == user.id)
            )
            or 0
        ),
    )
    chats.add("folders", _count(db, Folder, Folder.user_id, user.id))

    memory = Counts()
    memory.add("memories", _count(db, Memory, Memory.owner_id, user.id))

    everything = Counts()
    for part in (chats, memory):
        for name, n in part.rows.items():
            everything.add(name, n)
    everything.add("notes", _count(db, Note, Note.owner_id, user.id))
    everything.add("skills", _count(db, Skill, Skill.owner_id, user.id))
    everything.add("knowledge bases", _count(db, KnowledgeBase, KnowledgeBase.owner_id, user.id))
    everything.add("documents", _count(db, Document, Document.owner_id, user.id))
    everything.add("reports", _count(db, Report, Report.owner_id, user.id))
    everything.add("schedules", _count(db, Schedule, Schedule.user_id, user.id))
    everything.add("scratch documents", _count(db, ScratchDoc, ScratchDoc.user_id, user.id))
    return {LEVEL_CHATS: chats, LEVEL_MEMORY: memory, LEVEL_ALL: everything}


def busy(db: DBSession, user: User) -> str:
    """Why a reset must wait, or "" when it need not.

    A reply being written would land in a chat that no longer exists, and a
    schedule firing would write its report into a library that was just emptied.
    The person can stop the one and wait for the other; refusing is better than
    either half-finishing.
    """
    from lembas.services import generation

    chat_ids = set(db.scalars(select(Chat.id).where(Chat.user_id == user.id)))
    running = sum(1 for chat_id in chat_ids if generation.running_for(chat_id) is not None)
    if running:
        return (
            t(
                "A reply is still being written in one of your chats. Stop it, or wait "
                "for it to finish, and try again."
            )
            if running == 1
            else t(
                "%(n)s replies are still being written in your chats. Stop "
                "them, or wait for them to finish, and try again.",
                n=running,
            )
        )
    firing = db.scalar(
        select(Schedule.title).where(Schedule.user_id == user.id, Schedule.claimed_at.is_not(None))
    )
    if firing is not None:
        return t(
            "Your schedule “%(title)s” is running right now. Try again when it has finished.",
            title=firing,
        )
    return ""


def _delete_chats(db: DBSession, user: User, counts: Counts) -> None:
    from lembas.services import chat as chat_service

    chats = list(db.scalars(select(Chat).where(Chat.user_id == user.id)))
    counts.add("chats", len(chats))
    # Files first, while the attachment rows still say which they are.
    chat_service.delete_chats(db, chats)
    db.flush()
    counts.add(
        "folders", db.execute(delete(Folder).where(Folder.user_id == user.id)).rowcount or 0
    )


def _forget_memory(db: DBSession, user: User, counts: Counts) -> None:
    counts.add(
        "memories", db.execute(delete(Memory).where(Memory.owner_id == user.id)).rowcount or 0
    )


def _delete_library(db: DBSession, user: User, counts: Counts) -> None:
    from lembas.services import sharing
    from lembas.services.library.documents import stored_path

    # Shares of everything they own, while the rows are there to be matched.
    sharing.forget_owner(db, user.id)

    # Every document's file, in a base of theirs or not, before any row goes.
    for stored_name in db.scalars(
        select(Document.stored_name).where(Document.owner_id == user.id)
    ):
        path = stored_path(stored_name)
        if path is not None:
            path.unlink(missing_ok=True)
    counts.add(
        "documents", db.execute(delete(Document).where(Document.owner_id == user.id)).rowcount or 0
    )
    counts.add(
        "knowledge bases",
        db.execute(delete(KnowledgeBase).where(KnowledgeBase.owner_id == user.id)).rowcount or 0,
    )
    counts.add("notes", db.execute(delete(Note).where(Note.owner_id == user.id)).rowcount or 0)
    counts.add("skills", db.execute(delete(Skill).where(Skill.owner_id == user.id)).rowcount or 0)
    counts.add(
        "reports", db.execute(delete(Report).where(Report.owner_id == user.id)).rowcount or 0
    )
    counts.add(
        "schedules", db.execute(delete(Schedule).where(Schedule.user_id == user.id)).rowcount or 0
    )
    counts.add(
        "scratch documents",
        db.execute(delete(ScratchDoc).where(ScratchDoc.user_id == user.id)).rowcount or 0,
    )


def reset(db: DBSession, user: User, level: str) -> Counts:
    """Delete this person's data at `level`, and say how much went.

    Raises `ResetRefused` while anything of theirs is running, and `ValueError`
    for a level that does not exist. Commits.
    """
    if level not in LEVELS:
        raise ValueError(f"unknown reset level {level!r}")
    if reason := busy(db, user):
        raise ResetRefused(reason)

    counts = Counts()
    if level in (LEVEL_CHATS, LEVEL_ALL):
        _delete_chats(db, user, counts)
    if level in (LEVEL_MEMORY, LEVEL_ALL):
        _forget_memory(db, user, counts)
    if level == LEVEL_ALL:
        _delete_library(db, user, counts)
    db.commit()

    from lembas.services.library import indexing

    indexing.sweep_orphans(db)
    # `sweep_orphans` commits only when it removed something; its DELETE opened a
    # write transaction either way, and left open it holds SQLite's write lock
    # against every other session -- the audit record that follows, for one.
    db.commit()
    log.info("%s started over (%s): %s", user.email, level, counts.rows)
    return counts


__all__ = [
    "CONFIRM_WORD",
    "LEVELS",
    "LEVEL_ALL",
    "LEVEL_CHATS",
    "LEVEL_MEMORY",
    "Counts",
    "ResetRefused",
    "busy",
    "preview",
    "reset",
]
