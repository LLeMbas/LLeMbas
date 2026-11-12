"""The socket behind the terminal panel.

A WebSocket rather than SSE, because SSE is one-directional and a terminal is
not: keystrokes have to go up, and an HTTP round trip per keypress is not a
terminal. It is the only WebSocket in LLeMbas, and it is worth saying what that
costs -- a cross-site page that could reach this endpoint would have a shell on
somebody's machine, not merely a copy of their chat. So there are two locks on
the door, and this module is mostly them.

**Where a refusal happens is load-bearing.** A browser tells a page nothing
about a handshake that *failed*: `new WebSocket()` fires `error` with no status
and no reason. So the socket is accepted first and the reason sent as a frame
for everything a person could act on -- no permission, the connection is
disabled, its host key was never confirmed -- and refused before accepting only
for the two cases where accepting is itself the risk.

It holds no database session. A dependency would keep one open for the hour a
shell sits at a prompt; `session_scope()` opens one for the authorisation and
closes it, exactly as `generation._run` does.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect

from lembas.api.deps import RequiredUser
from lembas.db.models import KIND_AGENT, Chat
from lembas.db.session import session_scope
from lembas.security import permissions
from lembas.security.sessions import COOKIE_NAME, resolve_session
from lembas.services import settings_store
from lembas.services.agent import terminal as terminal_service
from lembas.services.agent.base import ExecError
from lembas.web.i18n import N_, t

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["terminal"])
# The terminal before a chat exists: on a device, in a directory (`draft_terminal_socket`).
devices_router = APIRouter(prefix="/api/devices", tags=["terminal"])

# Nothing a keyboard produces is anywhere near this. Paste is the only thing
# that comes close, and a megabyte pasted into a shell is a mistake either way.
MAX_INPUT_BYTES = 256 * 1024

# 1008 is "policy violation", the closest thing the protocol has to "no".
CLOSE_POLICY = 1008

# What the far side is told when a shell ends, in words rather than a code.
CLOSED_WORDS = {
    terminal_service.CLOSED_EXITED: N_("The shell exited."),
    terminal_service.CLOSED_IDLE: N_("This terminal was closed after sitting idle."),
    terminal_service.CLOSED_SHUTDOWN: N_("LLeMbas restarted, so this shell was closed."),
    terminal_service.CLOSED_REVOKED: N_("The connection behind this terminal was closed."),
    terminal_service.CLOSED_ERROR: N_("The connection to the machine was lost."),
    terminal_service.CLOSED_ENDED: N_("This shell was ended."),
}


def _same_origin(websocket: WebSocket) -> bool:
    """Whether this handshake came from a page served by this site.

    Required, not merely checked when present. The session cookie is SameSite
    Lax, which already withholds it from a handshake a foreign page starts, and
    this is the belt to that brace -- an absent Origin is not a browser, and a
    non-browser client has no business here.
    """
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if not origin or not host:
        return False
    return urlsplit(origin).netloc.lower() == host.lower()


def _chat_or_draft(db, user, chat_id: str):
    """The chat this panel belongs to, if it is this person's."""
    chat = db.get(Chat, chat_id)
    return chat if chat is not None and chat.user_id == user.id else None


def _prepare(db, user, chat_id: str) -> tuple[str, dict]:
    """Everything that has to be true, and what opening needs. One or the other.

    Returns a message to show, or the arguments for `open_session`. The order is
    the order somebody would ask the questions in, and every "no" is a sentence
    rather than a silence.
    """
    if not permissions.has(db, user, "agent.terminal"):
        return t("You do not have permission to open a terminal."), {}

    chat = _chat_or_draft(db, user, chat_id)
    if chat is None:
        return t("That chat no longer exists."), {}
    if chat.kind != KIND_AGENT:
        return t("This is an ordinary chat, so it has no machine to open a shell on."), {}

    values = settings_store.agents(db)
    if not values.get("terminal_enabled", True):
        return t("The terminal is switched off on this instance."), {}

    # A chat on a linked device: the shell is the device's, asked for
    # over its link; the device decides whether it opens one, and where.
    token_id = getattr(chat, "device_token_id", None)
    if token_id:
        return _on_device(db, user, token_id, chat.project_dir or "")

    return t("This chat is not on a linked device, so it has no shell to open."), {}


# A directory as the new-chat screen sends it: of a length a path has. Where it
# may be -- and whether it is a path there at all -- is the device's to say (its
# remote roots), as for a chat's.
MAX_DIR_CHARS = 4096


def prepare_draft(db, user, token_id: str, project_dir: str) -> tuple[str, dict]:
    """`_prepare` before the chat exists: the computer and the directory the
    new-chat screen has chosen, under the same gates -- the permission, the
    instance's switch, and every check a chat on that device would pass."""
    if not permissions.has(db, user, "agent.terminal"):
        return t("You do not have permission to open a terminal."), {}
    if not settings_store.agents(db).get("terminal_enabled", True):
        return t("The terminal is switched off on this instance."), {}
    project_dir = (project_dir or "").strip()
    if not project_dir or len(project_dir) > MAX_DIR_CHARS or "\x00" in project_dir:
        return t("Choose a directory, and the terminal opens there."), {}
    return _on_device(db, user, token_id, project_dir)


