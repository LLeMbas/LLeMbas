"""Which source serves a capability: the one decision, made in one place.

For a capability (`stt`, `tts`, `image`) and the connection of a chat's
model, `chain` answers the sources to try **in order**:

1. the connection's **own** -- its switch for the capability is on and it has
   a model enabled (`store.own`);
2. the instance's **default** -- a connection's model the administrator chose
   as the default, or the endpoint configured as before (the audio settings'
   two endpoints, the ComfyUI).

So a connection without media of its own stays on the default, and the only
fallback there is goes from a connection's own to the default, once. A chat
on a local model is never sent to a cloud connection's media unless the
administrator made that source the default themselves.

**Interactive-only.** A source on a connection its provider sells for a
person's own turns (`services/interactive.py`) is left out of the chain when
the work is not one: `interactive=False` -- a scheduled run's tool call, a
personal API key on `/v1`.

**Which chat.** `connection_of` is the definition: the connection of the
chat's current model, or of the model chosen for a chat that does not exist
yet; with neither, none -- and the chain is the default alone.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Chat, Connection, User
from lembas.services import settings_store
from lembas.services.llm.openai_client import Endpoint, LLMError
from lembas.services.media import store
from lembas.services.media.store import CAP_IMAGE, CAP_STT, CAP_TTS
from lembas.web.i18n import t

log = logging.getLogger(__name__)

ORIGIN_CONNECTION = "connection"
ORIGIN_INSTANCE = "instance"
# The instance's own ComfyUI: a picture source that `services/images/` draws
# with, workflow and checkpoints and all.
COMFYUI = "comfyui"

# The settings key that names a connection's model as the instance's default
# for a capability (`<connection id>/<model id>`; empty: the endpoint).
DEFAULT_KEYS = {CAP_STT: "stt_connection", CAP_TTS: "tts_connection", CAP_IMAGE: "connection"}

T = TypeVar("T")


@dataclass(frozen=True)
class Source:
    """One place a capability can be served from, as a snapshot: the key is
    already decrypted inside `endpoint`, so nothing downstream holds a
    database session -- or sees a stored secret -- while it works."""

    capability: str
    origin: str
    # The chat's connection's own model, as against the instance's default.
    own: bool
    # What a person is told answered: a connection's name and the model, or
    # the instance's default by its model.
    label: str
    model: str
    dialect: str
    endpoint: Endpoint | None = None
    # Money per unit of the capability, in `currency` (None: the instance's).
    price: float = 0.0
    currency: str | None = None
    voice: str = ""
    connection_id: str = ""
    provider: str = ""
    # The instance's settings group, for a default served by its endpoint.
    config: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    @property
    def usage_id(self) -> str:
        """What the usage page and the ledger call this source."""
        if self.origin == ORIGIN_CONNECTION:
            return f"{self.provider}/{self.model}"
        if self.capability == CAP_IMAGE:
            return f"{COMFYUI}/{self.model}" if self.model else COMFYUI
        from lembas.services import audio as audio_service

        return audio_service.service_id(self.config, self.capability)


@dataclass(frozen=True)
class Used:
    """What `first_that_works` hands back: the result, the source that gave
    it, and -- when that was not the first one tried -- why the first did not."""

    result: Any
    source: Source
    fell_back: bool = False
    reason: str = ""


def _settings(db: DBSession, capability: str) -> dict[str, Any]:
    return settings_store.images(db) if capability == CAP_IMAGE else settings_store.audio(db)


def enabled(db: DBSession, capability: str) -> bool:
    """The instance's switch for the capability: dictation, read-aloud,
    image generation. Off is off for every source."""
    values = _settings(db, capability)
    return bool(values.get("enabled" if capability == CAP_IMAGE else f"{capability}_enabled"))


def _from_connection(
    connection: Connection, entry: store.Entry, *, own: bool
) -> Source:
    from lembas.services.llm import providers

    # A subscription's media is counted and never priced, like its replies.
    plan = providers.subscription(connection.provider_type, connection.base_url)
    return Source(
        capability=entry.capability,
        origin=ORIGIN_CONNECTION,
        own=own,
        label=f"{connection.name} · {entry.model}",
        model=entry.model,
        dialect=store.dialect(
            connection.provider_type, connection.base_url, entry.capability, entry.model
        ),
        endpoint=Endpoint.from_connection(connection),
        price=0.0 if plan else entry.price,
        currency=connection.currency or None,
        voice=entry.voice,
        connection_id=connection.id,
        provider=connection.provider_name,
    )


def split_default(value: Any) -> tuple[str, str]:
    """`<connection id>/<model id>` as its two halves, or two empty strings."""
    connection_id, _, model = str(value or "").partition("/")
    return (connection_id, model) if connection_id and model else ("", "")


def default(db: DBSession, capability: str) -> Source | None:
    """The instance's default source for a capability, or None when nothing
    is configured: a connection's model the administrator chose, else the
    endpoint in the settings."""
    values = _settings(db, capability)
    connection_id, model = split_default(values.get(DEFAULT_KEYS[capability]))
    if connection_id:
        connection = db.get(Connection, connection_id)
        entry = store.find(connection, capability, model) if connection is not None else None
        if (
            connection is not None
            and connection.enabled
            and entry is not None
            and store.supported(connection.provider_type, connection.base_url)
        ):
            return _from_connection(connection, entry, own=False)
        # The chosen model has gone (deleted, switched off): nothing is
        # substituted for a choice an administrator made on purpose.
        return None
    if capability == CAP_IMAGE:
        if not (values.get("base_url") and values.get("checkpoints")):
            return None
        return Source(
            capability=capability, origin=ORIGIN_INSTANCE, own=False, label="ComfyUI",
            model="", dialect=COMFYUI, config=values,
        )
    if not str(values.get(f"{capability}_base_url") or "").strip():
        return None
    from lembas.services import audio as audio_service

    model_id = str(values.get(f"{capability}_model") or "").strip()
    return Source(
        capability=capability, origin=ORIGIN_INSTANCE, own=False,
        label=model_id or audio_service.service_id(values, capability),
        model=model_id or ("whisper-1" if capability == CAP_STT else "tts-1"),
        dialect=store.OPENAI,
        endpoint=audio_service.endpoint_for(values, capability),
        price=audio_service.price_of(values, capability),
        config=values,
    )


def _allowed(connection: Connection | None, interactive: bool) -> bool:
    from lembas.services import interactive as interactive_rules

    return interactive or not interactive_rules.connection_only(connection)


def chain(
    db: DBSession,
    capability: str,
    connection: Connection | None = None,
    *,
    interactive: bool = True,
) -> list[Source]:
    """The sources to try for a capability, in order: the connection's own,
    then the instance's default. Empty when the capability is off for the
    instance, or nothing is configured that this kind of work may use."""
    if not enabled(db, capability):
        return []
    out: list[Source] = []
    if (
        connection is not None
        and connection.enabled
        and store.supported(connection.provider_type, connection.base_url)
        and _allowed(connection, interactive)
    ):
        entry = store.own(connection, capability)
        if entry is not None:
            out.append(_from_connection(connection, entry, own=True))
    fallback = default(db, capability)
    if fallback is not None:
        behind = db.get(Connection, fallback.connection_id) if fallback.connection_id else None
        same = bool(out) and (out[0].connection_id, out[0].model) == (
            fallback.connection_id, fallback.model
        ) and fallback.origin == ORIGIN_CONNECTION
        if not same and (behind is None or _allowed(behind, interactive)):
            out.append(fallback)
    return out


def anywhere(db: DBSession, capability: str) -> bool:
    """Whether the capability can be served for some chat on this instance:
    it is on, and there is a default or a connection with its own."""
    if not enabled(db, capability):
        return False
    if default(db, capability) is not None:
        return True
    return any(
        store.own(connection, capability) is not None
        for connection in db.scalars(select(Connection).where(Connection.enabled.is_(True)))
        if store.supported(connection.provider_type, connection.base_url)
    )


def choices(db: DBSession, capability: str) -> list[dict[str, str]]:
    """The connections' enabled models for a capability, for the settings'
    "use a connection's model" choice: `value` is what `DEFAULT_KEYS` stores."""
    out: list[dict[str, str]] = []
    for connection in db.scalars(select(Connection).order_by(Connection.position)):
        if not store.supported(connection.provider_type, connection.base_url):
            continue
        for entry in store.entries(connection, capability, enabled=True):
            out.append(
                {
                    "value": f"{connection.id}/{entry.model}",
                    "label": f"{connection.name} · {entry.model}",
                }
            )
    return out


def connection_of(
    db: DBSession, user: User | None, *, chat_id: str = "", model: str = ""
) -> Connection | None:
    """The connection a speech or picture request belongs to: that of the
    chat's current model (the person's own chat), or of the model chosen for
    a chat that does not exist yet (a model this person may use). Neither, or
    one that does not resolve: None, which is the instance's default."""
    from lembas.services import chat as chat_service

    if user is None:
        return None
    if chat_id:
        chat = db.get(Chat, chat_id)
        if chat is None or chat.user_id != user.id:
            return None
        return connection_of_chat(db, chat)
    if model:
        row = chat_service.pick_model(chat_service.available_models(db, user), model)
        return row.connection if row is not None else None
    return None


