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
    CATCH_UPS,
    KIND_TASK,
    MODE_FRESH,
    MODES,
    NOTIFY_POLICIES,
    ORIGIN_USER,
    ORIGINS,
    TARGET_CHAT,
    TARGETS,
    Chat,
    Schedule,
    ScheduleRun,
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
    """The schedule a task chat belongs to, if any: its current chat, or the
    chat of one of its earlier fresh runs."""
    from lembas.services.schedule import runs

    return runs.schedule_for_chat(db, chat)


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


MAX_AGENTS = 8
MAX_GRACE_MINUTES = 7 * 24 * 60
# What `settings` accepts, and nothing else.
SETTING_KEYS = (
    "mode", "notify", "notify_when", "save_file", "catch_up", "grace_minutes", "effort",
    "agents", "tools", "watch", "runs_on",
)


def apply_settings(
    schedule: Schedule,
    settings: dict | None,
    *,
    db: DBSession | None = None,
    owner: User | None = None,
    calling_device: str = "",
) -> None:
    """A schedule's run settings from a form, the API or a model's tool call.

    Total like `rule.validate`: what it does not recognise it leaves alone, and
    every value is clamped, because the tool hands it model output. A key that
    is present and empty clears (back to the default)."""
    from lembas.services.schedule import runs

    settings = settings or {}
    if "mode" in settings:
        value = str(settings.get("mode") or "")
        schedule.mode = value if value in MODES else MODE_FRESH
    if "notify" in settings:
        value = str(settings.get("notify") or "")
        # `matters` is what `when` was called before harness spec 3.8.0: a
        # client or a model that still says it means the same thing.
        value = "when" if value == "matters" else value
        schedule.notify = value if value in NOTIFY_POLICIES else ""
    if "notify_when" in settings:
        # `when`'s condition, in the person's words: one line, bounded.
        schedule.notify_when = " ".join(str(settings.get("notify_when") or "").split())[:500]
    if "save_file" in settings:
        schedule.save_file = bool(settings.get("save_file"))
        # The `report` target was this switch before 3.8.0; a schedule that
        # still has it and is told "no file" would go on saving one.
        if not schedule.save_file and schedule.target == "report":
            schedule.target = "chat"
    if "catch_up" in settings:
        value = str(settings.get("catch_up") or "")
        schedule.catch_up = value if value in CATCH_UPS else CATCH_UPS[0]
    if "grace_minutes" in settings:
        try:
            minutes = int(settings.get("grace_minutes") or 0)
        except (TypeError, ValueError):
            minutes = 0
        schedule.grace_minutes = min(minutes, MAX_GRACE_MINUTES) if minutes > 0 else None
    if "effort" in settings:
        schedule.effort = str(settings.get("effort") or "")[:16]
    if "agents" in settings:
        try:
            agents = int(settings.get("agents") or 0)
        except (TypeError, ValueError):
            agents = 0
        schedule.agents = min(agents, MAX_AGENTS) if agents >= 2 else None
    if "runs_on" in settings:
        apply_runs_on(db, owner, schedule, settings.get("runs_on"), calling_device=calling_device)
    if "watch" in settings:
        apply_watch(schedule, settings.get("watch"))
    if schedule.watch_kind == "command" and not schedule.device_token_id:
        raise ScheduleError(
            N_("A command is watched on a linked computer: choose the computer it runs on.")
        )
    if "tools" in settings:
        raw = settings.get("tools")
        chosen = [str(t) for t in raw] if isinstance(raw, (list, tuple)) else []
        kept = [t for t in runs.TOOL_CHOICES if t in chosen]
        # The safe set exactly is stored as "the default", so a later change to
        # the default reaches it.
        schedule.tools_json = [] if tuple(kept) == runs.SAFE_TOOLS else kept


def apply_runs_on(
    db: DBSession | None, owner: User | None, schedule: Schedule, value: object,
    *, calling_device: str = "",
) -> None:
    """Where it runs (harness spec 3.5.0, `schedule/device.py`): `{device,
    cwd, mode, approval_wait_minutes}` for a linked computer, null or empty
    for the server. `device` is the token's id, its host, or `"this"` for the
    calling device. A change of computer or folder starts its chat afresh."""
    from lembas.services.schedule import device

    if db is None or owner is None:
        raise ScheduleError(N_("Say which computer to run it on."))
    try:
        token_id, cwd, mode, wait = device.validate(
            db, owner, value, calling_device=calling_device
        )
    except device.DeviceChoiceError as exc:
        raise ScheduleError(exc.message) from exc
    # A `continue` chat kept for the old computer or folder is not reused:
    # the runner takes the home chat only when it runs where the schedule does.
    schedule.device_token_id = token_id or None
    schedule.device_dir = cwd
    schedule.device_mode = mode
    schedule.approval_wait_minutes = wait


