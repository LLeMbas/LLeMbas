"""Files a linked device showed in an agent chat (`_lembas/show`, harness spec 3.0.0).

The `show` tool in a chat names a file in the tree, and the viewer fetches that
version through the resolver. A device's file is on the device, which this
instance cannot reach, so the device sends the text with the request (at most
1 MB) and it is kept here: the bytes in the blob store, by hash, and one row per
time it was shown, so a card drawn later still opens what the device sent then.

The row is a blob reference (`services/blobs.py` counts it): written with an
`incref`, and `services/shown.py` releases it when the chat goes. The chat's own
row cascades the rows away in the database as well, for any delete that went
around `delete_chats`; `blobs.recount` repairs the count after one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, UUIDPrimaryKey, utcnow


class ShownFile(UUIDPrimaryKey, Base):
    __tablename__ = "shown_files"

    chat_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The reply it was shown in, and the device's call: a plain id each, read to
    # find the card's file again, never joined on.
    message_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    call_id: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # As the device said it: relative to the session's working directory.
    path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    name: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    # markdown | code | text | html (acp.md), and the language of `code`.
    kind: Mapped[str] = mapped_column(String(16), default="text", nullable=False)
    language: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    line: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blob_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
