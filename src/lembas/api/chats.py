"""Chat creation, messaging and the streaming reply endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.db.models import (
    KIND_AGENT,
    KIND_CHAT,
    KINDS,
    ROLE_ASSISTANT,
    ROLE_USER,
    Chat,
    Folder,
    Message,
    Model,
    User,
)
from lembas.db.session import session_scope
from lembas.security import permissions
from lembas.services import audio as audio_service
from lembas.services import chat as chat_service
from lembas.services import compaction as compaction_service
from lembas.services import context as context_service
from lembas.services import data_groups, device_link, interaction, model_prefs, settings_store, sse
from lembas.services import files as files_service
from lembas.services import generation as generation_service
from lembas.services import jobs as jobs_service
from lembas.services import metrics as metrics_service
from lembas.services import prompts as prompts_service
from lembas.services import steps as steps_service
from lembas.services import subagent as subagent_service
from lembas.services import tokens as tokens_service
from lembas.services import tools as tools_service
from lembas.services.agent import policy as agent_policy
from lembas.services.agent import terminal as terminal_service
from lembas.services.llm.openai_client import LLMError
from lembas.services.markdown import LiveRender, escape_text, render_markdown
from lembas.web.i18n import t
from lembas.web.templating import render, templates

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["chats"])

# Seconds of silence before a comment frame is sent to hold the connection open.
# Well under nginx's 60s default; see services/sse.py:KEEPALIVE.
KEEPALIVE_AFTER = 15.0

# How often the metrics chips are re-sent when nothing else has changed. The
# reply's version does not move while a tool runs on the far machine, but its
# clock does, so without this the counts and tokens/second stand still for most
# of a long agent reply's wall time. A second is slow enough to be free and fast
# enough that the numbers read as live.
METRICS_INTERVAL = 1.0

# How many prompts may wait behind a reply at once. The terminal panel's Auto
# send is what this exists for: a `for` loop in a shell can produce commands
# faster than any model answers them, and a bound with a sentence attached is
# better than four hundred rows nobody meant to write.
MAX_QUEUED = 10


def _device_keeps_history() -> str:
    """Said where a device chat refuses an edit or a regenerate (a function, so it
    is in the reader's language)."""
    return t(
        "This chat runs on a device, which keeps its own history. Send a new message instead."
    )


async def _device_rewind(chat: Chat, asked: Message | None) -> int:
    """Rewind a device chat to the turn `asked` started, on the device, before
    the rows here are rewound (harness spec 1.1.0, `_lembas/session/rewind`).

    The conversation the device's model reads lives in its session, so an
    edit or a regenerate that only rewound the rows here would re-run the turn
    on top of the old one. A device that is not linked, or lists no `rewind`,
    or a turn that has no id here (one typed in the device's terminal) is the
    refusal this route always gave; a refusal from the device -- a session
    still working -- is said in its words. The files the turn changed stay as
    they are, which the control says.
    """
    if asked is None or asked.origin == device_link.ORIGIN_TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, _device_keeps_history())
    try:
        reverted = await device_link.rewind(chat.device_token_id, chat, asked.id)
    except device_link.DeviceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (TimeoutError, device_link.LinkClosed) as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, t("The device did not answer in time.")
        ) from exc
    if reverted is None:
        raise HTTPException(status.HTTP_409_CONFLICT, _device_keeps_history())
    return reverted


def _renew_turn(db: DBSession, message: Message) -> Message:
    """A device chat's turn under a fresh id, everything else carried over.

    The turn's id on the device is the message's id, and a device answers a
    turn id it has run before with the original's answer -- rewound or not,
    since a rewind drops the turn from the conversation and not from the log
    that deduplicates prompts. So a turn sent again after a rewind carries a
    new id, which here means a new row: same place in the order, same words
    (or the edited ones), same attachments. Only for a chat on a device; an
    ordinary chat keeps its row, as it always has.
    """
    fresh = Message(
        chat_id=message.chat_id,
        role=message.role,
        content=message.content,
        origin=message.origin,
        machine=message.machine,
        created_at=message.created_at,
        terminal_attachments_json=message.terminal_attachments_json,
    )
    db.add(fresh)
    db.flush()
    # Through the relationship, so the orphan cascade sees a move and not a
    # removal: assigning `message_id` alone leaves them in the old collection,
    # and deleting the old row would take the files with it.
    for attachment in list(message.attachments):
        fresh.attachments.append(attachment)
    chat = message.chat
    if chat is not None and chat.compacted_through_id == message.id:
        chat.compacted_through_id = fresh.id
    db.delete(message)
    db.flush()
    return fresh

# Which tools a request may compel the model to call. An allow list rather than
# a passthrough: this becomes `tool_choice`, and a name read straight off a form
# would let anyone who can send a message decide what the model must do next.
# Being on this list is not permission to *use* the tool -- `resolve_tools` still
# decides that, and forcing one that was never offered simply does nothing.
FORCEABLE_TOOLS = frozenset({"image_generate"})

# How many things one chat may have switched off. There are a dozen families and
# sixty skills at most, so this is not a limit anybody reaches by hand -- it is
# there so a crafted POST cannot grow the column without bound.
MAX_SCOPE_KEYS = 200


def _owned_chat(db: DBSession, chat_id: str, user_id: str) -> Chat:
    chat = db.get(Chat, chat_id)
    # 404 rather than 403 for someone else's chat: whether a given id exists is
    # not information this endpoint should hand out.
    if chat is None or chat.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That chat no longer exists."))
    return chat


def _new_chat(
    db: DBSession,
    user: User,
    *,
    folder_id: str = "",
    model_id: str = "",
    temporary: bool = False,
    kind: str = KIND_CHAT,
    ssh_profile_id: str = "",
    project_dir: str = "",
    agent_mode: str = "",
    reasoning_effort: str = "",
    scope_off: frozenset[str] = frozenset(),
    scope_on: frozenset[str] = frozenset(),
    skills_off: frozenset[str] = frozenset(),
) -> Chat:
    """Create a chat row, resolving which model it should use.

    An agent chat's connection is settled here and never again. That is the
    lock: the harness, the tools offered and the approval loop all differ, so a
    conversation whose earlier turns ran somewhere else is not one conversation.

    The mode is *not* part of that lock and is accepted here so it can be chosen
    before the first word. Without it, reaching Plan mode meant starting a chat
    in Manual, sending something to make the chat exist, and only then being
    offered the control -- by which point the model had already answered under
    the wrong rules. The reasoning effort is accepted for the same reason, and
    wins over the model's default: an explicit choice beats an inherited one.

    A folder's own defaults fill in anything the request left empty, and nothing
    it filled in. That order is the point: the folder says what this piece of
    work usually needs, and the screen in front of somebody says what they want
    this time. The folder's system prompt is deliberately not among them -- it
    is read at request time so that editing the folder later reaches the chats
    already in it.
    """
    folder = db.get(Folder, folder_id) if folder_id else None
    if folder is not None and folder.user_id != user.id:
        folder = None
    if folder is not None:
        model_id = model_id or folder.model_id
        kind = kind or folder.kind
        if kind == KIND_AGENT:
            ssh_profile_id = ssh_profile_id or folder.ssh_profile_id
            project_dir = project_dir or folder.project_dir
            agent_mode = agent_mode or folder.agent_mode

    chosen = None
    if model_id:
        match = next(
            (m for m in chat_service.available_models(db, user) if m.model_id == model_id), None
        )
        if match is not None:
            chosen = (match.model_id, match.connection_id)
    if chosen is None:
        chosen = chat_service.default_model(db, user)

    # `Model.params_json` has said "default sampling params applied to new chats
    # using this model" since it was added and has been applied nowhere. It is
    # empty on every existing row, so honouring it now changes nothing until an
    # administrator sets something -- and it is what makes a per-model default
    # reasoning effort possible without a second column meaning the same thing.
    defaults: dict = {}
    if chosen is not None:
        model = db.scalar(
            select(Model).where(
                Model.model_id == chosen[0], Model.connection_id == chosen[1]
            )
        )
        if model is not None:
            defaults = dict(model.params_json or {})
            # This person's own effort for the model (Settings -> Models)
            # in place of the administrator's -- still a seed, so the effort
            # chosen on the new-chat screen below wins over both.
            defaults = model_prefs.seed(db, user, model, defaults)

    # An agent chat runs on a linked LLeMbas CLI and nowhere else. The
    # field keeps its old name: it is what the composer's select is called.
    device = _device_target(db, user, kind, ssh_profile_id)
    chat = Chat(
        user_id=user.id,
        # `folder`, not `folder_id`: the raw value is what the request asked
        # for, and the lines above already discarded it when it names somebody
        # else's folder. Storing the raw one put the chat there anyway -- so the
        # ownership check governed which *seeds* were applied and not where the
        # chat actually went, and a folder's system prompt is read on every turn
        # from wherever the chat sits.
        folder_id=folder.id if folder is not None else None,
        model_id=chosen[0] if chosen else "",
        connection_id=chosen[1] if chosen else None,
        # The chat's data group is its model's, fixed now. It is what every
        # later turn reads memories and notes from, and what decides which models
        # this chat may be switched to -- see services/data_groups.py.
        data_group_id=(
            data_groups.for_pair(db, user, chosen[0], chosen[1])
            if chosen
            else data_groups.DEFAULT_GROUP
        ),
        temporary=temporary,
        kind=KIND_AGENT if device is not None else KIND_CHAT,
        device_token_id=device[0] if device is not None else None,
        project_dir=(project_dir.strip() or device[1]) if device is not None else "",
        params_json=defaults,
    )
    # Ignored rather than refused when it is not a mode, matching how every
    # other bad value here collapses: somebody who mistypes should get a chat
    # under the safest rules, not an error page holding their message hostage.
    # Left alone entirely on a plain chat, where it means nothing.
    if device is not None and agent_mode.strip() in agent_policy.MODES:
        chat.agent_mode = agent_mode.strip()
    # After the model's defaults, so choosing one on the new-chat screen wins
    # over the administrator's.
    #
    # `"off"` is a sentinel, and it has to be: `reasoning_effort` arrives as
    # `Form("")`, so an absent field and an empty one are indistinguishable --
    # the FastAPI trap this codebase has already been bitten by once. With
    # `value=""` on the off option, the reader would pick "off", the value would
    # fall out of EFFORTS, the model's default seeded above would stay, and they
    # would silently get "high". The picker shows what will be sent, so the two
    # have to agree.
    wanted_effort = reasoning_effort.strip().lower()
    if wanted_effort == "off":
        chat.params_json = {k: v for k, v in chat.params_json.items() if k != "reasoning_effort"}
    elif wanted_effort in chat_service.EFFORTS:
        chat.params_json = {**chat.params_json, "reasoning_effort": wanted_effort}

    # What the scope menu was set to before the first word. Only the *off* ones
    # are written, because absent means on and one representation of "on" is
    # what makes "why is this off?" have a single answer.
    #
    # This narrows and can never widen: `resolve_tools` applies `scope_json`
    # after the capability, permission and instance gates, so a crafted request
    # naming a gate that was never offered switches off something that was not
    # on -- which is exactly nothing. That is why these need no validation
    # against the offered set here.
    scoped = dict.fromkeys(scope_off, False)
    # A gate this person's own default switches off for the model, ticked on
    # the new-chat screen anyway: stored as an explicit True, the one
    # case where "on" is written down, because otherwise the default would win
    # over the choice made for this chat.
    if chosen is not None:
        defaulted = model_prefs.tools_off(db, user, chosen[0])
        scoped.update(dict.fromkeys(set(scope_on) & defaulted - set(scope_off), True))
    scoped_skills = dict.fromkeys(skills_off, False)
    if scoped or scoped_skills:
        chat.scope_json = {
            **(chat.scope_json or {}),
            **({"families": scoped} if scoped else {}),
            **({"skills": scoped_skills} if scoped_skills else {}),
        }
    db.add(chat)
    db.commit()
    return chat


