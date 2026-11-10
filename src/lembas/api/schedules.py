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
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.api.pages import sidebar_context
from lembas.db.models import (
    CATCH_UP_ONE,
    CATCH_UP_SKIP,
    MODE_CONTINUE,
    MODE_FRESH,
    NOTIFY_ALWAYS,
    NOTIFY_MATTERS,
    NOTIFY_NEVER,
    TARGET_CHAT,
    TARGET_REPORT,
    Schedule,
    ScheduleRun,
)
from lembas.services import chat as chat_service
from lembas.services import schedules as schedules_service
from lembas.services.schedule import clock, notify, runner, runs
from lembas.services.schedule import models as models_service
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
        (TARGET_REPORT, t("A file in your reports folder")),
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
        # "Its place in the month": the second Monday, the last Friday, the
        # last day. Replaces the weekdays and dates (`rule.validate` drops them).
        nth = str(form.get("nth_n") or "").strip()
        if nth.lstrip("-").isdigit() and int(nth):
            weekday = str(form.get("nth_weekday") or "").strip()
            if weekday == "day":
                raw["at"]["nth"] = [{"n": -1}]
            elif weekday.isdigit():
                raw["at"]["nth"] = [{"n": int(nth), "weekday": int(weekday)}]

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
        values.update(_settings_defaults(None, compiled.rule))
        return values
    values = _rule_defaults_from((schedule.rule_json if schedule else {}) or {})
    values.update(
        title=schedule.title if schedule else "",
        instruction=schedule.instruction if schedule else "",
        target=schedule.target if schedule else TARGET_CHAT,
    )
    values.update(_settings_defaults(schedule, (schedule.rule_json if schedule else {}) or {}))
    return values


def _settings_defaults(schedule: Schedule | None, rule: dict) -> dict:
    """The run settings the form shows: the stored ones, or the defaults."""
    if schedule is None:
        return {
            "model_id": "",
            "notify": notify.default_policy(rule) if rule else "",
            "mode": MODE_FRESH,
            "catch_up": CATCH_UP_ONE,
            "grace_minutes": "",
            "effort": "",
            "agents": "",
            "tools": list(runs.SAFE_TOOLS),
            "watch_url": "",
        }
    return {
        "model_id": schedule.model_id or "",
        "notify": notify.policy(schedule.notify, rule),
        "mode": schedule.mode or MODE_FRESH,
        "catch_up": schedule.catch_up or CATCH_UP_ONE,
        "grace_minutes": schedule.grace_minutes or "",
        "effort": schedule.effort or "",
        "agents": schedule.agents or "",
        "tools": list(runs.tools_for(schedule)),
        "watch_url": schedule.watch_target if schedule.watch_kind == "url" else "",
    }


def _settings_from_form(form) -> dict:
    """The run settings out of the form, for `schedules.apply_settings`."""
    settings = {
        "mode": str(form.get("mode") or ""),
        "catch_up": str(form.get("catch_up") or ""),
        "grace_minutes": str(form.get("grace_minutes") or ""),
        "effort": str(form.get("effort") or ""),
        "agents": str(form.get("agents") or ""),
        "tools": [str(t) for t in form.getlist("tools")],
        "watch": str(form.get("watch_url") or "").strip(),
    }
    if form.get("notify"):
        settings["notify"] = str(form.get("notify"))
    return settings


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
    nth = (at.get("nth") or [{}])[0]
    return {
        "repeat": repeat,
        "every_unit": unit,
        "every_amount": amount or 1,
        "nth_n": nth.get("n", "") if nth else "",
        "nth_weekday": (
            "day" if nth and "weekday" not in nth else nth.get("weekday", "") if nth else ""
        ),
        "weekdays": at.get("weekdays") or [],
        "times": ", ".join(at.get("times") or []),
        "month_days": ", ".join(str(d) for d in at.get("days") or []),
        "count": rule.get("count") or 0,
    }


def offered_notify() -> tuple[tuple[str, str], ...]:
    return (
        (NOTIFY_MATTERS, t("When it matters")),
        (NOTIFY_ALWAYS, t("Every run")),
        (NOTIFY_NEVER, t("Never")),
    )


def offered_modes() -> tuple[tuple[str, str], ...]:
    return (
        (MODE_FRESH, t("A fresh chat each time")),
        (MODE_CONTINUE, t("One chat that keeps growing")),
    )


def offered_catch_up() -> tuple[tuple[str, str], ...]:
    return (
        (CATCH_UP_ONE, t("Run once when it can")),
        (CATCH_UP_SKIP, t("Skip it")),
    )


