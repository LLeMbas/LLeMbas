"""The reply as it is written, as JSON server-sent events.

The twin of `api/chats.py:_follow`, which renders the same `Generation` to
HTML for the page. A second *reader*, not a second producer: the generation
runs once whoever watches, and both followers count in `followers`, so a reply
finished while only the app watched is not announced as unread.

The rules are `_follow`'s and they are why a flaky phone connection works:

* **Every frame carries the whole block**, never a delta. A follower attaching
  late -- the app back from the background, a dropped connection reopened --
  gets the whole state in its first pass and needs no bookkeeping.
* **A frame is sent only when its payload changed.**
* **A round closes in one frame**: `steps` carries every closed step and the
  emptied tail together, so text moving from the tail into a step is one update.
* **Metrics keep to a clock**, not to the version, and keepalives hold an idle
  connection open through a proxy.

| Event | Payload |
|---|---|
| `steps` | `{"steps": [...every closed step...], "tail": {...}}` |
| `tail` | `{"thinking", "text", "label"}` -- the step still being written |
| `steered` | `{"ids": [...]}` -- queued messages taken into the reply |
| `plan` | the plan, normalised |
| `status` | `{"text", "progress"}` -- `progress`: the phase object (spec 3.4.0) or null |
| `ask` | `{"cards": [...]}` -- empty when the last one was answered |
| `jobs` | `{"jobs": [...]}` -- only once there are any |
| `tree` | `{"tree": {...}}` -- the subagent tree (spec 3.4.0), once there is one |
| `metrics` | as on a message |
| `done` | `{"message", "chat": {"id", "title", "title_pending"}, "next": id or null}` |
| `title` | `{"chat": {"id", "title"}}` -- after `done`, only when a model named the chat |
| `close` | `{}` |

**`done` is the end of the reply**, sent as soon as the reply is saved. A new
chat is named by a model *after* that (seconds, on a reasoning model): `done`
carries the name made from the prompt and `title_pending: true`, and the
stream stays open only for the one `title` frame that follows when the name
is ready (at most `generation.TITLE_TIMEOUT` seconds), then `close`. An app
shows the reply as finished at `done` and never waits for `title`: one that
has gone by then finds the name on its next fetch of the chat or the list.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from lembas.api.chats import KEEPALIVE_AFTER, METRICS_INTERVAL, _think_label
from lembas.api.deps import Db
from lembas.api.v2 import serialise
from lembas.api.v2._common import Me, owned_chat, owned_message
from lembas.db.models import Chat, Message
from lembas.db.session import session_scope
from lembas.services import generation as generation_service
from lembas.services import metrics as metrics_service
from lembas.services import progress as progress_service
from lembas.services import sse, team_run, tree_view
from lembas.services import steps as steps_service

router = APIRouter()

# How often a chat's own subagent tree is looked at while a reply streams.
TREE_INTERVAL = 0.5


def frame(name: str, payload: object) -> str:
    return sse.event(name, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


@router.get("/chats/{chat_id}/messages/{message_id}/stream")
async def stream(caller: Me, db: Db, chat_id: str, message_id: str) -> StreamingResponse:
    chat = owned_chat(db, chat_id, caller.user)
    owned_message(db, chat, message_id)
    return StreamingResponse(
        follow_json(chat_id, message_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def follow_json(chat_id: str, message_id: str) -> AsyncIterator[str]:
    """See the module docstring; the twin of `api/chats.py:_follow`."""
    generation = generation_service.ensure(chat_id, message_id)
    generation.followers += 1
    seen = -1
    marks_done = -1
    last_frame = time.monotonic()
    last_metrics = 0.0
    unqueued: set[str] = set()
    # The last payload of each frame, as sent. A follower starts with nothing
    # sent -- not with the empty values, as the page's does -- because the app
    # has no streaming shell drawn already: its first pass is the whole state.
    sent: dict[str, str] = {}
    pacer = progress_service.Pacer()
    last_tree = 0.0
    with session_scope() as db:
        team_here = team_run.has_team(db, chat_id)

    def changed(name: str, payload: object) -> str | None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if sent.get(name) == encoded:
            return None
        sent[name] = encoded
        return sse.event(name, encoded)

    try:
        while True:
            if generation.version != seen:
                seen = generation.version
                thinking_tail, _ = steps_service.tail(generation)
                tail = serialise.tail(generation, _think_label(generation, thinking_tail))
                if len(generation.steps) != marks_done:
                    marks_done = len(generation.steps)
                    sent["tail"] = json.dumps(tail, ensure_ascii=False, separators=(",", ":"))
                    yield frame("steps", {"steps": serialise.live_steps(generation), "tail": tail})
                    if gone := steps_service.steered_ids(generation.steps) - unqueued:
                        unqueued |= gone
                        yield frame("steered", {"ids": sorted(gone)})
                if out := changed("tail", tail):
                    yield out
                current = getattr(generation, "plan", None) or generation.todos
                if current is not None and (out := changed("plan", serialise.plan(current))):
                    yield out
                status = status_payload(generation, pacer)
                if status is not None and (out := changed("status", status)):
                    yield out
                if out := changed("ask", {"cards": serialise.asks(generation.waiting)}):
                    yield out
                if generation.jobs and (
                    out := changed("jobs", {"jobs": serialise.jobs(generation.jobs)})
                ):
                    yield out
                if generation.team_tree and (
                    out := changed("tree", {"tree": generation.team_tree})
                ):
                    yield out
                last_frame = time.monotonic()

            # A chat's own tree (spec 3.4.0, subagents.md §12): its agents'
            # states, context and phases move without this reply's version,
            # so it is looked at on a clock, and sent when it changed.
            if team_here and time.monotonic() - last_tree > TREE_INTERVAL:
                last_tree = time.monotonic()
                with session_scope() as db:
                    chat_row = db.get(Chat, chat_id)
                    found = tree_view.server_tree(db, chat_row)[0] if chat_row else None
                if found and (out := changed("tree", {"tree": found})):
                    yield out
                    last_frame = time.monotonic()

            if time.monotonic() - last_metrics > METRICS_INTERVAL:
                last_metrics = time.monotonic()
                if out := changed(
                    "metrics", serialise.metrics(metrics_service.from_generation(generation))
                ):
                    yield out
                    last_frame = time.monotonic()

            if generation.done:
                break

            if time.monotonic() - last_frame > KEEPALIVE_AFTER:
                yield sse.KEEPALIVE
                last_frame = time.monotonic()

            await asyncio.sleep(generation_service.RENDER_INTERVAL * 0.8)
    finally:
        generation.followers = max(0, generation.followers - 1)

    with session_scope() as db:
        row = db.get(Message, message_id)
        chat = db.get(Chat, chat_id)
        if row is None or chat is None:
            yield frame("close", {})
            return
        # A prompt the queue handed to a fresh reply when this one ended: the
        # app attaches to it, as the page appends its bubble.
        following = (
            db.query(Message)
            .filter(Message.chat_id == chat.id, Message.complete.is_(False))
            .order_by(Message.created_at)
            .first()
            if generation.drained
            else None
        )
        naming = generation.title_task
        pending = naming is not None and not naming.done()
        done = done_payload(
            serialise.message(row),
            chat.id,
            chat.title,
            title_pending=pending,
            following=following.id if following is not None else None,
        )
    yield frame("done", done)
    if pending:
        # The name, when the model has it. Shielded: this follower going away
        # must not stop the naming, and the limit is the naming's own.
        named = ""
        try:
            named = await asyncio.wait_for(
                asyncio.shield(naming), generation_service.TITLE_TIMEOUT + 5
            )
        except Exception:  # timed out or failed: the name in `done` stands
            named = ""
        if named and named != done["chat"]["title"]:
            yield frame("title", title_payload(chat_id, named))
    yield frame("close", {})


def done_payload(
    message: dict, chat_id: str, title: str, *, title_pending: bool, following: str | None
) -> dict:
    """The `done` frame: the reply as saved, the chat's name as it stands, and
    the reply the queue started after this one (`next`), if any.

    `title_pending`: a model is still naming the chat, so `title` is the one
    made from the prompt and a `title` frame may follow before `close`."""
    return {
        "message": message,
        "chat": {"id": chat_id, "title": title, "title_pending": title_pending},
        "next": following,
    }


def title_payload(chat_id: str, title: str) -> dict:
    """The `title` frame: the name a model gave the chat after `done`."""
    return {"chat": {"id": chat_id, "title": title}}


def status_payload(generation, pacer: progress_service.Pacer | None = None) -> dict | None:
    """The `status` frame: the free text as before, and beside it the phase
    (spec 3.4.0, progress.md §5) -- or None while pacing holds a data-only
    change back and the text has not changed either."""
    value = progress_service.current(generation)
    if pacer is not None and not pacer.due(value):
        value = pacer.sent
    return {"text": generation.status, "progress": value}


__all__ = [
    "done_payload",
    "follow_json",
    "frame",
    "router",
    "status_payload",
    "title_payload",
]
