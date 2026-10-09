"""OpenAI-compatible endpoint connections and their discovered models."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    select,
)
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.models.data_group import InDataGroup
from lembas.db.types import JSONDict, JSONList

if TYPE_CHECKING:
    # Import only for the annotation; at runtime SQLAlchemy resolves the
    # name through its own class registry, so there is no import cycle.
    from lembas.db.models.user import Group

# Which groups may use a given model. A model with no rows here is reachable
# only by administrators unless it is marked public.
model_groups = Table(
    "model_groups",
    Base.metadata,
    Column("model_id", String(32), ForeignKey("models.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", String(32), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)


# What a connection speaks. OpenAI's dialect came first, which is why it is the
# default and what the migration leaves old rows on.
KIND_OPENAI = "openai"
KIND_ANTHROPIC = "anthropic"
KINDS = (KIND_OPENAI, KIND_ANTHROPIC)

# A connection's provider name: the first half of `provider/model`, the
# id `/v1` serves a model under and the LLeMbas CLI names it by. Lowercase, so
# `Llama` and `llama` cannot be two providers that a person cannot tell apart.
# What a model row is for, as `/v1/models` names it. A row is a chat
# model or an embedding model; speech and pictures are not rows at all -- they
# come from the instance's audio and image settings, and `/v1/models` lists them
# beside these as `stt`, `tts` and `image`.
MODEL_CHAT = "chat"
MODEL_EMBEDDING = "embedding"
MODEL_KINDS = (MODEL_CHAT, MODEL_EMBEDDING)


def guess_kind(model_id: str) -> str:
    """The kind a newly discovered model most likely is, from its id alone.

    Only "embed" in the id is trusted: `text-embedding-3-small`, `nomic-embed-
    text`, `bge-m3-embed`. Everything else is a chat model until an
    administrator says otherwise on the model's page -- a wrong guess either way
    is one select to put right, and no other part of an id is a reliable sign.
    """
    return MODEL_EMBEDDING if "embed" in (model_id or "").lower() else MODEL_CHAT


PROVIDER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
PROVIDER_MAX = 32


def provider_slug(name: str) -> str:
    """A provider name made from a connection's display name: lowercased,
    anything else to `-`, trimmed. Never empty -- a name of punctuation alone
    still has to become something."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:PROVIDER_MAX]
    return slug.strip("-") or "connection"


def unique_provider(base: str, taken: set[str]) -> str:
    """`base`, or `base-2`, `base-3`... -- the first that is not in `taken`."""
    if base not in taken:
        return base
    number = 2
    while True:
        suffix = f"-{number}"
        candidate = f"{base[: PROVIDER_MAX - len(suffix)].rstrip('-')}{suffix}"
        if candidate not in taken:
            return candidate
        number += 1


