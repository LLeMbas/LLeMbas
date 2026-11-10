"""`phone_action` (harness spec 3.6.0, `phone.md`): doing something on the
person's phone, carried out and confirmed there by the LLeMbas app."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from lembas.services.tools.core import (
    FAMILY_ASK,
    RISK_INTERACT,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)


def reachable(chat_id: str) -> bool:
    """`requires: phone`, at the call: the message being answered -- the
    chat's newest from the person -- is the one the phone sent (`phone.md`
    §1), and the chat is one somebody attends."""
    from lembas.db.models import ROLE_USER, Chat, Message
    from lembas.db.session import session_scope
    from lembas.services.tools.resolve import unattended

    with session_scope() as db:
        chat = db.get(Chat, chat_id) if chat_id else None
        if chat is None or not chat.phone_message_id or unattended(chat):
            return False
        newest = db.scalar(
            select(Message.id)
            .where(Message.chat_id == chat.id, Message.role == ROLE_USER)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        return newest == chat.phone_message_id


async def _run_phone_action(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Checked here; carried out by the generation loop, which puts the
    action on the phone's card and waits for its answer (`_settle_phone`):
    the event's `phone_pending` is what tells it to."""
    from lembas.services import harness_spec
    from lembas.services import phone as phone_service

    event: dict[str, Any] = {"name": "phone_action", "kind": "phone", "results": []}
    if context.unattended or not reachable(context.chat_id):
        text = harness_spec.tools()["phone_action"]["unavailable"]
        return ToolOutcome(text, {**event, "status": "error", "error": text})
    action, refused = phone_service.check(args)
    if action is None:
        return ToolOutcome(refused, {**event, "status": "error", "error": refused})
    return ToolOutcome(
        "",
        {**event, "status": "ok", "query": action["summary"], "phone": action,
         "phone_pending": True},
    )


TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="phone_action",
        # With `ask_user`: a person answers it, and it is withdrawn wherever
        # nobody attends. Offered only in a chat a phone has written to
        # (`resolve._offered_in`).
        family=FAMILY_ASK,
        description=spec_description("phone_action"),
        parameters=spec_parameters("phone_action"),
        run=_run_phone_action,
        risk=RISK_INTERACT,
    ),
)
