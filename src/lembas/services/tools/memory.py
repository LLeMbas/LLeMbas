"""`memory`: what is remembered about the person."""

from __future__ import annotations

from typing import Any

from lembas.db.models import (
    User,
)
from lembas.db.session import session_scope
from lembas.services import memories as memories_service
from lembas.services.tools.core import (
    FAMILY_MEMORY,
    RISK_WRITE,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)


# --- Memory ------------------------------------------------------------------
async def _run_memory(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """The spec's `memory`: one change or a list, applied together or not at
    all (`memories.apply`, the CLI's semantics and messages). One target here,
    `user` -- what is remembered about the person; the spec's other targets
    are the CLI's files, and `narrow.llembas` does not offer them."""
    target = str(args.get("target") or "user").strip()
    if target != "user":
        return ToolOutcome(
            "There is one memory here, about the person you are talking to: target 'user'.",
            {"name": "memory", "status": "error", "error": "No such target."},
        )
    raw_ops = args.get("operations")
    if isinstance(raw_ops, list) and raw_ops:
        ops = [op for op in raw_ops if isinstance(op, dict)]
    elif args.get("action"):
        ops = [
            {
                "action": args.get("action"),
                "content": args.get("content"),
                "new_text": args.get("new_text"),
                "old_text": args.get("old_text"),
            }
        ]
    else:
        ops = []
    with session_scope() as db:
        user = db.get(User, context.owner_id)
        result = memories_service.apply(db, owner=user, ops=ops, group=context.data_group)
    marks = "".join(
        {"add": "+", "remove": "−"}.get(str(op.get("action")), "~") for op in ops
    )
    if not result["ok"]:
        shown = result.get("entries")
        if shown is not None:
            listed = "\n".join(f"{i}. {entry}" for i, entry in enumerate(shown, 1)) or "(none)"
            text = f"{result['message']}\n\nCurrent entries ({result['usage']}):\n{listed}"
        else:
            text = f"{result['message']}\n\nuser profile: {result['usage']}"
        return ToolOutcome(
            text,
            {"name": "memory", "query": marks, "status": "error", "error": result["message"]},
        )
    return ToolOutcome(
        f"{result['message']} user profile: {result['usage']}. Saved — this update is "
        "complete; do not repeat it.",
        {
            "name": "memory",
            "query": "; ".join(
                str(op.get("content") or op.get("new_text") or op.get("old_text") or "")
                for op in ops
            )[:200],
            "detail": marks,
            "status": "ok",
            "results": [],
        },
    )


TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="memory",
        family=FAMILY_MEMORY,
        description=spec_description("memory"),
        parameters=spec_parameters("memory"),
        run=_run_memory,
        risk=RISK_WRITE,
    ),
)
