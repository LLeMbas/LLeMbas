"""Reports: a feed of finished work, and one report on its own page.

List-plus-detail, the same shape as the library — and for the same reason, since
an instance running a daily schedule accumulates reports faster than anything
else here.

**There is no composer on either page, and no route below accepts a message.**
That is the whole character of the section rather than an omission: a report is
addressed to the reader and cannot be answered, and the way to be sure of that
is for the machinery that would answer to be absent. Nothing here renders
`chat/_message.html`, so there is no `sse-connect` anywhere on these pages and
nothing on them can start a generation.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.api.library import PAGE_SIZE, _page
from lembas.api.pages import sidebar_context
from lembas.db.models import Report
from lembas.security import permissions
from lembas.services import reports as reports_service
from lembas.services import sharing
from lembas.services.library import retrieval
from lembas.services.markdown import render_markdown
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_permission("reports.use"))], tags=["reports"])


# Reports are a tab of the Library. The addresses of their former sidebar
# section redirect, so a notification, a link in
# a chat or the installed app's shortcut still lands; both stay behind
# `reports.use`, exactly as before, whatever `library.use` says.
LIST_URL = "/library/reports"


@router.get("/reports")
async def old_reports_list(request: Request) -> Response:
    query = request.url.query
    return RedirectResponse(
        f"{LIST_URL}?{query}" if query else LIST_URL, status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/reports/{report_id}")
async def old_report_detail(report_id: str) -> Response:
    return RedirectResponse(f"{LIST_URL}/{report_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get(LIST_URL)
async def reports_list(
    request: Request,
    db: Db,
    user: RequiredUser,
    q: str = "",
    page: int = 1,
    shared: bool = False,
):
    """`shared=1` narrows to reports other people have shared with this reader.

    Reports became shareable at the same time as this filter appeared, and the
    two arrived together on purpose: a feed that quietly grew somebody else's
    work with no way to see only theirs is worse than one that never grew.
    """
    if q.strip():
        rows = reports_service.search(
            db, user, q, limit=PAGE_SIZE, vector=await retrieval.embed_query(db, q)
        )
        pager = {"page": 1, "pages": 1, "total": len(rows)}
    else:
        rows, pager = _page(
            db,
            (
                select(Report).where(sharing.only_shared(Report, user))
                if shared
                else reports_service.visible(user)
            ).order_by(Report.created_at.desc()),
            page,
        )
    return render(
        request,
        "reports/index.html",
        {
            "section": "reports",
            "reports": rows,
            "q": q,
            "shared": shared,
            "pager": pager,
            **sidebar_context(db, user),
        },
    )


@router.get(LIST_URL + "/{report_id}")
async def report_detail(request: Request, db: Db, user: RequiredUser, report_id: str):
    report = reports_service.get(db, report_id, user)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That report is not available."))
    # Opening one is what reading it means. Done before rendering so the dot on
    # the way in and the dot on the way back to the list agree -- the poller
    # would otherwise re-announce a report the reader is looking at.
    #
    # Only the owner's own reading counts. `unread` is the owner's dot, and
    # somebody a report was shared with opening it would otherwise clear a
    # notification meant for a person who has not seen it.
    if report.owner_id == user.id:
        reports_service.mark_read(db, report)
    return render(
        request,
        "reports/detail.html",
        {
            "section": "reports",
            "report": report,
            # Model output, through the one path allowed to emit HTML.
            "body_html": render_markdown(report.body),
            "can_share": permissions.has(db, user, "library.share"),
            "is_owner": report.owner_id == user.id,
            "share_kind": "report",
            "share_id": report.id,
            **sidebar_context(db, user),
        },
    )


@router.post("/api/reports/{report_id}/delete")
async def delete_report(db: Db, user: RequiredUser, report_id: str) -> Response:
    # `owned`, not `get`: sharing grants reading, so being able to see a report
    # is not being able to delete it out from under the person who filed it.
    report = reports_service.owned(db, report_id, user)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That report is not available."))
    reports_service.delete(db, report)
    return RedirectResponse(LIST_URL, status_code=status.HTTP_303_SEE_OTHER)
