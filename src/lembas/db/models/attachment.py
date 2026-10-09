"""Files attached to chat messages."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey

# What the file is for, decided at upload time. Drives both how it is rendered
# and how it reaches the model: images become multimodal parts, everything else
# becomes text in the prompt.
KIND_IMAGE = "image"
KIND_DOCUMENT = "document"  # PDF: text is extracted
KIND_TEXT = "text"  # plain text, markdown, csv, source code


class Attachment(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "attachments"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    # Null while the file is uploaded but the message has not been sent yet.
    # Those orphans are swept periodically -- see services.files.sweep_orphans.
    message_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )

    # What the uploader called it. Display only, never used as a path.
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    # Random name on disk. See services.files for why the two are separate.
    stored_name: Mapped[str] = mapped_column(String(120), nullable=False)

    media_type: Mapped[str] = mapped_column(String(100), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default=KIND_DOCUMENT, nullable=False)

    # Images only, after downscaling.
    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Documents and text: the content that actually reaches the model. Held in
    # the database rather than re-extracted per request -- extraction is slow,
    # and a reply must not silently change because a PDF parser was upgraded.
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    pages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Non-empty when the file was stored but its text could not be read, e.g. a
    # scanned PDF with no text layer. Shown next to the attachment so the user
    # is not left wondering why the model ignored it.
    extraction_error: Mapped[str] = mapped_column(Text, default="")

    # Where this came from, when it came from somewhere with an address.
    #
    # `filename` is a display name and is frequently just the basename, which
    # is not enough: a model told it has been given `main.py` cannot tell which
    # of four it is looking at, and cannot name the file back to you if you ask
    # it to change something. So a project file carries its absolute path and
    # the machine it was read from, and both go into the tag the model sees.
    #
    # Nullable, and empty for an ordinary upload -- a file dragged in from a
    # laptop has no address this instance could meaningfully report.
    source_path: Mapped[str] = mapped_column(String(1000), default="")
    source_label: Mapped[str] = mapped_column(String(200), default="")

    message: Mapped[Message] = relationship(back_populates="attachments")  # noqa: F821

    @property
    def is_image(self) -> bool:
        return self.kind == KIND_IMAGE

    @property
    def human_size(self) -> str:
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB"):
            if size < 1024 or unit == "MB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} MB"

    def __repr__(self) -> str:
        return f"<Attachment {self.filename} {self.kind}>"
