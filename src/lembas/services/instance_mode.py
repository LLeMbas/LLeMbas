"""API-only instance mode: one gate in front of every harness product.

An administrator can make an instance a pure API front -- the OpenAI-compatible
`/v1/*`, the `/api/v1/*` services, device login and the link -- with nothing a
person chats in. The setting is `instance_mode` in the general settings group
(`settings_store.api_only`).

**One gate, not forty checks.** The product is spread over a dozen routers
(chats, prototypes, the terminal, files, folders, memories, schedules, audio
for the composer, `/mcp`), and a check in each route is
a check somebody forgets in the next one -- the same argument
`services/audit.AdminAuditMiddleware` makes. So this is a pure ASGI middleware
keyed on path, and the lists below are the whole of what API-only mode means.

**Deny-listed, not allow-listed.** The things that must keep working are many
and scattered (sign-in, Settings and its POSTs, the whole Admin, static files,
health, branding, the manifest, `/v1`, `/api/v1`, device login, the link
WebSocket, tokens, push), and the things that are switched off are a known set
of prefixes. An allow list would lock somebody out of a route added next year
that has nothing to do with chatting -- including, in the worst case, the Admin
page that switches this back. A route under none of the prefixes below is never
touched, which is also why `/admin/general` cannot be gated by construction.

**Admins are not exempt.** The mode is what the instance *is*, not what one
person may do; an administrator wanting a chat switches the mode back.

**Cheap.** The setting is read only for a request that falls under one of the
prefixes -- one primary-key read of the `settings` row in a session of its own.
`/v1/*`, `/api/v1/*`, static files and every other route pay nothing. Not cached
across requests on purpose: a process-level cache would need forgetting by every
writer and by the test fixtures, and a single-row read is not worth that.

Pages redirect (303) to Settings with a notice; APIs answer 404 with one sentence
in `detail`; the terminal WebSocket is closed before it is accepted. Background
work -- the schedule ticker, `wake.wake_chat` and resuming interrupted replies at
startup -- checks `settings_store.api_only` where it starts, since nothing passes
through here for it.
"""

from __future__ import annotations

import logging

from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

log = logging.getLogger(__name__)

# Where a refused page lands. `saved=api-only` is a key the Settings page turns
# into its sentence, in the reader's language -- the language is not known yet
# at this point in the stack.
NOTICE_URL = "/settings?saved=api-only"

# Full pages that are the product. `/` is the home, which lands on the new-chat
# screen in full mode; here it lands on Settings.
PAGE_EXACT = ("/",)
PAGE_PREFIXES = (
    "/chat",  # the new-chat screen and every chat, ordinary and agent
    "/folders",  # a folder's edit page
    "/scheduled",
    # One box over chats and memories -- every one
    # of them switched off here, so the page would be a search of nothing.
    "/search",
)

# APIs behind those pages. Each prefix covers everything under it.
API_PREFIXES = (
    "/api/v2",  # the app's door: chats, the stream, cards, attachments, push
    "/api/chats",  # messaging, the stream, prototypes, the terminal panel and its WebSocket
    "/api/files",  # the composer's uploads and @-mentions
    "/api/folders",
    "/api/memories",  # what is remembered, from Settings
    "/api/schedules",
    "/api/audio",  # a reply read aloud, and dictation into the composer (/v1/audio stays)
    "/mcp",  # the memory tool served over MCP
)


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def classify(path: str) -> str:
    """"page", "api" or "" (not gated) for a request path."""
    if path in PAGE_EXACT or any(_under(path, p) for p in PAGE_PREFIXES):
        return "page"
    if any(_under(path, p) for p in API_PREFIXES):
        return "api"
    # A linked device's directory listing, which only the new-agent-chat screen
    # asks for, and the composer's `@` files and `/` commands from it.
    # The link itself (`/api/devices/link`) and device login stay.
    if path.startswith("/api/devices/") and path.endswith(("/browse", "/files", "/commands")):
        return "api"
    return ""


def refusal() -> str:
    """The one sentence an API caller is given, in the instance's language."""
    from lembas.web import i18n

    i18n.activate(i18n.instance_default())
    return i18n.t("This instance serves the API only; chats and scheduled tasks are switched off.")


def _api_only() -> bool:
    # Never raises: a settings row that cannot be read is not a reason to fail
    # every chat page, and "full" is what an instance was before this existed.
    try:
        from lembas.db.session import session_scope
        from lembas.services import settings_store

        with session_scope() as db:
            return settings_store.api_only(db)
    except Exception:
        log.debug("could not read the instance mode; treating it as full", exc_info=True)
        return False


class InstanceModeMiddleware:
    """Refuses the harness product while the instance is API-only. Pure ASGI, so
    a stream or a WebSocket that is allowed through passes untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        kind = classify(scope.get("path", ""))
        if not kind or not _api_only():
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            # Closed before it is accepted, which a client sees as a 403.
            await WebSocketClose(code=1008, reason="api-only")(scope, receive, send)
            return

        response: Response
        if kind == "page":
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            if headers.get("hx-request") == "true":
                # An htmx swap must not put Settings inside a fragment.
                response = Response(status_code=204, headers={"HX-Redirect": NOTICE_URL})
            else:
                response = RedirectResponse(NOTICE_URL, status_code=303)
        else:
            response = JSONResponse({"detail": refusal()}, status_code=404)
        await response(scope, receive, send)
