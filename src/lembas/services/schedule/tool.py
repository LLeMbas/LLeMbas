"""Scheduling, as something a model can do rather than only a person.

## Why this exists

It did not, and that was the bug. Asked to "remind me every Monday at noon", a
model looked down its tool list, found `notes_create` described as *"something
worth having in a later conversation"* and `memory_add` beginning with the word
*Remember*, wrote a note, and reported that it had scheduled something. Nothing
in the list said scheduling existed; nothing said it did not. The near-misses
were the only thing to reach for.

No prompt fixes that. The seam had been left open on purpose --
`Schedule.origin` has defined `ORIGIN_MODEL` since the feature landed, with no
writer, and `services/schedules.py` says in its first line that it holds "what
the routes **and the tools** both need" -- and this is the tool that was meant
to go through it.

## One vocabulary, not a second one

Everything below is a thin layer over what the form already uses:
`rule.validate` is the single total normaliser (the manual form, the compile
step and this all hand it the same raw shape), `schedules.create` writes the row
and the task chat together, and `rule.describe` says what came out in words. A
second dialect for models would mean two definitions of "every other Tuesday"
and one of them going quietly wrong.

The rule shape is documented in `services/schedule/rule.py` and repeated to the
model in the `tool.schedule` fragment, which is deliberately worded from
`task.schedule_compile` -- the prompt that has been turning people's words into
this same JSON since the feature shipped.

## What the tool answers with

`rule.describe(...)`, always, not "done". A schedule is invisible until it fires,
which may be days away, so the one moment anybody can check that Monday was
understood as Monday is the sentence in the reply. The model is told to quote it.
`ORIGIN_MODEL` goes on the row for the same reason: the Scheduled list can then
say which of these nobody typed.

## What it deliberately cannot do

Touch anybody else's schedules -- `schedules.get` takes the user and returns
None for a row that is not theirs, which is the whole authorisation here as it
is in the routes. And it cannot create an agent task: `schedules.create` refuses
that on its own terms, for reasons written down there.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from lembas.db.models import ORIGIN_MODEL, TARGET_CHAT, TARGETS, Schedule, User
from lembas.db.session import session_scope
from lembas.services.schedule import clock
from lembas.services.schedule import rule as rule_service

if TYPE_CHECKING:  # pragma: no cover - typing only
    from lembas.services.tools import ToolContext, ToolDef, ToolOutcome

log = logging.getLogger(__name__)

# What a schedule may be told to do, as the model sees it. The same ones the
# form offers, in the same order, with the wording that says which to pick --
# `target` is the field a model gets wrong most often, because each is
# a plausible reading of "tell me".
TARGET_HELP = (
    "Where the result goes. "
    '"report" for something to read later — an investigation, a summary, a '
    "digest — which is written as a Markdown file in the person's reports/ "
    "directory and cannot be replied to. "
    '"chat" to leave it in the task\'s own chat, which is right when the runs '
    "build on each other and you want the transcript."
)

RULE_HELP = (
    "When it runs, as an object. Keys, all optional:\n"
    '- "start": ISO timestamp for the first (or only) run. Required for a '
    'one-off, and for "every".\n'
    '- "every": a plain timer — one of {"minutes": n}, {"hours": n}, '
    '{"days": n}, {"weeks": n}. Use this for "in ten minutes" and "every six '
    'hours". Minimum one minute.\n'
    '- "at": a calendar — {"weekdays": [...], "days": [1-31], '
    '"months": [1-12], "times": ["HH:MM"]}. Leave a list out to mean every one '
    'of them. Use this for "every Monday at noon" and "daily at nine". '
    "Weekdays are Monday=0, Tuesday=1, Wednesday=2, Thursday=3, Friday=4, "
    "Saturday=5, Sunday=6 — count them off rather than guessing, because "
    "naming the wrong day still looks like a working schedule. "
    'For a day counted within the month put "nth" in "at" instead of weekdays '
    'and days: [{"n": 2, "weekday": 0}] is the second Monday, '
    '[{"n": -1, "weekday": 4}] the last Friday, [{"n": -1}] the last day of '
    "the month.\n"
    '- "count": how many times in total, if a number was given.\n'
    '- "until": ISO timestamp to stop after, if one was given.\n'
    'Give both "every" and "at" only for something like "every other Tuesday". '
    "Times are in the reader's own timezone, which the system prompt states; "
    "schedule_list says what time it is there now."
)


def _outcome(text: str, event: dict[str, Any]) -> ToolOutcome:
    """Imported here rather than at module scope: `services/tools` imports
    this module to build the definitions, so a top-level import back is a cycle.
    """
    from lembas.services.tools import ToolOutcome

    return ToolOutcome(text, event)


def _error(name: str, message: str) -> ToolOutcome:
    return _outcome(message, {"name": name, "status": "error", "error": message})


def _summary(schedule: Schedule, owner: User | None) -> str:
    return rule_service.describe(schedule.rule_json or {}, zone=clock.zone_for(owner))


def _row(schedule: Schedule, owner: User | None, db=None) -> dict[str, Any]:
    """One schedule as the transcript shows it, and as the model reads it back."""
    from datetime import UTC, datetime, timedelta

    from lembas.services.schedule import notify, runs

    zone = clock.zone_for(owner)
    nexts: list[str] = []
    if schedule.enabled and schedule.next_fire_at:
        start = clock.as_utc(schedule.next_fire_at) - timedelta(microseconds=1)
        nexts = [
            m.astimezone(zone).strftime("%a %-d %b %H:%M")
            for m in rule_service.upcoming(
                schedule.rule_json or {}, now=start, zone=zone,
                fired=schedule.fired_count or 0, count=3,
            )
        ]
    last = runs.latest(db, schedule) if db is not None else None
    return {
        "id": schedule.id,
        "title": schedule.title,
        "summary": _summary(schedule, owner),
        "target": schedule.target,
        "enabled": bool(schedule.enabled),
        "next": schedule.next_fire_at.isoformat() if schedule.next_fire_at else "",
        "next_runs": nexts,
        "notify": notify.policy(schedule.notify, schedule.rule_json or {}),
        "mode": schedule.mode,
        "model": schedule.model_id or "",
        "watch_url": schedule.watch_target if schedule.watch_kind == "url" else "",
        "last_status": last.status if last is not None else "",
        "last_at": (
            clock.as_utc(last.finished_at or last.created_at).astimezone(zone)
            .strftime("%a %-d %b %H:%M")
            if last is not None and (last.finished_at or last.created_at)
            else ""
        ),
        "now": datetime.now(UTC).isoformat(),
    }


NOTIFY_HELP = (
    'When the person is notified of a result: "always" (every run), "matters" '
    "(every run except one that answers [SILENT] because there was nothing new; "
    'the default for anything that repeats) or "never" (it is only kept in the '
    "history). A one-off reminder defaults to always."
)
MODE_HELP = (
    '"fresh" (the default): each run starts a chat of its own and is told what the '
    'previous run found. "continue": every run goes into the one chat, which keeps '
    "growing -- right only when each run needs the whole conversation before it."
)
WATCH_HELP = (
    "A web address to watch (http or https). Each run first fetches it; when "
    "nothing changed since the last run that looked, the run stops there "
    "(recorded as unchanged, nobody is told) and the instruction is not run. When "
    "it changed -- and on the first look -- the run is told what changed and what "
    "is there now. Use it for \"tell me when this page changes\". An empty string "
    "stops watching."
)
MODEL_HELP = (
    "The model to run it on first, by id; omit for the person's scheduling list. "
    "When it is busy, the next free model of that list runs it."
)


def _settings_from(args: dict[str, Any]) -> dict[str, Any]:
    """The run settings a model may pass, under the names the form uses."""
    settings: dict[str, Any] = {}
    for key in ("notify", "mode"):
        if args.get(key) is not None:
            settings[key] = str(args.get(key) or "")
    if args.get("watch_url") is not None:
        settings["watch"] = str(args.get("watch_url") or "")
    return settings


def _model_for(db, owner: User, args: dict[str, Any], context) -> str:
    """The model a model asked for, else the chat's own when the person may
    schedule on it, else "" (the scheduling list decides)."""
    asked = str(args.get("model") or "").strip()
    if asked:
        return asked
    from lembas.services import chat as chat_service

    own = context.model_id or ""
    usable = {m.model_id for m in chat_service.available_models(db, owner)}
    return own if own in usable else ""


# --- Making one ----------------------------------------------------------------
async def _run_create(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from lembas.services import schedules as schedules_service

    title = str(args.get("title") or "").strip()
    instruction = str(args.get("instruction") or "").strip()
    target = str(args.get("target") or TARGET_CHAT).strip().lower()
    raw_rule = args.get("schedule") or args.get("rule") or {}

    if not instruction:
        return _error(
            "schedule_create",
            "A schedule needs an instruction: what should be done each time it "
            "runs, written out in full. It will be read on its own, days later, "
            "by a model that was not here — so say everything it needs.",
        )
    if not isinstance(raw_rule, dict):
        return _error("schedule_create", "The schedule must be an object saying when to run.")
    if target not in TARGETS:
        target = TARGET_CHAT

    with session_scope() as db:
        owner = db.get(User, context.owner_id)
        if owner is None:  # pragma: no cover - a session outliving its user
            return _error("schedule_create", "That account no longer exists.")
        try:
            schedule = schedules_service.create(
                db,
                owner=owner,
                title=title or instruction[:60],
                instruction=instruction,
                rule=raw_rule,
                target=target,
                model_id=_model_for(db, owner, args, context),
                # The value that has been declared and unwritten since the
                # feature shipped. It is what lets the Scheduled list say which
                # of these nobody typed.
                origin=ORIGIN_MODEL,
                settings=_settings_from(args),
            )
        except schedules_service.ScheduleError as exc:
            # Its message is written for a person and reads correctly to a
            # model too -- it says what is wrong with the timing rather than
            # that something failed, which is what makes the next attempt
            # different from this one.
            return _error("schedule_create", str(exc))

        summary = _summary(schedule, owner)
        log.info("%s scheduled %r by model (%s)", owner.email, schedule.title, summary)
        return _outcome(
            # The summary first, and phrased so quoting it is the obvious thing
            # to do: it is the only chance anybody has to notice that Monday was
            # read as Monday before the first run arrives.
            f"Scheduled {schedule.title!r}: {summary}. "
            f"The result goes to {schedule.target}. Tell the reader this summary "
            f"in your reply, in your own words, so they can correct it now rather "
            f"than when it first runs.",
            {
                "name": "schedule_create",
                "query": schedule.title,
                "status": "ok",
                "detail": summary,
                "results": [_row(schedule, owner, db)],
            },
        )


# --- Reading them back ---------------------------------------------------------
async def _run_list(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from lembas.services import schedules as schedules_service

    with session_scope() as db:
        owner = db.get(User, context.owner_id)
        # The time now, first: a chat's system prompt carries no clock (harness
        # spec 3.1.0, it is made once per session), and "in ten minutes" needs
        # one. `tool.schedule` tells the model to call this first.
        now = clock.now_for(owner)
        heading = (
            f"It is now {now.strftime('%A %-d %B %Y, %H:%M')} "
            f"({clock.name_for(owner) or clock.server_zone()})."
        )
        # `visible` is the same narrowing the routes use, and is the whole
        # authorisation: it answers with nothing at all for a missing user.
        rows = list(db.scalars(schedules_service.visible(owner).order_by(Schedule.created_at)))
        if not rows:
            return _outcome(
                f"{heading}\nThere are no schedules.",
                {"name": "schedule_list", "status": "ok", "results": []},
            )
        listed = [_row(row, owner, db) for row in rows]

        def line(row: dict[str, Any]) -> str:
            text = f"- {row['id']}: {row['title']} — {row['summary']}, to {row['target']}"
            text += f", notify {row['notify']}, {row['mode']}"
            if row["watch_url"]:
                text += f", watching {row['watch_url']}"
            if not row["enabled"]:
                text += " (paused)"
            elif row["next_runs"]:
                text += f"; next: {', '.join(row['next_runs'])}"
            if row["last_status"]:
                text += f"; last run {row['last_status']} ({row['last_at']})"
            return text

        lines = "\n".join(line(row) for row in listed)
        return _outcome(
            f"{heading}\n{len(listed)} schedule(s):\n{lines}",
            {"name": "schedule_list", "status": "ok", "results": listed},
        )


# --- Changing and stopping -----------------------------------------------------
async def _run_update(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from lembas.services import schedules as schedules_service

    with session_scope() as db:
        owner = db.get(User, context.owner_id)
        schedule = schedules_service.get(db, str(args.get("id") or ""), owner)
        if schedule is None:
            return _error(
                "schedule_update",
                "There is no such schedule, or it belongs to someone else. "
                "schedule_list gives the ids you can use.",
            )

        raw_rule = args.get("schedule") or args.get("rule")
        enabled = args.get("enabled")
        try:
            settings = _settings_from(args)
            if (
                raw_rule is not None
                or args.get("title")
                or args.get("instruction") is not None
                or args.get("target")
                or args.get("model") is not None
                or settings
            ):
                schedules_service.update(
                    db,
                    schedule,
                    owner=owner,
                    title=args.get("title"),
                    instruction=args.get("instruction"),
                    rule=raw_rule if isinstance(raw_rule, dict) else None,
                    target=str(args.get("target") or "") or None,
                    model_id=str(args.get("model")) if args.get("model") is not None else None,
                    settings=settings,
                )
            if enabled is not None:
                schedules_service.set_enabled(db, schedule, owner=owner, enabled=bool(enabled))
        except schedules_service.ScheduleError as exc:
            return _error("schedule_update", str(exc))

        summary = _summary(schedule, owner)
        state = "running" if schedule.enabled else "paused"
        return _outcome(
            f"Updated {schedule.title!r}: {summary}, {state}. Say the new timing "
            f"in your reply.",
            {
                "name": "schedule_update",
                "query": schedule.title,
                "status": "ok",
                "detail": summary,
                "results": [_row(schedule, owner, db)],
            },
        )


async def _run_cancel(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from lembas.services import schedules as schedules_service

    with session_scope() as db:
        owner = db.get(User, context.owner_id)
        schedule = schedules_service.get(db, str(args.get("id") or ""), owner)
        if schedule is None:
            return _error(
                "schedule_cancel",
                "There is no such schedule, or it belongs to someone else. "
                "schedule_list gives the ids you can use.",
            )
        title = schedule.title
        # The transcript is kept, which is `delete`'s own default and the right
        # one: removing a timer must not delete a conversation as a side effect.
        schedules_service.delete(db, schedule, keep_chat=True)
        log.info("%s cancelled schedule %r by model", owner.email if owner else "?", title)
        return _outcome(
            f"Stopped {title!r}. It will not run again; what it has already "
            f"written is kept.",
            {"name": "schedule_cancel", "query": title, "status": "ok", "results": []},
        )


def tool_defs() -> list[ToolDef]:
    """The four, built here so `services/tools` need not know the wording."""
    from lembas.services.tools import FAMILY_SCHEDULE, RISK_READ, RISK_WRITE, ToolDef

    return [
        ToolDef(
            name="schedule_create",
            family=FAMILY_SCHEDULE,
            description=(
                "Set something up to happen later, or repeatedly, without anybody "
                "asking again. Use this whenever the reader says when something "
                "should happen — “in ten minutes”, “every Monday at noon”, "
                "“daily”, “remind me”. Writing a note or a memory instead does "
                "not make anything happen at the time; a note is read only when "
                "somebody goes looking for it. Say the resulting timing back to "
                "the reader so they can correct it before the first run."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "A short name for this, five words or fewer.",
                    },
                    "instruction": {
                        "type": "string",
                        "description": (
                            "What to do each time it runs, written out in full and "
                            "as an instruction rather than a description. It is "
                            "read on its own, with none of this conversation "
                            "around it and nobody to ask, so say everything it "
                            "needs."
                        ),
                    },
                    "target": {
                        "type": "string",
                        "enum": list(TARGETS),
                        "description": TARGET_HELP,
                    },
                    "schedule": {"type": "object", "description": RULE_HELP},
                    "notify": {
                        "type": "string",
                        "enum": ["always", "matters", "never"],
                        "description": NOTIFY_HELP,
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["fresh", "continue"],
                        "description": MODE_HELP,
                    },
                    "model": {"type": "string", "description": MODEL_HELP},
                    "watch_url": {"type": "string", "description": WATCH_HELP},
                },
                "required": ["instruction", "schedule"],
            },
            run=_run_create,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="schedule_list",
            family=FAMILY_SCHEDULE,
            description=(
                "Everything already scheduled, with its id, its timing in words, "
                "its next runs, how it notifies and how its last run went. Worth "
                "calling before setting something "
                "up, so an existing one is changed rather than duplicated, and "
                "before answering a question about what is scheduled."
            ),
            parameters={"type": "object", "properties": {}},
            run=_run_list,
            risk=RISK_READ,
        ),
        ToolDef(
            name="schedule_update",
            family=FAMILY_SCHEDULE,
            description=(
                "Change a schedule: its timing, its instruction, its title, where "
                "its result goes, when the person is notified, fresh or continuing "
                "runs, its model, or whether it is paused. Omit a field to leave "
                "it alone. Changing the timing restarts the count, because an "
                "edited schedule is a new intention. Ids come from schedule_list."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "From schedule_list."},
                    "title": {"type": "string"},
                    "instruction": {"type": "string"},
                    "target": {"type": "string", "enum": list(TARGETS)},
                    "schedule": {"type": "object", "description": RULE_HELP},
                    "notify": {
                        "type": "string",
                        "enum": ["always", "matters", "never"],
                        "description": NOTIFY_HELP,
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["fresh", "continue"],
                        "description": MODE_HELP,
                    },
                    "model": {"type": "string", "description": MODEL_HELP},
                    "watch_url": {"type": "string", "description": WATCH_HELP},
                    "enabled": {
                        "type": "boolean",
                        "description": (
                            "False to pause it, true to resume. Resuming counts "
                            "from now, so a schedule paused for a month does not "
                            "come back owing runs."
                        ),
                    },
                },
                "required": ["id"],
            },
            run=_run_update,
            risk=RISK_WRITE,
        ),
        ToolDef(
            name="schedule_cancel",
            family=FAMILY_SCHEDULE,
            description=(
                "Stop a schedule for good and remove it. What it has already "
                "written is kept. To stop one temporarily, use schedule_update "
                "with enabled false instead."
            ),
            parameters={
                "type": "object",
                "properties": {"id": {"type": "string", "description": "From schedule_list."}},
                "required": ["id"],
            },
            run=_run_cancel,
            risk=RISK_WRITE,
        ),
    ]


__all__ = ["RULE_HELP", "TARGET_HELP", "tool_defs"]
