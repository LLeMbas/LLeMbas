"""Carrying a long conversation forward without carrying all of it.

Past a certain length every chat stops working: the window fills, and the only
options are to lose the beginning or to start again. Compaction summarises the
earlier turns and sends the summary in their place.

**The messages are kept.** They stay in the transcript, collapsed behind a
divider, and simply stop being part of the request. A summary that turned out
badly is then a bad turn rather than a lost conversation, which is what makes
the button safe to press and automatic compaction safe to have at all.

**Stored on the Chat, not as a synthetic Message.** A synthetic row would need a
role: `system` breaks the one-system-message rule the moment `build_messages`
emits it beside the harness, and `user`/`assistant` makes it a turn people can
edit, regenerate from and copy, indistinguishable from a real one in all four
places a bubble is rendered. Worse, "editing rewinds, it does not branch" would
silently delete it and leave no marker that compaction had ever happened.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import ROLE_ASSISTANT, Chat, Message
from lembas.services import metrics as metrics_service
from lembas.services import settings_store, tokens

log = logging.getLogger(__name__)

# Settings key, in the GENERAL group. 0 turns automatic compaction off; the
# button still works, because a person asking for it does not need a threshold.
THRESHOLD_KEY = "compact_threshold"
DEFAULT_THRESHOLD = 95


def threshold(db: DBSession) -> int:
    value = settings_store.get(db, THRESHOLD_KEY)
    return int(value) if isinstance(value, (int, float)) else DEFAULT_THRESHOLD


def moment(message: Message) -> datetime:
    """A message's timestamp, always comparable.

    SQLite does not store the offset, so a row loaded from disk comes back naive
    while one still in the session's identity map keeps the tzinfo it was
    created with. Comparing the two raises, and every comparison here is between
    exactly those: a cutoff fetched by id against history loaded in bulk.
    `files.sweep_orphans` already normalises for the same reason.
    """
    created = message.created_at
    return created if created.tzinfo is not None else created.replace(tzinfo=UTC)


def cutoff_message(db: DBSession, chat: Chat) -> Message | None:
    """The message compaction reached, or None if it never has.

    There is no foreign key to null this out on an upgraded database, so the
    check is load-bearing rather than defensive: an id pointing at a message
    that has been deleted means the boundary no longer describes anything, and
    the chat has to read as uncompacted.
    """
    if not chat.compact_summary or not chat.compacted_through_id:
        return None
    message = db.get(Message, chat.compacted_through_id)
    if message is None or message.chat_id != chat.id:
        return None
    return message


def reset(chat: Chat) -> None:
    """Forget that this chat was ever compacted."""
    from lembas.services import prefix

    chat.compact_summary = ""
    chat.compacted_through_id = None
    chat.compacted_at = None
    # What the model reads from the start changes, so its system prompt is made
    # again with the next request (harness spec 3.1.0, context.md §10).
    prefix.forget(chat)


def apply(chat: Chat, *, summary: str, upto: Message, db: DBSession | None = None) -> None:
    """Record a summary and move the boundary. Caller commits.

    With `db`, the summary is followed by the table of what was read in the
    turns it covers (harness spec 3.0.0, `context.md` 7): of the full reads
    made since the previous compaction in turns still part of the history
    being summarised, the newest of each file (by its absolute path), newest
    last, at most `compaction.rows`, with its result id -- so the model knows
    what it had read and that `recall` still shows it. Added by the harness,
    not the summariser. A chat does not send an earlier reply's results again,
    so they are drawn from what it stored for those turns: a read belongs to
    the reply it was made in (`tool_results.message_id`), and a reply taken
    back -- deleted, rewound away, or regenerated (whose earlier attempt's
    results are detached) -- takes its reads with it. The dedupe table it
    empties is already empty for the same reason: a stub only ever names a
    result in the request being built (`services/context.py`).
    """
    text = summary.strip()
    if db is not None:
        from lembas.services import context as context_service

        previous = cutoff_message(db, chat)
        turns = select(Message.id).where(
            Message.chat_id == chat.id, Message.created_at <= upto.created_at
        )
        if previous is not None:
            turns = turns.where(Message.created_at > previous.created_at)
        reads = context_service.reads_since(
            db, chat.id, chat.compacted_at, message_ids=set(db.scalars(turns))
        )
        table = context_service.ResultContext(context_service.MemoryStore()).compaction_table(
            reads
        )
        if table:
            text = f"{text}\n\n{table}"
    chat.compact_summary = text
    chat.compacted_through_id = upto.id
    chat.compacted_at = datetime.now(UTC)
    # The one deliberate break of the prompt cache: everything is read again
    # anyway, so the system prompt is made again with the next request
    # (harness spec 3.1.0, context.md §10 rule 1), from what is true then.
    from lembas.services import prefix

    prefix.forget(chat)


def split(
    db: DBSession, chat: Chat, messages: list[Message]
) -> tuple[list[Message], list[Message]]:
    """(summarised, live) -- what is behind the divider, and what is not."""
    cutoff = cutoff_message(db, chat)
    if cutoff is None:
        return [], list(messages)
    boundary = moment(cutoff)
    return (
        # A prompt still waiting to be sent stays on the live side whatever its
        # timestamp says. Folding one into the "earlier messages" details would
        # hide the only place its Send now and Discard exist, and it has not
        # been part of any request to summarise.
        [m for m in messages if not m.queued and moment(m) <= boundary],
        [m for m in messages if m.queued or moment(m) > boundary],
    )


def last_complete(db: DBSession, chat: Chat) -> Message | None:
    """The newest finished assistant turn: where compaction should stop.

    Landing on a reply rather than a question means the kept history starts on a
    user turn, which is what every chat template expects.
    """
    return db.scalar(
        select(Message)
        .where(
            Message.chat_id == chat.id,
            Message.role == ROLE_ASSISTANT,
            Message.complete.is_(True),
        )
        .order_by(Message.created_at.desc())
    )


# --- Asking for the summary (harness spec 3.1.0, context.md §7) -------------------------
async def summarise(chat_id: str, *, upto_id: str, speaker=None) -> str:
    """The summary, asked for as the next turn of the same conversation.

    The same frozen system prompt, the same tools, every turn as it was sent
    (notes included) up to and including `upto` -- the last finished reply --
    and then one more user turn, `task.compact` with `{{transcript}}` and
    `{{previous_summary}}` empty: the model has the conversation itself, an
    earlier summary included (the lead turn). So the request that makes the
    summary costs only that turn where the server has the rest cached; a
    separate request with a pasted transcript was a full re-read, and the
    transcript capped to fit it lost the conversation's middle.

    A tool call in the reply is not run: the text before it is the summary,
    and a reply with none is no summary (""), as an empty one is. Nothing here
    is stored as a turn. What the request used, cached tokens included, is
    counted like any reply's (`services/usage.py`).

    Read, close the session, ask, reopen to count -- the awaited call never
    holds a session open. Raises `LLMError` from the endpoint; "" when there is
    nothing to ask (the fragment cleared, the chat or the reply gone).
    """
    from lembas.db.models import User
    from lembas.db.session import session_scope
    from lembas.services import chat as chat_service
    from lembas.services import prompts
    from lembas.services import tools as tools_service

    with session_scope() as db:
        chat = db.get(Chat, chat_id)
        upto = db.get(Message, upto_id)
        if chat is None or upto is None or upto.chat_id != chat.id:
            return ""
        template = prompts.resolve(db, "task.compact")
        if not template.strip():
            return ""
        owner = db.get(User, chat.user_id)
        speaker = speaker or chat_service.speaker_for(db, chat)
        endpoint, model_id = chat_service.resolve_endpoint(db, chat, speaker)
        offered = tools_service.resolve_tools(db, chat, owner, speaker).schemas
        body = chat_service.build_request(
            db,
            chat,
            through=upto,
            tools=offered,
            user=owner,
            speaker=speaker,
            new_note=False,
        )
        # Both empty in this form (spec 3.1.0 §7): the conversation is in
        # front of the model, the earlier summary as its lead turn.
        ask = prompts.substitute(template, {"transcript": "", "previous_summary": ""}).strip()
        body["model"] = model_id
        body["messages"] = [*body["messages"], {"role": "user", "content": ask}]
        user_id = chat.user_id

    text, counts = await _ask(endpoint, body)

    with session_scope() as db:
        _count(db, user_id, speaker, counts)
    return text.strip()


async def _ask(endpoint, body: dict) -> tuple[str, dict[str, int]]:
    """Stream the summary turn: its words up to any tool call, and what the
    endpoint said it used (summed, as a reply's rounds are)."""
    from lembas.services.llm.openai_client import (
        chunk_usage,
        delta_text,
        delta_tool_calls,
        stream_chat,
    )
    from lembas.services.reasoning import REASONING, ReasoningSplitter

    splitter = ReasoningSplitter()
    words: list[str] = []
    counts: dict[str, int] = {}
    called = False
    async for chunk in stream_chat(endpoint, body):
        if (used := chunk_usage(chunk)) is not None:
            for name, value in used.items():
                counts[name] = counts.get(name, 0) + int(value)
        if delta_tool_calls(chunk):
            # Not run. What came before it is the summary.
            called = True
        if called:
            continue
        if text := delta_text(chunk):
            words.extend(piece for kind, piece in splitter.feed(text) if kind != REASONING)
    if not called:
        words.extend(piece for kind, piece in splitter.flush() if kind != REASONING)
    return "".join(words), counts


def _count(db: DBSession, user_id: str, speaker, counts: dict[str, int]) -> None:
    """The summary request's tokens and cost, on the speaker's usage row. Never
    raises: bookkeeping must not undo a compaction."""
    from lembas.services import chat as chat_service
    from lembas.services import pricing
    from lembas.services import usage as usage_service

    try:
        model = chat_service.model_row(db, speaker) if speaker is not None else None
        priced = pricing.price_reply(
            db, model, counts, reported_micro=counts.get("reported_cost_micro")
        )
        usage_service.record(
            db,
            user_id,
            model_id=(model.model_id if model is not None else getattr(speaker, "model_id", ""))
            or "",
            prompt_tokens=counts.get("prompt_tokens", 0),
            cached_tokens=counts.get("cached_tokens", 0),
            completion_tokens=counts.get("completion_tokens", 0),
            cost_micro=priced["cost_micro"] if priced else 0,
            currency=priced["currency"] if priced else None,
        )
    except Exception:  # pragma: no cover - bookkeeping
        log.debug("could not count the compaction request", exc_info=True)


def should_compact(db: DBSession, chat: Chat, *, pending: str = "") -> bool:
    """Whether the next request should be summarised first.

    Judged from the last reply's recorded usage plus an estimate of the new
    turn. True prompt_tokens are only knowable after a response, so a
    retrospective figure is the honest basis -- but on its own it is one turn
    stale, and fifty thousand characters pasted into the composer would overflow
    a window that measured 90% last time. The estimator covers only that delta.

    Never fires when the model's context length is unknown. Acting on a number
    nobody supplied is exactly what the 0-means-unknown rule exists to prevent.
    """
    limit = threshold(db)
    if limit <= 0:
        return False

    last = last_complete(db, chat)
    if last is None:
        return False

    usage = metrics_service.from_message(last.usage_json)
    if usage.context_limit <= 0 or usage.context_tokens <= 0:
        return False

    projected = usage.context_tokens + tokens.estimate(pending)
    return projected >= usage.context_limit * limit / 100
