"""The file pages: the explorer, the viewer, the editor, and a chat's view of them.

Everything here is the cookie's door and goes through `services/file_ui.py`,
which goes through a `fs.View` -- so a page reaches exactly what the file
tools reach: a person's own view at `/files`, and a chat's view (its grants,
its data group's floor) behind `/chat/{id}/…`. A path the view cannot read is
answered with the spec's own sentence, the same one a model gets.

Changes answer with a redirect back to the explorer, which a form inside the
page's `[data-in-place-root]` turns into a save in place (`web/in_place.py`).

The HTML of a file is never served as this application: a download is
`application/octet-stream` and `attachment`; HTML to look at is the sealed
frame (`security/sealed.py`), an opaque origin with no network.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from lembas.api._redirect import back
from lembas.api.deps import Db, RequiredUser, is_htmx
from lembas.api.pages import sidebar_context
from lembas.db.models import Chat, Node
from lembas.services import file_ui, fs
from lembas.web.i18n import t
from lembas.web.templating import render

router = APIRouter(tags=["files"])


def _here(path: str) -> str:
    return "/files" + (f"?path={quote(path)}" if path and path != "/" else "")


def _norm(raw: str) -> str:
    from lembas.security.access import BadPath, normalise

    try:
        return normalise(raw or "/")
    except BadPath as problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("That is not a path.")) from problem


def _refused(problem: fs.FsError, where: str) -> Response:
    return back(where, t("That did not work: %(reason)s", reason=problem.message), ok=False)


def _parent(path: str) -> str:
    from lembas.security.access import parent_of

    return parent_of(path)


def _owned_chat(db, chat_id: str, user) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That chat no longer exists."))
    return chat


# --- The explorer ------------------------------------------------------------------
def _tree(v: fs.View, path: str) -> list[dict]:
    """The directory tree beside the list: the root's directories, and the
    chain down to `path` opened."""
    from lembas.security.access import chain

    opened = set(chain(path))

    def level(at: str) -> list[dict]:
        out = []
        try:
            rows = file_ui.subdirs(v, at)
        except fs.FsError:
            return out
        for row in rows:
            is_open = row.path in opened
            out.append(
                {
                    "row": row,
                    "open": is_open,
                    "current": row.path == path,
                    "children": level(row.path) if is_open else None,
                }
            )
        return out

    return level("/")


@router.get("/files")
async def explorer(request: Request, db: Db, user: RequiredUser, path: str = "/"):
    v = fs.view(db, user)
    path = _norm(path)
    problem = ""
    rows: list[file_ui.Row] = []
    try:
        node = fs.stat(v, path)
        if node is not None and node.kind == "file":
            # A file's address opens its viewer.
            from fastapi.responses import RedirectResponse

            return RedirectResponse(f"/files/view?path={quote(path)}", status_code=303)
        rows = file_ui.listing(v, path)
    except fs.FsError as refused:
        problem = refused.message
    return render(
        request,
        "files/index.html",
        {
            "section": "files",
            "path": path,
            "crumbs": file_ui.crumbs(path),
            "rows": rows,
            "tree": _tree(v, path) if not problem else [],
            "problem": problem,
            "writable": not problem and v.level(path) in ("stage", "commit"),
            "saved": request.query_params.get("saved", ""),
            "error": request.query_params.get("error", ""),
            **sidebar_context(db, user),
        },
    )


@router.get("/files/dirs")
async def tree_children(request: Request, db: Db, user: RequiredUser, path: str = "/"):
    """One directory's subdirectories, for the tree's lazy branches."""
    v = fs.view(db, user)
    try:
        rows = file_ui.subdirs(v, _norm(path))
    except fs.FsError:
        rows = []
    return render(
        request,
        "files/_tree.html",
        {"branches": [{"row": r, "open": False, "current": False, "children": None} for r in rows]},
    )


@router.post("/files/upload")
async def upload(
    db: Db,
    user: RequiredUser,
    path: str = Form("/"),
    files: list[UploadFile] = File(default_factory=list),
) -> Response:
    v = fs.view(db, user)
    where = _norm(path)
    done = 0
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

            target = f"{where.rstrip('/')}/{name}"
            fs.write(v, target, chunks(), mime=upload_file.content_type or "")
            done += 1
        db.commit()
    except fs.FsError as refused:
        db.rollback()
        return _refused(refused, _here(where))
    return back(_here(where), t("Uploaded.") if done else t("Nothing was uploaded."))


@router.post("/files/mkdir")
async def make_dir(db: Db, user: RequiredUser, path: str = Form("/"), name: str = Form("")):
    v = fs.view(db, user)
    where = _norm(path)
    name = name.strip()
    if not name or "/" in name:
        return back(_here(where), t("A folder needs a name without a slash."), ok=False)
    try:
        fs.mkdir(v, f"{where.rstrip('/')}/{name}")
        db.commit()
    except fs.FsError as refused:
        db.rollback()
        return _refused(refused, _here(where))
    return back(_here(where), t("Folder made."))


@router.post("/files/rename")
async def rename(db: Db, user: RequiredUser, path: str = Form(...), name: str = Form("")):
    v = fs.view(db, user)
    source = _norm(path)
    where = _parent(source)
    name = name.strip()
    if not name or "/" in name:
        return back(_here(where), t("A name cannot be empty or hold a slash."), ok=False)
    try:
        fs.mv(v, source, f"{where.rstrip('/')}/{name}")
        db.commit()
    except fs.FsError as refused:
        db.rollback()
        return _refused(refused, _here(where))
    return back(_here(where), t("Renamed."))


@router.post("/files/move")
async def move(
    db: Db, user: RequiredUser, path: str = Form(...), to: str = Form(...), back_to: str = Form("")
):
    """Move `path` into the directory `to` (a drop in the explorer)."""
    v = fs.view(db, user)
    source = _norm(path)
    target_dir = _norm(to)
    where = _norm(back_to) if back_to else _parent(source)
    try:
        fs.mv(v, source, f"{target_dir.rstrip('/')}/{source.rsplit('/', 1)[-1]}")
        db.commit()
    except fs.FsError as refused:
        db.rollback()
        return _refused(refused, _here(where))
    return back(_here(where), t("Moved."))


@router.post("/files/delete")
async def delete(db: Db, user: RequiredUser, path: str = Form(...)):
    v = fs.view(db, user)
    target = _norm(path)
    where = _parent(target)
    try:
        fs.rm(v, target)
        db.commit()
    except fs.FsError as refused:
        db.rollback()
        return _refused(refused, _here(where))
    return back(_here(where), t("Moved to the trash."))


@router.get("/files/trash")
async def trash_page(request: Request, db: Db, user: RequiredUser):
    v = fs.view(db, user)
    return render(
        request,
        "files/trash.html",
        {
            "section": "files",
            "items": fs.trash(v),
            "saved": request.query_params.get("saved", ""),
            "error": request.query_params.get("error", ""),
            **sidebar_context(db, user),
        },
    )


@router.post("/files/restore")
async def restore(db: Db, user: RequiredUser, node_id: str = Form(...)):
    v = fs.view(db, user)
    try:
        fs.restore(v, node_id)
        db.commit()
    except fs.FsError as refused:
        db.rollback()
        return _refused(refused, "/files/trash")
    return back("/files/trash", t("Restored."))


# --- Looking at a file ---------------------------------------------------------------
def _viewer_context(opened: file_ui.Opened, *, frame: str, raw: str, chat: Chat | None):
    from lembas.services.markdown import render_markdown

    return {
        "opened": opened,
        "html": render_markdown(opened.text) if opened.kind == "markdown" else "",
        "frame_url": frame,
        "raw_url": raw,
        "chat": chat,
    }


@router.get("/files/view")
async def view_page(
    request: Request, db: Db, user: RequiredUser, path: str = "/", version: str = ""
):
    v = fs.view(db, user)
    target = _norm(path)
    try:
        opened = file_ui.open_file(v, target, version)
        versions = file_ui.history(v, opened.path)
    except fs.FsError as refused:
        return render(
            request,
            "files/view.html",
            {"problem": refused.message, "path": target, "crumbs": file_ui.crumbs(target),
             "section": "files", **sidebar_context(db, user)},
            status_code=404,
        )
    q = f"path={quote(opened.path)}" + (f"&version={quote(version)}" if version else "")
    context = {
        "section": "files",
        "path": opened.path,
        "crumbs": file_ui.crumbs(opened.path),
        "versions": versions,
        "current_version": version,
        "saved": request.query_params.get("saved", ""),
        "error": request.query_params.get("error", ""),
        **_viewer_context(opened, frame=f"/files/frame?{q}", raw=f"/files/raw?{q}", chat=None),
    }
    if is_htmx(request) and request.query_params.get("fragment"):
        return render(request, "files/_viewer.html", context)
    return render(request, "files/view.html", {**context, **sidebar_context(db, user)})


def _download(node: Node, chunks, *, inline: bool) -> Response:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if inline and node.mime in file_ui.INLINE_IMAGES:
        return StreamingResponse(chunks, media_type=node.mime, headers=headers)
    from lembas.services.files import content_disposition

    headers["Content-Disposition"] = content_disposition("attachment", node.name)
    return StreamingResponse(chunks, media_type="application/octet-stream", headers=headers)


@router.get("/files/raw")
async def raw(db: Db, user: RequiredUser, path: str, version: str = "", inline: bool = False):
    v = fs.view(db, user)
    try:
        node, chunks = file_ui.stream(v, _norm(path), version)
    except fs.FsError as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, refused.message) from refused
    return _download(node, chunks, inline=inline)


def _sealed(text: str) -> Response:
    from lembas.security import sealed

    return HTMLResponse(text, headers=sealed.headers())


@router.get("/files/frame")
async def frame(db: Db, user: RequiredUser, path: str, version: str = ""):
    """An HTML file as a page, sealed: for the viewer's frame only."""
    v = fs.view(db, user)
    try:
        opened = file_ui.open_file(v, _norm(path), version)
    except fs.FsError as refused:
        return _sealed(f"<p>{_escape(refused.message)}</p>")
    if opened.kind != "html":
        return _sealed("")
    return _sealed(opened.text)


