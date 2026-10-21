"""Chats, their messages, the controls and the cards -- as JSON.

Every route here is a page route's twin and calls the same core (see the
package docstring). What differs is only what comes back: the rows, through
`serialise`, instead of the bubbles.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select

from lembas.api import chats as chats_api
from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import JsonForm, Me, need, owned_chat, owned_message
from lembas.db.models import KIND_CHAT, KINDS, Chat, Message
from lembas.services import generation as generation_service
from lembas.web.i18n import t

router = APIRouter()

PAGE_DEFAULT = 50
PAGE_MAX = 200


def _limit(value: int) -> int:
    return max(1, min(PAGE_MAX, value))


def _running(chat: Chat) -> bool:
    return generation_service.running_for(chat.id) is not None


def _text(body: dict[str, Any], key: str = "text") -> str:
    value = body.get(key)
    return value.strip() if isinstance(value, str) else ""


def _ids(body: dict[str, Any], key: str = "attachments") -> list[str]:
    value = body.get(key)
    return [str(v) for v in value if isinstance(v, str) and v] if isinstance(value, list) else []


def _turn(user_message: Message | None, reply: Message | None) -> dict[str, Any]:
    return {
        "message": serialise.message(user_message) if user_message is not None else None,
        "reply": serialise.message(reply) if reply is not None else None,
    }


# --- The list ------------------------------------------------------------------------
@router.get("/chats")
async def list_chats(
    caller: Me,
    db: Db,
    kind: str = Query(""),
    archived: bool = Query(False),
    folder: str = Query(""),
    before: str = Query(""),
    limit: int = Query(PAGE_DEFAULT),
) -> dict[str, Any]:
    """The sidebar: newest first, `before` a chat id to page from.

    The rows the sidebar draws and no others -- not temporary chats, which have
    no row there, and not a section's own conversations (`Chat.kind` outside
    `KINDS`), which are reached through their section.
    """
    query = select(Chat).where(
        Chat.user_id == caller.user.id,
        Chat.temporary.is_(False),
        Chat.archived.is_(archived),
        Chat.kind.in_([kind] if kind in KINDS else list(KINDS)),
    )
    if folder:
        query = query.where(Chat.folder_id == folder)
    if before:
        anchor = db.get(Chat, before)
        if anchor is not None and anchor.user_id == caller.user.id:
            query = query.where(Chat.updated_at < anchor.updated_at)
    rows = list(db.scalars(query.order_by(Chat.updated_at.desc()).limit(_limit(limit) + 1)))
    page = rows[: _limit(limit)]
    return {
        "chats": [serialise.chat(c, running=_running(c)) for c in page],
        "more": len(rows) > len(page),
    }


@router.get("/chats/unread")
async def unread(caller: Me, db: Db) -> dict[str, Any]:
    """The badge: which chats have news. Reading only -- opening a chat is
    what marks it read, as on the page."""
    rows = db.scalars(
        select(Chat.id).where(
            Chat.user_id == caller.user.id,
            Chat.unread.is_(True),
            Chat.archived.is_(False),
            Chat.temporary.is_(False),
            Chat.kind.in_(KINDS),
        )
    )
    return {"unread": list(rows)}


# --- One chat ------------------------------------------------------------------------
@router.post("/chats", status_code=status.HTTP_201_CREATED)
async def create_chat(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """A chat from its first message, as the page makes one: there is no empty
    chat, so `text` (or an attachment) is required."""
    need(db, caller.user, "chat.create")
    text = _text(body)
    files = _ids(body)
    if not text and not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("A message cannot be empty."))
    device = str(body.get("device") or "")
    chat, user_message, reply = chats_api.start_new_chat(
        db,
        caller.user,
        text,
        file_ids=files,
        folder_id=str(body.get("folder_id") or ""),
        model_id=str(body.get("model") or ""),
        temporary=bool(body.get("temporary")),
        kind=str(body.get("kind") or KIND_CHAT),
        ssh_profile_id=f"device:{device}" if device else "",
        project_dir=str(body.get("directory") or ""),
        agent_mode=str(body.get("mode") or ""),
        reasoning_effort=str(body.get("effort") or ""),
    )
    return {"chat": serialise.chat_detail(chat, running=True), **_turn(user_message, reply)}


@router.get("/chats/{chat_id}")
async def get_chat(
    caller: Me, db: Db, chat_id: str, limit: int = Query(PAGE_DEFAULT)
) -> dict[str, Any]:
    """The chat, its settings and its newest page of messages. Opening a chat
    is what "read" means, here as on the page."""
    chat = owned_chat(db, chat_id, caller.user)
    if chat.unread or chat.unread_notified:
        chat.unread = False
        chat.unread_notified = False
        db.commit()
    page, more = _page(db, chat, before="", limit=limit)
    live = generation_service.running_for(chat.id)
    return {
        "chat": serialise.chat_detail(chat, running=live is not None),
        "messages": [serialise.message(m) for m in page],
        "more": more,
        "live": live.message_id if live is not None else None,
    }


@router.patch("/chats/{chat_id}")
async def update_chat(
    caller: Me, db: Db, chat_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Change a chat's settings. A key that is present is applied, even when
    empty (that clears it); a key that is absent is left alone -- the page
    route's rule, through the page route's lines (`apply_chat_update`)."""
    chat = owned_chat(db, chat_id, caller.user)
    names = {
        "title": "title",
        "folder_id": "folder_id",
        "mode": "agent_mode",
        "model": "model_id",
        "system_prompt": "system_prompt",
        "effort": "reasoning_effort",
        "temperature": "temperature",
        "top_p": "top_p",
        "max_tokens": "max_tokens",
    }
    items: list[tuple[str, str]] = []
    for key, field in names.items():
        if key in body:
            value = body[key]
            items.append((field, "" if value is None else str(value)))
    if "archived" in body:
        items.append(("archived", "1" if body["archived"] else "0"))
    if "knowledge_bases" in body:
        # Sent once even when empty, which is what clears them -- as the page's
        # hidden field does.
        bases = body["knowledge_bases"] if isinstance(body["knowledge_bases"], list) else []
        items.extend(("knowledge_base_ids", str(b)) for b in bases or [""])
    # A chat's connection is fixed when it is made; naming one is the page's
    # 409, so it is passed through to be refused rather than quietly dropped.
    locked = (("kind", "kind"), ("device", "device_token_id"), ("directory", "project_dir"))
    for key, field in locked:
        if key in body:
            items.append((field, ""))
    chats_api.apply_chat_update(db, caller.user, chat, JsonForm(items))
    db.refresh(chat)
    return {"chat": serialise.chat_detail(chat, running=_running(chat))}


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(caller: Me, db: Db, chat_id: str) -> Response:
    need(db, caller.user, "chat.delete")
    chat = owned_chat(db, chat_id, caller.user)
    await chats_api.delete_chat_now(db, chat)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Messages --------------------------------------------------------------------------
