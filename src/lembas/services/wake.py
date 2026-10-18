"""Starting a reply when nobody asked for one just now.

Two things need this: a background job finishing on the far side, and a schedule
coming due. Both are the same problem — put a turn into a chat from outside any
request, and get it answered — and both depend on the same invariant, which is
subtle enough that having two copies of it is how one of them drifts.

**The invariant.** A chat may have one generation running at a time. So the
check and the writes happen under a per-chat lock with **no `await` between
them**: two things waking one chat at the same moment cannot each spin up a
reply, because the second sees the first's already live and leaves its turn
`queued` for that reply's `_inject`/`_drain` to deliver.

**The role is load-bearing.** The turn is written as `user`, because `_inject`
sends a queued turn verbatim and `build_messages` must keep seeing a user turn
where one belongs. What stops the transcript claiming the reader typed it is
`Message.machine`, which changes the bubble and nothing about the request. The
framing the *model* reads therefore has to live in the words -- which is why
every caller passes prose that names itself, the way `execute_plan` quotes a
plan rather than asserting it.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_LOCKS: dict[str, asyncio.Lock] = {}


def lock_for(chat_id: str) -> asyncio.Lock:
    """The lock for one chat, made on first use.

    Not cleaned up: a lock is two pointers, chats are finite, and a reaper for
    these would be able to delete one while a waiter held it.
    """
    existing = _LOCKS.get(chat_id)
    if existing is None:
        existing = _LOCKS[chat_id] = asyncio.Lock()
    return existing


async def wake_chat(chat_id: str, content: str, *, model_id: str = "") -> str:
    """Put a turn into a chat and get it answered. Returns the assistant message
    id if a reply was started, or "" if the turn was queued for one already
    running.

    Never raises: a caller here is a watcher or a ticker, and one chat that
    cannot be woken must not stop the others.

    `model_id` overrides the chat's own, which is what lets a schedule name the
    model it wants without editing the chat. Empty means the chat decides.
    """
    from lembas.db.models import ROLE_ASSISTANT, ROLE_USER, Chat
    from lembas.db.session import session_scope
    from lembas.services import chat as chat_service
    from lembas.services import generation as generation_service
    from lembas.services import settings_store

    # An API-only instance starts no chat reply from anywhere: not a
    # schedule's `run_now`, a helper, nor a finished job. Checked here, at the
    # one place all of them start, rather than in each caller.
    try:
        with session_scope() as db:
            if settings_store.api_only(db):
                log.info("not waking chat %s: this instance serves the API only", chat_id)
                return ""
    except Exception:  # unreadable is "full", what it was before
        log.debug("could not read the instance mode for chat %s", chat_id, exc_info=True)

    assistant_id = ""
    async with lock_for(chat_id):
        # Read before the writes and with nothing awaited in between. This is
        # the whole invariant; moving either half out of the lock, or awaiting
        # anything between them, reintroduces two concurrent replies to one
        # chat -- which is a Stop button pointing at whichever bubble happens to
        # come first in the document.
        running = generation_service.running_for(chat_id) is not None
        try:
            with session_scope() as db:
                chat = db.get(Chat, chat_id)
                if chat is None:
                    return ""
                chat_service.create_message(
                    db, chat, ROLE_USER, content, queued=running, machine=True
                )
                if not running:
                    assistant = chat_service.create_message(
                        db,
                        chat,
                        ROLE_ASSISTANT,
                        "",
                        complete_=False,
                        model_id=model_id or chat.model_id,
                    )
                    assistant_id = assistant.id
        except Exception:  # a failed wake must not kill its caller
            log.exception("could not wake chat %s", chat_id)
            return ""

    # Outside the lock: `ensure` spawns a task that will want a session of its
    # own, and holding this while it starts would serialise every wake on the
    # instance behind the slowest one.
    if assistant_id:
        generation_service.ensure(chat_id, assistant_id)
    return assistant_id


def queued_count(chat_id: str) -> int:
    """How many turns are waiting to be delivered into this chat.

    `_drain` takes **one** per reply, deliberately -- two consecutive user turns
    in a request is worse than a delay. So something firing repeatedly into a
    chat that is answering slowly can build a backlog nobody asked for, and the
    callers here use this to stop rather than to queue for ever.
    """
    from sqlalchemy import func, select

    from lembas.db.models import Message
    from lembas.db.session import session_scope

    with session_scope() as db:
        return int(
            db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.chat_id == chat_id, Message.queued.is_(True))
            )
            or 0
        )