def _escape(text: str) -> str:
    from markupsafe import escape

    return str(escape(text))


@router.post("/files/versions/restore")
async def restore_version(
    db: Db, user: RequiredUser, path: str = Form(...), version: str = Form(...)
):
    v = fs.view(db, user)
    target = _norm(path)
    where = f"/files/view?path={quote(target)}"
    try:
        file_ui.restore_version(v, target, version)
        db.commit()
    except fs.FsError as refused:
        db.rollback()
        return _refused(refused, where)
    return back(where, t("That version is the current one again."))


# --- Editing ----------------------------------------------------------------------------
@router.get("/files/edit")
async def edit_page(request: Request, db: Db, user: RequiredUser, path: str = "/"):
    v = fs.view(db, user)
    target = _norm(path)
    problem = ""
    opened = None
    try:
        opened = file_ui.open_file(v, target)
        if not opened.writable:
            problem = fs.read_only(target).message
        elif opened.kind in ("image", "binary", "large"):
            problem = t("Only a text file of at most 1 MB can be edited here.")
    except fs.FsError as refused:
        problem = refused.message
    return render(
        request,
        "files/edit.html",
        {
            "section": "files",
            "path": target,
            "crumbs": file_ui.crumbs(target),
            "opened": opened,
            "problem": problem,
            "saved": request.query_params.get("saved", ""),
            "error": request.query_params.get("error", ""),
            **sidebar_context(db, user),
        },
    )


