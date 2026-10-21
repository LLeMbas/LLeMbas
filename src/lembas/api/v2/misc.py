"""Everything around the chats the app needs: who, which models, which folders,
the attachments, the linked devices and the push registration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from lembas import __version__
from lembas.api.deps import Db
from lembas.api.v2 import APP_API, serialise
from lembas.api.v2._common import Me, need
from lembas.db.models import Attachment, Folder, PushSubscription
from lembas.security import api_tokens, permissions
from lembas.services import chat as chat_service
from lembas.services import files as files_service
from lembas.web import i18n
from lembas.web.i18n import t

router = APIRouter()

# The permissions whose answer changes what the app draws. Not the whole table:
# what the app may *do* is decided here per request, and these only spare it
# offering a control the server would refuse.
SHOWN_PERMISSIONS = (
    "chat.create",
    "chat.delete",
    "chat.model_select",
    "chat.system_prompt",
    "chat.params",
    "files.upload",
    "tools.agent",
    "audio.transcribe",
    "audio.listen",
)


@router.get("/me")
async def me(caller: Me, db: Db) -> dict[str, Any]:
    from lembas.api.v1.services import available

    user = caller.user
    start = chat_service.default_model(db, user)
    return {
        "api": APP_API,
        "version": __version__,
        "account": {"id": user.id, "name": user.name, "email": user.email},
        "language": i18n.for_user(user),
        "theme": str((user.settings_json or {}).get("theme") or ""),
        "default_model": start[0] if start else None,
        "permissions": {key: permissions.has(db, user, key) for key in SHOWN_PERMISSIONS},
        "services": available(db, user),
    }


@router.get("/models")
async def models(caller: Me, db: Db, chat: str = Query("")) -> dict[str, Any]:
    """What this person may start a chat with -- or, with `chat`, what that chat
    may switch to: its data group's models only, the page's rule."""
    from lembas.api.v2._common import owned_chat
    from lembas.services import data_groups

    group = data_groups.for_chat(db, owned_chat(db, chat, caller.user)) if chat else None
    start = chat_service.default_model(db, caller.user)
    out = []
    for row in chat_service.available_models(db, caller.user, group):
        caps = row.capabilities_json or {}
        out.append(
            {
                "id": row.model_id,
                "name": row.display_name or row.model_id,
                "vision": bool(caps.get("vision")),
                "tools": bool(caps.get("tools")),
                "efforts": list(row.reasoning_efforts or []),
                "context": int(row.context_length or 0),
                "default": bool(start and (row.model_id, row.connection_id) == start),
            }
        )
    return {"models": out}


@router.get("/folders")
async def folders(caller: Me, db: Db) -> dict[str, Any]:
    rows = db.scalars(
        select(Folder).where(Folder.user_id == caller.user.id).order_by(Folder.position)
    )
    return {
        "folders": [
            {
                "id": f.id,
                "name": f.name,
                "parent_id": f.parent_id,
                "kind": f.kind or "",
                "position": f.position,
            }
            for f in rows
        ]
    }


@router.get("/devices")
async def devices(caller: Me, db: Db) -> dict[str, Any]:
    """The devices an agent chat can be started on: this person's device
    tokens that are linked right now (the new-agent-chat screen's list)."""
    from lembas.services import device_link
    from lembas.services import devices as devices_service

    out = []
    for token in devices_service.devices_of(db, caller.user):
        if not api_tokens.live(token) or device_link.link_for(token.id) is None:
            continue
        out.append(
            {
                "id": token.id,
                "name": token.name,
                "host": token.device_host or "",
                "platform": token.device_platform or "",
            }
        )
    return {"devices": out}


# --- Attachments ---------------------------------------------------------------------------
def _owned_attachment(db, attachment_id: str, user_id: str) -> Attachment:
    row = db.get(Attachment, attachment_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That file no longer exists."))
    return row


@router.post("/attachments", status_code=status.HTTP_201_CREATED)
async def upload(
    caller: Me, db: Db, file: UploadFile = File(...), chat: str = Query("")
) -> dict[str, Any]:
    """Stored now, bound when the message naming it is sent -- the composer's
    rule, so a file can be removed before sending and an unsent one is swept."""
    need(db, caller.user, "files.upload")
    payload = await file.read()
    try:
        row = files_service.store(
            db,
            user_id=caller.user.id,
            chat_id=chat or None,
            payload=payload,
            filename=file.filename or "file",
        )
    except files_service.FileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"attachment": serialise.attachment(row)}


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(caller: Me, db: Db, attachment_id: str) -> Response:
    row = _owned_attachment(db, attachment_id, caller.user.id)
    if row.message_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, t("That file is part of a sent message."))
    files_service.delete(db, row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/attachments/{attachment_id}")
async def content(caller: Me, db: Db, attachment_id: str) -> FileResponse:
    """The file, always as a download: the app decides how to show it, and a
    response that a WebView could run is the wrong default."""
    row = _owned_attachment(db, attachment_id, caller.user.id)
    path = files_service.stored_path(row.stored_name)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That file is no longer on disk."))
    return FileResponse(
        path,
        media_type=row.media_type if row.is_image else "application/octet-stream",
        headers={
            "Content-Disposition": files_service.content_disposition("attachment", row.filename),
            "Cache-Control": "private, max-age=604800",
            "X-Content-Type-Options": "nosniff",
        },
    )


# --- Push -----------------------------------------------------------------------------------
@router.get("/push")
async def push_key(caller: Me, db: Db) -> dict[str, Any]:
    """The VAPID public key, which a UnifiedPush distributor wants at
    registration, and whether this token already has a registration."""
    from lembas.services import push as push_service

    registered = db.scalar(
        select(PushSubscription.id).where(PushSubscription.token_id == caller.token.id)
    )
    return {"key": push_service.public_key(db), "registered": registered is not None}


@router.post("/push", status_code=status.HTTP_204_NO_CONTENT)
async def push_register(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> Response:
    """Register a UnifiedPush endpoint: `{endpoint, p256dh, auth}`, as the
    connector hands them out. The same Web Push the browsers get, so the sender
    is the one `services/push.py` already has. Kept under this token: signing
    it out removes the registration."""
    from lembas.api.push import register

    stored = register(
        db,
        caller.user,
        endpoint=str(body.get("endpoint") or ""),
        p256dh=str(body.get("p256dh") or ""),
        auth=str(body.get("auth") or ""),
        label=caller.token.name or "LLeMbas app",
        token_id=caller.token.id,
    )
    if not stored:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            t("That push address cannot be used: it must be https and reachable from here."),
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/push", status_code=status.HTTP_204_NO_CONTENT)
async def push_forget(caller: Me, db: Db) -> Response:
    for row in db.scalars(
        select(PushSubscription).where(PushSubscription.token_id == caller.token.id)
    ):
        db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
