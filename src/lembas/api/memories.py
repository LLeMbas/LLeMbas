"""What is remembered about a person: added, changed and removed in Settings.

Memories are records, not files (harness spec 3.0.0, `filesystem.md`
*Memory*): short facts kept per data group, in front of the model on every
turn (`services/memories.py`). These are the three forms on the Settings page;
the model's own changes go through the `memory` tool.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.db.models import AUTHOR_USER, User
from lembas.services import data_groups
from lembas.services import memories as memories_service
from lembas.web.i18n import t

router = APIRouter(dependencies=[Depends(require_permission("library.use"))], tags=["memories"])


def _chosen_group(db: DBSession, user: User, value: str) -> str:
    """A submitted group, if this person may use it; otherwise the default."""
    value = (value or "").strip()
    if value and data_groups.may_use(db, user, value):
        return value
    return data_groups.DEFAULT_GROUP


def _said(exc: ValueError) -> str:
    """A refusal in the reader's language; any other ValueError as it came."""
    if isinstance(exc, memories_service.Refused):
        return exc.translated()
    return str(exc)


@router.post("/api/memories")
async def add_memory(
    db: Db, user: RequiredUser, content: str = Form(""), data_group_id: str = Form("")
) -> Response:
    try:
        memories_service.add(
            db,
            owner=user,
            content=content,
            author=AUTHOR_USER,
            group=_chosen_group(db, user, data_group_id),
        )
    except ValueError as exc:
        return RedirectResponse(
            f"/settings?error={quote(_said(exc))}", status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        f"/settings?saved={quote(t('Memory added.'))}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/memories/{memory_id}")
async def update_memory(
    db: Db, user: RequiredUser, memory_id: str, content: str = Form("")
) -> Response:
    memory = memories_service.get(db, memory_id, user)
    if memory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That memory no longer exists."))
    try:
        memories_service.update(db, memory, content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _said(exc)) from exc
    return RedirectResponse(
        f"/settings?saved={quote(t('Memory updated.'))}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/api/memories/{memory_id}/delete")
async def delete_memory(db: Db, user: RequiredUser, memory_id: str) -> Response:
    memory = memories_service.get(db, memory_id, user)
    if memory is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That memory no longer exists."))
    memories_service.delete(db, memory)
    return RedirectResponse(
        f"/settings?saved={quote(t('Memory removed.'))}", status_code=status.HTTP_303_SEE_OTHER
    )
