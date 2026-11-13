"""What happens when a schedule fires: one run, from choosing its model to
telling somebody about it.

Every schedule fires the same way -- a turn into a chat, answered by the
ordinary generation loop -- and the run is recorded as a `ScheduleRun` row from
the moment it is due to the moment its outcome is known (`runs.py` has the
window and waiting arithmetic, `models.py` the free-model choice, `notify.py`
the notification). In order:

1. **A model**: the schedule's first choice, else the first free model of the
   scheduling list. None free: the run is `waiting` and the ticker tries it
   again (`runs.next_wait`) until its window closes, then it is `skipped`.
2. **A chat**: `fresh` (the default) gives each run a chat of its own, listed
   under the schedule, and tells it what the previous run found; `continue`
   puts every run into the one chat that keeps growing. The run's settings --
   model, effort, team size, tools -- go onto that chat, so the reply is an
   ordinary one and `generation` needs to know nothing about schedules.
3. **The turn**: what the schedule asks, framed as a scheduled event in words
   (the role stays `user`), with the time, how late it is, and when it is
   to file a report (`services/reports.py`, harness spec 3.8.0).
4. **Following** the reply until it is done, or until nothing has streamed for
   `INACTIVITY_SECONDS` (idle time, not wall clock: a long run that is working
   is never cut off).
5. **The outcome**: `ok`, `silent` or `failed` -- or `waiting` again, when the
   model was never reached and a retry is safe. A report is filed where the
   schedule says. Then the notification, by the schedule's policy.

**On a linked computer** (harness spec 3.5.0, `device.py`) step 1 is the
device instead of a model: away, the run waits for it on the same steps and is
`missed` when its window closes; one that does not accept scheduled work
fails the run. The chat is an agent chat there, in the schedule's folder and
mode, and the turn says `scheduled` on every request of its session. A watch
command is run on that device first (`_look`).

`fire` never raises: the ticker calls it and one bad schedule must not stop the
others. Whatever goes wrong is written on the run and on the schedule.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import func, select

from lembas.db.models import (
    KIND_AGENT,
    KIND_TASK,
    MODE_CONTINUE,
    RUN_FAILED,
    RUN_MISSED,
    RUN_OK,
    RUN_RUNNING,
    RUN_SKIPPED,
    RUN_UNCHANGED,
    RUN_WAITING,
    TARGET_CHAT,
    TARGET_REPORT,
    TIMING_MANUAL,
    TIMING_ON_TIME,
    WAIT_MODEL,
    WAIT_RATE_LIMIT,
    WAIT_RETRY,
    Chat,
    Message,
    Schedule,
    ScheduleRun,
    User,
)
from lembas.db.session import session_scope
from lembas.services import html_pages
from lembas.services import reports as reports_service
from lembas.services import wake as wake_service
from lembas.services.schedule import clock, device, monitor, notify, runs
from lembas.services.schedule import models as models_service
from lembas.services.schedule import rule as rule_service

log = logging.getLogger(__name__)

# How often the follower looks at a reply. Coarse on purpose: nothing is
# watching this, and a result arriving three seconds late costs nobody anything.
POLL_SECONDS = 3.0

# Error classes, on the run (`ScheduleRun.error_class`).
ERROR_UNREACHABLE = "unreachable"
ERROR_RATE_LIMIT = "rate_limit"
ERROR_TIMEOUT = "timeout"
ERROR_MODEL = "model"
ERROR_INTERNAL = "internal"
ERROR_NO_MODEL = "no_model"
ERROR_BACKLOG = "backlog"
ERROR_NO_CHAT = "no_chat"
# A monitor schedule could not look at what it watches.
ERROR_WATCH = "watch"


def _now() -> datetime:
    """The wall clock. A function so the ticker test can move it."""
    return datetime.now(UTC)


def _monotonic() -> float:
    return time.monotonic()


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


# --- The turn ---------------------------------------------------------------------
def _preamble(
    schedule: Schedule,
    *,
    zone,
    due_at: datetime | None,
    previous: ScheduleRun | None = None,
    previous_text: str = "",
    watched: str = "",
) -> str:
    """The turn a firing puts into the chat.

    Names itself a scheduled event in *words*, because the role stays `user` --
    `_inject` sends a queued turn verbatim and `build_messages` must keep seeing
    a user turn. The framing therefore cannot live in the role.

    The scheduled time is stated as well as the actual one, so a run caught up
    after an outage can say so rather than reporting stale news as current.
    """
    now = _now().astimezone(zone)
    lines = [
        "This turn was started by a schedule, not by the person — "
        "they are not necessarily at the keyboard.",
        "",
        f"[schedule: {schedule.title or 'untitled'}] "
        f"{rule_service.describe(schedule.rule_json or {}, zone=zone)}",
        f"It is now {now.strftime('%A %-d %B %Y, %H:%M')}.",
    ]
    if due_at is not None:
        late = (_now() - clock.as_utc(due_at)).total_seconds()
        if late > 600:
            local = clock.as_utc(due_at).astimezone(zone)
            lines.append(
                f"This run was due at {local.strftime('%A %-d %B, %H:%M')} and is late — "
                "say so if it makes any of what follows out of date."
            )
    lines.append(notify.NO_RECURSION)
    if previous is not None and previous_text.strip():
        when = clock.as_utc(previous.finished_at or previous.created_at).astimezone(zone)
        text = previous_text.strip()
        if len(text) > runs.PREVIOUS_CHARS:
            text = text[: runs.PREVIOUS_CHARS].rstrip() + " …"
        lines += [
            "",
            f"Last time ({when.strftime('%A %-d %B, %H:%M')}) this schedule's run said:",
            "<previous-run>",
            text,
            "</previous-run>",
            "Do not repeat what is already there unless it changed.",
        ]
    if watched:
        lines += ["", watched]
    lines += ["", schedule.instruction or schedule.request or ""]
    # When a report is wanted, in the spec's words (harness spec 3.8.0,
    # reports.md §3) -- after the schedule's instruction and the run's time,
    # in this turn and not in the system prompt, which stays the same bytes.
    # `[SILENT]` is no longer told to any model.
    reporting = reports_service.instruction(
        notify.policy(schedule.notify, schedule.rule_json or {}), schedule.notify_when
    )
    if reporting:
        lines += ["", reporting]
    return "\n".join(lines)


# --- The chat -----------------------------------------------------------------------
def _has_messages(db, chat_id: str) -> bool:
    return bool(
        db.scalar(select(func.count()).select_from(Message).where(Message.chat_id == chat_id))
    )


def _run_chat(db, schedule: Schedule, owner: User, model) -> Chat | None:
    """The chat this run goes into, made when it is a fresh run's. None when a
    `continue` schedule's chat is gone (the caller switches it off)."""
    home = db.get(Chat, schedule.chat_id) if schedule.chat_id else None
    if home is not None and home.user_id != owner.id:
        home = None
    if schedule.device_token_id:
        return _device_chat(db, schedule, owner, home)
    if schedule.mode == MODE_CONTINUE:
        return home
    if home is not None and not _has_messages(db, home.id):
        return home
    zone = clock.zone_for(owner)
    stamp = _now().astimezone(zone).strftime("%-d %b %H:%M")
    chat = Chat(
        user_id=owner.id,
        kind=KIND_TASK,
        title=f"{schedule.title or 'Scheduled task'} · {stamp}"[:200],
        model_id=model.model_id if model is not None else schedule.model_id or "",
        connection_id=model.connection_id if model is not None else schedule.connection_id,
        data_group_id=schedule.data_group_id,
        unattended=True,
    )
    db.add(chat)
    db.flush()
    # The schedule's chat is its newest run's: the links, the strip and a
    # `continue` switch all land on the latest.
    schedule.chat_id = chat.id
    return chat


