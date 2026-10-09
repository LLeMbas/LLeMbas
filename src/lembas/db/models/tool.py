"""Tools an administrator defined: HTTP endpoints and remote MCP servers.

Both are instance configuration rather than someone's content, so access is
shaped like `Model` and not like a note: a row is either public or reachable
through the groups it names, resolved the way `permissions.models_visible_to`
resolves a model. There is deliberately no per-user tool. A tool is a credential
pointed at a third party, and "anyone may define one" is a different feature
with a different threat model.

The two tables are near-twins on purpose -- name, slug, secret, group list,
last check -- because an administrator adding one should not have to learn a
second screen. What differs is what sits between the row and the model: a
custom tool *is* one call, described here in full, while an MCP server is a
conversation whose tools are discovered and cached.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.types import JSONDict, JSONList

if TYPE_CHECKING:
    # Annotation only; SQLAlchemy resolves the real class from its registry.
    from lembas.db.models.user import Group

# How a row's secret is attached to a request. Stored values, so these are
# schema rather than presentation.
SECRET_NONE = "none"
SECRET_BEARER = "bearer"
SECRET_HEADER = "header"
SECRET_QUERY = "query"

SECRET_PLACEMENTS = (SECRET_NONE, SECRET_BEARER, SECRET_HEADER, SECRET_QUERY)

# How a response becomes text for the model.
RESPONSE_TEXT = "text"  # prose; HTML reduced by fetch.html_to_text
RESPONSE_JSON = "json"  # parsed, narrowed by response_path, pretty-printed
RESPONSE_RAW = "raw"  # verbatim, truncated -- CSV, plain logs

RESPONSE_MODES = (RESPONSE_TEXT, RESPONSE_JSON, RESPONSE_RAW)

custom_tool_groups = Table(
    "custom_tool_groups",
    Base.metadata,
    Column(
        "tool_id", String(32), ForeignKey("custom_tools.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("group_id", String(32), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)

mcp_server_groups = Table(
    "mcp_server_groups",
    Base.metadata,
    Column(
        "server_id", String(32), ForeignKey("mcp_servers.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("group_id", String(32), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)


class CustomTool(UUIDPrimaryKey, Timestamps, Base):
    """One HTTP call, described well enough for a model to decide to make it."""

    __tablename__ = "custom_tools"

    # `slug` IS the function name sent to the endpoint, so it is bound by the
    # charset those accept and is fixed once the row exists: it is also half of
    # this tool's prompt-fragment key. `name` is the human label, shown in the
    # admin list and in the transcript.
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    # Sent verbatim in the tools array. The only thing the model has to decide
    # with, which is why the form insists on it.
    description: Mapped[str] = mapped_column(Text, default="")
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    # The *default* text of this tool's harness fragment. An administrator's
    # edit on /admin/prompts is an override stored in the settings group like
    # any other, so a tool deleted and recreated under the same slug keeps the
    # wording somebody chose for it.
    guidance: Mapped[str] = mapped_column(Text, default="")

    method: Mapped[str] = mapped_column(String(8), default="GET", nullable=False)
    # {{name}} placeholders, filled from the call's arguments. The scheme and
    # the host must be literal -- see services/custom_tools.py for why.
    url_template: Mapped[str] = mapped_column(String(1000), nullable=False)
    body_template: Mapped[str] = mapped_column(Text, default="")
    headers_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    secret_encrypted: Mapped[str] = mapped_column(Text, default="")
    secret_placement: Mapped[str] = mapped_column(
        String(16), default=SECRET_BEARER, nullable=False
    )
    secret_name: Mapped[str] = mapped_column(String(120), default="Authorization")

    response_mode: Mapped[str] = mapped_column(String(16), default=RESPONSE_TEXT, nullable=False)
    # A dotted path into a JSON response: "data.items.0.title". Empty is the
    # whole document. Not JSONPath -- that is a dependency and a syntax nobody
    # would remember for the one field they want.
    response_path: Mapped[str] = mapped_column(String(300), default="")
    max_chars: Mapped[int] = mapped_column(Integer, default=8000, nullable=False)
    timeout: Mapped[int] = mapped_column(Integer, default=20, nullable=False)

    # Whether this row may reach loopback, private or link-local addresses. Per
    # row rather than the instance-wide search setting: an administrator naming
    # http://127.0.0.1:11434 by hand is not the same act as a model handing the
    # fetcher a URL it read on a page.
    allow_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")

    groups: Mapped[list[Group]] = relationship(
        "Group", secondary=custom_tool_groups, back_populates="custom_tools"
    )

    def __repr__(self) -> str:
        return f"<CustomTool {self.slug}>"


class McpServer(UUIDPrimaryKey, Timestamps, Base):
    """A remote MCP server, reached over streamable HTTP.

    The tools it advertises are cached in `tools_json` rather than given a table
    of their own. A discovered tool carries exactly one administrator decision
    -- offered or not, which `tool_overrides_json` holds -- while credentials,
    guidance and access are all per server; and the whole list is replaced on
    every refresh, so a table would mean reconciling rows against a cache of
    somebody else's document.
    """

    __tablename__ = "mcp_servers"

    # Prefixed onto every tool name this server advertises, so that two servers
    # both exposing "search" do not collide and neither shadows a built-in.
    slug: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)

    guidance: Mapped[str] = mapped_column(Text, default="")

    secret_encrypted: Mapped[str] = mapped_column(Text, default="")
    secret_placement: Mapped[str] = mapped_column(
        String(16), default=SECRET_BEARER, nullable=False
    )
    secret_name: Mapped[str] = mapped_column(String(120), default="Authorization")
    headers_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    timeout: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    max_chars: Mapped[int] = mapped_column(Integer, default=8000, nullable=False)
    allow_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # The last tools/list, cached. One entry per tool:
    # {"name", "offer_name", "description", "schema"}.
    tools_json: Mapped[list[Any]] = mapped_column(JSONList, default=list)
    # Per-tool switch, keyed by the server's own name for it. Absent means on,
    # the same rule the model capability flags follow, so a newly advertised
    # tool works rather than silently doing nothing.
    tool_overrides_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    # What the server answered at initialize, for the admin list.
    protocol_version: Mapped[str] = mapped_column(String(32), default="")
    server_info: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")

    groups: Mapped[list[Group]] = relationship(
        "Group", secondary=mcp_server_groups, back_populates="mcp_servers"
    )

    def __repr__(self) -> str:
        return f"<McpServer {self.slug}>"
