"""`report_write` (harness spec 3.8.0, `reports.md`): a schedule's run files
its report for the person who set the schedule.

Offered only in a session a schedule opened for its run, and not when the
schedule's notify-when is `never` (`resolve._offered_in`). A turn there that
is not the run's -- the person writing into a run's chat afterwards -- finds
the tool in the list and answering its `unavailable` text. A run files one
report: a second call replaces the first.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from lembas.services.tools.core import (
    RISK_INTERACT,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)

# A family of its own, outside the gates: whether it is offered is a fact
# about the session (`session_schedule`), not a capability somebody switches.
FAMILY_REPORT = "report"
NAME = "report_write"


def session_schedule(db, chat):
    """The schedule whose run this chat is a session of and that wants
    reports, or None: a task chat's (or the chat a `continue` schedule runs
    in), for a schedule whose notify-when is not `never`. Never a subagent's
    chat, and never an agent chat -- a run on a linked computer has the
    device's own `report_write`."""
    from lembas.db.models import KIND_AGENT, Schedule, ScheduleRun
    from lembas.services import reports
    from lembas.services.schedule import notify

    if chat is None or chat.kind == KIND_AGENT or getattr(chat, "parent_chat_id", None):
        return None
    schedule = db.scalar(
        select(Schedule).where(Schedule.user_id == chat.user_id, Schedule.chat_id == chat.id)
    )
    if schedule is None:
        run = db.scalar(
            select(ScheduleRun)
            .where(ScheduleRun.chat_id == chat.id, ScheduleRun.user_id == chat.user_id)
            .order_by(ScheduleRun.created_at.desc())
        )
        schedule = db.get(Schedule, run.schedule_id) if run is not None else None
    if schedule is None:
        return None
    wanted = notify.policy(schedule.notify, schedule.rule_json or {})
    return None if wanted == reports.NEVER else schedule


def _running(db, context: ToolContext):
    """The run this call is made in: the one whose reply is being written."""
    from lembas.db.models import RUN_RUNNING, ScheduleRun

    if not context.chat_id or not context.message_id:
        return None
    return db.scalar(
        select(ScheduleRun).where(
            ScheduleRun.chat_id == context.chat_id,
            ScheduleRun.message_id == context.message_id,
            ScheduleRun.status == RUN_RUNNING,
        )
    )


async def _run_report_write(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from lembas.db.models import Schedule, User
    from lembas.db.session import session_scope
    from lembas.services import harness_spec, reports

    event: dict[str, Any] = {"name": NAME, "kind": "report", "results": []}
    unavailable = harness_spec.tools()[NAME]["unavailable"]
    with session_scope() as db:
        run = _running(db, context)
        schedule = db.get(Schedule, run.schedule_id) if run is not None else None
        owner = db.get(User, run.user_id) if run is not None else None
        if run is None or schedule is None or owner is None:
            return ToolOutcome(unavailable, {**event, "status": "error", "error": unavailable})
        report = reports.clean_call(args)
        if report is None:
            text = "A report needs a title and a body."
            return ToolOutcome(text, {**event, "status": "error", "error": text})
        replaced = bool(run.report_id)
        row = reports.file(
            db, owner_id=owner.id, report=report, schedule=schedule, run=run,
            model_id=run.model_id or "",
        )
        db.commit()
        title, report_id = row.title, row.id
    return ToolOutcome(
        reports.result_text(2 if replaced else 1),
        {**event, "status": "ok", "query": title, "report_id": report_id},
    )


TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name=NAME,
        family=FAMILY_REPORT,
        description=spec_description(NAME),
        parameters=spec_parameters(NAME),
        run=_run_report_write,
        risk=RISK_INTERACT,
    ),
)
