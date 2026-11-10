"""Search and memory, for an app.

**Search** is the web's search page said in JSON (`api/search.py` `run`):
the same stores, asked as their owner, offered by permission (memories need
`library.use`), chats found by their words and -- where the index is built --
by meaning. A hit's `snippet` is plain text, with `marks` the `[start, end)`
character ranges of the match in it: the app highlights, nothing here sends
markup. Chat titles and snippets may be model output (rule 6).

**Memory** is Settings → Memory: the person's own records, every data group,
added, changed and removed through `services/memories.py` -- the same limits,
the same exact-repeat rule. `library.use` for all of it.

| Route | |
|---|---|
| `GET /search?q=&in=&limit=` | `in`: all, chats, files, memories → `{q, in, offered, results}` |
| `GET /memories` | `{"memories": [memory], "groups": [{id, name}]}` |
| `POST /memories` | `{content, data_group_id?}` → 201 `{"memory"}` |
| `PATCH /memories/{id}` | `{content}` → `{"memory"}` |
| `DELETE /memories/{id}` | 204 |
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status
from fastapi.responses import Response
from markupsafe import Markup

from lembas.api import search as search_web
from lembas.api.deps import Db
from lembas.api.v2._common import Me, need
from lembas.db.models import AUTHOR_USER
from lembas.security import permissions
from lembas.services import data_groups
from lembas.services import memories as memories_service
from lembas.web.i18n import t

router = APIRouter()

MAX_QUERY = 200
LIMIT_DEFAULT = search_web.PREVIEW
LIMIT_MAX = search_web.FULL


# --- Search -------------------------------------------------------------------------------------
def plain(snippet: Any) -> tuple[str, list[list[int]]]:
    """The web's snippet (escaped text with `<mark>`) as plain text and the
    ranges that were marked."""
    raw = str(snippet or "")
    text, marks = "", []
    while True:
        start = raw.find("<mark>")
        if start < 0:
            text += Markup(raw).unescape()
            return text, marks
        text += Markup(raw[:start]).unescape()
        end = raw.find("</mark>", start)
        if end < 0:
            end = len(raw)
        inner = Markup(raw[start + len("<mark>"):end]).unescape()
        marks.append([len(text), len(text) + len(inner)])
        text += inner
        raw = raw[end + len("</mark>"):]


def hit(kind: str, row: dict[str, Any]) -> dict[str, Any]:
    if kind == "memories":
        # A memory's hit is its own text, marked: said once, as `snippet`.
        text, marks = plain(row.get("title"))
        return {"kind": "memory", "id": row.get("id"), "title": "", "snippet": text,
                "marks": marks, "archived": False}
    text, marks = plain(row.get("snippet"))
    out = {"kind": "chat" if kind == "chats" else "file", "title": str(row.get("title") or ""),
           "snippet": text, "marks": marks, "archived": bool(row.get("archived"))}
    if kind == "chats":
        out["id"] = row.get("id")
        out["agent"] = row.get("icon") == "terminal"
    else:
        out["path"] = row.get("path")
    return out


@router.get("/search")
async def search(
    caller: Me,
    db: Db,
    q: str = Query(""),
    within: str = Query("all", alias="in"),
    limit: int = Query(LIMIT_DEFAULT),
) -> dict[str, Any]:
    user = caller.user
    offered = search_web.allowed_types(permissions.resolve(db, user))
    chosen = within if within in offered else "all"
    needle = " ".join(q.split())[:MAX_QUERY]
    shown = max(1, min(int(limit), LIMIT_MAX))
    results: list[dict[str, Any]] = []
    if needle:
        kinds = offered if chosen == "all" else [chosen]
        found = await search_web.run(db, user, needle, kinds, shown + 1)
        results = [
            {
                "kind": kind,
                "hits": [hit(kind, row) for row in found.get(kind, [])[:shown]],
                "more": len(found.get(kind, [])) > shown,
            }
            for kind in kinds
            if found.get(kind)
        ]
    return {"q": needle, "in": chosen, "offered": offered, "results": results}


# --- Memory -------------------------------------------------------------------------------------
def memory(row, *, group_name: str) -> dict[str, Any]:
    """One record: its text, who wrote it (`user` or `model`), its data group."""
    from lembas.api.v2.serialise import stamp

    return {
        "id": row.id,
        "content": row.content,
        "author": row.author,
        "data_group": {"id": data_groups.group_of(row), "name": group_name},
        "created_at": stamp(row.created_at),
        "updated_at": stamp(row.updated_at),
    }


def _said(db, row) -> dict[str, Any]:
    return memory(row, group_name=data_groups.name_of(db, data_groups.group_of(row)))


def _refused(exc: ValueError) -> HTTPException:
    said = exc.translated() if isinstance(exc, memories_service.Refused) else str(exc)
    return HTTPException(status.HTTP_400_BAD_REQUEST, said)


def _owned(db, memory_id: str, user):
    row = memories_service.get(db, memory_id, user)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That memory no longer exists."))
    return row


@router.get("/memories")
async def list_memories(caller: Me, db: Db) -> dict[str, Any]:
    need(db, caller.user, "library.use")
    rows = memories_service.all_for(db, caller.user)
    groups = [{"id": g.id, "name": g.name} for g in data_groups.usable(db, caller.user)]
    return {"memories": [_said(db, row) for row in rows], "groups": groups}


@router.post("/memories", status_code=status.HTTP_201_CREATED)
async def add_memory(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    user = caller.user
    need(db, user, "library.use")
    wanted = str(body.get("data_group_id") or "").strip()
    group = wanted if wanted and data_groups.may_use(db, user, wanted) else (
        data_groups.DEFAULT_GROUP
    )
    try:
        row = memories_service.add(
            db, owner=user, content=str(body.get("content") or ""), author=AUTHOR_USER,
            group=group,
        )
    except ValueError as exc:
        raise _refused(exc) from exc
    return {"memory": _said(db, row)}


@router.patch("/memories/{memory_id}")
async def update_memory(
    caller: Me, db: Db, memory_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    need(db, caller.user, "library.use")
    row = _owned(db, memory_id, caller.user)
    try:
        memories_service.update(db, row, str(body.get("content") or ""))
    except ValueError as exc:
        raise _refused(exc) from exc
    return {"memory": _said(db, row)}


@router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(caller: Me, db: Db, memory_id: str) -> Response:
    need(db, caller.user, "library.use")
    memories_service.delete(db, _owned(db, memory_id, caller.user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["hit", "memory", "plain", "router"]
