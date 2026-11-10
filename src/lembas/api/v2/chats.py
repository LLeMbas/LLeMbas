"""Chats, their messages, the controls and the cards -- as JSON.

Every route here is a page route's twin and calls the same core (see the
package docstring). What differs is only what comes back: the rows, through
`serialise`, instead of the bubbles.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from lembas.api import chats as chats_api
from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import JsonForm, Me, need, owned_chat, owned_message
from lembas.db.models import KIND_CHAT, KINDS, Chat, Message
from lembas.services import activity as activity_service
from lembas.services import chat as chat_service
from lembas.services import generation as generation_service
from lembas.services import subagents as subagents_rules
from lembas.services import team_run
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
    levels: dict[tuple[str, str | None], Any] = {}

    def level(c: Chat) -> str:
        key = (c.model_id, c.connection_id)
        if key not in levels:
            levels[key] = chat_service.levels_for(chat_service.model_for(db, c))
        return levels[key].read(chat_service.resolved_effort(c))

    return {
        "chats": [serialise.chat(c, running=_running(c), level=level(c)) for c in page],
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
        **_agents_fields(body),
    )
    return {
        "chat": serialise.chat_detail(
            chat,
            running=True,
            cap=team_run.cap(db),
            level=chat_service.chat_level(db, chat),
            subagents=subagents_rules.view(db, caller.user, chat),
        ),
        **_turn(user_message, reply),
    }


def _agents_fields(body: dict[str, Any]) -> dict[str, str]:
    """`agents: {min, max}` from a body, as the page's two fields."""
    wanted = body.get("agents")
    if not isinstance(wanted, dict):
        return {}
    out: dict[str, str] = {}
    for key in ("min", "max"):
        if key in wanted and wanted[key] is not None:
            out[f"agents_{key}"] = str(wanted[key])
    return out


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
        "chat": serialise.chat_detail(
            chat,
            running=live is not None,
            cap=team_run.cap(db),
            level=chat_service.chat_level(db, chat),
            subagents=subagents_rules.view(db, caller.user, chat),
        ),
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
    # The team's size: `agents: {min, max}`, either alone (the other as
    # stored); refused outside `1 ≤ min ≤ max ≤ cap` with the bounds (422).
    items.extend(_agents_fields(body).items())
    # A chat's connection is fixed when it is made; naming one is the page's
    # 409, so it is passed through to be refused rather than quietly dropped.
    locked = (("kind", "kind"), ("device", "device_token_id"), ("directory", "project_dir"))
    for key, field in locked:
        if key in body:
            items.append((field, ""))
    chats_api.apply_chat_update(db, caller.user, chat, JsonForm(items))
    db.refresh(chat)
    return {
        "chat": serialise.chat_detail(
            chat,
            running=_running(chat),
            cap=team_run.cap(db),
            level=chat_service.chat_level(db, chat),
            subagents=subagents_rules.view(db, caller.user, chat),
        )
    }


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


# --- The team (spec 3.2.0) ------------------------------------------------------------
@router.get("/chats/{chat_id}/team")
async def get_team(
    caller: Me, db: Db, chat_id: str, since: int = Query(0, ge=0)
) -> dict[str, Any]:
    """The chat's team: the tree, and the room's messages after `since` --
    all of them, for the person sees the whole room. `cursor` is the last
    message's number, to pass as `since` next time."""
    chat = owned_chat(db, chat_id, caller.user)
    view = team_run.snapshot(db, chat.id, since) if team_run.may_have_team(chat) else None
    if view is not None:
        view["offered"] = team_run.offered(db, chat, caller.user)
    return {"team": serialise.team(view)}


@router.post("/chats/{chat_id}/team/{name}/stop")
async def stop_team_member(caller: Me, db: Db, chat_id: str, name: str) -> dict[str, Any]:
    """The person stops a member and those under it (the Team panel's Stop)."""
    chat = owned_chat(db, chat_id, caller.user)
    stopped = await team_run.person_stop(chat.id, name)
    if not stopped:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That member is not on the team."))
    return {"stopped": stopped}


# --- The subagent tree (harness spec 3.4.0, subagents.md §11-§12) ------------------------
def _tree_errors(exc: Exception) -> HTTPException:
    from lembas.services import tree_view

    if isinstance(exc, tree_view.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, t("No subagent with that id in this chat."))
    # Kept on the device, which cannot answer now: said, and asked again later.
    return HTTPException(
        status.HTTP_409_CONFLICT,
        t("That is kept on the computer this chat runs on, which is not linked right now."),
    )