def apply_watch(schedule: Schedule, value: object) -> None:
    """Monitor mode (`schedule/monitor.py`): what the schedule watches --
    `{"kind": "url", "url"}`, `{"kind": "command", "command", "timeout_s"}`
    (the shapes `/api/v2` answers), `{"kind", "target"}`, a bare address, or
    empty to watch nothing. A new target starts afresh: the first look after
    it is a first look."""
    from lembas.services.schedule import monitor

    timeout = None
    if isinstance(value, dict):
        kind = str(value.get("kind") or "")
        target = str(
            value.get("target") or value.get("url") or value.get("command") or ""
        )
        if value.get("timeout_s") not in (None, ""):
            try:
                timeout = max(1, min(600, int(float(value.get("timeout_s")))))
            except (TypeError, ValueError):
                timeout = None
    else:
        kind, target = "", str(value or "")
    try:
        kind, target = monitor.validate(kind, target)
    except monitor.WatchError as exc:
        raise ScheduleError(exc.message) from exc
    if (kind, target) != (schedule.watch_kind or "", schedule.watch_target or ""):
        schedule.watch_hash, schedule.watch_last, schedule.watch_changed_at = "", "", None
    schedule.watch_kind, schedule.watch_target = kind, target
    schedule.watch_timeout = timeout if kind == monitor.KIND_COMMAND else None


def model_choice(db: DBSession, owner: User, model_id: str) -> tuple[str, str | None]:
    """A first-choice model id, checked against what the person may use: the
    id and its connection, or ("", None) for "the scheduling list decides"."""
    from lembas.services import chat as chat_service

    model_id = (model_id or "").strip()
    if not model_id:
        return "", None
    row = next(
        (m for m in chat_service.available_models(db, owner) if m.model_id == model_id), None
    )
    if row is None:
        raise ScheduleError(N_("That model is not one you can use."))
    return row.model_id, row.connection_id


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
    settings: dict | None = None,
    calling_device: str = "",
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

    That pass is harness spec 3.5.0: a schedule may run **on a linked
    computer** that accepts scheduled work, in a mode built for it (Edit by
    default, approvals waiting on the phone, then denied). Each such run is an
    agent chat there, made by the runner (`schedule/device.py`); the chat made
    here stays the schedule's task chat.
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

    chosen = model_choice(db, owner, model_id)
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
    from lembas.services.schedule import notify

    schedule.notify = notify.default_policy(clean)
    if chosen[0]:
        schedule.model_id, schedule.connection_id = chosen
        chat.connection_id = chosen[1]
    apply_settings(schedule, settings, db=db, owner=owner, calling_device=calling_device)
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
    model_id: str | None = None,
    settings: dict | None = None,
    calling_device: str = "",
) -> Schedule:
    """Change a schedule. Absent arguments are left alone."""
    if title is not None and title.strip():
        schedule.title = title.strip()[:MAX_TITLE_CHARS]
    if instruction is not None:
        schedule.instruction = instruction.strip()[:MAX_INSTRUCTION_CHARS]
    if target is not None and target in TARGETS:
        schedule.target = target
    if model_id is not None:
        schedule.model_id, schedule.connection_id = model_choice(db, owner, model_id)
    apply_settings(schedule, settings, db=db, owner=owner, calling_device=calling_device)
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


def delete(db: DBSession, schedule: Schedule) -> None:
    """Remove a schedule, and its runs' working chats with it.

    A run's chat -- a task chat, or the agent chat of a run on a linked
    computer -- is the schedule's scratch space and goes with it. It used to
    be *kept* by default and turned into an ordinary chat "so it is reachable
    again", which is how deleting one schedule put forty chats named after it
    into the list of chats. What a schedule produced that is worth keeping is
    its **reports**: they stay, each with the conversation a reply continued
    in, and say which schedule they came from by the title they were filed
    under.

    A schedule that ran *in a chat of the person's own* (mode `continue` on an
    ordinary chat) never owned that chat: it is left exactly as it is."""
    # A run in flight is stopped first, as Stop does: its reply must not go
    # on being written into a chat that is about to be gone. What it has
    # already filed is a report like any other -- kept, and told, since the
    # run will never reach the point where it would have told it.
    from lembas.db.models import RUN_RUNNING

    orphaned: list[str] = []
    for run in db.scalars(
        select(ScheduleRun).where(
            ScheduleRun.schedule_id == schedule.id, ScheduleRun.status == RUN_RUNNING
        )
    ):
        if run.message_id:
            from lembas.services import generation as generation_service

            generation_service.request_stop(run.message_id)
        if run.report_id:
            orphaned.append(run.report_id)
    ids = {schedule.chat_id} if schedule.chat_id else set()
    ids |= {
        cid
        for cid in db.scalars(
            select(ScheduleRun.chat_id).where(ScheduleRun.schedule_id == schedule.id)
        )
        if cid
    }
    doomed = [
        c for c in (db.get(Chat, cid) for cid in ids)
        if c is not None and c.user_id == schedule.user_id
        and (c.kind == KIND_TASK or getattr(c, "scheduled", False))
    ]
    if doomed:
        from lembas.services import chat as chat_service

        chat_service.delete_chats(db, doomed)
    db.delete(schedule)
    db.commit()
    for report_id in orphaned:
        _tell_later(report_id)


# Announcements started by a delete, held so the loop does not collect one
# mid-flight (asyncio keeps only a weak reference to a task nobody awaits).
_TELLING: set = set()


def _tell_later(report_id: str) -> None:
    """Announce a report whose run was removed under it. From a request there
    is a loop to do it on; from anywhere else (a command, a test without one)
    it is simply not announced -- the report is still under Reports."""
    import asyncio

    from lembas.services.schedule import runner

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(runner.announce_report_alone(report_id))
    _TELLING.add(task)
    task.add_done_callback(_TELLING.discard)


def describe(schedule: Schedule, *, owner: User | None) -> str:
    return rule_service.describe(schedule.rule_json or {}, zone=clock.zone_for(owner))
