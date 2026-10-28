"""Putting an administrator's list in order by dragging (js/dnd.js).

One route for every ordered list rather than a pair of up/down routes per list:
the browser sends the ids of the rows it is showing, in their new order, and
the rows take the slots those same rows held before. Rows that were not on the
page -- the next page of a long list, or the rows a filter hid -- keep their
places, so dragging on page 2 of a filtered view never moves anything the
person could not see.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from lembas.api._redirect import back
from lembas.api.deps import AdminUser, Db
from lembas.db.models import Connection, CustomTool, McpServer, Model
from lembas.web.i18n import t

router = APIRouter(tags=["admin"])


def _ordered(db, kind: str) -> list:
    if kind == "models":
        return list(
            db.scalars(select(Model).join(Connection).order_by(Model.position, Model.model_id))
        )
    if kind == "tools":
        return list(db.scalars(select(CustomTool).order_by(CustomTool.position, CustomTool.slug)))
    if kind == "mcp":
        return list(db.scalars(select(McpServer).order_by(McpServer.position, McpServer.slug)))
    raise HTTPException(status.HTTP_404_NOT_FOUND)


def reorder(rows: list, wanted: list[str]) -> list:
    """`rows` in their new order: the rows named in `wanted` take, in that
    order, the slots the same rows held; every other row stays where it was.
    Unknown and repeated ids are ignored."""
    by_id = {row.id: row for row in rows}
    seen: set[str] = set()
    chosen = []
    for row_id in wanted:
        if row_id in by_id and row_id not in seen:
            seen.add(row_id)
            chosen.append(by_id[row_id])
    slots = [index for index, row in enumerate(rows) if row.id in seen]
    result = list(rows)
    for slot, row in zip(slots, chosen, strict=True):
        result[slot] = row
    return result


def _safe_back(raw: str, fallback: str) -> str:
    # Our own page only: a path, never a scheme or another host.
    if raw.startswith("/") and not raw.startswith("//") and "\\" not in raw:
        return raw
    return fallback


@router.post("/admin/order/{kind}")
async def put_in_order(request: Request, db: Db, user: AdminUser, kind: str) -> Response:
    form = await request.form()
    wanted = [part for part in str(form.get("ids", "")).split(",") if part.strip()]
    rows = reorder(_ordered(db, kind), [part.strip() for part in wanted])
    for position, row in enumerate(rows):
        row.position = position
    db.commit()
    fallback = {"models": "/admin/models", "tools": "/admin/tools", "mcp": "/admin/mcp"}[kind]
    return back(_safe_back(str(form.get("back", "")), fallback), t("Order saved."))


__all__ = ["reorder", "router"]
