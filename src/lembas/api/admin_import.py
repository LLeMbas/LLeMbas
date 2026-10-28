"""Admin → Import: bringing a 1.x instance across.

Two doors, one report page:

- **From the 1.x database** (`lembas.db.old`, set aside by the update): the
  library -- notes, reports, skills, knowledge -- as files under `/Imported/`
  of the person chosen, in that person's organisation (`services/import_old.py`).
- **From an administrator's export file** (1.7's *Export the configuration*):
  connections, models, data groups and the service settings
  (`services/import_admin.py`).

Either answers with the report of what was taken and what was left, and why --
never a bare "done". The update never runs either.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from sqlalchemy import select

from lembas.api.deps import AdminUser, Db
from lembas.db.models import Organisation, User
from lembas.services import export_format, import_admin, import_old
from lembas.services.import_report import Report
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/import", tags=["admin-import"])


def _people(db) -> list[tuple[User, str]]:
    names = {o.id: o.name for o in db.scalars(select(Organisation))}
    users = db.scalars(select(User).where(User.active.is_(True)).order_by(User.name)).all()
    return [(u, names.get(u.org_id, "")) for u in users]


@router.get("")
async def import_page(request: Request, db: Db, user: AdminUser):
    survey, problem = None, ""
    if import_old.old_path().exists():
        try:
            survey = await asyncio.to_thread(import_old.survey)
        except import_old.Refused as refused:
            problem = refused.translated()
    return render(
        request,
        "admin/import.html",
        {
            "old_exists": import_old.old_path().exists(),
            "survey": survey,
            "problem": problem,
            "people": _people(db),
            "me": user,
        },
    )


def _report(request: Request, report: Report | None, problem: str = "", *, source: str):
    return render(
        request,
        "admin/import_report.html",
        {"report": report, "problem": problem, "source": source},
    )


@router.post("/old")
async def import_from_old(
    request: Request,
    db: Db,
    user: AdminUser,
    owner: str = Form(...),
    from_email: str = Form(""),
    memories: bool = Form(False),
):
    account = db.get(User, owner)
    if account is None:
        return _report(request, None, t("That account is not here."), source="old")
    try:
        report = import_old.run(db, account, from_email=from_email, memories=memories)
    except import_old.Refused as refused:
        db.rollback()
        return _report(request, None, refused.translated(), source="old")
    log.info("%s imported the 1.x library for %s", user.email, account.email)
    return _report(request, report, source="old")


@router.post("/file")
async def import_from_file(
    request: Request,
    db: Db,
    user: AdminUser,
    file: UploadFile = File(...),
    passphrase: str = Form(""),
):
    limit = export_format.MAX_FILE[export_format.KIND_ADMIN]
    raw = await file.read(limit + 1)
    try:
        document = import_admin.load(raw)
        report = import_admin.run(db, document, passphrase=passphrase)
    except export_format.Refused as refused:
        db.rollback()
        return _report(request, None, str(refused), source="file")
    log.info("%s imported an administrator's export from %s", user.email,
             document.get("app_version"))
    return _report(request, report, source="file")


__all__ = ["router"]