def _on_device(db, user, token_id: str, project_dir: str) -> tuple[str, dict]:
    from lembas.db.models import ApiToken
    from lembas.services import device_link

    values = settings_store.agents(db)
    token = db.get(ApiToken, token_id)
    link = device_link.link_for(token_id)
    if token is None or token.user_id != user.id:
        return t("That device is not yours."), {}
    # A shell is an agent turn's reach and more, so it stands behind the
    # same gates as one.
    if refused := device_link.refusal(db, user, token_id):
        return refused, {}
    if link is None:
        device = token.device_host or token.name
        return t("%(device)s is not linked right now.", device=device), {}
    return "", {
        "owner_id": user.id,
        "label": device_link.short_name(link),
        "project_dir": project_dir,
        "idle_timeout": float(values["terminal_idle_timeout"]),
        "max_sessions": int(values["terminal_max_sessions"]),
        "max_per_user": int(values["terminal_max_per_user"]),
        "device_token_id": token_id,
    }


def availability(db, user, token_id: str) -> tuple[str, str]:
    """Whether a terminal can open on this device for this person now, and if
    not, which thing stands in the way and what to do about it: ("", "") or
    (code, sentence). The codes: `permission`, `instance` (switched off here),
    `unlinked` (the device is not connected now) and `device` (it is, and does
    not open terminals -- the CLI's `remote.terminal`, off unless switched on).

    The page's Terminal button and `GET /api/v2/devices` both say this, so a
    button that cannot work says why rather than not being there."""
    from lembas.db.models import ApiToken
    from lembas.services import device_link

    if not permissions.has(db, user, "agent.terminal"):
        return "permission", t("You do not have permission to open a terminal.")
    if not settings_store.agents(db).get("terminal_enabled", True):
        return "instance", t("The terminal is switched off on this instance.")
    token = db.get(ApiToken, token_id) if token_id else None
    link = device_link.link_for(token_id) if token_id else None
    if link is None:
        device = (token.device_host or token.name) if token is not None else t("the device")
        return "unlinked", t("%(device)s is not linked right now.", device=device)
    if not link.can("terminal"):
        return "device", t(
            "%(device)s does not open terminals. Switch it on there: in LLeMbas Desktop's "
            "settings, or with the LLeMbas CLI's remote.terminal setting.",
            device=device_link.short_name(link),
        )
    return "", ""


shells_router = APIRouter(prefix="/api/terminals", tags=["terminal"])


@shells_router.get("")
async def open_shells(user: RequiredUser) -> dict:
    """The caller's open shells: which computer, which folder, how long."""
    return {"shells": [terminal_service.describe(s) for s in terminal_service.shells_of(user.id)]}


@shells_router.post("/{shell_id}/end", status_code=204)
async def end_shell(user: RequiredUser, shell_id: str) -> Response:
    """End one of the caller's own shells. Somebody else's, or one already
    gone, is a 404 either way."""
    if not await terminal_service.end_shell(user.id, shell_id):
        raise HTTPException(404, t("That shell is no longer open."))
    return Response(status_code=204)


@router.websocket("/{chat_id}/terminal/ws")
async def terminal_socket(
    websocket: WebSocket,
    chat_id: str,
    cols: int = 80,
    rows: int = 24,
) -> None:
    if not _same_origin(websocket):
        await websocket.close(code=CLOSE_POLICY)
        return

    with session_scope() as db:
        user = resolve_session(db, websocket.cookies.get(COOKIE_NAME))
        if user is None:
            await websocket.close(code=CLOSE_POLICY)
            return
        problem, opening = _prepare(db, user, chat_id)
        owner_email = user.email

    await websocket.accept()
    await serve(websocket, chat_id, problem, opening, owner_email, cols=cols, rows=rows)


@devices_router.websocket("/{token_id}/terminal/ws")
async def draft_terminal_socket(
    websocket: WebSocket,
    token_id: str,
    directory: str = "",
    cols: int = 80,
    rows: int = 24,
) -> None:
    """The terminal before the chat: on the device and in the directory the
    new-chat screen has chosen. The first message that makes a chat there takes
    this shell over (`terminal_service.adopt`), so it carries on in the chat."""
    if not _same_origin(websocket):
        await websocket.close(code=CLOSE_POLICY)
        return

    with session_scope() as db:
        user = resolve_session(db, websocket.cookies.get(COOKIE_NAME))
        if user is None:
            await websocket.close(code=CLOSE_POLICY)
            return
        problem, opening = prepare_draft(db, user, token_id, directory)
        owner_id, owner_email = user.id, user.email

    await websocket.accept()
    key = terminal_service.draft_key(owner_id, token_id, directory.strip())
    await serve(websocket, key, problem, opening, owner_email, cols=cols, rows=rows)