@router.post("/files/save")
async def save(request: Request, db: Db, user: RequiredUser):
    """Save the editor. `base` is the content hash the editor opened; a file
    that changed since is refused (409) unless `force` is set, so two tabs, or
    a tab and a chat, never overwrite each other unseen."""
    form = await request.form()
    target = _norm(str(form.get("path", "")))
    content = str(form.get("content", ""))
    base = str(form.get("base", ""))
    wants_json = "application/json" in request.headers.get("accept", "")
    where = f"/files/edit?path={quote(target)}"
    v = fs.view(db, user)
    try:
        node = fs.stat(v, target)
        if node is not None and base and (node.blob_hash or "") != base and not form.get("force"):
            message = t("This file changed since you opened it. Reload it, or save anyway.")
            if wants_json:
                return JSONResponse({"detail": message, "conflict": True}, status_code=409)
            return back(where, message, ok=False)
        node = fs.write(v, target, content.encode("utf-8"))
        db.commit()
    except fs.FsError as refused:
        db.rollback()
        if wants_json:
            return JSONResponse({"detail": refused.message}, status_code=403)
        return _refused(refused, where)
    if wants_json:
        return JSONResponse({"sha": node.blob_hash, "message": t("Saved.")})
    return back(where, t("Saved."))


# --- A chat's view: the show card, and undoing the chat's changes ------------------------
@router.get("/chat/{chat_id}/show")
async def chat_show(
    request: Request,
    db: Db,
    user: RequiredUser,
    chat_id: str,
    node: str = "",
    version: str = "",
    shown: str = "",
    line: int = 0,
):
    """What a `show` card opens: the file as the chat sees it *now* -- the
    chat's view is rebuilt and the path re-checked, so a grant taken away since
    takes the file away too. A device's file (`shown`) is the copy the device
    sent, read through the other engineer's `services/shown.py`."""
    chat = _owned_chat(db, chat_id, user)
    context: dict = {"chat": chat, "line": line}
    if shown:
        from lembas.services import shown as shown_service

        row = shown_service.get(db, chat.id, shown)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, t("That file is not here any more."))
        text = shown_service.text_of(db, row)
        opened = file_ui.Opened(
            path=row.path, name=row.name, node=None, version=None, size=row.size,
            sha=row.blob_hash, mime="text/plain", kind=row.kind, language=row.language or "",
            text=text, level="ro",
        )
        url = f"/api/chats/{chat.id}/shown/{row.id}"
        context.update(_viewer_context(opened, frame=f"{url}?page=1", raw=url, chat=chat))
    else:
        found = db.get(Node, node) if node else None
        v = fs.view(db, user, chat)
        if found is None or found.org_id != user.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, t("That file is not here any more."))
        try:
            opened = file_ui.open_file(v, found.path, version)
        except fs.FsError as refused:
            raise HTTPException(status.HTTP_404_NOT_FOUND, refused.message) from refused
        q = f"node={quote(found.id)}" + (f"&version={quote(version)}" if version else "")
        context.update(
            _viewer_context(
                opened,
                frame=f"/chat/{chat.id}/show/frame?{q}",
                raw=f"/chat/{chat.id}/show/raw?{q}",
                chat=chat,
            )
        )
    if is_htmx(request):
        return render(request, "files/_viewer.html", context)
    return render(
        request,
        "files/view.html",
        {**context, "section": "files", "path": context["opened"].path,
         "crumbs": file_ui.crumbs(context["opened"].path), "versions": [],
         **sidebar_context(db, user)},
    )