@router.get("/chats/{chat_id}/tree")
async def get_tree(
    caller: Me, db: Db, chat_id: str, since: int = Query(0, ge=0)
) -> dict[str, Any]:
    """The chat's subagent tree and the room's messages after `since`: a chat
    on this server from its rows, an agent chat from its device (`live`), or
    as the device last sent it (`live` false) while it is not linked."""
    from lembas.services import tree_view

    chat = owned_chat(db, chat_id, caller.user)
    return await tree_view.tree(db, chat, since)


@router.get("/chats/{chat_id}/subagents/{agent_id}/transcript")
async def get_subagent_transcript(
    caller: Me, db: Db, chat_id: str, agent_id: str
) -> dict[str, Any]:
    """A subagent's conversation, read-only, live or after it ended."""
    from lembas.services import tree_view

    chat = owned_chat(db, chat_id, caller.user)
    try:
        return await tree_view.transcript(db, chat, agent_id)
    except (tree_view.NotFound, tree_view.OnTheDevice) as exc:
        raise _tree_errors(exc) from exc


@router.get("/chats/{chat_id}/subagents/{agent_id}/findings")
async def get_subagent_findings(
    caller: Me, db: Db, chat_id: str, agent_id: str
) -> dict[str, Any]:
    """A subagent's findings, kept after it ended."""
    from lembas.services import tree_view

    chat = owned_chat(db, chat_id, caller.user)
    try:
        return await tree_view.findings(db, chat, agent_id)
    except (tree_view.NotFound, tree_view.OnTheDevice) as exc:
        raise _tree_errors(exc) from exc


@router.post("/chats/{chat_id}/subagents/{agent_id}/stop")
async def stop_subagent(caller: Me, db: Db, chat_id: str, agent_id: str) -> dict[str, Any]:
    """The person stops a subagent and those under it (§10)."""
    from lembas.services import tree_view

    chat = owned_chat(db, chat_id, caller.user)
    try:
        return {"stopped": await tree_view.stop(db, chat, agent_id)}
    except (tree_view.NotFound, tree_view.OnTheDevice) as exc:
        raise _tree_errors(exc) from exc


# --- The activity panel: context, outputs, background agents, plan -----------------
@router.get("/chats/{chat_id}/activity")
async def get_activity(
    caller: Me,
    db: Db,
    chat_id: str,
    since: int = Query(0, ge=0),
    context: bool = Query(True),
) -> dict[str, Any]:
    """Everything the web's activity panel shows, in one answer: the agents
    (a team's members as a tree, helpers and commands, each with its state,
    elapsed time, model and latest step), the team's room after `since`, the
    context by category (`context=false` leaves it out: it is the costly
    part), the files written and the plan. Follow it with `/activity/events`."""
    chat = owned_chat(db, chat_id, caller.user)
    view = await activity_service.snapshot(db, chat, caller.user, since=since, with_context=context)
    return {"activity": serialise.activity(view)}


