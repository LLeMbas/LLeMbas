"""An MCP client: remote servers over streamable HTTP.

Three parts. `protocol` is the wire format and nothing else -- it knows no
database and no HTTP. `client` owns the transport, which is where the SSRF guard
lives and the reason none of this comes from the reference SDK. `registry` is
where a row becomes a tool the chat loop can be offered.

Local stdio servers are deliberately absent. Spawning a subprocess is the
agentic-execution feature, which wants a confirmation model before it does
anything; a URL is a different act with a different blast radius.
"""

from lembas.services.mcp.client import McpSpec, call_tool, list_tools, spec_from
from lembas.services.mcp.protocol import McpError
from lembas.services.mcp.registry import offer_name, refresh, tool_defs

__all__ = [
    "McpError",
    "McpSpec",
    "call_tool",
    "list_tools",
    "offer_name",
    "refresh",
    "spec_from",
    "tool_defs",
]