def _chat_node(db, user, chat_id: str, node_id: str):
    chat = _owned_chat(db, chat_id, user)
    found = db.get(Node, node_id) if node_id else None
    if found is None or found.org_id != user.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That file is not here any more."))
    return chat, found, fs.view(db, user, chat)


@router.get("/chat/{chat_id}/show/frame")
async def chat_show_frame(db: Db, user: RequiredUser, chat_id: str, node: str, version: str = ""):
    _chat, found, v = _chat_node(db, user, chat_id, node)
    try:
        opened = file_ui.open_file(v, found.path, version)
    except fs.FsError as refused:
        return _sealed(f"<p>{_escape(refused.message)}</p>")
    return _sealed(opened.text if opened.kind == "html" else "")


@router.get("/chat/{chat_id}/show/raw")
async def chat_show_raw(
    db: Db, user: RequiredUser, chat_id: str, node: str, version: str = "", inline: bool = False
):
    _chat, found, v = _chat_node(db, user, chat_id, node)
    try:
        row, chunks = file_ui.stream(v, found.path, version)
    except fs.FsError as refused:
        raise HTTPException(status.HTTP_404_NOT_FOUND, refused.message) from refused
    return _download(row, chunks, inline=inline)


@router.get("/chat/{chat_id}/undo")
async def undo_page(request: Request, db: Db, user: RequiredUser, chat_id: str):
    chat = _owned_chat(db, chat_id, user)
    context = {"chat": chat, "plan": file_ui.undo_plan(db, user, chat)}
    if is_htmx(request):
        return render(request, "files/_undo.html", context)
    return render(request, "files/undo.html", {**context, **sidebar_context(db, user)})


@router.post("/chat/{chat_id}/undo")
async def undo(db: Db, user: RequiredUser, chat_id: str):
    chat = _owned_chat(db, chat_id, user)
    try:
        done = file_ui.undo(db, user, chat)
        db.commit()
    except fs.FsError as refused:
        db.rollback()
        return _refused(refused, f"/chat/{chat.id}")
    said = t("The files are back as they were.") if done else t("Nothing to put back.")
    return back(f"/chat/{chat.id}", said)


__all__ = ["router"]
