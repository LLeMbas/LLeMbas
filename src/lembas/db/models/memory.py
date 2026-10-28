"""What is remembered about a person: one short fact a record.

Records, not files (harness spec 3.0.0, `filesystem.md` *Memory*): the server
keeps them for chats and for devices alike, per data group, and every one of
them is in front of the model on every turn, up to a budget
(`services/memories.py`). Not shareable: a record about a person is not
content to hand round.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.models.data_group import InDataGroup

# Who wrote a record: a person in Settings, or a model through the `memory` tool.
AUTHOR_USER = "user"
AUTHOR_MODEL = "model"


class Memory(UUIDPrimaryKey, Timestamps, InDataGroup, Base):
    """One short fact, in front of the model on every turn.

    Deliberately not shareable and deliberately small. The length cap is
    enforced in the service rather than by the column, so an over-long write
    from a tool is trimmed with an explanation instead of failing the turn.
    """

    __tablename__ = "memories"

    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str] = mapped_column(String(16), default=AUTHOR_MODEL, nullable=False)

    def __repr__(self) -> str:
        return f"<Memory {self.content[:40]!r}>"
