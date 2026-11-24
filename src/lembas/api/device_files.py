"""A linked computer as one more place of the Files page: `/files/device/{id}…`.

The cookie's door to `services/device_files.py` (the apps' is
`api/v2/device_files.py`): browse the computer's shared folders, open a file,
edit text, upload, download. Deliberately less than the server's own
explorer -- no move, copy, delete or search here; LLeMbas Desktop has those.

Every route is the person's own session and their own device
(`device_files.device`): nobody opens another person's computer, an
administrator included. The Origin check (`security/hardening.py`) covers the
two POSTs like every state-changing page route.

What a computer answers is somebody else's data. A name, a path and a
sentence are text in a template, escaped; a file's bytes leave through
`send_raw`, which serves a download unless the bytes themselves are a
picture, sound or video.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from lembas.api._redirect import back
from lembas.api.deps import Db, RequiredUser
from lembas.api.pages import sidebar_context
from lembas.api.v2.device_files import send_raw, upload_into
from lembas.services import device_files
from lembas.web.i18n import t
from lembas.web.templating import render

router = APIRouter()


def _computer(db, user, token_id: str) -> dict[str, Any]:
    """The place, as the pages name it -- or `Refused` when it is not this
    person's to open."""
    from lembas.services import device_link

    token = device_files.device(db, user, token_id)
    link = device_link.link_for(token.id)
    name = device_link.short_name(link) if link is not None else (token.device_host or token.name)
    return {
        "id": token.id,
        "name": name[: device_files.MAX_NAME],
        # Read-only where the computer's highest mode is plan (acp.md).
        "writable": link is not None and (link.max_mode or "") != "plan",
    }


def _crumbs(roots: list[str], path: str) -> list[tuple[str, str]]:
    """The path as links: the shared folder it is in, then each part under
    it. Never a link above a root: the computer would refuse it."""
    if not path:
        return []
    root = max((r for r in roots if path == r or path.startswith(r.rstrip("/") + "/")),
               key=len, default="")
    if not root:
        return [(path, path)]
    out = [(root.rstrip("/").rsplit("/", 1)[-1] or root, root)]
    at = root.rstrip("/")
    for part in [p for p in path[len(root):].split("/") if p]:
        at = f"{at}/{part}"
        out.append((part, at))
    return out


def _frame(request: Request, db, user, computer: dict[str, Any] | None) -> dict[str, Any]:
    """What the explorer's frame draws around a computer's page."""
    return {
        "section": "files",
        "computer": computer or {"id": "", "name": t("A linked computer"), "writable": False},
        "place": computer["id"] if computer else "",
        "places": device_files.places(db, user),
        "top": "/",
        "tree": [],
        "python": False,
        "saved": request.query_params.get("saved", ""),
        "error": request.query_params.get("error", ""),
        **sidebar_context(db, user),
    }


def _roots(token_id: str) -> list[str]:
    from lembas.services import device_link

    link = device_link.link_for(token_id)
    return [str(r) for r in (link.roots if link is not None else [])]


@router.get("/files/device/{token_id}")
async def device_explorer(request: Request, db: Db, user: RequiredUser, token_id: str,
                          path: str = ""):
    computer, listing, problem = None, None, ""
    try:
        computer = _computer(db, user, token_id)
        listing = await device_files.list_dir(db, user, token_id, path)
    except device_files.Refused as refused:
        problem = refused.message
    rows = []
    for entry in (listing or {}).get("entries", []):
        when = entry["mtime"]
        rows.append({
            **entry,
            "when": datetime.fromtimestamp(when / 1000, UTC) if 0 < when < 4e12 else None,
        })
    return render(
        request,
        "files/device.html",
        {
            **_frame(request, db, user, computer),
            "path": (listing or {}).get("path", "") if not problem else path,
            "crumbs": _crumbs(_roots(token_id), path) if computer else [],
            "rows": rows,
            "truncated": bool((listing or {}).get("truncated")),
            "problem": problem,
            "writable": bool((listing or {}).get("writable")) and not problem,
        },
        status_code=200,
    )


