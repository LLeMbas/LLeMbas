"""What is running here, and getting to what is not.

Read `services/updates.py` first — the reason the button writes a file rather
than doing the work is there, and it is the whole design.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from lembas import __version__
from lembas.api.deps import AdminUser, Db
from lembas.services import audit, harness_spec, settings_store, updates_auto
from lembas.services import export as export_service
from lembas.services import updates as updates_service
from lembas.web import i18n
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/updates", tags=["admin-updates"])


def _seen(db, user) -> None:
    """Opening this page is reading what changed: the *Updated* note goes."""
    values = dict(user.settings_json or {})
    if values.get("last_seen_version") != __version__:
        values["last_seen_version"] = __version__
        user.settings_json = values
        db.commit()


def _page(
    request: Request, state, saved: str = "", *, db=None, export_error: str = ""
) -> Response:
    values = settings_store.updates(db) if db is not None else {}
    return render(
        request,
        "admin/updates.html",
        {
            "state": state,
            "auto": updates_auto.STATE,
            "auto_values": values,
            "policies": updates_auto.POLICIES,
            "spec": harness_spec.version(),
            "command": updates_service.manual_command(),
            # Only asked when the helper is installed: no helper, no copy to read.
            "helper_guarded": (not state.helper) or updates_service.helper_guarded(),
            "saved": saved,
            "export_error": export_error,
        },
    )


@router.get("")
async def updates_page(
    request: Request, db: Db, user: AdminUser, saved: str = "", export_error: str = ""
):
    """No network on a page load.

    `read(fetch=False)` compares against whatever the last fetch left behind, so
    opening this is a few git reads off the local disk. A page that reached the
    remote every time it was rendered would be one somebody stops opening.
    """
    _seen(db, user)
    return _page(request, updates_service.read(), saved, db=db, export_error=export_error)


@router.post("/export")
async def export_admin(
    request: Request,
    db: Db,
    user: AdminUser,
    passphrase: str = Form(""),
    passphrase_confirm: str = Form(""),
) -> Response:
    """The instance's configuration as a file, for the next major version.

    A plain form POST answered with an attachment, not htmx: the browser saves
    the file and the page stays where it is. Keys go into it only encrypted
    with the passphrase given here, and without one they are left out -- the
    file is meant to be carried around, and rule 5 holds for it as for a page.
    The passphrase is not kept, logged or audited: the audit middleware records
    the path and never a form's values.
    """
    if passphrase != passphrase_confirm:
        return RedirectResponse(
            "/admin/updates?export_error="
            + quote(i18n.t("The two passphrases are not the same. Nothing was exported."))
            + "#export",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    document = export_service.admin(db, passphrase=passphrase)
    log.info(
        "%s exported the instance's configuration (%s items, secrets %s)",
        user.email,
        len(document["items"]),
        "encrypted" if passphrase else "left out",
    )
    return Response(
        content=export_service.dumps(document),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{export_service.filename(export_service.KIND_ADMIN)}"'
            ),
            "Cache-Control": "no-store",
        },
    )


@router.post("/check")
async def check(request: Request, db: Db, user: AdminUser) -> Response:
    """Ask the remote what is there. The one place this touches the network."""
    state = updates_service.read(fetch=True)
    log.info("%s checked for updates", user.email)
    audit.record("update.check", user=user, request=request,
                 summary=f"newest {state.available.label if state.available else 'none'}")
    return _page(request, state, db=db)


@router.post("/settings")
async def save_settings(
    request: Request,
    db: Db,
    user: AdminUser,
    auto_apply: str = Form("off"),
    check_every_hours: int = Form(6),
    window: str = Form(""),
    idle_minutes: int = Form(10),
    drain_after_minutes: int = Form(60),
) -> Response:
    window = window.strip()
    settings_store.replace(
        db,
        {
            "auto_apply": auto_apply if auto_apply in updates_auto.POLICIES else "off",
            "check_every_hours": min(max(check_every_hours, 1), 168),
            "window": window[:20] if updates_auto._WINDOW.match(window) or not window else "",
            "idle_minutes": min(max(idle_minutes, 0), 1440),
            "drain_after_minutes": min(max(drain_after_minutes, 0), 1440),
        },
        key=settings_store.UPDATES,
    )
    # A changed policy is judged on the next check, which is now.
    updates_auto.STATE.last_check = 0.0
    audit.record("update.settings", user=user, request=request,
                 summary=f"automatic updates: {auto_apply}")
    return RedirectResponse(
        f"/admin/updates?saved={quote(i18n.t('Saved.'))}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/apply")
async def apply(db: Db, user: AdminUser, when: str = Form("idle")) -> Response:
    """Write the request the helper is watching for.

    Refused when the helper is not installed rather than written and left to sit
    there: a file nothing is watching is a button that reports success and does
    nothing, which is the failure this codebase keeps cataloguing.
    """
    if not updates_service.helper_installed():
        return RedirectResponse(
            "/admin/updates?saved="
            + quote(i18n.t("The update helper is not installed on this host.")),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    state = updates_service.read()
    label = state.available.label if state.available else ""
    if when == "now":
        # Now still means backed up first: `updates_auto.apply` takes the backup
        # and only then writes the request.
        updates_auto.STATE.wanted = label
        problem = await updates_auto.apply(user.email)
        message = problem or i18n.t(
            "Backed up and requested. The service will restart in a moment."
        )
    else:
        updates_auto.want_now(label)
        message = i18n.t("It will be installed as soon as nothing is running and nobody is active.")
    return RedirectResponse(
        f"/admin/updates?saved={quote(message)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/cancel")
async def cancel(db: Db, user: AdminUser) -> Response:
    updates_service.clear_request()
    updates_auto.cancel()
    return RedirectResponse(
        f"/admin/updates?saved={quote(i18n.t('Request withdrawn.'))}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
