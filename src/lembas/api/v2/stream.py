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
| `status` | `{"text"}` |
| `ask` | `{"cards": [...]}` -- empty when the last one was answered |
| `jobs` | `{"jobs": [...]}` -- only once there are any |
| `metrics` | as on a message |
| `done` | `{"message", "chat": {"id", "title"}, "next": id or null}` |
| `close` | `{}` |
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
from lembas.services import sse
from lembas.services import steps as steps_service

router = APIRouter()


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
                if out := changed("status", {"text": generation.status}):
                    yield out
                if out := changed("ask", {"cards": serialise.asks(generation.waiting)}):
                    yield out
                if generation.jobs and (
                    out := changed("jobs", {"jobs": serialise.jobs(generation.jobs)})
                ):
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
        done = {
            "message": serialise.message(row),
            "chat": {"id": chat.id, "title": chat.title},
            "next": following.id if following is not None else None,
        }
    yield frame("done", done)
    yield frame("close", {})


__all__ = ["follow_json", "frame", "router"]
