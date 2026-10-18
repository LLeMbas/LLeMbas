"""The redirect a form handler answers with: back to its page, with a word."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import status
from fastapi.responses import RedirectResponse, Response


def back(path: str, message: str = "", *, ok: bool = True) -> Response:
    """See Other to `path`, carrying `message` as `?saved=` -- or `?error=`
    when it is not good news -- for the page to show; nothing when there is
    nothing to say."""
    if not message:
        return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)
    field = "saved" if ok else "error"
    return RedirectResponse(
        f"{path}?{field}={quote(message)}", status_code=status.HTTP_303_SEE_OTHER
    )
