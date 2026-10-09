"""Storing and reading uploaded attachments.

Three kinds of file, each handled differently on the way to the model:

* **Images** are downscaled and re-encoded, then sent as multimodal content
  parts. Downscaling is not cosmetic -- a phone photo is several megabytes of
  base64, which is both slow and a large slice of the context window.
* **PDFs** have their text extracted once, at upload. Extraction is slow and a
  reply must not silently change because a parser was upgraded later.
* **Plain text** (including source code and CSV) is decoded and stored as-is.

Everything an uploader supplies is treated as hostile: the type is decided by
inspecting the bytes rather than trusting the browser, the name on disk is
random, and both image dimensions and PDF page counts are capped so a small
file cannot expand into an enormous amount of work.
"""

from __future__ import annotations

import io
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.config import settings
from lembas.db.models import KIND_DOCUMENT, KIND_IMAGE, KIND_TEXT, Attachment, Message
from lembas.web.i18n import t

log = logging.getLogger(__name__)

# --- Limits ------------------------------------------------------------------
# These are the *defaults*, and an administrator can move every one of them on
# /admin/extraction. They stay here because a default belongs beside the code
# that depends on it, and because `prepare` is called from places with no
# database session at all.
#
# The values are read through `limits()`, a process-level snapshot with the same
# shape and the same reasoning as `services/branding.py`: one query per process,
# dropped when the page saves. Threading a session through `prepare`,
# `_process_image`, `_process_pdf` and `_process_text` would have meant six
# signatures changed to carry a number.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Longest edge after downscaling. Large enough for a model to read a screenshot
# or a page of text, small enough that the base64 stays reasonable.
MAX_IMAGE_EDGE = 1400
JPEG_QUALITY = 85

# Pillow's own guard against decompression bombs: a 60,000x60,000 PNG is a few
# KB on disk and hundreds of GB decoded.
#
# Deliberately NOT a setting. It is a guard, not a preference, and nothing good
# comes of being able to raise it from a form.
Image.MAX_IMAGE_PIXELS = 64_000_000

MAX_PDF_PAGES = 300
# Characters of extracted text kept per document. Roughly 30k tokens, which is
# already a large slice of most context windows; more is rarely useful and
# frequently breaks the request outright.
MAX_EXTRACTED_CHARS = 120_000

# Orphans are files uploaded into a composer that was never sent.
ORPHAN_AGE = timedelta(hours=24)

IMAGE_TYPES: dict[bytes, tuple[str, str]] = {
    b"\x89PNG\r\n\x1a\n": ("image/png", ".png"),
    b"\xff\xd8\xff": ("image/jpeg", ".jpg"),
    b"GIF87a": ("image/gif", ".gif"),
    b"GIF89a": ("image/gif", ".gif"),
}

# Extensions treated as text when the bytes decode cleanly as UTF-8. The list
# exists only to pick a sensible media type; decodability is what actually
# decides, so an unlisted extension still works.
TEXT_EXTENSIONS = {
    ".txt": "text/plain", ".md": "text/markdown", ".markdown": "text/markdown",
    ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".json": "application/json", ".yaml": "text/yaml", ".yml": "text/yaml",
    ".toml": "text/toml", ".ini": "text/plain", ".cfg": "text/plain",
    ".xml": "text/xml", ".html": "text/plain", ".css": "text/plain",
    ".py": "text/x-python", ".js": "text/javascript", ".ts": "text/typescript",
    ".rs": "text/x-rust", ".go": "text/x-go", ".c": "text/x-c", ".h": "text/x-c",
    ".cpp": "text/x-c++", ".java": "text/x-java", ".rb": "text/x-ruby",
    ".sh": "text/x-shellscript", ".sql": "text/x-sql", ".log": "text/plain",
}