def _device_chat(db, schedule: Schedule, owner: User, home: Chat | None) -> Chat:
    """The agent chat a run on a linked computer goes into: the schedule's
    own when it continues there (same computer, same folder) or is still
    empty, else a new one, which becomes the schedule's."""
    from lembas.services import chat as chat_service

    where = device.runs_on_of(schedule)
    same_place = (
        home is not None
        and home.kind == KIND_AGENT
        and home.device_token_id == where.device
        and (home.project_dir or "") == where.cwd
    )
    if same_place and (schedule.mode == MODE_CONTINUE or not _has_messages(db, home.id)):
        home.agent_mode = where.mode
        _device_effort(home, schedule)
        return home
    model_id = schedule.model_id or ""
    if not model_id:
        start = chat_service.default_model(db, owner)
        model_id = start[0] if start else ""
    zone = clock.zone_for(owner)
    stamp = _now().astimezone(zone).strftime("%-d %b %H:%M")
    chat = Chat(
        user_id=owner.id,
        kind=KIND_AGENT,
        title=f"{schedule.title or 'Scheduled task'} · {stamp}"[:200],
        model_id=model_id,
        connection_id=schedule.connection_id if schedule.model_id else None,
        data_group_id=schedule.data_group_id,
        device_token_id=where.device,
        project_dir=where.cwd,
        agent_mode=where.mode,
        # Its title is the schedule's and the run's time, not a model's guess.
        title_generated=True,
        # The schedule's, not one of the person's agent chats: out of the
        # Agents list, the unread dots, search and the "reply is ready" push.
        scheduled=True,
    )
    db.add(chat)
    db.flush()
    _device_effort(chat, schedule)
    schedule.chat_id = chat.id
    return chat


def _device_effort(chat: Chat, schedule: Schedule) -> None:
    """The schedule's effort on its device chat (sent as `session/new`'s
    `effort`); no model is chosen here, so the device judges the level."""
    params = dict(chat.params_json or {})
    if schedule.effort:
        params["reasoning_effort"] = schedule.effort
    else:
        params.pop("reasoning_effort", None)
    chat.params_json = params


