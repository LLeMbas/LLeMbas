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
| `POST /files/copy` | `{path, to, name?}` → 201 listing of `to`, with `copied` (the new entry) |
| `PUT /files/content` | `{path, text, base?, force?, create?}` → the content answer; 409 |
| `GET /files/raw?path=&version=&inline=` | the bytes; `Range` answered (`api/_download.py`) |
| `GET /files/node?id=` | where it is now: `{id, path, name, parent, kind, file_kind, trashed}` |
| `GET /files/trash` | `{items}` |
| `POST /files/restore` | `{id}` → `{restored, items}` |
| `DELETE /files/trash[?id=]` | for good: one thing, or everything → `{removed, items}` |
| `GET /files/versions?path=` | `{path, versions}` newest first |
| `POST /files/versions/restore` | `{path, version}` → `{path, sha, versions}` |
| `POST /files/batch` | `{op: move|copy|delete, paths, to?}` → `{op, results, done, failed}` |
| `POST /files/zip` | `{paths}` → a zip, written as it is read |

**A file's kind** (`file_kind` on an entry, `kind` on a content answer) is read
from its name and its own bytes (`services/file_kinds.py`), never from what the
sender called it. **A batch is not all-or-nothing**: each path is done by
itself and answered by itself (`services/file_ops.py`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response

from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import Me
from lembas.security.access import BadPath, normalise, parent_of
from lembas.services import file_ops, file_ui, fs
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
    return serialise.file_content(opened, _withheld(opened), await _archive(db, opened))


_ARCHIVE_PROBLEMS = {
    "unsupported": "What is inside this kind of archive cannot be listed here.",
    "busy": "Too many archives are being read at the moment. Open it again shortly.",
    "unreadable": "This archive could not be read.",
}


async def _archive(db, opened: file_ui.Opened) -> dict[str, Any] | None:
    """An archive's listing, as the web's viewer has it: the same service,
    the same caps, nothing unpacked. None for every other kind of file."""
    if opened.file_kind != "archive":
        return None
    from anyio import to_thread

    from lembas.services import archives

    # A tar is read through, which can take a moment: not on the loop.
    found = await to_thread.run_sync(
        archives.listing, db, opened.sha, opened.size, opened.mime, opened.name
    )
    sentence = _ARCHIVE_PROBLEMS.get(found.problem, "")
    return serialise.archive_listing(found, t(sentence) if sentence else "")


@router.get("/files/node")
async def file_node(caller: Me, db: Db, id: str = "") -> dict[str, Any]:
    """Where a file is now, by its id: `{id, path, name, parent, kind,
    file_kind, trashed}`. For a file a screen knows by id only -- a generated
    file's `node_id` -- so it still opens after a rename or a move. Under the
    checks of `/files/raw?node=`: read as the person may read that path, and
    a refusal (404) names the id, never where the file is."""
    try:
        node, trashed = fs.located(fs.view(db, caller.user), id.strip())
    except fs.FsError as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, refused.message) from refused
    kind = fs.kind_of(db, node).kind if node.kind == "file" else None
    return serialise.file_node(node, kind, trashed=trashed)


def _withheld(opened: file_ui.Opened) -> dict[str, str] | None:
    """Why a file's text is not in its answer: it is not text (a picture, a
    PDF, sound... -- fetched as bytes from `/files/raw`), or it is too long."""
    if opened.kind in ("image", "binary"):
        return {
            "reason": serialise.WITHHELD_NOT_TEXT,
            "message": t("This is not a text file, so it is not shown here."),
        }
    if opened.size > CONTENT_MAX_BYTES or opened.kind == "large":
        return {
            "reason": serialise.WITHHELD_TOO_LARGE,
            "message": t("This file is too large to show here."),
        }
    return None


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
        file_ops.store_uploads(v, where, files)
        db.commit()
    except file_ops.TooLarge as problem:
        db.rollback()
        raise _too_large(problem) from problem
    except fs.FsError as refused:
        raise _refused(db, refused) from refused
    return _listing(v, where)


def _too_large(problem: file_ops.TooLarge) -> HTTPException:
    return HTTPException(
        413,
        t("%(name)s is larger than %(n)s MB, the most that can be uploaded here.",
          name=problem.name, n=problem.limit_mb),
    )


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


