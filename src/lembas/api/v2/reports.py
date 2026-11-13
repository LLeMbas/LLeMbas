"""Reports, for an app (harness spec 3.8.0, `reports.md` §7).

What a schedule's run filed for its owner: listed, read, marked read, deleted,
and continued as a conversation that stays with the report. The same store as
the page's (`services/reports.py`); a report is its owner's and somebody
else's is a 404, as one that does not exist is.

| Route | |
|---|---|
| `GET /reports?unread=&schedule=` | `{"reports": [...], "unread": n}`, unread first |
| `GET /reports/{id}` | `{"report": {...}}` with `body`, `text`, `pages`, `model`, `chat` |
| `POST /reports/{id}/read` | marks it read → `{"report": {...}}` |
| `DELETE /reports/{id}` | 204; the conversation it continued in goes with it |
| `POST /reports/{id}/reply` | `{"chat": id}` -- the chat it continues in, made once |

The list and a single report are open to a linked device's token too (the
CLI's `/reports`); the permission is `schedule.use`. The chat a reply opens
is read and written through the chat routes by its id; it is never in
`GET /chats`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import MeOrDevice, need
from lembas.db.models import Report, Schedule
from lembas.services import reports as reports_service
from lembas.web.i18n import t

router = APIRouter()


def _owned(db, caller, report_id: str) -> Report:
    need(db, caller.user, "schedule.use")
    row = reports_service.owned(db, caller.user, report_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That report no longer exists."))
    return row


def _titles(db, user) -> dict[str, str]:
    from sqlalchemy import select

    return {
        s.id: s.title or ""
        for s in db.scalars(select(Schedule).where(Schedule.user_id == user.id))
    }


def _one(db, caller, row: Report, *, whole: bool = True) -> dict[str, Any]:
    return serialise.report(row, _titles(db, caller.user).get(row.schedule_id), whole=whole)


@router.get("/reports")
async def list_reports(
    caller: MeOrDevice,
    db: Db,
    unread: bool | None = Query(None),
    schedule: str = Query(""),
    limit: int = Query(100),
) -> dict[str, Any]:
    need(db, caller.user, "schedule.use")
    titles = _titles(db, caller.user)
    rows = reports_service.listing(
        db, caller.user, unread=unread, schedule_id=schedule, limit=limit
    )
    return {
        "reports": [serialise.report(r, titles.get(r.schedule_id)) for r in rows],
        "unread": reports_service.unread_count(db, caller.user.id),
    }


@router.get("/reports/{report_id}")
async def get_report(caller: MeOrDevice, db: Db, report_id: str) -> dict[str, Any]:
    return {"report": _one(db, caller, _owned(db, caller, report_id))}


@router.post("/reports/{report_id}/read")
async def read_report(caller: MeOrDevice, db: Db, report_id: str) -> dict[str, Any]:
    row = _owned(db, caller, report_id)
    reports_service.mark_read(db, row)
    return {"report": _one(db, caller, row)}


@router.delete("/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(caller: MeOrDevice, db: Db, report_id: str) -> Response:
    reports_service.delete(db, _owned(db, caller, report_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/reports/{report_id}/reply")
async def reply_to_report(caller: MeOrDevice, db: Db, report_id: str) -> dict[str, Any]:
    row = _owned(db, caller, report_id)
    chat = reports_service.reply_chat(db, caller.user, row)
    reports_service.mark_read(db, row)
    return {"chat": chat.id}


__all__ = ["router"]