@dataclass(frozen=True)
class Limits:
    """What extraction is allowed to spend, for one process.

    A snapshot rather than a lookup per call: `prepare` and everything under it
    are called from routes, from tool runners and from the startup sweep, and
    several of them have no session in hand. The pattern and the cost are the
    same as `services/branding.py` -- one query per process, dropped when the
    admin page saves, and stale across workers until each next reads.
    """

    max_upload_bytes: int = MAX_UPLOAD_BYTES
    max_image_edge: int = MAX_IMAGE_EDGE
    jpeg_quality: int = JPEG_QUALITY
    max_pdf_pages: int = MAX_PDF_PAGES
    max_extracted_chars: int = MAX_EXTRACTED_CHARS
    orphan_hours: int = 24
    extra_text_extensions: tuple[str, ...] = ()
    reject_unreadable_pdf: bool = False

    def media_type_for(self, extension: str) -> str | None:
        """The media type for a text extension, or None if it is not one.

        The built-in table first, then the administrator's additions as plain
        text. Additions are extensions and not a mapping, because the mapping is
        a thing somebody would have to get right twice and the media type of a
        `.env` is `text/plain` whatever anybody types.
        """
        if extension in TEXT_EXTENSIONS:
            return TEXT_EXTENSIONS[extension]
        return "text/plain" if extension in self.extra_text_extensions else None


_LIMITS: Limits | None = None


def limits() -> Limits:
    """The current extraction limits. Never raises -- see `branding.snapshot`."""
    global _LIMITS
    if _LIMITS is not None:
        return _LIMITS
    try:
        from lembas.db.session import session_scope
        from lembas.services import settings_store

        with session_scope() as db:
            values = settings_store.extraction(db)
        _LIMITS = Limits(
            max_upload_bytes=int(values["max_upload_mb"]) * 1024 * 1024,
            max_image_edge=int(values["max_image_edge"]),
            jpeg_quality=int(values["jpeg_quality"]),
            max_pdf_pages=int(values["max_pdf_pages"]),
            max_extracted_chars=int(values["max_extracted_chars"]),
            orphan_hours=int(values["orphan_hours"]),
            extra_text_extensions=tuple(
                _clean_extension(item) for item in values["extra_text_extensions"]
            ),
            reject_unreadable_pdf=bool(values.get("reject_unreadable_pdf")),
        )
    except Exception:  # noqa: BLE001 - the shipped defaults are a usable answer
        log.debug("could not read extraction settings; using defaults", exc_info=True)
        return Limits()
    return _LIMITS