@router.get("/chats/{chat_id}/activity/events")
async def activity_events(caller: Me, db: Db, chat_id: str) -> StreamingResponse:
    """Server-sent events while somebody looks at the panel: `activity`
    `{version, summary}` whenever the panel's facts change (the first at
    once), a keepalive comment in between, and `close` after a while -- open
    it again. On `activity`, fetch `GET /activity` (or poll that, comparing
    `version`, where a stream does not suit)."""
    chat = owned_chat(db, chat_id, caller.user)
    return StreamingResponse(
        _activity_frames(chat.id, caller.user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


ACTIVITY_POLL = 1.0
ACTIVITY_LIFETIME = 600.0


async def _activity_frames(chat_id: str, user_id: str):
    import asyncio
    import json
    import time

    from lembas.db.models import User
    from lembas.db.session import session_scope
    from lembas.services import sse

    sent = ""
    began = last = time.monotonic()
    while time.monotonic() - began < ACTIVITY_LIFETIME:
        with session_scope() as session:
            chat = session.get(Chat, chat_id)
            user = session.get(User, user_id)
            if chat is None or user is None:
                break
            view = await activity_service.snapshot(session, chat, user, with_context=False)
        if view["version"] != sent:
            sent = view["version"]
            payload = {"version": sent, "summary": view["summary"]}
            yield sse.event("activity", json.dumps(payload, separators=(",", ":")))
            last = time.monotonic()
        elif time.monotonic() - last > 15:
            yield sse.KEEPALIVE
            last = time.monotonic()
        await asyncio.sleep(ACTIVITY_POLL)
    yield sse.event("close", "{}")


def _find_agent(db, chat: Chat, kind: str, agent_id: str) -> dict[str, Any]:
    kinds = (activity_service.KIND_MEMBER, activity_service.KIND_JOB)
    if chat.device_token_id or kind not in kinds:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That agent is not here."))
    entry = activity_service.find(activity_service.agents(db, chat), kind, agent_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That agent is not here."))
    return entry


@router.get("/chats/{chat_id}/agents/{kind}/{agent_id}")
async def get_agent(caller: Me, db: Db, chat_id: str, kind: str, agent_id: str) -> dict[str, Any]:
    """One background agent and its whole conversation, read-only: the brief
    it was given, the messages it took in (`machine`), and each reply with its
    tool calls. `kind` is `member` (`agent_id` its name) or `job`. A helper
    nested under a member is found by its job id."""
    from lembas.services import team_run

    chat = owned_chat(db, chat_id, caller.user)
    entry = _find_agent(db, chat, kind, agent_id)
    child = ""
    if kind == activity_service.KIND_MEMBER:
        row = team_run.member_any(db, chat.id, agent_id)
        child = (row.chat_id or "") if row is not None else ""
    else:
        found = activity_service.job_owner(db, chat, agent_id)
        child = (found[1].child_chat_id or "") if found else ""
    turns = activity_service.chat_transcript(db, child) if child else []
    return {"agent": serialise.agent(entry), "transcript": turns}


@router.post("/chats/{chat_id}/agents/{kind}/{agent_id}/stop")
async def stop_agent(caller: Me, db: Db, chat_id: str, kind: str, agent_id: str) -> dict[str, Any]:
    """Stop a background agent: a member leaves the team with those under it
    (the lead is told in a note), a job is stopped as `job_stop` stops it."""
    from lembas.services import subagent as subagent_service
    from lembas.services import team_run

    chat = owned_chat(db, chat_id, caller.user)
    entry = _find_agent(db, chat, kind, agent_id)
    if not entry["stoppable"]:
        raise HTTPException(status.HTTP_409_CONFLICT, t("That agent is not running."))
    if kind == activity_service.KIND_MEMBER:
        stopped = await team_run.person_stop(chat.id, agent_id)
    else:
        found = activity_service.job_owner(db, chat, agent_id)
        owner = found[0] if found else chat.id
        stopped = [agent_id] if await subagent_service.stop_job(owner, agent_id) else []
    if not stopped:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That agent is not here."))
    return {"stopped": stopped}


@router.post("/chats/{chat_id}/compact")
async def compact_chat(caller: Me, db: Db, chat_id: str) -> dict[str, Any]:
    """Summarise the earlier turns and stop sending them -- the activity
    panel's Compact (`context.compactable` says whether it can now). A 409
    says why not, in the person's language. Answers the chat's newest page of
    messages, the divider among them."""
    chat = owned_chat(db, chat_id, caller.user)
    await chats_api.compact(db, chat, caller.user)
    db.refresh(chat)
    page, more = _page(db, chat, before="", limit=PAGE_DEFAULT)
    return {"messages": [serialise.message(m) for m in page], "more": more}


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


# --- The show card's file -------------------------------------------------------------
SHOW_MAX_BYTES = 1024 * 1024


@router.get("/chats/{chat_id}/show")
async def show_file(
    caller: Me, db: Db, chat_id: str, node: str = "", version: str = "", shown: str = ""
) -> Response:
    """What a `show` card opens, as plain text: `?node=<id>&version=<id>` for a
    file in the tree (the card's `node_id` and `version_id`), `?shown=<id>` for
    the copy a linked device sent (`shown_id`).

    The twin of the page's `/chat/{chat}/show`: a tree file is read through
    the chat's view, rebuilt now, so a grant taken away since takes the file
    away too. At most 1 MB, and text only -- an HTML file comes back as its
    source and is never rendered here; what the app does with it is the app's
    (it is model output, rule 6)."""
    chat = owned_chat(db, chat_id, caller.user)
    gone = t("That file is not here any more.")
    if shown:
        from lembas.services import shown as shown_service

        row = shown_service.get(db, chat.id, shown)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, gone)
        text = shown_service.text_of(db, row)
    else:
        from lembas.db.models import Node
        from lembas.services import file_ui, fs

        user = caller.user
        found = db.get(Node, node) if node else None
        if found is None or found.org_id != user.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, gone)
        try:
            opened = file_ui.open_file(fs.view(db, user, chat), found.path, version)
        except fs.FsError as refused:
            raise HTTPException(status.HTTP_404_NOT_FOUND, refused.message) from refused
        if opened.size > SHOW_MAX_BYTES or opened.kind == "large":
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, t("This file is too large to show here.")
            )
        if opened.kind in ("image", "binary"):
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                t("This is not a text file, so it is not shown here."),
            )
        text = opened.text
    return Response(
        text,
        media_type="text/plain; charset=utf-8",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


__all__ = ["router"]
