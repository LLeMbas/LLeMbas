"""A schedule's runs: when one may still start, when a waiting one is tried
again, what the history keeps, and one way of saying all of it.

The decisions here are pure functions of a schedule, a rule and a clock, so
the ticker test can drive them with a fake one (`tests/test_schedule_runs.py`).
The run itself -- choosing the model, the turn, following the reply, the
outcome -- is `runner.py`'s.

## The catch-up window

A run due at 09:00 that starts at 09:00:20 is on time; at 09:20 it is late but
still worth doing; at 15:00, after the host was off all morning, the moment has
passed. The window is **half the period, clamped to 2 minutes .. 2 hours** (a
schedule's own `grace_minutes` instead, when set; 2 minutes for a one-shot).
Inside it the run happens and is recorded `late`; beyond it the schedule's
`catch_up` decides: **one** catch-up run (the default) or none. Either way the
slots that passed unrun are recorded as `missed ×N`, so a gap in the history is
explained rather than invisible.

## Waiting

No free model: the run waits and is tried again 2, 5, 10 and 15 minutes after it
was due, then every 15, until its window closes -- then it is `skipped`. A
reply that never reached its model (the server down, a 5xx before a word) is
tried again 5, 15 and 30 minutes later; a 429 that said when, then. A real
failure is not retried: the same request would fail the same way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    CATCH_UP_SKIP,
    KIND_TASK,
    RUN_FAILED,
    RUN_MISSED,
    RUN_OK,
    RUN_RUNNING,
    RUN_SILENT,
    RUN_SKIPPED,
    RUN_WAITING,
    TIMING_CATCH_UP,
    TIMING_LATE,
    TIMING_ON_TIME,
    Chat,
    Schedule,
    ScheduleRun,
)
from lembas.services.schedule import clock
from lembas.services.schedule import rule as rule_service

log = logging.getLogger(__name__)

GRACE_MIN = timedelta(minutes=2)
GRACE_MAX = timedelta(hours=2)
# A run that starts within this of its slot is on time: two ticks and a margin.
ON_TIME = timedelta(seconds=120)
# How far back a sweep counts slots that passed unrun. Past it the count says
# "at least"; a schedule every minute, off for a month, is not 43 200 rows of
# arithmetic per sweep.
MISSED_CAP = 1000

# Minutes after the due moment a run waiting for a free model is tried again;
# then every `WAIT_EVERY` until its window closes.
WAIT_STEPS = (2, 5, 10, 15)
WAIT_EVERY = 15
# Minutes after a failure that never reached the model, for the 1st, 2nd and 3rd
# try again. Then it is a failure.
RETRY_STEPS = (5, 15, 30)
# A 429 that names no time is tried again after this.
RATE_LIMIT_DEFAULT = timedelta(minutes=5)
RATE_LIMIT_MAX = timedelta(hours=2)

# A run whose reply has streamed nothing for this long is stopped and failed.
# Idle time, not wall clock: a long run that is working is never cut off.
INACTIVITY_SECONDS = 600.0

# What a fresh run is told of the previous one, at most.
PREVIOUS_CHARS = 2000

# The tool families a run has unless its schedule chose: reading and looking
# things up, writing files, the plan. Never `ask` (nobody is there), never
# `schedule` (a schedule does not make schedules), never `python` (it needs a
# page). The rest -- pictures, an administrator's tools, MCP servers, helpers --
# are switched on per schedule.
SAFE_TOOLS = ("files", "recall", "search", "fetch", "memory", "skills", "plan")
OPTIONAL_TOOLS = ("image", "custom", "mcp", "subagent")
NEVER_TOOLS = ("ask", "schedule", "python")
TOOL_CHOICES = (*SAFE_TOOLS, *OPTIONAL_TOOLS)

FINISHED = (RUN_OK, RUN_SILENT, RUN_FAILED, RUN_SKIPPED, RUN_MISSED)


def tools_for(schedule: Schedule) -> tuple[str, ...]:
    chosen = [t for t in (schedule.tools_json or []) if t in TOOL_CHOICES]
    return tuple(chosen) if chosen else SAFE_TOOLS


def grace(schedule: Schedule, *, now: datetime, zone) -> timedelta:
    """How late a run of this schedule may still start (see the docstring)."""
    if schedule.grace_minutes is not None and schedule.grace_minutes > 0:
        return timedelta(minutes=min(int(schedule.grace_minutes), 7 * 24 * 60))
    gap = rule_service.period(schedule.rule_json or {}, now=now, zone=zone)
    if gap is None:
        return GRACE_MIN
    return max(GRACE_MIN, min(gap / 2, GRACE_MAX))


@dataclass(frozen=True)
class Slot:
    """What the sweep found when a schedule came due."""

    run: bool  # start a run now
    slot_at: datetime  # the slot it is for (the latest that passed)
    timing: str
    missed: int  # slots before it (or, when not run, including it) that never ran


def slot(
    schedule: Schedule, *, due: datetime, now: datetime, zone, fired: int
) -> Slot:
    """Which slot a due schedule runs for, how late, and what was missed.

    `due` is the stored due moment; `fired` the count before this one. Slots
    from `due` to `now` are counted; the latest is the one that runs.
    """
    rule = schedule.rule_json or {}
    slots = [due]
    after = due
    while len(slots) < MISSED_CAP:
        following = rule_service.next_after(rule, after, zone=zone, fired=fired + len(slots))
        if following is None or following > now:
            break
        slots.append(following)
        after = following
    latest = slots[-1]
    late_by = now - latest
    window = grace(schedule, now=now, zone=zone)
    if late_by <= window:
        timing = TIMING_ON_TIME if late_by <= ON_TIME else TIMING_LATE
        return Slot(True, latest, timing, len(slots) - 1)
    if schedule.catch_up == CATCH_UP_SKIP:
        return Slot(False, latest, TIMING_CATCH_UP, len(slots))
    return Slot(True, latest, TIMING_CATCH_UP, len(slots) - 1)


def deadline_for(schedule: Schedule, *, slot_at: datetime, now: datetime, zone) -> datetime:
    """When a run of this slot stops waiting: its window's end, and never less
    than the first wait step from now (a catch-up run gets its tries too)."""
    end = slot_at + grace(schedule, now=now, zone=zone)
    return max(end, now + timedelta(minutes=WAIT_STEPS[0]))


def next_wait(*, base: datetime, attempts: int, now: datetime) -> datetime:
    """When a run waiting for a free model is tried again, after `attempts`
    tries so far (1 after the first). Measured from `base`, the run's first
    try; never sooner than a minute from now."""
    if attempts <= len(WAIT_STEPS):
        minutes = WAIT_STEPS[max(1, attempts) - 1]
    else:
        minutes = WAIT_STEPS[-1] + WAIT_EVERY * (attempts - len(WAIT_STEPS))
    return max(base + timedelta(minutes=minutes), now + timedelta(minutes=1))


def next_retry(*, attempt: int, now: datetime) -> datetime | None:
    """When a reply that never reached its model is tried again, or None after
    the last try. `attempt` is how many retries have happened already."""
    if attempt >= len(RETRY_STEPS):
        return None
    return now + timedelta(minutes=RETRY_STEPS[attempt])


def rate_limited_until(retry_after: float | None, *, now: datetime) -> datetime:
    wait = timedelta(seconds=retry_after) if retry_after else RATE_LIMIT_DEFAULT
    return now + min(max(wait, timedelta(seconds=30)), RATE_LIMIT_MAX)


# --- The history ---------------------------------------------------------------------
def history(db: DBSession, schedule: Schedule, *, limit: int = 20, before: str = ""):
    """Runs newest first, `limit` of them, before the run `before` (a cursor)."""
    query = select(ScheduleRun).where(ScheduleRun.schedule_id == schedule.id)
    if before:
        anchor = db.get(ScheduleRun, before)
        if anchor is not None and anchor.schedule_id == schedule.id:
            query = query.where(ScheduleRun.created_at < anchor.created_at)
    rows = list(
        db.scalars(query.order_by(ScheduleRun.created_at.desc()).limit(max(1, limit) + 1))
    )
    return rows[:limit], len(rows) > limit


def latest(db: DBSession, schedule: Schedule) -> ScheduleRun | None:
    return db.scalars(
        select(ScheduleRun)
        .where(ScheduleRun.schedule_id == schedule.id)
        .order_by(ScheduleRun.created_at.desc())
        .limit(1)
    ).first()


def latest_by_schedule(db: DBSession, schedule_ids: list[str]) -> dict[str, ScheduleRun]:
    """The newest run of each of these schedules, for a list."""
    out: dict[str, ScheduleRun] = {}
    if not schedule_ids:
        return out
    for row in db.scalars(
        select(ScheduleRun)
        .where(ScheduleRun.schedule_id.in_(schedule_ids))
        .order_by(ScheduleRun.created_at.desc())
    ):
        out.setdefault(row.schedule_id, row)
    return out


def previous_result(db: DBSession, schedule: Schedule, *, before: ScheduleRun | None = None):
    """The last run that said something (`ok`), for a fresh run's context."""
    query = select(ScheduleRun).where(
        ScheduleRun.schedule_id == schedule.id, ScheduleRun.status == RUN_OK
    )
    if before is not None:
        query = query.where(ScheduleRun.id != before.id)
    return db.scalars(query.order_by(ScheduleRun.created_at.desc()).limit(1)).first()


