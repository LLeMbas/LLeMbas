"""A chat's tool results, kept whole: what `recall` reads (harness spec 3.0.0).

Every tool result a chat's model is given is first given an id (`#0001`) and
stored here in full -- before any cap, and for a stub the text it stands for --
so `recall` can show any of them for as long as the chat exists
(`services/context.py`). The chat's own row cascades them away.

The columns past `text` are what a later insertion decides by: a read's path,
content hash and lines (an unchanged re-read becomes a stub), a listing's or a
search's arguments and the hash of its result (the same one twice becomes a
stub), and for a write or an edit the path and the hash it left, which is how
`write` and `edit` know the file was read in this chat and has not changed
since.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, UUIDPrimaryKey, utcnow

RESULT_READ = "read"
RESULT_EDIT = "edit"
RESULT_OUTPUT = "output"
RESULT_RECALL = "recall"
RESULT_KINDS = (RESULT_READ, RESULT_EDIT, RESULT_OUTPUT, RESULT_RECALL)


class ToolResult(UUIDPrimaryKey, Base):
    __tablename__ = "tool_results"
    __table_args__ = (UniqueConstraint("chat_id", "rid", name="uq_tool_results_chat_rid"),)

    chat_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The id the model sees, `#` and hex: unique in the chat, never reused.
    rid: Mapped[str] = mapped_column(String(16), nullable=False)
    # Which reply it was given in. A plain id: the result outlives an edit
    # that rewinds the message away, and `recall` still finds it.
    message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    # Not a stub: only a full result may be the target of a later stub.
    full: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tool: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    # A read: the path as the call gave it; `key` the resolved absolute path.
    path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sha: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    line_from: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    line_to: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    line_of: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # An output: the call's arguments as JSON with sorted keys, and its text's hash.
    args: Mapped[str] = mapped_column(Text, default="", nullable=False)
    text_sha: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
