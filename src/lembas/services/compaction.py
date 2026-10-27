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

# What the summariser is shown. Past this the oldest turns are dropped with a
# marker: a transcript that does not fit the window it is protecting is no use.
MAX_TRANSCRIPT_CHARS = 24_000

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
    chat.compact_summary = ""
    chat.compacted_through_id = None
    chat.compacted_at = None


def apply(chat: Chat, *, summary: str, upto: Message, db: DBSession | None = None) -> None:
    """Record a summary and move the boundary. Caller commits.

    With `db`, the summary is followed by the table of what was read in the
    turns it covers (harness spec 3.0.0, `context.md` 7): the newest full read
    of each path since the last compaction, with its result id, so the model
    knows what it had read and that `recall` still shows it. Added by the
    harness, not the summariser. The spec builds it from the reads still in
    front of the model; a chat compacts between replies, when none is (a
    reply's tool results are not sent again), so it is drawn from the reads
    made since the previous compaction instead. The dedupe table it empties is
    already empty for the same reason: a stub only ever names a result in the
    request being built (`services/context.py`).
    """
    text = summary.strip()
    if db is not None:
        from lembas.services import context as context_service

        reads = context_service.reads_since(db, chat.id, chat.compacted_at)
        table = context_service.ResultContext(context_service.MemoryStore()).compaction_table(
            reads
        )
        if table:
            text = f"{text}\n\n{table}"
    chat.compact_summary = text
    chat.compacted_through_id = upto.id
    chat.compacted_at = datetime.now(UTC)


def _without_reads_table(summary: str) -> str:
    """A summary without the reads table the harness added after it."""
    from lembas.services import context as context_service

    heading = context_service.spec()["compaction"]["heading"]
    at = summary.find(f"\n\n{heading}")
    return summary[:at] if at >= 0 else summary


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


def transcript(db: DBSession, chat: Chat, *, upto: Message) -> str:
    """The turns to summarise, oldest first, as plain text.

    Only the delta since the last compaction: the previous summary is supplied
    separately, and the instruction asks for one record, so each summary
    subsumes the one before it. Re-summarising the whole chat every time grows
    quadratically and eventually exceeds the very window this protects.
    """
    previous = cutoff_message(db, chat)
    query = select(Message).where(
        Message.chat_id == chat.id,
        Message.created_at <= upto.created_at,
        Message.error == "",
        # Not yet sent to anything. Summarising it would fold words the model
        # has never seen into the record, and then deliver them again later.
        Message.queued.is_(False),
    )
    if previous is not None:
        query = query.where(Message.created_at > previous.created_at)

    lines: list[str] = []
    for message in db.scalars(query.order_by(Message.created_at)):
        body = message.content.strip()
        if not body:
            continue
        lines.append(f"{message.role}: {body}")

    text = "\n\n".join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        # Keep the most recent part: the older it is, the more likely the
        # previous summary already covers it.
        text = "[earlier turns omitted]\n\n" + text[-MAX_TRANSCRIPT_CHARS:]
    return text


def previous_summary_block(chat: Chat) -> str:
    """The earlier summary, headed, or "" on a first compaction.

    Empty is fine to pass straight through: `prompts.substitute` drops a line
    that held a known variable and expanded to nothing, so the prompt does not
    end up with a hole where a heading was.
    """
    earlier = _without_reads_table(chat.compact_summary).strip()
    if not earlier:
        return ""
    return "## Summary of even earlier turns\n\n" + earlier


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