# --- Copy, save, bytes --------------------------------------------------------------------------
@router.post("/files/copy", status_code=status.HTTP_201_CREATED)
async def copy_file(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Copy `path` into the directory `to` (as `name`, when given). A name
    already taken there is numbered, never overwritten: `copied` says what
    the copy is called."""
    v = fs.view(db, caller.user)
    source = _path(str(body.get("path") or ""))
    target = _path(str(body.get("to") or "/"))
    name = _bare_name(body.get("name")) if body.get("name") else None
    try:
        made = fs.cp(v, source, target, name)
        db.commit()
    except fs.FsError as refused:
        raise _refused(db, refused) from refused
    out = _listing(v, target)
    out["copied"] = next((e for e in out["entries"] if e["path"] == made.path), None)
    return out


def _conflict(v: fs.View, where: str) -> JSONResponse:
    """409: what the file holds now."""
    v.db.expire_all()
    node = fs.stat(v, where)
    newest = fs.versions(v, where)
    return JSONResponse(
        serialise.file_conflict(
            t("This file changed since you opened it. Reload it, or save anyway."),
            (node.blob_hash or "") if node is not None else "",
            newest[0].id if newest else None,
        ),
        status_code=status.HTTP_409_CONFLICT,
    )


@router.put("/files/content")
async def save_content(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> Any:
    """Save a text file. `base` is the `sha` the app opened; when the file
    holds something else by now the answer is **409** `{detail, conflict:
    true, sha, version}` -- what it holds now -- and nothing is written,
    unless `force` is true. Without `base` nothing is compared. A path with
    no file makes one.

    `create: true` makes a **new** file and never writes over one: when
    something is already at the path the answer is **409** `{detail, exists:
    true, path}` and nothing is written (`fs.write(create=True)`: decided by
    the tree, so of two creations of one name one is refused)."""
    v = fs.view(db, caller.user)
    where = _path(str(body.get("path") or ""))
    text = body.get("text")
    if not isinstance(text, str):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("There is no text to save."))
    data = text.encode("utf-8")
    if len(data) > file_ui.EDIT_MAX:
        raise HTTPException(
            413, t("This file is too large to show here.")
        )
    base = str(body.get("base") or "")
    try:
        try:
            node = fs.stat(v, where)
        except fs.FsError:
            node = None  # a new file: the write decides whether it may be made
        compare = node is not None and bool(base) and not body.get("force")
        if compare and (node.blob_hash or "") != base:
            return _conflict(v, where)
        # Compared again where the write is taken (`fs.write`, `base`): the
        # look above and the write are two moments.
        fs.write(v, where, data, create=bool(body.get("create")),
                 base=base if compare else None)
        db.commit()
        opened = file_ui.open_file(v, where)
    except fs.Changed:
        db.rollback()
        return _conflict(v, where)
    except fs.AlreadyExists:
        db.rollback()
        return JSONResponse(
            serialise.file_exists(
                t("There is already something called %(name)s here.",
                  name=where.rsplit("/", 1)[-1]),
                where,
            ),
            status_code=status.HTTP_409_CONFLICT,
        )
    except fs.FsError as refused:
        raise _refused(db, refused) from refused
    return serialise.file_content(opened, _withheld(opened))


@router.get("/files/raw")
async def raw_file(
    request: Request, caller: Me, db: Db, path: str = "", version: str = "",
    inline: bool = False, node: str = "",
) -> Response:
    """A file's bytes (or one version's). An attachment of no type, unless
    `inline` is asked and the file is, by its own bytes, a raster picture,
    sound or video -- then its own type. `Range` is answered, so a player
    can seek.

    `node=<id>` in place of `path`: the file by its id, wherever it is now --
    what a generated file's result carries (`node_id`), so its player still
    works after the file was renamed or moved. Read as the person may read
    that path, like any other."""
    from lembas.api import _download

    if node:
        path = fs.path_of(db, caller.user.org_id, node) or ""
        if not path:
            raise HTTPException(status.HTTP_404_NOT_FOUND, fs.no_such(node).message)
    try:
        found = file_ui.download(fs.view(db, caller.user), _path(path), version)
    except fs.FsError as refused:
        # Asked for by id: the refusal names the id, never the path it has.
        said = fs.no_such(node).message if node else refused.message
        raise HTTPException(status.HTTP_404_NOT_FOUND, said) from refused
    return _download.respond(
        found.name, found.digest, found.size, found.kind, inline=inline,
        range_header=request.headers.get("range"), if_range=request.headers.get("if-range"),
    )


# --- The trash ----------------------------------------------------------------------------------
def _trash(v: fs.View) -> list[dict[str, Any]]:
    return [serialise.trashed(node) for node in fs.trash(v)]


@router.get("/files/trash")
async def list_trash(caller: Me, db: Db) -> dict[str, Any]:
    return {"items": _trash(fs.view(db, caller.user))}


@router.post("/files/restore")
async def restore_file(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    v = fs.view(db, caller.user)
    try:
        node = fs.restore(v, str(body.get("id") or ""))
        db.commit()
    except fs.FsError as refused:
        raise _refused(db, refused) from refused
    return {"restored": node.path, "items": _trash(v)}


@router.delete("/files/trash")
async def empty_trash(caller: Me, db: Db, id: str = "") -> dict[str, Any]:
    """Remove for good: the one thing named, or all the person may restore."""
    v = fs.view(db, caller.user)
    try:
        removed = fs.purge(v, id or None)
        db.commit()
    except fs.FsError as refused:
        raise _refused(db, refused) from refused
    return {"removed": removed, "items": _trash(v)}


# --- Versions -----------------------------------------------------------------------------------
def _versions(v: fs.View, where: str) -> dict[str, Any]:
    from sqlalchemy import select

    from lembas.db.models import User

    rows = file_ui.history(v, where)
    node = fs.stat(v, where)
    writers = {r.by_user_id for r in rows if r.by_user_id}
    names = {
        u.id: u.name for u in v.db.scalars(select(User).where(User.id.in_(writers)))
    } if writers else {}
    current = rows[0].id if rows and rows[0].blob_hash == (node.blob_hash or "") else None
    return {
        "path": where,
        "sha": node.blob_hash or "",
        "versions": [serialise.file_version(r, names, current=r.id == current) for r in rows],
    }


@router.get("/files/versions")
async def list_versions(caller: Me, db: Db, path: str) -> dict[str, Any]:
    where = _path(path)
    try:
        return _versions(fs.view(db, caller.user), where)
    except fs.FsError as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, refused.message) from refused


@router.post("/files/versions/restore")
async def restore_version(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Make an older version the file again -- as a new version, so nothing
    is lost."""
    v = fs.view(db, caller.user)
    where = _path(str(body.get("path") or ""))
    try:
        file_ui.restore_version(v, where, str(body.get("version") or ""))
        db.commit()
        return _versions(v, where)
    except fs.FsError as refused:
        raise _refused(db, refused) from refused


