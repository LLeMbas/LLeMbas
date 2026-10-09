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
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status

from lembas.api.deps import Db, RequiredUser
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
        from lembas.db.models import ApiToken
        from lembas.services import device_link

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
            "profile_id": "",
            "label": device_link.short_name(link),
            "spec": {},
            "project_dir": chat.project_dir or "",
            "idle_timeout": float(values["terminal_idle_timeout"]),
            "max_sessions": int(values["terminal_max_sessions"]),
            "max_per_user": int(values["terminal_max_per_user"]),
            "integrate": False,
            "device_token_id": token_id,
        }

    return t("This chat is not on a linked device, so it has no shell to open."), {}


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
    if problem:
        await _refuse(websocket, problem)
        return

    try:
        session = await terminal_service.open_session(chat_id, cols=cols, rows=rows, **opening)
    except ExecError as exc:
        await _refuse(websocket, str(exc))
        return
    except Exception:  # noqa: BLE001 - a failure here is one socket, not the app
        log.exception("could not open a terminal for %s", owner_email)
        await _refuse(websocket, t("The shell could not be started."))
        return

    # Shaping a frame is this layer's job, not the session's; the session only
    # knows it finished something. Reassigned per socket and harmless: every
    # socket on this session would build the identical frame.
    session.on_command = lambda found: session.announce(
        json.dumps({"t": "command", "command": _command_frame(found)})
    )

    viewer = session.attach(cols, rows)
    await websocket.send_text(
        json.dumps(
            {
                "t": "ready",
                "label": session.label,
                "dir": session.project_dir,
                "cols": session.cols,
                "rows": session.rows,
                # Two tabs share one shell, and a size neither of them chose is
                # otherwise a mystery.
                "shared": len(session.viewers) > 1,
                # Whether this shell will tell us where commands begin and end,
                # which is what the Copy and Send buttons are made of.
                "integration": session.integration,
                "last": _command_frame(session.latest()),
                # A linked device's shell marks its own commands (capability
                # `shell`): the panel reads OSC 133 and 7 itself.
                "osc": bool(getattr(session, "osc", False)),
                "shell": getattr(session, "shell", ""),
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
        # The session is deliberately left running. Closing the panel, or
        # navigating away, is not "I am finished with this machine" -- a build
        # carries on and the scrollback is still there on the way back. The
        # idle timeout is what eventually ends it.
        session.detach(viewer)


@router.get("/{chat_id}/terminal/last")
async def last_command(db: Db, user: RequiredUser, chat_id: str) -> dict:
    """The last command and its output, rendered ready to paste.

    The *server* renders the text, so Copy and Send are a fetch and a
    clipboard write with no formatting logic in the browser -- and the block a
    model eventually reads exists in exactly one place. The panel's own screen
    buffer could not produce it anyway: it holds what is on screen, hard-wrapped
    at the terminal's width, with no way to tell a wrap from a newline.
    """
    if _chat_or_draft(db, user, chat_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That chat no longer exists."))
    if not permissions.has(db, user, "agent.terminal"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("You cannot open a terminal."))

    session = terminal_service.get(chat_id)
    found = session.latest() if session is not None else None
    if session is None or found is None:
        return {
            "ok": False,
            "message": t("Nothing has been run in this shell yet.")
            if session is not None
            else t("This terminal is not open."),
        }

    return {
        "ok": True,
        "command": found.command,
        "cwd": found.cwd,
        "exit": found.exit_status,
        "running": found.running,
        "summary": found.summary(),
        "text": found.as_text(label=session.label),
    }


def _command_frame(found) -> dict | None:
    """A finished command, small enough to push at every viewer.

    Tens of bytes, and deliberately *not* the output: a 64KB text frame would
    compete with PTY bytes on the one path that has to stay responsive, and the
    two buttons are pressed by a person, where a request is the natural shape.
    """
    if found is None:
        return None
    return {
        "seq": found.seq,
        "command": found.command,
        "cwd": found.cwd,
        "exit": found.exit_status,
        "running": found.running,
        "ms": found.duration_ms,
        "summary": found.summary(),
    }


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
        # A string in the queue is a control frame that had to keep its place
        # in the stream -- see `Session.announce`.
        if isinstance(chunk, str):
            await websocket.send_text(chunk)
            continue
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


async def _refuse(websocket: WebSocket, message: str) -> None:
    """Say why, then close. Sent as a frame because a browser cannot read a
    rejected handshake -- the reason would be lost exactly when it is needed."""
    with contextlib.suppress(Exception):
        await websocket.send_text(json.dumps({"t": "error", "message": message}))
    with contextlib.suppress(Exception):
        await websocket.close()