def schedule_for_chat(db: DBSession, chat: Chat) -> Schedule | None:
    """The schedule a task chat belongs to: its current chat, or one of its
    earlier fresh runs'."""
    found = db.scalars(select(Schedule).where(Schedule.chat_id == chat.id)).first()
    if found is not None:
        return found
    run = db.scalars(select(ScheduleRun).where(ScheduleRun.chat_id == chat.id)).first()
    return db.get(Schedule, run.schedule_id) if run is not None else None


def sweep(db: DBSession, *, now: datetime | None = None) -> int:
    """Forget runs older than `runs_days`, and the chats of fresh runs among
    them that are nobody's current chat. Returns how many runs went."""
    from lembas.services import chat as chat_service
    from lembas.services import settings_store

    now = now or datetime.now(UTC)
    days = int(settings_store.schedules(db).get("runs_days") or 90)
    cutoff = now - timedelta(days=days)
    old = list(
        db.scalars(
            select(ScheduleRun).where(
                ScheduleRun.created_at < cutoff, ScheduleRun.status.in_(FINISHED)
            )
        )
    )
    if not old:
        return 0
    current = set(db.scalars(select(Schedule.chat_id)))
    later = set(
        db.scalars(select(ScheduleRun.chat_id).where(ScheduleRun.created_at >= cutoff))
    )
    chat_ids = {r.chat_id for r in old if r.chat_id} - current - later
    chats = [
        c
        for c in (db.get(Chat, cid) for cid in chat_ids)
        if c is not None and c.kind == KIND_TASK
    ]
    if chats:
        chat_service.delete_chats(db, chats)
    db.execute(delete(ScheduleRun).where(ScheduleRun.id.in_([r.id for r in old])))
    db.commit()
    return len(old)


