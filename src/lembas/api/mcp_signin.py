"""Signing in to an MCP server with OAuth: the pages a person goes through.

Three steps, all on the cookie door. A page that says where the sign-in goes
(the address an app opens in a browser, too); a POST that starts it and names
the provider's page; and the address the provider sends the person back to.

The start answers with the address rather than a redirect from the form: the
content security policy keeps forms on this origin (`form-action 'self'`),
redirects included, so the browser is navigated by htmx or by a link.
"""

from __future__ import annotations

import logging
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from lembas.api._redirect import back
from lembas.api.deps import Db, RequiredUser, is_htmx
from lembas.api.devices import public_base
from lembas.db.models import McpServer, McpSignin, User
from lembas.security import permissions
from lembas.services import audit, tool_access
from lembas.services.fetch import FetchError
from lembas.services.mcp import oauth
from lembas.services.mcp import registry as mcp_registry
from lembas.services.mcp.protocol import McpError
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(tags=["mcp-signin"])

# Where a form may ask to be sent back to.
RETURNS = ("/settings?tab=security#mcp-signins", "/admin/mcp")


def signin_path(server: McpServer) -> str:
    return f"/settings/mcp/{server.id}/sign-in"


def offered_servers(db: Db, user: User) -> list[McpServer]:
    """The OAuth servers this person could use: the list their settings show."""
    if not permissions.has(db, user, "tools.mcp"):
        return []
    return [s for s in tool_access.visible_mcp_servers(db, user) if oauth.is_oauth(s)]


def _server(db: Db, user: User, server_id: str) -> McpServer:
    server = db.get(McpServer, server_id)
    if server is None or server.id not in {s.id for s in offered_servers(db, user)}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That server no longer exists."))
    return server


def _page(request: Request, db: Db, user: User, server: McpServer, **extra) -> Response:
    base = public_base(request)
    host = urlsplit(base).hostname or ""
    return render(
        request,
        "mcp_signin.html",
        {
            "server": server,
            "host": urlsplit(server.url or "").netloc,
            "auth": oauth.view(db, server, user),
            # Providers send a sign-in back to https or to this machine only.
            "plain_http": urlsplit(base).scheme != "https"
            and host not in ("localhost", "127.0.0.1", "::1"),
            "error": "",
            "go": "",
            "done": False,
            **extra,
        },
    )


@router.get("/settings/mcp/{server_id}/sign-in")
async def signin_page(
    request: Request, db: Db, user: RequiredUser, server_id: str, done: str = "", error: str = ""
) -> Response:
    return _page(
        request, db, user, _server(db, user, server_id), done=bool(done), error=error[:600]
    )


@router.post("/settings/mcp/{server_id}/sign-in")
async def signin_start(request: Request, db: Db, user: RequiredUser, server_id: str) -> Response:
    server = _server(db, user, server_id)
    if not oauth.may_sign_in(server, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            t("This server has one shared sign-in, which an administrator makes."),
        )
    try:
        go = await oauth.start(db, server, user, base=public_base(request))
    except (McpError, FetchError) as exc:
        if is_htmx(request):
            # Nothing is swapped on this form, so the reason is shown by the
            # page itself, reached again.
            return Response(
                status_code=status.HTTP_204_NO_CONTENT,
                headers={"HX-Redirect": f"{signin_path(server)}?error={quote(exc.message)}"},
            )
        return _page(request, db, user, server, error=exc.message)
    if is_htmx(request):
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"HX-Redirect": go})
    return _page(request, db, user, server, go=go)


@router.get(oauth.CALLBACK_PATH)
async def signin_callback(request: Request, db: Db, user: RequiredUser) -> Response:
    params = {key: str(value) for key, value in request.query_params.items()}
    try:
        server = await oauth.finish(db, user, params)
    except (McpError, FetchError) as exc:
        return render(
            request,
            "mcp_signin.html",
            {"server": None, "host": "", "auth": {}, "plain_http": False,
             "error": exc.message, "go": "", "done": False},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    audit.record(
        "mcp.signin",
        user=user,
        request=request,
        target_kind="mcp_server",
        target_id=server.id,
        summary=f"signed in to {server.name}"
        + (" (shared sign-in)" if server.oauth_shared else ""),
    )
    if not server.tools_json or user.is_admin:
        # The list of tools is the server's answer to a signed-in account,
        # so the first sign-in is the first moment it can be read.
        await mcp_registry.refresh(db, server, user)
    # Away from the address that carried the code.
    return RedirectResponse(f"{signin_path(server)}?done=1", status.HTTP_303_SEE_OTHER)


@router.post("/settings/mcp/{server_id}/sign-out")
async def signout(request: Request, db: Db, user: RequiredUser, server_id: str) -> Response:
    form = await request.form()
    target = str(form.get("next") or "")
    target = target if target in RETURNS else RETURNS[0]
    server = db.get(McpServer, server_id)
    if server is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That server no longer exists."))
    if server.oauth_shared and not user.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            t("This server has one shared sign-in, which an administrator makes."),
        )
    # By the row, not by what the person may see now: a sign-in to a server
    # since restricted or switched off is still theirs to end.
    owner = McpSignin.user_id.is_(None) if server.oauth_shared else McpSignin.user_id == user.id
    row = db.scalar(select(McpSignin).where(McpSignin.server_id == server.id, owner))
    if row is None:
        return back(target)
    revoked = await oauth.sign_out(db, row)
    audit.record(
        "mcp.signout",
        user=user,
        request=request,
        target_kind="mcp_server",
        target_id=server.id,
        summary=f"signed out of {server.name}",
    )
    if revoked:
        return back(target, t("Signed out of %(name)s, and its provider withdrew the sign-in.",
                              name=server.name))
    return back(target, t("Signed out of %(name)s.", name=server.name))


__all__ = ["offered_servers", "router", "signin_path"]
