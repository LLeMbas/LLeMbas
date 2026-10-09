"""Memory: short facts, in front of the model on every turn.

The whole design follows from being injected rather than searched.

* Each record is **capped short**, because every one of them costs tokens on
  every request forever. A tool that writes an essay gets it trimmed and is
  told so, rather than the write failing -- the model can then decide to put
  the long version in a note.
* There is a **budget** for the block as a whole. Past it the oldest are left
  out rather than the request growing without limit; the user can see the whole
  list in their settings and prune it.
* There is **no search tool**. Searching something the model is already looking
  at is a round trip for nothing.
* They are **not shareable**. A record about a person is not content to hand
  round, and nobody asked to share their memories with a group.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import AUTHOR_MODEL, AUTHOR_USER, DEFAULT_GROUP, Memory, User
from lembas.web.i18n import N_, t

log = logging.getLogger(__name__)


class Refused(ValueError):
    """A memory that was not saved, and why.

    Two readers, two languages: `str(exc)` is the English, which is what a
    model is handed as a tool result -- a generation inherits the request's
    language, so translating here would put a person's language into a prompt --
    and `translated()` is the same sentence in the reader's language, for a page.
    The text is marked with `N_()` where it is raised so the catalogue finds it.
    """

    def __init__(self, text: str, **fields: object) -> None:
        self.text, self.fields = text, fields
        super().__init__(text % fields if fields else text)

    def translated(self) -> str:
        return t(self.text, **self.fields)


# One fact, not a paragraph. Long enough for "prefers metric units and a 24-hour
# clock", short enough that fifty of them are still affordable.
MAX_MEMORY_CHARS = 400

# Ceiling on the injected block. Reached, the oldest records drop out of the
# prompt -- they are still listed in settings, so nothing disappears silently.
MAX_TOTAL_CHARS = 4000

# A hard stop on how many can exist, so an enthusiastic model cannot fill a
# database with variations on one fact.
MAX_RECORDS = 200


def all_for(db: DBSession, user: User | None, group: str | None = None) -> list[Memory]:
    """This person's memories; `group` narrows to one data group, for a model.

    `None` is the person's own list in their settings, which shows every group.
    """
    if user is None:
        return []
    query = select(Memory).where(Memory.owner_id == user.id)
    if group is not None:
        from lembas.services import data_groups

        query = query.where(data_groups.condition(Memory, group))
    return list(db.scalars(query.order_by(Memory.created_at)))


def get(db: DBSession, memory_id: str, user: User | None) -> Memory | None:
    memory = db.get(Memory, memory_id)
    if memory is None or user is None or memory.owner_id != user.id:
        return None
    return memory


def add(
    db: DBSession,
    *,
    owner: User,
    content: str,
    author: str = AUTHOR_MODEL,
    group: str = DEFAULT_GROUP,
) -> Memory:
    """Record a fact. Raises ValueError when there is no room or nothing to say.

    An exact repeat returns the record that already exists rather than making a
    second one. The prompt asks the model to check before adding -- it is shown
    every memory, so it can -- but the same preference saved four times in
    slightly different words is the commonest failure here, and it is worse than
    wasted tokens: it makes `memory_forget` ambiguous for every one of them.
    Wording handles the near-duplicates; this handles the exact ones, which is
    the half a prompt cannot be relied on for.
    """
    content = " ".join((content or "").split())
    if not content:
        raise Refused(N_("A memory cannot be empty."))
    content = content[:MAX_MEMORY_CHARS]
    group = group or DEFAULT_GROUP

    # Both checks are per data group. A repeat of a fact another group already
    # holds is a new memory *here* -- returning the other group's row would be
    # telling this group's model that it had saved something it cannot see.
    from lembas.services import data_groups

    in_group = data_groups.condition(Memory, group)
    existing = db.scalars(
        select(Memory).where(Memory.owner_id == owner.id, Memory.content == content, in_group)
    ).first()
    if existing is not None:
        return existing

    count = db.scalar(
        select(func.count())
        .select_from(Memory)
        .where(Memory.owner_id == owner.id, in_group)
    )
    if (count or 0) >= MAX_RECORDS:
        # Deliberately does NOT say "remove one first". Past MAX_TOTAL_CHARS the
        # injected block is truncated, so the model is not shown every memory
        # and would be choosing blind -- and deleting the wrong one is not
        # something anybody finds out about.
        raise Refused(
            N_(
                "There are already %(n)s memories, which is the limit, so "
                "nothing was saved. Do not remove one to make room — you are not "
                "shown all of them and would be guessing. Say that the limit has "
                "been reached, and put this in a note instead."
            ),
            n=MAX_RECORDS,
        )

    memory = Memory(
        owner_id=owner.id,
        data_group_id=group,
        content=content,
        author=author if author in (AUTHOR_USER, AUTHOR_MODEL) else AUTHOR_MODEL,
    )
    db.add(memory)
    db.commit()
    return memory


def update(db: DBSession, memory: Memory, content: str) -> Memory:
    content = " ".join((content or "").split())
    if not content:
        raise Refused(N_("A memory cannot be empty."))
    memory.content = content[:MAX_MEMORY_CHARS]
    db.commit()
    return memory


def delete(db: DBSession, memory: Memory) -> None:
    db.delete(memory)
    db.commit()


def block(db: DBSession, user: User | None, group: str | None = None) -> str:
    """The memories as they appear in the prompt, within the budget.

    Oldest first, and truncation drops the *newest* -- a fact that has survived
    a long time is more likely to be a standing preference than something said
    once this morning.
    """
    records = all_for(db, user, group)
    if not records:
        return ""

    lines: list[str] = []
    total = 0
    for memory in records:
        line = f"- {memory.content}"
        if total + len(line) > MAX_TOTAL_CHARS:
            lines.append(f"- (…{len(records) - len(lines)} more, see your settings)")
            break
        lines.append(line)
        total += len(line)

    return "\n".join(lines)