# --- Saying it ------------------------------------------------------------------------
def _iso(moment: datetime | None) -> str | None:
    return clock.as_utc(moment).isoformat() if moment else None


def run_progress(row: ScheduleRun, now: datetime | None = None) -> dict[str, Any] | None:
    """What a run is doing, as a progress object (harness spec 3.4.0): waiting
    for a model (`waiting_for_model`, or `loading_model` while its server
    loads one), waiting to try again (`retrying`), or its reply's own phase
    while it runs. None otherwise."""
    from lembas.services import generation as generation_service
    from lembas.services import progress

    now = now or datetime.now(UTC)
    if row.status == RUN_RUNNING and row.message_id:
        running = generation_service.get(row.message_id)
        return progress.current(running) if running is not None else None
    if row.status != RUN_WAITING:
        return None
    if row.waiting_for == "model":
        waiting = dict(row.waiting_json or {})
        since = int(waiting.get("since") or now.timestamp() * 1000)
        if not waiting.get("model"):
            return None
        if waiting.get("reason") == "loading":
            return {"phase": "loading_model", "since": since, "model": waiting["model"]}
        return {"phase": "waiting_for_model", "since": since, "model": waiting["model"],
                "reason": waiting.get("reason") or "busy"}
    if row.retry_at is not None:
        left = max(0, int((clock.as_utc(row.retry_at) - now).total_seconds()))
        reason = (row.error or "").strip().split("\n")[0][:300] or row.waiting_for
        out = {"phase": "retrying", "since": int(now.timestamp() * 1000), "reason": reason,
               "in": left}
        if row.attempts:
            out["attempt"] = int(row.attempts)
        return out
    return None


