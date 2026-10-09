"""The loop that notices a schedule is due, and claims it.

Modelled on `agent/terminal.py:_reaper_loop`, which is the only periodic task
this codebase had before now — including the blanket `except` around the sweep,
for a reason that is sharper here: **a ticker that dies on one bad row stops
every schedule on the instance, and says nothing.** Nothing else would notice.
There is no request failing, no reply erroring, no dot appearing. The reports
simply stop, and the first person to find out is whoever eventually wonders why.

Started from the lifespan rather than lazily like the reaper. Lazy is right for
terminals — a shell only exists once somebody opened one — and wrong here: a
schedule can be due at startup with nobody logged in, which is most of the point.

## Claiming, and why the order is the whole design

One worker and one loop, so the risk is not two processes racing; it is two
*overlapping sweeps*, and a firing that raises being retried every tick for ever.
Three things answer that:

1. A lock around the sweep, so a slow one (a firing awaits a model, which can
   take minutes) cannot overlap the next tick.
2. **Advance, then fire.** The row is moved on and committed *before* anything
   is awaited. A firing that dies has still consumed its slot, so the schedule
   resumes at its next occurrence with the reason on the row — rather than
   becoming a hot loop against an endpoint that is down.
3. `claimed_at` outliving a firing is what lets a run that never finished say so
   instead of looking like one that never started.

Exhaustion **disables**: a rule with nothing left returns `None`, and the row is
switched off rather than being re-examined for ever.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from lembas.db.models import Schedule, User
from lembas.db.session import session_scope
from lembas.services import settings_store
from lembas.services.schedule import clock, runner
from lembas.services.schedule import rule as rule_service

log = logging.getLogger(__name__)

_TICKER: asyncio.Task | None = None
_SWEEPING = asyncio.Lock()
# Live firings, so shutdown can wait for them rather than leaving a half-written
# reply and a `claimed_at` that never clears.
_FIRING: set[asyncio.Task] = set()

# Fallback when nothing has been configured. `settings_store.schedules` clamps
# the stored value; this is only for a sweep that runs before anything is read.
TICK_SECONDS = 30.0


def _due(now: datetime):
    return (
        select(Schedule)
        .where(
            Schedule.enabled.is_(True),
            Schedule.next_fire_at.is_not(None),
            Schedule.next_fire_at <= now,
        )
        .order_by(Schedule.next_fire_at)
    )


def claim(schedule: Schedule, *, now: datetime, zone) -> tuple[bool, datetime | None]:
    """Move one schedule on, and say whether it is owed a firing.

    Pure bookkeeping on the row: it does not fire anything and does not commit,
    so the caller decides the transaction boundary. The caller must commit
    before awaiting.
    """
    rule = schedule.rule_json or {}
    after = clock.as_utc(schedule.next_fire_at) if schedule.next_fire_at else now
    fire_now, following = rule_service.advance(
        rule,
        # A microsecond earlier, because `next_after` answers *strictly* after
        # what it is given -- so handing it the stored due moment would return
        # the one following and skip the firing that is actually owed. The
        # alternative, making `next_after` inclusive, would break the far more
        # common "give me the one after this one" call it exists for.
        after=after - timedelta(microseconds=1),
        now=now,
        zone=zone,
        fired=schedule.fired_count or 0,
    )
    if fire_now:
        schedule.fired_count = (schedule.fired_count or 0) + 1
        schedule.last_fire_at = now
    schedule.next_fire_at = following
    if following is None:
        # Nothing left to do: a spent count, a closed window, a calendar that
        # matches nothing inside the horizon. Switched off rather than left
        # enabled with a null next time, which would read as "waiting" for ever.
        schedule.enabled = False
    return fire_now, following


async def sweep(*, now: datetime | None = None) -> int:
    """One pass. Returns how many schedules were fired.

    Claims every due row and commits, then starts the firings — in that order,
    and with the commit in between, which is the property `test_schedule_ticker`
    checks by making a firing raise.
    """
    now = now or datetime.now(tz=UTC)
    to_fire: list[tuple[str, datetime]] = []

    async with _SWEEPING:
        with session_scope() as db:
            if not settings_store.schedules(db).get("enabled"):
                return 0
            # An API-only instance fires nothing. Nothing is claimed
            # either, so what came due meanwhile is still due -- and handled by
            # its own misfire rule -- once the mode is switched back.
            if settings_store.api_only(db):
                return 0
            limit = int(settings_store.schedules(db).get("max_concurrent") or 3)
            for schedule in db.scalars(_due(now)):
                try:
                    owner = db.get(User, schedule.user_id)
                    if owner is None:
                        # The account is gone; the CASCADE will take the row.
                        schedule.enabled = False
                        continue
                    due_at = clock.as_utc(schedule.next_fire_at) if schedule.next_fire_at else now
                    fire_now, _ = claim(schedule, now=now, zone=clock.zone_for(owner))
                    if fire_now:
                        to_fire.append((schedule.id, due_at))
                except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
                    log.exception("could not claim schedule %s", schedule.id)
                    with contextlib.suppress(Exception):
                        schedule.enabled = False
                        schedule.last_error = "This schedule could not be read, so it was stopped."
            # Committed before a single firing starts. This is the claim.
            db.commit()

    if not to_fire:
        return 0

    semaphore = asyncio.Semaphore(max(1, limit))

    async def _guarded(schedule_id: str, due_at: datetime) -> None:
        async with semaphore:
            await runner.fire(schedule_id, due_at=due_at)

    for schedule_id, due_at in to_fire:
        task = asyncio.create_task(_guarded(schedule_id, due_at))
        _FIRING.add(task)
        task.add_done_callback(_FIRING.discard)
    return len(to_fire)


def _interval() -> float:
    with contextlib.suppress(Exception), session_scope() as db:
        return float(settings_store.schedules(db).get("tick_seconds") or TICK_SECONDS)
    return TICK_SECONDS


# Housekeeping that rides on the ticker rather than having a loop of its own:
# one long-lived task per process is enough, and each job here is a query that
# usually finds nothing. Throttled separately from the schedule sweep, whose
# interval an administrator sets for a different reason.
TIDY_SECONDS = 60.0
_LAST_TIDY = 0.0


def tidy(*, force: bool = False) -> None:
    """Delete temporary chats whose time is up (services/chat.py:sweep_temporary),
    audit events past their retention and stale device logins.

    Synchronous and quick; called from the loop at most once a `TIDY_SECONDS`.
    Its own guard, because a failure here must never stop a schedule firing.
    """
    global _LAST_TIDY
    now = time.monotonic()
    if not force and now - _LAST_TIDY < TIDY_SECONDS:
        return
    _LAST_TIDY = now
    try:
        from lembas.services import audit, devices
        from lembas.services.chat import sweep_temporary

        with session_scope() as db:
            sweep_temporary(db)
            # Audit events past the retention setting, and device logins an
            # hour past their life. Both one indexed DELETE.
            audit.sweep(db)
            devices.sweep(db)
            # Plans whose month is up.
            from lembas.services import credits

            credits.renew_due(db)
    except Exception:  # noqa: BLE001 - housekeeping must never stop the ticker
        log.exception("the ticker's housekeeping raised")


_LAST_UPDATE_TICK = 0.0


async def update_tick(*, force: bool = False) -> None:
    """The automatic update check (services/updates_auto.py), at most once a
    minute; it decides itself how often to reach the remote."""
    global _LAST_UPDATE_TICK
    now = time.monotonic()
    if not force and now - _LAST_UPDATE_TICK < TIDY_SECONDS:
        return
    _LAST_UPDATE_TICK = now
    from lembas.services import updates_auto

    await updates_auto.tick()


async def _loop() -> None:
    while True:
        try:
            await asyncio.sleep(_interval())
            tidy()
            await update_tick()
            await sweep()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the ticker must outlive one bad sweep
            log.exception("the schedule ticker raised")


def start() -> None:
    """Begin ticking, once. Idempotent, so a second call in one process is not a
    second ticker firing everything twice."""
    global _TICKER
    if _TICKER is None or _TICKER.done():
        _TICKER = asyncio.create_task(_loop())


async def shutdown() -> None:
    global _TICKER
    if _TICKER is not None:
        _TICKER.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await _TICKER
        _TICKER = None
    for task in list(_FIRING):
        task.cancel()
    for task in list(_FIRING):
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    _FIRING.clear()


def release_claims() -> int:
    """Clear `claimed_at` on rows whose firing did not survive the last run.

    A restart abandons a reply in flight -- that is already true of every
    generation here -- so a schedule whose firing was interrupted would
    otherwise carry a claim stamp for ever and read as permanently running.
    """
    with session_scope() as db:
        stuck = list(db.scalars(select(Schedule).where(Schedule.claimed_at.is_not(None))))
        for schedule in stuck:
            schedule.claimed_at = None
            schedule.last_error = "This run was interrupted by a restart."
        if stuck:
            db.commit()
        return len(stuck)
