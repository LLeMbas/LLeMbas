"""Reports: what a schedule's run filed, as pages.

A list (unread first, by schedule), one report as a page -- opening it is
reading it -- delete, and **Reply**, which continues the report as a
conversation that stays with it (`services/reports.py`). Behind
`schedule.use`, like Scheduled. A report is its owner's: somebody else's is a
404, as one that is not there is.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.api.pages import sidebar_context
from lembas.db.models import Report
from lembas.services import reports as reports_service
from lembas.services.schedule import clock
from lembas.web.i18n import t
from lembas.web.templating import render

router = APIRouter(tags=["reports"], dependencies=[Depends(require_permission("schedule.use"))])


def _owned(db, user, report_id: str) -> Report:
    row = reports_service.owned(db, user, report_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That report no longer exists."))
    return row


@router.get("/reports")
async def reports_page(
    request: Request, db: Db, user: RequiredUser, schedule: str = "", unread: str = ""
):
    rows = reports_service.listing(
        db, user, unread=True if unread else None, schedule_id=schedule
    )
    return render(
        request,
        "reports/list.html",
        {
            "reports": rows,
            "schedules": reports_service.schedules_named(db, user),
            "picked_schedule": schedule,
            "only_unread": bool(unread),
            "chat": None,
            "as_local": _local(user),
            "section": "reports",
            **sidebar_context(db, user),
        },
    )


@router.get("/reports/{report_id}")
async def report_page(request: Request, db: Db, user: RequiredUser, report_id: str):
    row = _owned(db, user, report_id)
    # Opening it is reading it.
    reports_service.mark_read(db, row)
    named = dict(reports_service.schedules_named(db, user))
    return render(
        request,
        "reports/show.html",
        {
            "report": row,
            "schedule_title": named.get(row.schedule_id) or row.schedule_title,
            "schedule_alive": bool(
                row.schedule_id and reports_service.schedule_exists(db, user, row.schedule_id)
            ),
            "chat": None,
            "as_local": _local(user),
            "section": "reports",
            **sidebar_context(db, user),
        },
    )


@router.post("/reports/{report_id}/delete")
async def delete_report(db: Db, user: RequiredUser, report_id: str) -> Response:
    reports_service.delete(db, _owned(db, user, report_id))
    return RedirectResponse("/reports", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/reports/{report_id}/reply")
async def reply_to_report(db: Db, user: RequiredUser, report_id: str) -> Response:
    """Continue the report as a conversation: made once, and opened."""
    chat = reports_service.reply_chat(db, user, _owned(db, user, report_id))
    return RedirectResponse(f"/chat/{chat.id}", status_code=status.HTTP_303_SEE_OTHER)


def _local(user):
    """A stored moment in the reader's own time zone."""
    zone = clock.zone_for(user)
    return lambda moment: clock.as_utc(moment).astimezone(zone) if moment else None


__all__ = ["router"]
