"""Running one tool call, and the turns that carry calls and results."""

from __future__ import annotations

import json
import logging
from typing import Any

from lembas.services import harness_spec
from lembas.services.tools.core import ToolContext, ToolDef, ToolOutcome
from lembas.services.tools.registry import REGISTRY

log = logging.getLogger("lembas.services.tools")


def parse_arguments(tool: ToolDef | None, raw: str) -> dict[str, Any]:
    """One tool call's arguments, as a dict, however badly they were spelled.

    **The only place a call's arguments are interpreted.** It used to live
    inside `run_tool`, while the approval card had its own plain `json.loads`
    that returned `{}` on failure -- so a model emitting malformed JSON got a
    card headed "Run a command" with an empty body, while the fallback below
    handed the raw string to `shell_run` as its command and ran it. The card
    showed one thing and the machine did another, and `policy.decide` was
    handed an empty command line it could match against neither list.

    So the loop parses once and the same dict reaches the card, the policy and
    the runner. Callers that only have a name resolve the `ToolDef` first; a
    `None` tool still parses valid JSON, which is what an unknown name needs.
    """
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        # Small models emit malformed argument JSON often enough that this is a
        # normal path, not an exceptional one. Treat the whole string as the
        # tool's first argument rather than giving up: what it says is required,
        # else the first thing it declares, and only then a guess -- a schema
        # somebody else wrote need not have either.
        parameters = tool.parameters if tool is not None else {}
        properties = parameters.get("properties") or {}
        names = parameters.get("required") or list(properties) or ["query"]
        parsed = {str(names[0]): raw.strip()}
    if not isinstance(parsed, dict):
        return {"query": str(parsed)}
    return parsed


async def run_tool(
    context: ToolContext,
    name: str,
    arguments: str,
    *,
    parsed: dict[str, Any] | None = None,
) -> ToolOutcome:
    """Execute one tool call.

    Never raises. A tool that fails hands the model an explanation and lets it
    carry on -- a failed lookup should produce "I could not find that" rather
    than killing the whole reply.

    The lookup is against what was *offered*, not against everything that
    exists. Reaching for the registry directly meant a model naming a tool its
    chat was gated out of -- a family switched off for the model, a permission
    the reader does not have -- had it run anyway, because only the offer was
    ever filtered.

    `parsed` is the arguments the caller has already interpreted. The generation
    loop passes it so that what a person approved is what runs; a caller with
    only the raw string gets the same result, because both go through
    `parse_arguments`.
    """
    book = REGISTRY if context.tools is None else context.tools
    tool = book.get(name)
    if tool is None:
        return ToolOutcome(
            f"There is no tool called {name!r}.",
            {"name": name, "status": "error", "error": "Unknown tool."},
        )

    if parsed is None:
        parsed = parse_arguments(tool, arguments)
    # The spec's `purpose` (purpose.json): one sentence for the person, added
    # to the parameters of every tool whose spec says `purpose: "purpose"`.
    # Shown beside the call in the transcript; the tool itself never sees it.
    purpose = ""
    if isinstance(parsed, dict) and "purpose" in parsed and takes_purpose(name):
        purpose = str(parsed.pop("purpose") or "").strip()[:300]
    try:
        outcome = await tool.run(context, parsed)
    except Exception as exc:  # a tool must never kill the reply
        log.exception("tool %s failed", name)
        outcome = ToolOutcome(
            f"The {name} tool failed: {exc}",
            {"name": name, "status": "error", "error": str(exc)[:200]},
        )
    if purpose:
        outcome.event["purpose"] = purpose
    return outcome


def takes_purpose(name: str) -> bool:
    """Whether the spec adds `purpose` to this tool's parameters."""
    spec = harness_spec.tools().get(name)
    return bool(spec) and spec.get("purpose") == "purpose"



def assistant_turn(
    calls: list[dict[str, Any]],
    content: str,
    blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The assistant message to send back with the tool results.

    The endpoint needs its own tool_calls echoed before the tool replies, or it
    has nothing to match the tool_call_ids against.

    `blocks` is the turn in the endpoint's own words, when it sent one
    (Anthropic, whose thinking has to come back unchanged, signature and all, with
    the tool calls it led to). Carried only when there is one, so an OpenAI
    endpoint sees exactly the message it always did.
    """
    turn: dict[str, Any] = {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": call["arguments"]},
            }
            for call in calls
        ],
    }
    if blocks:
        turn["assistant_blocks"] = list(blocks)
    return turn


def tool_turn(call: dict[str, Any], content: str) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call["id"],
        "name": call["name"],
        "content": content,
    }
