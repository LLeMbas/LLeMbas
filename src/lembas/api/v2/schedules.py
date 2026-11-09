"""Scheduled work, for the apps: the schedules, their runs, the compile and the
preview, the scheduling models, and a person-level event stream.

The same core the web pages use (`services/schedules.py` writes a schedule,
`services/schedule/runner.py` runs one, `services/schedule/runs.py` says both),
so a rule cannot hold through one door and not the other. Every route needs
`schedule.use`, as the pages do.

| Route | |
|---|---|
| `GET /schedules` | `{"schedules": [schedule]}`, newest first |
| `POST /schedules` | `{title, instruction, rule, …settings}` → 201 `{"schedule"}` |
| `GET /schedules/{id}` | `{"schedule"}` |
| `PATCH /schedules/{id}` | any of the create fields, and `enabled` → `{"schedule"}` |
| `DELETE /schedules/{id}?keep_chat=true` | 204 |
| `POST /schedules/{id}/pause`, `/resume` | `{"schedule"}` |
| `POST /schedules/{id}/run` | 202 `{"run"}`: started now, not counted against the rule |
| `GET /schedules/{id}/runs?before=&limit=` | `{"runs": [run], "more"}`, newest first |
| `GET /schedules/{id}/runs/{run}` | `{"run", "text"}`: the result (Markdown, model output) |
| `POST /schedules/compile` | `{text, model?}` → what the words mean, and the next runs |
| `POST /schedules/preview` | `{rule}` → the rule cleaned, in words, and the next runs |
| `GET /schedules/models` | the scheduling models, in order, and which are free now |
| `PUT /schedules/models` | `{"models": [id]}`: the person's narrowing of the list |
| `GET /events` | server-sent events: `schedules` whenever the list changes |
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse

from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import Me, need
from lembas.db.models import Message, Schedule, ScheduleRun, User
from lembas.db.session import session_scope
from lembas.services import schedules as schedules_service
from lembas.services import sse
from lembas.services.schedule import clock, runner, runs
from lembas.services.schedule import models as models_service
from lembas.services.schedule import rule as rule_service
from lembas.web.i18n import t

router = APIRouter()

PERMISSION = "schedule.use"
EVENT_POLL_SECONDS = 2.0
KEEPALIVE_SECONDS = 15.0


def _owned(db, schedule_id: str, user: User) -> Schedule:
    schedule = schedules_service.get(db, schedule_id, user)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That schedule is not available."))
    return schedule


def _one(db, schedule: Schedule, user: User) -> dict[str, Any]:
    return runs.schedule_dict(db, schedule, owner=user, last=runs.latest(db, schedule))


def _all(db, user: User) -> list[dict[str, Any]]:
    rows = list(db.scalars(schedules_service.visible(user).order_by(Schedule.created_at.desc())))
    latest = runs.latest_by_schedule(db, [r.id for r in rows])
    return [runs.schedule_dict(db, r, owner=user, last=latest.get(r.id)) for r in rows]


def _settings(body: dict[str, Any]) -> dict[str, Any]:
    return {key: body[key] for key in schedules_service.SETTING_KEYS if key in body}


def _refuse(exc: schedules_service.ScheduleError) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, exc.translated())


@router.get("/schedules")
async def list_schedules(caller: Me, db: Db) -> dict[str, Any]:
    need(db, caller.user, PERMISSION)
    return {"schedules": _all(db, caller.user)}


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict:
    need(db, caller.user, PERMISSION)
    rule = body.get("rule")
    try:
        schedule = schedules_service.create(
            db,
            owner=caller.user,
            title=str(body.get("title") or ""),
            instruction=str(body.get("instruction") or ""),
            request=str(body.get("request") or body.get("instruction") or ""),
            rule=rule if isinstance(rule, dict) else {},
            target=str(body.get("target") or "chat"),
            model_id=str(body.get("model") or ""),
            settings=_settings(body),
        )
    except schedules_service.ScheduleError as exc:
        raise _refuse(exc) from exc
    return {"schedule": _one(db, schedule, caller.user)}


# Static paths before `/schedules/{schedule_id}` (route order matters).
@router.post("/schedules/compile")
async def compile_words(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict:
    """Words → a schedule: the rule, the rule in words, the next runs, and the
    title and instruction the compile wrote. `ok` false: fill in the rest
    yourself (`reason` says why); the fields are filled as far as they went."""
    from lembas.services import prompts as prompts_service
    from lembas.services.schedule import compile as compile_service

    need(db, caller.user, PERMISSION)
    text = str(body.get("text") or "").strip()
    template = prompts_service.resolve(db, "task.schedule_compile")
    resolved = compile_service.endpoint_for(db, caller.user, str(body.get("model") or ""))
    if not text:
        compiled = compile_service.Compiled(reason=t("Say what you want to happen."))
    elif resolved is None:
        compiled = compile_service.Compiled(
            instruction=text,
            title=text[:80],
            reason=t("There is no model configured to work this out, so fill it in yourself."),
        )
    else:
        endpoint, model_id = resolved
        compiled = await compile_service.compile_request(
            endpoint, model_id, text, template=template, user=caller.user
        )
    return {
        "ok": compiled.ok,
        "reason": t(compiled.reason) if compiled.reason else None,
        "title": compiled.title,
        "instruction": compiled.instruction,
        "target": compiled.target,
        **_preview(compiled.rule, caller.user),
    }


def _preview(raw: object, user: User) -> dict[str, Any]:
    from datetime import UTC, datetime

    clean = rule_service.validate(raw)
    zone = clock.zone_for(user)
    nexts = rule_service.upcoming(clean, now=datetime.now(UTC), zone=zone) if clean else []
    return {
        "rule": clean,
        "description": rule_service.describe(clean, zone=zone) if clean else None,
        "next_runs": [clock.as_utc(m).isoformat() for m in nexts],
        "timezone": clock.name_for(user) or str(clock.server_zone()),
    }


@router.post("/schedules/preview")
async def preview(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict:
    """A rule as a form has it, cleaned and read back: what every form shows
    before saving. An empty `rule` in the answer: it cannot run."""
    need(db, caller.user, PERMISSION)
    return _preview(body.get("rule"), caller.user)


async def _models(db, user: User) -> dict[str, Any]:
    offered = models_service.offered_models(db, user)
    mine = set(models_service.person_list(user))
    counted = models_service.ours(db)
    swaps: dict[str, Any] = {}
    out = []
    for row in offered:
        why = await models_service.busy_reason(row, counted, swaps)
        out.append(
            {
                **serialise.model(row),
                "free": not why,
                "busy": why or None,
                "chosen": not mine or row.model_id in mine,
            }
        )
    return {
        "models": out,
        "instance": models_service.instance_list(db),
        "mine": models_service.person_list(user),
    }


@router.get("/schedules/models")
async def scheduling_models(caller: Me, db: Db) -> dict[str, Any]:
    """The models scheduled work may run on, in the order they are tried, and
    which are free right now. `chosen`: in this person's narrowing (all of
    them when they made none)."""
    need(db, caller.user, PERMISSION)
    return await _models(db, caller.user)


@router.put("/schedules/models")
async def choose_models(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict:
    need(db, caller.user, PERMISSION)
    wanted = body.get("models")
    offered = {row.model_id for row in models_service.offered_models(db, caller.user)}
    chosen = [str(m) for m in wanted if str(m) in offered] if isinstance(wanted, list) else []
    user = caller.user
    user.settings_json = {**(user.settings_json or {}), models_service.PERSON_KEY: chosen}
    db.commit()
    return await _models(db, user)


@router.get("/schedules/{schedule_id}")
async def get_schedule(caller: Me, db: Db, schedule_id: str) -> dict[str, Any]:
    need(db, caller.user, PERMISSION)
    return {"schedule": _one(db, _owned(db, schedule_id, caller.user), caller.user)}


@router.patch("/schedules/{schedule_id}")
async def update_schedule(
    caller: Me, db: Db, schedule_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    need(db, caller.user, PERMISSION)
    schedule = _owned(db, schedule_id, caller.user)
    rule = body.get("rule")
    try:
        schedules_service.update(
            db,
            schedule,
            owner=caller.user,
            title=str(body["title"]) if "title" in body else None,
            instruction=str(body["instruction"]) if "instruction" in body else None,
            rule=rule if isinstance(rule, dict) else None,
            target=str(body["target"]) if "target" in body else None,
            model_id=str(body.get("model") or "") if "model" in body else None,
            settings=_settings(body),
        )
        if "enabled" in body:
            schedules_service.set_enabled(
                db, schedule, owner=caller.user, enabled=bool(body["enabled"])
            )
    except schedules_service.ScheduleError as exc:
        raise _refuse(exc) from exc
    return {"schedule": _one(db, schedule, caller.user)}


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    caller: Me, db: Db, schedule_id: str, keep_chat: bool = Query(True)
) -> Response:
    need(db, caller.user, PERMISSION)
    schedules_service.delete(db, _owned(db, schedule_id, caller.user), keep_chat=keep_chat)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/schedules/{schedule_id}/pause")
async def pause(caller: Me, db: Db, schedule_id: str) -> dict[str, Any]:
    need(db, caller.user, PERMISSION)
    schedule = _owned(db, schedule_id, caller.user)
    schedules_service.set_enabled(db, schedule, owner=caller.user, enabled=False)
    return {"schedule": _one(db, schedule, caller.user)}


@router.post("/schedules/{schedule_id}/resume")
async def resume(caller: Me, db: Db, schedule_id: str) -> dict[str, Any]:
    need(db, caller.user, PERMISSION)
    schedule = _owned(db, schedule_id, caller.user)
    schedules_service.set_enabled(db, schedule, owner=caller.user, enabled=True)
    return {"schedule": _one(db, schedule, caller.user)}


@router.post("/schedules/{schedule_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_now(caller: Me, db: Db, schedule_id: str) -> dict[str, Any]:
    """Run it now. The next scheduled run is not used up by this one."""
    need(db, caller.user, PERMISSION)
    schedule = _owned(db, schedule_id, caller.user)
    run_id = runner.start_now(schedule.id)
    db.expire_all()
    row = db.get(ScheduleRun, run_id)
    return {"run": runs.run_dict(row)}


@router.get("/schedules/{schedule_id}/runs")
async def list_runs(
    caller: Me,
    db: Db,
    schedule_id: str,
    before: str = Query(""),
    limit: int = Query(20),
) -> dict[str, Any]:
    need(db, caller.user, PERMISSION)
    schedule = _owned(db, schedule_id, caller.user)
    page, more = runs.history(db, schedule, limit=max(1, min(int(limit), 100)), before=before)
    return {"runs": [runs.run_dict(r) for r in page], "more": more}


@router.get("/schedules/{schedule_id}/runs/{run_id}")
async def get_run(caller: Me, db: Db, schedule_id: str, run_id: str) -> dict[str, Any]:
    """One run, and its result in full: Markdown written by a model, to be
    rendered as untrusted text. `text` is null when there is none."""
    need(db, caller.user, PERMISSION)
    schedule = _owned(db, schedule_id, caller.user)
    row = db.get(ScheduleRun, run_id)
    if row is None or row.schedule_id != schedule.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That run is not available."))
    message = db.get(Message, row.message_id) if row.message_id else None
    text = message.content if message is not None and message.complete else None
    return {"run": runs.run_dict(row), "text": text}


# --- The person's event stream -------------------------------------------------------------
def version_of(payload: list[dict[str, Any]]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


async def _events(user_id: str) -> AsyncIterator[str]:
    """`schedules` with the whole list whenever it changes -- a schedule made,
    paused or deleted, a run starting, waiting or finishing. Whole, as every
    frame here is: a client back from the background needs no bookkeeping."""
    sent = ""
    quiet = 0.0
    while True:
        with session_scope() as db:
            user = db.get(User, user_id)
            if user is None:
                return
            listed = _all(db, user)
        version = version_of(listed)
        if version != sent:
            sent = version
            quiet = 0.0
            yield sse.event(
                "schedules",
                json.dumps(
                    {"version": version, "schedules": listed},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        elif quiet >= KEEPALIVE_SECONDS:
            quiet = 0.0
            yield sse.KEEPALIVE
        await asyncio.sleep(EVENT_POLL_SECONDS)
        quiet += EVENT_POLL_SECONDS


@router.get("/events")
async def events(caller: Me, db: Db) -> StreamingResponse:
    """Server-sent events for this person, outside any chat. Today: `schedules`
    (`{"version", "schedules": [schedule]}`), sent first at once and then
    whenever `version` moves. Without `schedule.use` the stream stays open and
    says nothing, so a client needs one code path."""
    from lembas.security import permissions

    allowed = permissions.has(db, caller.user, PERMISSION)
    user_id = caller.user.id

    async def silent() -> AsyncIterator[str]:
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            yield sse.KEEPALIVE

    return StreamingResponse(
        _events(user_id) if allowed else silent(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["router", "version_of"]
