"""The text of a file, for the index: decided by its bytes, not its name.

The index is keyed by content hash, so what a hash's text is must depend on
the content alone -- the same bytes under two names are one entry. So the
format is sniffed: a PDF by its header, a DOCX by the zip entry Word keeps its
body in, text by decoding cleanly. Everything else (images, archives, binaries)
has no text and is skipped, which is a state, not an error.

PDF extraction follows `services/files.py`'s attachment path (pypdf, page by
page, `[page N]` markers, a page that fails is skipped rather than fatal), so a
PDF reads the same in a chat and in the tree. DOCX is read with the standard
library: a `.docx` is a zip, its body `word/document.xml`, and the text is in
`w:t` runs inside `w:p` paragraphs -- no dependency for it.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree

log = logging.getLogger(__name__)

# Files above this are not read at all: the index is for finding things, and a
# 200 MB log is not found by meaning.
MAX_BYTES = 25 * 1024 * 1024
# Characters of text kept per hash: about a million tokens, two thousand pieces.
MAX_CHARS = 2_000_000
MAX_PDF_PAGES = 2000

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(frozen=True)
class Extracted:
    """The text, or why there is none (`skip`), or what went wrong (`error`)."""

    text: str = ""
    skip: str = ""
    error: str = ""
    truncated: bool = False


def looks_like_pdf(data: bytes) -> bool:
    return b"%PDF-" in data[:1024]


def looks_like_docx(data: bytes) -> bool:
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return "word/document.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def decode_text(data: bytes) -> str | None:
    """Text if the bytes are text: UTF-8 (BOM or not), or UTF-16 with a BOM.

    Not latin-1, which decodes any byte at all and so says nothing; a NUL in
    the first few kilobytes means binary whatever decoded.
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None
    if "\x00" in text[:8192]:
        return None
    return text


def pdf_text(data: bytes) -> Extracted:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # any failure means the same thing
                return Extracted(skip="password-protected PDF")
        parts: list[str] = []
        total = 0
        truncated = len(reader.pages) > MAX_PDF_PAGES
        for number, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # one bad page is not fatal
                log.debug("page %d of a PDF failed to extract: %s", number, exc)
                continue
            if not text.strip():
                continue
            parts.append(f"[page {number}]\n{text.strip()}")
            total += len(text)
            if total >= MAX_CHARS:
                truncated = True
                break
        body = "\n\n".join(parts)
        if not body.strip():
            return Extracted(skip="no text in the PDF (a scan?)")
        return Extracted(text=body[:MAX_CHARS], truncated=truncated)
    except Exception as exc:  # a bad file must not stop the queue
        return Extracted(error=f"unreadable PDF: {exc}"[:500])


def docx_text(data: bytes) -> Extracted:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_BYTES * 4:
                return Extracted(skip="document body too large")
            xml = archive.read(info)
        root = ElementTree.fromstring(xml)
    except Exception as exc:
        return Extracted(error=f"unreadable DOCX: {exc}"[:500])
    paragraphs: list[str] = []
    total = 0
    for paragraph in root.iter(f"{_W}p"):
        out: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_W}t" and node.text:
                out.append(node.text)
            elif node.tag == f"{_W}tab":
                out.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                out.append("\n")
        line = "".join(out).strip()
        if line:
            paragraphs.append(line)
            total += len(line)
            if total >= MAX_CHARS:
                break
    body = "\n\n".join(paragraphs)
    if not body.strip():
        return Extracted(skip="no text in the document")
    return Extracted(text=body[:MAX_CHARS], truncated=total >= MAX_CHARS)


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f]")


def extract(data: bytes) -> Extracted:
    """The text of `data`, by what it is."""
    if len(data) > MAX_BYTES:
        return Extracted(skip=f"larger than {MAX_BYTES // (1024 * 1024)} MB")
    if not data.strip():
        return Extracted(skip="empty")
    if looks_like_pdf(data):
        return pdf_text(data)
    if looks_like_docx(data):
        return docx_text(data)
    text = decode_text(data)
    if text is None:
        return Extracted(skip="not text")
    # A few stray control characters say nothing; many say a binary format
    # that happened to be valid UTF-8.
    sample = text[:8192]
    if sample and len(_CONTROL.findall(sample)) > len(sample) // 100 + 4:
        return Extracted(skip="not text")
    if not text.strip():
        return Extracted(skip="empty")
    return Extracted(text=text[:MAX_CHARS], truncated=len(text) > MAX_CHARS)


__all__ = [
    "MAX_BYTES",
    "MAX_CHARS",
    "Extracted",
    "decode_text",
    "docx_text",
    "extract",
    "pdf_text",
]