@router.post("/start", dependencies=[Depends(require_permission("chat.create"))])
async def start_chat(
    db: Db,
    user: RequiredUser,
    content: str = Form(""),
    file_ids: list[str] = Form(default=[]),
    folder_id: str = Form(""),
    model_id: str = Form(""),
    temporary: bool = Form(False),
    kind: str = Form(KIND_CHAT),
    ssh_profile_id: str = Form(""),
    project_dir: str = Form(""),
    agent_mode: str = Form(""),
    reasoning_effort: str = Form(""),
    # The scope menu, as it stood before the first word. `scope_all` names every
    # gate the menu drew and is always submitted; `scope_on` names only the
    # ticked ones, because that is all a browser sends. The difference is what
    # was switched off -- see the note in `chat/_composer.html` for why the
    # control is not simply inverted.
    scope_all: list[str] = Form(default=[]),
    scope_on: list[str] = Form(default=[]),
    scope_skill_all: list[str] = Form(default=[]),
    scope_skill_on: list[str] = Form(default=[]),
) -> Response:
    """Create a chat from its first message.

    Chats are made here rather than by a "New chat" button so that an opened-
    and-abandoned chat never exists: the row appears only once there is
    something in it. The reply then streams the same way as any other, because
    /chat/{id} renders the unfinished assistant message with its sse-connect.
    """
    content = content.strip()
    if not content and not file_ids:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    chat, _, _ = start_new_chat(
        db,
        user,
        content,
        file_ids=file_ids,
        folder_id=folder_id,
        model_id=model_id,
        temporary=temporary,
        kind=kind,
        ssh_profile_id=ssh_profile_id,
        project_dir=project_dir,
        agent_mode=agent_mode,
        reasoning_effort=reasoning_effort,
        scope_off=frozenset(scope_all) - frozenset(scope_on),
        scope_on=frozenset(scope_on),
        skills_off=frozenset(scope_skill_all) - frozenset(scope_skill_on),
    )

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["HX-Redirect"] = f"/chat/{chat.id}"
    return response


def start_new_chat(
    db: DBSession,
    user: User,
    content: str,
    *,
    file_ids: list[str] | None = None,
    **settings,
) -> tuple[Chat, Message, Message]:
    """Make a chat from its first message and start the reply: `(chat, the
    person's row, the reply's row)`. `settings` are `_new_chat`'s.

    The core of `start_chat`, shared with the app's JSON door. The quota is
    checked *before* `_new_chat`, not after: a refusal that has already written
    the row leaves an empty chat in the sidebar as the visible result of being
    told no. There is no chat yet to exclude from the count, and none is needed
    -- nothing can be running for a chat that does not exist.
    """
    _refuse_extra_reply(db, None, user)
    chat = _new_chat(db, user, **settings)
    user_message = chat_service.create_message(db, chat, ROLE_USER, content)
    if file_ids:
        files_service.claim(db, ids=file_ids, user_id=user.id, message_id=user_message.id)
    assistant = chat_service.create_message(
        db, chat, ROLE_ASSISTANT, "", complete_=False, model_id=chat.model_id
    )
    generation_service.ensure(chat.id, assistant.id)
    return chat, user_message, assistant


def _device_target(db: DBSession, user: User, kind: str, value: str) -> tuple[str, str] | None:
    """A linked device an agent chat is being pointed at (`device:<token id>`), as
    (token id, its first allowed root), or None.

    Only a device of this person's that is linked right now: a chat started on one
    that is not would have nothing to write its first reply. Every "no" makes
    the chat an ordinary one rather than an error: somebody whose permission was
    withdrawn between opening the composer and sending must not lose the
    message.
    """
    from lembas.db.models import TOKEN_DEVICE, ApiToken
    from lembas.security import api_tokens, permissions
    from lembas.services import device_link

    if kind != KIND_AGENT or not value.startswith("device:"):
        return None
    if not permissions.has(db, user, "api.use") or not permissions.has(db, user, "tools.agent"):
        return None
    if not settings_store.agents(db).get("enabled"):
        return None
    token = db.get(ApiToken, value.removeprefix("device:"))
    if token is None or token.user_id != user.id or token.kind != TOKEN_DEVICE:
        return None
    if not api_tokens.live(token):
        return None
    link = device_link.link_for(token.id)
    if link is None:
        return None
    return token.id, (link.roots or [""])[0]


# There is deliberately no route that creates an empty chat. Starting one is
# navigation to /chat (optionally ?model=...), and the row is written by
# /start when the first message is actually sent.


@router.get("/{chat_id}/inspect")
async def inspect_chat(request: Request, db: Db, user: RequiredUser, chat_id: str) -> Response:
    """What this chat would send upstream right now.

    Owner-checked *and* admin-checked, not admin alone. `permissions.resolve`
    giving an admin everything is about configuration, which they can grant
    themselves anyway; reading someone's conversation is a different act, which
    is why `sharing.visible_to` has no admin branch either. An inspector that
    could dump any user's transcript would be that branch under another name.

    Rebuilt, not recorded. Recording every request would store a copy of the
    whole conversation against every message, which grows quadratically with
    chat length -- and the thing an administrator actually wants to see is what
    the current configuration produces. The panel says so in as many words.
    """
    chat = _owned_chat(db, chat_id, user.id)
    if not user.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, t("The inspector is restricted to administrators.")
        )

    offered = tools_service.enabled_tools(db, chat, user)
    payload = chat_service.build_request(db, chat, tools=offered, user=user)
    last = db.scalar(
        select(Message)
        .where(Message.chat_id == chat.id, Message.role == ROLE_ASSISTANT)
        .order_by(Message.created_at.desc())
    )

    messages = payload.get("messages") or []
    system = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""

    return render(
        request,
        "chat/_inspector_body.html",
        {
            "chat": chat,
            "system": system,
            "request_json": _pretty(_redact(payload)),
            "row": last,
            "metrics": metrics_service.from_message(last.usage_json if last else None),
            "tool_names": [
                (t.get("function") or {}).get("name", "") for t in offered
            ],
            "model": chat_service.model_for(db, chat),
        },
    )


@router.get("/{chat_id}/usage")
async def chat_usage(request: Request, db: Db, user: RequiredUser, chat_id: str) -> Response:
    """What this conversation has cost, and how full the window is.

    Owner-checked and nothing else: it is your own chat's totals. Unlike the
    inspector next door there is no admin branch, because there is no reason
    for one -- the numbers describe a conversation, and reading somebody's
    conversation is exactly what `sharing` has no admin branch for either.

    Summed from what each reply recorded rather than recomputed: an endpoint
    that reported no usage contributed an estimate at the time, and re-deriving
    it now with a different estimator would make the totals move under a chat
    that had not changed.
    """
    chat = _owned_chat(db, chat_id, user.id)
    replies = list(
        db.scalars(
            select(Message)
            .where(Message.chat_id == chat.id, Message.role == ROLE_ASSISTANT)
            .order_by(Message.created_at)
        )
    )

    totals = {"prompt": 0, "completion": 0, "total": 0}
    estimated = False
    for reply in replies:
        usage = metrics_service.from_message(reply.usage_json)
        totals["prompt"] += usage.prompt_tokens
        totals["completion"] += usage.completion_tokens
        totals["total"] += usage.total_tokens
        estimated = estimated or usage.estimated

    last = replies[-1] if replies else None
    return render(
        request,
        "chat/_usage.html",
        {
            "chat": chat,
            "totals": totals,
            "estimated": estimated,
            "replies": len(replies),
            "metrics": metrics_service.from_message(last.usage_json if last else None),
            "model": chat_service.model_for(db, chat),
            "cost": _chat_cost(db, [reply.id for reply in replies]),
        },
    )


def _chat_cost(db: DBSession, reply_ids: list[str]) -> dict | None:
    """What this chat's replies cost, summed from what was stored on each when
    it was written -- not a price applied now. None when none was priced."""
    from lembas.services import pricing

    if not reply_ids:
        return None
    rows = db.execute(
        select(Message.currency, func.sum(Message.cost_micro))
        .where(Message.id.in_(reply_ids), Message.cost_micro.is_not(None))
        .group_by(Message.currency)
    ).all()
    if not rows:
        return None
    # One line per currency, should the instance's have changed in between.
    return {
        "money": " + ".join(
            pricing.format_money(int(total or 0), currency or settings_store.currency(db))
            for currency, total in rows
        )
    }


# Roughly what a downscaled phone photo comes to as base64. The exact figure
# does not matter; putting megabytes of it into the DOM does.
_REDACTED_URI = "data:…base64 image omitted…"
MAX_INSPECT_CHARS = 40_000


def _redact(payload: dict) -> dict:
    """Replace image data URIs before dumping.

    Nothing else is hidden -- fidelity is the whole point of the panel, and API
    keys never appear because `build_request` returns a body, not headers.
    """
    messages = []
    for message in payload.get("messages") or []:
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    parts.append({"type": "image_url", "image_url": {"url": _REDACTED_URI}})
                else:
                    parts.append(part)
            message = {**message, "content": parts}
        messages.append(message)
    return {**payload, "messages": messages}


def _pretty(payload: dict) -> str:
    text = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    if len(text) > MAX_INSPECT_CHARS:
        return text[:MAX_INSPECT_CHARS] + "\n… truncated"
    return text


@router.post("/{chat_id}/compact")
async def compact_chat(request: Request, db: Db, user: RequiredUser, chat_id: str) -> Response:
    """Summarise the earlier turns and stop sending them.

    No permission of its own: compaction changes only what one chat sends
    upstream, and gating it would mean answering "why can this user not tidy
    their own conversation".
    """
    chat = _owned_chat(db, chat_id, user.id)

    unfinished = db.scalar(
        select(Message).where(Message.chat_id == chat.id, Message.complete.is_(False))
    )
    if unfinished is not None:
        # Summarising a transcript that is still being written races
        # build_request. Queuing it is a state machine nobody asked for.
        raise HTTPException(
            status.HTTP_409_CONFLICT, t("Wait for the current reply to finish, then compact.")
        )

    upto = compaction_service.last_complete(db, chat)
    # A chat on a device: the device's session is the context its model
    # reads, so the device compacts it; the divider here marks the same point.
    if chat.device_token_id:
        if upto is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, t("There is nothing here to summarise yet.")
            )
        if refused := device_link.refusal(db, user, chat.device_token_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, refused)
        try:
            summary = await device_link.compact_session(
                chat.device_token_id, chat.device_session_id
            )
        except device_link.DeviceError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, t("Not compacted: %(error)s", error=exc)
            ) from exc
        if not summary:
            raise HTTPException(
                status.HTTP_409_CONFLICT, t("The device returned no summary, so nothing changed.")
            )
        compaction_service.apply(chat, summary=summary, upto=upto, db=db)
        db.commit()
        return templates.TemplateResponse(
            request, "chat/_thread.html", {"request": request, **_thread_context(db, chat, user)}
        )

    template = prompts_service.resolve(db, "task.compact")
    if not template.strip():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            t("Compaction is turned off: its prompt is empty under Admin → Prompts."),
        )

    if upto is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, t("There is nothing here to summarise yet.")
        )

    try:
        endpoint, model_id = chat_service.resolve_endpoint(db, chat)
    except Exception as exc:  # said, rather than a bare 500
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            t("This chat has no model that can summarise it -- pick one in the composer first."),
        ) from exc
    transcript = compaction_service.transcript(db, chat, upto=upto)
    previous = compaction_service.previous_summary_block(chat)

    try:
        summary = await chat_service.summarise_for_compaction(
            endpoint,
            model_id,
            transcript=transcript,
            previous_summary=previous,
            template=template,
        )
    except LLMError as exc:
        # Said in the toast; a bare 500 would show on the page as nothing
        # happening at all.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, t("Could not compact: %(error)s", error=exc.message)
        ) from exc
    if not summary:
        raise HTTPException(
            status.HTTP_409_CONFLICT, t("The model returned no summary, so nothing changed.")
        )

    compaction_service.apply(chat, summary=summary, upto=upto, db=db)
    db.commit()
    log.info("chat %s compacted through %s", chat.id, upto.id)

    return templates.TemplateResponse(
        request, "chat/_thread.html", {"request": request, **_thread_context(db, chat, user)}
    )


@router.post("/{chat_id}/scope")
async def set_scope(
    db: Db,
    user: RequiredUser,
    chat_id: str,
    kind: str = Form(""),
    name: str = Form(""),
    on: bool = Form(False),
) -> Response:
    """Turn one thing this chat may use on or off. **Narrowing only.**

    Nothing here widens anything. `resolve_tools` applies this *after* the
    model's capabilities, the reader's permissions and the instance
    configuration, so a crafted POST turning something on reaches a tool those
    gates have already removed -- there is a test for exactly that.

    On is stored by **removing** the key rather than by writing True, so absent
    stays the single representation of "on" and the column cannot grow a row per
    family per chat. Bounded, so a crafted request cannot grow it either.

    JSON reassignment rather than mutation: a plain dict assignment into a JSON
    column is not detected.
    """
    chat = _owned_chat(db, chat_id, user.id)
    bucket = {"family": "families", "skill": "skills"}.get(kind.strip())
    wanted = name.strip()[:64]
    if bucket is None or not wanted:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("Say what to turn on or off."))

    scope = dict(chat.scope_json or {})
    entries = dict(scope.get(bucket) or {})
    if on and bucket == "families" and wanted in model_prefs.tools_off(db, user, chat.model_id):
        # Back on over this person's own default for the model: the
        # one "on" that has to be written down, or the default would win.
        entries[wanted] = True
    elif on:
        entries.pop(wanted, None)
    else:
        if len(entries) >= MAX_SCOPE_KEYS:
            raise HTTPException(status.HTTP_409_CONFLICT, t("Too many things switched off."))
        entries[wanted] = False

    if entries:
        scope[bucket] = entries
    else:
        scope.pop(bucket, None)
    chat.scope_json = scope
    db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{chat_id}/keep")
