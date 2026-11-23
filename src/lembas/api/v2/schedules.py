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
| `DELETE /schedules/{id}` | 204: its run chats go, its reports stay (`keep_chat`: ignored) |
| `POST /schedules/{id}/pause`, `/resume` | `{"schedule"}` |
| `POST /schedules/{id}/run` | 202 `{"run"}`: started now, not counted against the rule |
| `GET /schedules/{id}/runs?before=&limit=` | `{"runs": [run], "more"}`, newest first |
| `GET /schedules/{id}/runs/{run}` | `{"run", "text"}`: the result (Markdown, model output) |
| `POST /schedules/compile` | `{text, model?}` → what the words mean, and the next runs |
| `POST /schedules/preview` | `{rule}` → the rule cleaned, in words, and the next runs |
| `GET /schedules/models` | the scheduling models, in order, and which are free now |
| `PUT /schedules/models` | `{"models": [id]}`: the person's narrowing of the list |
| `GET /events` | server-sent events: `schedules` whenever the list changes, `reports`,
  and `reply` whenever a reply starts in one of the person's chats |

Monitor mode: `watch` among the settings -- an address (`"https://…"`), or
`{"kind": "url", "target"}`, or `""` to stop watching. A run whose look found
nothing changed ends `unchanged`, with no model run and no notification.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import Me, MeOrDevice, calling_device, need
from lembas.db.models import Message, Schedule, ScheduleRun, User
from lembas.db.session import session_scope
from lembas.services import reports as reports_service
from lembas.services import schedules as schedules_service
from lembas.services import sse
from lembas.services.schedule import clock, runner, runs
from lembas.services.schedule import models as models_service
from lembas.services.schedule import rule as rule_service
from lembas.web.i18n import t

router = APIRouter()

PERMISSION = "schedule.use"
EVENT_POLL_SECONDS = 2.0
# How soon a reply that started is said on the person's stream.
REPLY_POLL_SECONDS = 0.25
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
async def list_schedules(caller: MeOrDevice, db: Db) -> dict[str, Any]:
    need(db, caller.user, PERMISSION)
    return {"schedules": _all(db, caller.user)}


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
async def create_schedule(caller: MeOrDevice, db: Db, body: dict[str, Any] = Body(...)) -> dict:
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
            calling_device=calling_device(caller),
        )
    except schedules_service.ScheduleError as exc:
        raise _refuse(exc) from exc
    return {"schedule": _one(db, schedule, caller.user)}


# Static paths before `/schedules/{schedule_id}` (route order matters).
@router.post("/schedules/compile")
async def compile_words(caller: MeOrDevice, db: Db, body: dict[str, Any] = Body(...)) -> dict:
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
async def preview(caller: MeOrDevice, db: Db, body: dict[str, Any] = Body(...)) -> dict:
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
async def get_schedule(caller: MeOrDevice, db: Db, schedule_id: str) -> dict[str, Any]:
    need(db, caller.user, PERMISSION)
    return {"schedule": _one(db, _owned(db, schedule_id, caller.user), caller.user)}


