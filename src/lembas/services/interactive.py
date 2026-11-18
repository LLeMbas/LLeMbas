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

# The spec's names for the background calls and what each does with nowhere
# to go (`loop.json` `interactive.background`).
CALL_TITLE = "title"
CALL_COMPACTION = "compaction"
CALL_AUTO_COMPACTION = "auto_compaction"


# --- The rules (pure; harness spec 3.9.0 `interactive.md`, conformance `interactive.json`) ---
def _only_ids(models: list[dict[str, Any]]) -> set[str]:
    return {str(m.get("id")) for m in models if m.get("interactive_only")}


def _tail(model_id: str) -> str:
    """What names the model whoever serves it: the id after its first `/`."""
    return model_id.split("/", 1)[1] if "/" in model_id else model_id


def twin_id(model: str, models: list[dict[str, Any]]) -> str | None:
    """The same model on a connection that is not interactive-only: another
    served id with the same part after its first `/`, the first in the list's
    order -- or None."""
    only = _only_ids(models)
    for entry in models:
        other = str(entry.get("id"))
        if other != model and other not in only and _tail(other) == _tail(model):
            return other
    return None


def background_call(
    call: str, model: str, models: list[dict[str, Any]], defaults: list[str],
    unfit: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Where a background call that would use `model` goes: `{model}` (itself,
    its twin, or the first default that is listed and not interactive-only),
    `{skip: True}`, or `{refused}` with the spec's sentence. `unfit`: models
    that cannot take the call (a summary too large for their window) --
    passed over like ones that are not listed, twin and default alike."""
    from lembas.services import harness_spec

    only = _only_ids(models)
    if model not in only:
        return {"model": model}
    models = [m for m in models if str(m.get("id")) == model or str(m.get("id")) not in unfit]
    found = twin_id(model, models)
    if found is None:
        listed = {str(m.get("id")) for m in models}
        found = next((d for d in defaults if d in listed and d not in only), None)
    if found is not None:
        return {"model": found}
    rules = harness_spec.loop()["interactive"]
    if rules["background"].get(call) == "refuse":
        return {"refused": rules["texts"]["compact"]}
    return {"skip": True}


def scheduled_run(model: str, models: list[dict[str, Any]]) -> dict[str, Any]:
    """Scheduled work on `model`: `{run: True}`, or `{refused}` with the
    spec's sentence -- never quietly on its twin."""
    from lembas.services import harness_spec

    if model not in _only_ids(models):
        return {"run": True}
    text = harness_spec.loop()["interactive"]["texts"]["scheduled"]
    return {"refused": harness_spec.fill(text, {"model": model})}


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
    """Whether what a chat is doing now is scheduled work: a run's own turn
    in its chat (a task chat, or an agent chat marked `scheduled`), or a
    subagent or team member working under one.

    **A person writing into a run's chat afterwards is a person**
    (`interactive.md` §2): that turn is interactive, and so is what it
    starts. A run's turn is the one the runner wrote -- a `machine` turn --
    so a run's chat is scheduled work while the turn it last took is one,
    and not while it is the person's own."""
    seen = 0
    while chat is not None and seen < _DEPTH:
        if chat.kind == KIND_TASK or bool(getattr(chat, "scheduled", False)):
            return not _persons_turn(db, chat)
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


def _persons_turn(db: DBSession, chat: Chat) -> bool:
    """Whether the turn a run's chat took last is a person's own: its newest
    user message is one they wrote, not one the runner did."""
    from sqlalchemy import select

    from lembas.db.models import ROLE_USER, Message

    last = db.scalars(
        select(Message)
        .where(Message.chat_id == chat.id, Message.role == ROLE_USER)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).first()
    return last is not None and not bool(last.machine)


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


def background_model(
    db: DBSession, chat: Chat, owner: User | None, model_id: str, *, needs: int = 0,
    tools: bool = False,
) -> Model | None:
    """Where a background call for an interactive-only chat goes: the twin,
    else the instance's default model, else None.

    `needs`: about how many tokens the call sends; `tools`: whether what is
    sent holds tool calls. A model whose stated context window is smaller, or
    that takes no tools for such a conversation, is no place to send it -- it
    would fail on every turn -- and is passed over like one that is not
    there (the spec's `unfit`)."""
    from lembas.services import chat as chat_service
    from lembas.services import data_groups, settings_store

    # The spec's rule (`background_call`) over what this person may use in
    # the chat's data group, each row named by its own id so two connections
    # serving one model id are two entries with one tail.
    rows = chat_service.available_models(db, owner, data_groups.for_chat(db, chat))
    by_id = {f"{m.id}/{m.model_id}": m for m in rows}
    asked = next(
        (key for key, m in by_id.items() if m.model_id == model_id and model_only(m)), None
    )
    listed = [{"id": key, "interactive_only": model_only(m)} for key, m in by_id.items()]
    unfit = [
        key for key, m in by_id.items()
        if (needs and m.context_length and needs > m.context_length)
        or (tools and not (m.capabilities_json or {}).get("tools"))
    ]
    if asked is None:
        # The chat's own row is not among them (taken from the person
        # since): still an interactive-only model looking for its twin.
        asked = f"?/{model_id}"
        listed.append({"id": asked, "interactive_only": True})
    default = settings_store.get(db, "default_model")
    defaults = [key for key, m in by_id.items() if default and m.model_id == default]
    went = background_call(CALL_TITLE, asked, listed, defaults, unfit)
    return by_id.get(went.get("model", ""))


def route(
    db: DBSession, chat: Chat, endpoint: Endpoint, model_id: str, *, needs: int = 0,
    tools: bool = False,
) -> tuple[Endpoint, str, Model | None] | None:
    """Where a background call on `chat`'s behalf goes: the endpoint, the
    model id, and -- when it goes elsewhere -- the row it goes to, which is
    then the model the call is shaped for, counted on and priced by.

    Unchanged (the row None) when the chat's connection is not
    interactive-only. None when there is nowhere: the caller skips the call
    or refuses it."""
    if not endpoint_only(endpoint):
        return endpoint, model_id, None
    owner = db.get(User, chat.user_id) if chat.user_id else None
    row = background_model(db, chat, owner, model_id, needs=needs, tools=tools)
    if row is None or row.connection is None:
        return None
    return Endpoint.from_connection(row.connection), row.model_id, row


def background(
    db: DBSession, chat: Chat, endpoint: Endpoint, model_id: str
) -> tuple[Endpoint, str] | None:
    """`route`, for a caller that needs only where to send."""
    went = route(db, chat, endpoint, model_id)
    return None if went is None else (went[0], went[1])


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
    "background_call",
    "background_model",
    "compact_refusal",
    "connection_only",
    "endpoint_only",
    "model_only",
    "route",
    "scheduled_refusal",
    "scheduled_run",
    "scheduled_work",
    "twin_id",
    "usable",
]
