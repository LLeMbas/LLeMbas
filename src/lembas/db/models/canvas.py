"""A chat's own working surface."""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.models.library import AUTHOR_USER


class ScratchDoc(UUIDPrimaryKey, Timestamps, Base):
    """A text artefact belonging to one chat, written by either side of it.

    The model can write into it, the person can edit it, and either can hand the
    result to the next message as an ordinary attachment. Distinct from a note,
    which is a durable artefact of the reader's that outlives the chat -- this
    is the chat's own record of what it is working on, which is the same line
    `plan_update` is on rather than `notes_edit`.

    A separate table rather than a column on `chats` for one plain reason:
    `select(Chat)` runs for the sidebar on every page load, and SQLAlchemy loads
    every column -- so a Text body would ride along with two hundred sidebar
    rows to answer a question about none of them.

    One per chat. Several would mean a picker, names, deletion and a sweep, and
    would mean the model choosing an id; one means `scratch:<chat_id>` is
    derivable rather than looked up. If several are ever wanted, they are notes.
    """

    __tablename__ = "scratch_docs"

    chat_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("chats.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(300), default="Scratch")
    body: Mapped[str] = mapped_column(Text, default="")
    # Who wrote it last, so the panel can say. Not authorisation: the chat's
    # owner is the only person who can reach it either way.
    author: Mapped[str] = mapped_column(String(16), default=AUTHOR_USER, nullable=False)

    def __repr__(self) -> str:
        return f"<ScratchDoc {self.chat_id}>"