def offered_tools() -> tuple[tuple[str, str], ...]:
    """The tool families a schedule may switch on, with what each is called on
    the page. The safe ones first; scheduling, questions and Python never."""
    return (
        ("files", t("Files")),
        ("recall", t("Earlier results")),
        ("search", t("Web search")),
        ("fetch", t("Reading web pages")),
        ("memory", t("Memory")),
        ("skills", t("Skills")),
        ("plan", t("Planning")),
        ("image", t("Pictures")),
        ("custom", t("Custom tools")),
        ("mcp", t("MCP servers")),
        ("subagent", t("Subagents")),
    )


def _levels(models) -> list[str]:
    """Every level any of these models offers, in the canonical order."""
    found: set[str] = set()
    for model in models:
        found.update(chat_service.levels_for(model).offered)
    order = (chat_service.LEVEL_OFF, *chat_service.EFFORTS)
    return [level for level in order if level in found]


def _team_offered(db, user, models) -> bool:
    from lembas.services import team as team_rules
    from lembas.services import team_run

    # A run's subagents run on its own model: nobody is there to name another
    # (harness spec 3.3.0, subagents.md §2). So its model must serve two at
    # once -- or state no limit, as a hosted API does.
    return any(
        team_run.subagents_allowed(db, user, m)
        and team_rules.capacity_allows(team_run.model_parallel(m))
        for m in models
    )