def _apply_settings(db, chat: Chat, schedule: Schedule, model) -> None:
    """The run's settings onto its chat: model, effort, team size, tools."""
    from lembas.services import chat as chat_service
    from lembas.services.tools import FAMILIES

    if model is not None:
        chat.model_id = model.model_id
        chat.connection_id = model.connection_id
        params = dict(chat.params_json or {})
        level = schedule.effort or ""
        offered = chat_service.levels_for(model).offered
        if level and level in offered:
            params["reasoning_effort"] = level
        else:
            params.pop("reasoning_effort", None)
        chat.params_json = params
    if schedule.agents and schedule.agents >= 2:
        chat.agents_min = 1
        chat.agents_max = int(schedule.agents)
    else:
        chat.agents_min = None
        chat.agents_max = None
    allowed = set(runs.tools_for(schedule))
    families = {
        family: (family in allowed)
        for family in (*FAMILIES, "custom", "mcp")
        if family not in ("schedule", "ask", "python")
    }
    for family in runs.NEVER_TOOLS:
        families[family] = False
    # Off is stored as False; on as absent (the chat's own "on" would override
    # the person's per-model default, which a run has no business doing).
    scope = dict(chat.scope_json or {})
    scope["families"] = {k: False for k, on in families.items() if not on}
    chat.scope_json = scope


# --- One run ---------------------------------------------------------------------------
def _finish(run: ScheduleRun, status: str, *, error_class: str = "", error: str = "") -> None:
    run.status = status
    run.finished_at = _now()
    run.retry_at = None
    run.waiting_for = ""
    run.error_class = error_class
    run.error = (error or "")[:2000]


def _new_run(db, schedule: Schedule, *, due_at: datetime | None, timing: str) -> ScheduleRun:
    owner = db.get(User, schedule.user_id)
    zone = clock.zone_for(owner)
    now = _now()
    due = clock.as_utc(due_at) if due_at else now
    run = ScheduleRun(
        schedule_id=schedule.id,
        user_id=schedule.user_id,
        due_at=due,
        timing=timing,
        status=RUN_RUNNING,
        deadline=runs.deadline_for(schedule, slot_at=due, now=now, zone=zone),
    )
    db.add(run)
    db.flush()
    return run


async def fire(
    schedule_id: str,
    *,
    due_at: datetime | None = None,
    run_id: str | None = None,
    timing: str | None = None,
) -> str:
    """Run one schedule now (or take up a waiting run again). Returns the run's
    id, or "" when there was nothing to run. Never raises."""
    try:
        with session_scope() as db:
            schedule = db.get(Schedule, schedule_id)
            if schedule is None:
                return ""
            if run_id is None:
                run = _new_run(
                    db,
                    schedule,
                    due_at=due_at,
                    timing=timing or (TIMING_ON_TIME if due_at else TIMING_MANUAL),
                )
                db.commit()
                run_id = run.id
        await attempt(run_id)
        return run_id
    except asyncio.CancelledError:
        raise
    except Exception:  # one bad schedule must not stop the rest
        log.exception("schedule %s failed to fire", schedule_id)
        with contextlib.suppress(Exception), session_scope() as db:
            schedule = db.get(Schedule, schedule_id)
            if schedule is not None:
                schedule.last_error = "Something went wrong running this. See the log."
                schedule.claimed_at = None
            run = db.get(ScheduleRun, run_id) if run_id else None
            if run is not None and run.status in (RUN_RUNNING, RUN_WAITING):
                _finish(run, RUN_FAILED, error_class=ERROR_INTERNAL,
                        error="Something went wrong running this.")
            db.commit()
        return run_id or ""


async def attempt(run_id: str) -> None:
    """One try of a run: choose a model, put the turn in, follow the reply."""
    from lembas.services import generation as generation_service

    choice: models_service.Choice | None = None
    if not await _look(run_id):
        return
    with session_scope() as db:
        run = db.get(ScheduleRun, run_id)
        if run is None or run.status not in (RUN_RUNNING, RUN_WAITING):
            return
        schedule = db.get(Schedule, run.schedule_id)
        owner = db.get(User, run.user_id)
        if schedule is None or owner is None:
            return
        now = _now()
        run.attempts = (run.attempts or 0) + 1
        run.first_try_at = run.first_try_at or now

        on_device = bool(schedule.device_token_id)
        if on_device and not _device_ready(db, schedule, owner, run, now=now):
            return
        order = [] if on_device else models_service.candidates(db, owner, schedule)
        if order:
            choice = await models_service.choose(db, owner, schedule)
            if choice.model is None:
                _wait_for_model(db, schedule, owner, run, choice, now=now)
                return
        model = choice.model if choice is not None else None
        try:
            prepared = _prepare(db, schedule, owner, run, choice, model, now=now)
        except BaseException:
            if choice is not None:
                models_service.release(choice)
            raise
        if prepared is None:
            if choice is not None:
                models_service.release(choice)
            return
        restart, chat_id, content, limit = prepared
        model_id = model.model_id if model is not None else ""
        connection_id = model.connection_id if model is not None else None
        if on_device:
            # Said on every request of the session while the run lasts.
            device.TURNS[chat_id] = device.scheduled_meta(schedule, run)

    try:
        if restart is not None:
            generation_service.restart(*restart)
            message_id = restart[1]
        else:
            # A chat already carrying a backlog is one whose replies are slower
            # than its schedule; adding to it makes that permanently worse.
            if wake_service.queued_count(chat_id) >= limit:
                with session_scope() as db:
                    run = db.get(ScheduleRun, run_id)
                    schedule = db.get(Schedule, run.schedule_id) if run else None
                    if run is not None and schedule is not None:
                        schedule.last_error = (
                            "Skipped: the previous run was still going, and turns are "
                            "already waiting in its chat."
                        )
                        schedule.claimed_at = None
                        _finish(run, RUN_SKIPPED, error_class=ERROR_BACKLOG,
                                error=schedule.last_error)
                        db.commit()
                device.TURNS.pop(chat_id, None)
                return
            message_id = await wake_service.wake_chat(
                chat_id, content, model_id=model_id, connection_id=connection_id
            )
    finally:
        if choice is not None:
            models_service.release(choice)
    if not message_id:
        device.TURNS.pop(chat_id, None)

    with session_scope() as db:
        run = db.get(ScheduleRun, run_id)
        if run is None:
            return
        run.message_id = message_id or ""
        schedule = db.get(Schedule, run.schedule_id)
        if not message_id and schedule is not None:
            schedule.claimed_at = None
            if run.status == RUN_RUNNING:
                _finish(run, RUN_SKIPPED, error_class=ERROR_BACKLOG,
                        error="The turn was queued behind a reply still being written.")
        db.commit()

    if message_id:
        await follow(run_id)


