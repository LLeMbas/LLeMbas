"""A file an agent shows that is not text: a picture, a recording, a PDF.

The `show` tool hands the person a file. A text file opens in the viewer
beside the chat (`services/tools/files.py`, `services/shown.py`); anything
else is drawn **in the conversation, by its kind**, as a generated file is:
the same typed result on the tool call (`media`, `services/media/library.py`),
the same card, the same route for the bytes. Whether the chat's model could
read such a file does not come into it: reading and showing are different
things, and nothing shown here is sent to a model.

Two sources, one result:

- **A file in the tree** (the chat's own `show`): the card names the file
  itself, by its id. Nothing is copied.
- **A file on a linked computer** (`_lembas/show` with a `handle`, harness
  spec 3.11.0): this server cannot reach the device's disk, so it fetches the
  bytes over the link (`_lembas/show/read`) and writes them **once** into the
  person's `Generated/`, as a generation is -- so the file outlives the chat,
  and the conversation addresses it by node id like any other.

**What a file is, is read from its bytes** (`file_kinds.detect`). The name and
the kind a device or a model gave are a hint for the formats that have no
signature and nothing more: a "picture" that is a web page is not drawn as a
picture, and it is never served inline because somebody called it one -- the
raw route decides that again, by the bytes, every time it is asked.

The result's `kind` is the file kind: `image`, `audio`, `video`, `model3d`,
`pdf`, `archive`, `binary` -- and `text` for a text file too large for the
viewer. A client draws the first five and offers the rest to open or download.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import User
from lembas.services import file_kinds, fs, tokens
from lembas.services.media import library

# The most a shown file may be, and how much one answer of a device carries
# (harness spec 3.11.0, `loop.json` `files`); read from the vendored spec
# where it has them.
MAX_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
WAIT_SECONDS = 300

# The kinds drawn in the conversation; the rest are a name with two buttons.
DRAWN = (file_kinds.IMAGE, file_kinds.AUDIO, file_kinds.VIDEO, file_kinds.MODEL3D, file_kinds.PDF)

_UNSAFE = re.compile(r"[^\w.\- ]+", re.UNICODE)


class Refused(ValueError):
    """A file that is not shown, with the sentence the model is told."""


def limits() -> dict[str, int]:
    """`max_bytes`, `chunk_bytes`, `wait_seconds`: the spec's numbers where
    the vendored spec carries them (3.11.0), else the same numbers from here."""
    from lembas.services import harness_spec

    files = harness_spec.loop().get("files")
    files = files if isinstance(files, dict) else {}
    show = files.get("show") if isinstance(files.get("show"), dict) else {}

    def number(value: Any, fallback: int) -> int:
        return int(value) if isinstance(value, int) and not isinstance(value, bool) and value > 0 \
            else fallback

    return {
        "max_bytes": number(show.get("media_max_bytes"), MAX_BYTES),
        "chunk_bytes": number(files.get("chunk_max_bytes"), CHUNK_BYTES),
        "wait_seconds": number(show.get("media_wait_seconds"), WAIT_SECONDS),
    }


def megabytes(size: int) -> str:
    return f"{max(1, round(size / (1024 * 1024)))} MB"


def too_large(name: str, size: int) -> str:
    """The refusal for a file past the limit, in words (the spec's sentence
    shape: what it is, what the most is, what to do instead)."""
    return (
        f"{name} is {megabytes(size)}; at most {megabytes(limits()['max_bytes'])} can be "
        "shown. Give the user the path instead."
    )


def kind_of(name: str, head: bytes) -> file_kinds.Kind:
    """What the file is, by its bytes; the name only where a format has no
    signature."""
    return file_kinds.detect(name, head[: file_kinds.HEAD_BYTES])


def is_text(name: str, head: bytes) -> bool:
    return kind_of(name, head).is_text


def result(node, head: bytes, *, version_id: str = "") -> dict[str, Any]:
    """The typed result for a file of the tree: `media` on the tool call."""
    found = kind_of(node.name, head)
    out: dict[str, Any] = {
        "kind": "text" if found.is_text else found.kind,
        "path": node.path,
        "node_id": node.id,
        "version_id": version_id,
        "name": node.name,
        "mime": found.mime,
        "size": int(node.size or 0),
        # Shown by an agent, not generated: a client words its card by this.
        "shown": True,
    }
    if found.kind == file_kinds.IMAGE and (size := tokens.image_size(head[:4096])):
        width, height = size
        if 0 < width <= 100_000 and 0 < height <= 100_000:
            out["width"], out["height"] = int(width), int(height)
    return out


def file_name(name: str, found: file_kinds.Kind, when: datetime) -> str:
    """What a device's file is called in `Generated/`: its own name, made
    safe, with the moment in it -- and the extension its bytes say, where they
    say one."""
    base = name.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = _UNSAFE.sub("-", stem).strip(" .-")[:80] or "file"
    ext = "".join(ch for ch in ext.lower() if ch.isalnum())[:8]
    ext = library.EXTENSIONS.get(found.mime) or ext or "bin"
    if found.mime == "application/pdf":
        ext = "pdf"
    return f"{stem}-{when.strftime('%Y%m%d-%H%M%S')}.{ext}"


def keep(
    db: DBSession,
    user: User,
    *,
    name: str,
    payload: bytes | list[bytes],
    chat_id: str | None = None,
    message_id: str | None = None,
    when: datetime | None = None,
) -> dict[str, Any]:
    """Write a device's file into the person's `Generated/` and answer its
    typed result. With the person's own access, as `library.keep` writes: the
    place is this server's choice, not a path a model named. `payload` may be
    the parts as they arrived: they are written one after another and never
    joined into a second copy. The caller commits. Raises `fs.FsError` when
    the tree refuses."""
    parts = [payload] if isinstance(payload, bytes | bytearray) else list(payload)
    head = b"".join(parts[:1])[:8192]
    found = kind_of(name, head)
    home = fs.ensure_home(db, user).path
    view = fs.view(db, user, message_id=message_id or None, stamp_chat_id=chat_id or None)
    called = file_name(name, found, when or datetime.now(UTC))
    stem, _, ext = called.rpartition(".")
    node = None
    for number in range(1, 200):
        numbered = stem if number == 1 else f"{stem}-{number}"
        path = f"{home.rstrip('/')}/{library.DIRECTORY}/{numbered}.{ext}"
        try:
            node = fs.write(view, path, iter(parts), create=True)
            break
        except fs.AlreadyExists:
            continue
    if node is None:  # pragma: no cover - two hundred files in one second
        raise Refused("The file could not be given a name.")
    newest = fs.versions(view, node.path)
    return result(node, head, version_id=newest[0].id if newest else "")


def digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = [
    "CHUNK_BYTES",
    "DRAWN",
    "MAX_BYTES",
    "Refused",
    "WAIT_SECONDS",
    "digest",
    "file_name",
    "is_text",
    "keep",
    "kind_of",
    "limits",
    "megabytes",
    "result",
    "too_large",
]
