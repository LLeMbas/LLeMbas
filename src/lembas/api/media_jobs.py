"""The card of a long generation in the conversation.

A chat has no stream while no reply is being written (`api/chats.py`
`thread_tail`: the page asks), and a job outlives the reply that started it.
So the card asks too: `GET /media/jobs/{id}/card` is the card as it stands,
and while the job is active the card carries the htmx trigger that asks
again; a finished one does not, and holds the player. Cancel is a POST that
answers with the same card.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from lembas.api.deps import Db, RequiredUser
from lembas.services.media import jobs
from lembas.web.i18n import t
from lembas.web.templating import render

router = APIRouter(tags=["media"])


def _card(request: Request, found: dict | None) -> Response:
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That job no longer exists."))
    return render(request, "chat/_media_job.html", {"job": found})


@router.get("/media/jobs/{job_id}/card")
async def card(request: Request, db: Db, user: RequiredUser, job_id: str) -> Response:
    row = jobs.owned(db, job_id, user.id)
    return _card(request, jobs.view(row) if row is not None else None)


@router.post("/media/jobs/{job_id}/cancel")
async def cancel(request: Request, db: Db, user: RequiredUser, job_id: str) -> Response:
    user_id = user.id
    db.rollback()
    return _card(request, await jobs.cancel(job_id, user_id))


@router.get("/media/open")
async def open_in_files(db: Db, user: RequiredUser, node: str = "") -> Response:
    """The file page of a generated file, by the file's id: where it is now."""
    from urllib.parse import quote

    from fastapi.responses import RedirectResponse

    from lembas.services import fs

    path = fs.path_of(db, user.org_id, node)
    try:
        # Only to a file this person may see: the address names its path.
        seen = fs.stat(fs.view(db, user), path) if path else None
    except fs.FsError:
        seen = None
    if seen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, fs.no_such(node or "/").message)
    return RedirectResponse(f"/files/view?path={quote(path)}", status_code=303)
