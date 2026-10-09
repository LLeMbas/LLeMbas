"""Scheduled: the list, the setup form, and one task chat's controls.

A schedule's own chat is rendered by the ordinary chat page — same transcript,
same tail poller, same canvas — with the composer replaced by a strip of
controls. That is the whole reason `KIND_TASK` reuses `Chat` and `Message`
rather than growing tables of its own.

The rule form here is the **manual** one, and it is not a fallback in the
apologetic sense: it is what makes "an empty override means off" safe for the
compile step in Phase 3. Clearing `task.schedule_compile` must switch off the
*compiling*, not the feature.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.api.pages import sidebar_context
from lembas.db.models import TARGET_CHAT, TARGET_REPORT, Schedule
from lembas.services import chat as chat_service
from lembas.services import schedules as schedules_service
from lembas.services.schedule import clock, runner
from lembas.services.schedule import rule as rule_service
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(
    dependencies=[Depends(require_permission("schedule.use"))], tags=["schedules"]
)

# What the setup form may ask for, in the order they are offered.
def offered_targets() -> tuple[tuple[str, str], ...]:
    """A function so the labels are in the reader's language."""
    return (
        (TARGET_CHAT, t("Its own chat")),
        (TARGET_REPORT, t("Reports")),
    )

REPEAT_ONCE = "once"
REPEAT_EVERY = "every"
REPEAT_CALENDAR = "calendar"


