"""`ask_user`: stopping to ask the reader something."""

from __future__ import annotations

from typing import Any

from lembas.services.tools.core import (
    FAMILY_ASK,
    RISK_ASK,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)


# --- Asking the reader -------------------------------------------------------
async def _run_ask_user(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Never reached on the normal path.

    `services.generation` intercepts every `ask` call before the runners are
    reached, because the answer comes from a person and `ToolContext` is a
    session-free snapshot that deliberately holds no way to reach one. Getting
    here means some other path called `run_tool` directly, and saying so is
    better than returning an empty answer the model would treat as a reply.

    It read `args["question"]`, singular, against a schema that declares
    `questions` and a list -- so the event it built always carried an empty
    `query`, and the card showed a refusal with no sign of what had been asked.
    Harmless only because this path is unreachable, which is exactly why nothing
    caught it: schema drift on a branch no test exercises. Tolerant of the same
    spellings `generation._questions_in` accepts, rather than importing it,
    which would be a circular import for one field on a dead path.
    """
    asked: Any = args.get("questions") or args.get("question") or ""
    if isinstance(asked, list):
        asked = asked[0] if asked else ""
    if isinstance(asked, dict):
        asked = asked.get("question") or ""
    question = str(asked).strip()
    return ToolOutcome(
        "That question could not be put to anyone, so it has gone unanswered. "
        "Carry on without it, or say what you need.",
        {
            "name": "ask_user",
            "kind": "ask",
            "query": question,
            "status": "error",
            "error": "No one was there to ask.",
            "results": [],
        },
    )


TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name="ask_user",
        family=FAMILY_ASK,
        # The spec's text and schema: a `why`, up to four questions each
        # with a chip `header`, options with a `recommended` one, and the
        # answer format the card produces (`generation._answered`).
        description=spec_description("ask_user"),
        parameters=spec_parameters("ask_user"),
        # Never resolved by this runner. The reader answers it, in every
        # mode, and the loop turns their answer into the outcome -- see
        # services/interaction.py. The runner exists so that a call reaching
        # it by some path that skipped the loop fails loudly rather than
        # silently returning nothing.
        run=_run_ask_user,
        risk=RISK_ASK,
    ),
)
