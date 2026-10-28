"""Save in place: a settings form answered with its page's new state, not a reload.

Every settings and administration form in this application posts and is
answered with `303 See Other` back to its page (`api/_redirect.back`, and a
hundred `RedirectResponse`s written before it existed). Followed by the browser,
that is a full reload: the page jumps back to the top, an open `<details>`
shuts, and the person has to find the field they were on again.

Rewriting every handler to render a fragment would put two answers in every
route. This keeps the one answer and changes what it means to a form that asks
for it. A form inside a `[data-in-place-root]` is sent by htmx with
`LMB-In-Place: 1` (`web/static/js/inplace.js`); for such a request, a redirect

* **back to the page the form is on** is followed *here*: the page is rendered
  by an internal GET, in the same request, with the same cookies (plus any the
  POST just set), and returned as the answer -- the browser swaps the root in
  place and nothing else moves. The redirect's `saved` / `error` / `message`
  words travel in an `HX-Trigger` so the page can say them as a toast, and
  `HX-Replace-Url` takes them back out of the address bar so a reload does not
  say them again.
* **to anywhere else** (a create that lands on the new thing's page, a delete
  that leaves the thing's page) becomes `HX-Redirect`, which htmx turns into an
  ordinary navigation. Nobody is kept on a page that no longer exists.

Anything that is not a redirect -- a page re-rendered with a validation error, a
JSON refusal -- passes through untouched, and the script decides what to do with
it. Requests without the header are not touched at all, so every form keeps
working with JavaScript off.

Pure ASGI rather than `BaseHTTPMiddleware`, because it has to call the
application a second time with a scope of its own.
"""

from __future__ import annotations

import json
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, urlencode, urlsplit

HEADER = b"lmb-in-place"
REDIRECTS = (301, 302, 303, 307, 308)
# What a page reads from its query string to say something once.
FLASH_PARAMS = ("saved", "error", "message")
# Not carried to the internal GET: it describes the POST's body, not a GET's.
_DROP = {b"content-type", b"content-length", b"transfer-encoding", b"hx-trigger-name",
         b"hx-trigger", b"hx-target"}


def _header(headers, name: bytes) -> str:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def _local(location: str, host: str) -> tuple[str, str] | None:
    """`(path, query)` of a same-site location, or None."""
    parts = urlsplit(location)
    if parts.scheme and parts.scheme not in ("http", "https"):
        return None
    if parts.netloc and parts.netloc.lower() != host.lower():
        return None
    if not parts.path.startswith("/"):
        return None
    return parts.path, parts.query


def _cookie_header(original: str, set_cookies: list[str]) -> str:
    """The request's Cookie header with whatever the POST just set applied."""
    jar: dict[str, str] = {}
    for part in original.split(";"):
        name, _, value = part.strip().partition("=")
        if name:
            jar[name] = value
    for raw in set_cookies:
        parsed = SimpleCookie()
        try:
            parsed.load(raw)
        except Exception:  # an odd cookie is the browser's business, not ours
            continue
        for name, morsel in parsed.items():
            expired = morsel["max-age"] == "0" or (
                morsel["expires"] and "1970" in morsel["expires"]
            )
            if expired:
                jar.pop(name, None)
            else:
                jar[name] = morsel.value
    return "; ".join(f"{name}={value}" for name, value in jar.items())


class InPlaceMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") in ("GET", "HEAD", "OPTIONS")
            or not _header(scope.get("headers", []), HEADER)
            or _header(scope.get("headers", []), b"hx-request") != "true"
        ):
            await self.app(scope, receive, send)
            return

        held: dict = {}

        async def watch(message) -> None:
            if message["type"] == "http.response.start":
                if message["status"] in REDIRECTS:
                    held["start"] = message
                    return
            elif message["type"] == "http.response.body" and "start" in held:
                return  # a redirect's body is never shown
            await send(message)

        await self.app(scope, receive, watch)
        if "start" not in held:
            return
        await self._answer(scope, held["start"], send)

    async def _answer(self, scope, start, send) -> None:
        headers = list(start.get("headers", []))
        location = _header(headers, b"location")
        set_cookies = [v.decode("latin-1") for k, v in headers if k.lower() == b"set-cookie"]
        request_headers = scope.get("headers", [])
        host = _header(request_headers, b"host")
        target = _local(location, host) if location else None
        current = urlsplit(_header(request_headers, b"hx-current-url"))

        cookies_out = [(b"set-cookie", c.encode("latin-1")) for c in set_cookies]

        if target is None or target[0] != current.path:
            # Somewhere else: let the browser go there, as it would have.
            await _send(send, 200, cookies_out + [(b"hx-redirect", location.encode("latin-1"))])
            return

        path, query = target
        page = await self._get(scope, path, query, set_cookies)
        if page is None or page[0] != 200:
            await _send(send, 200, cookies_out + [(b"hx-redirect", location.encode("latin-1"))])
            return

        status, page_headers, body = page
        pairs = parse_qsl(query, keep_blank_values=True)
        said = {name: value for name, value in pairs if name in FLASH_PARAMS}
        clean = urlencode([(n, v) for n, v in pairs if n not in FLASH_PARAMS])
        trigger = json.dumps({"lembas:in-place": said})
        out = [
            (k, v)
            for k, v in page_headers
            if k.lower() not in (b"content-length", b"hx-trigger", b"hx-replace-url")
        ]
        out += cookies_out
        out += [
            (b"hx-trigger", trigger.encode("utf-8")),
            (b"hx-replace-url", (path + ("?" + clean if clean else "")).encode("latin-1")),
            (b"content-length", str(len(body)).encode("latin-1")),
        ]
        await send({"type": "http.response.start", "status": status, "headers": out})
        await send({"type": "http.response.body", "body": body})

    async def _get(self, scope, path: str, query: str, set_cookies: list[str]):
        headers = [
            (k, v)
            for k, v in scope.get("headers", [])
            if k.lower() not in _DROP and k.lower() != b"cookie"
        ]
        cookie = _cookie_header(_header(scope.get("headers", []), b"cookie"), set_cookies)
        if cookie:
            headers.append((b"cookie", cookie.encode("latin-1")))
        sub = dict(scope)
        sub.update(
            method="GET",
            path=path,
            raw_path=path.encode("utf-8"),
            query_string=query.encode("latin-1"),
            headers=headers,
        )
        # The request state of the POST is not the GET's.
        sub["state"] = {}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        result: dict = {"body": b""}

        async def collect(message) -> None:
            if message["type"] == "http.response.start":
                result["status"] = message["status"]
                result["headers"] = list(message.get("headers", []))
            elif message["type"] == "http.response.body":
                result["body"] += message.get("body", b"")

        try:
            await self.app(sub, receive, collect)
        except Exception:
            return None
        if "status" not in result:
            return None
        return result["status"], result["headers"], result["body"]


async def _send(send, status: int, headers: list) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": headers + [(b"content-length", b"0")],
        }
    )
    await send({"type": "http.response.body", "body": b""})


__all__ = ["FLASH_PARAMS", "InPlaceMiddleware"]