async def _look(run_id: str) -> bool:
    """A monitor schedule's look, once per run (`monitor.py`): True when the
    run goes on -- it watches nothing, it already looked, or what it watches
    changed -- and False when the run ended here, `unchanged` or failed."""
    from lembas.services import settings_store

    with session_scope() as db:
        run = db.get(ScheduleRun, run_id)
        schedule = db.get(Schedule, run.schedule_id) if run else None
        if run is None or schedule is None or run.status not in (RUN_RUNNING, RUN_WAITING):
            return True
        if not schedule.watch_kind or run.watch_json:
            return True
        if schedule.watch_kind == monitor.KIND_COMMAND:
            # On the schedule's computer (spec 3.5.0): away, the run waits
            # for it; not accepting, it fails.
            owner = db.get(User, run.user_id)
            run.attempts = (run.attempts or 0) + 1
            run.first_try_at = run.first_try_at or _now()
            if not _device_ready(db, schedule, owner, run, now=_now()):
                return False
            run.attempts -= 1  # the attempt proper counts itself
            outcome = await _look_on_device(schedule, run)
        else:
            allow_private = bool(settings_store.search(db).get("allow_private_fetch"))
            # Looked at outside a held write: the session stays open (the
            # schedule's state is kept on it), but the fetch is bounded in time.
            outcome = await monitor.check(schedule, allow_private=allow_private)
        record = dict(outcome.record)
        if outcome.block:
            record["block"] = outcome.block
        run.watch_json = record
        if outcome.ok and outcome.changed:
            db.commit()
            return True
        schedule.claimed_at = None
        if outcome.ok:
            _finish(run, RUN_UNCHANGED)
            schedule.failure_streak = 0
            schedule.last_error = ""
            db.commit()
            return False
        _finish(run, RUN_FAILED, error_class=ERROR_WATCH,
                error=f"Could not look at what it watches: {outcome.error}"[:2000])
        schedule.failure_streak = int(schedule.failure_streak or 0) + 1
        schedule.last_error = run.error[:500]
        db.commit()
        schedule_id = schedule.id
    _notify_failure_later(schedule_id, run_id)
    return False


async def _look_on_device(schedule, run) -> monitor.Outcome:
    """`_lembas/monitor` on the schedule's computer: its hash against the
    one kept, as `monitor.check` does for an address. Never raises."""
    from lembas.services import device_link

    link = device.linked(schedule.device_token_id)
    kind = monitor.KIND_COMMAND
    try:
        if link is None:
            raise device_link.LinkClosed("the computer is not linked")
        answer = await device.look(schedule, run, link=link)
    except device_link.DeviceError as exc:
        error = f"the computer refused the watch command: {exc}"
        return monitor.Outcome(False, error=error, record={"kind": kind, "error": error})
    except (device_link.LinkClosed, TimeoutError) as exc:
        error = f"the computer did not answer the watch command ({exc or 'timed out'})"
        return monitor.Outcome(False, error=error, record={"kind": kind, "error": error})
    digest = str(answer.get("hash") or "")
    if not digest:
        error = "the watch command timed out"
        return monitor.Outcome(False, error=error, record={"kind": kind, "error": error})
    return monitor.compare_command(schedule, answer)


