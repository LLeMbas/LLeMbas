"""What a linked device shows in an agent chat (`_lembas/show`, harness spec 3.0.0).

The device's `show` tool sends a file's text, since this instance cannot reach
the device's disk: `{ sessionId, toolCallId, path, kind, language?, line?, text }`,
the text at most 1 MB. It is kept as a blob attached to the chat
(`db/models/shown.py`) and becomes the same card the chat's own `show` tool
makes -- `event["show"]` on the tool call, in the same shape -- with the tree's
two ids empty and `shown_id` naming the kept copy instead. The viewer reads that
copy from `GET /api/chats/{chat}/shown/{id}`, as text, whoever's eyes it is for:
the content came from the device's model and is untrusted (rule 6), so the
response is never HTML to the browser and the viewer renders it as it renders a
tree file -- HTML only in its sandboxed frame.
"""

from __future__ import annotations

import mimetypes
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models.shown import ShownFile
from lembas.services import blobs

MAX_BYTES = 1024 * 1024
KINDS = ("markdown", "code", "text", "html")


class Refused(ValueError):
    """A show request that is not kept, with the reason the device's model is told."""


def keep(
    db: DBSession,
    *,
    chat_id: str,
    message_id: str,
    params: dict[str, Any],
) -> ShownFile:
    """Store what the device sent and the row naming it. Does not commit."""
    text = params.get("text")
    if not isinstance(text, str):
        raise Refused("The request carries no text to show.")
    data = text.encode("utf-8", errors="replace")
    if len(data) > MAX_BYTES:
        raise Refused(f"The file is {round(len(data) / 1024)} KB; at most 1 MB can be shown.")
    path = str(params.get("path") or "").strip()[:1000]
    if not path:
        raise Refused("The request names no file.")
    kind = str(params.get("kind") or "text")
    if kind not in KINDS:
        kind = "text"
    line = params.get("line")
    line = line if isinstance(line, int) and not isinstance(line, bool) and line > 0 else 0
    digest = blobs.put_bytes(db, data)
    blobs.incref(db, digest)
    row = ShownFile(
        chat_id=chat_id,
        message_id=message_id or "",
        call_id=str(params.get("toolCallId") or "")[:200],
        path=path,
        name=path.rstrip("/").rsplit("/", 1)[-1][:300],
        kind=kind,
        language=str(params.get("language") or "")[:40] if kind == "code" else "",
        line=line,
        blob_hash=digest,
        size=len(data),
    )
    db.add(row)
    db.flush()
    return row


def payload(row: ShownFile) -> dict[str, Any]:
    """`event["show"]` for a device's file: the chat `show` tool's keys, the
    tree's ids empty, and where the kept copy is read from."""
    return {
        "path": row.path,
        "name": row.name,
        "kind": row.kind,
        "language": row.language,
        "line": row.line,
        "node_id": "",
        "version_id": "",
        "sha": row.blob_hash,
        "size": row.size,
        "mime": mimetypes.guess_type(row.name)[0] or "text/plain",
        "source": "device",
        "shown_id": row.id,
        "url": f"/api/chats/{row.chat_id}/shown/{row.id}",
    }


def get(db: DBSession, chat_id: str, shown_id: str) -> ShownFile | None:
    row = db.get(ShownFile, shown_id)
    return row if row is not None and row.chat_id == chat_id else None


def text_of(db: DBSession, row: ShownFile) -> str:
    return blobs.read_bytes(db, row.blob_hash).decode("utf-8", errors="replace")


def forget_for_chats(db: DBSession, chat_ids: list[str]) -> int:
    """Release the blobs of every file shown in these chats, and their rows.
    Called by `chat.delete_chats` before the chats go. Does not commit."""
    if not chat_ids:
        return 0
    rows = list(db.scalars(select(ShownFile).where(ShownFile.chat_id.in_(chat_ids))))
    for row in rows:
        blobs.decref(db, row.blob_hash)
        db.delete(row)
    return len(rows)


__all__ = ["KINDS", "MAX_BYTES", "Refused", "forget_for_chats", "get", "keep", "payload",
           "text_of"]