async def keep_chat(db: Db, user: RequiredUser, chat_id: str) -> Response:
    """Stop a temporary chat being temporary.

    A conversation that turns out to matter has to have a way out; without one,
    the sweep destroys it a day later with no recourse, and people discover that
    exactly once.
    """
    chat = _owned_chat(db, chat_id, user.id)
    chat.temporary = False
    db.commit()

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    # The sidebar has to gain a row and the topbar has to lose a badge; a full
    # refresh is one line against a handful of out-of-band fragments.
    response.headers["HX-Refresh"] = "true"
    return response


@router.post("/{chat_id}/leave")
async def leave_chat(db: Db, user: RequiredUser, chat_id: str) -> Response:
    """The page of a temporary chat was left.

    Only marks it: the sweep deletes the chat `LEAVE_GRACE` later unless it is
    opened again first, because a reload reports a leave as well and must not
    lose the conversation. Does nothing for an ordinary chat, a kept one, or an
    instance whose temporary chats live for a set time -- the beacon is only on
    the page when the setting is 0, but a stale page could still send it.
    """
    chat = db.get(Chat, chat_id)
    if (
        chat is not None
        and chat.user_id == user.id
        and chat_service.read_only_temporary(chat)
        and chat_service.temporary_lifetime(db) == 0
    ):
        chat_service.mark_left(chat.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _is_new(chat: Chat) -> bool:
    """Made in the last few minutes -- a terminal session's chat the sidebar on an
    open page has never drawn."""
    created = chat.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return datetime.now(UTC) - created < timedelta(minutes=10)


@router.get("/unread")
async def unread_poll(request: Request, db: Db, user: RequiredUser) -> Response:
    """Dots for the sidebar, and an announcement for anything newly arrived.

    Polled rather than pushed: a browser sitting on a different chat has no
    open connection to the one that finished, and a second always-on channel
    per tab is a lot of machinery for a green dot.

    Returns out-of-band spans so only the dots change -- re-rendering the whole
    sidebar would reset the folder open/closed state on every tick.

    **Everything that can arrive is announced, not only chats.** The dots have
    covered Reports and Messages since those sections existed, but the
    announcement did not: only a chat reply produced an `HX-Trigger`, so a
    scheduled run that filed a report or posted into Messages lit a dot in the
    corner and said nothing at all. That is precisely the arrival nobody is
    watching for -- a chat reply is one you asked for a moment ago and are
    probably looking at, while a schedule fires while you are elsewhere. So each
    kind carries its own `*_notified` flag and each announces once.

    The payload is a list of items rather than a list of titles, because the
    browser notification wants somewhere to go when it is clicked and a title on
    its own cannot say where.
    """
    chats = list(
        db.scalars(
            select(Chat).where(
                Chat.user_id == user.id,
                Chat.archived.is_(False),
                # A temporary chat has no sidebar row, so a dot has nowhere to
                # land and the toast would name a chat nobody can navigate to.
                Chat.temporary.is_(False),
                # And neither has a conversation belonging to a section rather
                # than to the tree. Those get one dot per *section*, below --
                # forty task chats must not mean forty out-of-band spans aimed
                # at elements that are not on the page. htmx says nothing at all
                # when an OOB target is missing, so this would be silent waste.
                Chat.kind.in_(KINDS),
            )
        )
    )

    # What to announce, in the order it will be read out. Each entry carries
    # where it came from and where to go, because a browser notification is a
    # thing you click.
    items: list[dict[str, str]] = []

    fresh = [c for c in chats if c.unread and not c.unread_notified]
    for chat in fresh:
        chat.unread_notified = True
        items.append({"kind": "chat", "title": chat.title, "url": f"/chat/{chat.id}"})

    markup = "".join(
        f'<span id="unread-{c.id}" class="unread-dot" hx-swap-oob="true"'
        f'{"" if c.unread else " hidden"} title="New reply"></span>'
        for c in chats
    )
    # A session a linked device's terminal shared is a chat no open page
    # has a row for: the tree comes along, so it appears where it belongs rather
    # than only in a toast. Only then -- a re-rendered tree forgets which folders
    # were open.
    if any(c.device_session_id and _is_new(c) for c in fresh):
        from lembas.api.pages import sidebar_context

        tree = templates.get_template("partials/_sidebar_tree.html").render(
            {
                "request": request,
                "chat": None,
                "user": user,
                "oob": False,
                **sidebar_context(db, user),
            }
        )
        markup += tree.replace(
            '<div id="sidebar-tree">', '<div id="sidebar-tree" hx-swap-oob="outerHTML">', 1
        )

    if items:
        db.commit()

    response = HTMLResponse(markup)
    if items:
        # HX-Trigger carries it; ui.js turns it into a toast, a browser
        # notification and a count in the tab title.
        response.headers["HX-Trigger"] = json.dumps({"lembas:unread": {"items": items}})
    return response


@router.get("/{chat_id}/device-model")
async def device_model_badge(
    request: Request, db: Db, user: RequiredUser, chat_id: str
) -> Response:
    """The topbar's "on the device: …" badge, polled: the device may
    switch its session's model at any time, from its terminal or its settings,
    and the chat follows (`device_link._remodel`)."""
    chat = _owned_chat(db, chat_id, user.id)
    label = ""
    if not chat.device_model:
        current = next(
            (m for m in chat_service.available_models(db, user) if m.model_id == chat.model_id),
            None,
        )
        label = current.label if current is not None else ""
    return templates.TemplateResponse(
        request,
        "chat/_device_model.html",
        {"request": request, "chat": chat, "picker_label": label},
    )


@router.get("/{chat_id}/tail")
async def thread_tail(db: Db, user: RequiredUser, chat_id: str, after: str = "") -> Response:
    """Turns this page has not got yet, appended to the transcript it is showing.

    A reply can begin without a request from the browser: `wake.wake_chat`
    writes a turn and starts a reply when a schedule fires, or when a background
    helper ends and its note reaches an idle chat. There is no channel to tell
    the page about it. The only
    stream here is per-message and it is opened by the `sse-connect` on an
    incomplete assistant bubble -- a bubble this page does not have, because the
    reply that created it started somewhere else. `_queue_frames` proves the swap
    works, but it can only ride a stream that is already open.

    So the page asks. Polled for the same reason `/unread` is: a second always-on
    connection per tab is a great deal of machinery for something that happens a
    few times a day. The cursor comes from the browser -- see `app.js`, which
    reads the last bubble in `#thread`, the honest answer to what this page
    already holds.
    """
    chat = _owned_chat(db, chat_id, user.id)

    # Somebody is looking at this chat, which is what `unread` means the absence
    # of. `_persist` marks a reply unread whenever `generation.followers == 0`,
    # and that is true of a job-woken reply even with the reader watching it --
    # so today the toast announces a chat that is already on screen. This is
    # `pages.chat_detail` said again for as long as the page stays open rather
    # than once when it loads, and it is cleared whether or not anything arrived:
    # the claim being made is that somebody is here.
    #
    # Not airtight, and not pretending to be: the sidebar polls on 10s and this
    # on 5s, so this usually wins, but a badly timed tick can still raise one
    # toast for the chat in front of you.
    if chat.unread or chat.unread_notified:
        chat.unread = False
        chat.unread_notified = False
        db.commit()

    # No cursor, a cursor from another chat, or one naming a row a rewind has
    # since deleted. Answering with the transcript would append a second copy of
    # every bubble the page still holds, and a page whose history was rewritten
    # underneath it is one only a reload can reconcile -- which is not this
    # route's decision to make, with a half-typed message possibly in the box.
    cut = db.get(Message, after) if after else None
    if cut is None or cut.chat_id != chat.id:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # The cut is read from the row rather than taken as a timestamp on the wire,
    # which is what makes `_inject`'s restamp harmless: if the page's last bubble
    # was the assistant placeholder and the placeholder moved, the cut moves with
    # it. Compared in SQL and never in Python, for the reason `compaction.moment`
    # exists -- a row read back from SQLite is naive and one still in the session
    # is aware, and `>` between them raises.
    #
    # The id clause is not decoration. Under a bare `>` a row sharing the cut's
    # microsecond is skipped forever; with it, at most the one sorting lower is.
    fresh = list(
        db.scalars(
            select(Message)
            .where(
                Message.chat_id == chat.id,
                or_(
                    Message.created_at > cut.created_at,
                    and_(Message.created_at == cut.created_at, Message.id > cut.id),
                ),
            )
            .order_by(Message.created_at, Message.id)
        )
    )
    if not fresh:
        # 204 and not an empty 200: htmx does not swap on a 204, where an empty
        # body would still fire a swap and a settle on every open page every
        # five seconds.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Queued turns come too, unfiltered. A completion waiting behind a running
    # reply is exactly what the reader wants to watch arrive, and its bubble can
    # never carry `sse-connect` -- `_message.html` requires the assistant role
    # for that. When the running reply ends, `_queue_frames` deletes the stale
    # node out of band and re-renders it in place, so arriving early costs
    # nothing.
    #
    # No `just_finished`: that flag is what read-aloud-automatically keys off,
    # and a bubble the page merely missed must not start talking.
    return HTMLResponse("".join(_render_bubble(db, chat, user, row) for row in fresh))


@router.post("/{chat_id}/messages")
async def post_message(
    request: Request,
    db: Db,
    user: RequiredUser,
    chat_id: str,
    content: str = Form(""),
    file_ids: list[str] = Form(default=[]),
    force_tool: str = Form(""),
) -> Response:
    """Persist the user's turn and hand back the pair of bubbles.

    The assistant bubble comes back empty, carrying the sse-connect attribute
    that opens the stream below. Splitting it this way means the POST returns
    immediately and the slow part is a separate, resumable connection.

    `force_tool` is `/image` and nothing else. It is checked against a fixed
    list rather than passed through: this ends up in `tool_choice`, and a name
    taken from a form would let anybody who can send a message pick which tool
    the model is compelled to call. Whether that tool is *offered* is still
    decided by `resolve_tools`, so this can only ever narrow to something the
    chat was already allowed.
    """
    chat = _owned_chat(db, chat_id, user.id)

    content = content.strip()
    # "Here, look at this" with no words is a legitimate turn, so an empty
    # message is only empty when it carries nothing at all.
    if not content and not file_ids:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    forced = force_tool.strip() if force_tool.strip() in FORCEABLE_TOOLS else ""
    return _send(request, db, chat, user, content, file_ids=file_ids, force_tool=forced)


@router.post("/{chat_id}/continue")
async def continue_chat(request: Request, db: Db, user: RequiredUser, chat_id: str) -> Response:
    """Continue: carry on after a stop, or after an answer that ended too
    soon -- the LLeMbas CLI's ctrl+g, with its sentence (`tasks/continue.md` of the
    harness spec). An ordinary turn: queued like any other while a reply runs,
    and on a device chat it goes to the device as a prompt."""
    from lembas.services import harness_spec

    chat = _owned_chat(db, chat_id, user.id)
    return _send(request, db, chat, user, harness_spec.prompt("tasks/continue.md"))


def _reply_in_flight(db: DBSession, chat: Chat) -> bool:
    """Whether this chat already has a reply being written.

    The row is the authority, not the registry: a restart leaves an incomplete
    assistant message behind with no `Generation` anywhere, and that row is what
    starts the reply again on the next page load. The registry is consulted too,
    for the sliver in which a generation is still running and its row has
    already been written -- `_persist` sets `complete` before `_run` sets
    `done`.
    """
    unfinished = db.scalar(
        select(Message).where(Message.chat_id == chat.id, Message.complete.is_(False))
    )
    return unfinished is not None or generation_service.running_for(chat.id) is not None


# How long an edit or a regenerate waits for the reply it stopped to end. Stop
# lands within a second now (`generation._until_stopped`); a device gets its own
# grace (`device_link.DEVICE_STOP_GRACE`) inside this.
STOP_WAIT_SECONDS = 8.0


async def _stop_running(db: DBSession, chat: Chat) -> None:
    """Stop the reply this chat is writing, if any, and wait for it to end.

    For a rewind: an edit or a regenerate pressed while a reply runs means "not
    that -- this", which is Stop and then the new turn. Refused with a 409 only
    if the reply will not end in time, which leaves nothing half-done.
    """
    running = generation_service.running_for(chat.id)
    if running is None:
        return
    generation_service.request_stop(running.message_id)
    deadline = time.monotonic() + STOP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if generation_service.running_for(chat.id) is None:
            db.expire_all()
            return
        await asyncio.sleep(0.1)
    raise HTTPException(
        status.HTTP_409_CONFLICT, t("Wait for the current reply to finish, or stop it.")
    )


def _note_rewind(chat: Chat) -> None:
    """An agent chat's transcript went back and the machine did not.

    Deliberately no attempt to undo anything out there. The project directory is
    somebody's real working tree, and deleting their work to match a rewound
    transcript would be far worse than the inconsistency. The device is told
    through `rewind` (`device_link.rewind`) where it can be, and keeps its files
    either way. `Chat.rewound_at` used to be stamped here and was read by
    nothing; the column stays (the schema is additive) and is no longer written.
    """
    return None


def _too_many_replies(db: DBSession, chat: Chat | None, user: User) -> str:
    """Why this account may not start another reply right now, or "".

    In-process, and that is exact rather than approximate only because this
    application runs one worker -- see the first known limit in the roadmap. With
    several, this becomes a guess, and a quota that is a guess should be a
    number in the database instead. Stated here rather than discovered.
    """
    from lembas.security import permissions

    ceiling = permissions.limit(db, user, "concurrent_replies")
    if ceiling <= 0:
        return ""
    mine = {
        row[0]
        for row in db.execute(select(Chat.id).where(Chat.user_id == user.id)).all()
    }
    # `chat` is None on the new-chat path, where there is no row yet and so
    # nothing to exclude -- every running reply of theirs counts.
    here = chat.id if chat is not None else None
    running = sum(
        1
        for chat_id in mine
        if chat_id != here and generation_service.running_for(chat_id) is not None
    )
    if running < ceiling:
        return ""
    if running == 1:
        return t(
            "You already have 1 reply being written, which is this account's limit. "
            "Wait for one to finish."
        )
    return t(
        "You already have %(n)s replies being written, which is this account's limit. "
        "Wait for one to finish.",
        n=running,
    )


def _refuse_extra_reply(db: DBSession, chat: Chat | None, user: User) -> None:
    """Raise if this account is already writing as many replies as it may.

    A function rather than two lines repeated, because it is repeated five
    times now. It used to be called once -- from `_send`, which serves
    `post_message` and `execute_plan` -- while four other routes start a
    generation: `start_chat`, `edit_message`, `send_queued_now` and
    `regenerate`. So a group's `concurrent_replies` was reached by sending into
    a chat that already existed and walked straight past by pressing New chat,
    which is the commonest way to start a reply there is. A quota you can step
    over by using the obvious button is not a quota.
    """
    if busy := _too_many_replies(db, chat, user):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, busy)


