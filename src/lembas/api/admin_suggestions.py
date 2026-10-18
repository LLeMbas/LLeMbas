"""Administration for the cards offered on the new-chat screen."""

from __future__ import annotations

import logging
from functools import partial

from fastapi import APIRouter, Form, HTTPException, Request, Response, status

from lembas.api._redirect import back
from lembas.api.deps import AdminUser, Db
from lembas.db.models import Suggestion
from lembas.services import suggestions as suggestions_service
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/suggestions", tags=["admin-suggestions"])


def _suggestion(db: Db, suggestion_id: str) -> Suggestion:
    suggestion = db.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That suggestion no longer exists."))
    return suggestion


_back = partial(back, "/admin/suggestions")


@router.get("")
async def suggestions_page(request: Request, db: Db, user: AdminUser, saved: str = ""):
    rows = suggestions_service.all_of_them(db)
    return render(
        request,
        "admin/suggestions.html",
        {
            "suggestions": rows,
            "at_limit": len(rows) >= suggestions_service.MAX_SUGGESTIONS,
            "max_suggestions": suggestions_service.MAX_SUGGESTIONS,
            "max_shown": suggestions_service.MAX_SHOWN,
            "saved": saved,
        },
    )


@router.post("")
async def create_suggestion(
    db: Db,
    user: AdminUser,
    name: str = Form(""),
    description: str = Form(""),
    prompt: str = Form(""),
) -> Response:
    name = name.strip()
    if not name:
        return _back(t("A suggestion needs a name."))
    if len(suggestions_service.all_of_them(db)) >= suggestions_service.MAX_SUGGESTIONS:
        return _back(
            t("That is already %(n)s, which is plenty.", n=suggestions_service.MAX_SUGGESTIONS)
        )

    suggestions_service.create(db, name=name, description=description, prompt=prompt)
    log.info("%s added suggestion %s", user.email, name)
    return _back(t("Added %(name)s.", name=name))


# Registered before /{suggestion_id}: FastAPI matches in registration order, so
# with the parameterised route first any literal segment added later would be
# captured as an id. That has already been a bug once, in /admin/models.
@router.post("/{suggestion_id}/delete")
async def delete_suggestion(db: Db, user: AdminUser, suggestion_id: str) -> Response:
    suggestion = _suggestion(db, suggestion_id)
    name = suggestion.name
    db.delete(suggestion)
    db.commit()
    log.info("%s deleted suggestion %s", user.email, name)
    return _back(t("Deleted %(name)s.", name=name))


@router.post("/{suggestion_id}")
async def update_suggestion(
    request: Request,
    db: Db,
    user: AdminUser,
    suggestion_id: str,
) -> Response:
    """Save one row.

    The raw form is read rather than declared parameters because `enabled` is a
    checkbox: FastAPI cannot tell an unticked box from an absent field, and an
    absent one is exactly what an unticked box sends.
    """
    suggestion = _suggestion(db, suggestion_id)
    form = await request.form()

    suggestion.name = (
        str(form.get("name") or "").strip()[: suggestions_service.MAX_NAME] or suggestion.name
    )
    suggestion.description = str(form.get("description") or "").strip()[
        : suggestions_service.MAX_DESCRIPTION
    ]
    suggestion.prompt = str(form.get("prompt") or "").replace("\r\n", "\n")[
        : suggestions_service.MAX_PROMPT
    ]
    suggestion.enabled = "enabled" in form

    position = str(form.get("position") or "").strip()
    if position.isdigit():
        suggestion.position = min(max(int(position) - 1, 0), 999)

    db.commit()
    log.info("%s updated suggestion %s", user.email, suggestion.name)
    return _back(t("Saved %(name)s.", name=suggestion.name))
