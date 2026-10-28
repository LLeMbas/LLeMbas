"""Settings → Memory: export, import and forget-all, per data group.

The file is `lembas-export` kind `memory` version 1 (`services/memory_io.py`,
`LLeMbas.wiki/Export-format.md`). An import answers with a report page naming
what was taken and what was left and why, never with a bare "done".
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response

from lembas.api._redirect import back
from lembas.api.deps import Db, RequiredUser, require_permission
from lembas.api.pages import sidebar_context
from lembas.services import data_groups, memory_io
from lembas.web.i18n import t
from lembas.web.templating import render

router = APIRouter(dependencies=[Depends(require_permission("library.use"))], tags=["memories"])

BACK = "/settings?tab=memory"


@router.get("/api/memories/export")
async def export(db: Db, user: RequiredUser, group: str = "") -> Response:
    chosen = group if group and data_groups.may_use(db, user, group) else None
    document = memory_io.export(db, user, chosen)
    name = "lembas-memory" + (f"-{chosen}" if chosen else "") + ".json"
    return Response(
        json.dumps(document, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.post("/api/memories/import")
async def import_file(
    request: Request,
    db: Db,
    user: RequiredUser,
    file: UploadFile = File(...),
    into: str = Form(""),
):
    raw = await file.read(memory_io.MAX_FILE + 1)
    problem = ""
    report = None
    try:
        document = memory_io.load(raw)
        report = memory_io.import_into(db, user, document, into=into)
    except memory_io.Refused as refused:
        problem = str(refused)
    return render(
        request,
        "memory/import_report.html",
        {"problem": problem, "report": report, **sidebar_context(db, user)},
    )


@router.post("/api/memories/purge")
async def purge(db: Db, user: RequiredUser, group: str = Form(...)) -> Response:
    if not data_groups.may_use(db, user, group):
        return back(BACK, t("That data group is not one of yours."), ok=False)
    count = memory_io.purge(db, user, group)
    return back(BACK, t("Forgotten.") if count else t("There was nothing to forget."))


__all__ = ["router"]
