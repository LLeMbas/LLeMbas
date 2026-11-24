"""A linked computer's files for the apps: `/api/v2/devices/{id}/fs…`.

The JSON door to `services/device_files.py`, which relays the device's own
`_lembas/fs/*` (harness spec 3.11.0, capability `fs`). The web's Files page
is the other door (`api/device_files.py`); both answer from that one module.

| Route | What |
|---|---|
| `GET /devices/{id}/fs?path=&hidden=` | a directory; no `path`: the device's roots |
| `GET /devices/{id}/fs/stat?path=&hash=` | one entry |
| `GET /devices/{id}/fs/content?path=` | a text file for an editor, with its `hash` |
| `PUT /devices/{id}/fs/content` | save text, with the base check |
| `GET /devices/{id}/fs/raw?path=&inline=` | the bytes |
| `POST /devices/{id}/fs/upload?path=&overwrite=` | multipart `files` into a directory |
| `POST /devices/{id}/fs/mkdir` · `/rename` | |
| `POST /devices/{id}/fs/move` · `/copy` · `/delete` | several paths, or one |
| `DELETE /devices/{id}/fs?path=&permanent=` | one path: to the device's trash, or for good |
| `GET /devices/{id}/fs/search?path=&q=&content=&regex=&limit=` | names, or lines |

A refusal is `{detail, reason}` with the device's reason as a word (`off`,
`outside`, `protected`, `own`, `runs_code`, `read_only`, `missing`, `exists`, `changed`,
`not_a_file`, `not_a_directory`, `no_trash`, `too_large`, `invalid`) or this
server's (`not_found`, `device_not_linked`, `cli_too_old`, `no_answer`,
`bad_answer`); a `changed` one adds `conflict: true`, `sha` and `mtime`.

The shapes are the tree's own where the two meet (`GET`/`PUT /files/content`,
`POST /files/batch`), so an app reads a file of a computer with the code that
reads a file of the server. **Copying between two places** is the client's:
`GET …/raw` from one and a multipart upload to the other (`/fs/upload` here,
`/files/upload` there); this server holds no copy in between.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from lembas.api.deps import Db
from lembas.api.v2._common import Me
from lembas.services import device_files, file_kinds
from lembas.web.i18n import t

router = APIRouter()

_UNSET = device_files._UNSET


def refusal(exc: device_files.Refused) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.status_code)


def _need(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise device_files.Refused(
            "invalid", t("`%(name)s` is required: a path on the computer.", name=name)
        )
    return value


def how(body: dict[str, Any]) -> dict[str, Any]:
    """What a save says it is based on, as `device_files.write` takes it:
    `base` (the `sha` the editor opened), `create: true` or `base: null` (a
    new file: it must not exist), or `force`/`overwrite` (whatever is
    there). None of them: left out, and the device refuses."""
    if body.get("force") is True or body.get("overwrite") is True:
        return {"overwrite": True}
    if body.get("create") is True:
        return {"base": None}
    if "base" not in body:
        return {}
    base = body["base"]
    if base is not None and not isinstance(base, str):
        raise device_files.Refused("invalid", t("`base` is a hash, or null for a new file."))
    return {"base": base or None}


@router.get("/devices/{token_id}/fs")
async def list_files(
    caller: Me, db: Db, token_id: str, path: str = "", hidden: bool = False
):
    """`{device, path, parent, writable, entries, truncated}`. Without
    `path`: the device's roots (`path` ""). An entry is `{name, path, kind,
    size, mtime, updated_at, file_kind, link, outside, protected, runs_code,
    writable}` -- `kind` `file`, `dir` or `other`; `mtime` milliseconds and
    `updated_at` the same moment as text; `link` a symlink's target as
    written, else null; `outside` a link that leaves the roots (it cannot be
    opened); `protected` an entry the device names and will not open;
    `runs_code` one of the files that run code (git's own directory, a
    project's agent configuration), which the device lets nobody change from
    here unless its own setting says so; `writable` whether to offer a change
    at all -- the device decides for good."""
    try:
        return await device_files.list_dir(db, caller.user, token_id, path, hidden=hidden)
    except device_files.Refused as exc:
        return refusal(exc)


@router.get("/devices/{token_id}/fs/stat")
async def stat_file(caller: Me, db: Db, token_id: str, path: str = "", hash: bool = False):
    try:
        return await device_files.stat(
            db, caller.user, token_id, _need(path, "path"), with_hash=hash
        )
    except device_files.Refused as exc:
        return refusal(exc)


@router.get("/devices/{token_id}/fs/content")
async def read_content(caller: Me, db: Db, token_id: str, path: str = ""):
    """A file for an editor, as the tree's `GET /files/content`: `{path,
    name, size, mtime, mime, kind, file_kind, writable, language, sha, text}`,
    and `withheld {reason, message}` (`not_text`, `too_large`: over 1 MB) with
    `text` "" -- fetch `/fs/raw` instead. `sha` is the `base` of the save
    that follows."""
    try:
        return await device_files.read_text(db, caller.user, token_id, _need(path, "path"))
    except device_files.Refused as exc:
        return refusal(exc)


@router.put("/devices/{token_id}/fs/content")
async def save_content(caller: Me, db: Db, token_id: str, body: dict[str, Any] = Body(...)):
    """`{path, text, base? | create? | force?}` → `{path, size, mtime, sha}`.

    `base` is the `sha` the editor opened: when the file holds something
    else by now -- or its folder is not where it was -- the device refuses
    and nothing is written: **409** `{detail, reason: "changed", conflict:
    true, sha, mtime}`, the sha it has now (null when it is gone).
    `create: true` (or `base: null`) makes a new file and refuses one that
    exists (`exists`). `force: true` (also spelled `overwrite`) writes
    whatever is there. None of the three is refused (`invalid`)."""
    try:
        text = body.get("text")
        if not isinstance(text, str):
            raise device_files.Refused("invalid", t("`text` is required: the file's text."))
        data = text.encode("utf-8")
        if len(data) > device_files.TEXT_MAX_BYTES:
            raise device_files.Refused(
                "too_large", t("That text is over 1 MB: too large to save from an editor here.")
            )
        return await device_files.write(
            db, caller.user, token_id, _need(body.get("path"), "path"), data, **how(body)
        )
    except device_files.Refused as exc:
        return refusal(exc)


async def send_raw(
    request: Request, db, user, token_id: str, path: str, *, inline: bool
) -> Response:
    """A device's file going out, for both doors. As `api/_download.py` has
    it: an attachment of no type, unless asked for `inline` and its own first
    bytes say it is a picture, sound or video -- never because of its name.
    Read from the device part by part as it is sent."""
    from lembas.services.files import content_disposition

    first = await device_files.read_first(db, user, token_id, path)
    name = path.rstrip("/").rsplit("/", 1)[-1][: device_files.MAX_NAME] or "file"
    parts = device_files.read_parts(db, user, token_id, path, first)
    head = await anext(parts)
    kind = file_kinds.detect(name, head[: file_kinds.HEAD_BYTES])
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    size = device_files._int(first.get("size"))
    if size >= len(head):
        headers["Content-Length"] = str(size)
    if inline and kind.inline:
        media_type = kind.mime
    else:
        media_type = "application/octet-stream"
        headers["Content-Disposition"] = content_disposition("attachment", name)

    async def body():
        yield head
        async for piece in parts:
            yield piece

    return StreamingResponse(body(), media_type=media_type, headers=headers)


@router.get("/devices/{token_id}/fs/raw")
async def raw_file(
    request: Request, caller: Me, db: Db, token_id: str, path: str = "", inline: bool = False
):
    """The file's bytes: a download, or with `inline=1` a picture, sound or
    video under its own type. No `Range`: the whole file, read from the
    computer as it is sent."""
    try:
        return await send_raw(
            request, db, caller.user, token_id, _need(path, "path"), inline=inline
        )
    except device_files.Refused as exc:
        return refusal(exc)


async def upload_into(
    db, user, token_id: str, directory: str, files: list[UploadFile], *, overwrite: bool
) -> list[dict[str, Any]]:
    """Each uploaded file written into `directory` on the device, by its own
    name (a name is a name: nothing of a path in it survives). A file that
    exists is refused unless `overwrite`. At most the instance's upload size
    a file."""
    from lembas.services import files as files_service

    most = files_service.limits().max_upload_bytes
    out = []
    for upload in files:
        name = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not name or name in (".", ".."):
            raise device_files.Refused("invalid", t("An uploaded file has no name."))
        data = await upload.read(most + 1)
        if len(data) > most:
            raise device_files.Refused(
                "too_large",
                t("%(name)s is larger than this instance takes in one upload (%(mb)s MB).",
                  name=name, mb=most // (1024 * 1024)),
            )
        target = f"{directory.rstrip('/')}/{name}"
        out.append(
            await device_files.write(
                db, user, token_id, target, data,
                base=_UNSET if overwrite else None, overwrite=overwrite,
            )
        )
    return out


@router.post("/devices/{token_id}/fs/upload", status_code=201)
async def upload_files(
    caller: Me, db: Db, token_id: str, path: str = Query(""), overwrite: bool = False,
    files: list[UploadFile] = File(...),
):
    """Multipart `files` into the directory `path` → 201 `{written: [{path,
    size, mtime, sha}], listing}`, the directory as it is now. A name that
    is taken is **409** `exists` unless `overwrite=1`; files before it stay
    written."""
    try:
        directory = _need(path, "path")
        done = await upload_into(db, caller.user, token_id, directory, files, overwrite=overwrite)
        return {
            "written": done,
            "listing": await device_files.list_dir(db, caller.user, token_id, directory),
        }
    except device_files.Refused as exc:
        return refusal(exc)


def _under(body: dict[str, Any]) -> str:
    """`{path}` the directory itself, or `{path, name}` a name in it."""
    path = _need(body.get("path"), "path")
    name = body.get("name")
    if name is None:
        return path
    if not isinstance(name, str) or not name.strip() or "/" in name or name in (".", ".."):
        raise device_files.Refused("invalid", t("A name is one name: no slash in it."))
    return f"{path.rstrip('/')}/{name}"


@router.post("/devices/{token_id}/fs/mkdir", status_code=201)
async def make_directory(caller: Me, db: Db, token_id: str, body: dict[str, Any] = Body(...)):
    """`{path}` (the directory to make) or `{path, name}` (a folder `name`
    in `path`) → `{path}`; parents are made, one already there is done."""
    try:
        return await device_files.mkdir(db, caller.user, token_id, _under(body))
    except device_files.Refused as exc:
        return refusal(exc)


@router.post("/devices/{token_id}/fs/rename")
async def rename_entry(caller: Me, db: Db, token_id: str, body: dict[str, Any] = Body(...)):
    """`{path, name}` → `{path}`: the same directory, a new name. A name
    that is taken is `exists`."""
    try:
        path = _need(body.get("path"), "path")
        target = _under({"path": path.rstrip("/").rsplit("/", 1)[0] or "/",
                         "name": body.get("name") or ""})
        return await device_files.transfer(db, caller.user, token_id, "move", path, target)
    except device_files.Refused as exc:
        return refusal(exc)


def _paths(body: dict[str, Any]) -> list[str]:
    paths = body.get("paths")
    if not isinstance(paths, list) or not paths or not all(
        isinstance(p, str) and p.strip() for p in paths
    ):
        raise device_files.Refused("invalid", t("`paths` is required: a list of paths."))
    if len(paths) > device_files.MAX_BATCH:
        raise device_files.Refused("invalid", t("Too many paths in one request."))
    return paths


async def _transfer(caller, db, token_id: str, method: str, body: dict[str, Any]):
    try:
        if "paths" in body:
            return await device_files.batch(
                db, caller.user, token_id, method, _paths(body), _need(body.get("to"), "to"),
                overwrite=body.get("overwrite") is True,
            )
        return await device_files.transfer(
            db, caller.user, token_id, method,
            _need(body.get("from"), "from"), _need(body.get("to"), "to"),
            overwrite=body.get("overwrite") is True,
        )
    except device_files.Refused as exc:
        return refusal(exc)


@router.post("/devices/{token_id}/fs/move")
async def move_entry(caller: Me, db: Db, token_id: str, body: dict[str, Any] = Body(...)):
    """Two forms. `{paths, to, overwrite?}`: each path into the directory
    `to`, under its own name → the tree's batch answer `{op, to, atomic:
    false, results: [{path, ok, to?, error?, reason?}], done, failed}` (200
    whenever the request made sense). `{from, to, overwrite?}`: one entry to
    the exact new path `to` → `{path}`. A target that exists is `exists`
    unless `overwrite`; a directory is never written over."""
    return await _transfer(caller, db, token_id, "move", body)


@router.post("/devices/{token_id}/fs/copy")
async def copy_entry(caller: Me, db: Db, token_id: str, body: dict[str, Any] = Body(...)):
    """As `move`, copying: a directory with all in it."""
    return await _transfer(caller, db, token_id, "copy", body)


@router.post("/devices/{token_id}/fs/delete")
async def delete_entries(caller: Me, db: Db, token_id: str, body: dict[str, Any] = Body(...)):
    """`{paths, permanent?}` → the batch answer; each result that went says
    `trash` (whether to the computer's trash). One the computer has no trash
    for fails with `reason: "no_trash"`: ask the person, then send those
    paths again with `permanent: true`, which cannot be undone."""
    try:
        return await device_files.batch(
            db, caller.user, token_id, "delete", _paths(body),
            permanent=body.get("permanent") is True,
        )
    except device_files.Refused as exc:
        return refusal(exc)


@router.delete("/devices/{token_id}/fs")
async def delete_entry(
    caller: Me, db: Db, token_id: str, path: str = "", permanent: bool = False
):
    """→ `{deleted: true, trash}`: `trash` says whether it went to the
    computer's trash. Where the computer has none for this file the delete is
    **409** `no_trash` -- ask the person, then send it again with
    `permanent=1`, which cannot be undone."""
    try:
        return await device_files.delete(
            db, caller.user, token_id, _need(path, "path"), permanent=permanent
        )
    except device_files.Refused as exc:
        return refusal(exc)


@router.get("/devices/{token_id}/fs/search")
async def search_files(
    caller: Me, db: Db, token_id: str, path: str = "", q: str = "", content: bool = False,
    regex: bool = False, limit: int = 0,
):
    """Names under `path`: `{root, files: [{path, kind}], truncated}`. With
    `content=1`: `{root, matches: [{path, line, text}], truncated}` -- `q` as
    written unless `regex=1`."""
    try:
        return await device_files.search(
            db, caller.user, token_id, _need(path, "path"), _need(q, "q"),
            content=content, regex=regex, limit=limit,
        )
    except device_files.Refused as exc:
        return refusal(exc)


__all__ = ["how", "refusal", "router", "send_raw", "upload_into"]
