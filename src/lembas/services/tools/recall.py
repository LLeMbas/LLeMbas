"""`recall`: a stored result of this chat shown again (harness spec 3.0.0).

The text is the insertion's (`services/context.py`): the generation loop
answers the call from the chat's stored results. The runner only carries the
call there, which is why what it returns is what the transcript shows.
"""

from __future__ import annotations

from typing import Any

from lembas.services.tools.core import (
    FAMILY_RECALL,
    RISK_READ,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)


async def _run_recall(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    raw = str(args.get("id") or "").strip()
    target = raw if raw.startswith("#") else f"#{raw}"
    entry: dict[str, Any] = {"kind": "recall", "target": raw}
    for key in ("offset", "limit"):
        try:
            value = int(args.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value >= 1:
            entry[key] = value
    return ToolOutcome(
        f"recall {target}",
        {"name": "recall", "status": "ok", "query": target},
        context=entry,
    )


TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="recall",
        family=FAMILY_RECALL,
        description=spec_description("recall"),
        parameters=spec_parameters("recall"),
        run=_run_recall,
        risk=RISK_READ,
    ),
)
