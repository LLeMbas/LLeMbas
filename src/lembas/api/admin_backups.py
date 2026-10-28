"""Admin → Backups: the list, back up now, download, delete, restore, the schedule.

Read `services/backup.py` first: what is in an archive, how a restore is staged
and why it is applied only at the next start. This page never replaces the
running data; it stages, and says so.
"""

from __future__ import annotations

import asyncio
import logging
import secrets

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, Response

from lembas.api._redirect import back
from lembas.api.deps import AdminUser, Db
from lembas.config import settings
from lembas.services import backup, crypto, settings_store
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/backups", tags=["admin-backups"])

PAGE = "/admin/backups"


@router.get("")
async def backups_page(request: Request, db: Db, user: AdminUser, saved: str = "",
                       error: str = ""):
    values = settings_store.backups(db)
    return render(
        request,
        "admin/backups.html",
        {
            "archives": backup.archives(),
            "values": values,
            "has_passphrase": bool(values.get("key_passphrase_encrypted")),
            "sentinel": crypto.UNCHANGED_SENTINEL,
            "pending": backup.pending(),
            "directory": str(backup.backup_dir()),
            "compression": backup.extension(),
            "ephemeral_key": settings.secret_key_is_ephemeral,
            "saved": saved,
            "error": error,
        },
    )


@router.post("/now")
async def back_up_now(db: Db, user: AdminUser, passphrase: str = Form("")) -> Response:
    try:
        taken = await asyncio.to_thread(backup.take, "by-hand",
                                        passphrase=passphrase or None)
    except Exception as problem:
        log.exception("a backup by hand failed")
        return back(PAGE, t("The backup failed: %(error)s", error=type(problem).__name__),
                    ok=False)
    log.info("%s backed the instance up to %s", user.email, taken.name)
    return back(PAGE, t("Backed up: %(name)s", name=taken.name))


@router.get("/file/{name}")
async def download(name: str, user: AdminUser) -> Response:
    found = backup.find(name)
    if found is None:
        return Response(status_code=404)
    return FileResponse(
        found.path,
        media_type="application/octet-stream",
        filename=found.name,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.post("/file/{name}/delete")
async def delete(name: str, user: AdminUser) -> Response:
    if not backup.delete(name):
        return back(PAGE, t("That backup is not there any more."), ok=False)
    return back(PAGE, t("Deleted."))


@router.post("/restore")
async def restore(
    user: AdminUser,
    name: str = Form(""),
    passphrase: str = Form(""),
    file: UploadFile | None = File(None),
) -> Response:
    """Stage a backup -- one from the list, or an uploaded one -- for the next start."""
    upload = None
    if file is not None and file.filename:
        folder = settings.data_dir
        upload = folder / f".upload-{secrets.token_hex(6)}"
        with upload.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        path = upload
    else:
        found = backup.find(name)
        if found is None:
            return back(PAGE, t("Choose a backup, or upload one."), ok=False)
        path = found.path
    try:
        staged = await asyncio.to_thread(backup.stage, path, passphrase)
    except backup.Refused as refused:
        return back(PAGE, refused.translated(), ok=False)
    finally:
        if upload is not None:
            upload.unlink(missing_ok=True)
    log.warning("%s staged a restore of a backup from %s", user.email, staged.created_at)
    words = [t("The backup is checked and staged. It is put in place when the service restarts.")]
    words += [t(w) for w in staged.warnings]
    return back(PAGE, " ".join(words))


@router.post("/restore/cancel")
async def cancel(user: AdminUser) -> Response:
    backup.cancel()
    return back(PAGE, t("The staged restore is cancelled."))


@router.post("/settings")
async def save_settings(
    request: Request,
    db: Db,
    user: AdminUser,
    enabled: bool = Form(False),
    time: str = Form("03:00"),
    keep_daily: int = Form(7),
    keep_weekly: int = Form(4),
) -> Response:
    form = await request.form()
    if backup.parse_time(time) is None:
        return back(PAGE, t("The time must be HH:MM, for example 03:00."), ok=False)
    changes = {
        "enabled": enabled,
        "time": time.strip(),
        "keep_daily": min(max(keep_daily, 0), 366),
        "keep_weekly": min(max(keep_weekly, 0), 520),
    }
    if "key_passphrase" in form:
        typed = str(form.get("key_passphrase") or "")
        if typed != crypto.UNCHANGED_SENTINEL:
            changes["key_passphrase_encrypted"] = crypto.encrypt(typed)
    settings_store.update(db, changes, key=settings_store.BACKUPS)
    return back(PAGE, t("Saved."))


__all__ = ["router"]
