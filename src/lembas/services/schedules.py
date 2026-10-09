"""Making, changing and stopping a schedule.

The row-level half: what the routes and the tools both need, so neither has its
own idea of what creating a schedule involves. `services/schedule/` holds the
machinery — when it next comes due, and what happens when it does.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    KIND_TASK,
    ORIGIN_USER,
    ORIGINS,
    TARGET_CHAT,
    TARGETS,
    Chat,
    Schedule,
    User,
)
from lembas.services import settings_store
from lembas.services.schedule import clock
from lembas.services.schedule import rule as rule_service
from lembas.web.i18n import N_, t

log = logging.getLogger(__name__)

MAX_TITLE_CHARS = 200
MAX_INSTRUCTION_CHARS = 8000


class ScheduleError(Exception):
    """Something a person needs told, in words they can act on.

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


def visible(user: User | None):
    if user is None:
        return select(Schedule).where(Schedule.id.is_(None))
    return select(Schedule).where(Schedule.user_id == user.id)


def get(db: DBSession, schedule_id: str, user: User | None) -> Schedule | None:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None or user is None or schedule.user_id != user.id:
        return None
    return schedule


def for_chat(db: DBSession, chat: Chat) -> Schedule | None:
    """The schedule a task chat belongs to, if any."""
    return db.scalars(select(Schedule).where(Schedule.chat_id == chat.id)).first()


def group_for(db: DBSession, owner: User, model_id: str) -> str:
    """The data group a schedule runs in: its model's, or the person's default's.

    Stamped on the schedule and on its task chat when it is made. A run reads
    that group's memories and notes, and what it produces -- a report, a turn
    in a chat -- lands in it.
    """
    from lembas.services import chat as chat_service
    from lembas.services import data_groups

    if model_id:
        return data_groups.for_pair(db, owner, model_id)
    chosen = chat_service.default_model(db, owner)
    return data_groups.for_pair(db, owner, *chosen) if chosen else data_groups.DEFAULT_GROUP


def count_for(db: DBSession, user: User) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(Schedule).where(Schedule.user_id == user.id)
        )
        or 0
    )


def create(
    db: DBSession,
    *,
    owner: User,
    title: str,
    instruction: str,
    rule: dict,
    request: str = "",
    target: str = TARGET_CHAT,
    model_id: str = "",
    origin: str = ORIGIN_USER,
) -> Schedule:
    """Write a schedule and the chat it fires into.

    **The chat is created here, with the schedule**, and this is the one place
    "chats are created lazily" is deliberately bent. That rule exists so an
    opened-and-abandoned chat never appears in the sidebar; a task chat is not
    opened and abandoned, because creating it *is* the act. It also has to exist
    before the first firing, which may be days away and will have nobody
    present to make one.

    A task chat is never `KIND_AGENT`. Scheduling one would mean running
    commands on a timer with nobody watching — and since every mode except Auto
    stalls waiting for an approval that will not come, the only two outcomes are
    "unattended execution" and "does nothing". That deserves its own pass with a
    mode built for it, not a flag here.
    """
    clean = rule_service.validate(rule)
    if not clean:
        raise ScheduleError(
            N_(
                "That does not describe a time anything could run at. "
                "Say when it should happen — a date and time, or how often."
            )
        )
    if rule_service.next_after(clean, datetime.now(tz=UTC), zone=clock.zone_for(owner)) is None:
        # Belt and braces over `validate`'s own invariant. A schedule that can
        # never fire looks exactly like a working one on every screen it appears
        # on, so it is refused at the only moment somebody is present to be told.
        raise ScheduleError(N_("That schedule has no next run — its time has already passed."))

    group = group_for(db, owner, model_id)
    target = target if target in TARGETS else TARGET_CHAT

    limit = int(settings_store.schedules(db).get("max_per_user") or 20)
    if count_for(db, owner) >= limit:
        raise ScheduleError(
            N_(
                "You already have %(limit)s schedules, which is the most this instance "
                "allows. Remove one before adding another."
            ),
            limit=limit,
        )

    chat = Chat(
        user_id=owner.id,
        kind=KIND_TASK,
        title=(title.strip() or "Scheduled task")[:MAX_TITLE_CHARS],
        model_id=model_id or "",
        data_group_id=group,
        # Said on the row as well as implied by the kind. `tools.unattended`
        # reads both, because the column was added to a table that already held
        # task chats and a backfill cannot know which they were -- but every one
        # written from here on says so for itself, which is what the check on
        # the kind is there to stop being needed forever.
        unattended=True,
    )
    db.add(chat)
    db.flush()

    schedule = Schedule(
        user_id=owner.id,
        title=(title.strip() or "Scheduled task")[:MAX_TITLE_CHARS],
        request=(request or "").strip()[:MAX_INSTRUCTION_CHARS],
        instruction=(instruction or "").strip()[:MAX_INSTRUCTION_CHARS],
        rule_json=clean,
        target=target if target in TARGETS else TARGET_CHAT,
        chat_id=chat.id,
        model_id=model_id or "",
        data_group_id=group,
        origin=origin if origin in ORIGINS else ORIGIN_USER,
        enabled=True,
        next_fire_at=rule_service.next_after(
            clean, datetime.now(tz=UTC), zone=clock.zone_for(owner)
        ),
        compiled_at=datetime.now(tz=UTC),
    )
    db.add(schedule)
    db.commit()
    return schedule


