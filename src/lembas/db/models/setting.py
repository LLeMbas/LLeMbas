"""Instance-wide settings, stored as a key/value table."""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps
from lembas.db.types import JSONDict


class Setting(Timestamps, Base):
    """One row per settings group, value is an arbitrary JSON object.

    A key/value table rather than a wide typed table: admin settings grow with
    every feature (tools, agents, image generation) and adding a column to a
    live SQLite database without migrations is exactly what this avoids.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    def __repr__(self) -> str:
        return f"<Setting {self.key}>"