@router.patch("/schedules/{schedule_id}")
async def update_schedule(
    caller: MeOrDevice, db: Db, schedule_id: str, body: dict[str, Any] = Body(...)
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
            calling_device=calling_device(caller),
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
    caller: MeOrDevice, db: Db, schedule_id: str, keep_chat: bool = Query(True)
) -> Response:
    need(db, caller.user, PERMISSION)
    # `keep_chat` did something before 2.0.0-beta.12; clients still send it.
    del keep_chat
    schedules_service.delete(db, _owned(db, schedule_id, caller.user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/schedules/{schedule_id}/pause")
async def pause(caller: MeOrDevice, db: Db, schedule_id: str) -> dict[str, Any]:
    need(db, caller.user, PERMISSION)
    schedule = _owned(db, schedule_id, caller.user)
    schedules_service.set_enabled(db, schedule, owner=caller.user, enabled=False)
    return {"schedule": _one(db, schedule, caller.user)}


@router.post("/schedules/{schedule_id}/resume")
async def resume(caller: MeOrDevice, db: Db, schedule_id: str) -> dict[str, Any]:
    need(db, caller.user, PERMISSION)
    schedule = _owned(db, schedule_id, caller.user)
    schedules_service.set_enabled(db, schedule, owner=caller.user, enabled=True)
    return {"schedule": _one(db, schedule, caller.user)}


@router.post("/schedules/{schedule_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_now(caller: MeOrDevice, db: Db, schedule_id: str) -> dict[str, Any]:
    """Run it now. The next scheduled run is not used up by this one."""
    need(db, caller.user, PERMISSION)
    schedule = _owned(db, schedule_id, caller.user)
    run_id = runner.start_now(schedule.id)
    db.expire_all()
    row = db.get(ScheduleRun, run_id)
    return {"run": runs.run_dict(row)}


@router.get("/schedules/{schedule_id}/runs")
async def list_runs(
    caller: MeOrDevice,
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
async def get_run(caller: MeOrDevice, db: Db, schedule_id: str, run_id: str) -> dict[str, Any]:
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
def reports_frame(
    unread: int, newest, *, schedule_title: str | None = None, latest_created: str | None = None
) -> str:
    """The `reports` event: `{"unread": n, "newest": report | null,
    "latest_created": time | null}`.

    `newest` is the newest **unread** report, in the list shape of
    `GET /reports` -- so it moves *back* to an older one when the newest is
    read, and is not by itself news. `latest_created` is when the newest
    report of any state was filed: **a report has arrived when
    `latest_created` is later than the one the client last saw**, and only
    then is `newest` worth announcing. Null while there is none."""
    from lembas.api.v2 import serialise

    return json.dumps(
        {"unread": int(unread),
         "newest": serialise.report(newest, schedule_title) if newest is not None else None,
         "latest_created": latest_created},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def version_of(payload: list[dict[str, Any]]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def reply_frame(chat_id: str, message_id: str, live: bool) -> str:
    """The `reply` event: `{"chat_id", "message_id", "live"}` -- a reply has
    started in that chat, whoever started it. `live`: it is being written
    now (follow `GET /chats/{chat_id}/messages/{message_id}/stream`); false:
    it has already ended, and the chat is to be read again."""
    return json.dumps(
        {"chat_id": chat_id, "message_id": message_id, "live": bool(live)},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _own_replies(db, user_id: str, replies: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Those of the replies that are in this person's own chats, in the order
    given. Not a subagent's or a team member's chat (`parent_chat_id`): no
    client lists or opens those as chats, and what they do is the activity
    panel's news (`/chats/{chat}/activity/events`)."""
    from lembas.db.models import Chat

    ids = {chat_id for chat_id, _ in replies}
    if not ids:
        return []
    mine = set(
        db.scalars(
            select(Chat.id).where(
                Chat.id.in_(ids), Chat.user_id == user_id, Chat.parent_chat_id.is_(None)
            )
        )
    )
    return [(chat_id, message_id) for chat_id, message_id in replies if chat_id in mine]


# How often an open events stream asks again whether its caller may still be
# told anything: the token it was opened with revoked or expired, the account
# switched off or deleted. A stream outlives the request that was checked, and
# says which chats are writing and under which ids.
RECHECK_SECONDS = 30.0


def _still_allowed(user_id: str, token_id: str) -> bool:
    """Whether the stream's caller would be let in if they asked now -- the
    same two questions `api_tokens.resolve` asks of every request."""
    from lembas.db.models import ApiToken
    from lembas.security import api_tokens

    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None or not user.active:
            return False
        return not token_id or api_tokens.live(db.get(ApiToken, token_id))


async def _events(
    user_id: str, *, scheduled: bool = True, token_id: str = ""
) -> AsyncIterator[str]:
    """`schedules` with the whole list whenever it changes -- a schedule made,
    paused or deleted, a run starting, waiting or finishing. Whole, as every
    frame here is: a client back from the background needs no bookkeeping.

    And `reply`, the one frame that is an event and not a state: a reply has
    started in one of the person's chats (`live` true), or is over (`live`
    false -- sent when it ends, however it ends, once). A client with that chat open and
    idle has no other way to hear of a reply it did not ask for -- a schedule
    firing into the chat, a finished job's note, a subagent's wake, a turn a
    linked device started by itself. Every reply is said, the client's own
    too (it knows those by their message id); the ones being written when the
    stream opens are said at once. `scheduled` false (no `schedule.use`):
    `reply` only."""
    from lembas.services import generation as generation_service

    sent = ""
    reports_sent = ""
    quiet = 0.0
    # Replies: those running now first, then each as `generation.ensure`
    # numbers it. The cursor is taken before the running ones are read, so one
    # starting in between is in both, and `told` keeps it from being said twice.
    cursor = generation_service.started()
    with session_scope() as db:
        current = _own_replies(db, user_id, generation_service.running())
    for chat_id, message_id in current:
        yield sse.event("reply", reply_frame(chat_id, message_id, True))
    told = set(current)
    # ... and each as it ends: the same frame with `live` false, once. A reply
    # already said to be over (it had ended by the time its start was said)
    # is not said over a second time.
    end_cursor = generation_service.ended()
    over: set[tuple[str, str]] = set()
    checked = 0.0
    while True:
        if checked >= RECHECK_SECONDS:
            # Signed out, revoked or switched off since the stream opened:
            # it ends here, and the client's reconnect gets the 401.
            checked = 0.0
            if not _still_allowed(user_id, token_id):
                return
        fresh = generation_service.started_after(cursor)
        if fresh:
            cursor = fresh[-1][0]
            with session_scope() as db:
                mine = _own_replies(db, user_id, [(c, m) for _, c, m in fresh])
            for chat_id, message_id in mine:
                if (chat_id, message_id) in told:
                    continue
                running = generation_service.get(message_id)
                quiet = 0.0
                live = running is not None and not running.done
                if not live:
                    over.add((chat_id, message_id))
                yield sse.event("reply", reply_frame(chat_id, message_id, live))
            told.clear()
        finished = generation_service.ended_after(end_cursor)
        if finished:
            end_cursor = finished[-1][0]
            with session_scope() as db:
                mine = _own_replies(db, user_id, [(c, m) for _, c, m in finished])
            for chat_id, message_id in mine:
                if (chat_id, message_id) in over:
                    over.discard((chat_id, message_id))
                    continue
                quiet = 0.0
                yield sse.event("reply", reply_frame(chat_id, message_id, False))
            if len(over) > generation_service.STARTS_KEPT:
                over.clear()
        if not scheduled:
            if quiet >= KEEPALIVE_SECONDS:
                quiet = 0.0
                yield sse.KEEPALIVE
            waited = await _pause(cursor, end_cursor)
            quiet, checked = quiet + waited, checked + waited
            continue
        with session_scope() as db:
            user = db.get(User, user_id)
            if user is None:
                return
            listed = _all(db, user)
            unread = reports_service.unread_count(db, user_id)
            newest = reports_service.listing(db, user, unread=True, limit=1)
            newest_title = (
                reports_service.schedule_title(db, user, newest[0].schedule_id)
                if newest else None
            )
            latest = reports_service.latest_created(db, user_id)
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
        # `reports` (harness spec 3.8.0): how many are unread and the newest
        # unread one, whenever either changes -- the first at once -- so an
        # app can badge its Reports entry and say that one arrived.
        said = reports_frame(
            unread, newest[0] if newest else None, schedule_title=newest_title,
            latest_created=serialise.stamp(latest) if latest else None,
        )
        if said != reports_sent:
            reports_sent = said
            quiet = 0.0
            yield sse.event("reports", said)
        elif quiet >= KEEPALIVE_SECONDS:
            quiet = 0.0
            yield sse.KEEPALIVE
        waited = await _pause(cursor, end_cursor)
        quiet, checked = quiet + waited, checked + waited


async def _pause(cursor: int, end_cursor: int = -1) -> float:
    """Wait for the next look at the lists -- EVENT_POLL_SECONDS, or less when
    a reply starts meanwhile: that is said within REPLY_POLL_SECONDS, because
    a chat that is open should not sit still for two seconds while its reply
    is already being written. Returns how long it waited."""
    from lembas.services import generation as generation_service

    waited = 0.0
    while waited < EVENT_POLL_SECONDS:
        await asyncio.sleep(REPLY_POLL_SECONDS)
        waited += REPLY_POLL_SECONDS
        if generation_service.started() > cursor:
            break
        if end_cursor >= 0 and generation_service.ended() > end_cursor:
            break
    return waited


@router.get("/events")
async def events(caller: Me, db: Db) -> StreamingResponse:
    """Server-sent events for this person, outside any chat. `schedules`
    (`{"version", "schedules": [schedule]}`), sent first at once and then
    whenever `version` moves; and `reports` (`{"unread": n, "newest": report |
    null}` -- the newest unread one, in the list shape of `GET /reports`),
    sent at once and whenever either changes; and `reply` (`{"chat_id",
    "message_id", "live"}`), whenever a reply starts in one of the person's
    chats, whoever started it. Without `schedule.use` the stream stays open
    and says only `reply`, so a client needs one code path."""
    from lembas.security import permissions

    allowed = permissions.has(db, caller.user, PERMISSION)
    user_id = caller.user.id

    return StreamingResponse(
        _events(user_id, scheduled=allowed, token_id=getattr(caller.token, "id", "") or ""),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["reply_frame", "router", "version_of"]
