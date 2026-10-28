"""The redirect a form handler answers with: back to its page, with a word.

A form saved in place (web/in_place.py) gets the same answer and never sees the
redirect: the middleware follows it, renders the page, and hands the word to the
browser as a toast. So a handler is written once, for both.
"""

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
    # A page already carrying a query (a filtered list on page 2) keeps it.
    joiner = "&" if "?" in path else "?"
    return RedirectResponse(
        f"{path}{joiner}{field}={quote(message)}", status_code=status.HTTP_303_SEE_OTHER
    )