def _prepare(db, schedule, owner, run, choice, model, *, now: datetime):
    """Everything up to the turn, in the run's session: the chat (or, for a
    safe retry, the reply blanked for `restart`), the settings and the words.
    Returns `(restart, chat_id, content, max_queued)`, or None when the run
    ended here (its chat is gone). Commits."""
    from lembas.services import settings_store

    restart: tuple[str, str] | None = None
    retrying = bool(run.message_id) and run.waiting_for in (WAIT_RETRY, WAIT_RATE_LIMIT)
    if retrying:
        chat = db.get(Chat, run.chat_id)
        message = db.get(Message, run.message_id)
        if chat is None or message is None:
            _finish(run, RUN_FAILED, error_class=ERROR_NO_CHAT,
                    error="The run's chat no longer exists.")
            db.commit()
            return None
        if model is not None:
            _apply_settings(db, chat, schedule, model)
            message.model_id = model.model_id
            message.connection_id = model.connection_id
        message.content = ""
        message.error = ""
        message.reasoning = ""
        message.complete = False
        restart = (chat.id, message.id)
    else:
        chat = _run_chat(db, schedule, owner, model)
        if chat is None:
            schedule.enabled = False
            schedule.last_error = "Its chat no longer exists, so it has been switched off."
            _finish(run, RUN_FAILED, error_class=ERROR_NO_CHAT, error=schedule.last_error)
            db.commit()
            return None
        _apply_settings(db, chat, schedule, model)

    _record_model(run, choice, model)
    run.status = RUN_RUNNING
    run.waiting_for = ""
    run.retry_at = None
    run.started_at = run.started_at or now
    run.chat_id = chat.id
    schedule.claimed_at = now
    schedule.last_error = ""
    zone = clock.zone_for(owner)
    limit = int(settings_store.schedules(db).get("max_queued") or 3)
    previous = None
    previous_text = ""
    if schedule.mode != MODE_CONTINUE:
        previous = runs.previous_result(db, schedule, before=run)
        if previous is not None and previous.report_id:
            from lembas.db.models import Report

            left = db.get(Report, previous.report_id)
            previous_text = html_pages.project(left.body) if left is not None else ""
        if previous is not None and not previous_text and previous.message_id:
            said = db.get(Message, previous.message_id)
            previous_text = notify.strip_sentinel(said.content) if said is not None else ""
    content = _preamble(
        schedule,
        zone=zone,
        due_at=run.due_at if run.timing != TIMING_MANUAL else None,
        previous=previous,
        previous_text=previous_text,
        watched=str((run.watch_json or {}).get("block") or ""),
    )
    db.commit()
    return restart, chat.id, content, limit


def _record_model(run: ScheduleRun, choice, model) -> None:
    if model is None:
        return
    run.model_id = model.model_id
    run.connection_id = model.connection_id
    run.model_label = model.label
    run.model_reason = choice.reason if choice is not None else ""
    run.switched_from = choice.switched_from if choice is not None else ""


def _wait_for_model(db, schedule, owner, run, choice, *, now: datetime) -> None:
    """No free model: wait, or -- past the window -- skip."""
    base = clock.as_utc(run.first_try_at or now)
    retry = runs.next_wait(base=base, attempts=run.attempts, now=now)
    deadline = clock.as_utc(run.deadline) if run.deadline else now
    why = choice.why_waiting or "no model was free"
    if retry > deadline:
        _finish(run, RUN_SKIPPED, error_class=ERROR_NO_MODEL,
                error=f"No model was free before the run's time passed ({why}).")
        schedule.claimed_at = None
        db.commit()
        _notify_failure_later(schedule.id, run.id)
        return
    run.status = RUN_WAITING
    run.waiting_for = WAIT_MODEL
    run.waiting_json = {**choice.waiting_on, "since": int(base.timestamp() * 1000)}
    run.retry_at = retry
    run.error_class = ERROR_NO_MODEL
    run.error = f"Waiting for a free model: {why}."[:2000]
    db.commit()


def _device_ready(db, schedule, owner, run, *, now: datetime) -> bool:
    """Whether the run's computer can take it now. Away: the run waits for it
    (or is missed); linked but not accepting, or not allowed: the run fails,
    not retried -- it is somebody's setting. Commits when it says no."""
    from lembas.services import device_link

    link = device.linked(schedule.device_token_id)
    if link is None:
        _wait_for_device(db, schedule, run, now=now)
        return False
    refused = device_link.refusal(db, owner, schedule.device_token_id) or device.refusal_of(link)
    if refused:
        _fail_on_device(db, schedule, run, refused)
        return False
    return True


def _fail_on_device(db, schedule, run, error: str) -> None:
    _finish(run, RUN_FAILED, error_class=device.ERROR_DEVICE, error=error)
    schedule.claimed_at = None
    schedule.failure_streak = int(schedule.failure_streak or 0) + 1
    schedule.last_error = run.error[:500]
    db.commit()
    _notify_failure_later(schedule.id, run.id)


def _wait_for_device(db, schedule, run, *, now: datetime) -> None:
    """The computer is away: wait on the steps a busy model is waited for,
    and -- past the run's window -- record it `missed` (spec 3.5.0)."""
    base = clock.as_utc(run.first_try_at or now)
    retry = runs.next_wait(base=base, attempts=run.attempts, now=now)
    deadline = clock.as_utc(run.deadline) if run.deadline else now
    name = device.device_name(db, schedule.device_token_id) or "The computer"
    if retry > deadline:
        _finish(run, RUN_MISSED, error_class=device.ERROR_DEVICE_AWAY,
                error=f"{name} was not linked before the run's time passed.")
        run.missed = max(1, int(run.missed or 0))
        schedule.claimed_at = None
        db.commit()
        _notify_failure_later(schedule.id, run.id)
        return
    run.status = RUN_WAITING
    run.waiting_for = device.WAIT_DEVICE
    run.waiting_json = {"device": name, "since": int(base.timestamp() * 1000)}
    run.retry_at = retry
    run.error_class = device.ERROR_DEVICE_AWAY
    run.error = f"Waiting for {name} to be linked."[:2000]
    db.commit()