def write_turn(
    db: DBSession,
    chat: Chat,
    user: User,
    content: str,
    *,
    file_ids: list[str] | None = None,
    force_tool: str = "",
) -> tuple[Message, Message | None]:
    """Write a turn and start the reply: `(the person's row, the reply's row)`,
    the second None when the turn was queued behind a reply already running.

    The core of `_send`, without the HTML: the app's JSON door
    (`api/v2/chats.py`) sends through this too, so the two cannot drift.

    Shared by the composer and by anything else that puts words into a
    conversation on somebody's behalf -- carrying out a plan, for one. One path
    rather than two, so a second way of sending cannot drift from the first.

    If a reply is already being written, the turn is *queued* instead: written,
    shown, and not sent. Starting a second reply here is what used to happen,
    and it produced two generations answering the same chat from two different
    prefixes of it, with Stop pointing at whichever bubble came first in the
    document.
    """
    # How many of *this account's* chats are already writing. Checked here and
    # not inside `generation`, because this is where there is somebody to tell:
    # a schedule firing or a finished job waking a chat has nobody at the
    # keyboard, and refusing those would be a quota silently eating work an
    # administrator set up on purpose.
    #
    # This chat's own reply does not count against it -- a second message here
    # is queued rather than sent, a few lines down, and that path is what the
    # queue is for.
    _refuse_extra_reply(db, chat, user)

    if queued := _reply_in_flight(db, chat):
        waiting = db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.chat_id == chat.id, Message.queued.is_(True))
        )
        if waiting >= MAX_QUEUED:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                t("There are already %(n)s messages waiting to be sent.", n=MAX_QUEUED),
            )

    user_message = chat_service.create_message(db, chat, ROLE_USER, content, queued=queued)
    if file_ids:
        files_service.claim(db, ids=file_ids, user_id=user.id, message_id=user_message.id)
        db.refresh(user_message)

    if queued:
        # One bubble and no assistant placeholder. The streaming shell is the
        # only thing that starts a generation, so a placeholder here would be a
        # second concurrent reply -- exactly what the queue exists to prevent.
        if (live := generation_service.running_for(chat.id)) is not None:
            # So a reply between two rounds of tool calls notices it, and so
            # anybody following sees the status change.
            live.touch()
        return user_message, None

    assistant_message = chat_service.create_message(
        db, chat, ROLE_ASSISTANT, "", complete_=False, model_id=chat.model_id
    )
    generation_service.ensure(chat.id, assistant_message.id, force_tool=force_tool)
    return user_message, assistant_message


def _send(
    request: Request,
    db: Db,
    chat: Chat,
    user: User,
    content: str,
    *,
    file_ids: list[str] | None = None,
    force_tool: str = "",
) -> Response:
    """Write a turn, start the reply, and hand back the pair of bubbles.

    Shared by the composer and by anything else that puts words into a
    conversation on somebody's behalf -- carrying out a plan, for one. The
    writing is `write_turn`; this is the page's rendering of it.
    """
    user_message, assistant_message = write_turn(
        db, chat, user, content, file_ids=file_ids, force_tool=force_tool
    )
    if assistant_message is None:
        return templates.TemplateResponse(
            request,
            "chat/_message.html",
            {
                "request": request,
                "message": user_message,
                "chat": chat,
                "user": user,
                "models_by_id": {
                    m.model_id: m for m in chat_service.available_models(db, user)
                },
                **audio_service.template_flags(db, user),
            },
        )

    # `user` is required by the shared message template, which renders both
    # roles; without it the user bubble's initial blows up.
    return templates.TemplateResponse(
        request,
        "chat/_turn.html",
        {
            "request": request,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "chat": chat,
            "user": user,
            "models_by_id": {
                m.model_id: m for m in chat_service.available_models(db, user)
            },
            **audio_service.template_flags(db, user),
        },
    )


@router.get("/{chat_id}/messages/{message_id}/stream")
async def stream_message(
    db: Db,
    user: RequiredUser,
    chat_id: str,
    message_id: str,
) -> Response:
    """Stream the assistant's reply as server-sent events.

    Emits `token` events carrying escaped text, then a single `done` event
    carrying the finished bubble rendered from Markdown, then `close`.
    """
    chat = _owned_chat(db, chat_id, user.id)
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That message no longer exists."))

    return StreamingResponse(
        _follow(chat.id, message.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which turns a stream
            # into one delivery at the end. This is the documented opt-out.
            "X-Accel-Buffering": "no",
        },
    )


def _step_html(message_id: str, step) -> str:
    """One closed step of a running reply.

    `SimpleNamespace` for the message: the partial wants an id to build its
    element ids from and nothing else, and there is no `Message` in scope here
    -- the row is not written until the reply ends. `reasoning_ms` is only
    known then too, so a live step says "Thought" and the stored one says how
    long for.
    """
    return templates.get_template("chat/_step.html").render(
        {"step": step, "message": SimpleNamespace(id=message_id, reasoning_ms=0)}
    )


def _plan_panel_html(plan) -> str:
    """The plan panel, marked to land in its place above the composer.

    Takes the plan as a structure, or -- until the device link sends the
    structure -- a device's todo list, which is read as one (`plans.from_todos`).
    """
    from lembas.services import plans

    if isinstance(plan, list):
        plan = plans.from_todos(plan)
    return templates.get_template("chat/_plan_panel.html").render(
        {"plan": plans.normalise(plan), "oob": True, "chat": None}
    )


def _jobs_strip_html(chat_id: str, jobs: list[dict], *, oob: bool = True) -> str:
    """The jobs strip, marked to land in its place above the composer. `jobs`
    is `JobInfo` dicts -- a reply's `generation.jobs`, the rows, or a device's
    answer -- all in the one shape `services/jobs.py` gives."""
    return templates.get_template("chat/_jobs_strip.html").render(
        {"chat": SimpleNamespace(id=chat_id), "jobs": jobs, "oob": oob}
    )


async def chat_jobs(db: DBSession, chat: Chat) -> list[dict]:
    """The chat's background jobs as the strip shows them: a device chat's from
    the device (capability `jobs`), any other chat's from its rows. Never
    raises -- a device that cannot say has no jobs to show."""
    if chat.device_token_id:
        try:
            return await device_link.list_jobs(chat.device_token_id, chat.device_session_id)
        except (device_link.DeviceError, device_link.LinkClosed, TimeoutError):
            return []
    return [jobs_service.info(db, job) for job in jobs_service.for_chat(db, chat.id)]


def _tail_oob(message_id: str, thinking_tail: str, think_label: str, tail_html: str) -> str:
    """The open tail, as out-of-band swaps riding on a `steps` frame.

    A round closing moves the text being written out of the tail and into a
    step above it. Sent as two frames -- `steps`, then an emptying `render` --
    the browser can paint between them, and for that paint the sentence is on
    screen twice: once in its new step and once still in the tail, with the
    whole transcript shifted down by its height. Carrying the tail in the same
    frame as the steps means one swap, one paint, and nothing moves that should
    not. Whatever the tail holds *now* is sent, not an empty string: the next
    round may already have begun by the time the follower polls.
    """
    return (
        f'<div id="stream-{message_id}" hx-swap-oob="innerHTML">'
        f"{tail_html}</div>"
        f'<div id="reasoning-body-{message_id}" hx-swap-oob="innerHTML">'
        f"{escape_text(thinking_tail)}</div>"
        f'<span id="think-stats-{message_id}" hx-swap-oob="innerHTML">'
        f"{escape_text(think_label)}</span>"
    )


def _think_label(generation, thinking_tail: str) -> str:
    """How long this round has been thinking, and roughly how much.

    This round's, not the reply's, so the live block means the same thing as the
    closed blocks above it and does not change meaning the moment it settles.
    The reply's total is already under the bubble, in the metrics chips.

    The producer owns the number. Computing it here from a start time would
    keep the clock running after the model had stopped thinking and moved on to
    a tool, which is a timer rather than a measurement.
    """
    return steps_service.thinking_label(
        ms=generation.round_thinking_ms,
        tokens=tokens_service.estimate(thinking_tail),
        live=True,
    )


def _ask_html(chat_id: str, pending) -> str:
    """The cards asking the reader something, or nothing at all.

    Returns "" when there is nothing pending, and an empty frame is sent the
    moment it becomes empty, because this is one of the few blocks that has to
    be able to *clear* itself: the card must vanish the moment it is answered.
    `steps` is the opposite -- guarded by truthiness so a frame can never blank
    it.

    `pending` is the generation's list of open cards -- one each, in the
    order they were asked, every one answered on its own -- or a single card.
    """
    if pending is None:
        return ""
    cards = pending if isinstance(pending, list | tuple) else [pending]
    template = templates.get_template("chat/_interaction.html")
    return "".join(
        template.render(
            # The sentinel the "Something else" row submits, passed in rather
            # than written into the template, so the value the card sends and
            # the value this module looks for cannot drift apart.
            {"ask": card, "chat_id": chat_id, "other_value": interaction.OTHER}
        )
        for card in cards
    )