@router.get("/files/device/{token_id}/view")
async def device_file(request: Request, db: Db, user: RequiredUser, token_id: str,
                      path: str = ""):
    computer, opened, problem = None, None, ""
    try:
        computer = _computer(db, user, token_id)
        if not path:
            raise device_files.Refused("invalid", t("No file was named."))
        opened = await device_files.read_text(db, user, token_id, path)
    except device_files.Refused as refused:
        problem = refused.message
    writable = bool(opened and opened["writable"])
    crumbs = _crumbs(_roots(token_id), path) if computer else []
    return render(
        request,
        "files/device_view.html",
        {
            **_frame(request, db, user, computer),
            "path": path,
            "crumbs": crumbs,
            "opened": opened,
            "problem": problem,
            "writable": writable,
            # The editor: a text file the computer lets this person change,
            # whose hash is known -- without it a save could not say what it
            # was based on.
            "editing": bool(
                opened and opened["writable"] and not opened.get("withheld") and opened["sha"]
            ),
        },
    )


@router.get("/files/device/{token_id}/raw")
async def device_raw(request: Request, db: Db, user: RequiredUser, token_id: str,
                     path: str = "", inline: bool = False):
    try:
        if not path:
            raise device_files.Refused("invalid", t("No file was named."))
        return await send_raw(request, db, user, token_id, path, inline=inline)
    except device_files.Refused as refused:
        return Response(refused.message, status_code=refused.status_code,
                        media_type="text/plain; charset=utf-8",
                        headers={"X-Content-Type-Options": "nosniff"})


@router.post("/files/device/{token_id}/save")
async def device_save(request: Request, db: Db, user: RequiredUser, token_id: str):
    """Save the editor (js/editor.js, or the plain form). `base` is the hash
    the editor opened: a file that changed on the computer since is refused
    (409, `conflict`) unless `force` is set -- the same answer
    `POST /files/save` gives for a file of the tree."""
    form = await request.form()
    path = str(form.get("path", ""))
    content = str(form.get("content", ""))
    base = str(form.get("base", ""))
    force = bool(form.get("force"))
    wants_json = "application/json" in request.headers.get("accept", "")
    where = f"/files/device/{quote(token_id)}/view?path={quote(path)}"
    try:
        data = content.encode("utf-8")
        if len(data) > device_files.TEXT_MAX_BYTES:
            raise device_files.Refused(
                "too_large", t("That text is over 1 MB: too large to save from an editor here.")
            )
        if not path or not (base or force):
            raise device_files.Refused("invalid", t("This save does not say what it is based on."))
        done = await device_files.write(
            db, user, token_id, path, data,
            **({"overwrite": True} if force else {"base": base}),
        )
    except device_files.Refused as refused:
        conflict = refused.reason == "changed"
        message = (
            t("This file changed on the computer since you opened it. Reload it, or save anyway.")
            if conflict else refused.message
        )
        if wants_json:
            return JSONResponse({"detail": message, "conflict": conflict},
                                status_code=409 if conflict else refused.status_code)
        return back(where, message, ok=False)
    if wants_json:
        # `sha`: the editor's next base, under the name it reads.
        return JSONResponse({"sha": done["sha"], "message": t("Saved.")})
    return back(where, t("Saved."))


@router.post("/files/device/{token_id}/upload")
async def device_upload(
    db: Db, user: RequiredUser, token_id: str, path: str = Form(""),
    files: list[UploadFile] = File(...),
):
    """Files into the directory `path`. A name that is taken there is not
    written over: said, and the rest that were sent before it stay."""
    where = f"/files/device/{quote(token_id)}?path={quote(path)}"
    try:
        if not path:
            raise device_files.Refused("invalid", t("No folder was named."))
        done = await upload_into(db, user, token_id, path, files, overwrite=False)
    except device_files.Refused as refused:
        return back(where, refused.message, ok=False)
    return back(where, t("%(n)s file(s) uploaded.", n=len(done)))


__all__ = ["router"]
