"""A long generation -- a video, a track, a 3D model -- as a job.

A row rather than a task in memory, for the reason `ChatJob` gives: the thing
a job exists for is to outlive the reply that started it, and the process
that was told about it. **The queue is the database**: what is still to be
followed is every row whose state is not final, read again at each start
(`services/media/jobs.py`).

Its states, in order:

- `queued` -- the row exists; the far side has not confirmed anything yet.
- `running` -- the far side has it: `provider_ref` is the ComfyUI prompt id
  or the provider's task id, which is what a restart re-attaches by.
- `done` -- the file is in the person's Files (`result_json`), counted and
  priced. `failed` -- with `error`, in words. `cancelled` -- by the person.
  `expired` -- the deadline passed, or the far side forgot the task.

`notified_at` is when the chat was told (woken); a job that ended while
nobody could be told keeps its result here and in Files regardless.

Additive: a new table, plain ids instead of foreign keys, like every other
reference written since the first release.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.types import JSONDict

MEDIA_QUEUED = "queued"
MEDIA_RUNNING = "running"
MEDIA_DONE = "done"
MEDIA_FAILED = "failed"
MEDIA_CANCELLED = "cancelled"
MEDIA_EXPIRED = "expired"
MEDIA_ACTIVE = (MEDIA_QUEUED, MEDIA_RUNNING)
MEDIA_FINAL = (MEDIA_DONE, MEDIA_FAILED, MEDIA_CANCELLED, MEDIA_EXPIRED)
MEDIA_STATES = MEDIA_ACTIVE + MEDIA_FINAL


class MediaJob(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "media_jobs"

    user_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    # The chat that asked, and the reply whose tool call it was. Empty for a
    # job started outside a chat.
    chat_id: Mapped[str | None] = mapped_column(String(32), index=True)
    message_id: Mapped[str | None] = mapped_column(String(32))
    # What is being made: `video`, `music`, `model3d` (a capability name).
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), default=MEDIA_QUEUED, index=True,
                                       nullable=False)
    # Where it runs, as a snapshot of the source that was resolved when it
    # started: the connection, how it is spoken to, the model (or workflow).
    # Never a key: that is read from the connection each time it is needed.
    connection_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    dialect: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    label: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    # The far side's own name for it.
    provider_ref: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    # What was asked (`prompt`, `seconds`, ...), for the card and the note.
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    # The stored file, as the typed result a tool event carries (`kind`,
    # `path`, `node_id`, `mime`, `size`, and what is known of its shape).
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Micro-units of money per unit (a second of video, a track, a model) and
    # the currency, as resolved at the start; and what the finished job was
    # counted as.
    price_micro: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(8))
    cost_micro: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    usage_id: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<MediaJob {self.id} {self.kind} {self.state}>"
