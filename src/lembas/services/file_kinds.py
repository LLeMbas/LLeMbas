"""What a file is: its kind, decided in one place from its name and its bytes.

Every answer that says what a file is -- a listing, a file's content, the
header a download goes out under -- asks `detect`. The type a browser or an
app claimed at upload is never asked: it is somebody's word.

**The bytes outrank the name.** A format with a signature (a picture, a PDF,
sound, video, an archive, a binary glTF) is what its first bytes say it is,
whatever it is called; a name that promises such a format over bytes that are
not it gets nothing for the promise -- plain `text` when the bytes are text
(drawn escaped, like any text), `binary` otherwise. A kind that is text
(Markdown, code, HTML, SVG, a table, a notebook, a text model) comes from the
extension, and holds only while the bytes are text.

| Kind | What |
|---|---|
| `markdown` `code` `text` `html` | text, as the viewer and the editor draw it |
| `image` | png, jpeg, gif, webp, avif -- raster, by signature |
| `svg` | its own kind: a document that can carry script, never an image to serve inline |
| `pdf` `audio` `video` | by signature |
| `table` | csv, tsv |
| `notebook` | ipynb |
| `model3d` | glb (signature), gltf, obj, stl |
| `archive` | zip, tar, gz (and bz2, xz, 7z) |
| `binary` | everything else |

`INLINE` names the kinds whose bytes may be answered with their own type and
without `attachment`: formats a browser decodes and never runs. Everything
else is a download, or drawn by a viewer from its text.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass

# How much of a file is looked at. A tar's signature sits at byte 257.
HEAD_BYTES = 512

MARKDOWN = "markdown"
CODE = "code"
TEXT = "text"
HTML = "html"
IMAGE = "image"
SVG = "svg"
PDF = "pdf"
AUDIO = "audio"
VIDEO = "video"
TABLE = "table"
NOTEBOOK = "notebook"
MODEL3D = "model3d"
ARCHIVE = "archive"
BINARY = "binary"

KINDS = (
    MARKDOWN, CODE, TEXT, HTML, IMAGE, SVG, PDF, AUDIO, VIDEO, TABLE, NOTEBOOK, MODEL3D,
    ARCHIVE, BINARY,
)
# Kinds that are text when their bytes are: what an editor may open.
TEXT_KINDS = (MARKDOWN, CODE, TEXT, HTML, SVG, TABLE, NOTEBOOK)
# Kinds a browser decodes and never runs: may be served with their own type.
INLINE = (IMAGE, AUDIO, VIDEO)


@dataclass(frozen=True)
class Kind:
    kind: str
    mime: str
    language: str = ""

    @property
    def inline(self) -> bool:
        return self.kind in INLINE

    @property
    def is_text(self) -> bool:
        return self.kind in TEXT_KINDS


def _ext(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    return ext.lower() if dot and stem else ""


def looks_like_text(head: bytes) -> bool:
    """UTF-8 without a NUL -- a head cut in the middle of a character is fine."""
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as problem:
        return problem.start >= len(head) - 3 and "unexpected end" in problem.reason
    return True


# ISO base media (`....ftyp<brand>`): which brands are sound, which a picture.
_AUDIO_BRANDS = (b"M4A ", b"M4B ", b"M4P ")
_AVIF_BRANDS = (b"avif", b"avis")
_ZIP_EXTENSIONS = ("zip",)


def _signature(head: bytes, ext: str) -> Kind | None:
    """The kind a signature gives, or None when the bytes carry none we know."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Kind(IMAGE, "image/png")
    if head.startswith(b"\xff\xd8\xff"):
        return Kind(IMAGE, "image/jpeg")
    if head.startswith((b"GIF87a", b"GIF89a")):
        return Kind(IMAGE, "image/gif")
    if head[:4] == b"RIFF" and len(head) >= 12:
        form = head[8:12]
        if form == b"WEBP":
            return Kind(IMAGE, "image/webp")
        if form == b"WAVE":
            return Kind(AUDIO, "audio/wav")
        if form == b"AVI ":
            return Kind(VIDEO, "video/x-msvideo")
        return Kind(BINARY, "application/octet-stream")
    if head[4:8] == b"ftyp" and len(head) >= 12:
        brand = head[8:12]
        if brand in _AVIF_BRANDS:
            return Kind(IMAGE, "image/avif")
        if brand in _AUDIO_BRANDS:
            return Kind(AUDIO, "audio/mp4")
        if brand.startswith(b"qt"):
            return Kind(VIDEO, "video/quicktime")
        if brand in (b"heic", b"heix", b"mif1", b"msf1"):
            # A picture no browser draws: a download.
            return Kind(BINARY, "image/heic")
        return Kind(VIDEO, "video/mp4")
    if head.startswith(b"%PDF-"):
        return Kind(PDF, "application/pdf")
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        if ext in ("weba", "mka"):
            return Kind(AUDIO, "audio/webm")
        return Kind(VIDEO, "video/x-matroska" if ext == "mkv" else "video/webm")
    if head.startswith(b"OggS"):
        if ext in ("ogv", "ogm"):
            return Kind(VIDEO, "video/ogg")
        return Kind(AUDIO, "audio/ogg")
    if head.startswith(b"fLaC"):
        return Kind(AUDIO, "audio/flac")
    if head.startswith(b"ID3"):
        return Kind(AUDIO, "audio/mpeg")
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0 and ext in (
        "mp3", "aac", "mp2", "mpga",
    ):
        # A frame sync is two bytes, too little to believe without the name.
        return Kind(AUDIO, "audio/aac" if ext == "aac" else "audio/mpeg")
    if head.startswith(b"glTF"):
        return Kind(MODEL3D, "model/gltf-binary")
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        # A zip by its bytes -- and so is every office document, book and
        # package. Only a file called one is listed as one.
        if ext in _ZIP_EXTENSIONS:
            return Kind(ARCHIVE, "application/zip")
        return Kind(BINARY, mimetypes.guess_type(f"x.{ext}")[0] or "application/octet-stream")
    if head.startswith(b"\x1f\x8b"):
        return Kind(ARCHIVE, "application/gzip")
    if head.startswith(b"BZh"):
        return Kind(ARCHIVE, "application/x-bzip2")
    if head.startswith(b"\xfd7zXZ\x00"):
        return Kind(ARCHIVE, "application/x-xz")
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return Kind(ARCHIVE, "application/x-7z-compressed")
    if head[257:262] == b"ustar":
        return Kind(ARCHIVE, "application/x-tar")
    return None