# --- Following the reply ------------------------------------------------------------------
async def follow(run_id: str) -> None:
    """Wait for a run's reply, then settle the run."""
    from lembas.services import generation as generation_service

    with session_scope() as db:
        run = db.get(ScheduleRun, run_id)
        if run is None or not run.message_id:
            return
        chat_id, message_id = run.chat_id, run.message_id

    seen = -1
    quiet_since = _monotonic()
    timed_out = False
    while True:
        generation = generation_service.get(message_id)
        if generation is not None and generation.done:
            break
        if generation is None:
            # Not in the registry: finished long enough ago to be pruned, or a
            # reply the last restart cut off and `resume_interrupted` has not
            # taken up yet. The row says which.
            with session_scope() as db:
                message = db.get(Message, message_id)
                if message is None or message.complete:
                    break
            version = -2
        else:
            version = generation.version
        if version != seen or (generation is not None and getattr(generation, "waiting", None)):
            # A card waiting for the person (a scheduled turn's approval, up
            # to the schedule's wait) is not a reply gone quiet.
            seen = version
            quiet_since = _monotonic()
        elif _monotonic() - quiet_since > runs.INACTIVITY_SECONDS:
            timed_out = True
            generation_service.request_stop(message_id)
            break
        await _sleep(POLL_SECONDS)

    if timed_out:
        # The stop is asked for; give the reply a moment to write itself down.
        for _ in range(10):
            generation = generation_service.get(message_id)
            if generation is None or generation.done:
                break
            await _sleep(1.0)
    await settle(run_id, generation_service.get(message_id), timed_out=timed_out,
                 chat_id=chat_id)


async def settle(run_id: str, generation, *, timed_out: bool = False, chat_id: str = "") -> None:
    """A run's reply is done (or given up on): record the outcome, file the
    report, tell whoever should be told, or arrange a safe retry."""
    from lembas.services import metrics as metrics_service

    notify_result = False
    failure: tuple[str, str] | None = None
    with session_scope() as db:
        run = db.get(ScheduleRun, run_id)
        if run is None:
            return
        schedule = db.get(Schedule, run.schedule_id)
        owner = db.get(User, run.user_id)
        if schedule is None or owner is None:
            return
        schedule.claimed_at = None
        message = db.get(Message, run.message_id) if run.message_id else None
        now = _now()
        if message is not None:
            usage = metrics_service.from_message(message.usage_json)
            run.prompt_tokens = int(usage.prompt_tokens or 0)
            run.completion_tokens = int(usage.completion_tokens or 0)
            if message.currency:
                run.cost_micro = int(message.cost_micro or 0)
                run.currency = message.currency

        on_device = bool(schedule.device_token_id) and message is not None
        if on_device:
            device.TURNS.pop(message.chat_id, None)
        said = getattr(generation, "scheduled_outcome", None) or {}
        reached_device = message is not None and bool(
            (message.content or "").strip() or (message.tool_calls_json or [])
        )
        if on_device and getattr(generation, "device_away", False) and not reached_device:
            # Away when the turn was sent: wait for it like a busy model. The
            # empty reply is dropped; the next try writes a new one.
            db.delete(message)
            run.message_id = ""
            _wait_for_device(db, schedule, run, now=now)
            return
        if timed_out:
            failure = (
                ERROR_TIMEOUT,
                f"Nothing was written for {int(runs.INACTIVITY_SECONDS // 60)} minutes, "
                "so the run was stopped.",
            )
        elif message is None:
            failure = (ERROR_INTERNAL, "The run's reply no longer exists.")
        elif on_device and said.get("status") in ("failed", "cancelled"):
            # The device's own word on its turn (spec 3.5.0).
            text = str(said.get("error") or "").strip()
            if said.get("status") == "cancelled":
                text = text or "The run was stopped on the computer."
            failure = (device.ERROR_DEVICE, text or device.text("no_reply"))
        elif on_device and message.error and not reached_device:
            # Refused by the device (-32602: not accepting, a folder it does
            # not allow, a permission): not retried.
            failure = (device.ERROR_DEVICE, message.error)
        elif message.error or not message.complete:
            reached = bool(
                (message.content or "").strip()
                or (message.reasoning or "").strip()
                or (message.tool_calls_json or [])
            )
            status = getattr(generation, "error_status", None)
            unreachable = bool(getattr(generation, "error_unreachable", False))
            if not reached and status == 429:
                run.status = RUN_WAITING
                run.waiting_for = WAIT_RATE_LIMIT
                run.retry_at = runs.rate_limited_until(
                    getattr(generation, "error_retry_after", None), now=now
                )
                run.error_class = ERROR_RATE_LIMIT
                run.error = (message.error or "Rate limited.")[:2000]
                db.commit()
                return
            if not reached and (unreachable or (status is not None and status >= 500)):
                again = runs.next_retry(attempt=int(run.retries or 0), now=now)
                if again is not None:
                    run.retries = int(run.retries or 0) + 1
                    run.status = RUN_WAITING
                    run.waiting_for = WAIT_RETRY
                    run.retry_at = again
                    run.error_class = ERROR_UNREACHABLE
                    run.error = (message.error or "The model's server was not reached.")[:2000]
                    db.commit()
                    return
                failure = (
                    ERROR_UNREACHABLE, message.error or "The model's server was not reached."
                )
            else:
                failure = (ERROR_MODEL, message.error or "The reply did not finish.")
        else:
            text = message.content or ""
            run.preview = notify.preview(text)
            # Never `silent` under spec 3.8.0: a run is ok, with a report or
            # without one.
            _finish(run, RUN_OK)
            schedule.failure_streak = 0
            schedule.last_error = ""
            report = _leave(db, schedule, owner, run, text, said=said, now=now)
            notify_result = report is not None
            db.commit()

        if failure is not None:
            error_class, text = failure
            _finish(run, RUN_FAILED, error_class=error_class, error=text)
            schedule.failure_streak = int(schedule.failure_streak or 0) + 1
            schedule.last_error = text[:500]
            # A report already written is kept: what the model found is not
            # thrown away with the error. It is told like any report.
            notify_result = bool(run.report_id)
            db.commit()

    if notify_result:
        await _announce_report(run_id)
    if failure is not None:
        await _announce_failure(run_id)


