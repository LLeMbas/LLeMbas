"""Fallback models: opt-in, ordered, and said out loud.

A model may list other models to try when its own connection fails
(`Model.fallback_model_ids`). With none listed -- the default -- nothing here
does anything and a failed reply fails as it always did.

**When it falls back.** Only when the connection failed before a single chunk
of the reply arrived, and only for a failure of the connection rather than of
the request: unreachable, timed out, or a 5xx. Never on a Stop (that is not an
error at all), never on a 4xx -- a refused request, a content refusal, a
context too long are the request's problem and another model would be handed
the same one -- and never once the reply has started, which would write a
second answer after the first.

**Which models.** The listed ones the chat's owner may use, enabled, on an
enabled connection, **in the chat's own data group**: a fallback is never a way
to hand a conversation to a provider its group keeps it from.

**Said, and recorded.** The reply opens with a line naming the model that did
not answer and the one that did, and the message row records the model that
actually answered (`Message.model_id`, `connection_id`), which is what its
cost is then worked out for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.services.llm.openai_client import Endpoint, LLMError, StreamError
from lembas.web.i18n import t


@dataclass(frozen=True)
class Candidate:
    endpoint: Endpoint
    model_id: str
    connection_id: str
    label: str


@dataclass
class Route:
    """Where a reply's requests go: the model asked, then its fallbacks."""

    endpoint: Endpoint
    model_id: str
    label: str = ""
    fallbacks: list[Candidate] = field(default_factory=list)
    # Set once a fallback has taken over: the candidate now answering.
    taken: Candidate | None = None


def plan(db: DBSession, chat, owner, model, endpoint: Endpoint, model_id: str) -> Route:
    """The route for a reply on `model` (its row; None: no fallbacks)."""
    route = Route(endpoint=endpoint, model_id=model_id,
                  label=(model.label if model is not None else model_id))
    wanted = list(getattr(model, "fallback_model_ids", None) or []) if model is not None else []
    if not wanted:
        return route
    from lembas.services import chat as chat_service
    from lembas.services import data_groups

    group = data_groups.for_chat(db, chat)
    usable = {m.id: m for m in chat_service.available_models(db, owner, group=group)}
    for row_id in wanted:
        candidate = usable.get(row_id)
        if candidate is None or candidate.id == getattr(model, "id", None):
            continue
        connection = candidate.connection
        if connection is None or not connection.enabled or not candidate.enabled:
            continue
        route.fallbacks.append(
            Candidate(
                endpoint=Endpoint.from_connection(connection),
                model_id=candidate.model_id,
                connection_id=connection.id,
                label=candidate.label,
            )
        )
    return route


def eligible(exc: LLMError) -> bool:
    """Whether this failure is the connection's -- the only kind a fallback
    answers. A transport failure carries no status; a 5xx is the server's."""
    status = exc.status_code
    if isinstance(exc, StreamError):
        return status is not None and status >= 500
    return status is None or status >= 500


def note(failed: str, answered: str, reason: str) -> str:
    """The line a reply opens with when another model answered it."""
    return (
        "*("
        + t(
            "%(failed)s did not answer (%(reason)s), so %(answered)s answered instead.",
            failed=failed,
            answered=answered,
            reason=reason.rstrip("."),
        )
        + ")*\n\n"
    )


async def stream(route: Route, payload: dict[str, Any], generation, stream_once):
    """`stream_once(endpoint, payload, generation, model_id)`'s chunks, moving
    down the route's fallbacks when the connection fails before the first one.
    Once a fallback has answered, it stays for the rest of the reply."""
    while True:
        sent = False
        try:
            async for chunk in stream_once(route.endpoint, payload, generation, route.model_id):
                sent = True
                yield chunk
            return
        except LLMError as exc:
            if (
                sent
                or not route.fallbacks
                or not eligible(exc)
                or getattr(generation, "cancel", False)
            ):
                raise
            failed = route.taken.label if route.taken is not None else route.label
            nxt = route.fallbacks.pop(0)
            route.endpoint, route.model_id, route.taken = nxt.endpoint, nxt.model_id, nxt
            payload["model"] = nxt.model_id
            generation.fallback = {
                "model_id": nxt.model_id,
                "connection_id": nxt.connection_id,
                "label": nxt.label,
            }
            generation.content.append(note(failed, nxt.label, exc.message))
            generation.touch()


__all__ = ["Candidate", "Route", "eligible", "note", "plan", "stream"]