def _rule_from_form(form) -> dict:
    """Build a rule dict out of the setup form's fields.

    Deliberately builds the *raw* shape and hands it to `rule.validate` rather
    than validating here: there is one normaliser, it is total, and it is the
    same one a model's compiled output will go through in Phase 3. Two
    validators would be two ideas of what a legal schedule is.
    """
    repeat = str(form.get("repeat") or REPEAT_ONCE)
    raw: dict = {}

    when = str(form.get("start_date") or "").strip()
    at_time = str(form.get("start_time") or "").strip() or "09:00"
    if when:
        raw["start"] = f"{when}T{at_time}:00"

    if repeat == REPEAT_EVERY:
        unit = str(form.get("every_unit") or "hours")
        try:
            amount = int(form.get("every_amount") or 1)
        except (TypeError, ValueError):
            amount = 1
        raw["every"] = {unit: amount}
        # A timer with no start begins now. Said here rather than in the rule
        # module, which has no clock by design.
        raw.setdefault("start", datetime.now(tz=UTC).isoformat())

    elif repeat == REPEAT_CALENDAR:
        times = [t.strip() for t in str(form.get("times") or "09:00").split(",") if t.strip()]
        raw["at"] = {
            "weekdays": [int(d) for d in form.getlist("weekdays") if str(d).isdigit()],
            "times": times,
        }
        days = str(form.get("month_days") or "").strip()
        if days:
            raw["at"]["days"] = [int(d) for d in days.split(",") if d.strip().isdigit()]

    try:
        count = int(form.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    if count > 0:
        raw["count"] = count

    until = str(form.get("until") or "").strip()
    if until:
        raw["until"] = f"{until}T23:59:00"

    return raw


def _form_values(
    *, schedule: Schedule | None = None, compiled=None
) -> dict:
    """Everything `schedules/_form.html` renders, from whichever source there is.

    One dict for both pages, because they are the same fields: an existing row
    on the edit page, and what the compile proposed on the new one. The form
    reads only this, so what a model suggested is displayed through exactly the
    same path as what is stored -- there is no branch in the template that could
    show one of them differently.
    """
    if compiled is not None:
        values = _rule_defaults_from(compiled.rule)
        values.update(
            title=compiled.title, instruction=compiled.instruction, target=compiled.target
        )
        return values
    values = _rule_defaults_from((schedule.rule_json if schedule else {}) or {})
    values.update(
        title=schedule.title if schedule else "",
        instruction=schedule.instruction if schedule else "",
        target=schedule.target if schedule else TARGET_CHAT,
    )
    return values


def _rule_defaults_from(rule: dict) -> dict:
    """What the form should show for a rule.

    Derived from the *normalised* rule, so the form and the engine cannot
    disagree about what is stored -- an edit screen showing something other
    than what runs is the same failure as a label that names the wrong tool.
    Shared by the edit page and by the compile's review step, so what a model
    proposed is displayed through exactly the same path as what is saved.
    """
    rule = rule or {}
    at = rule.get("at") or {}
    every = rule.get("every") or {}
    if at:
        repeat = REPEAT_CALENDAR
    elif every:
        repeat = REPEAT_EVERY
    else:
        repeat = REPEAT_ONCE
    minutes = int(every.get("minutes") or 0)
    unit, amount = "minutes", minutes
    for size, name in ((10080, "weeks"), (1440, "days"), (60, "hours")):
        if minutes and not minutes % size:
            unit, amount = name, minutes // size
            break
    return {
        "repeat": repeat,
        "every_unit": unit,
        "every_amount": amount or 1,
        "weekdays": at.get("weekdays") or [],
        "times": ", ".join(at.get("times") or []),
        "month_days": ", ".join(str(d) for d in at.get("days") or []),
        "count": rule.get("count") or 0,
    }


def _context(db, user, schedule: Schedule | None, *, error: str = "") -> dict:
    return {
        "section": "scheduled",
        "schedule": schedule,
        "targets": offered_targets(),
        "weekday_names": list(
            enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
        ),
        "form": _form_values(schedule=schedule),
        "error": error,
        "models": chat_service.available_models(db, user),
        "timezone": clock.name_for(user) or str(clock.server_zone()),
        **sidebar_context(db, user),
    }


# --- The list ------------------------------------------------------------------
# The list is the Scheduled tab of Settings (`api/pages.py` draws it with this).
LIST_URL = "/settings?tab=scheduled"


def listed(db, user) -> list[dict]:
    """This person's schedules, newest first, with what the list says of each."""
    rows = list(
        db.scalars(schedules_service.visible(user).order_by(Schedule.created_at.desc()))
    )
    zone = clock.zone_for(user)
    return [
        {
            "row": row,
            "summary": rule_service.describe(row.rule_json or {}, zone=zone),
            "next": clock.as_utc(row.next_fire_at).astimezone(zone)
            if row.next_fire_at
            else None,
        }
        for row in rows
    ]


@router.get("/scheduled")
async def scheduled_list() -> Response:
    """The list's former page of its own. Kept, and still behind `schedule.use`, so
    a bookmark, the installed app's shortcut and an old notification land."""
    return RedirectResponse(LIST_URL, status_code=status.HTTP_303_SEE_OTHER)


@router.get("/scheduled/new")
async def new_schedule(request: Request, db: Db, user: RequiredUser, error: str = ""):
    """One question: what do you want to schedule?

    The detail comes from the compile. The manual form is on the same page
    behind a disclosure, so somebody who already knows exactly when it should
    run does not have to describe it in prose and hope.
    """
    return render(
        request,
        "schedules/new.html",
        {**_context(db, user, None, error=error), "compiled": None, "described": ""},
    )


@router.post("/api/schedules/describe")
async def describe_schedule(request: Request, db: Db, user: RequiredUser):
    """Work a plain-language request into a schedule, and show it back.

    Deliberately a *review* step rather than creating the schedule outright.
    The whole point of the compile is that a model chose the timing, and a
    timing nobody looked at is exactly the standing instruction this codebase
    refuses to create silently elsewhere.

    Nothing here can fail into an error page: a cleared fragment, an endpoint
    that is down, prose instead of JSON and a rule that means nothing all end at
    the same place, which is the form with the reader's own words in it and a
    line saying what to finish.
    """
    from lembas.services import prompts as prompts_service
    from lembas.services.schedule import compile as compile_service

    form = await request.form()
    described = str(form.get("request") or "").strip()

    template = prompts_service.resolve(db, "task.schedule_compile")
    resolved = compile_service.endpoint_for(db, user, str(form.get("model_id") or "").strip())
    if resolved is None:
        compiled = compile_service.Compiled(
            instruction=described,
            title=described[:80],
            reason=t("There is no model configured to work this out, so fill it in yourself."),
        )
    else:
        endpoint, model_id = resolved
        compiled = await compile_service.compile_request(
            endpoint, model_id, described, template=template, user=user
        )

    context = _context(db, user, None)
    # The compiled values become the form's values, so the reader edits what the
    # model proposed rather than being shown it beside an empty form.
    context["form"] = _form_values(compiled=compiled)
    return render(
        request,
        "schedules/new.html",
        {
            **context,
            "compiled": compiled,
            "described": described,
            "summary": rule_service.describe(compiled.rule, zone=clock.zone_for(user))
            if compiled.rule
            else "",
        },
    )


@router.get("/scheduled/{schedule_id}/edit")
async def edit_schedule(
    request: Request, db: Db, user: RequiredUser, schedule_id: str, error: str = ""
):
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    return render(request, "schedules/edit.html", _context(db, user, schedule, error=error))


# --- Writing --------------------------------------------------------------------
@router.post("/api/schedules")
async def create_schedule(request: Request, db: Db, user: RequiredUser) -> Response:
    form = await request.form()
    try:
        schedule = schedules_service.create(
            db,
            owner=user,
            title=str(form.get("title") or ""),
            instruction=str(form.get("instruction") or ""),
            request=str(form.get("instruction") or ""),
            rule=_rule_from_form(form),
            target=str(form.get("target") or TARGET_CHAT),
            model_id=str(form.get("model_id") or ""),
        )
    except schedules_service.ScheduleError as error:
        # Back to the form with the reason, rather than a 400 nobody can act on.
        return RedirectResponse(
            f"/scheduled/new?error={quote(error.translated())}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(f"/chat/{schedule.chat_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/schedules/{schedule_id}")
async def save_schedule(
    request: Request, db: Db, user: RequiredUser, schedule_id: str
) -> Response:
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    form = await request.form()
    try:
        schedules_service.update(
            db,
            schedule,
            owner=user,
            title=str(form.get("title") or ""),
            instruction=str(form.get("instruction") or ""),
            rule=_rule_from_form(form),
            target=str(form.get("target") or TARGET_CHAT),
        )
    except schedules_service.ScheduleError as error:
        return RedirectResponse(
            f"/scheduled/{schedule_id}/edit?error={quote(error.translated())}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(f"/chat/{schedule.chat_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/schedules/{schedule_id}/toggle")
async def toggle_schedule(
    db: Db, user: RequiredUser, schedule_id: str, enabled: str = Form("")
) -> Response:
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    schedules_service.set_enabled(
        db, schedule, owner=user, enabled=enabled not in ("", "0", "false")
    )
    return RedirectResponse(
        f"/chat/{schedule.chat_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/schedules/{schedule_id}/run")
async def run_schedule(db: Db, user: RequiredUser, schedule_id: str) -> Response:
    """Fire it now, without consuming the run it was scheduled for.

    `runner.run_now` is a different entry point from the ticker's for exactly
    that reason -- testing a schedule must not skip the real one.
    """
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    chat_id = schedule.chat_id
    await runner.run_now(schedule_id)
    return RedirectResponse(f"/chat/{chat_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/api/schedules/{schedule_id}/delete")
async def delete_schedule(
    db: Db, user: RequiredUser, schedule_id: str, keep_chat: str = Form("1")
) -> Response:
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    schedules_service.delete(db, schedule, keep_chat=keep_chat not in ("", "0", "false"))
    return RedirectResponse(LIST_URL, status_code=status.HTTP_303_SEE_OTHER)