async def serve(
    websocket: WebSocket,
    key: str,
    problem: str,
    opening: dict,
    owner_email: str,
    *,
    cols: int,
    rows: int,
) -> None:
    """An accepted socket: the refusal, or the shell under `key` (a chat's id,
    or a draft's key) followed both ways until either side goes. Shared by the
    page's door and the app's (`api/v2/terminal.py`)."""
    if problem:
        await _refuse(websocket, problem)
        return

    if key.startswith(terminal_service.DRAFT_PREFIX):
        await terminal_service.close_other_drafts(opening["owner_id"], key)
    opened_at = time.monotonic()
    try:
        session = await terminal_service.open_session(key, cols=cols, rows=rows, **opening)
    except terminal_service.TooManyShells as exc:
        # With the shells that are open, so one can be ended from here: the
        # limit used to be a sentence with no way to act on it.
        await _refuse(
            websocket,
            str(exc),
            shells=[
                terminal_service.describe(s)
                for s in terminal_service.shells_of(opening["owner_id"])
            ],
        )
        return
    except ExecError as exc:
        await _refuse(websocket, str(exc))
        return
    except Exception:  # a failure here is one socket, not the app
        log.exception("could not open a terminal for %s", owner_email)
        await _refuse(websocket, t("The shell could not be started."))
        return

    resumed = session.started_at < opened_at
    viewer = session.attach(cols, rows)
    await websocket.send_text(
        json.dumps(
            {
                "t": "ready",
                # What `POST /api/terminals/<id>/end` ends: the panel's End shell.
                "id": session.sid,
                # Whether the shell was already running (a second tab, a
                # reconnect that found it) or was opened by this socket.
                "resumed": resumed,
                "label": session.label,
                "dir": session.project_dir,
                "cols": session.cols,
                "rows": session.rows,
                # Two tabs share one shell, and a size neither of them chose is
                # otherwise a mystery.
                "shared": len(session.viewers) > 1,
                # A linked device's shell marks its own commands (capability
                # `shell`): the panel reads OSC 133 and 7 itself.
                "osc": bool(getattr(session, "osc", False)),
            }
        )
    )
    if viewer.snapshot:
        await websocket.send_bytes(viewer.snapshot)

    downward = asyncio.create_task(_to_browser(websocket, session, viewer))
    upward = asyncio.create_task(_from_browser(websocket, session, viewer))
    try:
        await asyncio.wait({downward, upward}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (downward, upward):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        # A chat's session is deliberately left running. Closing the panel, or
        # navigating away, is not "I am finished with this machine" -- a build
        # carries on and the scrollback is still there on the way back. The
        # idle timeout is what eventually ends it. A shell opened before its
        # chat is different: with nobody watching and no chat to come back to,
        # it ends shortly (`terminal_service.unwatched`).
        session.detach(viewer)
        await terminal_service.unwatched(key, session)


async def _to_browser(websocket: WebSocket, session, viewer) -> None:
    """Everything the shell says, plus the one frame that says it stopped."""
    while True:
        chunk = await viewer.queue.get()
        if chunk is None:
            reason = terminal_service.CLOSED_EXITED if viewer.dropped else session.closed_reason
            payload = {"t": "closed", "reason": reason, "message": _words(reason)}
            if viewer.dropped:
                # Not the session's doing: this browser stopped reading and was
                # disconnected so the others kept up. Reconnecting costs it
                # nothing, because the scrollback is the state.
                payload = {"t": "behind", "message": t("Reconnecting: output arrived faster than "
                           "this window could draw it.")}
            with contextlib.suppress(Exception):
                await websocket.send_text(json.dumps(payload))
            return
        await websocket.send_bytes(chunk)


async def _from_browser(websocket: WebSocket, session, viewer) -> None:
    """Keystrokes as binary, everything else as JSON.

    Binary for the hot path is what makes multi-byte characters safe: a read on
    the far side lands mid-sequence often enough to matter, and decoding each
    frame here would corrupt every boundary. Nothing decodes, so nothing splits.
    """
    while True:
        try:
            message = await websocket.receive()
        except WebSocketDisconnect:
            return
        if message["type"] == "websocket.disconnect":
            return

        data = message.get("bytes")
        if data is not None:
            if len(data) > MAX_INPUT_BYTES:
                continue
            await session.send(data)
            continue

        text = message.get("text")
        if text:
            _control(session, viewer, text)


def _control(session, viewer, text: str) -> None:
    try:
        payload = json.loads(text)
    except ValueError:
        return
    if not isinstance(payload, dict) or payload.get("t") != "resize":
        return
    session.resize(viewer, payload.get("cols", 80), payload.get("rows", 24))


def _words(reason: str) -> str:
    return t(CLOSED_WORDS.get(reason) or N_("This terminal closed."))


async def _refuse(websocket: WebSocket, message: str, **more) -> None:
    """Say why, then close. Sent as a frame because a browser cannot read a
    rejected handshake -- the reason would be lost exactly when it is needed.
    `more` rides on the frame: the person's open shells, when the reason is
    that they have too many."""
    with contextlib.suppress(Exception):
        await websocket.send_text(json.dumps({"t": "error", "message": message, **more}))
    with contextlib.suppress(Exception):
        await websocket.close()
