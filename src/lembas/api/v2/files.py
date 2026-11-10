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

**The library's writes** are the explorer's (`api/tree.py`), through the same
`fs` calls as the person: upload into a directory, rename in place, move into
another directory, delete (to the trash, as on the web), and make a directory.
Each answers with the listing of the directory it changed, so the app redraws
from what is there; a refusal is the explorer's, as a 400 with its reason.

| Route | |
|---|---|
| `POST /files/upload?path=` | multipart `files` → 201 listing of `path` |
| `POST /files/mkdir` | `{path, name}` → 201 listing of `path` |
| `POST /files/rename` | `{path, name}` → listing of its directory |
| `POST /files/move` | `{path, to}` → listing of `to` |
| `DELETE /files?path=` | 204 (to the trash) |
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import Me
from lembas.security.access import BadPath, normalise, parent_of
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


# --- Writes -------------------------------------------------------------------------------------
def _refused(db, problem: fs.FsError) -> HTTPException:
    db.rollback()
    return HTTPException(
        status.HTTP_400_BAD_REQUEST, t("That did not work: %(reason)s", reason=problem.message)
    )


def _bare_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or "/" in name or name in (".", ".."):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, t("A name cannot be empty or hold a slash.")
        )
    return name


def _listing(v, where: str) -> dict[str, Any]:
    try:
        return serialise.file_listing(where, file_ui.listing(v, where))
    except fs.FsError as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, refused.message) from refused


@router.post("/files/upload", status_code=status.HTTP_201_CREATED)
async def upload_files(
    caller: Me,
    db: Db,
    path: str = Query("/"),
    files: list[UploadFile] = File(default_factory=list),
) -> dict[str, Any]:
    v = fs.view(db, caller.user)
    where = _path(path)
    try:
        for upload_file in files:
            if not upload_file.filename:
                continue
            name = upload_file.filename.replace("\\", "/").rsplit("/", 1)[-1]

            def chunks(source=upload_file):
                while True:
                    part = source.file.read(1024 * 1024)
                    if not part:
                        return
                    yield part

            fs.write(v, f"{where.rstrip('/')}/{name}", chunks(),
                     mime=upload_file.content_type or "")
        db.commit()
    except fs.FsError as refused:
        raise _refused(db, refused) from refused
    return _listing(v, where)


@router.post("/files/mkdir", status_code=status.HTTP_201_CREATED)
async def make_dir(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    v = fs.view(db, caller.user)
    where = _path(str(body.get("path") or "/"))
    name = _bare_name(body.get("name"))
    try:
        fs.mkdir(v, f"{where.rstrip('/')}/{name}")
        db.commit()
    except fs.FsError as refused:
        raise _refused(db, refused) from refused
    return _listing(v, where)


@router.post("/files/rename")
async def rename_file(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    v = fs.view(db, caller.user)
    source = _path(str(body.get("path") or ""))
    where = parent_of(source)
    name = _bare_name(body.get("name"))
    try:
        fs.mv(v, source, f"{where.rstrip('/')}/{name}")
        db.commit()
    except fs.FsError as refused:
        raise _refused(db, refused) from refused
    return _listing(v, where)


@router.post("/files/move")
async def move_file(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    v = fs.view(db, caller.user)
    source = _path(str(body.get("path") or ""))
    target = _path(str(body.get("to") or "/"))
    try:
        fs.mv(v, source, f"{target.rstrip('/')}/{source.rsplit('/', 1)[-1]}")
        db.commit()
    except fs.FsError as refused:
        raise _refused(db, refused) from refused
    return _listing(v, target)


@router.delete("/files", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(caller: Me, db: Db, path: str = Query(...)) -> Response:
    v = fs.view(db, caller.user)
    target = _path(path)
    try:
        fs.rm(v, target)
        db.commit()
    except fs.FsError as refused:
        raise _refused(db, refused) from refused
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
