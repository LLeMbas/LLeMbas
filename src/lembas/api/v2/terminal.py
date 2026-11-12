"""The terminal, for an app: the page's shell on a linked device, over the app's door.

Two WebSockets, the page's two (`api/terminal.py`) behind the app's token:

* `/api/v2/chats/{chat}/terminal/ws` -- an agent chat's shell, the one its page
  panel opens; the same session, so the page and the app share it.
* `/api/v2/devices/{device}/terminal/ws?directory=` -- before the chat exists,
  on the device and in the directory the new-chat screen has chosen. The first
  message that makes a chat there (`POST /chats`, or the page's) takes the shell
  over, so it carries on in the chat.

Both take `cols` and `rows`. Frames are the page's: the shell's output as binary;
keystrokes up as binary; `{"t": "resize", "cols", "rows"}` up as text; down as
text `{"t": "ready", "label", "dir", "cols", "rows", "shared", "osc"}`, then
`{"t": "closed" | "behind" | "error", "message"}` (an error is a refusal, in the
person's language, and the socket closes after it).

**The token is the only key.** `Authorization: Bearer` with the `app` scope, in
the handshake's header: never the cookie, so there is no Origin to check -- a
page cannot set the header, as for the device link. A missing or refused token
closes with 4401 after accepting, so a client can tell "sign in again" from
"the network dropped". Everything else is the page's gates, in the page's words.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, WebSocket

from lembas.api import terminal as page_terminal
from lembas.api.v2._common import Me
from lembas.db.session import session_scope
from lembas.security import api_tokens
from lembas.services.agent import terminal as terminal_service
from lembas.web import i18n

router = APIRouter()

# The token was missing, invalid, or may no longer use the app's door.
CLOSE_UNAUTHORIZED = 4401


def _caller(db, websocket: WebSocket):
    header = websocket.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        return None
    found = api_tokens.resolve(db, raw.strip())
    if found is None:
        return None
    token, user = found
    if api_tokens.SCOPE_APP not in (token.scopes_json or []):
        return None
    if not api_tokens.may_hold(db, user, api_tokens.SCOPE_APP):
        return None
    return user


async def _unauthorized(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.close(code=CLOSE_UNAUTHORIZED, reason="not allowed")


@router.websocket("/chats/{chat_id}/terminal/ws")
async def chat_terminal(websocket: WebSocket, chat_id: str, cols: int = 80, rows: int = 24):
    with session_scope() as db:
        user = _caller(db, websocket)
        if user is None:
            await _unauthorized(websocket)
            return
        i18n.activate(i18n.for_user(user))
        problem, opening = page_terminal._prepare(db, user, chat_id)
        email = user.email
    await websocket.accept()
    await page_terminal.serve(websocket, chat_id, problem, opening, email, cols=cols, rows=rows)


@router.websocket("/devices/{device_id}/terminal/ws")
async def draft_terminal(
    websocket: WebSocket, device_id: str, directory: str = "", cols: int = 80, rows: int = 24
):
    with session_scope() as db:
        user = _caller(db, websocket)
        if user is None:
            await _unauthorized(websocket)
            return
        i18n.activate(i18n.for_user(user))
        problem, opening = page_terminal.prepare_draft(db, user, device_id, directory)
        user_id, email = user.id, user.email
    await websocket.accept()
    key = terminal_service.draft_key(user_id, device_id, directory.strip())
    await page_terminal.serve(websocket, key, problem, opening, email, cols=cols, rows=rows)


@router.get("/terminals")
async def open_shells(caller: Me) -> dict:
    """The caller's open shells -- `{"shells": [{id, device, directory,
    seconds, chat, watched}]}` -- as the socket's `error` frame lists them when
    the person has as many open as they may (`shells` beside `message`)."""
    return {
        "shells": [
            terminal_service.describe(s) for s in terminal_service.shells_of(caller.user.id)
        ]
    }


@router.post("/terminals/{shell_id}/end", status_code=204)
async def end_shell(caller: Me, shell_id: str) -> Response:
    if not await terminal_service.end_shell(caller.user.id, shell_id):
        raise HTTPException(404, i18n.t("That shell is no longer open."))
    return Response(status_code=204)


__all__ = ["CLOSE_UNAUTHORIZED", "router"]
