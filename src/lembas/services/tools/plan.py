"""`plan_write`, `plan_update` and `plan_submit`."""

from __future__ import annotations

from typing import Any

from lembas.db.models import (
    Chat,
)
from lembas.db.session import session_scope
from lembas.services import plans
from lembas.services.tools.core import (
    FAMILY_PLAN,
    RISK_READ,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)


# --- The plan ------------------------------------------------------------------
def _plan_event(name: str, plan: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """What the transcript keeps of a plan call: the structure, for the panel and
    the card, and nothing drawn in the thread (`block: plan`)."""
    return {
        "name": name,
        "status": "ok",
        "query": plan.get("title") or "",
        "results": [],
        "plan": plan,
        **extra,
    }


async def _run_plan_write(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """The whole plan, replacing any before it. The result is the plan rendered
    with the ids this side gave it, which is what plan_update then takes."""
    plan = plans.normalise(args)
    if not plan or not plan.get("steps"):
        return ToolOutcome(
            "A plan needs a title and at least one phase with a task in it. Nothing was "
            "written.",
            {"name": "plan_write", "status": "error", "error": "Empty plan.", "results": []},
        )
    return ToolOutcome(plans.render(plan), _plan_event("plan_write", plan))


def _current_plan(context: ToolContext) -> dict[str, Any]:
    if not context.chat_id:
        return {}
    with session_scope() as db:
        chat = db.get(Chat, context.chat_id)
        return chat.plan if chat is not None else {}


async def _run_plan_update(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Changes to the plan as it stands on the chat -- or, within one reply, as
    this reply last wrote it: the loop hands the latest in through `args` under
    `_current`, since `_persist` writes the row only at the end."""
    current = args.pop("_current", None) or _current_plan(context)
    result = plans.apply(current, args)
    if "error" in result:
        return ToolOutcome(
            result["error"],
            {"name": "plan_update", "status": "error", "error": result["error"], "results": []},
        )
    plan = result["plan"]
    return ToolOutcome(plans.render(plan), _plan_event("plan_update", plan))


async def _run_plan_submit(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """The plan put to the person. The loop sees `plan_final` and stops the
    reply on the plan card; what they decide becomes this call's result there
    (`generation._settle_plan`). This runner only hands the plan up."""
    current = args.pop("_current", None) or _current_plan(context)
    plan = plans.normalise(current)
    if not plan:
        return ToolOutcome(
            "There is no plan to submit yet. Write one with plan_write first.",
            {"name": "plan_submit", "status": "error", "error": "No plan.", "results": []},
        )
    return ToolOutcome(plans.render(plan), _plan_event("plan_submit", plan, plan_final=True))


TOOLS: tuple[ToolDef, ...] = (
    # The plan, as the spec defines it. `interact` in the spec; here the
    # risk class governs the four modes of a machine, and these touch no
    # machine -- the same argument `scratch_write` makes -- so they are
    # reads, offered in every mode and never put on an approval card.
    ToolDef(
        name="plan_write",
        family=FAMILY_PLAN,
        description=spec_description("plan_write"),
        parameters=spec_parameters("plan_write"),
        run=_run_plan_write,
        risk=RISK_READ,
    ),
    ToolDef(
        name="plan_update",
        family=FAMILY_PLAN,
        description=spec_description("plan_update"),
        parameters=spec_parameters("plan_update"),
        run=_run_plan_update,
        risk=RISK_READ,
    ),
    # Offered only in Plan mode -- `resolve_tools` withholds it elsewhere.
    # It is the approval: the reply stops on the plan card and the answer
    # is the call's result.
    ToolDef(
        name="plan_submit",
        family=FAMILY_PLAN,
        description=spec_description("plan_submit"),
        parameters=spec_parameters("plan_submit"),
        run=_run_plan_submit,
        risk=RISK_READ,
    ),
)