def _leave(db, schedule, owner, run, answer: str, *, said: dict, now: datetime):
    """What a finished run leaves (harness spec 3.8.0, reports.md §4): its
    report -- the one `report_write` filed, a device's with its answer, or
    under `always` the final answer itself -- or none, and its line in the
    schedule's history. Returns the report or None; the caller commits."""
    from lembas.db.models import Report

    policy = notify.policy(schedule.notify, schedule.rule_json or {})
    calls: list[dict] = []
    filed = db.get(Report, run.report_id) if run.report_id else None
    if filed is not None:
        calls = [{"title": filed.title, "body": filed.body, "summary": filed.summary,
                  "importance": filed.importance}]
    elif isinstance(said.get("report"), dict) and policy != reports_service.NEVER:
        # A run on a linked computer: the report travels with its answer.
        calls = [dict(said["report"])]
    report, run.note = reports_service.outcome(
        notify=policy, title=schedule.title or "", calls=calls, answer=answer
    )
    if report is None:
        return None
    if filed is None:
        filed = reports_service.file(
            db, owner_id=owner.id, report=report, schedule=schedule, run=run,
            model_id=run.model_id or "",
        )
    # "Also save as a file": what the `report` target was before 3.8.0.
    if schedule.save_file or schedule.target == TARGET_REPORT:
        run.report_path = file_report(
            db, owner, filed.title or schedule.title or "Scheduled run", filed.body,
            when=clock.as_utc(run.started_at or now),
        )
    return filed


async def _announce_report(run_id: str) -> None:
    """One notification for the run's report -- its title and summary -- and
    none for a run without one."""
    from lembas.db.models import Report

    try:
        with session_scope() as db:
            run = db.get(ScheduleRun, run_id)
            owner = db.get(User, run.user_id) if run else None
            report = db.get(Report, run.report_id) if run and run.report_id else None
            if run is None or owner is None or report is None or report.notified:
                return
            told = await notify.announce_report(db, owner, report=report, run=run)
            report.notified = True
            run.notified = told
            db.commit()
    except Exception:  # a push service having a bad hour is not the run's failure
        log.exception("could not announce the report of schedule run %s", run_id)


# --- Telling somebody ---------------------------------------------------------------------
async def _announce(run_id: str, *, failed: bool, ask_pause: bool = False) -> None:
    try:
        with session_scope() as db:
            run = db.get(ScheduleRun, run_id)
            schedule = db.get(Schedule, run.schedule_id) if run else None
            owner = db.get(User, run.user_id) if run else None
            if run is None or schedule is None or owner is None:
                return
            told = await notify.announce(
                db, owner, schedule=schedule, run=run, failed=failed, ask_pause=ask_pause
            )
            run.notified = told
            db.commit()
    except Exception:  # a push service having a bad hour is not the run's failure
        log.exception("could not announce schedule run %s", run_id)


async def _announce_failure(run_id: str) -> None:
    """A failed (or skipped) run: told the first time, then once every six
    hours for the same error, and the third in a row asks about pausing."""
    with session_scope() as db:
        run = db.get(ScheduleRun, run_id)
        schedule = db.get(Schedule, run.schedule_id) if run else None
        if run is None or schedule is None:
            return
        if notify.policy(schedule.notify, schedule.rule_json or {}) == "never":
            return
        sig = notify.signature(run.error_class, run.error)
        tell, ask_pause, notes = notify.failure_decision(
            schedule.failure_notes_json or {},
            sig=sig,
            streak=int(schedule.failure_streak or 0),
            now=_now(),
        )
        schedule.failure_notes_json = notes
        db.commit()
    if tell:
        await _announce(run_id, failed=True, ask_pause=ask_pause)


