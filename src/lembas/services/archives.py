"""What is inside an archive: a listing, and nothing else.

Nothing is ever extracted. A zip is read from its directory at the end of
the file, which says every name and size without unpacking a byte -- by this
module's own few lines (`_zip`), not by `zipfile`, so that what is capped and
what is read are the same numbers. A tar
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
import threading
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
    problem: str = ""  # "unreadable", "unsupported" or "busy": no listing


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


# How much of a zip's directory is read to list it, and how many entries a
# directory may claim before it is not read at all.
MAX_DIRECTORY = 4 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 100_000
# A tar header that carries a long name or pax records says how long it is,
# and `tarfile` reads that much into memory in one piece. A name is a few
# hundred bytes; a megabyte of pax records is already extraordinary.
MAX_TAR_HEADER = 1024 * 1024

_EOCD = b"PK\x05\x06"
_LOCATOR = b"PK\x06\x07"
_EOCD64 = b"PK\x06\x06"
_ENTRY = b"PK\x01\x02"


def _zip(source, size: int) -> Listing:
    """A zip's listing, read from its directory **by this code and no
    library**.

    `zipfile` reads the whole directory into memory before anything can be
    capped, and it decides how large that is its own way: from the classic
    end record -- or from a zip64 one, whenever a locator for it sits in
    front, whatever the classic record says. A check made on one record and
    a read made by the other is no check (a few bytes can claim a directory
    of gigabytes and millions of entries). So the end records are read
    here, both, the numbers are capped, and then only the first part of the
    directory is read and walked -- the same numbers checked and used.
    """
    tail = min(size, 66_000)
    source.seek(size - tail)
    end = source.read(tail)
    at = end.rfind(_EOCD)
    if at < 0 or len(end) - at < 22:
        return Listing(problem="unreadable")
    where = size - tail + at  # the end record's own place in the file
    count = int.from_bytes(end[at + 10 : at + 12], "little")
    directory = int.from_bytes(end[at + 12 : at + 16], "little")
    offset = int.from_bytes(end[at + 16 : at + 20], "little")
    locator = where - 20
    if locator >= 0:
        source.seek(locator)
        found = source.read(20)
        if found[:4] == _LOCATOR:
            # A zip64: the real numbers are in its own end record.
            where = int.from_bytes(found[8:16], "little")
            if where < 0 or where + 56 > size:
                return Listing(problem="unreadable")
            source.seek(where)
            record = source.read(56)
            if record[:4] != _EOCD64:
                return Listing(problem="unreadable")
            count = int.from_bytes(record[32:40], "little")
            directory = int.from_bytes(record[40:48], "little")
            offset = int.from_bytes(record[48:56], "little")
        elif count == 0xFFFF or directory == 0xFFFFFFFF or offset == 0xFFFFFFFF:
            return Listing(problem="unreadable")
    if count > MAX_DIRECTORY_ENTRIES or directory > size:
        return Listing(total=min(count, 10**12), cut=True)
    # The directory ends where its end record begins; an archive with
    # something put in front of it (a self-extracting one) has every offset
    # short by that much, so the place is worked out from the end.
    start = where - directory
    if start < 0:
        start = offset
    source.seek(start)
    data = source.read(min(directory, MAX_DIRECTORY))
    if count and data[:4] != _ENTRY:
        source.seek(offset)
        data = source.read(min(directory, MAX_DIRECTORY))
        if data[:4] != _ENTRY:
            return Listing(problem="unreadable")
    listing = Listing(total=count)
    at = 0
    while len(listing.entries) < min(count, MAX_ENTRIES):
        head = data[at : at + 46]
        if len(head) < 46 or head[:4] != _ENTRY:
            break
        flags = int.from_bytes(head[8:10], "little")
        unpacked = int.from_bytes(head[24:28], "little")
        name_len = int.from_bytes(head[28:30], "little")
        extra_len = int.from_bytes(head[30:32], "little")
        comment_len = int.from_bytes(head[32:34], "little")
        raw = data[at + 46 : at + 46 + name_len]
        if len(raw) < name_len:
            break
        if unpacked == 0xFFFFFFFF:
            unpacked = _zip64_size(data[at + 46 + name_len : at + 46 + name_len + extra_len])
        name = raw.decode("utf-8" if flags & 0x800 else "cp437", "replace")
        is_dir = name.endswith("/")
        listing.entries.append(Entry(_name(name), None if is_dir else unpacked, is_dir))
        at += 46 + name_len + extra_len + comment_len
    listing.cut = len(listing.entries) < count
    return listing


def _zip64_size(extra: bytes) -> int | None:
    """The real size from an entry's zip64 extra field (id 1), where the
    32-bit one is all ones."""
    at = 0
    while at + 4 <= len(extra):
        kind = int.from_bytes(extra[at : at + 2], "little")
        length = int.from_bytes(extra[at + 2 : at + 4], "little")
        if kind == 1 and length >= 8:
            return int.from_bytes(extra[at + 4 : at + 12], "little")
        at += 4 + length
    return None


class _Header(tarfile.TarInfo):
    """A tar header that will not have a long name or pax records of any
    size read for it: `tarfile` reads what such a header declares into
    memory at once, and the declaration is the archive's own."""

    def _proc_member(self, tarfile_):
        special = (
            tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK, tarfile.XHDTYPE,
            tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE,
        )
        if self.type in special and self.size > MAX_TAR_HEADER:
            raise _TooMuch
        return super()._proc_member(tarfile_)


def _tar(stream, deadline: float) -> Listing:
    listing = Listing()
    try:
        with tarfile.open(fileobj=io.BufferedReader(_Counted(stream, deadline), 1 << 20),
                          mode="r|", tarinfo=_Header) as archive:
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


# How many archives are read at once, in the whole process, and how long one
# more waits for its turn. A listing can pass over a quarter of a gigabyte
# and take seconds; a page of them opened at once is not allowed to be the
# only thing the server does.
MAX_AT_ONCE = 2
WAIT_SECONDS = 4.0
_turns = threading.BoundedSemaphore(MAX_AT_ONCE)


def listing(db: DBSession, digest: str, size: int, mime: str, name: str) -> Listing:
    """The entries of the archive stored under `digest`. Never raises: an
    archive that cannot be read is a listing with a `problem` --
    `unreadable`, `unsupported`, or `busy` when too many are being read."""
    if not digest or size <= 0:
        return Listing(problem="unreadable")
    if not _turns.acquire(timeout=WAIT_SECONDS):
        return Listing(problem="busy")
    try:
        return _listing(db, digest, size, mime, name)
    finally:
        _turns.release()


def _listing(db: DBSession, digest: str, size: int, mime: str, name: str) -> Listing:
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
