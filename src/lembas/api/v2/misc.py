"""Everything around the chats the app needs: who, which models, which folders,
the attachments, the linked devices and the push registration."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select

from lembas import __version__
from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import Me, need
from lembas.db.models import KIND_AGENT, Attachment, Folder, PushSubscription
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


def _me(caller, db) -> dict[str, Any]:
    from lembas.api.preferences import themes
    from lembas.api.v1.services import available
    from lembas.api.v2.python import state as python_state
    from lembas.services import branding

    user = caller.user
    start = chat_service.default_model(db, user)
    offered = set(themes())
    return serialise.me(
        user,
        version=__version__,
        language=i18n.for_user(user),
        default_model=start[0] if start else None,
        permissions={key: permissions.has(db, user, key) for key in SHOWN_PERMISSIONS},
        services=available(db, user),
        scopes=list(caller.token.scopes_json or []),
        languages=[{"id": code, "name": name} for code, name in i18n.LANGUAGES],
        themes=[
            {"id": theme.id, "label": str(theme.label), "scheme": theme.scheme}
            for theme in branding.snapshot().themes
            if theme.id in offered
        ],
        chosen_language=str((user.settings_json or {}).get("language") or ""),
        python=python_state(db),
    )


@router.get("/me")
async def me(caller: Me, db: Db) -> dict[str, Any]:
    return _me(caller, db)


@router.patch("/me")
async def update_me(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """The person's own choices, as Settings makes them: `language` (a code
    from `languages`, or "" to follow the instance) and `theme` (an id from
    `themes`). An unknown value is refused (400) rather than stored; an absent
    key is left alone."""
    from lembas.api.preferences import themes

    user = caller.user
    settings = dict(user.settings_json or {})
    if "language" in body:
        chosen = str(body.get("language") or "").strip().lower()
        if chosen and chosen not in i18n.LANGUAGE_IDS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, t("That language is not offered."))
        settings["language"] = chosen
    if "theme" in body:
        chosen = str(body.get("theme") or "").strip()
        if chosen not in themes():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, t("That theme is not offered."))
        settings["theme"] = chosen
    user.settings_json = settings
    db.commit()
    # The answer in the language just chosen.
    i18n.activate(i18n.for_user(user))
    return _me(caller, db)


@router.get("/apps")
async def apps(caller: Me) -> dict[str, Any]:
    """Which Android and Desktop release this server recommends (its own
    channel's: a beta server may name a beta app, a stable one never does);
    an app compares it with itself and offers the update."""
    from lembas.services import app_releases

    return app_releases.recommended()


@router.get("/models")
async def models(
    caller: Me, db: Db, chat: str = Query(""), kind: str = Query(""), device: str = Query(""),
    directory: str = Query(""),
) -> dict[str, Any]:
    """What this person may start a chat with -- or, with `chat`, what that chat
    may switch to: its data group's models only, the page's rule.

    `kind=agent` (spec 3.4.0, subagents.md §14): for a new agent chat, whose
    subagents run on its device -- each model's `subagents` is the agent
    chat's fact (offered where the instance and the person allow them, the
    same `team_run.agent_offered` the web's new-chat slider reads), and
    `agent_subagents` says it once, with `device_runs` for `device` (one of
    the person's devices): whether it lists `agents` and `tree`, null while
    it is not linked or none is named.

    With `directory` as well (spec 3.9.0): `start` -- `{model, ref, from}`
    (`ref` names the one row: send it as `model` to be exact), the
    model a new agent chat on that device and directory starts on when the
    app names none, and where it comes from: `directory` (the model last used
    there, which the device remembers and the person still has) or `default`.
    It is what `POST /chats` without a `model` will do, asked the same way:
    an app shows it as the draft's model and need not send it."""
    from lembas.api.v2._common import owned_chat
    from lembas.services import data_groups

    group = data_groups.for_chat(db, owned_chat(db, chat, caller.user)) if chat else None
    start = chat_service.default_model(db, caller.user)
    from lembas.services import subagents as subagents_rules
    from lembas.services import team_run

    # Whether a chat on each is offered subagents (harness spec 3.3.0): for a
    # new chat with no `chat`, for that chat's model switch with one.
    owned = owned_chat(db, chat, caller.user) if chat else None
    agent = kind == KIND_AGENT and owned is None
    agent_offered = team_run.agent_offered(db, caller.user) if agent else False
    out = [
        serialise.model(
            row,
            default=bool(start and (row.model_id, row.connection_id) == start),
            subagents=(
                {"offered": agent_offered, "named_only": False}
                if agent
                else subagents_rules.view(db, caller.user, None if owned is None else owned, row)
            ),
        )
        for row in chat_service.available_models(db, caller.user, group)
    ]
    answer: dict[str, Any] = {"models": out, "subagents_cap": team_run.cap(db)}
    if agent:
        answer["agent_subagents"] = {
            "offered": agent_offered,
            "device_runs": _device_runs(db, caller.user, device) if device else None,
        }
        if device and directory.strip():
            answer["start"] = await _agent_start(db, caller.user, device, directory.strip(), start)
    return answer


async def _agent_start(db, user, token_id: str, path: str, default) -> dict[str, Any]:
    """`start` of `GET /models?kind=agent&device=&directory=`."""
    from lembas.services import directories

    token = directories.owned_device(db, user, token_id)
    row = await directories.model_for(db, user, token.id, path) if token is not None else None
    if row is not None:
        return {"model": row.model_id, "ref": chat_service.model_ref(row),
                "from": directories.FROM_DIRECTORY}
    usual = chat_service.model_row(
        db, chat_service.Speaker(model_id=default[0], connection_id=default[1])
    ) if default else None
    return {"model": default[0] if default else None,
            "ref": chat_service.model_ref(usual) if usual is not None else None,
            "from": directories.FROM_DEFAULT}


def _device_runs(db, user, token_id: str) -> bool | None:
    """Whether one of this person's devices runs subagents (lists `agents`
    and `tree`); None while it is not linked, or not theirs."""
    from lembas.db.models import ApiToken
    from lembas.services import device_link

    token = db.get(ApiToken, token_id)
    if token is None or token.user_id != user.id or device_link.link_for(token_id) is None:
        return None
    return device_link.offers(token_id, "agents") and device_link.offers(token_id, "tree")


# --- An editor's two models ------------------------------------------------------
@router.get("/editor")
async def editor_settings(caller: Me, db: Db) -> dict[str, Any]:
    """The person's two models in an editor (`services/editor.py`):
    `{autocomplete: {model, source, allowed}, main: {model}}`. A model is its
    `ref` (`provider/id`, as on `GET /models`) or null. `source` is `person`
    (their own choice), `instance` (the administrator's default) or null with
    no model. `allowed` lists the refs an administrator allows for
    autocomplete, or is null when any of the person's chat models may be
    used. The same answer as `GET /v1/lembas/editor`."""
    from lembas.services import editor

    return editor.view(db, caller.user)


@router.put("/editor")
async def set_editor_settings(
    caller: Me, db: Db, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Set the person's choices: `autocomplete_model` and `main_model`, each a
    ref or null (null: back to the instance's). A field left out keeps what
    is stored. Refused whole -- nothing written -- for a model that is not
    theirs (404) or not allowed for autocomplete (403)."""
    from lembas.services import editor

    changes = {key: body[key] for key in ("autocomplete_model", "main_model") if key in body}
    try:
        editor.save(db, caller.user, **changes)
    except editor.Refused as exc:
        raise HTTPException(exc.status_code, editor.refusal_text(exc)) from exc
    return editor.view(db, caller.user)


@router.get("/models/images/{filename}")
async def model_image(caller: Me, db: Db, filename: str) -> Response:
    """A model's picture, for the app: the token's door to what the page reads
    at `/uploads/models/{filename}` with the cookie. Only the picture of a model
    this person may use -- a name alone opens nothing."""
    from lembas.services import uploads

    if not any(
        row.image_path == filename for row in chat_service.available_models(db, caller.user)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("No such image."))
    path = uploads.model_image_path(filename)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("No such image."))
    return FileResponse(
        path,
        media_type=uploads.media_type_for(filename),
        # A new picture gets a new name, so a long cache is safe.
        headers={"Cache-Control": "private, max-age=604800"},
    )


@router.get("/folders")
async def folders(caller: Me, db: Db) -> dict[str, Any]:
    rows = db.scalars(
        select(Folder).where(Folder.user_id == caller.user.id).order_by(Folder.position)
    )
    return {"folders": [serialise.folder(f) for f in rows]}


@router.get("/devices")
async def devices(caller: Me, db: Db) -> dict[str, Any]:
    """The devices an agent chat can be started on: this person's device
    tokens that are linked right now (the new-agent-chat screen's list)."""
    from lembas.api.terminal import availability
    from lembas.services import device_link
    from lembas.services import devices as devices_service
    from lembas.services.schedule import device as scheduled_device

    out = []
    for token in devices_service.devices_of(db, caller.user):
        link = device_link.link_for(token.id) if api_tokens.live(token) else None
        if link is None:
            continue
        terminal_off = availability(db, caller.user, token.id)
        out.append(
            serialise.device(
                token,
                terminal=_terminal_on(db, caller.user, link),
                terminal_off=terminal_off,
                scheduled=link.can("scheduled") and scheduled_device.accepts(link),
                max_mode=link.max_mode or "",
                directory_model=link.can("directory_model"),
                files=link.can("fs"),
                files_run_code=(link.hello.get("limits") or {}).get("files_that_run_code")
                is True,
            )
        )
    return {"devices": out}


@router.get("/mcp")
async def mcp_servers(request: Request, caller: Me, db: Db) -> dict[str, Any]:
    """The MCP servers whose tools this person's chats may be offered, each
    with the state of its sign-in and, where they have to sign in, the page
    to open in a browser."""
    from lembas.api.devices import public_base
    from lembas.api.mcp_signin import signin_path
    from lembas.services import tool_access
    from lembas.services.mcp import oauth
    from lembas.services.mcp.registry import _offered_tools

    if not permissions.has(db, caller.user, "tools.mcp"):
        return {"servers": []}
    base = public_base(request)
    out = []
    for server in tool_access.visible_mcp_servers(db, caller.user):
        auth = oauth.view(db, server, caller.user)
        wanted = auth["can_sign_in"] and auth["state"] in ("signed_out", "needs_signin")
        out.append(
            serialise.mcp_server(
                server,
                auth,
                tools=len(_offered_tools(server)),
                signin_url=base + signin_path(server) if wanted else "",
            )
        )
    return {"servers": out}


def _terminal_on(db, user, link) -> bool:
    from lembas.services import settings_store

    return bool(
        link.can("terminal")
        and permissions.has(db, user, "agent.terminal")
        and settings_store.agents(db).get("terminal_enabled", True)
    )


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
        media_type=(
            row.media_type if row.is_image or row.is_playable else "application/octet-stream"
        ),
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
