"""Reports: what was found, written down once and never replied to."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.models.data_group import InDataGroup

# Where a report came from. Not a foreign key to anything -- see `source_id`.
SOURCE_SCHEDULE = "schedule"
SOURCE_CHAT = "chat"
SOURCE_MANUAL = "manual"
SOURCES = (SOURCE_SCHEDULE, SOURCE_CHAT, SOURCE_MANUAL)


class Report(UUIDPrimaryKey, Timestamps, InDataGroup, Base):
    """A finished piece of work, filed.

    Deliberately not a `Chat` with one `Message` in it. A report is read top to
    bottom and never answered, so everything a conversation carries -- a
    composer, a sidebar row, a title that regenerates itself, a bubble with an
    avatar and a rewind button -- would be machinery to suppress rather than
    machinery to use. It is the same line `services/library/` already draws
    between a note and a chat: a durable artefact is not a turn.

    It must also be writable with no chat behind it at all, being the fallback
    destination for a scheduled run whose own chat has gone.

    `body` is Markdown written by a model and goes through
    `services/markdown.py` like everything else from an endpoint. Hard rule 6
    applies here exactly as it does in a transcript.
    """

    __tablename__ = "reports"

    owner_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # One line for the list page, so a feed of forty reports can be read without
    # opening any of them. Written by the model beside the body; falls back to
    # the body's first line when it did not bother.
    summary: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")

    source: Mapped[str] = mapped_column(String(16), default=SOURCE_MANUAL, nullable=False)
    # The chat or the schedule this came out of, kept so a report can say where
    # it was made. Deliberately not a ForeignKey: `migrations.py` compiles the
    # column type only, so a REFERENCES clause would exist on a fresh database
    # and not on an upgraded one -- the same reason `Chat.compacted_through_id`
    # and `Folder.ssh_profile_id` are plain ids. Both are validated on read, and
    # the row outliving what it points at is normal rather than exceptional: a
    # report is worth keeping after the chat that produced it has been deleted.
    source_id: Mapped[str] = mapped_column(String(32), default="")
    schedule_id: Mapped[str] = mapped_column(String(32), default="")
    model_id: Mapped[str] = mapped_column(String(300), default="")

    # NOT NULL with a scalar default so `migrations._add_column_sql` can backfill
    # it if this column is ever added to a table that already has rows.
    unread: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Whether its arrival has already been announced. The dot can be shown for
    # as long as it is unread; the toast and the browser notification must fire
    # once. Without this the poll would announce the same report every ten
    # seconds until somebody opened it, which is the shape of notification
    # nobody leaves switched on. `Chat.unread_notified` exists for exactly this
    # and this is the same pair.
    unread_notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Why a run produced nothing worth reading. A scheduled report that failed
    # is still a report -- one that silently did not appear is indistinguishable
    # from a schedule that never fired.
    error: Mapped[str] = mapped_column(Text, default="")

    def __repr__(self) -> str:
        return f"<Report {self.title!r}>"