def run_dict(row: ScheduleRun) -> dict[str, Any]:
    """One run as the API sends it and the page reads it. `preview` and `error`
    are model output or a server's words: escaped wherever shown."""
    return {
        "id": row.id,
        "schedule_id": row.schedule_id,
        "status": row.status,
        "timing": row.timing,
        "missed": int(row.missed or 0),
        "due_at": _iso(row.due_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "waiting_for": row.waiting_for or None,
        "retry_at": _iso(row.retry_at) if row.status == RUN_WAITING else None,
        "progress": run_progress(row),
        "attempts": int(row.attempts or 0),
        "model": {
            "id": row.model_id or None,
            "name": row.model_label or row.model_id or None,
            "reason": row.model_reason or None,
            "switched_from": row.switched_from or None,
        },
        "tokens": {
            "prompt": int(row.prompt_tokens or 0),
            "completion": int(row.completion_tokens or 0),
        },
        "cost": (
            {"micro": int(row.cost_micro or 0), "currency": row.currency}
            if row.currency
            else None
        ),
        "chat_id": row.chat_id or None,
        "message_id": row.message_id or None,
        "report_path": row.report_path or None,
        "preview": row.preview or "",
        "error": {"class": row.error_class, "text": row.error} if row.error_class else None,
        "notified": bool(row.notified),
        # What the notify decision told (`notify.notification`), or null:
        # an app following the `schedules` event shows it as it would a push.
        "notification": dict(row.notification_json) if row.notification_json else None,
    }


def schedule_dict(
    db: DBSession, schedule: Schedule, *, owner, last: ScheduleRun | None = None,
    upcoming: int = 5,
) -> dict[str, Any]:
    """One schedule as the API sends it and the list reads it."""
    zone = clock.zone_for(owner)
    now = datetime.now(UTC)
    nexts = (
        rule_service.upcoming(
            schedule.rule_json or {},
            now=clock.as_utc(schedule.next_fire_at) - timedelta(microseconds=1)
            if schedule.next_fire_at
            else now,
            zone=zone,
            fired=schedule.fired_count or 0,
            count=upcoming,
        )
        if schedule.enabled and schedule.next_fire_at
        else []
    )
    from lembas.services.schedule import notify

    return {
        "id": schedule.id,
        "title": schedule.title,
        "instruction": schedule.instruction,
        "request": schedule.request,
        "rule": schedule.rule_json or {},
        "description": rule_service.describe(schedule.rule_json or {}, zone=zone),
        "timezone": clock.name_for(owner) or str(clock.server_zone()),
        "enabled": bool(schedule.enabled),
        "next_run": _iso(schedule.next_fire_at) if schedule.enabled else None,
        "next_runs": [_iso(m) for m in nexts],
        "target": schedule.target,
        "mode": schedule.mode,
        "notify": notify.policy(schedule.notify, schedule.rule_json or {}),
        "catch_up": schedule.catch_up,
        "grace_minutes": schedule.grace_minutes,
        "model": schedule.model_id or None,
        "effort": schedule.effort or None,
        "agents": schedule.agents,
        "tools": list(tools_for(schedule)),
        "origin": schedule.origin,
        "chat_id": schedule.chat_id or None,
        "fired_count": int(schedule.fired_count or 0),
        "failure_streak": int(schedule.failure_streak or 0),
        "last_error": schedule.last_error or None,
        "last_run": run_dict(last) if last is not None else None,
        "running": bool(last is not None and last.status == RUN_RUNNING),
        "created_at": _iso(schedule.created_at),
    }


__all__ = [
    "INACTIVITY_SECONDS",
    "OPTIONAL_TOOLS",
    "RETRY_STEPS",
    "SAFE_TOOLS",
    "TOOL_CHOICES",
    "WAIT_STEPS",
    "Slot",
    "deadline_for",
    "grace",
    "history",
    "latest",
    "latest_by_schedule",
    "next_retry",
    "next_wait",
    "previous_result",
    "rate_limited_until",
    "run_dict",
    "schedule_dict",
    "schedule_for_chat",
    "slot",
    "sweep",
    "tools_for",
]
