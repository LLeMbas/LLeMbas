"""The import-time registry of built-in tools, and former tool names."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lembas.services import plans
from lembas.services.tools import (
    ask,
    files,
    knowledge,
    memory,
    notes,
    plan,
    recall,
    reports,
    scratch,
    skills,
    web,
)
from lembas.services.tools.core import ToolDef, spec_aliases

# Every built-in tool, in the order they are offered: each module's `TOOLS`, one
# concern per module, so a family that goes away takes its module with it.
REGISTRY: dict[str, ToolDef] = {
    tool.name: tool
    for tool in (
        *files.TOOLS,
        *recall.TOOLS,
        *web.TOOLS,
        *knowledge.TOOLS,
        *notes.TOOLS,
        *scratch.TOOLS,
        *memory.TOOLS,
        *reports.TOOLS,
        *skills.TOOLS,
        *ask.TOOLS,
        *plan.TOOLS,
    )
}


def resolve_alias(
    name: str, args: dict[str, Any], offered: Mapping[str, Any] | None = None
) -> tuple[str, dict[str, Any]]:
    """A tool call by a former name, as the call it means.

    The spec keeps each tool's old names per project (`aliases.llembas`), and a
    transcript or a model trained on the old name still uses it. Most aliases
    take the same arguments; the ones that do not are converted here: `todo`'s
    list becomes one "Work" phase for `plan_write` (the spec's conversion,
    `plans.from_todos`), and `subagent_run`'s task, title, context and write
    become `task`'s prompt and description, with `write` kept as the helper's
    own extra. A name that is no alias comes back unchanged.

    So does a name this request has a tool by. 1.3.0 rewrote every alias
    regardless, while the spec already listed `fetch`, `notes_get`,
    `memory_add` and the rest as former names of tools this server did not have
    yet, and every call to them answered "Unknown tool". `offered` is what this
    request was offered: given it, the call keeps its name when that name is
    offered or the alias's target is not. Without it, the registry stands in
    for the first check.
    """
    target = spec_aliases().get(name)
    if target is None:
        return name, args
    if name in (REGISTRY if offered is None else offered):
        return name, args
    if offered is not None and target not in offered:
        return name, args
    if name == "todo":
        plan = plans.from_todos(args.get("todos"))
        return target, {k: v for k, v in plan.items() if k in ("title", "phases")} if plan else {}
    # The library's former names (spec 1.2.0): each was one action of a tool
    # that is now several, so the action is filled in from the name.
    if name in ("memory_add", "memory_forget"):
        content = args.get("content")
        if name == "memory_add":
            return target, {"target": "user", "action": "add", "content": content}
        return target, {"target": "user", "action": "remove", "old_text": content}
    if name in ("notes_create", "notes_edit", "notes_delete"):
        action = name.split("_", 1)[1]
        kept = {k: v for k, v in args.items() if k in ("id", "title", "body")}
        return target, {"action": action, **kept}
    if name == "skill_create":
        return target, {
            "operations": [
                {
                    "action": "create",
                    "name": args.get("name"),
                    "description": args.get("description"),
                    "instructions": args.get("body"),
                }
            ]
        }
    if name == "skill_edit":
        # Its description and body as they were sent, either one alone: the
        # runner reads `_set` against the skill as it stands, since a patch
        # would need the text this call never quoted.
        return target, {
            "operations": [
                {
                    "action": "patch",
                    "name": args.get("name"),
                    "reason": args.get("reason"),
                    "_set": {"description": args.get("description"), "body": args.get("body")},
                }
            ]
        }
    if name == "subagent_run":
        prompt = str(args.get("task") or "").strip()
        briefing = str(args.get("context") or "").strip()
        if briefing:
            prompt = f"{prompt}\n\n## Context\n{briefing}" if prompt else briefing
        converted = {
            "description": str(args.get("title") or "").strip()[:60],
            "prompt": prompt,
        }
        if args.get("write"):
            converted["write"] = True
        return target, converted
    return target, args
