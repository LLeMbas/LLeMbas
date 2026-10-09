"""Whether agent chats exist here at all (they run on a linked LLeMbas CLI,
which decides what it may do), how long an approval waits, the terminal
panel, and helpers."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from lembas.api.deps import AdminUser, Db
from lembas.services import settings_store
from lembas.services.agent import policy
from lembas.services.agent import terminal as terminal_service
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/agents", tags=["admin-agents"])


@router.get("")
async def agents_page(request: Request, db: Db, user: AdminUser, saved: bool = False):
    values = settings_store.agents(db)
    return render(
        request,
        "admin/agents.html",
        {
            "values": values,
            "terminal_count": terminal_service.count(),
            "modes": [(m, policy.MODE_LABELS[m], policy.MODE_HINTS[m]) for m in policy.MODES],
            # A group of its own, saved by its own form. Subagents are not an
            # agent-chat feature -- an ordinary chat can delegate too -- but
            # this is the page somebody looks at when they want to know what a
            # reply is allowed to set going on its own, and a nav entry for one
            # card would be worse than the near-miss.
            "subagents": settings_store.subagents(db),
            "saved": saved,
        },
    )


@router.post("/subagents")
async def save_subagents(
    db: Db,
    user: AdminUser,
    enabled: bool = Form(False),
    max_per_reply: int = Form(4),
    max_concurrent: int = Form(6),
    max_rounds: int = Form(30),
    wall_seconds: int = Form(600),
    max_completion_tokens: int = Form(60_000),
    keep_transcript: bool = Form(False),
) -> Response:
    """Its own route because it is its own settings group.

    A single form writing two groups would mean one save handler deciding which
    key each field belongs to, which is a mapping that goes wrong silently. Two
    forms, two keys, and the browser posts only the one that was submitted.
    """
    settings_store.update(
        db,
        {
            "enabled": enabled,
            # Clamped here as well as on read, for the reason the agent settings
            # give: a number with no bound is a way to break the instance from a
            # form. Zero is kept only for the token ceiling, where it means "no
            # ceiling"; everywhere else a zero would be the feature switched off
            # wearing the switch's clothes.
            "max_per_reply": min(max(max_per_reply, 1), 20),
            "max_concurrent": min(max(max_concurrent, 1), 50),
            "max_rounds": min(max(max_rounds, 1), 200),
            "wall_seconds": min(max(wall_seconds, 30), 7200),
            "max_completion_tokens": min(max(max_completion_tokens, 0), 5_000_000),
            "keep_transcript": keep_transcript,
        },
        key=settings_store.SUBAGENTS,
    )
    log.info("subagents %s by %s", "enabled" if enabled else "disabled", user.email)
    return RedirectResponse("/admin/agents?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("")
async def save_agents(
    db: Db,
    user: AdminUser,
    enabled: bool = Form(False),
    approval_timeout: int = Form(900),
    ask_free_text: bool = Form(False),
    terminal_enabled: bool = Form(False),
    terminal_idle_timeout: int = Form(1800),
    terminal_max_sessions: int = Form(20),
    terminal_max_per_user: int = Form(3),
) -> Response:
    settings_store.update(
        db,
        {
            "enabled": enabled,
            "approval_timeout": min(max(approval_timeout, 60), 3600),
            "ask_free_text": ask_free_text,
            "terminal_enabled": terminal_enabled,
            "terminal_idle_timeout": min(max(terminal_idle_timeout, 60), 86400),
            "terminal_max_sessions": min(max(terminal_max_sessions, 1), 500),
            "terminal_max_per_user": min(max(terminal_max_per_user, 1), 50),
        },
        key=settings_store.AGENTS,
    )
    log.info("agent chats %s by %s", "enabled" if enabled else "disabled", user.email)
    return RedirectResponse("/admin/agents?saved=1", status_code=status.HTTP_303_SEE_OTHER)
