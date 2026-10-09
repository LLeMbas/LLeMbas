"""Serving a prototype to the frame that shows it.

One address per block: the chat, the message, and the digest of the block's
contents (see `services/prototypes.py` for why a digest). Every response --
the page, the refusal, the download -- carries the same sealing policy, so
there is no answer from here that a browser runs as this application.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from fastapi.responses import HTMLResponse

from lembas.api.deps import Db, RequiredUser
from lembas.db.models import Chat, Message
from lembas.services import generation
from lembas.services import prototypes as prototypes_service
from lembas.web.i18n import t

router = APIRouter(prefix="/api/chats", tags=["prototypes"])


def _headers(db) -> dict[str, str]:
    return {
        "Content-Security-Policy": prototypes_service.policy(
            allow_network=prototypes_service.network(db)
        ),
        "X-Content-Type-Options": "nosniff",
        # The frame is an opaque origin and has no business telling anybody
        # which conversation it came out of.
        "Referrer-Policy": "no-referrer",
        # Not cached: the digest pins the contents, but not whether this person
        # may still see them -- an administrator who turns prototypes off means
        # now, not when somebody's cache expires.
        "Cache-Control": "no-store",
    }


def _refuse(db, message: str, code: int) -> HTMLResponse:
    return HTMLResponse(prototypes_service.refusal(message), status_code=code, headers=_headers(db))


def _find(db, user, chat_id: str, message_id: str, digest: str):
    """The block, or the response saying why not.

    404 for somebody else's chat, as everywhere else in this API: whether an
    id exists is not information these routes hand out.
    """
    if not prototypes_service.allowed(db, user):
        return None, _refuse(db, t("Prototypes are turned off here."), status.HTTP_403_FORBIDDEN)

    chat = db.get(Chat, chat_id)
    message = db.get(Message, message_id)
    if chat is None or chat.user_id != user.id or message is None or message.chat_id != chat.id:
        return None, _refuse(db, t("This prototype is not there any more."), 404)

    text = message.content or ""
    if not message.complete:
        # Still being written: the row has nothing yet, and the reply's own
        # buffer is where a block that has already closed can be found.
        running = generation.get(message.id)
        if running is not None:
            text = running.text

    found = prototypes_service.find(text, digest)
    if found is None:
        return None, _refuse(db, t("This prototype is not there any more."), 404)
    if len(found.code.encode("utf-8")) > prototypes_service.MAX_BYTES:
        return None, _refuse(db, t("This prototype is too large to show."), 413)
    return found, None


@router.get("/{chat_id}/messages/{message_id}/prototypes/{digest}")
async def show_prototype(
    db: Db, user: RequiredUser, chat_id: str, message_id: str, digest: str, scheme: str = ""
) -> Response:
    found, refusal = _find(db, user, chat_id, message_id, digest)
    if refusal is not None:
        return refusal
    page = prototypes_service.document(found.code, scheme=scheme)
    return HTMLResponse(page, headers=_headers(db))


@router.get("/{chat_id}/messages/{message_id}/prototypes/{digest}/download")
async def download_prototype(
    db: Db, user: RequiredUser, chat_id: str, message_id: str, digest: str
) -> Response:
    """Exactly what the model wrote, as a file -- without our boot script, which
    talks to a frame that will not be there."""
    found, refusal = _find(db, user, chat_id, message_id, digest)
    if refusal is not None:
        return refusal
    headers = _headers(db)
    headers["Content-Disposition"] = (
        f'attachment; filename="{prototypes_service.filename_for(found.title)}"'
    )
    return Response(found.code, media_type="text/html; charset=utf-8", headers=headers)