def _context(db, user, schedule: Schedule | None, *, error: str = "") -> dict:
    models = models_service.offered_models(db, user)
    return {
        "section": "scheduled",
        "schedule": schedule,
        "targets": offered_targets(),
        "notify_choices": offered_notify(),
        "mode_choices": offered_modes(),
        "catch_up_choices": offered_catch_up(),
        "tool_choices": offered_tools(),
        "weekday_names": list(
            enumerate(("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
        ),
        "weekday_long": list(enumerate((
            t("Monday"), t("Tuesday"), t("Wednesday"), t("Thursday"),
            t("Friday"), t("Saturday"), t("Sunday"),
        ))),
        "form": _form_values(schedule=schedule),
        "error": error,
        "models": models,
        "levels": _levels(models),
        "team_offered": _team_offered(db, user, models),
        "timezone": clock.name_for(user) or str(clock.server_zone()),
        "preview": _preview_for(schedule.rule_json if schedule else {}, user, schedule)
        if schedule
        else None,
        **sidebar_context(db, user),
    }


def _preview_for(rule: dict, user, schedule: Schedule | None = None) -> dict:
    """The rule in words and its next five runs, in the reader's zone: what
    every form shows before saving."""
    zone = clock.zone_for(user)
    clean = rule_service.validate(rule or {})
    after = datetime.now(UTC)
    fired = 0
    if schedule is not None and schedule.next_fire_at and schedule.enabled:
        after = clock.as_utc(schedule.next_fire_at) - timedelta(microseconds=1)
        fired = schedule.fired_count or 0
    nexts = rule_service.upcoming(clean, now=after, zone=zone, fired=fired) if clean else []
    return {
        "summary": rule_service.describe(clean, zone=zone) if clean else "",
        "next": [m.astimezone(zone) for m in nexts],
        "valid": bool(clean and nexts),
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
    latest = runs.latest_by_schedule(db, [row.id for row in rows])
    return [
        {
            "row": row,
            "summary": rule_service.describe(row.rule_json or {}, zone=zone),
            "next": clock.as_utc(row.next_fire_at).astimezone(zone)
            if row.next_fire_at
            else None,
            "last": latest.get(row.id),
            "last_label": status_label(latest[row.id].status) if row.id in latest else "",
            "last_badge": status_badge(latest[row.id].status) if row.id in latest else "",
        }
        for row in rows
    ]


def status_label(status: str) -> str:
    """A run's status, as the history and the list say it."""
    return {
        "running": t("Running"),
        "waiting": t("Waiting"),
        "ok": t("Done"),
        "silent": t("Nothing new"),
        "unchanged": t("Unchanged"),
        "failed": t("Failed"),
        "skipped": t("Skipped"),
        "missed": t("Missed"),
    }.get(status, status)


def status_badge(status: str) -> str:
    """The badge variant a status is drawn in."""
    return {
        "ok": "badge--success",
        "failed": "badge--danger",
        "skipped": "badge--warning",
        "missed": "badge--warning",
        "waiting": "badge--warning",
        "running": "badge--leaf",
    }.get(status, "")


def timing_label(timing: str) -> str:
    return {
        "on_time": t("on time"),
        "late": t("late"),
        "catch_up": t("caught up"),
        "manual": t("run by hand"),
    }.get(timing, timing)


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
            "preview": _preview_for(compiled.rule, user) if compiled.rule else None,
        },
    )


@router.post("/api/schedules/preview")
async def preview_schedule(request: Request, db: Db, user: RequiredUser):
    """The form's rule read back -- in words, and its next five runs -- before
    anything is saved. A fragment, swapped into the form."""
    form = await request.form()
    return render(
        request,
        "schedules/_preview.html",
        {"preview": _preview_for(_rule_from_form(form), user)},
    )


@router.post("/api/schedules/models")
async def choose_models(request: Request, db: Db, user: RequiredUser) -> Response:
    """This person's narrowing of the scheduling models (Settings → Scheduled)."""
    form = await request.form()
    offered = {m.model_id for m in models_service.offered_models(db, user)}
    chosen = [str(m) for m in form.getlist("models") if str(m) in offered]
    user.settings_json = {**(user.settings_json or {}), models_service.PERSON_KEY: chosen}
    db.commit()
    return RedirectResponse(LIST_URL + "&saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/scheduled/{schedule_id}")
async def show_schedule(
    request: Request,
    db: Db,
    user: RequiredUser,
    schedule_id: str,
    run: str = "",
    before: str = "",
):
    """One schedule: when, how, and what happened each time (the history)."""
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    page, more = runs.history(db, schedule, limit=20, before=before)
    zone = clock.zone_for(user)
    picked = next((r for r in page if r.id == run), None)
    if run and picked is None:
        found = db.get(ScheduleRun, run)
        picked = found if found is not None and found.schedule_id == schedule.id else None
        if picked is not None:
            page = [picked, *page]
    return render(
        request,
        "schedules/show.html",
        {
            **_context(db, user, schedule),
            "runs": page,
            "more": more,
            "picked": picked.id if picked is not None else "",
            "zone": zone,
            "as_local": lambda moment: clock.as_utc(moment).astimezone(zone) if moment else None,
            "status_label": status_label,
            "status_badge": status_badge,
            "timing_label": timing_label,
            "notify_label": dict(offered_notify()),
            "mode_label": dict(offered_modes()),
            "catch_up_label": dict(offered_catch_up()),
            "tool_label": dict(offered_tools()),
            "target_label": dict(offered_targets()),
            "tools_on": runs.tools_for(schedule),
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
            settings=_settings_from_form(form),
        )
    except schedules_service.ScheduleError as error:
        # Back to the form with the reason, rather than a 400 nobody can act on.
        return RedirectResponse(
            f"/scheduled/new?error={quote(error.translated())}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(f"/scheduled/{schedule.id}", status_code=status.HTTP_303_SEE_OTHER)


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
            model_id=str(form.get("model_id") or ""),
            settings=_settings_from_form(form),
        )
    except schedules_service.ScheduleError as error:
        return RedirectResponse(
            f"/scheduled/{schedule_id}/edit?error={quote(error.translated())}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(f"/scheduled/{schedule.id}", status_code=status.HTTP_303_SEE_OTHER)


def _back(where: str, fallback: str) -> str:
    """Where a form asked to go back to: one of our own pages, never elsewhere."""
    where = (where or "").strip()
    if where.startswith(("/scheduled/", "/settings", "/chat/")) and "//" not in where:
        return where
    return fallback


@router.post("/api/schedules/{schedule_id}/toggle")
async def toggle_schedule(
    db: Db,
    user: RequiredUser,
    schedule_id: str,
    enabled: str = Form(""),
    back: str = Form(""),
) -> Response:
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    schedules_service.set_enabled(
        db, schedule, owner=user, enabled=enabled not in ("", "0", "false")
    )
    return RedirectResponse(
        _back(back, f"/scheduled/{schedule.id}"), status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/schedules/{schedule_id}/pause", status_code=status.HTTP_204_NO_CONTENT)
async def pause_schedule(db: Db, user: RequiredUser, schedule_id: str) -> Response:
    """Pause, with nothing to show: a notification's Pause action, sent by the
    service worker with the person's cookie."""
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    schedules_service.set_enabled(db, schedule, owner=user, enabled=False)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/schedules/{schedule_id}/run")
async def run_schedule(db: Db, user: RequiredUser, schedule_id: str) -> Response:
    """Fire it now, without consuming the run it was scheduled for.

    `runner.run_now` is a different entry point from the ticker's for exactly
    that reason -- testing a schedule must not skip the real one.
    """
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    # Started, not awaited: a run is followed to its end, and a browser request
    # held open that long is a page that appears to hang. The schedule's page
    # shows the run from the moment it exists.
    run_id = runner.start_now(schedule.id)
    return RedirectResponse(
        f"/scheduled/{schedule.id}?run={run_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/schedules/{schedule_id}/delete")
async def delete_schedule(
    db: Db, user: RequiredUser, schedule_id: str, keep_chat: str = Form("1")
) -> Response:
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    schedules_service.delete(db, schedule, keep_chat=keep_chat not in ("", "0", "false"))
    return RedirectResponse(LIST_URL, status_code=status.HTTP_303_SEE_OTHER)