# --- Several at once ----------------------------------------------------------------------------
def _paths(body: dict[str, Any]) -> list[str]:
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("Choose at least one file or folder."))
    if len(paths) > file_ops.BATCH_MAX:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            t("At most %(n)s files or folders at once.", n=file_ops.BATCH_MAX),
        )
    return paths


@router.post("/files/batch")
async def batch_files(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """`{op, paths, to?}`: move or copy each path into the directory `to`, or
    delete each (to the trash). **Not all-or-nothing**: every path is done by
    itself, and `results` answers for each, in order -- `{path, ok, to?,
    error?}`. The answer is 200 whenever the request itself made sense, also
    when every path was refused: read `failed`."""
    op = str(body.get("op") or "")
    if op not in file_ops.OPS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("That is not something to do."))
    paths = _paths(body)
    target = _path(str(body.get("to") or "/")) if op != "delete" else ""
    if op != "delete" and not body.get("to"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("Choose a folder."))
    results = file_ops.batch(fs.view(db, caller.user), op, paths, target)
    return file_ops.batch_answer(op, target, results)


@router.post("/files/zip")
async def zip_files(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> Response:
    """The chosen files and folders as one zip, written as it is read."""
    paths = _paths(body)
    try:
        entries = file_ops.zip_plan(fs.view(db, caller.user), [_path(p) for p in paths])
    except file_ops.TooLarge as problem:
        raise _zip_too_large(problem) from problem
    except fs.FsError as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, refused.message) from refused
    from lembas.api import _download

    return _download.zip_response(entries, file_ops.zip_name(paths))


def _zip_too_large(problem: file_ops.TooLarge) -> HTTPException:
    return HTTPException(
        413,
        t("That is more than %(n)s MB, the most a zip may hold. Choose less.",
          n=problem.limit_mb),
    )


__all__ = ["router"]
