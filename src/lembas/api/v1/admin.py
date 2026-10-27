"""The administrator's REST API: accounts, usage and the audit log.

Read-only. Every route takes a bearer token with an admin scope
-- `admin:read` for accounts and usage, `admin:audit` for the log -- and never
the cookie, so a page open in a browser cannot reach it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select

from lembas.api.deps import Db
from lembas.db.models import Usage, User
from lembas.security.api_tokens import AdminReadCaller, AuditCaller
from lembas.services import audit as audit_service

router = APIRouter(prefix="/api/v1/admin", tags=["admin-api"])


def _user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "active": bool(user.active),
        "groups": [group.name for group in user.groups],
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.get("/users")
async def list_users(db: Db, caller: AdminReadCaller) -> dict[str, Any]:
    users = db.scalars(select(User).order_by(User.created_at))
    return {"data": [_user(user) for user in users]}


@router.get("/users/{user_id}")
async def get_user(db: Db, caller: AdminReadCaller, user_id: str) -> dict[str, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no such account.")
    return _user(user)


@router.get("/usage")
async def usage(db: Db, caller: AdminReadCaller, period: str = "") -> dict[str, Any]:
    """Tokens, replies, images and cost per account and model, for one month
    (`YYYY-MM`) or all. `cost_money` is in `currency`."""
    from lembas.services import pricing, settings_store

    query = select(Usage, User.email).join(User, User.id == Usage.user_id)
    if period:
        query = query.where(Usage.period == period)
    rows = db.execute(
        query.order_by(Usage.period.desc(), User.email, Usage.model_id)
    ).all()
    return {
        "currency": settings_store.currency(db),
        "data": [
            {
                "user_id": row.Usage.user_id,
                "email": row.email,
                "period": row.Usage.period,
                "model_id": row.Usage.model_id,
                "prompt_tokens": row.Usage.tokens_in,
                "cached_tokens": row.Usage.tokens_cached,
                "completion_tokens": row.Usage.tokens_out,
                "replies": row.Usage.requests,
                "images": row.Usage.images,
                "cost_money": round(pricing.micro_to_money(row.Usage.cost_micro), 6),
            }
            for row in rows
        ],
    }


def _when(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{raw!r} is not an ISO 8601 date or time."
        ) from exc


@router.get("/audit")
async def audit(
    db: Db,
    caller: AuditCaller,
    action: str = "",
    actor: str = "",
    since: str = "",
    until: str = "",
    limit: int = 200,
    offset: int = 0,
    format: str = "json",
):
    events = audit_service.query(
        db,
        action=action,
        actor=actor,
        since=_when(since),
        until=_when(until),
        limit=limit,
        offset=offset,
    )
    if format == "csv":
        return Response(
            audit_service.to_csv(events),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
        )
    return {"data": [audit_service.as_dict(event) for event in events]}


__all__ = ["router"]
