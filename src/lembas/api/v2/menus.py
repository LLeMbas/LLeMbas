"""The composer's `/` and `@` menus, for an app.

The JSON twins of what the page's composer asks (`api/devices.py`
`device_commands` and `device_files`, `api/files.py` `/mention-picker`,
`/from-attachment` and `/link`), on the same code (`services/menus.py`) and
under the same rules: the device is the caller's and may run agent chats now,
the chat is theirs, an attachment is copied only within its data group, and a
file of the tree only where the chat -- or, before one exists, the person --
may read it.

| Route | Answer |
|---|---|
| `GET /devices/{id}/commands?chat=&cwd=` | `{"commands": [{name, description, hint, kind}]}` |
| `GET /devices/{id}/files?q=&chat=&cwd=` | `{"files": [{path, kind, name}], "error", "device"}` |
| `GET /mentions?q=&chat=` | `{"items": [{kind, id?, path?, url?, name, detail}]}` |
| `POST /attachments/from` | `{"attachment": {...}}`, as `POST /attachments` answers |

`chat` names an existing chat; `cwd` the directory a new agent chat's composer
has chosen (a chat with a session answers for its session, whatever `cwd`
says). A device that is not linked, cannot answer or offers nothing answers
with an empty list, never an error: the menu still shows the rest.

`POST /attachments/from` takes exactly one of `{"attachment": id}` (a copy of
something sent earlier), `{"path": "/…"}` (a file of the tree, as it is now)
or `{"url": "https://…"}` (a page, reduced to text), each with an optional
`"chat"`. A mention item says which by its `kind`: `attachment` carries `id`,
`file` carries `path` -- which an app may equally put in the message as
`@path` text, as the page does, for the model to read itself -- and `url`
carries `url`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, status

from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import Me, need
from lembas.services import menus
from lembas.services.fetch import FetchError
from lembas.web.i18n import t

router = APIRouter()


@router.get("/devices/{token_id}/commands")
async def device_commands(
    caller: Me, db: Db, token_id: str, chat: str = Query(""), cwd: str = Query("")
) -> dict[str, Any]:
    commands, device = await menus.device_commands(
        db, caller.user, token_id, chat_id=chat, cwd=cwd
    )
    return {"commands": [serialise.device_command(c) for c in commands], "device": device}


@router.get("/devices/{token_id}/files")
async def device_files(
    caller: Me,
    db: Db,
    token_id: str,
    q: str = Query(""),
    chat: str = Query(""),
    cwd: str = Query(""),
) -> dict[str, Any]:
    found = await menus.device_files(db, caller.user, token_id, q=q, chat_id=chat, cwd=cwd)
    return {"files": found.files, "error": found.error, "device": found.device}


@router.get("/mentions")
async def mentions(caller: Me, db: Db, q: str = Query(""), chat: str = Query("")) -> dict:
    need(db, caller.user, "files.upload")
    return {"items": serialise.mentions(menus.mentions(db, caller.user, q=q, chat_id=chat))}


@router.post("/attachments/from", status_code=status.HTTP_201_CREATED)
async def attach_from(caller: Me, db: Db, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    need(db, caller.user, "files.upload")
    chat = str(body.get("chat") or "")
    given = [key for key in ("attachment", "path", "url") if str(body.get(key) or "").strip()]
    if len(given) != 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            t("Name one thing to attach: an attachment, a path or a URL."),
        )
    try:
        if given[0] == "attachment":
            row = menus.attach_copy(
                db, caller.user, str(body["attachment"]), chat_id=chat,
                model_id=str(body.get("model") or ""),
            )
        elif given[0] == "path":
            row = menus.attach_tree_file(db, caller.user, str(body["path"]), chat_id=chat)
        else:
            row = await menus.attach_url(db, caller.user, str(body["url"]).strip(), chat_id=chat)
    except menus.NotAvailable as exc:
        # One answer whether it is missing or out of reach.
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            exc.message or t("That is not available to attach here."),
        ) from exc
    except FetchError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message) from exc
    return {"attachment": serialise.attachment(row)}


__all__ = ["router"]
