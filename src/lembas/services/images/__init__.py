"""Making pictures, on a ComfyUI somebody else is running.

Three modules, split along the same seam the rest of the codebase uses:
`comfy.py` speaks HTTP and knows nothing about chats, `workflow.py` turns a
stored template plus a model's arguments into the document ComfyUI wants, and
`tool.py` is the `ToolDef` that ties them to a conversation.

Nothing here executes anything locally. That is the same rule agent chats
follow: the work happens on a service reached over HTTP, chosen and configured
by an administrator, and the security of it is the security of that service.
"""

from __future__ import annotations
