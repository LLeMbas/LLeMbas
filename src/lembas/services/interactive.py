"""Connections a provider allows for a person's own turns and nothing else.

Some subscriptions (Alibaba Cloud Model Studio's Token Plan and Coding Plan)
are sold for interactive use: their terms forbid automated services, batch
scripts and scheduled background jobs, and a key used for those can be
suspended. Whether a connection is one is a property of its provider type
(`providers.interactive_only`), never a name somebody matched.

Two rules follow, and this module is where both are decided:

1. **Scheduled work never runs on one.** A schedule -- on the server or on a
   linked computer -- cannot be given such a model (the forms do not offer it,
   the APIs refuse it), and a run whose model has since become one fails and
   says so. A scheduled run's subagents and fallbacks are the same run.
   `refuse_scheduled` is the check a reply makes before it is asked for.

2. **Background calls on a chat's behalf go elsewhere.** What is not the
   answer to the person's own turn -- naming the chat, compacting it -- is
   sent, when the chat's model is on such a connection, to (`background`):

   - the **same model id on a connection that is not** interactive-only, which
     the person may use and which is in the chat's data group (the plan's
     pay-as-you-go twin), else
   - the **instance's default model**, when that is not interactive-only
     either and is in the chat's data group, else
   - nowhere: the caller skips what can be skipped (a title falls back to the
     prompt's first words) and fails, saying why, what cannot (compaction).

What a person starts in a chat is interactive, and stays on the chat's model:
its replies, the tool calls inside them, the subagents and teams it starts
and the turns they wake.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import KIND_TASK, Chat, Model, User
from lembas.services.llm import providers
from lembas.services.llm.openai_client import Endpoint, LLMError
from lembas.web.i18n import N_, t

# Said to a person; translated where it is shown.
SCHEDULED_REFUSED = N_(
    "%(model)s is on a connection for interactive use only: its provider's terms do not "
    "allow scheduled or background work. Choose a model on a pay-as-you-go connection."
)
COMPACT_REFUSED = N_(
    "This chat's model is on a connection for interactive use only, and compacting is "
    "background work its provider's terms do not allow. It needs the same model on a "
    "pay-as-you-go connection, or a default model that is not interactive-only."
)

# How far up a chain of subagents a chat is followed to what started it.
_DEPTH = 8


def connection_only(connection: Any) -> bool:
    """Whether a connection row is interactive-only."""
    if connection is None:
        return False
    return providers.interactive_only(
        getattr(connection, "provider_type", ""), getattr(connection, "base_url", "")
    )


def endpoint_only(endpoint: Endpoint | None) -> bool:
    if endpoint is None:
        return False
    return providers.interactive_only(endpoint.provider_type, endpoint.base_url)


def model_only(model: Any) -> bool:
    """Whether a model row is on an interactive-only connection."""
    return model is not None and connection_only(getattr(model, "connection", None))


def usable(models: list[Model]) -> list[Model]:
    """`models` without the interactive-only ones: what scheduled work may use."""
    return [m for m in models if not model_only(m)]


def scheduled_work(db: DBSession, chat: Chat | None) -> bool:
    """Whether a chat is scheduled work: a run's own chat (a task chat, or an
    agent chat marked `scheduled`), or a subagent or team member under one."""
    seen = 0
    while chat is not None and seen < _DEPTH:
        if chat.kind == KIND_TASK or bool(getattr(chat, "scheduled", False)):
            return True
        if seen and _run_in(db, chat):
            # A subagent under a person's chat that a schedule is continuing
            # right now: the run's, not the person's. (The chat itself is
            # not asked this -- its run's reply is already on a model the
            # schedule may use, and the person's own turns stay theirs.)
            return True
        parent = getattr(chat, "parent_chat_id", None)
        chat = db.get(Chat, parent) if parent else None
        seen += 1
    return False


def _run_in(db: DBSession, chat: Chat) -> bool:
    from sqlalchemy import select

    from lembas.db.models import RUN_RUNNING, ScheduleRun

    return db.scalar(
        select(ScheduleRun.id)
        .where(ScheduleRun.chat_id == chat.id, ScheduleRun.status == RUN_RUNNING)
        .limit(1)
    ) is not None


def scheduled_refusal(label: str) -> str:
    return t(SCHEDULED_REFUSED, model=label)


def refuse_scheduled(db: DBSession, chat: Chat, endpoint: Endpoint, model_id: str) -> None:
    """Raise when a reply of scheduled work is about to be asked of an
    interactive-only connection. Nothing has been sent."""
    if endpoint_only(endpoint) and scheduled_work(db, chat):
        raise LLMError(scheduled_refusal(model_id))


def _candidates(db: DBSession, chat: Chat, owner: User | None) -> list[Model]:
    from lembas.services import chat as chat_service
    from lembas.services import data_groups

    group = data_groups.for_chat(db, chat)
    return usable(chat_service.available_models(db, owner, group))


def twin(db: DBSession, chat: Chat, owner: User | None, model_id: str) -> Model | None:
    """The same model id on a connection that is not interactive-only, which
    the chat's owner may use, in the chat's data group -- or None."""
    return next((m for m in _candidates(db, chat, owner) if m.model_id == model_id), None)


def background_model(
    db: DBSession, chat: Chat, owner: User | None, model_id: str
) -> Model | None:
    """Where a background call for an interactive-only chat goes: the twin,
    else the instance's default model, else None."""
    from lembas.services import settings_store

    rows = _candidates(db, chat, owner)
    found = next((m for m in rows if m.model_id == model_id), None)
    if found is not None:
        return found
    default = settings_store.get(db, "default_model")
    if default:
        return next((m for m in rows if m.model_id == default), None)
    return None


def background(
    db: DBSession, chat: Chat, endpoint: Endpoint, model_id: str
) -> tuple[Endpoint, str] | None:
    """The endpoint and model id a background call on `chat`'s behalf uses.

    Unchanged when the chat's connection is not interactive-only. Otherwise
    the twin's or the default model's -- or None, for the caller to skip the
    call or refuse it."""
    if not endpoint_only(endpoint):
        return endpoint, model_id
    owner = db.get(User, chat.user_id) if chat.user_id else None
    row = background_model(db, chat, owner, model_id)
    if row is None or row.connection is None:
        return None
    return Endpoint.from_connection(row.connection), row.model_id


def compact_refusal(db: DBSession, chat: Chat) -> str:
    """Why this chat cannot be compacted here for being interactive-only, said
    to a person, or "". Checks without asking a model anything."""
    from lembas.services import chat as chat_service

    if chat.device_token_id:
        return ""  # the device compacts its own session
    try:
        endpoint, model_id = chat_service.resolve_endpoint(db, chat)
    except LLMError:
        return ""
    return "" if background(db, chat, endpoint, model_id) is not None else t(COMPACT_REFUSED)


__all__ = [
    "COMPACT_REFUSED",
    "SCHEDULED_REFUSED",
    "background",
    "background_model",
    "compact_refusal",
    "connection_only",
    "endpoint_only",
    "model_only",
    "refuse_scheduled",
    "scheduled_refusal",
    "scheduled_work",
    "twin",
    "usable",
]
