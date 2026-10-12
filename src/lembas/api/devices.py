"""Finding this instance, and signing a device in to it.

- `GET /.well-known/lembas.json` -- what this is: LLeMbas, its version, the
  protocol and harness spec versions, and where to log in. Public on purpose:
  the LLeMbas CLI's `/login` asks for an address and nothing else, and this is how it
  tells a LLeMbas instance from any other web server before asking anybody to
  approve anything. It names no account and no model.
- `POST /api/device/code` and `POST /api/device/token` -- RFC 8628, for the
  device. No cookie and no token; the device code is the credential.
- `GET /device` and `POST /device` -- the page a signed-in person approves or
  denies a device on. The cookie door, like every page.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse, RedirectResponse, Response

from lembas import __version__
from lembas.api.deps import Db, RequiredUser
from lembas.security import api_tokens, permissions
from lembas.services import audit, harness_spec
from lembas.services import devices as devices_service
from lembas.web import i18n
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(tags=["devices"])

GRANT_DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"


def public_base(request: Request) -> str:
    """This instance's address as the caller reached it.

    The forwarded headers are honoured here because the answer is only ever
    handed back to whoever asked -- a spoofed one misleads only its own sender.
    """
    scheme = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip()
    scheme = scheme or request.url.scheme
    host = host or request.headers.get("host", "") or request.url.netloc
    return f"{scheme}://{host}"


@router.get("/.well-known/lembas.json")
async def discovery(request: Request) -> JSONResponse:
    from lembas.api.v1.services import connection_name
    from lembas.services import branding, device_link

    base = public_base(request)
    # The name is on the sign-in page already, so saying it here tells nobody
    # anything new; `connection` is what a device calls this instance.
    name = branding.snapshot().name
    return JSONResponse(
        {
            "service": "lembas",
            "version": __version__,
            "name": name,
            "connection": connection_name(name),
            # Still 1: an older LLeMbas CLI compares this for
            # equality with 1 and would refuse to log in to a 2. What protocol 2
            # added is offered by capability, and `protocols` says both are
            # spoken -- what a newer CLI reads.
            "protocol": 1,
            "protocols": list(device_link.PROTOCOLS),
            "harness_spec": harness_spec.version(),
            "base_url": base,
            "api": {
                "openai": f"{base}/v1",
                "admin": f"{base}/api/v1/admin",
                "instance": f"{base}/api/v1/instance",
            },
            "login": {
                "device": {
                    "code": f"{base}/api/device/code",
                    "token": f"{base}/api/device/token",
                    "verify": f"{base}/device",
                }
            },
            "scopes": list(api_tokens.USER_SCOPES),
        },
        headers={"Cache-Control": "no-store"},
    )


# Codes asked for, per address, in the last ten minutes. A device login is a
# thing a person does a few times a year; a stream of them is somebody fishing
# for a code that a careless click will approve.
_ASKED: dict[str, deque[float]] = {}
CODES_PER_TEN_MINUTES = 10


def _too_many(ip: str) -> bool:
    now = time.monotonic()
    window = _ASKED.setdefault(ip, deque())
    while window and window[0] <= now - 600:
        window.popleft()
    if len(window) >= CODES_PER_TEN_MINUTES:
        return True
    window.append(now)
    return False


async def _fields(request: Request) -> dict[str, Any]:
    """RFC 8628 posts a form; a program may as well send JSON. Both are read."""
    kind = request.headers.get("content-type", "")
    if "application/json" in kind:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        return body if isinstance(body, dict) else {}
    form = await request.form()
    return {key: value for key, value in form.items() if isinstance(value, str)}


def _oauth_error(error: str, description: str = "", status_code: int = 400) -> JSONResponse:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return JSONResponse(body, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.post("/api/device/code")
async def device_code(request: Request, db: Db) -> JSONResponse:
    ip = request.client.host if request.client else ""
    if _too_many(ip):
        return _oauth_error("slow_down", "Too many device logins from this address.", 429)
    fields = await _fields(request)
    scopes = str(fields.get("scope") or "").split() or [api_tokens.SCOPE_MODELS]
    row, raw = devices_service.start(
        db,
        client=str(fields.get("client_id") or fields.get("client") or ""),
        device_name=str(fields.get("device_name") or ""),
        device_host=str(fields.get("device_host") or ""),
        device_platform=str(fields.get("device_platform") or ""),
        scopes=scopes,
        ip=ip,
    )
    audit.record(
        "device.code",
        request=request,
        target_kind="device_code",
        target_id=row.id,
        summary=f"{row.device_name} ({row.client or 'unknown client'}) asked to sign in",
        detail={"host": row.device_host, "scopes": row.scopes_json},
    )
    base = public_base(request)
    shown = devices_service.shown(row.user_code)
    return JSONResponse(
        {
            "device_code": raw,
            "user_code": shown,
            "verification_uri": f"{base}/device",
            "verification_uri_complete": f"{base}/device?code={quote(shown)}",
            "expires_in": int(devices_service.LIFETIME.total_seconds()),
            "interval": row.interval,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/device/token")
async def device_token(request: Request, db: Db) -> JSONResponse:
    fields = await _fields(request)
    if fields.get("grant_type") != GRANT_DEVICE_CODE:
        return _oauth_error("unsupported_grant_type")
    result = devices_service.poll(db, str(fields.get("device_code") or ""))
    if result.error:
        return _oauth_error(result.error)
    token = result.token
    from lembas.db.models import User

    user = db.get(User, token.user_id)
    audit.record(
        "device.token",
        user=user,
        request=request,
        target_kind="token",
        target_id=token.id,
        summary=f"{token.name} was signed in",
        detail={"host": token.device_host, "scopes": token.scopes_json},
    )
    return JSONResponse(
        {
            "access_token": result.raw,
            "token_type": "Bearer",
            "scope": " ".join(token.scopes_json or []),
            "account": {"email": user.email, "name": user.name} if user else {},
            "base_url": public_base(request),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/api/v1/token")
async def sign_out(request: Request, db: Db) -> Response:
    """The token presented revokes itself: `lembas logout`. Any live token may,
    whatever its scopes -- giving up access needs none."""
    header = request.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    found = api_tokens.resolve(db, raw.strip()) if scheme.lower() == "bearer" else None
    if found is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That token is not valid.")
    token, user = found
    api_tokens.revoke(db, token)
    from lembas.api.tokens import _drop_link

    await _drop_link(token.id)
    audit.record(
        "token.revoke",
        user=user,
        token=token,
        request=request,
        target_kind="token",
        target_id=token.id,
        summary=f"{token.name!r} ({token.prefix}…) signed itself out",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.websocket("/api/devices/link")
async def device_link_socket(websocket: WebSocket) -> None:
    """A linked device dials in. Bearer token with the `link` scope, in
    the header; never a cookie, so no Origin to check -- a page cannot set the
    header. LLeMbas is the ACP client on it: see services/device_link.py."""
    import asyncio
    import contextlib

    from lembas.db.session import session_scope
    from lembas.services import device_link

    header = websocket.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    with session_scope() as db:
        found = api_tokens.resolve(db, raw.strip()) if scheme.lower() == "bearer" else None
        allowed = (
            found is not None
            and api_tokens.SCOPE_LINK in (found[0].scopes_json or [])
            and api_tokens.may_hold(db, found[1], api_tokens.SCOPE_LINK)
        )
        if found is not None:
            token, user = found
            token_id, user_id, name, email = token.id, user.id, token.name, user.email
    if not allowed:
        # Accepted and closed with the refusal code, so the device can tell "this
        # token is done" (stop dialling) from "the network dropped" (dial again).
        await websocket.accept()
        await websocket.close(code=device_link.CLOSE_REFUSED, reason="not allowed to link")
        return

    await websocket.accept()
    link = device_link.Link(
        token_id=token_id,
        user_id=user_id,
        name=name,
        send_text=websocket.send_text,
        close=lambda code, reason: websocket.close(code=code, reason=reason),
    )
    await device_link.register(link)
    # Before the reader starts, so before the device can announce anything: a
    # session deleted here while it was away is a tombstone again.
    device_link.tombstone_pending(link)
    audit.record("device.link", actor_label=email, summary=f"{name} linked",
                 target_kind="token", target_id=token_id)

    async def reader() -> None:
        try:
            while True:
                text = await websocket.receive_text()
                # One malformed message is that message's problem, not the
                # link's: a device sending a list where an object goes, or a
                # string where a number does, must not end the link and send
                # every reply in flight into the relink wait.
                try:
                    await link.receive(text)
                # ArithmeticError: `1e999` is valid JSON to Python and
                # reads as inf, which a conversion further in may refuse.
                except (AttributeError, TypeError, ValueError, KeyError, ArithmeticError):
                    log.warning("device %s sent a message that could not be read", name,
                                exc_info=True)
        except (WebSocketDisconnect, RuntimeError):
            return

    reading = asyncio.create_task(reader())
    try:
        # The device speaks first: hello, with its protocol and its limits.
        for _ in range(int(device_link.HELLO_WAIT * 10)):
            if link.hello or reading.done():
                break
            await asyncio.sleep(0.1)
        if int(link.hello.get("protocol") or 0) not in device_link.PROTOCOLS:
            await websocket.close(
                code=device_link.CLOSE_PROTOCOL,
                reason="this instance speaks device protocol "
                + " or ".join(str(p) for p in device_link.PROTOCOLS),
            )
            return
        await device_link.handshake(link)
        # The deletes it missed: beside the reading, never in its way --
        # each one waits on the device's answer, which the reader delivers.
        sending = asyncio.create_task(device_link.send_pending_deletes(link))
        await reading
        sending.cancel()
    except Exception:  # noqa: BLE001 - one device's link, not the app
        log.warning("device link for %s ended badly", name, exc_info=True)
    finally:
        reading.cancel()
        with contextlib.suppress(BaseException):
            await reading
        device_link.unregister(link)
        audit.record("device.unlink", actor_label=email, summary=f"{name} unlinked",
                     target_kind="token", target_id=token_id)


@router.get("/api/devices/{token_id}/browse")
async def browse_device(
    request: Request, db: Db, user: RequiredUser, token_id: str, path: str = ""
):
    """A directory on one of this person's linked devices, as the fragment the
    composer's directory dialog swaps in -- the SSH picker's twin.

    LLeMbas cannot look at a device's disk: it asks the device
    (`_lembas/directories`), which answers only inside its own remote roots and
    says of each directory whether it would open a session there. A device
    whose CLI does not know `_lembas/directories` answers "method not found";
    the dialog then says so and a typed path still works.
    """
    from lembas.db.models import TOKEN_DEVICE, ApiToken
    from lembas.services import device_link

    token = db.get(ApiToken, token_id)
    if (
        token is None
        or token.user_id != user.id
        or token.kind != TOKEN_DEVICE
        or device_link.refusal(db, user, token.id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("No such device."))
    answer: dict[str, Any] = {}
    error = ""
    link = device_link.link_for(token.id)
    if link is None:
        error = i18n.t("This device is not linked right now.")
    else:
        try:
            got = await link.request("_lembas/directories", {"path": path}, timeout=10)
            answer = got if isinstance(got, dict) else {}
        except device_link.DeviceError as exc:
            message = str(exc)
            older = i18n.t("This device's LLeMbas CLI is too old to list its directories. Type the path below.")  # noqa: E501
            error = older if "not found" in message.lower() else message
        except (device_link.LinkClosed, TimeoutError):
            error = i18n.t("The device did not answer.")
    here = str(answer.get("path") or "")
    entries = answer.get("entries") if here else answer.get("places")
    return render(
        request,
        "agents/_device_browse.html",
        {
            "here": here,
            "parent": str(answer.get("parent") or ""),
            "accepted": bool(answer.get("accepted", True)),
            "reason": str(answer.get("reason") or ""),
            "entries": [e for e in (entries or []) if isinstance(e, dict) and e.get("path")],
            "error": error or str(answer.get("error") or ""),
        },
    )


def _owned_device(db, user, token_id: str):
    """One of this person's device tokens that may run agent chats now, or 404."""
    from lembas.db.models import TOKEN_DEVICE, ApiToken
    from lembas.services import device_link

    token = db.get(ApiToken, token_id)
    if (
        token is None
        or token.user_id != user.id
        or token.kind != TOKEN_DEVICE
        or device_link.refusal(db, user, token.id)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such device.")
    return token


def _where(db, user, token_id: str, chat_id: str, cwd: str) -> dict[str, str]:
    """What a device question is about: the chat's session when it has
    one -- the device knows its root -- else the chat's directory, else the one
    the new-chat screen has chosen."""
    from lembas.db.models import Chat

    if chat_id:
        chat = db.get(Chat, chat_id)
        if chat is None or chat.user_id != user.id or chat.device_token_id != token_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That chat no longer exists.")
        if chat.device_session_id:
            return {"sessionId": chat.device_session_id}
        cwd = chat.project_dir or cwd
    return {"cwd": cwd} if cwd.strip() else {}


@router.get("/api/devices/{token_id}/files")
async def device_files(
    request: Request,
    db: Db,
    user: RequiredUser,
    token_id: str,
    q: str = "",
    chat_id: str = "",
    cwd: str = "",
):
    """What `@` offers from a linked device (capability `files`): the
    device's own file index, ranked as its terminal's `@` picker ranks it. A
    chosen file goes in as `@path` text -- the device reads it when it gets the
    prompt, so nothing is copied here. Empty, saying nothing, for a device that
    cannot answer; the composer still offers the rest."""
    from lembas.services import device_link

    _owned_device(db, user, token_id)
    where = _where(db, user, token_id, chat_id, cwd)
    link = device_link.link_for(token_id)
    files: list[dict[str, str]] = []
    error = ""
    if link is not None and link.can("files") and where:
        try:
            got = await link.request(
                "_lembas/files/search", {**where, "query": q[:200], "limit": 20}, timeout=5
            )
        except device_link.DeviceError as exc:
            error = str(exc)
        except (device_link.LinkClosed, TimeoutError):
            error = i18n.t("The device did not answer.")
        else:
            for entry in (got or {}).get("files") or []:
                path = str((entry or {}).get("path") or "") if isinstance(entry, dict) else ""
                if path and "\n" not in path and len(path) <= 1000:
                    kind = "dir" if entry.get("kind") == "dir" else "file"
                    files.append({"path": path, "kind": kind,
                                  "name": path.rstrip("/").rsplit("/", 1)[-1] or path})
    return render(
        request,
        "chat/_device_files.html",
        {
            "files": files[:50],
            "error": error,
            "device": device_link.short_name(link) if link else "",
        },
    )


@router.get("/api/devices/{token_id}/commands")
async def device_commands(
    db: Db, user: RequiredUser, token_id: str, chat_id: str = "", cwd: str = ""
) -> JSONResponse:
    """The `/` commands a linked device offers here (capability
    `commands`): the set its session last announced, or, for a composer with a
    directory and no session yet, the device's answer to `_lembas/commands`.
    Only the commands that make sense without its terminal ever come."""
    from lembas.services import device_link

    _owned_device(db, user, token_id)
    where = _where(db, user, token_id, chat_id, cwd)
    link = device_link.link_for(token_id)
    commands: list[dict[str, Any]] = []
    if link is not None and link.can("commands") and where:
        session = where.get("sessionId", "")
        if session and session in link.commands:
            commands = link.commands[session]
        else:
            try:
                got = await link.request("_lembas/commands", where, timeout=5)
                commands = device_link.clean_commands((got or {}).get("commands"))
            except (device_link.DeviceError, device_link.LinkClosed, TimeoutError):
                commands = []
            if session:
                link.commands[session] = commands
    return JSONResponse(
        {"commands": commands, "device": device_link.short_name(link) if link else ""},
        headers={"Cache-Control": "no-store"},
    )


def _may_approve(db, user) -> bool:
    return permissions.has(db, user, "api.use")


@router.get("/device")
async def device_page(request: Request, db: Db, user: RequiredUser, code: str = ""):
    row = devices_service.pending(db, code) if code else None
    return render(
        request,
        "device.html",
        {
            "code": devices_service.shown(code) if code else "",
            "row": row,
            "looked": bool(code),
            "allowed": _may_approve(db, user),
            "scope_label": api_tokens.scope_label,
        },
    )


@router.post("/device")
async def device_decide(
    request: Request,
    db: Db,
    user: RequiredUser,
    code: str = Form(...),
    decision: str = Form(...),
) -> Response:
    if not _may_approve(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, i18n.t("You may not sign devices in to this instance.")
        )
    row = devices_service.pending(db, code)
    if row is None:
        return RedirectResponse(
            f"/device?code={quote(code)}", status_code=status.HTTP_303_SEE_OTHER
        )
    if decision == "approve":
        devices_service.approve(db, row, user)
        audit.record(
            "device.approve",
            user=user,
            request=request,
            target_kind="device_code",
            target_id=row.id,
            summary=f"approved {row.device_name} on {row.device_host or 'an unnamed host'}",
            detail={"scopes": row.scopes_json, "from": row.ip},
        )
        message = i18n.t("Approved. The device is signing in now; you can close this page.")
    else:
        devices_service.deny(db, row)
        audit.record(
            "device.deny",
            user=user,
            request=request,
            target_kind="device_code",
            target_id=row.id,
            summary=f"denied {row.device_name}",
        )
        message = i18n.t("Denied. The device was not signed in.")
    return RedirectResponse(
        f"/settings?tab=devices&saved={quote(message)}#devices",
        status_code=status.HTTP_303_SEE_OTHER,
    )


__all__ = ["public_base", "router"]
