"""Tools a model may call while it answers.

A registry of named callables with a JSON schema each: offered to the endpoint,
executed here when it asks. MCP servers and agentic execution plug in at the
same place, which is why the registry is keyed and grouped rather than being a
handful of if-statements.

Three things gate whether a tool is offered:

* the instance is configured for it (web search has a provider, and so on),
* the reader has the permission, and
* the chat's model is marked as having that tool.

The last is not optional politeness. Sending a ``tools`` array to an endpoint
that does not implement tool calling fails the entire request, exactly the way
sending image parts to a model without vision does.

Tools that *write* -- notes, memories, skills -- need a database session and a
user, and they run inside a background generation that outlives the request. So
they are handed a `ToolContext` carrying an owner id rather than a live session,
and open their own scope, the same way `services.generation` does.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.services import prompts as prompts_service
from lembas.services.llm.openai_client import ToolCallAccumulator

# Everything public in the package, importable from here as it was when this
# was one module. Each name is re-exported deliberately, so unused-import
# checks do not apply to this file.
# ruff: noqa: F401
from lembas.services.tools.context import (
    context_for,
)
from lembas.services.tools.core import (
    FAMILIES,
    FAMILY_ASK,
    FAMILY_CUSTOM,
    FAMILY_FETCH,
    FAMILY_IMAGE,
    FAMILY_KNOWLEDGE,
    FAMILY_MCP,
    FAMILY_MEMORY,
    FAMILY_NOTES,
    FAMILY_PLAN,
    FAMILY_REPORT,
    FAMILY_REPORT_BACK,
    FAMILY_SCHEDULE,
    FAMILY_SCRATCH,
    FAMILY_SEARCH,
    FAMILY_SKILLS,
    FAMILY_SUBAGENT,
    HELPER_AGENTS,
    LLEMBAS_VARS,
    MAX_DOCUMENT_CHARS,
    MAX_FETCH_CHARS,
    RISK_ASK,
    RISK_EXECUTE,
    RISK_INTERACT,
    RISK_READ,
    RISK_WRITE,
    RISKS,
    SPEC_VARIABLES,
    Runner,
    ToolContext,
    ToolDef,
    ToolOutcome,
    ToolSet,
    gate_of,
    spec_aliases,
    spec_description,
    spec_parameters,
)
from lembas.services.tools.registry import (
    REGISTRY,
    resolve_alias,
)
from lembas.services.tools.resolve import (
    GATE_LABELS,
    effective_off,
    enabled_tools,
    families,
    gate_label,
    plan_mode,
    registry,
    resolve_tools,
    scoped_allow,
    scoped_off,
    scoped_on,
    scoped_skills_off,
    scoped_writes_off,
    unattended,
)
from lembas.services.tools.run import (
    assistant_turn,
    parse_arguments,
    run_tool,
    takes_purpose,
    tool_turn,
)
from lembas.services.tools.skills import (
    compose_skill,
    parse_skill,
)
from lembas.web.i18n import N_


def _row_source(db: DBSession):
    """One harness fragment per administrator-defined tool.

    The seam `prompts.register_source` exists for. The row supplies the default
    text and the admin page supplies the override, which is why a tool deleted
    and recreated under the same slug keeps whatever wording somebody chose for
    it -- the override outlives the row.

    Gated on the tool's own family, so the guidance appears exactly when the
    tool it describes is offered and never otherwise. An MCP server gets one
    fragment rather than one per advertised tool: forty entries on the prompts
    page is a page nobody would read.
    """
    from lembas.db.models import CustomTool, McpServer

    for row in db.scalars(select(CustomTool).order_by(CustomTool.position, CustomTool.slug)):
        yield prompts_service.Fragment(
            key=f"tool.custom_{row.slug}",
            label=row.name or row.slug,
            group=prompts_service.GROUP_TOOLS,
            order=500 + row.position,
            families=(f"{FAMILY_CUSTOM}:{row.slug}",),
            hint=f"Appears when the {row.slug} tool is offered.",
            default=row.guidance or "",
        )

    for server in db.scalars(select(McpServer).order_by(McpServer.position, McpServer.slug)):
        yield prompts_service.Fragment(
            key=f"tool.mcp_{server.slug}",
            label=server.name or server.slug,
            group=prompts_service.GROUP_TOOLS,
            order=600 + server.position,
            families=(f"{FAMILY_MCP}:{server.slug}",),
            hint=f"Appears when any tool from {server.name or server.slug} is offered.",
            default=server.guidance or "",
        )


__all__ = [
    "FAMILIES",
    "REGISTRY",
    "ToolCallAccumulator",
    "ToolContext",
    "ToolDef",
    "ToolOutcome",
    "ToolSet",
    "assistant_turn",
    "context_for",
    "enabled_tools",
    "families",
    "parse_arguments",
    "registry",
    "resolve_tools",
    "run_tool",
    "scoped_allow",
    "tool_turn",
]


# Registered at import. `services.tools` is imported by the chat routes, the
# generation service and the prompts admin, so the source is in place before
# anything renders a catalogue.
prompts_service.register_source(_row_source)