async def _follow(chat_id: str, message_id: str) -> AsyncIterator[str]:
    """Stream a generation that is running independently of this request.

    This connection only *watches*. Closing it -- navigating away, opening
    another chat -- leaves the reply being written, and reconnecting replays
    the whole state immediately rather than starting over.

    Every frame carries the complete block each time rather than a delta, which
    is what makes reattaching mid-reply work at all: a follower arriving late has
    no earlier fragments to append to.

    The split is along **closed versus open**, not along kind. `steps` carries
    every step that has finished and moves only when a round ends; `reasoning`
    and `render` carry the step still being written and move at streaming speed.
    That is what makes this affordable: the old `tools` frame re-rendered every
    tool call in the reply twelve times a second, against an output budget of a
    megabyte, so a long agent reply spent most of its wall time re-rendering its
    own transcript. `rendered` below is a render cache and not a wire protocol --
    it starts empty for every follower, so one attaching mid-reply still receives
    the whole prefix in its first frame.

    The order within one pass is load-bearing: `steps` before `reasoning` and
    `render`, because `steps` carries the containers those two are swapped into.
    htmx re-registers `sse-swap` on content it swaps in, which is the same
    property the approval card's buttons already rely on.

    **A frame is sent only when its content changed.** Every version bump used
    to send `reasoning`, `think`, `render`, `status` and `ask` whether or not
    anything in them had moved, and each one is a swap the browser lays out and
    the page scrolls after -- seven swaps a bump, twelve bumps a second, most of
    them replacing a block with itself. The page jumped under the reader for the
    whole of a long reply. `sent` below is the last payload of each, and a frame
    whose payload matches it is not sent. A follower starts with nothing sent,
    so one attaching mid-reply still receives the whole state in its first pass.

    **A round closes in one frame.** The `steps` frame carries the tail with it,
    out of band (`_tail_oob`), so the text moving from the tail into a step is
    one swap rather than two with a paint between them in which it is on screen
    twice. The tail is recorded as sent, so the frames that follow do not repeat
    it.
    """
    generation = generation_service.ensure(chat_id, message_id)
    generation.followers += 1
    seen = -1
    last_frame = time.monotonic()
    last_metrics = 0.0
    # The HTML of every step already rendered, and how many *marks* that covers.
    # Two counters and not one: a mark can produce up to three steps -- thinking,
    # prose, tools -- so the length of the list is not an index into the marks.
    rendered: list[str] = []
    marks_done = 0
    # The plan last sent, so the panel is re-sent only when it changed: a
    # chat's own plan (a structure) or, from a device that still sends a todo
    # list, that list -- `_plan_panel_html` reads either.
    plan_sent: dict | list | None = None
    # Messages already taken out of the queue at the bottom of the thread.
    unqueued: set[str] = set()
    # The last payload of every frame that is sent only on change. The five
    # that land in the bubble start as empty, because that is how the
    # streaming shell renders their containers: a first pass that finds them
    # empty has nothing to say about them.
    sent: dict[str, str] = {"reasoning": "", "think": "", "render": "", "status": "", "ask": ""}
    # The tail's markdown, rendered at the cost of what is new each pass and
    # rendered once a pass -- it used to be rendered whole, twice at a round's
    # close, ten times a second. See `markdown.LiveRender`.
    live = LiveRender()

    def changed(name: str, payload: str) -> bool:
        if sent.get(name) == payload:
            return False
        sent[name] = payload
        return True

    try:
        while True:
            pass_started = time.monotonic()
            if generation.version != seen:
                seen = generation.version
                thinking_tail, text_tail = steps_service.tail(generation)
                think_label = _think_label(generation, thinking_tail)
                tail_html = live.render(text_tail) if text_tail else ""
                if len(generation.steps) > marks_done:
                    for step in steps_service.closed_from(generation, since=marks_done):
                        rendered.append(_step_html(message_id, step))
                    marks_done = len(generation.steps)
                    # The tail rides along, and is recorded as sent: see
                    # `_tail_oob` for why it is one frame and not two.
                    sent["reasoning"] = escape_text(thinking_tail)
                    sent["think"] = escape_text(think_label)
                    sent["render"] = tail_html
                    yield sse.event(
                        "steps",
                        "".join(rendered)
                        + _tail_oob(message_id, thinking_tail, think_label, tail_html),
                    )
                    # A message taken in just now is shown in the reply; its
                    # bubble at the bottom of the thread goes.
                    if gone := steps_service.steered_ids(generation.steps) - unqueued:
                        unqueued |= gone
                        yield sse.event(
                            "steered",
                            "".join(
                                f'<article id="msg-{i}" hx-swap-oob="delete"></article>'
                                for i in sorted(gone)
                            ),
                        )
                # The open tail, whenever it moved -- an empty one included,
                # which is what clears it after a round closes with nothing new
                # yet. Safe precisely because these carry the open tail only;
                # the version that carried the whole reply had to be guarded,
                # or a frame could wipe the answer.
                if changed("reasoning", escape_text(thinking_tail)):
                    yield sse.event("reasoning", sent["reasoning"])
                if changed("think", escape_text(think_label)):
                    yield sse.event("think", sent["think"])
                if changed("render", tail_html):
                    yield sse.event("render", sent["render"])
                current_plan = getattr(generation, "plan", None) or generation.todos
                if current_plan is not None and current_plan != plan_sent:
                    # Out of band, into the panel above the composer.
                    plan_sent = (
                        dict(current_plan) if isinstance(current_plan, dict) else list(current_plan)
                    )
                    yield sse.event("plan", _plan_panel_html(plan_sent))
                if changed("status", escape_text(generation.status)):
                    yield sse.event("status", sent["status"])
                if changed("ask", _ask_html(chat_id, generation.waiting)):
                    yield sse.event("ask", sent["ask"])
                # The background jobs, out of band into the strip above the
                # composer -- only once there are any: an empty frame would
                # clear a strip the page filled for itself.
                if generation.jobs and changed(
                    "jobs", _jobs_strip_html(chat_id, list(generation.jobs.values()))
                ):
                    yield sse.event("jobs", sent["jobs"])
                last_frame = time.monotonic()

            # The metrics keep to a clock rather than to the version. The
            # counts move with every token, so sending them on every bump is a
            # swap twelve times a second for numbers nobody reads that fast;
            # and the version does not move while a tool runs -- there is no
            # `touch()` inside `_run_calls` -- while a five-minute build on the
            # far side is exactly when somebody looks at these numbers to see
            # whether anything is happening. The elapsed clock is advancing
            # throughout, so tok/s has to be allowed to fall; frozen chips
            # beside a spinner read as a hang. One small swap a second, and
            # only while the reply is live.
            if time.monotonic() - last_metrics > METRICS_INTERVAL:
                last_metrics = time.monotonic()
                if changed("metrics", _metrics_html(generation)):
                    yield sse.event("metrics", sent["metrics"])
                    last_frame = time.monotonic()

            if generation.done:
                break

            # A reasoning model can think for a minute or more without emitting
            # anything, and an idle connection is what a proxy closes. The
            # comment frame keeps it open and is ignored by the browser.
            if time.monotonic() - last_frame > KEEPALIVE_AFTER:
                yield sse.KEEPALIVE
                last_frame = time.monotonic()

            # Polling rather than per-follower wakeups: the producer already
            # works in RENDER_INTERVAL steps, so a short sleep is simpler and
            # cannot drop a notification. Never less than twice what this pass
            # cost: a follower whose rendering got expensive backs off rather
            # than taking the event loop from everything else.
            spent = time.monotonic() - pass_started
            await asyncio.sleep(max(generation_service.RENDER_INTERVAL * 0.8, 2 * spent))
    finally:
        generation.followers = max(0, generation.followers - 1)

    # The producer commits the message before marking itself done, so by here
    # the row is authoritative and the final bubble can be rendered from it.
    with session_scope() as db:
        message = db.get(Message, message_id)
        chat = db.get(Chat, chat_id)
        if message is None or chat is None:
            yield sse.event("close", "")
            return

        owner = db.get(User, chat.user_id)
        final_html = templates.get_template("chat/_message.html").render(
            {
                "message": message,
                "chat": chat,
                # Passed even though an assistant bubble never reads it: the
                # template shares both roles, and a missing `user` would only
                # blow up on whichever branch is not being exercised here.
                "user": owner,
                # `owner`, never None. `models_visible_to` answers an absent
                # user with [], so a None here is not "every model" but *no*
                # model -- and this frame replaces the whole bubble at the
                # moment a reply finishes. The template then finds no
                # `speaking_model` and the finished reply swaps its avatar for
                # the LLeMbas mark, its author for the instance name, and grows
                # a raw model_id chip, all of which a reload silently corrects.
                # That is why it went unreported for so long.
                "models_by_id": {
                    m.model_id: m for m in chat_service.available_models(db, owner)
                },
                # This frame replaces the whole bubble, so it has to carry the
                # speaker button's conditions too -- and the owner's, not the
                # follower's: there is no request here to ask who is watching.
                **audio_service.template_flags(db, owner),
                # The one render that means "this reply just landed", which is
                # what read-aloud-automatically keys off. A page load must not
                # set it or reopening a chat would start talking.
                "just_finished": True,
            }
        )
        title_html = templates.get_template("chat/_title_oob.html").render({"chat": chat})

        # What the queue did while this reply was running. There is no push
        # channel that outlives one message's stream, and this is the last frame
        # that reaches the browser -- so it carries the rest out of band, the
        # way the chat title already does.
        moved_html, queue_html = _queue_frames(db, chat, owner, generation)
        # The strip as the reply leaves it: with rows, it polls from here on,
        # because no frame of this stream reaches the page after this one.
        jobs_html = (
            _jobs_strip_html(chat.id, list(generation.jobs.values())) if generation.jobs else ""
        )

    yield sse.event("done", moved_html + final_html + queue_html + title_html + jobs_html)
    yield sse.event("close", "")


def _render_bubble(db: DBSession, chat: Chat, owner: User | None, message: Message) -> str:
    """One finished bubble, rendered the way the `done` frame renders its own."""
    return templates.get_template("chat/_message.html").render(
        {
            "message": message,
            "chat": chat,
            "user": owner,
            "models_by_id": {m.model_id: m for m in chat_service.available_models(db, owner)},
            **audio_service.template_flags(db, owner),
        }
    )


def _queue_frames(
    db: DBSession, chat: Chat, owner: User | None, generation
) -> tuple[str, str]:
    """The bubbles the queue produced during this reply, as out-of-band HTML.

    Two pieces, because they swap differently. Anything taken *into* this reply
    mid-round now sorts before it, so it is rendered ahead of the finished
    bubble in the same `outerHTML` swap and its stale node is deleted out of
    band -- one frame, and the DOM ends up in the order the database is in.
    Anything drained *after* the reply is a new pair appended to the thread.
    """
    moved: list[str] = []
    out_of_band: list[str] = []

    inline = steps_service.steered_ids(generation.steps)
    for injected_id in generation.injected_ids:
        row = db.get(Message, injected_id)
        if row is None:
            continue
        # Shown inside the reply, where it went in: its own bubble
        # goes. One taken in with no mark (an older device) still moves above.
        if row.id not in inline:
            moved.append(_render_bubble(db, chat, owner, row))
        out_of_band.append(f'<article id="msg-{row.id}" hx-swap-oob="delete"></article>')

    if generation.drained:
        fresh = list(
            db.scalars(
                select(Message)
                .where(Message.chat_id == chat.id, Message.complete.is_(False))
                .order_by(Message.created_at)
            )
        )
        for assistant in fresh:
            # The user turn that was waiting has just lost its Send now and
            # Discard, so it is re-rendered in place.
            delivered = db.scalars(
                select(Message)
                .where(
                    Message.chat_id == chat.id,
                    Message.role == ROLE_USER,
                    Message.created_at <= assistant.created_at,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            ).first()
            if delivered is not None:
                out_of_band.append(
                    f'<div hx-swap-oob="outerHTML:#msg-{delivered.id}">'
                    + _render_bubble(db, chat, owner, delivered)
                    + "</div>"
                )
            out_of_band.append(
                '<div hx-swap-oob="beforeend:#thread">'
                + _render_bubble(db, chat, owner, assistant)
                + "</div>"
            )

    return "".join(moved), "".join(out_of_band)


def _metrics_html(generation) -> str:
    """The metric chips for a reply still being written.

    Built from the same Metrics object the finished bubble uses, so the numbers
    do not jump when the stream ends -- the only thing that changes is that an
    estimate may have become exact.
    """
    return templates.get_template("chat/_metrics.html").render(
        {"metrics": metrics_service.from_generation(generation)}
    )


def _thread_context(db: DBSession, chat: Chat, user: User) -> dict:
    """Everything chat/_thread.html needs to render the conversation."""
    everything = list(
        db.scalars(select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at))
    )
    compacted, messages = compaction_service.split(db, chat, everything)
    return {
        "chat": chat,
        "user": user,
        "messages": messages,
        "compacted": compacted,
        "models_by_id": {m.model_id: m for m in chat_service.available_models(db, user)},
        **audio_service.template_flags(db, user),
    }