def _clean_extension(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return ""
    return value if value.startswith(".") else f".{value}"


def forget() -> None:
    """Drop the snapshot. Called by the admin page's save, and by tests."""
    global _LIMITS
    _LIMITS = None


class FileError(Exception):
    """A rejected upload, with a message fit to show the user."""


@dataclass
class Prepared:
    """The result of inspecting and processing an upload, before it is stored."""

    payload: bytes
    kind: str
    media_type: str
    extension: str
    width: int = 0
    height: int = 0
    extracted_text: str = ""
    pages: int = 0
    truncated: bool = False
    extraction_error: str = ""


# --- Storage -----------------------------------------------------------------
def attachments_dir() -> Path:
    path = settings.uploads_dir / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stored_path(stored_name: str) -> Path | None:
    """Resolve a stored name to a path, refusing anything outside the directory."""
    if not stored_name or "/" in stored_name or "\\" in stored_name or stored_name.startswith("."):
        return None
    base = attachments_dir().resolve()
    path = (base / stored_name).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path if path.is_file() else None


# --- Type detection ----------------------------------------------------------
def _detect_image(payload: bytes) -> tuple[str, str] | None:
    for signature, (media_type, extension) in IMAGE_TYPES.items():
        if payload.startswith(signature):
            return media_type, extension
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


def _looks_like_pdf(payload: bytes) -> bool:
    # The header is allowed a little leading junk by the spec, and real files
    # in the wild use it.
    return b"%PDF-" in payload[:1024]


# --- Processing --------------------------------------------------------------
def _process_image(payload: bytes) -> Prepared:
    bounds = limits()
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            has_alpha = image.mode in ("RGBA", "LA", "P") and "transparency" in image.info
            # Animation is lost on re-encode; keeping only the first frame is
            # honest and is what a model would see anyway.
            frame = image.convert("RGBA" if has_alpha else "RGB")

            width, height = frame.size
            longest = max(width, height)
            if longest > bounds.max_image_edge:
                scale = bounds.max_image_edge / longest
                frame = frame.resize(
                    (max(1, int(width * scale)), max(1, int(height * scale))),
                    Image.LANCZOS,
                )

            buffer = io.BytesIO()
            if has_alpha:
                frame.save(buffer, format="PNG", optimize=True)
                media_type, extension = "image/png", ".png"
            else:
                frame.save(buffer, format="JPEG", quality=bounds.jpeg_quality, optimize=True)
                media_type, extension = "image/jpeg", ".jpg"

            return Prepared(
                payload=buffer.getvalue(),
                kind=KIND_IMAGE,
                media_type=media_type,
                extension=extension,
                width=frame.width,
                height=frame.height,
            )
    except Image.DecompressionBombError as exc:
        raise FileError(t("That image's dimensions are implausibly large.")) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise FileError(t("That image could not be read. Is it corrupt?")) from exc


def _process_pdf(payload: bytes) -> Prepared:
    bounds = limits()
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    prepared = Prepared(
        payload=payload, kind=KIND_DOCUMENT, media_type="application/pdf", extension=".pdf"
    )

    try:
        reader = PdfReader(io.BytesIO(payload))
        if reader.is_encrypted:
            # An empty password unlocks a surprising number of "encrypted" PDFs.
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 - any failure means the same thing
                prepared.extraction_error = (
                    "This PDF is password-protected, so its text could not be read."
                )
                return prepared

        prepared.pages = len(reader.pages)
        chunks: list[str] = []
        total = 0

        for index, page in enumerate(reader.pages[:bounds.max_pdf_pages]):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001 - one bad page is not fatal
                log.debug("page %d of a PDF failed to extract: %s", index, exc)
                continue
            if not text.strip():
                continue
            chunks.append(f"[page {index + 1}]\n{text.strip()}")
            total += len(text)
            if total >= bounds.max_extracted_chars:
                prepared.truncated = True
                break

        if prepared.pages > bounds.max_pdf_pages:
            prepared.truncated = True

        prepared.extracted_text = "\n\n".join(chunks)[:bounds.max_extracted_chars]

        if not prepared.extracted_text.strip():
            # Almost always a scan. Saying so beats the model silently ignoring
            # a document the user believes it can read.
            prepared.extraction_error = (
                "No text could be extracted. This looks like a scanned PDF; "
                "LLeMbas does not do OCR yet."
            )

    except PdfReadError as exc:
        prepared.extraction_error = "This file is not a readable PDF."
        log.info("unreadable PDF: %s", exc)
    except Exception as exc:  # noqa: BLE001 - never let a bad file 500 the upload
        prepared.extraction_error = "This PDF could not be read."
        log.warning("unexpected PDF failure: %s", exc)

    return prepared


def _process_text(payload: bytes, filename: str) -> Prepared:
    bounds = limits()
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            text = payload.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise FileError(t("That file is not text, and is not a format LLeMbas can read."))

    # Null bytes mean this decoded by luck (latin-1 decodes any byte) and is
    # really a binary file.
    if "\x00" in text[:4096]:
        raise FileError(t("That file is not text, and is not a format LLeMbas can read."))

    truncated = len(text) > bounds.max_extracted_chars
    extension = Path(filename).suffix.lower()

    return Prepared(
        payload=payload,
        kind=KIND_TEXT,
        media_type=bounds.media_type_for(extension) or "text/plain",
        extension=extension if bounds.media_type_for(extension) else ".txt",
        extracted_text=text[:bounds.max_extracted_chars],
        truncated=truncated,
    )


def _keep_image(payload: bytes) -> Prepared:
    """An image stored as it arrived, measured but not re-encoded.

    `_process_image` exists to protect the window from a phone camera: eight
    megapixels of JPEG become 1400px of JPEG at quality 85, and for something
    somebody photographed that is all upside. For an image *this application
    asked a diffusion model to make*, at a size somebody chose, it is a visible
    loss on the one output the feature exists to produce -- soft detail and
    ringing on exactly the fine texture the prompt was about.

    Still opened by Pillow, so a malformed file is still refused and the
    dimensions are still real rather than claimed; still bounded by
    `MAX_UPLOAD_BYTES` in `prepare`. What is skipped is only the resize and the
    transcode.
    """
    detected = _detect_image(payload)
    if detected is None:
        raise FileError(t("That is not an image."))
    media_type, extension = detected
    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            width, height = image.size
    except Image.DecompressionBombError as exc:
        raise FileError(t("That image's dimensions are implausibly large.")) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise FileError(t("That image could not be read. Is it corrupt?")) from exc

    return Prepared(
        payload=payload,
        kind=KIND_IMAGE,
        media_type=media_type,
        extension=extension,
        width=width,
        height=height,
    )


def prepare(payload: bytes, filename: str, *, keep_original: bool = False) -> Prepared:
    """Inspect an upload, decide what it is, and process it accordingly.

    `keep_original` is for an image the application produced rather than one
    somebody sent: see `_keep_image`. It applies to images only -- there is no
    argument for keeping an unparsed PDF, and the text path stores its bytes
    verbatim already.
    """
    if not payload:
        raise FileError(t("That file is empty."))
    ceiling = limits().max_upload_bytes
    if len(payload) > ceiling:
        raise FileError(t("Files must be under %(mb)s MB.", mb=ceiling // (1024 * 1024)))

    if _detect_image(payload) is not None:
        return _keep_image(payload) if keep_original else _process_image(payload)
    if _looks_like_pdf(payload):
        return _process_pdf(payload)
    return _process_text(payload, filename)


# --- Public API --------------------------------------------------------------
def safe_display_name(filename: str) -> str:
    """A filename fit to show. Never used as a path; the stored name is random."""
    cleaned = Path(filename or "file").name.strip() or "file"
    return cleaned[:300]


def store(
    db: DBSession,
    *,
    user_id: str,
    chat_id: str | None,
    payload: bytes,
    filename: str,
    keep_original: bool = False,
    source_path: str = "",
    source_label: str = "",
    message_id: str | None = None,
) -> Attachment:
    """Process and persist an upload. Raises FileError if it is unusable.

    `message_id` is normally left null -- an upload is bound to a turn by
    `claim()` when the message is sent. A generated image is the mirror image of
    that: it exists *because* a reply is being written, so it says which turn it
    belongs to at the moment it is made.
    """
    prepared = prepare(payload, filename, keep_original=keep_original)

    stored_name = f"{secrets.token_hex(16)}{prepared.extension}"
    (attachments_dir() / stored_name).write_bytes(prepared.payload)

    attachment = Attachment(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        filename=safe_display_name(filename),
        stored_name=stored_name,
        media_type=prepared.media_type,
        size_bytes=len(prepared.payload),
        kind=prepared.kind,
        width=prepared.width,
        height=prepared.height,
        extracted_text=prepared.extracted_text,
        pages=prepared.pages,
        truncated=prepared.truncated,
        extraction_error=prepared.extraction_error,
        source_path=source_path[:1000],
        source_label=source_label[:200],
    )
    db.add(attachment)
    db.commit()
    log.info(
        "stored %s (%s, %d bytes) for user %s",
        attachment.filename,
        attachment.kind,
        attachment.size_bytes,
        user_id,
    )
    return attachment


def store_text(
    db: DBSession,
    *,
    user_id: str,
    chat_id: str | None,
    filename: str,
    text: str,
    truncated: bool = False,
    source_note: str = "",
    source_path: str = "",
    source_label: str = "",
) -> Attachment:
    """Attach text that did not arrive as a file -- a fetched web page.

    Written to disk like any other attachment so it can be downloaded and so
    there is one cleanup path, rather than a second kind of attachment that
    exists only in the database.

    `source_note` leads the *text*; `source_path` and `source_label` are
    columns. The two are not the same thing and both are wanted: the note is
    prose a model reads inside the document, and the columns become attributes
    on the tag around it, which is what a reader sees on the chip and what
    survives if the text is later truncated away from its own first line.
    """
    body = text[:limits().max_extracted_chars]
    payload = body.encode("utf-8")

    stored_name = f"{secrets.token_hex(16)}.txt"
    (attachments_dir() / stored_name).write_bytes(payload)

    attachment = Attachment(
        user_id=user_id,
        chat_id=chat_id,
        filename=safe_display_name(filename),
        stored_name=stored_name,
        media_type="text/plain",
        size_bytes=len(payload),
        kind=KIND_TEXT,
        # The URL leads the text so the model can cite it, and so the reader
        # can see where an attachment called "Some Page.txt" came from.
        extracted_text=f"Source: {source_note}\n\n{body}" if source_note else body,
        truncated=truncated,
        source_path=source_path[:1000],
        source_label=source_label[:200],
    )
    db.add(attachment)
    db.commit()
    return attachment


def copy_attachment(
    db: DBSession, *, user_id: str, chat_id: str | None, attachment: Attachment
) -> Attachment:
    """Duplicate something already sent, so it can ride along with a new message.

    A copy and not a second reference to one row: an attachment belongs to the
    message it was sent with, and sharing one between two would make deleting
    either of them a question rather than an answer.
    """
    stored_name = ""
    source = attachments_dir() / attachment.stored_name if attachment.stored_name else None
    if source is not None and source.exists():
        stored_name = f"{secrets.token_hex(16)}{Path(source.name).suffix}"
        (attachments_dir() / stored_name).write_bytes(source.read_bytes())

    copy = Attachment(
        user_id=user_id,
        chat_id=chat_id,
        filename=attachment.filename,
        stored_name=stored_name,
        media_type=attachment.media_type,
        size_bytes=attachment.size_bytes,
        kind=attachment.kind,
        width=attachment.width,
        height=attachment.height,
        extracted_text=attachment.extracted_text,
        pages=attachment.pages,
        truncated=attachment.truncated,
        extraction_error=attachment.extraction_error,
        source_path=attachment.source_path,
        source_label=attachment.source_label,
    )
    db.add(copy)
    db.commit()
    return copy


def copy_document(
    db: DBSession, *, user_id: str, chat_id: str | None, document
) -> Attachment:
    """Copy a library document into a message being composed.

    A copy rather than a reference. History must not change under a conversation
    because a document was edited or deleted afterwards -- the same reason text
    is extracted once at upload instead of per request. The bytes are duplicated
    too, so deleting the document cannot leave a message pointing at nothing.
    """
    from lembas.services.library import documents as documents_service

    stored_name = ""
    source = documents_service.stored_path(document.stored_name)
    if source is not None:
        stored_name = f"{secrets.token_hex(16)}{Path(source.name).suffix}"
        (attachments_dir() / stored_name).write_bytes(source.read_bytes())

    attachment = Attachment(
        user_id=user_id,
        chat_id=chat_id,
        filename=document.filename or f"{document.title}.txt",
        stored_name=stored_name,
        media_type=document.media_type,
        size_bytes=document.size_bytes,
        kind=document.kind,
        width=document.width,
        height=document.height,
        extracted_text=document.extracted_text,
        pages=document.pages,
        truncated=document.truncated,
        extraction_error=document.extraction_error,
        # Where it came from, for the same reason a project file carries it: a
        # model handed four documents cannot tell which is which, and cannot
        # name one back when asked to work on it. This was the one attach path
        # that dropped provenance.
        source_path=(document.title or "")[:1000],
        source_label=(document.base.name if document.base else "Knowledge")[:200],
    )
    db.add(attachment)
    db.commit()
    return attachment


def delete(db: DBSession, attachment: Attachment) -> None:
    path = stored_path(attachment.stored_name)
    if path is not None:
        path.unlink(missing_ok=True)
    db.delete(attachment)
    db.commit()


def claim(db: DBSession, *, ids: list[str], user_id: str, message_id: str) -> list[Attachment]:
    """Bind pending uploads to the message that was just sent.

    Only unclaimed attachments belonging to this user are taken, so a stray or
    forged id cannot pull someone else's file into a conversation.

    **`chat_id` is set here, and it was not.** `POST /api/files` takes one, and
    the composer sends it -- but only once a chat exists. A file picked on the
    *new-chat* screen is stored before there is a chat to name, so its
    `chat_id` stayed NULL for the rest of its life even after the message it
    belongs to was sent. Six places filter on that column, and every one of them
    was quietly wrong about those files: the harness did not name them among the
    attached documents, the canvas refused to open them, and
    `remove_files_for_chats` could not find them to delete -- so the temporary
    sweep, the one caller it had, was removing nothing.

    Read from the message rather than passed in, so no caller can bind an
    attachment to one chat and a message in another.
    """
    if not ids:
        return []

    pending = list(
        db.scalars(
            select(Attachment).where(
                Attachment.id.in_(ids),
                Attachment.user_id == user_id,
                Attachment.message_id.is_(None),
            )
        )
    )
    message = db.get(Message, message_id)
    for attachment in pending:
        attachment.message_id = message_id
        if message is not None:
            attachment.chat_id = message.chat_id
    db.commit()
    return pending


def remove_files_for_chats(db: DBSession, chat_ids: list[str]) -> int:
    """Unlink the files belonging to these chats' attachments.

    Deleting a Chat cascades to its Message and Attachment *rows* but leaves the
    files on disk -- only `sweep_orphans` unlinks anything, and it only looks at
    uploads that were never attached. Anything that deletes chats has to call
    this first, while the rows still say which files to remove.
    """
    if not chat_ids:
        return 0

    removed = 0
    for attachment in db.scalars(select(Attachment).where(Attachment.chat_id.in_(chat_ids))):
        path = stored_path(attachment.stored_name)
        if path is not None and path.exists():
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def sweep_orphans(db: DBSession, older_than: timedelta | None = None) -> int:
    """Delete uploads that were never attached to a message.

    A file picked in the composer and then abandoned would otherwise sit on
    disk forever.

    `older_than` defaults to the configured age rather than to a constant, and
    it is resolved *here* rather than in the signature: a default argument is
    evaluated at import, so a module-level `ORPHAN_AGE` in the signature would
    pin the shipped 24 hours whatever an administrator later set.
    """
    if older_than is None:
        older_than = timedelta(hours=limits().orphan_hours)
    cutoff = datetime.now(UTC) - older_than
    orphans = list(db.scalars(select(Attachment).where(Attachment.message_id.is_(None))))

    removed = 0
    for attachment in orphans:
        created = attachment.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if created >= cutoff:
            continue
        path = stored_path(attachment.stored_name)
        if path is not None:
            path.unlink(missing_ok=True)
        db.delete(attachment)
        removed += 1

    if removed:
        db.commit()
        log.info("swept %d orphaned upload(s)", removed)
    return removed


def data_uri(attachment: Attachment) -> str | None:
    """Base64 data URI for an image, as sent to a vision model.

    A data URI rather than a link back to this server: a local endpoint has no
    route to LLeMbas, and a hosted one has no credentials for it.
    """
    import base64

    path = stored_path(attachment.stored_name)
    if path is None:
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{attachment.media_type};base64,{encoded}"


def preview_data_uri(payload: bytes, *, max_edge: int = 0) -> str | None:
    """The same thing for bytes in hand, downscaled, for a model to look at.

    Fidelity and weight are two different jobs. What is stored is what ComfyUI
    produced, because that is the artefact somebody keeps; what is *shown to a
    model to be judged* wants to be small, because a 400KB PNG is 550KB of
    base64 in a request that exists only to answer one question.

    Takes bytes rather than an Attachment: the reviewer looks at an image that
    may be about to be thrown away, and writing a row for something rejected
    seconds later is work with nothing to show for it.

    `max_edge` of 0 means the configured one. Zero rather than None because the
    caller that passes a number passes a number, and a sentinel that is also a
    plausible value would be worse -- an edge of zero is not a picture.
    """
    import base64

    max_edge = max_edge or limits().max_image_edge

    try:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
            frame = image.convert("RGB")
            longest = max(frame.size)
            if longest > max_edge:
                scale = max_edge / longest
                frame = frame.resize(
                    (max(1, int(frame.width * scale)), max(1, int(frame.height * scale))),
                    Image.LANCZOS,
                )
            buffer = io.BytesIO()
            frame.save(buffer, format="JPEG", quality=limits().jpeg_quality, optimize=True)
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        log.warning("could not build a preview of a generated image", exc_info=True)
        return None

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
