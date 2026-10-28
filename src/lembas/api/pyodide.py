"""The browser half of the `python` tool: a chat's open page runs it in Pyodide.

The protocol is `services/pages.py`'s: a page **announces** itself while it can
run Python (and again as a heartbeat), **withdraws** when it closes, **takes**
the next call by long poll and **answers** it. Every route is the cookie's door
and checks that the chat is the caller's own -- a page can only ever run code
for, and hand results to, a chat of the person whose browser it is.

Offered only when an administrator has switched it on (`pyodide_enabled`, off
by default) **and** Pyodide is vendored under `static/vendor/pyodide/`
(`available()`). Pyodide is CPython in WebAssembly, run in a worker in the
person's browser: nothing runs on this machine, and the code sees only the
files the tool hands it.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, status
from fastapi.responses import JSONResponse, Response

from lembas.api.deps import Db, RequiredUser
from lembas.db.models import Chat
from lembas.services import pages, settings_store
from lembas.web.i18n import t
from lembas.web.templating import STATIC_DIR

router = APIRouter(tags=["pyodide"])

VENDOR = STATIC_DIR / "vendor" / "pyodide"
TAKE_SECONDS = 25.0


def available() -> bool:
    """Whether Pyodide is on this server at all."""
    return (VENDOR / "pyodide.js").is_file()


def enabled(db) -> bool:
    return bool(settings_store.get_group(db, settings_store.GENERAL).get("pyodide_enabled"))


def offered(db, chat: Chat | None) -> bool:
    """Whether a chat page should load the runner."""
    return chat is not None and chat.kind != "agent" and enabled(db) and available()


def _chat(db, user, chat_id: str) -> Chat:
    chat = db.get(Chat, chat_id)
    if chat is None or chat.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That chat no longer exists."))
    if not offered(db, chat):
        raise HTTPException(status.HTTP_409_CONFLICT, t("Python is not switched on here."))
    return chat


def _page_id(body: dict) -> str:
    page_id = str(body.get("page_id") or "")
    if not (8 <= len(page_id) <= 64) or not page_id.replace("-", "").isalnum():
        raise HTTPException(status.HTTP_400_BAD_REQUEST)
    return page_id


@router.post("/chat/{chat_id}/page/announce")
async def announce(db: Db, user: RequiredUser, chat_id: str, body: dict = Body(...)) -> Response:
    chat = _chat(db, user, chat_id)
    pages.announce(chat.id, _page_id(body), {"python"})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/chat/{chat_id}/page/withdraw")
async def withdraw(db: Db, user: RequiredUser, chat_id: str, body: dict = Body(...)) -> Response:
    chat = db.get(Chat, chat_id)
    if chat is not None and chat.user_id == user.id:
        pages.withdraw(chat.id, _page_id(body))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/chat/{chat_id}/page/take")
async def take(db: Db, user: RequiredUser, chat_id: str, page: str) -> Response:
    chat = _chat(db, user, chat_id)
    page_id = _page_id({"page_id": page})
    chat_id = chat.id
    # Not holding the request's database session across the wait.
    db.close()
    request = await pages.take(chat_id, page_id, timeout=TAKE_SECONDS)
    if request is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return JSONResponse(request, headers={"Cache-Control": "no-store"})


@router.post("/chat/{chat_id}/page/answer")
async def answer(db: Db, user: RequiredUser, chat_id: str, body: dict = Body(...)) -> Response:
    chat = _chat(db, user, chat_id)
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    taken = pages.answer(chat.id, _page_id(body), str(body.get("call_id") or ""), result)
    return Response(status_code=status.HTTP_204_NO_CONTENT if taken else status.HTTP_404_NOT_FOUND)


def python_runner(chat: Chat | None) -> bool:
    """For the chat page's template: load the runner here?"""
    if chat is None or not available():
        return False
    from lembas.db.session import session_scope

    try:
        with session_scope() as db:
            return offered(db, chat)
    except Exception:  # a page must render even when this cannot be read
        return False


def _register() -> None:
    from lembas.web.templating import templates

    templates.env.globals["python_runner"] = python_runner


_register()

__all__ = ["available", "enabled", "offered", "python_runner", "router"]
