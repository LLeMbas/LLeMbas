"""Starting points offered on the new-chat screen."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey


class Suggestion(UUIDPrimaryKey, Timestamps, Base):
    """One card on the empty chat screen.

    Instance-wide rather than per-user: these are what an administrator wants
    people to start with, the same way the instance system prompt is. There is
    no owner_id and therefore nothing for `sharing` to decide.
    """

    __tablename__ = "suggestions"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="")
    # Sent as the first message the moment the card is clicked, so it has to
    # stand on its own -- there is no chance to add anything to it first. The
    # built-ins ask for what they need rather than assuming material.
    prompt: Mapped[str] = mapped_column(Text, default="")

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<Suggestion {self.name}>"
