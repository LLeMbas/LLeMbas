"""Where a generated thing is kept: the person's Files, under `Generated/`.

Every generation -- a picture, an edited picture, a video, a track, a 3D
model -- is written **once**, into the home of the person it was made for:
`<home>/Generated/<kind>-<date>-<time>.<ext>`. Through `fs.write` as that
person, so the tree's rules hold (their home is theirs to write), made and
never written over (`create=True`: a name that is taken gets a number), and
with the chat and the reply in the version, as any tool's write has them.
With the person's own access and not the chat's: the place is this server's
choice, not a path a model named, and a chat bounded to one project folder
still gets its video.

What comes back is the **typed result** a tool event and a job carry:

```
{"kind": "video", "path": "/home/frodo/Generated/video-20260102-030405.mp4",
 "node_id": "…", "name": "video-….mp4", "mime": "video/mp4", "size": 1234,
 "width": 1280, "height": 720, "seconds": 5}
```

`kind` is the capability (`image`, `image_edit`, `video`, `music`,
`model3d`); `width`, `height` and `seconds` are there where they are known.

**What a file is, is read from its bytes** (`file_kinds.detect`): a "video"
that is a web page is refused, whatever the far side called it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import User
from lembas.services import file_kinds, fs
from lembas.services.llm.openai_client import LLMError
from lembas.services.media import store
from lembas.web.i18n import N_, t

DIRECTORY = "Generated"

# The file kind (`services/file_kinds.py`) each capability must turn out to be.
EXPECTED = {
    store.CAP_IMAGE: file_kinds.IMAGE,
    store.CAP_IMAGE_EDIT: file_kinds.IMAGE,
    store.CAP_VIDEO: file_kinds.VIDEO,
    store.CAP_MUSIC: file_kinds.AUDIO,
    store.CAP_MODEL3D: file_kinds.MODEL3D,
}
# What the file is called, and the extension tried when the far side gave none.
WORDS = {
    store.CAP_IMAGE: ("image", "png"),
    store.CAP_IMAGE_EDIT: ("edit", "png"),
    store.CAP_VIDEO: ("video", "mp4"),
    store.CAP_MUSIC: ("music", "mp3"),
    store.CAP_MODEL3D: ("model", "glb"),
}
EXTENSIONS = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
    "image/avif": "avif", "video/mp4": "mp4", "video/webm": "webm",
    "video/x-matroska": "mkv", "video/quicktime": "mov", "audio/mpeg": "mp3",
    "audio/flac": "flac", "audio/wav": "wav", "audio/ogg": "ogg", "audio/mp4": "m4a",
    "audio/webm": "weba", "model/gltf-binary": "glb", "model/stl": "stl",
}
WRONG = {
    store.CAP_IMAGE: N_("What came back is not a picture."),
    store.CAP_IMAGE_EDIT: N_("What came back is not a picture."),
    store.CAP_VIDEO: N_("What came back is not a video."),
    store.CAP_MUSIC: N_("What came back is not sound."),
    store.CAP_MODEL3D: N_("What came back is not a 3D model this instance knows."),
}


def sniff(kind: str, payload: bytes, hint: str = "") -> tuple[str, str]:
    """`(extension, mime)` of a generated file, by its own bytes -- or a
    refusal when it is not the kind of thing that was asked for. `hint` is
    the extension the far side used, which only matters for the formats that
    have no signature (a text glTF, an OBJ, an STL)."""
    hint = "".join(ch for ch in hint.lower().lstrip(".") if ch.isalnum())[:8]
    ext = hint or WORDS[kind][1]
    found = file_kinds.detect(f"file.{ext}", payload[: file_kinds.HEAD_BYTES])
    if found.kind != EXPECTED[kind] and hint:
        # The name lied; ask the bytes alone.
        found = file_kinds.detect(f"file.{WORDS[kind][1]}", payload[: file_kinds.HEAD_BYTES])
        ext = WORDS[kind][1]
    if found.kind != EXPECTED[kind]:
        raise LLMError(t(WRONG[kind]))
    return EXTENSIONS.get(found.mime, ext), found.mime


def keep(
    db: DBSession,
    user: User,
    *,
    kind: str,
    payload: bytes,
    hint: str = "",
    chat_id: str | None = None,
    message_id: str | None = None,
    width: int = 0,
    height: int = 0,
    seconds: float = 0,
    when: datetime | None = None,
) -> dict[str, Any]:
    """Write one generated file into the person's `Generated/` and answer its
    typed result. The caller commits. Raises `LLMError` when the bytes are
    not the kind asked for, `fs.FsError` when the tree refuses."""
    extension, _ = sniff(kind, payload, hint)
    home = fs.ensure_home(db, user).path
    view = fs.view(db, user, message_id=message_id or None, stamp_chat_id=chat_id or None)
    stamp = (when or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    stem = f"{home.rstrip('/')}/{DIRECTORY}/{WORDS[kind][0]}-{stamp}"
    node = None
    for number in range(1, 200):
        path = f"{stem}{'' if number == 1 else f'-{number}'}.{extension}"
        try:
            node = fs.write(view, path, payload, create=True)
            break
        except fs.AlreadyExists:
            continue
    if node is None:  # pragma: no cover - two hundred files in one second
        raise LLMError(t("The file could not be given a name."))
    newest = fs.versions(view, node.path)
    out: dict[str, Any] = {
        "kind": kind,
        "path": node.path,
        "node_id": node.id,
        "version_id": newest[0].id if newest else "",
        "name": node.name,
        "mime": node.mime or "",
        "size": int(node.size or 0),
    }
    if width and height:
        out["width"], out["height"] = int(width), int(height)
    if seconds:
        out["seconds"] = round(float(seconds), 2)
    return out


__all__ = ["DIRECTORY", "EXPECTED", "keep", "sniff"]
