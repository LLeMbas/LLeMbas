"""What is inside an archive: a listing, and nothing else.

Nothing is ever extracted. A zip is read from its directory at the end of
the file, which says every name and size without unpacking a byte. A tar
(plain, or under gzip, bzip2 or xz) has no directory, so its headers are
read one after another -- which means passing over the contents, and under
compression unpacking them on the way. That is bounded three ways: how many
entries are listed, how many bytes are unpacked, and how long it may take.
Past any of them the listing stops and says it is not the whole of it.

Every name in a listing is somebody else's text: the page escapes it.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import tarfile
import time
import zipfile
from dataclasses import dataclass, field

from sqlalchemy.orm import Session as DBSession

from lembas.services import blobs

MAX_ENTRIES = 2000
MAX_UNPACKED = 256 * 1024 * 1024
MAX_SECONDS = 3.0
MAX_NAME = 400


@dataclass
class Entry:
    name: str
    size: int | None
    is_dir: bool = False


@dataclass
class Listing:
    entries: list[Entry] = field(default_factory=list)
    total: int | None = None  # how many the archive holds, where it says
    cut: bool = False  # there is more than is listed
    problem: str = ""  # "unreadable" or "unsupported": no listing


class _Blob(io.RawIOBase):
    """A stored file as a seekable stream, read by range: never whole."""

    def __init__(self, db: DBSession, digest: str, size: int) -> None:
        self._db, self._digest, self._size, self._at = db, digest, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._at

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._at, io.SEEK_END: self._size}[whence]
        self._at = max(0, base + offset)
        return self._at

    def readinto(self, buffer) -> int:
        want = min(len(buffer), self._size - self._at)
        if want <= 0:
            return 0
        data = b"".join(
            blobs.iter_range(self._db, self._digest, self._at, self._at + want - 1)
        )
        buffer[: len(data)] = data
        self._at += len(data)
        return len(data)


class _TooMuch(Exception):
    pass


class _Counted(io.RawIOBase):
    """An unpacking stream that stops at a number of bytes and at a time."""

    def __init__(self, inner, deadline: float) -> None:
        self._inner, self._deadline, self._count = inner, deadline, 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        if self._count > MAX_UNPACKED or time.monotonic() > self._deadline:
            raise _TooMuch
        data = self._inner.read(min(len(buffer), 1 << 20))
        buffer[: len(data)] = data
        self._count += len(data)
        return len(data)


def _name(raw: str) -> str:
    return raw.replace("\x00", "")[:MAX_NAME]


MAX_DIRECTORY = 16 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 100_000


def _zip(source, size: int) -> Listing:
    # `zipfile` reads the whole directory into memory before anything can be
    # capped, so its size is read first, from the record at the very end.
    tail = min(size, 66_000)
    source.seek(size - tail)
    end = source.read(tail)
    at = end.rfind(b"PK\x05\x06")
    if at < 0 or len(end) - at < 22:
        return Listing(problem="unreadable")
    count = int.from_bytes(end[at + 10 : at + 12], "little")
    directory = int.from_bytes(end[at + 12 : at + 16], "little")
    if count == 0xFFFF or directory == 0xFFFFFFFF:
        # A zip64: its real numbers are elsewhere and can be anything.
        return Listing(cut=True)
    if directory > MAX_DIRECTORY or count > MAX_DIRECTORY_ENTRIES:
        return Listing(total=count, cut=True)
    source.seek(0)
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
    listing = Listing(total=len(infos), cut=len(infos) > MAX_ENTRIES)
    for info in infos[:MAX_ENTRIES]:
        listing.entries.append(
            Entry(_name(info.filename), None if info.is_dir() else info.file_size, info.is_dir())
        )
    return listing


def _tar(stream, deadline: float) -> Listing:
    listing = Listing()
    try:
        with tarfile.open(fileobj=io.BufferedReader(_Counted(stream, deadline), 1 << 20),
                          mode="r|") as archive:
            for member in archive:
                if len(listing.entries) >= MAX_ENTRIES:
                    listing.cut = True
                    break
                listing.entries.append(
                    Entry(_name(member.name), None if member.isdir() else member.size,
                          member.isdir())
                )
    except _TooMuch:
        listing.cut = True
    return listing


# Each is read through once by `_tar` and dropped with it; none holds a file
# of the system's, only the stored blob's reader.
_UNPACKERS = {
    "application/gzip": lambda source: gzip.GzipFile(fileobj=source),
    "application/x-bzip2": lambda source: bz2.BZ2File(source),
    "application/x-xz": lambda source: lzma.LZMAFile(source),  # noqa: SIM115
}


def listing(db: DBSession, digest: str, size: int, mime: str, name: str) -> Listing:
    """The entries of the archive stored under `digest`. Never raises: an
    archive that cannot be read is a listing with a `problem`."""
    if not digest or size <= 0:
        return Listing(problem="unreadable")
    source = io.BufferedReader(_Blob(db, digest, size), 1 << 20)
    deadline = time.monotonic() + MAX_SECONDS
    try:
        if mime == "application/zip":
            return _zip(source, size)
        if mime == "application/x-tar":
            return _tar(source, deadline)
        unpack = _UNPACKERS.get(mime)
        if unpack is None:
            return Listing(problem="unsupported")
        try:
            found = _tar(unpack(source), deadline)
        except tarfile.ReadError:
            found = Listing()
        if found.entries or found.cut:
            return found
        # Not a tar underneath: one packed file, named as the archive is.
        inner = name.rsplit(".", 1)[0] if "." in name else name
        return Listing(entries=[Entry(_name(inner), None)], total=1)
    except _TooMuch:
        return Listing(cut=True)
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, ValueError,
            lzma.LZMAError, NotImplementedError):
        return Listing(problem="unreadable")


__all__ = ["Entry", "Listing", "listing"]
