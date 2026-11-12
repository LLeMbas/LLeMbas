"""The `python` tool's runner, for an app.

Python runs where the person is: in Pyodide, in the page of the chat
(`api/pyodide.py`, `services/pages.py`). These are the same four calls for an
app that brings its own runner -- LLeMbas Desktop's window -- on the same
registry, so a chat open in the app is a chat with a page, and the files the
runner needs, from the same verified copy the page loads.

| Route | |
|---|---|
| `POST /chats/{id}/page/announce {page_id}` | this runner is here (again every 20 s) → 204 |
| `POST /chats/{id}/page/withdraw {page_id}` | it is closing → 204 |
| `GET /chats/{id}/page/take?page=` | long poll: 200 the call, 204 nothing yet, 410 announce again |
| `POST /chats/{id}/page/answer {page_id, call_id, result}` | 204; 404 for another runner's call |
| `GET /pyodide/{version}/{name}` | one file of the pinned Pyodide release |

A call is `{call_id, code, files: [{path, content_b64}], save: [path],
limits: {seconds, memory, output_chars}}`; its `result`
`{stdout, result, error, files: [{path, content_b64}]}`. `page_id` is the
runner's own random id, 8 to 64 letters, digits and hyphens. Where Python is
not switched on, or the chat is an agent chat (its code runs on its device),
the chat routes answer **409**. `GET /me` says whether it is on, which version,
and the files with their SHA-256 and sizes (`python`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status
from fastapi.responses import Response

from lembas.api import pyodide as page_routes
from lembas.api.deps import Db
from lembas.api.v2._common import Me, owned_chat
from lembas.services import pages, pyodide_files
from lembas.web.i18n import t

router = APIRouter()


def _chat(db, caller, chat_id: str):
    chat = owned_chat(db, chat_id, caller.user)
    if not page_routes.offered(db, chat):
        raise HTTPException(status.HTTP_409_CONFLICT, t("Python is not switched on here."))
    return chat


@router.post("/chats/{chat_id}/page/announce", status_code=status.HTTP_204_NO_CONTENT)
async def announce(
    caller: Me, db: Db, chat_id: str, body: dict[str, Any] = Body(...)
) -> Response:
    chat = _chat(db, caller, chat_id)
    pages.announce(chat.id, page_routes._page_id(body), {"python"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chats/{chat_id}/page/withdraw", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw(
    caller: Me, db: Db, chat_id: str, body: dict[str, Any] = Body(...)
) -> Response:
    chat = owned_chat(db, chat_id, caller.user)
    pages.withdraw(chat.id, page_routes._page_id(body))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/chats/{chat_id}/page/take")
async def take(caller: Me, db: Db, chat_id: str, page: str = Query(...)) -> Response:
    chat = _chat(db, caller, chat_id)
    page_id = page_routes._page_id({"page_id": page})
    found = chat.id
    # Not holding the request's database session across the wait.
    db.close()
    return await page_routes.take_response(found, page_id)


@router.post("/chats/{chat_id}/page/answer")
async def answer(
    caller: Me, db: Db, chat_id: str, body: dict[str, Any] = Body(...)
) -> Response:
    chat = _chat(db, caller, chat_id)
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    taken = pages.answer(
        chat.id, page_routes._page_id(body), str(body.get("call_id") or ""), result
    )
    return Response(
        status_code=status.HTTP_204_NO_CONTENT if taken else status.HTTP_404_NOT_FOUND
    )


@router.get("/pyodide/{version}/{name}")
async def pyodide_file(caller: Me, version: str, name: str) -> Response:
    """One file of the pinned release, as `/vendor/pyodide/…` serves it to the
    page: a name the lock lists, of the pinned version, once verified."""
    return await page_routes.pyodide_file(version, name)


def state(db) -> dict[str, Any]:
    """Python on this instance, for `GET /me`: whether a chat's runner is
    wanted at all, and what it loads."""
    on = bool(page_routes.enabled(db) and page_routes.available())
    return {
        "enabled": on,
        "version": pyodide_files.version() if on else "",
        "files": [
            {"name": name, "sha256": str(entry.get("sha256") or ""),
             "size": int(entry.get("size") or 0)}
            for name, entry in sorted(pyodide_files.files().items())
        ] if on else [],
    }


__all__ = ["router", "state"]
