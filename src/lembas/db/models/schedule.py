"""Schedules: what should happen later, and where its result goes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.models.data_group import InDataGroup
from lembas.db.types import JSONDict

# Where a firing's result is delivered. Chosen per schedule rather than fixed by
# the screen it was made on: Reports has to stay reachable from anywhere, being
# the fallback, and a schedule somebody wants moved from its own chat to Reports
# should not have to be built again.
TARGET_CHAT = "chat"
TARGET_REPORT = "report"
TARGETS = (TARGET_CHAT, TARGET_REPORT)

# Who made it. Kept because "why is this running?" is a question with two very
# different answers, and one of them is "a model decided to".
ORIGIN_USER = "user"
ORIGIN_MODEL = "model"
ORIGINS = (ORIGIN_USER, ORIGIN_MODEL)


class Schedule(UUIDPrimaryKey, Timestamps, InDataGroup, Base):
    """One standing instruction and when it comes due.

    The row carries no recurrence logic at all: `rule_json` is read by
    `services/schedule/rule.py`, which is pure and knows nothing about rows.
    What lives here is the bookkeeping the ticker needs to claim a firing
    without doing it twice.
    """

    __tablename__ = "schedules"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")

    # What the reader actually typed, kept verbatim and for ever. The compile
    # rewrites it into `instruction`, and "what did I actually ask for" has to
    # survive that -- both so the edit form can show it and so a recompile has
    # something to work from other than its own previous output.
    request: Mapped[str] = mapped_column(Text, default="")
    # What is sent when it fires. The compiled form: standalone, since it is
    # read with no conversation around it.
    instruction: Mapped[str] = mapped_column(Text, default="")

    rule_json: Mapped[dict] = mapped_column(JSONDict, default=dict)

    target: Mapped[str] = mapped_column(String(16), default=TARGET_CHAT, nullable=False)
    # The chat this fires into. Deliberately not a ForeignKey -- `migrations.py`
    # compiles the column type only, so a REFERENCES clause would exist on a
    # fresh database and not on an upgraded one. Validated on read, and a
    # dangling value disables the schedule rather than raising every tick.
    chat_id: Mapped[str] = mapped_column(String(32), default="")
    model_id: Mapped[str] = mapped_column(String(300), default="")

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # The ticker's entire query. Nullable because "nothing more to do" is a real
    # state -- a spent count, a closed window, a calendar matching nothing --
    # and is different from "due at the epoch".
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Stamped when a firing starts and cleared when it finishes, so a run that
    # died halfway says so instead of looking like one that never happened.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    fired_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Why the last run did not work. Shown on the schedule's own page: a
    # schedule that silently stopped producing anything is indistinguishable
    # from one that was never due.
    last_error: Mapped[str] = mapped_column(Text, default="")

    origin: Mapped[str] = mapped_column(String(16), default=ORIGIN_USER, nullable=False)
    compiled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<Schedule {self.title!r} {'on' if self.enabled else 'off'}>"
