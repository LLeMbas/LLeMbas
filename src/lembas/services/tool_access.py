"""Who may be offered which administrator-defined tool.

One definition, used by the resolver that builds a chat's tool set and by the
admin screens that say what a row will reach. It mirrors
`permissions.models_visible_to` rather than `services/sharing.py`, and the
difference matters: sharing has no admin branch because reading someone's
private notes is not something a role should grant, while a tool is instance
configuration an administrator could give themselves in one click anyway.

`everything=True` is for callers that need to map a tool *name* back to what it
belongs to -- the harness, the prompt preview -- rather than to decide what to
offer. Gating on visibility there would mean an administrator's own preview
disagreeing with what a reader actually gets.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import CustomTool, McpServer, User


def _permitted(rows: list, user: User | None, *, everything: bool) -> list:
    if everything or (user is not None and user.is_admin):
        return rows
    if user is None:
        return []
    member_of = {group.id for group in user.groups}
    return [
        row for row in rows if row.public or member_of.intersection({g.id for g in row.groups})
    ]


def visible_custom_tools(
    db: DBSession, user: User | None, *, everything: bool = False
) -> list[CustomTool]:
    """Enabled custom tools this user may be offered, in position order."""
    rows = list(
        db.scalars(
            select(CustomTool)
            .where(CustomTool.enabled.is_(True))
            .order_by(CustomTool.position, CustomTool.slug)
        )
    )
    return _permitted(rows, user, everything=everything)


def visible_mcp_servers(
    db: DBSession, user: User | None, *, everything: bool = False
) -> list[McpServer]:
    """Enabled MCP servers this user may be offered, in position order."""
    rows = list(
        db.scalars(
            select(McpServer)
            .where(McpServer.enabled.is_(True))
            .order_by(McpServer.position, McpServer.slug)
        )
    )
    return _permitted(rows, user, everything=everything)


__all__ = ["visible_custom_tools", "visible_mcp_servers"]