def connection_of_chat(db: DBSession, chat: Chat, speaker: Any = None) -> Connection | None:
    from lembas.services import chat as chat_service

    speaker = speaker or chat_service.speaker_for(db, chat)
    if speaker.connection_id:
        connection = db.get(Connection, speaker.connection_id)
        if connection is not None:
            return connection
    row = chat_service.model_row(db, speaker)
    return row.connection if row is not None else None


async def first_that_works(
    sources: list[Source], call: Callable[[Source], Awaitable[T]]
) -> Used:
    """`call` on the first source, and -- when that fails and there is
    another -- on the next, once. The last failure is the one raised."""
    if not sources:
        raise LLMError(t("Nothing is configured to do that on this instance."))
    reason = ""
    for number, source in enumerate(sources[:2]):
        try:
            result = await call(source)
        except LLMError as exc:
            if number == 0 and len(sources) > 1:
                # A provider's own words: kept short, and shown escaped.
                reason = exc.message[:300]
                log.info("%s on %s failed, trying the default: %s",
                         source.capability, source.label, reason)
                continue
            raise
        return Used(result, source, fell_back=number > 0, reason=reason)
    raise LLMError(reason)  # pragma: no cover - the loop returns or raises


def record(
    db: DBSession,
    user_id: str,
    source: Source,
    *,
    seconds: float = 0,
    characters: int = 0,
    images: int = 0,
    usage_id: str = "",
) -> int:
    """Count one use against the source that served it, priced at that
    source's own price in its own currency. Returns the cost in micro-units.
    Never raises (`usage.record`); the caller commits."""
    from lembas.services import pricing
    from lembas.services import usage as usage_service

    quantity = seconds / 60 if seconds else characters / 1_000_000 if characters else images
    cost = pricing.service_cost_micro(source.price, quantity)
    usage_service.record(
        db,
        user_id,
        model_id=usage_id or source.usage_id,
        requests=1,
        cost_micro=cost,
        images=images,
        audio_seconds=seconds,
        speech_chars=characters,
        currency=source.currency,
    )
    return cost


__all__ = [
    "COMFYUI",
    "DEFAULT_KEYS",
    "ORIGIN_CONNECTION",
    "ORIGIN_INSTANCE",
    "Source",
    "Used",
    "anywhere",
    "chain",
    "choices",
    "connection_of",
    "connection_of_chat",
    "default",
    "enabled",
    "first_that_works",
    "record",
    "split_default",
]
