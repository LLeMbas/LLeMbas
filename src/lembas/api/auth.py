"""Registration, sign-in and sign-out."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from lembas.api.deps import CurrentUser, Db
from lembas.config import settings
from lembas.db.models import ROLE_ADMIN, ROLE_USER, User
from lembas.security import hardening
from lembas.security.passwords import hash_password, validate_password
from lembas.security.sessions import COOKIE_NAME, create_session, resolve_session, revoke_session
from lembas.services import audit, settings_store
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _no_users_yet(db: Db) -> bool:
    return db.scalar(select(func.count()).select_from(User)) == 0


def _set_session_cookie(response: Response, token: str, request: Request | None = None) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.session_ttl,
        httponly=True,
        # Lax is what makes this application CSRF-safe without tokens: the
        # cookie is not sent on cross-site POSTs, and every mutating route here
        # is a POST. Do not relax to "none".
        #
        # One route is no longer a POST: the terminal WebSocket is a GET, and
        # what it opens is a shell. Lax still withholds the cookie from a
        # handshake a foreign page starts, so the attack is blocked -- but the
        # sentence above is no longer the whole story, which is why
        # `api/terminal.py` also *requires* a same-origin Origin header rather
        # than merely checking one when it happens to be there.
        samesite="lax",
        # Only over HTTPS when the deployment is not plain local http. Marking
        # it secure on http would silently break sign-in for a LAN install.
        # It has always meant "a network attacker on plain http can steal a
        # session"; with the terminal it also means they get a shell on the
        # machine behind that chat. See deploy/README.md.
        #
        # `Secure` whenever the browser came in over HTTPS --
        # directly or through a proxy saying so -- so the cookie is never sent
        # back over plain HTTP by a browser that has once used HTTPS here.
        secure=request is not None and hardening.is_https(request),
        path="/",
    )


def _safe_next(raw: str | None) -> str:
    """Reject open redirects: only same-origin absolute paths are allowed."""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return "/"
    return raw


def _login_page(request: Request, db: Db, *, status_code: int = 200, **context):
    """Render the sign-in page.

    Always goes through here so `allow_signup` reflects the *stored* setting
    rather than the environment default baked in by render(). Otherwise the
    "Create one" link would keep appearing after an administrator closed
    registration, offering a link that only leads to a refusal.
    """
    context.setdefault("next", "/")
    context["allow_signup"] = settings_store.signup_allowed(db)
    return render(request, "auth/login.html", context, status_code=status_code)


@router.get("/login")
async def login_form(request: Request, db: Db, user: CurrentUser, next: str = "/"):
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    # An empty database means this install has never been set up. Send the
    # first visitor straight to registration rather than to a login form they
    # cannot possibly satisfy.
    if _no_users_yet(db):
        return RedirectResponse("/auth/register", status_code=status.HTTP_303_SEE_OTHER)
    return _login_page(request, db, next=_safe_next(next))


@router.post("/login")
async def login(
    request: Request,
    db: Db,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    email = email.strip().lower()
    address = request.client.host if request.client else ""

    # Throttled per email and per address (security/hardening.py): a wait is
    # refused before the password is even checked, so guessing gets no faster
    # by being wrong quickly.
    wait = hardening.sign_in_wait(email, address)
    if wait:
        audit.record(
            "auth.login_throttled", request=request, actor_label=email,
            summary=f"sign-in for {email} refused: too many failures",
        )
        return _login_page(
            request,
            db,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error=t(
                "Too many failed attempts. Try again in %(seconds)s seconds.",
                seconds=int(wait) + 1,
            ),
            email=email,
            next=_safe_next(next),
        )

    # Through the identity seam: the instance's own accounts today, an
    # external backend later -- see security/identity.py.
    from lembas.security import identity

    user = identity.provider().authenticate(db, email, password)

    # One message for "no such account" and "wrong password" alike, so the form
    # cannot be used to discover which addresses are registered.
    if user is None:
        log.info("failed sign-in for %s", email)
        hardening.sign_in_failed(email, address)
        audit.record(
            "auth.login_failed", request=request, actor_label=email,
            summary=f"failed sign-in for {email}",
        )
        return _login_page(
            request,
            db,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error=t("That email and password do not match."),
            email=email,
            next=_safe_next(next),
        )

    if not user.active:
        return _login_page(
            request,
            db,
            status_code=status.HTTP_403_FORBIDDEN,
            error=t("This account has been deactivated. Ask an administrator."),
            email=email,
            next=_safe_next(next),
        )

    hardening.sign_in_succeeded(email)
    token = create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=request.client.host if request.client else "",
    )
    audit.record("auth.login", user=user, request=request, summary=f"{email} signed in")
    response = RedirectResponse(_safe_next(next), status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, token, request)
    return response


@router.get("/register")
async def register_form(request: Request, db: Db, user: CurrentUser):
    if user is not None:
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    first_run = _no_users_yet(db)
    if not first_run and not settings_store.signup_allowed(db):
        return _login_page(
            request,
            db,
            status_code=status.HTTP_403_FORBIDDEN,
            error=t("Registration is closed. Ask an administrator for an account."),
        )
    return render(request, "auth/register.html", {"first_run": first_run})


@router.post("/register")
async def register(
    request: Request,
    db: Db,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
):
    first_run = _no_users_yet(db)
    if not first_run and not settings_store.signup_allowed(db):
        return _login_page(
            request,
            db,
            status_code=status.HTTP_403_FORBIDDEN,
            error=t("Registration is closed. Ask an administrator for an account."),
        )

    name = name.strip()
    email = email.strip().lower()

    def fail(message: str) -> Response:
        return render(
            request,
            "auth/register.html",
            {"error": message, "name": name, "email": email, "first_run": first_run},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not name:
        return fail(t("Please enter a name."))
    if "@" not in email or "." not in email.split("@")[-1]:
        return fail(t("Please enter a valid email address."))
    if (problem := validate_password(password)) is not None:
        return fail(problem)
    if db.scalar(select(User).where(User.email == email)) is not None:
        return fail(t("An account with that email already exists."))

    # Whoever sets the instance up owns it. Everyone after that is a plain user
    # until an admin says otherwise.
    user = User(
        name=name,
        email=email,
        password_hash=hash_password(password),
        role=ROLE_ADMIN if first_run else ROLE_USER,
    )
    db.add(user)
    db.commit()
    log.info("registered %s as %s", email, user.role)
    audit.record(
        "auth.register", user=user, request=request, summary=f"{email} registered as {user.role}"
    )

    token = create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent", ""),
        ip_address=request.client.host if request.client else "",
    )
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookie(response, token, request)
    return response


@router.post("/logout")
async def logout(request: Request, db: Db):
    who = resolve_session(db, request.cookies.get(COOKIE_NAME))
    if who is not None:
        audit.record("auth.logout", user=who, request=request, summary=f"{who.email} signed out")
    revoke_session(db, request.cookies.get(COOKIE_NAME))
    response = RedirectResponse("/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
