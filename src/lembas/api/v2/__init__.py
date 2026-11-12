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
permission, model-access, data-group and quota checks its page twin
does. Errors are `{"detail": "…"}`, as on every JSON answer this server gives.

`APP_API` is the contract's version. A change to what a route returns or a
frame carries that an existing client would misread is a new number, not a
tweak; adding a field is not. 2 (with 2.0): the stream has no `canvas` frame
and a chat no `knowledge_bases`; a tool call may carry a `show` card, whose
file is `GET /chats/{chat}/show`. Added within 2: `GET /files` and
`GET /files/content`, the person's files (`files.py`); a chat's `agents`
(its team's size, readable and writable) and `GET /chats/{chat}/team`, the
team's tree and room since a cursor (harness spec 3.2.0); the activity panel --
`GET /chats/{chat}/activity` (agents, room since a cursor, context, outputs,
plan, `version`), `GET /chats/{chat}/activity/events` (an `activity` event
whenever `version` moves), `GET /chats/{chat}/agents/{kind}/{id}` (one agent
and its whole conversation) and `POST .../stop`; scheduled work -- `/schedules`
and its runs, the compile and the preview, the scheduling models
(`schedules.py`) -- and `GET /events`, the person's own stream (a `schedules`
event); the terminal on a linked device -- an agent chat's, and one before the
chat exists, on a device and in a directory (`terminal.py`) -- and `terminal`
on each of `GET /devices`. Parity with the web (beta.9): folders made,
renamed, moved and deleted (`folders.py`), `pinned` on a chat's PATCH, search
and memory (`library.py`), the library's writes (`files.py`), a chat's tool and
skill switches and `/allow/clear` (`chats.py`), and `PATCH /me` for language
and theme, with `scopes`, `languages` and `themes` on `GET /me`.

The composer's menus (beta.11, `menus.py`): a linked device's `/` commands and
`@` files, what `@` offers from the server, and `POST /attachments/from`.

The `python` tool's runner (beta.11, `python.py`): a chat's page protocol
(announce, withdraw, take, answer) and the Pyodide files, so an app's window
can be the page Python runs in; `python` on `GET /me` says whether it is on.

Voice and images are not here: they are `/v1/audio/*` and
`/v1/images/generations`, the OpenAI-compatible routes, which need the `models`
scope. An app asks for `app models` at sign-in (the device login takes any of
the person's scopes) and reads `scopes` on `GET /me` to know it has both.
"""

from __future__ import annotations

from fastapi import APIRouter

APP_API = 2

router = APIRouter(prefix="/api/v2", tags=["app"])


def _include() -> None:
    from lembas.api.v2 import (
        chats,
        files,
        folders,
        library,
        menus,
        misc,
        python,
        schedules,
        stream,
        terminal,
    )

    router.include_router(chats.router)
    router.include_router(folders.router)
    router.include_router(library.router)
    router.include_router(schedules.router)
    router.include_router(files.router)
    router.include_router(stream.router)
    router.include_router(misc.router)
    router.include_router(terminal.router)
    # Before nothing it could shadow: `/devices/{id}/commands` and `/files`
    # are longer than `misc`'s `/devices`, and `/attachments/from` is a POST
    # where `/attachments/{id}` is a GET and a DELETE.
    router.include_router(menus.router)
    router.include_router(python.router)


_include()

__all__ = ["APP_API", "router"]
