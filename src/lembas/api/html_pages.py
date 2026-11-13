"""A page a model wrote into an answer, as a document for its frame (harness
spec 3.8.0, `pages.md` §6).

`/pages/m/{message}/{digest}` and `/pages/r/{report}/{digest}`: the page of
that reply or report whose digest this is -- found by parsing the stored text
again, never stored apart from it, and never taken from the request. Served
**rebuilt and static** (`services/html_pages.document`) under
`security/sealed.page_headers()`: no script, no network, an origin of its own.
The colours in the query are the frame's variables as the page that shows it
has them; each is kept only if it is plainly a colour.

Behind the instance's switch, and the reader's own conversations and reports
only. Asked for as a document of its own -- a tab, not a frame -- it is
refused where the browser says so (`Sec-Fetch-Dest`): a page is something an
answer shows, not an address.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from lembas.api.deps import Db, RequiredUser
from lembas.db.models import Chat, Message, Report
from lembas.security import sealed
from lembas.services import html_pages
from lembas.services.markdown import pages_in
from lembas.web.i18n import t

router = APIRouter(tags=["html-pages"])

_FRAMED = ("iframe", "frame", "embed", "object")


def _serve(request: Request, db, text: str, digest: str) -> HTMLResponse:
    asked_as = request.headers.get("sec-fetch-dest", "")
    if asked_as and asked_as not in _FRAMED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A page is shown in its answer.")
    page = next((p for p in pages_in(text) if p.digest == digest), None)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not html_pages.instance_on(db):
        # Switched off on this instance: the frame says so, under the same
        # policy, and the reader still has the source beside it.
        page = html_pages.Page(page.title, f"<p>{t('Pages in answers are switched off here.')}</p>")
    colours = {name: request.query_params.get(name, "") for name in html_pages.COLOURS}
    return HTMLResponse(html_pages.document(page, colours), headers=sealed.page_headers())


@router.get("/pages/m/{message_id}/{digest}")
async def message_page(
    request: Request, db: Db, user: RequiredUser, message_id: str, digest: str
) -> HTMLResponse:
    message = db.get(Message, message_id)
    chat = db.get(Chat, message.chat_id) if message is not None else None
    # Somebody else's is as absent as one that is not there.
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return _serve(request, db, message.content or "", digest)


@router.get("/pages/r/{report_id}/{digest}")
async def report_page(
    request: Request, db: Db, user: RequiredUser, report_id: str, digest: str
) -> HTMLResponse:
    report = db.get(Report, report_id)
    if report is None or report.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return _serve(request, db, report.body or "", digest)


__all__ = ["router"]
