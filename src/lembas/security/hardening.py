"""The security pass: sign-in throttling, the Origin check, and headers.

Each of these was a finding of the audit written up on the wiki's
*Security-audit* page, and each is small on purpose -- a security measure that
is hard to read is one nobody can tell is still working.

**Sign-in throttling.** Failures are counted per address and per email, in
memory (one worker serves every request). After `FREE_FAILURES` failures the
next attempt must wait, doubling from `FIRST_WAIT` up to `LONGEST_WAIT`; a
success clears the email's count. A wait is refused with the same page and a
sentence saying how long, never with a silent drop.

**The Origin check.** `SameSite=Lax` already withholds the cookie from a
cross-site POST. This is the belt to that brace: a state-changing request that
carries the session cookie *and* an `Origin` (or, failing that, a `Referer`) from
another host is refused with 403. A request with neither header is a program,
not a browser, and SameSite still covers it. The bearer-token routes are not
checked -- they never read the cookie.

**Headers.** Every response gets `X-Content-Type-Options: nosniff` and
`Referrer-Policy: same-origin`; every HTML page without a policy of its own gets
a Content-Security-Policy that loads nothing from anywhere but this instance and
lets nothing frame it. The prototype route sets its own, sealed policy, and is
left alone -- an application-wide policy over it would stop every prototype
loading.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from lembas.security.sessions import COOKIE_NAME

FREE_FAILURES = 5
FIRST_WAIT = 2.0
LONGEST_WAIT = 900.0
# An address sees more people than an email does (a household, an office), so it
# gets more room before it waits.
FREE_FAILURES_PER_ADDRESS = 20
FORGET_AFTER = 3600.0


@dataclass
class _Failures:
    count: int = 0
    last: float = 0.0
    until: float = 0.0


@dataclass
class Throttle:
    free: int
    entries: dict[str, _Failures] = field(default_factory=dict)

    def wait(self, key: str, *, now: float | None = None) -> float:
        """Seconds this key must still wait before another attempt, or 0."""
        now = now if now is not None else time.monotonic()
        entry = self.entries.get(key)
        if entry is None:
            return 0.0
        if now - entry.last > FORGET_AFTER:
            self.entries.pop(key, None)
            return 0.0
        return max(0.0, entry.until - now)

    def failed(self, key: str, *, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        entry = self.entries.setdefault(key, _Failures())
        if now - entry.last > FORGET_AFTER:
            entry.count = 0
        entry.count += 1
        entry.last = now
        over = entry.count - self.free
        if over > 0:
            entry.until = now + min(LONGEST_WAIT, FIRST_WAIT * (2 ** (over - 1)))

    def succeeded(self, key: str) -> None:
        self.entries.pop(key, None)


BY_EMAIL = Throttle(free=FREE_FAILURES)
BY_ADDRESS = Throttle(free=FREE_FAILURES_PER_ADDRESS)


def sign_in_wait(email: str, address: str) -> float:
    return max(BY_EMAIL.wait(email.lower()), BY_ADDRESS.wait(address))


def sign_in_failed(email: str, address: str) -> None:
    BY_EMAIL.failed(email.lower())
    BY_ADDRESS.failed(address)


def sign_in_succeeded(email: str) -> None:
    BY_EMAIL.succeeded(email.lower())


def reset_throttles() -> None:
    BY_EMAIL.entries.clear()
    BY_ADDRESS.entries.clear()


def is_https(request) -> bool:
    """Whether the browser reached us over HTTPS, behind a proxy or not.

    The forwarded header is trusted here because what it decides is only whether
    the cookie is marked `Secure` -- a spoofed one can only stop the spoofer's
    own browser sending its cookie over plain HTTP.
    """
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return forwarded == "https" or request.url.scheme == "https"


# --- Origin check ---------------------------------------------------------------
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Routes a program reaches with a bearer token or no credential at all: no cookie
# is read there, so there is nothing for a foreign page to ride.
EXEMPT_PREFIXES = ("/v1/", "/api/v1/", "/api/v2/", "/api/device/", "/mcp")


def _hosts(request: Request) -> set[str]:
    hosts = {request.headers.get("host", "").lower()}
    forwarded = request.headers.get("x-forwarded-host", "").split(",")[0].strip().lower()
    if forwarded:
        hosts.add(forwarded)
    hosts.discard("")
    return hosts


def foreign_origin(request: Request) -> bool:
    """Whether a cookie-carrying, state-changing request came from another site."""
    if request.method not in UNSAFE:
        return False
    if request.url.path.startswith(EXEMPT_PREFIXES):
        return False
    if COOKIE_NAME not in request.cookies:
        return False
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    if not source or source == "null":
        # `Origin: null` is what a sandboxed frame sends -- a prototype, for one.
        # It is refused below only if it carries the cookie, which a sandboxed
        # frame without allow-same-origin cannot.
        return source == "null"
    netloc = urlsplit(source).netloc.lower()
    return netloc not in _hosts(request)


# --- Headers --------------------------------------------------------------------
CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        # The pre-paint theme script in base.html is inline, and Alpine
        # evaluates its attributes; both are this application's own code.
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "worker-src 'self'",
        "manifest-src 'self'",
        "frame-src 'self'",
        "frame-ancestors 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    )
)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if foreign_origin(request):
            return JSONResponse(
                {"detail": "This request came from another site and was refused."},
                status_code=403,
            )
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        kind = response.headers.get("content-type", "")
        if kind.startswith("text/html") and "content-security-policy" not in response.headers:
            response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response


__all__ = [
    "CONTENT_SECURITY_POLICY",
    "SecurityMiddleware",
    "foreign_origin",
    "is_https",
    "reset_throttles",
    "sign_in_failed",
    "sign_in_succeeded",
    "sign_in_wait",
]
