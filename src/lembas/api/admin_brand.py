"""Administration → General: the instance's logo, and Web Push.

**The logo** is a PNG, JPEG or WebP of at most `MAX_LOGO` bytes, kept in the
blob store by hash (one reference, released when replaced or removed) and shown
wherever the instance's name is: it replaces the pixel leaf in `mark()`
(`_macros.html`), so the sidebar, the sign-in page and the empty states follow
at once. The type is read from the bytes, never the name. SVG is refused: it is
a document that can carry script, and `/brand/logo` is served from this
instance's own origin. The route is public, because the sign-in page shows it.

**Web Push** is off by default (`settings_store.web_push_enabled`), with the
note the owner asked for: a browser's push goes through its vendor's relay,
and what is sent says only that something happened (`services/push.py`).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status

from lembas.api._redirect import back
from lembas.api.deps import AdminUser, Db
from lembas.services import blobs, branding, settings_store
from lembas.web.i18n import t

log = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])

MAX_LOGO = 512 * 1024
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


def sniff(data: bytes) -> str:
    """The image type by its first bytes, or "" for anything not accepted."""
    for magic, mime in SIGNATURES:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _set_logo(db, digest: str, mime: str) -> None:
    values = settings_store.get_group(db, settings_store.GENERAL)
    old = values.get("logo_blob_hash") or ""
    if digest:
        blobs.incref(db, digest)
    if old:
        blobs.decref(db, old)
    settings_store.update(db, {"logo_blob_hash": digest, "logo_mime": mime})
    db.commit()
    branding.forget()


@router.post("/admin/general/logo")
async def upload_logo(db: Db, user: AdminUser, logo: UploadFile = File(...)) -> Response:
    data = await logo.read(MAX_LOGO + 1)
    if len(data) > MAX_LOGO:
        return back("/admin/general", t("The logo can be at most 512 KB."), ok=False)
    mime = sniff(data)
    if not mime:
        return back("/admin/general", t("The logo must be a PNG, JPEG or WebP image."), ok=False)
    digest = blobs.put_bytes(db, data)
    _set_logo(db, digest, mime)
    log.info("instance logo set by %s", user.email)
    return back("/admin/general", t("Logo saved."))


@router.post("/admin/general/logo/delete")
async def remove_logo(db: Db, user: AdminUser) -> Response:
    _set_logo(db, "", "")
    return back("/admin/general", t("The LLeMbas mark is back."))


@router.post("/admin/general/push")
async def save_push(db: Db, user: AdminUser, web_push_enabled: bool = Form(False)) -> Response:
    settings_store.update(db, {"web_push_enabled": web_push_enabled})
    db.commit()
    log.info("web push %s by %s", "on" if web_push_enabled else "off", user.email)
    return back("/admin/general", t("Saved."))


@router.post("/admin/general/python")
async def save_python(db: Db, user: AdminUser, pyodide_enabled: bool = Form(False)) -> Response:
    settings_store.update(db, {"pyodide_enabled": pyodide_enabled})
    db.commit()
    log.info("python in the browser %s by %s", "on" if pyodide_enabled else "off", user.email)
    return back("/admin/general", t("Saved."))


@router.get("/brand/logo")
async def logo(db: Db) -> Response:
    values = settings_store.get_group(db, settings_store.GENERAL)
    digest = values.get("logo_blob_hash") or ""
    mime = values.get("logo_mime") or ""
    if not digest or mime not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return Response(
        blobs.read_bytes(db, digest),
        media_type=mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            # The address carries the hash (`?v=`), so a new logo is a new URL.
            "Cache-Control": "public, max-age=604800",
        },
    )


__all__ = ["MAX_LOGO", "router", "sniff"]