def update(
    db: DBSession,
    schedule: Schedule,
    *,
    owner: User,
    title: str | None = None,
    instruction: str | None = None,
    rule: dict | None = None,
    target: str | None = None,
) -> Schedule:
    """Change a schedule. Absent arguments are left alone."""
    if title is not None and title.strip():
        schedule.title = title.strip()[:MAX_TITLE_CHARS]
    if instruction is not None:
        schedule.instruction = instruction.strip()[:MAX_INSTRUCTION_CHARS]
    if target is not None and target in TARGETS:
        schedule.target = target
    if rule is not None:
        clean = rule_service.validate(rule)
        if not clean:
            raise ScheduleError(
                N_(
                    "That does not describe a time anything could run at. "
                    "Say when it should happen — a date and time, or how often."
                )
            )
        schedule.rule_json = clean
        # Recomputed from now, and the count restarted: an edited schedule is a
        # new intention, and carrying the old `fired_count` into a new `count`
        # would silently spend most of it before the first run.
        schedule.fired_count = 0
        schedule.next_fire_at = rule_service.next_after(
            clean, datetime.now(tz=UTC), zone=clock.zone_for(owner)
        )
        if schedule.next_fire_at is None:
            raise ScheduleError(N_("That schedule has no next run — its time has already passed."))
    db.commit()
    return schedule


def set_enabled(db: DBSession, schedule: Schedule, *, owner: User, enabled: bool) -> Schedule:
    """Pause or resume.

    **Resuming recomputes from now**, never from the stored value. A schedule
    paused for a month would otherwise come back due — and with catching-up in
    the sweep, it would fire the moment it was switched on, having decided it
    was owed a run from four weeks ago.
    """
    schedule.enabled = bool(enabled)
    if enabled:
        schedule.last_error = ""
        schedule.next_fire_at = rule_service.next_after(
            schedule.rule_json or {},
            datetime.now(tz=UTC),
            zone=clock.zone_for(owner),
            fired=schedule.fired_count or 0,
        )
        if schedule.next_fire_at is None:
            schedule.enabled = False
            schedule.last_error = "There are no runs left in this schedule."
    db.commit()
    return schedule


def delete(db: DBSession, schedule: Schedule, *, keep_chat: bool = True) -> None:
    """Remove a schedule, and by default keep its transcript.

    Keeping is the default because deleting a conversation as a side effect of
    removing a timer is exactly the destructive default this codebase avoids
    elsewhere. The chat becomes an ordinary one so it is reachable again — a
    `KIND_TASK` chat with no schedule behind it would be in no list at all.
    """
    chat = db.get(Chat, schedule.chat_id) if schedule.chat_id else None
    if chat is not None:
        if keep_chat:
            chat.kind = "chat"
        else:
            from lembas.services import chat as chat_service

            chat_service.delete_chats(db, [chat])
    db.delete(schedule)
    db.commit()


def describe(schedule: Schedule, *, owner: User | None) -> str:
    return rule_service.describe(schedule.rule_json or {}, zone=clock.zone_for(owner))