def _notify_failure_later(schedule_id: str, run_id: str) -> None:
    with contextlib.suppress(RuntimeError):
        task = asyncio.get_running_loop().create_task(_announce_failure(run_id))
        _FIRINGS.add(task)
        task.add_done_callback(_FIRINGS.discard)


# --- Reports -------------------------------------------------------------------------------
REPORTS_DIR = "reports"


def report_path(home: str, title: str, when: datetime) -> str:
    """Where a run's report goes: `<home>/reports/YYYY-MM-DD HHMM <title>.md`,
    a name a person can sort and read, with nothing in it a path cannot hold."""
    safe = " ".join(
        "".join(c if c.isalnum() or c in " -_.,()" else " " for c in title).split()
    )[:80].strip(" .") or "Scheduled run"
    stamp = clock.as_utc(when).strftime("%Y-%m-%d %H%M")
    return f"{home}/{REPORTS_DIR}/{stamp} {safe}.md"


def file_report(db, owner: User, title: str, body: str, *, when: datetime) -> str:
    """Write a run's answer as a file in the owner's home, as the owner (no chat:
    a schedule's delivery is the person's own write). Returns the path."""
    from lembas.services import fs

    home = fs.ensure_home(db, owner).path
    path = report_path(home, title, when)
    view = fs.view(db, owner)
    n = 1
    while fs.lookup(db, owner.org_id, path) is not None:
        n += 1
        path = report_path(home, f"{title} ({n})", when)
    fs.write(view, path, f"# {title}\n\n{body.strip()}\n".encode(), mime="text/markdown")
    db.commit()
    return path


def _finished_reply(db, chat_id: str, message_id: str) -> Message | None:
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat_id:
        return None
    if not message.complete or message.error:
        return None
    return message


async def deliver(schedule_id: str, message_id: str, *, since: datetime) -> None:
    """File a finished reply where its schedule says (a report), outside a run.
    The run path files its own (`settle`); this is for a reply that is already
    written."""
    with session_scope() as db:
        schedule = db.get(Schedule, schedule_id)
        if schedule is None or schedule.target == TARGET_CHAT:
            return
        owner = db.get(User, schedule.user_id)
        if owner is None:
            return
        message = db.get(Message, message_id)
        chat_id = message.chat_id if message is not None else schedule.chat_id
        found = _finished_reply(db, chat_id, message_id)
        body = "The run did not produce a reply." if found is None else found.content
        file_report(db, owner, schedule.title or "Scheduled run", body, when=since)


# --- Run now, and after a restart -----------------------------------------------------------
async def run_now(schedule_id: str) -> str:
    """Fire a schedule because somebody pressed the button, and follow it.

    **Deliberately does not advance `next_fire_at`.** Testing a schedule must
    not consume the run it was testing. The ticker owns advancing.
    """
    return await fire(schedule_id, timing=TIMING_MANUAL)


# The firings started here, held so the loop does not collect a task nothing
# else refers to; each removes itself when it ends.
_FIRINGS: set[asyncio.Task] = set()


def fire_later(schedule_id: str) -> None:
    """`run_now` without the wait: for the button, whose request must come
    back at once."""
    task = asyncio.get_running_loop().create_task(run_now(schedule_id))
    _FIRINGS.add(task)
    task.add_done_callback(_FIRINGS.discard)


def start_now(schedule_id: str) -> str:
    """Open a manual run (Run now) and start it in the background. Returns the
    run's id at once, so a caller can point at it; "" when there is no such
    schedule. Does not advance the schedule (see `run_now`)."""
    with session_scope() as db:
        schedule = db.get(Schedule, schedule_id)
        if schedule is None:
            return ""
        run = _new_run(db, schedule, due_at=None, timing=TIMING_MANUAL)
        db.commit()
        run_id = run.id
    task = asyncio.get_running_loop().create_task(fire(schedule_id, run_id=run_id))
    _FIRINGS.add(task)
    task.add_done_callback(_FIRINGS.discard)
    return run_id


def follow_later(run_id: str) -> None:
    task = asyncio.get_running_loop().create_task(follow(run_id))
    _FIRINGS.add(task)
    task.add_done_callback(_FIRINGS.discard)


def resume_following() -> int:
    """After a restart: a run whose reply exists is followed again (the reply is
    taken up by `generation.resume_interrupted`, or is already written); one
    that never got as far as a reply is failed, saying why. Returns how many
    are followed."""
    followed = 0
    with session_scope() as db:
        for run in db.scalars(select(ScheduleRun).where(ScheduleRun.status == RUN_RUNNING)):
            if run.message_id and db.get(Message, run.message_id) is not None:
                with contextlib.suppress(RuntimeError):
                    follow_later(run.id)
                    followed += 1
                continue
            _finish(run, RUN_FAILED, error_class=ERROR_INTERNAL,
                    error="This run was interrupted by a restart.")
        db.commit()
    return followed


__all__ = [
    "deliver",
    "file_report",
    "fire",
    "fire_later",
    "follow",
    "report_path",
    "resume_following",
    "run_now",
    "settle",
    "start_now",
]
