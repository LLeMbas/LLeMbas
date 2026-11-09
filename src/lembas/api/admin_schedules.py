"""Scheduling administration: whether work may run on its own, and how much.

Everything here is clamped again in `settings_store.schedules` on the way out.
That is not belt and braces for its own sake: a value stored by an earlier
release, or edited into the database by hand, has to be survivable too, and the
same argument `agents` and `images` already make. What this page adds is telling
somebody *why* a number matters at the moment they change it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from lembas.api.deps import AdminUser, Db
from lembas.db.models import Schedule
from lembas.services import settings_store
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/schedules", tags=["admin-schedules"])


def _model_rows(db, user) -> list[dict]:
    """Every chat model, the scheduling list's first and in its order, then the
    rest: what the Models card draws."""
    from lembas.services import chat as chat_service

    listed = list(settings_store.schedules(db).get("models") or [])
    rows = chat_service.available_models(db, user)
    seen: set[str] = set()
    unique = []
    for row in rows:
        if row.model_id not in seen:
            seen.add(row.model_id)
            unique.append(row)
    ordered = sorted(
        unique,
        key=lambda m: (listed.index(m.model_id) if m.model_id in listed else len(listed), 0),
    )
    return [
        {
            "model_id": m.model_id,
            "label": m.display_name or m.model_id,
            "used": m.model_id in listed,
            "order": listed.index(m.model_id) + 1 if m.model_id in listed else "",
            "parallel": m.parallel_limit,
            "one_at_a_time": bool(getattr(m.connection, "one_model_at_a_time", False)),
        }
        for m in ordered
    ]


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
            "model_rows": _model_rows(db, user),
        },
    )


@router.post("")
async def save_schedules(request: Request, db: Db, user: AdminUser) -> Response:
    """The page's fields. Read from the form by hand: the Models card's rows come
    and go with the models, and a checkbox left unticked is simply absent."""
    form = await request.form()

    def number(name: str, default: int) -> int:
        try:
            return int(str(form.get(name) or default))
        except ValueError:
            return default

    enabled = str(form.get("enabled") or "") in ("true", "on", "1")
    used = {str(v) for v in form.getlist("models")}
    ids = [str(v) for v in form.getlist("model_ids")]
    orders = [str(v) for v in form.getlist("order")]
    ranked = []
    for index, model_id in enumerate(ids):
        if model_id not in used:
            continue
        raw = orders[index] if index < len(orders) else ""
        try:
            rank = int(raw)
        except ValueError:
            rank = 1000 + index
        ranked.append((rank, index, model_id))
    models = [model_id for _, _, model_id in sorted(ranked)]
    settings_store.update(
        db,
        {
            "enabled": enabled,
            "tick_seconds": number("tick_seconds", 30),
            "max_per_user": number("max_per_user", 20),
            "max_concurrent": number("max_concurrent", 3),
            "min_interval_seconds": number("min_interval_seconds", 60),
            "max_queued": number("max_queued", 3),
            "runs_days": number("runs_days", 90),
            "models": models,
        },
        key=settings_store.SCHEDULES,
    )
    log.info("scheduling %s by %s", "enabled" if enabled else "disabled", user.email)
    return RedirectResponse("/admin/schedules?saved=1", status_code=status.HTTP_303_SEE_OTHER)
