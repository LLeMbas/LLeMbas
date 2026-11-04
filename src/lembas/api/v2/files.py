"""The person's files, for an app: a directory's entries and a file's text.

`GET /files?path=` and `GET /files/content?path=` are the explorer's listing
and viewer, said in JSON. Both go through `services/fs.py` as the person, with
no chat -- the person's own access (`security/access.py`), the same the
explorer at `/files` draws by, and no chat's grants on top or below. A path the
person cannot read is refused in the harness spec's words, as a **404**.

The content follows the `show` card's file (`chats.show_file`): text only, at
most 1 MB. **An HTML file comes back as its source** and is never rendered
here -- what the app does with it is the app's, and it is model output (rule
6). A file that is not text, or is too large, comes back with an empty `text`
and `withheld: {reason, message}` (`too_large`, `not_text`) rather than a
refusal, so the app can still say what the file is and offer to open it
elsewhere.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import Me
from lembas.security.access import BadPath, normalise
from lembas.services import file_ui, fs
from lembas.web.i18n import t

router = APIRouter()

CONTENT_MAX_BYTES = 1024 * 1024


def _path(raw: str) -> str:
    try:
        return normalise(raw or "/")
    except BadPath as problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("That is not a path.")) from problem


@router.get("/files")
async def list_files(caller: Me, db: Db, path: str = "/") -> dict[str, Any]:
    where = _path(path)
    v = fs.view(db, caller.user)
    if where == "/":
        # A member cannot read `/`: the listing answers for their home, and
        # its `path` says so, so an app's breadcrumbs start there.
        where = fs.start(v)
    try:
        rows = file_ui.listing(v, where)
    except fs.FsError as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, refused.message) from refused
    return serialise.file_listing(where, rows)


@router.get("/files/content")
async def file_content(caller: Me, db: Db, path: str) -> dict[str, Any]:
    where = _path(path)
    try:
        opened = file_ui.open_file(fs.view(db, caller.user), where)
    except fs.FsError as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, refused.message) from refused
    withheld = None
    if opened.size > CONTENT_MAX_BYTES or opened.kind == "large":
        withheld = {
            "reason": serialise.WITHHELD_TOO_LARGE,
            "message": t("This file is too large to show here."),
        }
    elif opened.kind in ("image", "binary"):
        withheld = {
            "reason": serialise.WITHHELD_NOT_TEXT,
            "message": t("This is not a text file, so it is not shown here."),
        }
    return serialise.file_content(opened, withheld)


__all__ = ["router"]
