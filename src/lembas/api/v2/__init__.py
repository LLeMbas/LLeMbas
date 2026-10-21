"""The app's door: `/api/v2`, JSON in and out, bearer token with the `app` scope.

A third door beside the two the server already had. Pages and htmx take the
session cookie and answer HTML; `/v1` takes a bearer token and stores nothing.
A native client needs the chats themselves -- the list, the transcript, the
stream, the cards -- and this is them, as JSON.

It is a **serialisation of what the web UI already shows, not a new feature
set**. Every route calls the same core the page route calls (`write_turn`,
`start_new_chat`, `rewind_to`, `regenerate_reply`, `answer_card`,
`apply_chat_update`, `delete_chat_now` in `api/chats.py`), so a rule cannot
hold through one door and not the other. The stream (`stream.py`) is a second
reader of the same `Generation` `_follow` reads, under the same rules.

A token never reaches what its holder could not do in a browser: the scope is
re-checked per request (`security/api_tokens.py`), and every route applies the
permission, model-access, data-group, quota and credit checks its page twin
does. Errors are `{"detail": "…"}`, as on every JSON answer this server gives.

`APP_API` is the contract's version. A change to what a route returns or a
frame carries that an existing client would misread is a new number, not a
tweak; adding a field is not.
"""

from __future__ import annotations

from fastapi import APIRouter

APP_API = 1

router = APIRouter(prefix="/api/v2", tags=["app"])


def _include() -> None:
    from lembas.api.v2 import chats, misc, stream

    router.include_router(chats.router)
    router.include_router(stream.router)
    router.include_router(misc.router)


_include()

__all__ = ["APP_API", "router"]
