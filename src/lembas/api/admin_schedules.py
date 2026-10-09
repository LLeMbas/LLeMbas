"""Scheduling administration: whether work may run on its own, and how much.

Everything here is clamped again in `settings_store.schedules` on the way out.
That is not belt and braces for its own sake: a value stored by an earlier
release, or edited into the database by hand, has to be survivable too, and the
same argument `agents` and `images` already make. What this page adds is telling
somebody *why* a number matters at the moment they change it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from lembas.api.deps import AdminUser, Db
from lembas.db.models import Schedule
from lembas.services import settings_store
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/schedules", tags=["admin-schedules"])


@router.get("")
async def schedules_page(request: Request, db: Db, user: AdminUser, saved: bool = False):
    total = int(db.scalar(select(func.count()).select_from(Schedule)) or 0)
    active = int(
        db.scalar(
            select(func.count()).select_from(Schedule).where(Schedule.enabled.is_(True))
        )
        or 0
    )
    return render(
        request,
        "admin/schedules.html",
        {
            "values": settings_store.schedules(db),
            # Shown because turning the switch off does not delete anything, and
            # an administrator who has just done so should be able to see what
            # has stopped rather than infer it.
            "total": total,
            "active": active,
            "saved": saved,
        },
    )


@router.post("")
async def save_schedules(
    db: Db,
    user: AdminUser,
    enabled: bool = Form(False),
    tick_seconds: int = Form(30),
    max_per_user: int = Form(20),
    max_concurrent: int = Form(3),
    min_interval_seconds: int = Form(60),
    max_queued: int = Form(3),
) -> Response:
    settings_store.update(
        db,
        {
            "enabled": enabled,
            "tick_seconds": tick_seconds,
            "max_per_user": max_per_user,
            "max_concurrent": max_concurrent,
            "min_interval_seconds": min_interval_seconds,
            "max_queued": max_queued,
        },
        key=settings_store.SCHEDULES,
    )
    log.info("scheduling %s by %s", "enabled" if enabled else "disabled", user.email)
    return RedirectResponse("/admin/schedules?saved=1", status_code=status.HTTP_303_SEE_OTHER)