def _messages_after(db: DBSession, message: Message) -> list[Message]:
    """Everything later in this chat than one message.

    Everything *tied* with it counts as later, which is the part worth
    explaining. Under a bare `>` a row sharing this one's microsecond is never
    after it and survives a rewind -- an orphan below the turn being edited, in
    the transcript and in every later request. `_send` writes a user turn and its
    assistant placeholder back to back, so that pair is exactly what ties, and it
    is exactly what a rewind of that turn has to take.

    ⚠ Deliberately **not** `thread_tail`'s `(created_at, id)` tiebreak, which is
    right there and wrong here. That one needs any stable total order, because it
    is a polling cursor. This one has to agree with the order somebody is looking
    at, and `Message.id` is a random UUID -- so comparing ids would resolve a tie
    by coin toss, keeping some later rows and deleting some earlier ones. Reading
    an ambiguous tie as "later" instead is the safe direction for an operation
    whose whole purpose is to discard what follows: one extra row deleted is what
    the reader asked for, while one row left behind corrupts every request after
    it.
    """
    return list(
        db.scalars(
            select(Message)
            .where(
                Message.chat_id == message.chat_id,
                Message.created_at >= message.created_at,
                Message.id != message.id,
            )
            .order_by(Message.created_at, Message.id)
        )
    )


@router.get("/{chat_id}/messages/{message_id}/edit")
async def edit_form(
    request: Request, db: Db, user: RequiredUser, chat_id: str, message_id: str
) -> Response:
    """Swap one of the reader's own turns into an editable form."""
    chat = _owned_chat(db, chat_id, user.id)
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat.id or message.role != ROLE_USER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That message no longer exists."))
    # See `edit_message` for why, and for why this is not the same sentence.
    if message.machine:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, t("A background job's message cannot be edited.")
        )
    # A chat on a device: the conversation the model reads lives in the
    # device's session, which only a device offering `rewind` can rewind from
    # here -- otherwise editing would re-run a turn on top of the old one,
    # side effects and all. A turn typed in the device's terminal has no turn
    # id here to rewind by.
    if chat.device_token_id and (
        not device_link.offers(chat.device_token_id, "rewind")
        or message.origin == device_link.ORIGIN_TERMINAL
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, _device_keeps_history())

    return templates.TemplateResponse(
        request,
        "chat/_edit_form.html",
        {
            "request": request,
            "chat": chat,
            "user": user,
            "message": message,
            "following": len(_messages_after(db, message)),
        },
    )


@router.get("/{chat_id}/messages/{message_id}/cancel-edit")
async def cancel_edit(
    request: Request, db: Db, user: RequiredUser, chat_id: str, message_id: str
) -> Response:
    """Put the bubble back, unchanged."""
    chat = _owned_chat(db, chat_id, user.id)
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That message no longer exists."))

    return templates.TemplateResponse(
        request,
        "chat/_message.html",
        {
            "request": request,
            "chat": chat,
            "user": user,
            "message": message,
            "models_by_id": {},
        },
    )


@router.post("/{chat_id}/messages/{message_id}/edit")
async def edit_message(
    request: Request,
    db: Db,
    user: RequiredUser,
    chat_id: str,
    message_id: str,
    content: str = Form(...),
) -> Response:
    """Rewrite one of the reader's turns and run the conversation on from there.

    Everything after the edited message is deleted rather than branched. A
    branch would need a UI for choosing between versions, and "go back and try
    again from here" is what was actually asked for -- the simpler behaviour is
    also the one people expect from every other chat client.
    """
    chat = _owned_chat(db, chat_id, user.id)
    await rewind_to(db, chat, user, message_id, content)
    return templates.TemplateResponse(
        request, "chat/_thread.html", {"request": request, **_thread_context(db, chat, user)}
    )


async def rewind_to(
    db: DBSession, chat: Chat, user: User, message_id: str, content: str
) -> Message:
    """Rewrite one of the person's turns, drop everything after it, and start
    the reply again: the new reply's row. The core of `edit_message`, shared
    with the app's JSON door."""
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat.id or message.role != ROLE_USER:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That message no longer exists."))
    # The bubble hides the pencil, but a hidden button is a courtesy and this is
    # the rule: editing rewinds and re-sends under the reader's own authority,
    # and what a machine reported is not theirs to rewrite. Its own sentence,
    # because "no longer exists" would be false and would leave nothing to do.
    if message.machine:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, t("A background job's message cannot be edited.")
        )
    content = content.strip()
    if not content and not message.attachments:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("A message cannot be empty."))

    # Editing rewinds and then starts a reply. Doing that while one is being
    # written would be two generations answering one chat -- so the running
    # one is stopped first, as Stop would, and the edit goes ahead once it has.
    # It used to refuse ("wait for it to finish, or stop it"), which is the
    # same thing with a step for the person to do.
    await _stop_running(db, chat)
    # `_reply_in_flight` is about *this* chat; the quota is about the account.
    _refuse_extra_reply(db, chat, user)

    # A chat on a device: the conversation its model reads lives in the
    # device's session, so the device rewinds first -- and refuses, as this
    # route always did, when it cannot (not linked, no `rewind`, a turn typed
    # in its terminal). Nothing here has changed yet when it refuses.
    if chat.device_token_id:
        await _device_rewind(chat, message)

    message.content = content

    # Attachments cascade with their message, so the files go too.
    discarded = _messages_after(db, message)
    for later in discarded:
        db.delete(later)
    if chat.device_token_id:
        # Sent to the device as a new turn, under a new id -- see `_renew_turn`.
        message = _renew_turn(db, message)

    # A rewind to at or before the compaction boundary leaves that boundary
    # describing turns that no longer exist. There is no foreign key to null it
    # out on an upgraded database, so it is cleared here.
    cutoff = compaction_service.cutoff_message(db, chat)
    if cutoff is None or compaction_service.moment(message) <= compaction_service.moment(cutoff):
        compaction_service.reset(chat)

    _note_rewind(chat)
    db.commit()

    assistant = chat_service.create_message(
        db, chat, ROLE_ASSISTANT, "", complete_=False, model_id=chat.model_id
    )
    generation_service.ensure(chat.id, assistant.id)
    log.info("chat %s rewound to a message, %d discarded", chat.id, len(discarded))
    return assistant


@router.post("/{chat_id}/messages/{message_id}/execute-plan")
async def execute_plan(
    request: Request, db: Db, user: RequiredUser, chat_id: str, message_id: str
) -> Response:
    """Carry out a plan the model proposed.

    `message_id` names the bubble whose snapshot to carry out, or `current` for
    the plan as the chat has it now (the panel's button). Either way the plan
    becomes the chat's, so the harness puts it in front of the model each turn
    and `plan_update` keeps it current -- a model that cannot see the plan it
    is carrying out could not tick anything off if it wanted to.

    An agent chat switches to **Edit**, never Auto. The plan was written under
    a mode where every command stopped for approval, and a button that also
    removed the asking is not the button anybody pressed. A chat leaves Plan
    mode, which is what withheld the tools that change things.

    The plan is sent back **marked as a quotation of the model's own words**
    rather than as a bare instruction. A plan whose text came out of a file the
    model read would otherwise arrive in the most trusted role in the
    transcript, wearing the reader's authority -- which is precisely how an
    injected instruction would like to arrive.
    """
    chat = _owned_chat(db, chat_id, user.id)
    content = plan_prompt(db, chat, message_id)
    log.info("%s executing a plan in chat %s", user.email, chat.id)
    return _send(request, db, chat, user, content)


def plan_prompt(db: DBSession, chat: Chat, message_id: str) -> str:
    """Make a plan the chat's, switch the mode the way carrying it out needs,
    and return the turn that asks for it (see `execute_plan`). Shared with the
    app's JSON door, which sends the turn through `write_turn`."""
    if message_id == "current":
        plan = chat.plan
        if not plan:
            raise HTTPException(status.HTTP_404_NOT_FOUND, t("This chat has no plan."))
    else:
        message = db.get(Message, message_id)
        if message is None or message.chat_id != chat.id or not message.plan_json:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, t("There is no plan on that message.")
            )
        # `message.plan`, the property, so a row written before version 2
        # comes through as one phase.
        plan = message.plan
        chat.plan_json = plan
        chat.plan_message_id = message.id

    # `steps` is flattened from every phase in order and is always written,
    # which is why this line needed no change when the shape grew findings,
    # objectives and phases.
    steps = [str(s) for s in (plan.get("steps") or [])]
    body = "\n".join(f"{n}. {step}" for n, step in enumerate(steps, start=1))

    chat.agent_mode = agent_policy.MODE_EDIT if chat.kind == KIND_AGENT else ""
    db.commit()

    return (
        "Carry out the plan you proposed above:\n\n"
        f"> **{plan.get('title') or 'The plan'}**\n"
        + "\n".join(f"> {line}" for line in body.splitlines())
        + "\n\nWork through it in order. If a step turns out to be wrong, stop "
        "and say so rather than improvising around it."
    )


@router.post("/{chat_id}/messages/{message_id}/stop")
async def stop_message(db: Db, user: RequiredUser, chat_id: str, message_id: str) -> Response:
    """Ask a running generation to stop.

    Whatever has arrived is kept: a half-written answer the reader chose to cut
    short is still worth having, and discarding it would be a surprise.
    """
    chat = _owned_chat(db, chat_id, user.id)
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That message no longer exists."))

    generation_service.request_stop(message.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Background jobs: the strip and the helper panel (spec 2.0.0) --------------------
def _device_or_409(chat: Chat, error: Exception) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, t("Not done: %(error)s", error=error))


@router.get("/{chat_id}/jobs")
async def jobs_strip(db: Db, user: RequiredUser, chat_id: str) -> Response:
    """The strip, as the page polls it while it has rows."""
    chat = _owned_chat(db, chat_id, user.id)
    return HTMLResponse(_jobs_strip_html(chat.id, await chat_jobs(db, chat), oob=False))


async def _job_turns(db: DBSession, chat: Chat, job_id: str) -> tuple[dict, list[dict]]:
    """A job's facts and its transcript, from the device or the rows; 404 for an
    id the chat does not have, 409 for a device that cannot answer."""
    if chat.device_token_id:
        try:
            jobs = await device_link.list_jobs(chat.device_token_id, chat.device_session_id)
            job = next((j for j in jobs if j["id"] == job_id), None)
            if job is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, t("That job is not here any more."))
            turns = await device_link.job_transcript(
                chat.device_token_id, chat.device_session_id, job_id
            )
        except (device_link.DeviceError, device_link.LinkClosed, TimeoutError) as exc:
            raise _device_or_409(chat, exc) from exc
        return job, turns
    row = jobs_service.by_id(db, chat.id, job_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That job is not here any more."))
    return jobs_service.info(db, row), jobs_service.transcript(db, row)


def _panel_html(chat: Chat, job: dict, turns: list[dict], *, part: str = "") -> str:
    for turn in turns:
        if turn.get("role") == "assistant":
            turn["html"] = render_markdown(turn.get("text") or "") if turn.get("text") else ""
    name = "chat/_job_turns.html" if part == "turns" else "chat/_job_panel_body.html"
    return templates.get_template(name).render({"chat": chat, "job": job, "turns": turns})


@router.get("/{chat_id}/jobs/{job_id}")
async def job_panel(
    db: Db, user: RequiredUser, chat_id: str, job_id: str, part: str = ""
) -> Response:
    """The helper panel's body for one job, or its transcript alone (`part=turns`)."""
    chat = _owned_chat(db, chat_id, user.id)
    job, turns = await _job_turns(db, chat, job_id)
    return HTMLResponse(_panel_html(chat, job, turns, part=part))


@router.post("/{chat_id}/jobs/{job_id}/stop")
async def stop_job(db: Db, user: RequiredUser, chat_id: str, job_id: str) -> Response:
    """Stop a job from the strip. On a device chat the device stops it
    (`_lembas/job/stop`) and wakes its session with the end note; here the
    helper's chat is stopped and its watcher does the same. Returns the strip."""
    chat = _owned_chat(db, chat_id, user.id)
    if chat.device_token_id:
        if refused := device_link.refusal(db, user, chat.device_token_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, refused)
        try:
            await device_link.stop_job(chat.device_token_id, chat.device_session_id, job_id)
        except (device_link.DeviceError, device_link.LinkClosed, TimeoutError) as exc:
            raise _device_or_409(chat, exc) from exc
    else:
        if not await subagent_service.stop_job(chat.id, job_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, t("That job is not here any more."))
    return HTMLResponse(_jobs_strip_html(chat.id, await chat_jobs(db, chat), oob=False))


@router.post("/{chat_id}/jobs/{job_id}/send")
async def send_job(
    db: Db, user: RequiredUser, chat_id: str, job_id: str, text: str = Form("")
) -> Response:
    """A message to a helper, from the person: what `job_send` does for the
    model. Returns the panel body, with the message in the transcript."""
    chat = _owned_chat(db, chat_id, user.id)
    text = text.strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("Write something to send."))
    if chat.device_token_id:
        if refused := device_link.refusal(db, user, chat.device_token_id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, refused)
        try:
            await device_link.send_job(chat.device_token_id, chat.device_session_id, job_id, text)
        except (device_link.DeviceError, device_link.LinkClosed, TimeoutError) as exc:
            raise _device_or_409(chat, exc) from exc
    else:
        state = await subagent_service.send_to_job(chat.id, job_id, text)
        if not state:
            raise HTTPException(status.HTTP_404_NOT_FOUND, t("That job is not here any more."))
        if state == "gone":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                t("That helper's conversation is gone, so it cannot be messaged."),
            )
    job, turns = await _job_turns(db, chat, job_id)
    return HTMLResponse(_panel_html(chat, job, turns))