class Connection(UUIDPrimaryKey, Timestamps, InDataGroup, Base):
    """A configured upstream endpoint: OpenAI's HTTP dialect or Anthropic's.

    The OpenAI dialect covers api.openai.com as well as LM Studio, vLLM,
    llama.cpp, Ollama's compatibility layer, OpenRouter, and anything else
    exposing /v1. Anthropic's Messages API is the other one.
    """

    __tablename__ = "connections"
    # Unique, but an index rather than a column constraint: SQLite cannot add a
    # UNIQUE constraint to an existing table, and an index it can. The migration
    # creates the same index, by the same name, on a database that predates it.
    __table_args__ = (Index("ix_connections_provider", "provider", unique=True),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)

    # Which dialect the endpoint speaks: `openai` or `anthropic`. Decides
    # which client talks to it -- in one place, `services/llm/openai_client`.
    kind: Mapped[str] = mapped_column(String(16), default=KIND_OPENAI, nullable=False)

    # The provider name, see PROVIDER_PATTERN. Nullable only because a
    # column added to rows that exist cannot be both NOT NULL and unique; every
    # row is given one -- old rows by the migration, new ones on insert below.
    provider: Mapped[str | None] = mapped_column(String(PROVIDER_MAX), nullable=True)

    # Fernet ciphertext, never the raw key. See lembas.services.crypto.
    # Empty string is legitimate: local endpoints often need no auth at all.
    api_key_encrypted: Mapped[str] = mapped_column(Text, default="")

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Extra headers merged into every request (e.g. OpenRouter's HTTP-Referer).
    extra_headers_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    # How to ask this endpoint to drop its model from memory, for the Preserve
    # VRAM option in image generation. Per connection and not instance-wide,
    # because the VRAM being freed is a particular machine's: llama-swap on this
    # host answers `GET /unload`, while a remote vLLM has no such call and no
    # reason to be unloaded when ComfyUI needs memory *here*.
    #
    # Empty means "this connection cannot be unloaded", which is the honest
    # default -- there is no call that works everywhere, and guessing one would
    # send an unexplained request to somebody's endpoint.
    unload_url: Mapped[str] = mapped_column(String(500), default="")
    unload_method: Mapped[str] = mapped_column(String(8), default="POST")

    # Whether this endpoint holds one model at a time -- llama-swap in front of
    # one GPU, which swaps the model out to serve another. A model here may then
    # be its own helper, never another model from this connection: the helper
    # would evict the model whose reply is waiting on it. Named for the
    # *restrictive* state on purpose: `sync_schema` backfills a NOT NULL boolean
    # with False, so False has to mean "as before" (any number of models at once).
    one_model_at_a_time: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Result of the most recent "Test & refresh", surfaced in the admin list.
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")

    models: Mapped[list[Model]] = relationship(
        back_populates="connection",
        cascade="all, delete-orphan",
        order_by="Model.model_id",
    )

    @property
    def is_anthropic(self) -> bool:
        return (self.kind or KIND_OPENAI) == KIND_ANTHROPIC

    @property
    def provider_name(self) -> str:
        """The provider, or one made from the name for a row that has none yet."""
        return self.provider or provider_slug(self.name)

    def __repr__(self) -> str:
        return f"<Connection {self.name} {self.base_url}>"


@event.listens_for(Connection, "before_insert")
def _give_a_provider(mapper, connection, target: Connection) -> None:
    """Every new row gets a provider name, whoever made it: a test, a script, or
    an older form. Made from the name and de-duplicated against the table, the
    same way the migration names the rows that predate the column."""
    if target.provider:
        return
    table = Connection.__table__
    taken = {
        row[0]
        for row in connection.execute(select(table.c.provider).where(table.c.provider.is_not(None)))
    }
    # Rows in the same flush are not in the table yet, but those named before
    # this one already carry their provider.
    session = object_session(target)
    for pending in session.new if session is not None else ():
        if isinstance(pending, Connection) and pending is not target and pending.provider:
            taken.add(pending.provider)
    target.provider = unique_provider(provider_slug(target.name), taken)


class Model(UUIDPrimaryKey, Timestamps, Base):
    """A model advertised by a connection, cached locally.

    Cached rather than fetched live so the chat UI stays responsive and keeps
    working when an endpoint is briefly unreachable. Refreshed on demand from
    the admin screen.
    """

    __tablename__ = "models"
    __table_args__ = (UniqueConstraint("connection_id", "model_id"),)

    connection_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_id: Mapped[str] = mapped_column(String(300), nullable=False)
    display_name: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")

    # What the *other* models are told about this one, when the roster is in
    # front of them. Separate from `description`, which is written for people
    # and reads like marketing; this is meant to be facts -- parameters,
    # quantisation, a benchmark figure, what it is bad at.
    #
    # A column and not a key in `capabilities_json`, for the reason
    # `context_length` and `reasoning_efforts` both carry: that dict is rebuilt
    # wholesale from the submitted checkboxes on every save.
    notes: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Sort order in every picker. Ties fall back to model_id so the order is
    # stable rather than whatever SQLite feels like today.
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Pinned models are offered first, before the full list.
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Whether this model serves one request at a time, so it cannot be its own
    # helper: the helper would take the only slot and push out the conversation
    # the server has cached for the waiting reply. The restrictive state, for the backfill reason on
    # `Connection.one_model_at_a_time`. A column and not a `capabilities_json`
    # key, because that dict is rebuilt from the checkboxes on every save.
    single_session: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Public models are usable by anyone; otherwise access comes from `groups`.
    public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Filename under <data>/uploads/models. Stored rather than a URL so the
    # image cannot become a request to a third party on every page render.
    image_path: Mapped[str] = mapped_column(String(300), default="")

    # Applied to chats using this model when the chat has none of its own.
    # See services.chat.effective_system_prompt for the precedence.
    system_prompt: Mapped[str] = mapped_column(Text, default="")

    # Endpoints do not reliably advertise capabilities, so these are admin
    # overrides. Recognised keys: vision, tools, reasoning.
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    # Default sampling params applied to new chats using this model.
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)
    # What a reply on this model costs: per million tokens for
    # input, cached_input, output and reasoning, and per_request. Empty is free,
    # which is how a local model stays free beside priced ones. A column, not a
    # key in capabilities_json, for the reason context_length is one.
    #
    # The prices are **money** per million tokens (and per request), in
    # the instance's currency, marked `"unit": "money"`; what that is in credits
    # follows `credits.credit_value`. A dict with no mark is an older one
    # and holds credits -- `services/credits.prices` reads it as such, so it is
    # right whether or not `migrations.prices_to_money` has run yet.
    pricing_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    # Chat or embedding (`MODEL_KINDS`): what `/v1/models` says the model
    # is, and which of `/v1/chat/completions` and `/v1/embeddings` takes it.
    # Nullable on purpose: a row from before the column existed arrives NULL -- "nobody has
    # said" -- and is served as a chat model that `/v1/embeddings` still
    # accepts, so an upgrade breaks no client that embedded through a model it
    # had never been told the kind of. `migrations.infer_model_kinds` marks the
    # ones the instance itself uses for embeddings; the model page sets the rest.
    kind: Mapped[str | None] = mapped_column(String(16), default=MODEL_CHAT)

    # How many tokens this model can hold. 0 means unknown, which is what an
    # endpoint that does not advertise it leaves behind -- and unknown has to
    # stay tellable from "small", because the context percentage and automatic
    # compaction both refuse to act on a number nobody supplied.
    #
    # A column rather than a key in capabilities_json: that dict is rebuilt
    # wholesale from the submitted checkboxes on every save (api/admin_models.py),
    # so a number living in it would be destroyed the next time an administrator
    # ticked anything.
    context_length: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # The most the model can write in one reply, where its endpoint states it
    # (Anthropic's model list does). 0 is unknown. A ceiling only -- a request
    # is never made to ask for all of it by default -- and a column for the
    # reason `context_length` is one.
    output_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Which reasoning efforts this model actually accepts. Empty means "nobody
    # has said", and `services/chat.efforts_for` answers with the common set.
    #
    # It has to be per model, because the vocabulary is: gpt-oss takes
    # low/medium/high, Bonsai takes low/medium/xhigh and *raises* on high, and
    # OpenAI's own list has grown minimal, xhigh and max at different times. A
    # single global tuple is a guess that is wrong for somebody.
    #
    # ⚠ A column and not a key in `capabilities_json`, for exactly the reason
    # `context_length` is one: that dict is rebuilt wholesale from the submitted
    # checkboxes on every save, so anything in it that is not a checkbox is
    # destroyed the next time an administrator ticks anything.
    reasoning_efforts: Mapped[list[str]] = mapped_column(JSONList, default=list)

    connection: Mapped[Connection] = relationship(back_populates="models")
    groups: Mapped[list[Group]] = relationship(
        "Group", secondary=model_groups, back_populates="models"
    )

    @property
    def label(self) -> str:
        return self.display_name or self.model_id

    @property
    def supports_reasoning(self) -> bool:
        return bool((self.capabilities_json or {}).get("reasoning"))

    @property
    def initial(self) -> str:
        """First character of the label, for the fallback avatar."""
        return (self.label.strip() or "?")[0].upper()

    def __repr__(self) -> str:
        return f"<Model {self.model_id}>"
