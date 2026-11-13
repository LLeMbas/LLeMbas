"""Reports: what a schedule's run found, written down for its owner.

Harness spec 3.8.0 (`reports.md`): a run does not answer into a conversation.
It files a report with `report_write`, or it does not, and a run without one
leaves a line in its schedule's history.
"""

from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey

IMPORTANCE_NORMAL = "normal"
IMPORTANCE_HIGH = "high"
IMPORTANCES = (IMPORTANCE_NORMAL, IMPORTANCE_HIGH)


class Report(UUIDPrimaryKey, Timestamps, Base):
    """One run's report.

    Deliberately not a chat with one message in it: a report is read top to
    bottom, listed with the others, and is unread or not. Replying to one
    *continues* it as a conversation (`chat_id`), which stays with the report
    and never joins the list of chats.

    Every reference is a plain id, not a foreign key: `db/migrations.py` adds
    a column by its type alone, so a REFERENCES clause would exist on a fresh
    database and not on an upgraded one. Each is validated on read, and a
    report outliving what it points at is normal: it is worth keeping after
    its schedule is deleted.

    `body` is Markdown a model wrote and goes through `services/markdown.py`
    like everything else from an endpoint.
    """

    __tablename__ = "reports"

    owner_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    # One or two sentences for the list and the notification.
    summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    importance: Mapped[str] = mapped_column(String(16), nullable=False, default=IMPORTANCE_NORMAL)
    schedule_id: Mapped[str] = mapped_column(String(32), nullable=False, default="", index=True)
    # The schedule's title when the report was filed: what the list says after
    # the schedule is gone.
    schedule_title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    run_id: Mapped[str] = mapped_column(String(32), nullable=False, default="", index=True)
    model_id: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    unread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Whether its arrival was announced: the dot stays while it is unread, the
    # notification goes once.
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Whether pages were offered to the run that wrote it: only then is an
    # `html-view` block in its body a page.
    html_pages: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Said once to an open page (the unread poll's toast): the page's own
    # flag, as `notified` is the push's.
    page_told: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The chat this report continues in; NULL until somebody replies.
    chat_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    def __repr__(self) -> str:
        return f"<Report {self.title!r}>"