_TEXT_BY_EXTENSION = {
    "md": Kind(MARKDOWN, "text/markdown"),
    "markdown": Kind(MARKDOWN, "text/markdown"),
    "html": Kind(HTML, "text/html"),
    "htm": Kind(HTML, "text/html"),
    "svg": Kind(SVG, "image/svg+xml", "xml"),
    "csv": Kind(TABLE, "text/csv"),
    "tsv": Kind(TABLE, "text/tab-separated-values"),
    "ipynb": Kind(NOTEBOOK, "application/x-ipynb+json", "json"),
    "gltf": Kind(MODEL3D, "model/gltf+json", "json"),
    "obj": Kind(MODEL3D, "model/obj"),
}


def _by_name(name: str, ext: str) -> Kind:
    """A text file's kind, by what it is called (the `show` tool's table for
    Markdown, HTML and code, and the kinds an app has a viewer for)."""
    from lembas.services.tools.files import show_kind

    known = _TEXT_BY_EXTENSION.get(ext)
    if known is not None:
        return known
    kind, language = show_kind(name)
    guessed = mimetypes.guess_type(name)[0] or ""
    mime = guessed if guessed.startswith("text/") or guessed == "application/json" else "text/plain"
    return Kind(kind, mime, language)


def detect(name: str, head: bytes | None) -> Kind:
    """The kind of a file called `name` whose first bytes are `head`.

    `head` is at most `HEAD_BYTES` from the start; an empty file is text by
    its name. `None` -- the bytes are not at hand -- answers by the name alone
    for the text kinds and `binary` for the rest, and is for a row that will
    be asked again with its bytes before anything is served by the answer.
    """
    ext = _ext(name)
    if head is None:
        return _by_name(name, ext) if _named_text(name, ext) else Kind(
            BINARY, mimetypes.guess_type(name)[0] or "application/octet-stream"
        )
    head = bytes(head[:HEAD_BYTES])
    signed = _signature(head, ext)
    if signed is not None:
        return signed
    if looks_like_text(head):
        if ext == "stl":
            return Kind(MODEL3D, "model/stl")
        if ext == "svg" and head.strip() and b"<" not in head:
            return Kind(TEXT, "text/plain")
        if ext in _SIGNED_EXTENSIONS:
            # Called a picture (a film, an archive) and holding words.
            return Kind(TEXT, "text/plain")
        return _by_name(name, ext)
    if ext == "stl":
        # A binary STL has no signature: eighty bytes of anything, then a count.
        return Kind(MODEL3D, "model/stl")
    return Kind(BINARY, "application/octet-stream")


# Extensions whose format has a signature: by the name alone they promise
# nothing, so without bytes they are `binary`.
_SIGNED_EXTENSIONS = frozenset({
    "png", "jpg", "jpeg", "gif", "webp", "avif", "heic", "pdf", "mp3", "wav", "ogg", "oga",
    "opus", "flac", "m4a", "aac", "weba", "mka", "mp4", "m4v", "mov", "webm", "mkv", "avi",
    "ogv", "glb", "zip", "gz", "tgz", "tar", "bz2", "xz", "7z", "stl", "docx", "xlsx", "pptx",
    "odt", "ods", "odp", "epub", "jar", "apk", "exe", "dll", "so", "bin", "iso", "dmg", "woff",
    "woff2", "ttf", "otf", "ico", "bmp", "tiff", "tif", "psd", "sqlite", "db",
})


def _named_text(name: str, ext: str) -> bool:
    return ext not in _SIGNED_EXTENSIONS


__all__ = [
    "ARCHIVE",
    "AUDIO",
    "BINARY",
    "CODE",
    "HEAD_BYTES",
    "HTML",
    "IMAGE",
    "INLINE",
    "KINDS",
    "MARKDOWN",
    "MODEL3D",
    "NOTEBOOK",
    "PDF",
    "SVG",
    "TABLE",
    "TEXT",
    "TEXT_KINDS",
    "VIDEO",
    "Kind",
    "detect",
    "looks_like_text",
]
