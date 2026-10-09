"""Storing uploaded images.

Only model avatars use this today. Files are written under the data directory
and served back by a dedicated route, never from a URL supplied by a user --
a remote image URL would turn every page render into a request to a third
party, which is both a privacy leak and a way to make the UI depend on someone
else's uptime.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from lembas.config import settings
from lembas.web.i18n import t

log = logging.getLogger(__name__)

# Raster and vector formats a browser will render inline. Deliberately narrow:
# every entry here is something that cannot execute in an <img> tag.
ALLOWED_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

MAX_BYTES = 2 * 1024 * 1024  # 2 MB; these are 64px avatars

# Magic numbers, checked against the declared content type. A browser sniffs
# content, so trusting the client's Content-Type alone would let a file claim
# to be a PNG and be served as something else.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class UploadError(Exception):
    """A rejected upload, with a message fit to show the user."""


def _detect(payload: bytes) -> str | None:
    for signature, media_type in _SIGNATURES:
        if payload.startswith(signature):
            return media_type
    # WEBP is "RIFF" + 4 size bytes + "WEBP".
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def models_dir() -> Path:
    path = settings.uploads_dir / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_model_image(payload: bytes, declared_type: str) -> str:
    """Validate and store a model avatar. Returns the stored filename."""
    if not payload:
        raise UploadError(t("The file was empty."))
    if len(payload) > MAX_BYTES:
        raise UploadError(t("Images must be under %(mb)s MB.", mb=MAX_BYTES // (1024 * 1024)))

    actual = _detect(payload)
    if actual is None:
        raise UploadError(t("That does not look like a PNG, JPEG, WEBP or GIF image."))
    if declared_type and declared_type.split(";")[0].strip() != actual:
        # Not fatal on its own, but worth knowing about.
        log.info("upload declared %s but is actually %s", declared_type, actual)

    # Random name rather than the client's: no path traversal, no collisions,
    # and no leaking whatever the uploader called the file.
    filename = f"{secrets.token_hex(16)}{ALLOWED_TYPES[actual]}"
    (models_dir() / filename).write_bytes(payload)
    return filename


def model_image_path(filename: str) -> Path | None:
    """Resolve a stored filename to a path, refusing anything outside the dir."""
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        return None
    path = (models_dir() / filename).resolve()
    try:
        path.relative_to(models_dir().resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


def delete_model_image(filename: str) -> None:
    path = model_image_path(filename)
    if path is not None:
        path.unlink(missing_ok=True)


def media_type_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    for media_type, extension in ALLOWED_TYPES.items():
        if extension == suffix:
            return media_type
    return "application/octet-stream"
