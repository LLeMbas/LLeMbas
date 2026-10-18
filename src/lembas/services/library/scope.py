"""The library's scope for one conversation, wherever the conversation runs.

A chat on the server's own loop and an agent chat on a linked device search the
same knowledge: the bases attached to the chat, in the data group of the model
answering. The chat loop reads them through `tools.context_for`; the device
link names them to the device (`_meta.lembas.library` on `session/new` and
`session/prompt`, harness spec 1.1.0), and the device sends the session back as
`_meta.lembas.scope` on every `tools/call` to `/mcp`, where this module finds
the chat the scope names. One place for both, so the list a device is told and
the list `/mcp` searches cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Chat, User
from lembas.services import data_groups


def chat_bases(db: DBSession, user: User | None, chat: Chat | None, speaker=None) -> list[dict]:
    """The bases this chat is scoped to, as `{"id", "name"}`: the attached ones
    in the answering model's data group -- exactly the list the harness names
    to the model as `{{knowledge_scope}}`. Empty when nothing is attached,
    which to a device means "the account's whole library" (harness spec
    `acp.md`, `library`), as a chat with none attached searches everything.

    Not `tools._searchable_bases`: that one widens an empty list to every
    visible base, because a running tool must know what `base` may name. The
    scope *sent* stays empty so the device, and the model it tells, read it
    as "everything" rather than as a list that happens to be complete today.
    """
    if chat is None or user is None:
        return []
    from lembas.services import chat as chat_service

    group = data_groups.for_speaker(db, user, chat, speaker or chat_service.speaker_for(db, chat))
    return [
        {"id": base.id, "name": base.name}
        for base in chat.knowledge_bases
        if (base.data_group_id or data_groups.DEFAULT_GROUP) == group
    ]


def device_chat(
    db: DBSession, token_id: str, session_id: str, user_id: str | None = None
) -> Chat | None:
    """The chat a device's session id names, for this device's token and no
    other: a session id is the device's, so one device's `scope` can never
    reach a chat another device runs -- not even one of the same account's."""
    if not token_id or not session_id:
        return None
    wanted: list[Any] = [
        Chat.device_token_id == token_id,
        Chat.device_session_id == session_id,
    ]
    if user_id:
        wanted.append(Chat.user_id == user_id)
    return db.scalars(select(Chat).where(*wanted)).first()


__all__ = ["chat_bases", "device_chat"]