def _page(db, chat: Chat, *, before: str, limit: int) -> tuple[list[Message], bool]:
    """A page of the transcript, newest last within it. `before` is a message id:
    a transcript is appended to while it is read, so an offset would shift."""
    query = select(Message).where(Message.chat_id == chat.id)
    if before:
        anchor = db.get(Message, before)
        if anchor is not None and anchor.chat_id == chat.id:
            query = query.where(Message.created_at < anchor.created_at)
    rows = list(db.scalars(query.order_by(Message.created_at.desc()).limit(_limit(limit) + 1)))
    page = rows[: _limit(limit)]
    return list(reversed(page)), len(rows) > len(page)


@router.get("/chats/{chat_id}/messages")
async def list_messages(
    caller: Me,
    db: Db,
    chat_id: str,
    before: str = Query(""),
    limit: int = Query(PAGE_DEFAULT),
) -> dict[str, Any]:
    chat = owned_chat(db, chat_id, caller.user)
    page, more = _page(db, chat, before=before, limit=limit)
    return {"messages": [serialise.message(m) for m in page], "more": more}


@router.post("/chats/{chat_id}/messages", status_code=status.HTTP_201_CREATED)
async def send(caller: Me, db: Db, chat_id: str, body: dict[str, Any] = Body(...)) -> dict:
    """A turn. `reply` is null when it was queued behind a reply already
    running; the stream of that reply carries it in (`steered`) or hands it to
    the next one (`done.next`)."""
    chat = owned_chat(db, chat_id, caller.user)
    text = _text(body)
    files = _ids(body)
    if not text and not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("A message cannot be empty."))
    forced = str(body.get("force_tool") or "")
    forced = forced if forced in chats_api.FORCEABLE_TOOLS else ""
    user_message, reply = chats_api.write_turn(
        db, chat, caller.user, text, file_ids=files, force_tool=forced
    )
    return _turn(user_message, reply)


