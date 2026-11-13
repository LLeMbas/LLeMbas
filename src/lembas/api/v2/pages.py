"""Pages in answers, for an app that draws them (harness spec 3.8.0,
`pages.md`).

There is one sanitiser, and it is here: an app asks for a page and is given
the **same rebuilt, static document** the web's own frame is served
(`services/html_pages.document`) -- never the model's markup to clean for
itself.

| Route | |
|---|---|
| `GET /pages/m/{message}/{digest}?bg=&ink=&muted=&accent=&line=` | a page of that reply |
| `GET /pages/r/{report}/{digest}?…` | a page of that report |
| `GET /chats/{id}/html-pages` · `POST … {"on": bool}` | the chat's switch: `{"offered", "on"}` |

A page answers as JSON, `{"title", "digest", "html", "policy"}`: `html` is the
whole document, to be handed to a frame the app seals itself, and `policy` is
the Content-Security-Policy that frame's document must be given -- the one
the web serves it under (`security/sealed.page_policy`). JSON and not
`text/html`: an app's window must never be navigated to model-written HTML on
this server's origin by a slip, and a body that is data cannot be.

The colours are the frame's variables as the app's theme has them, kept only
when each is plainly a colour. The reader's own replies and reports only:
somebody else's is a 404, as one that is not there is. While the instance's
switch is off the document says so and shows nothing of the page.

`offered` is whether this chat could have pages at all -- the instance's
switch, the model's flag, and not an agent chat, a subagent's or the phone
assistant's; `on` is the chat's own switch. A change is told to the model on
the next message, as from the page.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request, status

from lembas.api.deps import Db
from lembas.api.v2._common import Me, owned_chat
from lembas.db.models import Chat, Message, Report
from lembas.security import sealed
from lembas.services import chat as chat_service
from lembas.services import html_pages
from lembas.services.markdown import pages_in
from lembas.web.i18n import t

router = APIRouter()


def page_json(db, text: str, digest: str, colours: dict[str, str]) -> dict[str, Any]:
    page = next((p for p in pages_in(text) if p.digest == digest), None)
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    shown = page
    if not html_pages.instance_on(db):
        shown = html_pages.Page(
            page.title, f"<p>{t('Pages in answers are switched off here.')}</p>"
        )
    return {
        "title": page.title,
        "digest": page.digest,
        "html": html_pages.document(shown, colours),
        "policy": sealed.page_policy(),
    }


def _colours(request: Request) -> dict[str, str]:
    return {name: request.query_params.get(name, "") for name in html_pages.COLOURS}


@router.get("/pages/m/{message_id}/{digest}")
async def message_page(
    request: Request, caller: Me, db: Db, message_id: str, digest: str
) -> dict[str, Any]:
    message = db.get(Message, message_id)
    chat = db.get(Chat, message.chat_id) if message is not None else None
    if chat is None or chat.user_id != caller.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return page_json(db, message.content or "", digest, _colours(request))


@router.get("/pages/r/{report_id}/{digest}")
async def report_page(
    request: Request, caller: Me, db: Db, report_id: str, digest: str
) -> dict[str, Any]:
    report = db.get(Report, report_id)
    if report is None or report.owner_id != caller.user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return page_json(db, report.body or "", digest, _colours(request))


def switch(db, chat: Chat) -> dict[str, bool]:
    offered = html_pages.available(db, chat, chat_service.model_for(db, chat))
    return {"offered": offered, "on": bool(offered and html_pages.chat_on(chat))}


@router.get("/chats/{chat_id}/html-pages")
async def read_switch(caller: Me, db: Db, chat_id: str) -> dict[str, bool]:
    return switch(db, owned_chat(db, chat_id, caller.user))


@router.post("/chats/{chat_id}/html-pages")
async def set_switch(
    caller: Me, db: Db, chat_id: str, body: dict[str, Any] = Body(default_factory=dict)
) -> dict[str, bool]:
    chat = owned_chat(db, chat_id, caller.user)
    wanted = body.get("on") is True
    if wanted and not switch(db, chat)["offered"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT, t("Pages in answers are not offered in this chat.")
        )
    scope = dict(chat.scope_json or {})
    if wanted:
        scope[html_pages.SCOPE_KEY] = True
    else:
        scope.pop(html_pages.SCOPE_KEY, None)
    chat.scope_json = scope
    db.commit()
    return switch(db, chat)


__all__ = ["page_json", "router", "switch"]
