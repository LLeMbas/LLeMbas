"""Chat folders, for an app: made, renamed, moved and deleted.

The same rules as the web's (`api/folders.py`): `folder.manage` for all of it,
only the person's own folders, nesting at most `MAX_DEPTH` deep, never into
itself, and deleting a folder takes its child folders but never a chat (the
chats fall back to no folder). A chat is moved with `PATCH /chats/{id}`
`{"folder_id"}` (null or "" for no folder), as the web's drag does.

| Route | |
|---|---|
| `GET /folders` | `{"folders": [folder]}` (`misc.py`) |
| `POST /folders` | `{name, parent_id?}` → 201 `{"folder"}` |
| `PATCH /folders/{id}` | any of `name`, `parent_id` (null: top level), `collapsed` → `{"folder"}` |
| `DELETE /folders/{id}` | 204 |
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, status
from fastapi.responses import Response

from lembas.api import folders as folders_web
from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import Me, need
from lembas.db.models import Folder
from lembas.web.i18n import t

router = APIRouter()

MAX_NAME = 200


def _name(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_NAME]


def _deep() -> HTTPException:
    return HTTPException(
        status.HTTP_400_BAD_REQUEST,
        t("Folders cannot be nested more than %(n)s deep.", n=folders_web.MAX_DEPTH),
    )


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_folder(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    user = caller.user
    need(db, user, "folder.manage")
    name = _name(body.get("name"))
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, t("A folder needs a name."))
    parent_id = str(body.get("parent_id") or "")
    parent = folders_web._owned_folder(db, parent_id, user.id) if parent_id else None
    if parent is not None and folders_web._depth_of(db, parent) >= folders_web.MAX_DEPTH:
        raise _deep()
    folder = Folder(user_id=user.id, name=name, parent_id=parent.id if parent else None)
    db.add(folder)
    db.commit()
    return {"folder": serialise.folder(folder)}


@router.patch("/folders/{folder_id}")
async def update_folder(
    caller: Me, db: Db, folder_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    user = caller.user
    need(db, user, "folder.manage")
    folder = folders_web._owned_folder(db, folder_id, user.id)
    if "name" in body:
        name = _name(body.get("name"))
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, t("A folder needs a name."))
        folder.name = name
    if "parent_id" in body:
        parent_id = str(body.get("parent_id") or "")
        parent = folders_web._owned_folder(db, parent_id, user.id) if parent_id else None
        cursor = parent
        while cursor is not None:
            if cursor.id == folder.id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, t("A folder cannot be moved inside itself.")
                )
            cursor = db.get(Folder, cursor.parent_id) if cursor.parent_id else None
        height = folders_web._subtree_height(db, folder)
        if parent is not None and folders_web._depth_of(db, parent) + height > (
            folders_web.MAX_DEPTH
        ):
            raise _deep()
        folder.parent_id = parent.id if parent else None
    if "collapsed" in body:
        folder.collapsed = bool(body.get("collapsed"))
    db.commit()
    return {"folder": serialise.folder(folder)}


@router.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(caller: Me, db: Db, folder_id: str) -> Response:
    user = caller.user
    need(db, user, "folder.manage")
    folder = folders_web._owned_folder(db, folder_id, user.id)
    db.delete(folder)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