@router.post("/chats/{chat_id}/messages/{message_id}/stop", status_code=204)
async def stop(caller: Me, db: Db, chat_id: str, message_id: str) -> Response:
    chat = owned_chat(db, chat_id, caller.user)
    message = owned_message(db, chat, message_id)
    generation_service.request_stop(message.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chats/{chat_id}/messages/{message_id}/edit")
async def edit(
    caller: Me, db: Db, chat_id: str, message_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    chat = owned_chat(db, chat_id, caller.user)
    reply = await chats_api.rewind_to(db, chat, caller.user, message_id, _text(body))
    return _turn(None, reply)


@router.post("/chats/{chat_id}/messages/{message_id}/regenerate")
async def regenerate(caller: Me, db: Db, chat_id: str, message_id: str) -> dict[str, Any]:
    chat = owned_chat(db, chat_id, caller.user)
    reply = await chats_api.regenerate_reply(db, chat, caller.user, message_id)
    return _turn(None, reply)


@router.post("/chats/{chat_id}/continue", status_code=status.HTTP_201_CREATED)
async def continue_chat(caller: Me, db: Db, chat_id: str) -> dict[str, Any]:
    from lembas.services import harness_spec

    chat = owned_chat(db, chat_id, caller.user)
    user_message, reply = chats_api.write_turn(
        db, chat, caller.user, harness_spec.prompt("tasks/continue.md")
    )
    return _turn(user_message, reply)


# --- The plan ---------------------------------------------------------------------------
@router.get("/chats/{chat_id}/plan")
async def get_plan(caller: Me, db: Db, chat_id: str) -> dict[str, Any]:
    chat = owned_chat(db, chat_id, caller.user)
    return {"plan": serialise.plan(chat.plan) if chat.plan else None}


@router.post("/chats/{chat_id}/plan/execute", status_code=status.HTTP_201_CREATED)
async def execute_plan(
    caller: Me, db: Db, chat_id: str, body: dict[str, Any] | None = Body(None)
) -> dict[str, Any]:
    """Carry out a plan: `message_id` names the reply whose snapshot to carry
    out, or is absent for the chat's plan as it stands (the panel's button)."""
    chat = owned_chat(db, chat_id, caller.user)
    which = str((body or {}).get("message_id") or "current")
    content = chats_api.plan_prompt(db, chat, which)
    user_message, reply = chats_api.write_turn(db, chat, caller.user, content)
    return _turn(user_message, reply)


# --- Cards ------------------------------------------------------------------------------
@router.post("/chats/{chat_id}/interactions/{interaction_id}")
async def answer(
    caller: Me, db: Db, chat_id: str, interaction_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Answer a card. The body, as the page's form would say it:

        {"verdict": "allow" | "allow_always" | "deny" | "" (an answer),
         "reason": "…",                     # on a refusal
         "choices": {"<key>": ["label", …]},  # picked options
         "texts": {"<key>": "…"},           # typed: an answer, or a corrected command
         "asks": {"<key>": "…"}}            # a question asked back

    First answer wins -- on the phone, the page or the device's terminal alike;
    a card that is no longer waiting is a 409, and the app takes that as
    "somebody answered already" and refreshes.
    """
    chat = owned_chat(db, chat_id, caller.user)
    items: list[tuple[str, str]] = [
        ("verdict", str(body.get("verdict") or "")),
        ("reason", str(body.get("reason") or "")),
    ]
    for prefix, field in (("choice", "choices"), ("text", "texts"), ("ask", "asks")):
        values = body.get(field)
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            for one in value if isinstance(value, list) else [value]:
                items.append((f"{prefix}.{key}", str(one)))
    answered, remembered, unmatchable = chats_api.answer_card(
        db, chat, interaction_id, JsonForm(items)
    )
    if not answered:
        raise HTTPException(
            status.HTTP_409_CONFLICT, t("That question is no longer waiting for an answer.")
        )
    return {"answered": True, "remembered": remembered, "not_remembered": unmatchable}


__all__ = ["router"]
