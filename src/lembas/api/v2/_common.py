"""What every `/api/v2` route needs: the caller, in their language, and the
ownership and permission checks the page routes make."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import Db
from lembas.db.models import Chat, Message, User
from lembas.security import api_tokens
from lembas.security.api_tokens import Caller
from lembas.web import i18n
from lembas.web.i18n import t

_guard = api_tokens.require_token(api_tokens.SCOPE_APP)


async def app_caller(request: Request, db: Db) -> Caller:
    """The caller, holding a live token with the `app` scope -- and the
    request's language set to theirs.

    ⚠ `async`, for the reason `deps.get_current_user` gives: a sync dependency
    runs in a threadpool and the language `ContextVar` set there is thrown away,
    so every refusal would be in English whatever the person reads.
    """
    caller = _guard(request, db)
    i18n.activate(i18n.for_user(caller.user))
    return caller


Me = Annotated[Caller, Depends(app_caller)]


def need(db: DBSession, user: User, key: str) -> None:
    """The page routes' `require_permission`, for a caller who came by token."""
    from lembas.security import permissions

    if not permissions.has(db, user, key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, t("You do not have permission to do that."))


def owned_chat(db: DBSession, chat_id: str, user: User) -> Chat:
    from lembas.api.chats import _owned_chat

    return _owned_chat(db, chat_id, user.id)


def owned_message(db: DBSession, chat: Chat, message_id: str) -> Message:
    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That message no longer exists."))
    return message


class JsonForm:
    """A JSON body dressed as the form a page route reads.

    The cores that read a browser's form (`apply_chat_update`, `answer_card`)
    ask it three things: is a field there, what is it, and every value of one
    that repeats. Answering those from the JSON body is what lets the app go
    through exactly the lines the page goes through, rather than through a
    second reading of the same rules.
    """

    def __init__(self, items: list[tuple[str, str]]) -> None:
        self._items = items

    def __contains__(self, key: object) -> bool:
        return any(name == key for name, _ in self._items)

    def __getitem__(self, key: str) -> str:
        for name, value in self._items:
            if name == key:
                return value
        raise KeyError(key)

    def get(self, key: str, default: str | None = None) -> str | None:
        try:
            return self[key]
        except KeyError:
            return default

    def getlist(self, key: str) -> list[str]:
        return [value for name, value in self._items if name == key]

    def multi_items(self) -> list[tuple[str, str]]:
        return list(self._items)


__all__ = ["JsonForm", "Me", "app_caller", "need", "owned_chat", "owned_message"]
