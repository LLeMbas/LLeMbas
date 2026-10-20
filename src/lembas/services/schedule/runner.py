"""What happens when a schedule fires.

Every schedule fires the same way — a turn into a chat, answered by the ordinary
generation loop — and the *target* decides only what becomes of the finished
reply. One mechanism, two deliveries:

- `chat`     leave it there. The reply is the point, and it is already in the
             task chat where somebody will read it.
- `report`   copy it into a `Report` and keep the chat out of the way. Copied
             rather than moved: the task chat is the working area and keeps the
             tool calls, the steps and the metrics; the report gets the answer.

The alternative — a one-shot `complete()` in the shape of `generate_title` — was
rejected because it has no tools and no rounds, which is useless for the case
this feature exists for. "Give me a daily news report" needs to search the web.

**Nothing in `services/generation.py` changes.** The waiting happens here, in a
task per firing -- the same shape a background helper's watcher has
(`services/subagent.py`). Making generation aware of schedules would mean a
branch inside `_persist`, and that is the single writer with one rule.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime

from lembas.db.models import (
    DEFAULT_GROUP,
    TARGET_CHAT,
    TARGET_REPORT,
    Chat,
    Message,
    Schedule,
    User,
)
from lembas.db.session import session_scope
from lembas.services import reports as reports_service
from lembas.services import wake as wake_service
from lembas.services.schedule import clock
from lembas.services.schedule import rule as rule_service

log = logging.getLogger(__name__)

# How long to wait for a firing's reply before giving up on delivering it. The
# reply itself is not cancelled -- it goes on and lands in its chat, which is
# where a task chat's output belongs anyway. What times out is only *this*
# task's interest in copying the result somewhere.
DELIVERY_TIMEOUT = 3600.0
# How often the waiter looks. Coarse on purpose: nothing is watching this, and a
# report arriving three seconds late costs nobody anything.
POLL_SECONDS = 3.0


def _preamble(schedule: Schedule, *, zone, due_at: datetime | None) -> str:
    """The turn a firing puts into the chat.

    Names itself a scheduled event in *words*, because the role stays `user` --
    `_inject` sends a queued turn verbatim and `build_messages` must keep seeing
    a user turn. The framing therefore cannot live in the role, exactly as it
    cannot for a finished background job.

    The scheduled time is stated as well as the actual one, so a run caught up
    after an outage can say so rather than reporting stale news as current.
    """
    now = datetime.now(tz=UTC).astimezone(zone)
    lines = [
        "This turn was started by a schedule, not by the person — "
        "they are not necessarily at the keyboard.",
        "",
        f"[schedule: {schedule.title or 'untitled'}] "
        f"{rule_service.describe(schedule.rule_json or {}, zone=zone)}",
        f"It is now {now.strftime('%A %-d %B %Y, %H:%M')}.",
    ]
    if due_at is not None:
        late = (datetime.now(tz=UTC) - clock.as_utc(due_at)).total_seconds()
        if late > 600:
            local = clock.as_utc(due_at).astimezone(zone)
            lines.append(
                f"This run was due at {local.strftime('%A %-d %B, %H:%M')} and is late — "
                "say so if it makes any of what follows out of date."
            )
    lines += ["", schedule.instruction or schedule.request or ""]
    return "\n".join(lines)


async def _await_reply(chat_id: str, message_id: str) -> None:
    """Wait for one generation to finish.

    Polled rather than awaited on the task itself: `generation` owns its
    registry and its tasks, and reaching into either from here would couple this
    to internals whose whole job is to be replaceable. A poll costs nothing at
    this interval and cannot deadlock.
    """
    from lembas.services import generation as generation_service

    waited = 0.0
    while waited < DELIVERY_TIMEOUT:
        running = generation_service.running_for(chat_id)
        # `running_for` already excludes a finished generation, so `None` is the
        # ordinary end of this loop. The id check is what stops us waiting on
        # somebody's *next* reply in the same chat, which would otherwise happen
        # whenever a queued turn is drained straight after ours.
        if running is None or running.message_id != message_id:
            return
        await asyncio.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    log.warning("gave up waiting for the reply to schedule message %s", message_id)


def _finished_reply(db, chat_id: str, message_id: str) -> Message | None:
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat_id:
        return None
    if not message.complete or message.error:
        return None
    return message


async def deliver(schedule_id: str, message_id: str, *, since: datetime) -> None:
    """Put a finished reply where the schedule said it should go.

    `since` is the moment the firing began, and it is what tells a report the
    model filed itself apart from one filed on a previous run.
    """
    with session_scope() as db:
        schedule = db.get(Schedule, schedule_id)
        if schedule is None:
            return
        target = schedule.target
        chat_id = schedule.chat_id

    if target == TARGET_CHAT:
        # Already where it belongs. Stated rather than left to fall through, so
        # a reader of this function does not have to infer the common case.
        return

    await _await_reply(chat_id, message_id)

    with session_scope() as db:
        schedule = db.get(Schedule, schedule_id)
        if schedule is None:
            return
        owner = db.get(User, schedule.user_id)
        if owner is None:
            return
        message = _finished_reply(db, chat_id, message_id)

        if target == TARGET_REPORT:
            # If the model filed one itself with `report_write`, that is the
            # report and this must not file a second. The tool stamps
            # `source_id` with the chat, which is what makes them the same run;
            # `since` is what makes it *this* run. Both stamps go through
            # `as_utc` because one comes from a row read back from SQLite (which
            # loses the offset) and the other is still in memory -- comparing
            # the two raises, the trap `compaction.moment` exists for.
            already = reports_service.recent(db, owner, limit=5)
            if any(
                r.source_id == chat_id and clock.as_utc(r.created_at) >= clock.as_utc(since)
                for r in already
            ):
                return
            if message is None:
                reports_service.create(
                    db,
                    owner=owner,
                    title=schedule.title or "Scheduled run",
                    body="",
                    source="schedule",
                    source_id=chat_id,
                    schedule_id=schedule.id,
                    error="The run did not produce a reply.",
                    group=schedule.data_group_id or DEFAULT_GROUP,
                )
                return
            reports_service.create(
                db,
                owner=owner,
                title=schedule.title or "Scheduled run",
                body=message.content or "",
                source="schedule",
                source_id=chat_id,
                schedule_id=schedule.id,
                model_id=message.model_id or "",
                group=schedule.data_group_id or DEFAULT_GROUP,
            )
            return


async def fire(schedule_id: str, *, due_at: datetime | None = None) -> None:
    """Run one schedule now.

    Never raises: the ticker calls this and one bad schedule must not stop the
    others. Anything that goes wrong is written to `last_error`, where the
    schedule's own page shows it — a run that failed silently is
    indistinguishable from one that was never due.
    """
    from lembas.services import settings_store

    try:
        with session_scope() as db:
            schedule = db.get(Schedule, schedule_id)
            if schedule is None:
                return
            owner = db.get(User, schedule.user_id)
            chat = db.get(Chat, schedule.chat_id) if schedule.chat_id else None
            if owner is None:
                return
            if chat is None or chat.user_id != owner.id:
                # The chat was deleted, or never belonged to this owner. Stop
                # rather than fire into nothing on every tick from now on.
                schedule.enabled = False
                schedule.last_error = "Its chat no longer exists, so it has been switched off."
                db.commit()
                return

            limit = int(settings_store.schedules(db).get("max_queued") or 3)
            zone = clock.zone_for(owner)
            content = _preamble(schedule, zone=zone, due_at=due_at)
            chat_id = chat.id
            model_id = schedule.model_id or chat.model_id
            began = datetime.now(tz=UTC)
            schedule.claimed_at = began
            schedule.last_error = ""
            db.commit()

        # Outside the session: a chat already carrying a backlog is one whose
        # replies are slower than its schedule, and adding to it makes that
        # permanently worse. `_drain` takes one queued turn per reply.
        if wake_service.queued_count(chat_id) >= limit:
            with session_scope() as db:
                schedule = db.get(Schedule, schedule_id)
                if schedule is not None:
                    schedule.last_error = (
                        "Skipped: the previous run was still going, and turns are "
                        "already waiting in its chat."
                    )
                    schedule.claimed_at = None
                    db.commit()
            return

        message_id = await wake_service.wake_chat(chat_id, content, model_id=model_id)

        with session_scope() as db:
            schedule = db.get(Schedule, schedule_id)
            if schedule is not None:
                schedule.claimed_at = None
                db.commit()

        if message_id:
            await deliver(schedule_id, message_id, since=began)
    except asyncio.CancelledError:
        raise
    except Exception:  # one bad schedule must not stop the rest
        log.exception("schedule %s failed to fire", schedule_id)
        with contextlib.suppress(Exception), session_scope() as db:
            schedule = db.get(Schedule, schedule_id)
            if schedule is not None:
                schedule.last_error = "Something went wrong running this. See the log."
                schedule.claimed_at = None
                db.commit()


async def run_now(schedule_id: str) -> None:
    """Fire a schedule because somebody pressed the button, and follow it.

    **Deliberately does not advance `next_fire_at`.** Testing a schedule must
    not consume the run it was testing -- somebody who presses this at 14:00 to
    check a 15:00 report still expects the 15:00 one. The ticker owns advancing,
    and it is the only thing that does.
    """
    await fire(schedule_id)


# The firings started by `fire_later`, held so the loop does not collect a task
# nothing else refers to; each removes itself when it ends.
_FIRINGS: set[asyncio.Task] = set()


def fire_later(schedule_id: str) -> None:
    """`run_now` without the wait: for the button, whose request must come
    back at once. The run itself is `fire`'s, unchanged."""
    task = asyncio.get_running_loop().create_task(run_now(schedule_id))
    _FIRINGS.add(task)
    task.add_done_callback(_FIRINGS.discard)