def _waiting_message(db: DBSession, chat: Chat, message_id: str) -> Message:
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat.id or not message.queued:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, t("That message is not waiting to be sent.")
        )
    return message


@router.post("/{chat_id}/messages/{message_id}/discard")
async def discard_queued(db: Db, user: RequiredUser, chat_id: str, message_id: str) -> Response:
    """Withdraw a prompt that has not been sent.

    Deleted outright rather than marked: it never reached a model, nothing in
    the transcript refers to it, and a conversation full of tombstones for
    things nobody said is worse than the row being gone. Attachments cascade.

    An empty body rather than a 204, because htmx does not swap on a 204 and the
    bubble has to disappear.
    """
    chat = _owned_chat(db, chat_id, user.id)
    message = _waiting_message(db, chat, message_id)

    db.delete(message)
    db.commit()
    return HTMLResponse("")


@router.post("/{chat_id}/messages/{message_id}/send-now")
async def send_queued_now(
    request: Request, db: Db, user: RequiredUser, chat_id: str, message_id: str
) -> Response:
    """Deliver a waiting prompt at once.

    Refused while a reply is being written rather than allowed to jump ahead of
    it: that is what the queue *is*, and starting a second generation here is
    the thing this whole mechanism exists to stop. Stop the reply first.

    The whole thread comes back, which is the rewind and compaction idiom, and
    is safe only because of the refusal above -- there is no live bubble to
    destroy.
    """
    chat = _owned_chat(db, chat_id, user.id)
    message = _waiting_message(db, chat, message_id)
    if _reply_in_flight(db, chat):
        raise HTTPException(
            status.HTTP_409_CONFLICT, t("Wait for the current reply to finish, or stop it.")
        )
    _refuse_extra_reply(db, chat, user)

    message.queued = False
    db.commit()
    assistant = chat_service.create_message(
        db, chat, ROLE_ASSISTANT, "", complete_=False, model_id=chat.model_id
    )
    generation_service.ensure(chat.id, assistant.id)

    return templates.TemplateResponse(
        request, "chat/_thread.html", {"request": request, **_thread_context(db, chat, user)}
    )


@router.post("/{chat_id}/interaction/{interaction_id}")
async def answer_interaction(
    request: Request,
    db: Db,
    user: RequiredUser,
    chat_id: str,
    interaction_id: str,
) -> Response:
    """Answer the questions, or allow the action, a reply is waiting on.

    The whole card comes back at once, which is why the raw form is read rather
    than declared parameters: one `ask_user` call may have put four questions,
    and each carries a chosen option and a box to write something else. Per
    question, what was written wins over what was picked -- somebody who typed
    in the box after clicking an option meant the typing.

    `_owned_chat` is the authorisation and it is not decoration: without it any
    signed-in account that guessed an id would be answering -- and later,
    approving a command in -- somebody else's conversation.

    An id matching nothing (already answered, timed out, or the server was
    restarted) is a 204 with a toast rather than a 404. The card is gone either
    way, and an error page swapped into the middle of a chat is worse than
    being told plainly.
    """
    chat = _owned_chat(db, chat_id, user.id)
    answered, remembered, unmatchable = answer_card(
        db, chat, interaction_id, await request.form()
    )

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if not answered:
        response.headers["HX-Trigger"] = json.dumps(
            {"lembas:notify": {"message": t("That question is no longer waiting for an answer.")}}
        )
    elif remembered:
        response.headers["HX-Trigger"] = json.dumps(
            {
                "lembas:notify": {
                    "message": (
                        t(
                            "This chat will not ask about 1 more action. Clear that from "
                            "the menu beside the composer."
                        )
                        if remembered == 1
                        else t(
                            "This chat will not ask about %(n)s more actions. Clear that "
                            "from the menu beside the composer.",
                            n=remembered,
                        )
                    )
                }
            }
        )
    elif unmatchable:
        # Otherwise this is a button that silently did nothing, which is the
        # failure the rest of this feature was arranged to avoid. It is allowed
        # to store nothing -- a composed command line must never become a
        # standing permission -- but it is not allowed to say nothing.
        response.headers["HX-Trigger"] = json.dumps(
            {
                "lembas:notify": {
                    "message": t(
                        "Allowed once. A command line that runs more than one thing "
                        "cannot be stored as a rule, so this chat will ask again."
                    )
                }
            }
        )
    return response


def answer_card(db: DBSession, chat: Chat, interaction_id: str, form) -> tuple[bool, int, int]:
    """Answer a card from its form: `(answered, remembered, unmatchable)`.

    `form` is anything with `get` and `multi_items` -- the browser's
    `FormData`, or the one the app's JSON door builds from its body, so both
    answer through exactly these lines. See `answer_interaction` for the rules.
    """
    verdict = str(form.get("verdict") or "").strip()

    # Why they refused, in their own words. A card-level field rather than a
    # `text.<key>` one: the card covers everything in the round, so one reason
    # answers the round -- and on an approval card `text.<key>` already means a
    # corrected command, which is a different thing arriving in the same shape.
    # Only read on a refusal, so a reason typed and then abandoned by pressing
    # Allow cannot travel with a permission.
    reason = str(form.get("reason") or "").strip() if verdict == interaction.DENY else ""

    # Gathered in two passes because one field can now arrive several times: a
    # question the model marked `multiple` is checkboxes, and every ticked one
    # posts under the same name. A `setdefault` would keep the first and lose
    # the rest, which is an answer that says something the reader did not.
    chosen: dict[str, list[str]] = {}
    typed: dict[str, str] = {}
    # A question asked back instead of an answer (`ask.<key>`): carried to the
    # model as a counter-question, which the spec has it answer and then ask
    # again.
    asked_back: dict[str, str] = {}
    for field, value in form.multi_items():
        kind, _, key = str(field).partition(".")
        if not key or kind not in ("choice", "text", "ask"):
            continue
        written = str(value).strip()
        if kind == "text":
            typed[key] = written
        elif kind == "ask":
            if written:
                asked_back[key] = written
        elif written:
            chosen.setdefault(key, []).append(written)

    answers: dict[str, str] = {}
    for key, picks in chosen.items():
        # The "Something else" row carries a sentinel, not an answer: what it
        # means is whatever was typed beside it. Dropped entirely when the box
        # was left empty, so ticking it and writing nothing is the same as not
        # ticking it -- rather than the model being told the answer is
        # "__other__", which is the shape of thing it would try to act on.
        parts = [pick for pick in picks if pick != interaction.OTHER]
        if interaction.OTHER in picks and typed.get(key):
            parts.append(typed[key])
        if parts:
            answers[key] = ", ".join(parts)

    # A box with no choice beside it: the approval card's corrected command,
    # which is the one place `text.` still stands on its own.
    for key, written in typed.items():
        if written and key not in answers and key not in chosen:
            answers[key] = written

    # Read and recorded *before* resolving: `interaction.wait_for` clears
    # `generation.pending` in its `finally`, so a moment later there is nothing
    # left to remember and "always" would quietly mean "once".
    #
    # `answers` is gathered first because an approval card can now carry a
    # corrected command, and "always allow this" has to mean the command that is
    # about to run rather than the one the model asked for. Remembering the
    # proposed one would grant a standing permission nobody approved.
    remembered = 0
    unmatchable = 0
    if verdict == interaction.ALLOW_ALWAYS:
        remembered, unmatchable = _remember_always(
            db,
            chat,
            generation_service.pending_items(chat.id, interaction_id),
            answers=answers,
        )

    # What was ticked and typed, per item, as it was: a device's
    # question wants its answers back as lists of labels and a custom text, not
    # the sentence `answers` makes of them.
    picks: dict[str, list[str]] = {key: list(values) for key, values in chosen.items()}
    for key, written in typed.items():
        if written:
            picks[f"text.{key}"] = [written]
    for key, written in asked_back.items():
        picks[f"ask.{key}"] = [written]
        answers.setdefault(key, written)
    answered = generation_service.answer(
        chat.id,
        interaction_id,
        verdict=verdict,
        answers=answers,
        reason=reason,
        picks=picks,
    )
    return answered, remembered, unmatchable


def _remember_always(
    db: DBSession, chat: Chat, items, *, answers: dict[str, str] | None = None
) -> tuple[int, int]:
    """Record what "always allow" was said about.

    Returns (how many were new, how many could not be stored at all). The second
    is what the caller turns into a toast: `always_patterns` yields nothing for a
    command line an allow could not be trusted for, so pressing the button on
    one is right to store nothing and wrong to say nothing.

    The pattern is derived **here**, and still never taken from the request as a
    pattern: `answers` carries the command a person may have corrected on the
    card, and it goes through `agent_policy.always_patterns` exactly as
    `item.detail` does. That splits the line the way `decide` does, so what is
    stored is what will be compared later; it returns nothing for a line holding
    what the split could not see, which is precisely the shape that must never
    become a standing permission.

    Reading the edit matters rather than being a nicety. Somebody who corrects a
    command and presses "always allow" has approved the corrected one, and
    storing what the model originally asked for would be a standing permission
    for something nobody ever agreed to.

    A tool name for everything that is not a command, which is the convention
    `subagent.SAFE_COMMANDS` uses too: `read` and `list` are entries in it.
    """
    scope = dict(chat.scope_json or {})
    entries = list(scope.get("allow") or [])
    written = answers or {}
    added = 0
    unmatchable = 0

    for item in items:
        if item.kind != interaction.KIND_APPROVAL or len(entries) >= MAX_SCOPE_KEYS:
            continue
        detail = item.detail
        if item.editable:
            detail = (written.get(item.key) or "").strip() or item.detail
        # What "always" covers is the spec's: each command's meaningful prefix
        # (`git commit -m x` → `git commit *`), exactly for one that runs another,
        # and nothing for a line holding what an allow could not be trusted for.
        patterns = agent_policy.always_patterns(item.tool_name, detail)
        if not patterns:
            unmatchable += 1
            continue
        for pattern in patterns:
            if pattern in entries or len(entries) >= MAX_SCOPE_KEYS:
                continue
            entries.append(pattern)
            added += 1

    if added:
        # Reassigned rather than mutated: a plain dict assignment into a JSON
        # column is not detected.
        chat.scope_json = {**scope, "allow": entries}
        db.commit()
        log.info("chat %s will stop asking about %d action(s)", chat.id, added)
    return added, unmatchable


@router.post("/{chat_id}/allow/clear")
async def clear_allow(db: Db, user: RequiredUser, chat_id: str) -> Response:
    """Forget everything this chat was told to stop asking about.

    An empty body rather than a 204, because the row in the menu has to
    disappear -- htmx does not swap on a 204, and a Clear that leaves the count
    on screen is the silent control this codebase keeps cataloguing.
    """
    chat = _owned_chat(db, chat_id, user.id)
    scope = dict(chat.scope_json or {})
    if scope.pop("allow", None) is not None:
        chat.scope_json = scope
        db.commit()
    return HTMLResponse("")


