"""Shared FastAPI dependencies: database sessions and the current user."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DBSession
from starlette.requests import HTTPConnection

from lembas.db.models import User
from lembas.db.session import get_session_factory
from lembas.security.sessions import COOKIE_NAME, resolve_session
from lembas.web import i18n


def get_db() -> Iterator[DBSession]:
    """One database session per request, always closed."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


Db = Annotated[DBSession, Depends(get_db)]


async def get_current_user(conn: HTTPConnection, db: Db) -> User | None:
    """Resolve the session cookie to a user, or None when signed out.

    ⚠ `async def`, and that is load-bearing rather than tidy. FastAPI runs a
    *sync* dependency in a threadpool, and `i18n.activate` below sets a
    `ContextVar` -- which anyio copies **into** the thread and discards on the way
    out, so the language was set in a context nothing else could see and every
    page rendered in English however anybody's preference was stored. An async
    dependency is awaited in the request's own task, where the value survives to
    the render.

    What it costs is one indexed SELECT on the event loop rather than in a
    thread, which is what every route in this application already does with its
    session.

    Cached on the connection's state so several dependencies in one request do
    not each hit the sessions table.

    `HTTPConnection` rather than `Request` because the terminal panel is a
    WebSocket, and FastAPI injects a `WebSocket` there -- annotating this
    `Request` fails at *connect* time rather than at import, so it would pass
    every smoke test and break in a browser. `HTTPConnection` is the base of
    both and carries the cookies and the state either way.
    """
    cached = getattr(conn.state, "user", None)
    if cached is not None:
        return cached
    user = resolve_session(db, conn.cookies.get(COOKIE_NAME))
    conn.state.user = user
    # The language this request renders in, set here because this is where the
    # person is already known -- no second session and no second cookie read. A
    # request that never resolves a user keeps whatever `LanguageMiddleware` set,
    # which is the instance default.
    i18n.activate(i18n.for_user(user))
    return user


CurrentUser = Annotated[User | None, Depends(get_current_user)]


class RedirectToLogin(HTTPException):
    """Signals "not signed in" so the exception handler can redirect a browser.

    Raised instead of returning a response because dependencies cannot return
    one. lembas.main turns this into a 303 for page loads and an HX-Redirect
    header for HTMX requests, so a partial swap never renders a login form
    inside the chat pane.
    """

    def __init__(self, next_url: str = "/") -> None:
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
        self.next_url = next_url


def require_user(request: Request, user: CurrentUser) -> User:
    if user is None:
        raise RedirectToLogin(next_url=request.url.path)
    return user


RequiredUser = Annotated[User, Depends(require_user)]


def require_admin(user: RequiredUser) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=i18n.t("This area is restricted to administrators."),
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]


def require_permission(key: str):
    """Dependency factory guarding a route behind a named permission.

        @router.post("", dependencies=[Depends(require_permission("chat.create"))])

    Administrators always pass; see lembas.security.permissions for why.
    """

    def guard(db: Db, user: RequiredUser) -> User:
        from lembas.security import permissions

        if not permissions.has(db, user, key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=i18n.t("You do not have permission to do that."),
            )
        return user

    return guard


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def login_redirect(next_url: str = "/") -> RedirectResponse:
    target = "/auth/login"
    if next_url and next_url not in ("/", "/auth/login"):
        from urllib.parse import quote

        target = f"{target}?next={quote(next_url, safe='')}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
