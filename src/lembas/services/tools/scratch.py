"""`scratch_write`: the chat's scratch document in the canvas panel."""

from __future__ import annotations

from typing import Any

from lembas.db.models import (
    AUTHOR_MODEL,
    Chat,
)
from lembas.db.session import session_scope
from lembas.services import scratch as scratch_service
from lembas.services.tools.core import (
    _STRING,
    FAMILY_SCRATCH,
    RISK_READ,
    ToolContext,
    ToolDef,
    ToolOutcome,
    _object,
)


# --- The chat's scratch document ---------------------------------------------
async def _run_scratch_write(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Write into the pad the person can see beside the conversation.

    Opens its own session, like every other runner: a generation outlives the
    session that resolved it.

    `append` is a service function rather than a read-and-concatenate here,
    because two calls in one round would otherwise each read the same body and
    the second would drop the first.
    """
    with session_scope() as db:
        chat = db.get(Chat, context.chat_id) if context.chat_id else None
        if chat is None:
            return ToolOutcome(
                "There is no chat to write into.",
                {"name": "scratch_write", "status": "error", "error": "No chat."},
            )
        doc = scratch_service.for_chat(db, chat)
        text = str(args.get("text") or "")
        if str(args.get("mode") or "append").strip().lower() == "replace":
            scratch_service.update(db, doc, body=text, author=AUTHOR_MODEL)
            what = "Replaced"
        else:
            scratch_service.append(db, doc, text, author=AUTHOR_MODEL)
            what = "Added to"
        return ToolOutcome(
            f"{what} the scratch document ({len(doc.body)} characters). "
            "It is on screen beside the conversation.",
            {
                "name": "scratch_write",
                "query": doc.title,
                "status": "ok",
                "results": [],
                # Opens the tab, the same way a file tool does. Never brings it
                # to the front -- see `canvas.open_tab`.
                "canvas": {
                    "key": f"scratch:{chat.id}",
                    "title": doc.title,
                    "source": "scratch",
                },
            },
        )


TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="scratch_write",
        family=FAMILY_SCRATCH,
        description=(
            "Write into this chat's scratch document, which the person can "
            "see and edit beside the conversation. Use it for something you "
            "are building up as you work — a draft, a table of findings, a "
            "list you keep adding to — rather than putting it in the reply "
            "and rewriting the whole thing each turn. It is not searchable "
            "later and belongs to this chat alone; use a note for anything "
            "worth keeping beyond it."
        ),
        parameters=_object(
            {
                "mode": {
                    **_STRING,
                    "enum": ["append", "replace"],
                    "description": "append is the default.",
                },
                "text": {**_STRING, "description": "Markdown."},
            },
            ["text"],
        ),
        run=_run_scratch_write,
        # What a tool does to the *world the four modes govern*, which is the
        # machine -- and this cannot touch it. RISK_WRITE would put an
        # approval card on screen every time the model jotted a paragraph,
        # which is exactly the interruption batching exists to prevent. The
        # same argument `plan_update` carries.
        risk=RISK_READ,
    ),
)