@router.patch("/{chat_id}")
async def update_chat(request: Request, db: Db, user: RequiredUser, chat_id: str) -> Response:
    """Partially update a chat.

    The raw form is read rather than declaring Form() parameters because
    FastAPI substitutes the default for an empty form value, which makes
    "field absent" and "field submitted empty" indistinguishable. That
    difference is exactly what this endpoint needs: an empty system prompt or
    temperature means *clear it*, not *leave it alone*.
    """
    chat = _owned_chat(db, chat_id, user.id)
    folder_before = chat.folder_id
    renamed, archived_changed = apply_chat_update(db, user, chat, await request.form())
    # A chat dropped on a folder (js/dnd.js) leaves one part of the tree for
    # another, exactly as archiving does.
    moved = chat.folder_id != folder_before

    if renamed:
        # The two out-of-band spans the `done` frame already uses, so one
        # response updates the heading *and* the sidebar row. Renaming used to
        # be the `/title` command alone, which set the heading and left the
        # sidebar showing the old name until the next reload -- a rename that
        # looks half-applied is one people do twice.
        return HTMLResponse(
            templates.get_template("chat/_title_oob.html").render({"chat": chat})
        )

    # Archiving moves a row out of one group and into another, so the sidebar
    # has to be re-rendered -- and it cannot be, from a 204. htmx's own config
    # is `{code: "204", swap: false}`, so a control aimed at `#sidebar-tree`
    # with this endpoint's usual answer sets the column and then does visibly
    # nothing at all, which is this codebase's signature failure rather than a
    # new one. The same fragment and the same `oob` the sidebar switch returns,
    # for the same reason: New chat lives above the tree and comes along out of
    # band.
    if archived_changed or moved:
        from lembas.api.pages import sidebar_context

        return templates.TemplateResponse(
            request,
            "partials/_sidebar_tree.html",
            {"chat": None, "user": user, "oob": True, **sidebar_context(db, user)},
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


def apply_chat_update(db: DBSession, user: User, chat: Chat, form) -> tuple[bool, bool]:
    """Apply a partial update from its form: `(renamed, archived_changed)`.

    `form` is anything with `get`, `getlist` and `in` -- the browser's
    `FormData`, or the one the app's JSON door builds from its body, so a
    field means the same thing through either door. See `update_chat`.
    """
    allowed = permissions.resolve(db, user)

    renamed = False
    if "title" in form:
        cleaned = str(form["title"]).strip()[:300]
        if cleaned:
            chat.title = cleaned
            # An explicit rename must not be overwritten by auto-titling later.
            chat.title_generated = True
            renamed = True
            # The session on the device takes the name too.
            if chat.device_token_id:
                device_link.rename_session(chat.device_token_id, chat.device_session_id, cleaned)

    if "folder_id" in form:
        # Resolved against *this person's* folders, not taken as given. A folder
        # is not just a label: `effective_system_prompt` walks up from the chat
        # through its folder and its parents, so a chat attached to somebody
        # else's folder would take their system prompt -- reading a setting
        # across an ownership boundary through a field that looks like a tag.
        # Unknown or not theirs means no folder, which is the same answer
        # `_new_chat` gives.
        wanted = str(form["folder_id"]).strip()
        folder = db.get(Folder, wanted) if wanted else None
        chat.folder_id = folder.id if folder is not None and folder.user_id == user.id else None

    # Out of the way, and reversible.
    #
    # `Chat.archived` has been filtered on in four places since folders arrived
    # and written by nothing anywhere -- so the hiding worked, the archiving
    # did not, and the column read as a built feature to anyone who grepped for
    # it. Here rather than as its own endpoint because it is a property of the
    # chat, exactly like its title and its folder, and `update_chat` already
    # reads the raw form for the reason this field needs too: absent must mean
    # "leave it alone" and "0" must mean "put it back".
    archived_changed = False
    if "archived" in form:
        wanted = str(form["archived"]).strip() not in ("", "0", "false")
        archived_changed = wanted != chat.archived
        chat.archived = wanted

    # The mode is the one agent field that changes mid-chat: it decides what
    # gets asked about, not what the conversation is. A chat has Plan or
    # normal (empty); an agent chat has the four. Anything else is ignored
    # rather than refused, as a bad model id below is.
    if "agent_mode" in form:
        wanted = str(form["agent_mode"]).strip()
        if chat.kind == KIND_AGENT:
            if wanted in agent_policy.MODES:
                chat.agent_mode = wanted
        elif wanted in ("", agent_policy.MODE_PLAN) and not chat.parent_chat_id:
            chat.agent_mode = wanted

    # And these are the ones that never do. Refused rather than ignored: a form
    # that quietly did nothing would look like a bug from the outside, and
    # without the refusal a crafted POST would repoint a conversation at another
    # machine halfway through.
    for locked in ("kind", "ssh_profile_id", "device_token_id", "project_dir"):
        if locked in form:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                t(
                    "A chat's connection is fixed when it is created. Start a new "
                    "chat to work somewhere else."
                ),
            )

    model_id = str(form.get("model_id", "")).strip()

    if model_id:
        if not allowed.get("chat.model_select"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, t("You may not change the model for a chat.")
            )
        # Checked against what this user can reach, not merely what exists --
        # otherwise the picker is advisory and a crafted request bypasses it.
        # And within the chat's own data group: the new model would be sent the
        # whole history, which is exactly what a group keeps from its provider.
        group = data_groups.for_chat(db, chat)
        match = next(
            (
                m
                for m in chat_service.available_models(db, user, group)
                if m.model_id == model_id
            ),
            None,
        )
        if match is None:
            elsewhere = any(
                m.model_id == model_id for m in chat_service.available_models(db, user)
            )
            if elsewhere:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    t(
                        "That model is in another data group than this chat "
                        "(%(group)s), so it cannot be given this "
                        "chat's history. Start a new chat with it instead.",
                        group=data_groups.name_of(db, group),
                    ),
                )
            raise HTTPException(status.HTTP_403_FORBIDDEN, t("That model is not available to you."))
        chat.model_id = model_id
        chat.connection_id = match.connection_id
        # Chosen here: the device's own model it had switched to stops being
        # what the chat shows, and this one is sent with the next prompt.
        chat.device_model = ""

    if "system_prompt" in form:
        if not allowed.get("chat.system_prompt"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, t("You may not set a system prompt.")
            )
        chat.system_prompt = str(form["system_prompt"]).strip()[:8000]

    submitted_params = {name: form[name] for name in _PARAM_RANGES if name in form}
    if submitted_params:
        if not allowed.get("chat.params"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, t("You may not change sampling parameters.")
            )
        chat.params_json = {
            **(chat.params_json or {}),
            **_clean_params(**{k: str(v) for k, v in submitted_params.items()}),
        }

    # Not a number, so it cannot go through _PARAM_RANGES. `"off"` and empty
    # both clear it -- the sentinel because that is what the picker sends now,
    # empty because anything still posting the old value must keep working.
    # Anything that is neither is ignored rather than refused, so a typo does
    # not cost a message.
    if "reasoning_effort" in form:
        if not allowed.get("chat.params"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, t("You may not change sampling parameters.")
            )
        wanted = str(form["reasoning_effort"]).strip().lower()
        if not wanted or wanted == "off":
            chat.params_json = {**(chat.params_json or {}), "reasoning_effort": None}
        elif wanted in chat_service.EFFORTS:
            chat.params_json = {**(chat.params_json or {}), "reasoning_effort": wanted}

    # Switching model re-seeds an effort that was never chosen, so "what the
    # picker shows is what is sent" stays true afterwards. Only when the key is
    # ABSENT: `None` means somebody cleared it deliberately, and resurrecting
    # that would make "off" silently do nothing on the next model change.
    if model_id and "reasoning_effort" not in (chat.params_json or {}):
        # This person's own default for the new model first, then the
        # model's.
        seeded = model_prefs.default_effort(db, user, match)
        if seeded in chat_service.EFFORTS:
            chat.params_json = {**(chat.params_json or {}), "reasoning_effort": seeded}

    db.commit()
    return renamed, archived_changed


# Bounds are the ones every provider agrees on. Out-of-range values are
# dropped rather than clamped: silently changing what someone typed is worse
# than ignoring it, and the form shows what actually stuck on reload.
_PARAM_RANGES: dict[str, tuple[type, float, float]] = {
    "temperature": (float, 0.0, 2.0),
    "top_p": (float, 0.0, 1.0),
    "max_tokens": (int, 1, 1_000_000),
}


def _clean_params(**submitted: str | None) -> dict[str, float | int | None]:
    """Parse sampling parameters, dropping anything unusable.

    An empty string means "unset this and let the provider default apply", so
    it maps to None rather than being ignored.
    """
    cleaned: dict[str, float | int | None] = {}
    for name, raw in submitted.items():
        if raw is None:
            continue
        if not raw.strip():
            cleaned[name] = None
            continue
        caster, low, high = _PARAM_RANGES[name]
        try:
            value = caster(raw)
        except (TypeError, ValueError):
            continue
        if low <= value <= high:
            cleaned[name] = value
    return cleaned


@router.delete("/{chat_id}", dependencies=[Depends(require_permission("chat.delete"))])
async def delete_chat(db: Db, user: RequiredUser, chat_id: str) -> Response:
    chat = _owned_chat(db, chat_id, user.id)
    await delete_chat_now(db, chat)

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.headers["HX-Redirect"] = "/chat"
    return response


async def delete_chat_now(db: DBSession, chat: Chat) -> None:
    """Everything deleting a chat involves; shared with the app's JSON door."""
    chat_id = chat.id
    # Before the row goes: a terminal is keyed on the chat id, so afterwards
    # there would be nothing left to find it by and a shell would sit open on
    # somebody's machine until the idle timeout noticed.
    await terminal_service.close_chat(chat_id)
    # A chat on a device is a session there: stopped if it is writing,
    # and deleted on the device too, so neither side keeps what the other let go.
    if chat.device_token_id:
        if (live := generation_service.running_for(chat.id)) is not None:
            generation_service.request_stop(live.message_id)
        device_link.forget_session(chat.device_token_id, chat.device_session_id, db)
    # Not `db.delete(chat)`: that cascades to the attachment rows and leaves
    # every file they name on disk forever. See `chat_service.delete_chats`.
    chat_service.delete_chats(db, [chat])
    db.commit()


@router.post("/{chat_id}/messages/{message_id}/regenerate")
async def regenerate(
    request: Request,
    db: Db,
    user: RequiredUser,
    chat_id: str,
    message_id: str,
) -> Response:
    """Discard an assistant reply and produce a fresh one in its place."""
    chat = _owned_chat(db, chat_id, user.id)
    message = await regenerate_reply(db, chat, user, message_id)

    return templates.TemplateResponse(
        request,
        "chat/_message.html",
        {
            "request": request,
            "message": message,
            "chat": chat,
            "user": user,
            "models_by_id": {
                m.model_id: m for m in chat_service.available_models(db, user)
            },
            **audio_service.template_flags(db, user),
        },
    )


async def regenerate_reply(db: DBSession, chat: Chat, user: User, message_id: str) -> Message:
    """Blank a reply and produce it again: the row, reused. The core of
    `regenerate`, shared with the app's JSON door. A reply still being
    written is stopped first, as for an edit."""
    await _stop_running(db, chat)
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat.id or message.role != ROLE_ASSISTANT:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That reply no longer exists."))

    _refuse_extra_reply(db, chat, user)

    if chat.device_token_id:
        # A chat on a device: the turn this reply answered -- the person's
        # message before it, as `generation._device_turn` finds it -- is
        # rewound on the device first, and sent again under a new id. The
        # device refuses while the session works, and that is the answer.
        asked = db.scalars(
            select(Message)
            .where(
                Message.chat_id == chat.id,
                Message.role == ROLE_USER,
                Message.created_at <= message.created_at,
            )
            .order_by(Message.created_at.desc())
        ).first()
        await _device_rewind(chat, asked)
        _renew_turn(db, asked)

    message.content = ""
    message.error = ""
    message.complete = False
    # The earlier attempt's tool results stay for `recall`, but are no longer
    # this reply's: a compaction counts only the reads of turns still there.
    context_service.detach(db, chat.id, message.id)
    # Whose reply this was stays whose reply it is, unless the chat's model has
    # been changed since -- in which case regenerating is how somebody asks for
    # the new one. Always resetting to the chat's model would, since the row *is*
    # the model that answers, silently regenerate somebody else's turn as the
    # chat's model.
    if not (message.model_id or "").strip():
        message.model_id = chat.model_id
        message.connection_id = chat.connection_id
    _note_rewind(chat)
    db.commit()
    # restart, not ensure: this is the one caller that reuses a Message row, and
    # the finished generation for it is still registered.
    generation_service.restart(chat.id, message.id)
    return message


__all__ = ["render", "router"]
