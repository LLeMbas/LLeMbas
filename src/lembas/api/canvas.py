"""The canvas panel: open a file, read it, change it, save it.

Every route answers with an HTML fragment, errors included. An exception page
swapped into a side panel is a blank side panel, and a panel that goes blank
tells somebody nothing about why.

`GET` never moves the active tab. There is no CSRF token in this application and
the session cookie is SameSite Lax, so a state-changing GET is a link somebody
can be made to follow -- and one of the things a tab can be is a file on
somebody's server.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db, RequiredUser
from lembas.db.models import Chat, User
from lembas.services import canvas as canvas_service
from lembas.services import generation as generation_service
from lembas.services.agent.base import Conflict
from lembas.services.markdown import highlight_code, render_markdown
from lembas.web.i18n import t
from lembas.web.templating import templates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["canvas"])


def _owned_chat(db: DBSession, chat_id: str, user_id: str) -> Chat:
    """404 rather than 403 for somebody else's chat: whether it exists at all is
    not this account's business.

    """
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That chat no longer exists."))
    return chat


def _remember_tabs(chat: Chat, state: dict) -> bool:
    """Whether the tabs live somewhere other than the row. Never: the new-chat
    screen has no draft canvas."""
    return False


async def _panel(
    request: Request,
    db: DBSession,
    user: User,
    chat: Chat,
    *,
    key: str = "",
    message: str = "",
    conflict: canvas_service.Doc | None = None,
    mine: str = "",
) -> Response:
    """The strip and whichever tab is in front, as one fragment.

    Both together, always. Rendering only the body would leave the strip showing
    a tab that is no longer there after a close, and rendering only the strip
    would leave the previous file on screen after a switch.
    """
    wanted = key or canvas_service.active_of(chat)
    doc: canvas_service.Doc | None = None
    error = message
    if wanted and not error:
        try:
            doc = await canvas_service.load(db, user, chat, wanted)
        except canvas_service.Refused as exc:
            error = str(exc)
        except Exception:  # pragma: no cover - a machine going away mid-request
            log.exception("canvas could not open %s", wanted)
            error = t("That could not be opened.")

    body = ""
    if doc is not None and doc.text:
        # The one `|safe` in this panel, and it is safe because pygments escapes
        # what it is given. Markdown goes through render_markdown, the single
        # path in this application allowed to emit HTML. Everything else -- the
        # editor's contents, the titles, the paths -- is escaped by Jinja.
        body = render_markdown(doc.text) if doc.markdown else highlight_code(doc.text, doc.language)

    return templates.TemplateResponse(
        request,
        "chat/_canvas_inner.html",
        {
            "user": user,
            "chat": chat,
            "tabs": canvas_service.tabs_of(chat),
            "active": wanted,
            "doc": doc,
            "rendered": body,
            "error": error,
            "conflict": conflict,
            "mine": mine,
        },
    )


@router.get("/{chat_id}/canvas")
async def show(request: Request, db: Db, user: RequiredUser, chat_id: str, key: str = ""):
    """Whatever is in front, or the tab named by `?key=`.

    Read-only in every sense: a `?key=` that is not open does not become open,
    it is simply shown. Opening is a POST.
    """
    chat = _owned_chat(db, chat_id, user.id)
    return await _panel(request, db, user, chat, key=key)


@router.post("/{chat_id}/canvas/tabs")
async def open_tab(
    request: Request,
    db: Db,
    user: RequiredUser,
    chat_id: str,
    key: str = Form(...),
    title: str = Form(""),
):
    """Open a file, or bring an already-open one to the front.

    Idempotent, because opening what is already open is switching to it -- the
    same reason `generation.ensure` is idempotent.
    """
    chat = _owned_chat(db, chat_id, user.id)


    try:
        doc = await canvas_service.load(db, user, chat, key)
    except canvas_service.Refused as exc:
        return await _panel(request, db, user, chat, message=str(exc))

    state = canvas_service.open_tab(
        dict(chat.canvas_json or {}),
        {"key": doc.key, "title": title.strip() or doc.title, "source": doc.key.split(":")[0]},
    )
    # Reassigned rather than mutated: an in-place edit of a JSON column is not
    # reliably detected as a change.
    chat.canvas_json = state
    if not _remember_tabs(chat, state):
        db.commit()

    # A reply running right now holds its own snapshot, seeded when it started.
    # Without this the next frame it sends would contradict what was just
    # swapped in -- the same reach into live state `request_stop` makes.
    live = generation_service.running_for(chat.id)
    if live is not None:
        canvas_service.open_tab(live.canvas, {"key": doc.key, "title": doc.title})

    return await _panel(request, db, user, chat, key=doc.key)


@router.post("/{chat_id}/canvas/tabs/close")
async def close_tab(
    request: Request, db: Db, user: RequiredUser, chat_id: str, key: str = Form(...)
):
    chat = _owned_chat(db, chat_id, user.id)
    chat.canvas_json = canvas_service.close_tab(dict(chat.canvas_json or {}), key)
    if not _remember_tabs(chat, chat.canvas_json):
        db.commit()

    live = generation_service.running_for(chat.id)
    if live is not None:
        canvas_service.close_tab(live.canvas, key)

    return await _panel(request, db, user, chat)


@router.post("/{chat_id}/canvas/save")
async def save(
    request: Request,
    db: Db,
    user: RequiredUser,
    chat_id: str,
    key: str = Form(...),
    text: str = Form(""),
    revision: str = Form(""),
):
    """Write it back.

    A conflict comes back as a card, at 200, so htmx swaps it: the panel has to
    be able to show Overwrite, Discard mine and Show what changed, and none of
    those can be offered from an error status htmx will not render. Never save
    silently over a change; never discard silently either.
    """
    chat = _owned_chat(db, chat_id, user.id)

    try:
        await canvas_service.save(db, user, chat, key, text, revision)
    except Conflict:
        try:
            theirs = await canvas_service.load(db, user, chat, key)
        except canvas_service.Refused as exc:
            return await _panel(request, db, user, chat, key=key, message=str(exc))
        return await _panel(request, db, user, chat, key=key, conflict=theirs, mine=text)
    except canvas_service.Refused as exc:
        return await _panel(request, db, user, chat, key=key, message=str(exc))
    except Exception:  # pragma: no cover - the machine going away mid-write
        log.exception("canvas could not save %s", key)
        return await _panel(
            request, db, user, chat, key=key, message=t("That could not be saved.")
        )

    return await _panel(request, db, user, chat, key=key)
