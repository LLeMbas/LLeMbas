"""Folders, chats and messages."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.models.data_group import InDataGroup
from lembas.db.types import JSONDict, JSONList

if TYPE_CHECKING:
    # Annotation only; SQLAlchemy resolves the name through its own registry at
    # runtime, so there is no import cycle. A bare `Mapped[list]` would be read
    # as a scalar and hand back None instead of [].
    from lembas.db.models.library import KnowledgeBase

ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"

# What a conversation is allowed to be. A plain chat can never act; an agent
# chat is pointed at a machine before it starts and stays pointed there.
KIND_CHAT = "chat"
KIND_AGENT = "agent"

# The two sides of the sidebar's Chat/Agent switch, and nothing else.
# `KINDS` must NOT grow: `api/preferences.py:set_sidebar_kind` validates against
# it, so a third entry would make the tree filterable to a side with no button
# to leave it -- the "one side of a fork nobody can move" failure the
# `sidebar_split` guard already exists to prevent.
KINDS = (KIND_CHAT, KIND_AGENT)

# Conversations that belong to a section of their own rather than to the tree.
# A task chat belongs to a schedule and is reached through Scheduled. It is never
# listed among the chats, so it is not a side of the switch.
KIND_TASK = "task"

# What a row's `kind` may actually be. Every listing that means "the sidebar
# tree" filters on KINDS; every check that means "is this a real value" uses
# this. Reading `kind == ""` as "no filter" is what leaks a task chat into the
# ordinary list on an instance with agents switched off, where the sidebar
# passes "" precisely because there is no switch to read.
ALL_KINDS = (*KINDS, KIND_TASK)

# Duplicated from services/agent/policy.py rather than imported: a model module
# importing a service would invert the dependency, and this is only the column
# default. policy.MODES is the vocabulary; this is what a row starts as.
MODE_MANUAL = "manual"


class Folder(UUIDPrimaryKey, Timestamps, Base):
    """A user-owned, arbitrarily nested container for chats."""

    __tablename__ = "folders"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("folders.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    collapsed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # What chats started in this folder inherit. A folder is where somebody
    # groups the work on one thing, so it is the natural place to say "chats
    # about this use this prompt, this model, this machine" -- said once rather
    # than on every new chat.
    description: Mapped[str] = mapped_column(String(500), default="")
    # Read at request time, never copied onto the chat: editing the folder later
    # has to reach the chats already in it, which is the whole point of putting
    # it here. It slots into the ladder between the chat and the model.
    system_prompt: Mapped[str] = mapped_column(Text, default="")

    # Seeds, copied onto a new chat and then that chat's own. Empty means "no
    # opinion", so a folder can carry a prompt without also dictating a model.
    model_id: Mapped[str] = mapped_column(String(300), default="")
    kind: Mapped[str] = mapped_column(String(16), default="")
    # Deliberately not a ForeignKey. `migrations.py` compiles the column type
    # only, so a REFERENCES clause would exist on a fresh database and not on an
    # upgraded one -- the same reason `Chat.compacted_through_id` is a plain id.
    # The profile may also have been deleted, so it is validated on read.
    ssh_profile_id: Mapped[str] = mapped_column(String(32), default="")
    project_dir: Mapped[str] = mapped_column(String(1000), default="")
    agent_mode: Mapped[str] = mapped_column(String(16), default="")

    children: Mapped[list[Folder]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="Folder.position, Folder.name",
    )
    parent: Mapped[Folder | None] = relationship(back_populates="children", remote_side="Folder.id")
    chats: Mapped[list[Chat]] = relationship(back_populates="folder")

    def visible_chats(self, kind: str = "") -> list[Chat]:
        """The chats in this folder that belong in the sidebar.

        The relationship itself stays unfiltered -- back-population needs every
        row -- so the listing rule lives here rather than in the template, where
        the loop and the "Empty" check would have to agree by hand and already
        did not: archived chats have been showing inside folders since folders
        existed. The unfiled list has always filtered them (api/pages.py); the
        folder branch went through the relationship and filtered nothing.

        `kind` narrows to one side of the sidebar's Chat/Agent switch. Empty
        means *both sides of the switch* -- which is not the same as "no filter",
        and the difference only became visible once a third kind existed. An
        instance with agents disabled passes "" because there is no switch to
        read, so a bare `not kind` would list every task chat among somebody's
        ordinary chats. Those have a section of their own and are never in the
        tree.

        Ordered like the unfiled list: pinned first, then most recently touched.
        """
        wanted = (kind,) if kind else KINDS
        kept = [
            chat
            for chat in self.chats
            if not chat.archived and not chat.temporary and chat.kind in wanted
        ]
        kept.sort(key=lambda chat: chat.updated_at, reverse=True)
        kept.sort(key=lambda chat: not chat.pinned)
        return kept

    def holds(self, kind: str = "") -> bool:
        """Whether anything of this kind is anywhere under this folder.

        Recursive, because a folder's only matching chat may be three levels
        down and judging on its own contents alone would bury it.
        """
        if self.visible_chats(kind):
            return True
        return any(child.holds(kind) for child in self.children)

    def shown_in(self, kind: str = "") -> bool:
        """Whether this folder belongs on one side of the sidebar's switch.

        Two different reasons a folder can have nothing in it, and only one of
        them is a reason to hide it. A folder full of ordinary chats is noise on
        the Agent side and is dropped. A folder that is empty of *everything* is
        a container somebody just made and has not filled yet -- hiding that one
        means it can never be found again, let alone filed into, so it shows on
        both sides and says "Empty" for itself.
        """
        return self.holds(kind) or not self.holds()

    def __repr__(self) -> str:
        return f"<Folder {self.name}>"


class Chat(UUIDPrimaryKey, Timestamps, InDataGroup, Base):
    __tablename__ = "chats"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Deleting a folder keeps its chats; they fall back to the unfiled list.
    folder_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("folders.id", ondelete="SET NULL"), index=True
    )

    title: Mapped[str] = mapped_column(String(300), default="New chat")
    # Set once the model writes the first reply, so auto-titling only runs once.
    title_generated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Denormalised rather than a foreign key: chat history must survive an admin
    # deleting a connection or a model disappearing upstream.
    model_id: Mapped[str] = mapped_column(String(300), default="")
    connection_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("connections.id", ondelete="SET NULL")
    )

    system_prompt: Mapped[str] = mapped_column(Text, default="")
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Never listed in the sidebar, and swept a day after the last thing said in
    # it. A real row rather than something held in the browser, so a reload or a
    # dropped connection does not lose the conversation -- and `Keep` clears the
    # flag, because a temporary chat that turns out to matter must have a way
    # out. See services/chat.py:sweep_temporary.
    temporary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # A reply landed while nobody was watching this chat. Cleared when the chat
    # is next opened. `unread_notified` stops the same arrival being announced
    # on every poll.
    unread: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    unread_notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- Agent chats ---------------------------------------------------------
    # Whether this conversation may act, and where. Chosen on the new-chat
    # screen and fixed once there is a message: the harness, the tools offered
    # and the approval loop all differ, so a chat that changed kind halfway
    # would have a transcript whose earlier turns were produced under other
    # rules. The connection is locked with it -- a shell history and a project
    # directory do not transplant to another machine.
    kind: Mapped[str] = mapped_column(String(16), default=KIND_CHAT, nullable=False)
    # Where an agent chat ran when that could be an SSH connection. Nothing
    # writes it any more -- an agent chat runs on a linked device, recorded
    # below -- and it stays only because the schema is additive: dropping a
    # column is a manual migration, and an old row's value is harmless.
    ssh_profile_id: Mapped[str | None] = mapped_column(String(32))
    # The linked device: the device token of a LLeMbas CLI that dials in
    # (services/device_link.py). A plain id rather than a ForeignKey, for the
    # reason `compacted_through_id` below gives: migrations.py compiles only the
    # column type, so a REFERENCES clause would exist on a fresh database and
    # not on an upgraded one. Validated on read instead.
    device_token_id: Mapped[str | None] = mapped_column(String(32))
    # The device's own id for this chat's session: the same session its
    # terminal lists and can open. Kept so that a restart of either side carries
    # the conversation on rather than starting the device afresh, and so that a
    # session a terminal shared finds the chat it already has.
    device_session_id: Mapped[str | None] = mapped_column(String(64))
    # The model the device's session runs on when it is not one of this
    # instance's: switched there to a model of its own, so the chat says
    # "on the device: <ref>" and sends no model with the next prompt. Empty
    # while the chat's own model is the one in use.
    device_model: Mapped[str] = mapped_column(String(300), default="")
    # The agent's working checklist as it last stood: the `todo` tool's
    # whole list, from this instance's agent or a linked device's. Shown in its own
    # panel above the composer while anything on it is open, as the LLeMbas CLI shows
    # it above its prompt -- not as a block in the transcript. On the chat, not a
    # message: it outlives the reply that wrote it. Nullable for the reason
    # `steps_json` is.
    todos_json: Mapped[list[Any] | None] = mapped_column(JSONList, nullable=True, default=list)
    # Where commands start on the far side, and what file paths resolve against.
    project_dir: Mapped[str] = mapped_column(String(500), default="")
    # Which of the four permission modes is in force. The one agent field that
    # IS switchable mid-chat: it decides what gets asked about, not what the
    # conversation is.
    agent_mode: Mapped[str] = mapped_column(String(16), default=MODE_MANUAL, nullable=False)
    # Set when a turn was edited or regenerated in an agent chat. The project
    # directory is deliberately NOT rewound with the transcript -- it is
    # somebody's real working tree and deleting their work would be far worse
    # than an inconsistency -- so the harness says so instead.
    rewound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Which message carries the plan currently in force. A plain id and not a
    # ForeignKey, for the reason `compacted_through_id` below gives; validated
    # on read. It exists so the harness can put the plan in front of the model
    # with one `db.get` by primary key rather than a scan for "the newest
    # message with a plan" -- `context_variables` is synchronous and on the
    # request path. A plan a model cannot see is a plan it cannot keep current.
    plan_message_id: Mapped[str | None] = mapped_column(String(32))
    # What this chat has switched off, narrowing what it is already allowed.
    # {"families": {"web_search": false}, "skills": {"weekly-report": false}}.
    # **Absent means on**, for every key -- the same convention
    # `McpServer.tool_overrides_json` uses, and for the same reason: two
    # representations of "on" makes "why is this off?" unanswerable.
    scope_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    # What this chat generates pictures with when the model names neither. A
    # preference rather than a constraint -- the model may still choose another
    # template or checkpoint for a particular image, and the harness lists what
    # is on offer -- so this is where "in this chat I am working in SDXL" is
    # said once instead of in every prompt.
    #
    # Plain columns rather than keys in `scope_json`: that one narrows what a
    # chat may *reach* and absent means on, which is the opposite of what an
    # empty default here means. A workflow that has since been deleted reads
    # back as no preference, so it is validated on use like `device_token_id`.
    image_workflow_id: Mapped[str | None] = mapped_column(String(32))
    image_checkpoint: Mapped[str] = mapped_column(String(300), default="")

    # --- Subagents -----------------------------------------------------------
    # The chat whose reply spawned this one, when a model delegated a piece of
    # work. A plain id and not a ForeignKey, for the reason the three above
    # give, and validated on read. Its presence is what makes a chat a
    # subagent's: `generation` bounds its rounds by the subagent settings,
    # `services/subagent.py` refuses to spawn from one, and the sweep finds it.
    parent_chat_id: Mapped[str | None] = mapped_column(String(32))
    # Nobody is at the keyboard for this conversation, and nothing in it may
    # stop to ask. Not the same question as `kind`: a scheduled task's chat is
    # unattended because of what started it, a subagent's because of what it is,
    # and a future third thing will be unattended for a third reason. Reading
    # the flag rather than the kind is what stops each of those needing its own
    # branch in `resolve_tools` and in `_authorise`.
    unattended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Which files are open in the canvas panel, and which of them is in front.
    # {"tabs": [{"key": "agent:/srv/app/main.py", "title": …, "source": …}],
    #  "active": "agent:/srv/app/main.py"}
    #
    # Server-side rather than in the browser because a model reading a file
    # opens a tab, and every frame this application streams is HTML swapped
    # whole -- if the browser owned the list, the server could not render the
    # strip and the frame would have to become data for JavaScript to interpret.
    # One chat, one canvas, the same consequence the terminal panel documents:
    # two tabs on the same chat share it.
    canvas_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    # --- Compaction ----------------------------------------------------------
    # A summary of the turns up to `compacted_through_id`, sent in their place.
    # The messages themselves are kept and still shown; they simply stop being
    # part of the request. See services/compaction.py.
    compact_summary: Mapped[str] = mapped_column(Text, default="")
    # A plain id, deliberately not a ForeignKey: db/migrations.py compiles only
    # the column type, so a REFERENCES clause would exist on a freshly created
    # database and not on an upgraded one, and a constraint half the fleet has
    # is worse than none. It is validated on every read instead -- the same
    # reasoning `model_id` above carries.
    compacted_through_id: Mapped[str | None] = mapped_column(String(32))
    compacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    folder: Mapped[Folder | None] = relationship(back_populates="chats")
    messages: Mapped[list[Message]] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )
    # Which knowledge bases this chat draws on. None means "everything its owner
    # can see"; naming some scopes the knowledge tool to those.
    knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(
        "KnowledgeBase", secondary="chat_knowledge_bases"
    )

    def __repr__(self) -> str:
        return f"<Chat {self.title!r}>"


class Message(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "messages"

    chat_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Reserved for conversation branching (edit a message, regenerate a reply
    # and keep both). Nothing reads it yet; it exists now because retrofitting a
    # column onto a live SQLite database without migrations is painful.
    parent_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("messages.id"))

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")

    # Reserved for multimodal turns: [{"type": "image_url", ...}, ...].
    # Plain-text messages leave this empty and use `content`.
    content_parts_json: Mapped[list[Any]] = mapped_column(JSONList, default=list)

    # A reasoning model's visible thinking, kept separate from the answer so it
    # can be collapsed, and so it is never fed back as context on the next turn
    # -- providers expect the answer alone, and replaying the thinking both
    # wastes the window and degrades the reply.
    reasoning: Mapped[str] = mapped_column(Text, default="")
    # Milliseconds spent producing the reasoning, for the "Thought for Xs" label.
    reasoning_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Which model wrote this, or is about to. Written on every assistant
    # placeholder at creation and **read back as the model that answers** --
    # `chat_service.speaker_for`. Not a display snapshot only, because the two
    # could then disagree: `wake_chat` accepts a model override that would reach
    # this column and never the request, so a schedule naming another model
    # would get the chat's model wearing this label.
    model_id: Mapped[str] = mapped_column(String(300), default="")

    # Which connection that model was reached through. Nullable and usually
    # empty, meaning "resolve it from the model id as this application always
    # has"; it matters only where the same id is offered by two connections,
    # since `Model` is unique on the pair and their capabilities, context lengths
    # and effort lists are separate rows.
    #
    # No foreign key, deliberately, and the same reasoning `Chat.model_id`
    # carries: a transcript has to survive an administrator deleting a
    # connection, and `migrations.py` compiles only the column type -- so a
    # REFERENCES clause would exist on a fresh database and not on an upgraded
    # one. Validated on read instead.
    connection_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # What the model did before answering: one entry per tool call, with its
    # arguments and results. Shown in the transcript so the sources behind an
    # answer stay visible, and deliberately NOT replayed as context on the next
    # turn -- see services/generation.py for why.
    tool_calls_json: Mapped[list[Any]] = mapped_column(JSONList, default=list)


    # Where each round's contribution ended, so `content`, `reasoning` and
    # `tool_calls_json` can be shown as the one sequence they actually were
    # rather than as three stacked zones. One entry per closed step, holding the
    # cumulative length of each of the three at that moment. See
    # services/steps.py; read it through the `steps` property below.
    #
    # Nullable, and that is load-bearing rather than lazy. `migrations.py`
    # derives a backfill for a NOT NULL column from `column.type.python_type`,
    # and `JSONList` is `MutableList.as_mutable(JSON)` whose `python_type` is
    # `dict` -- so a NOT NULL list column would be backfilled `'{}'` on every
    # existing row and fail on the first read. Nullable means no default, which
    # is what an older row should have anyway: no marks, and the old layout.
    steps_json: Mapped[list[Any] | None] = mapped_column(JSONList, nullable=True, default=list)

    usage_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    # A plan produced in Plan mode, or the state of one being carried out. See
    # services/plans.py for the shape. Marked on the row rather than parsed back
    # out of the prose, so the Execute button sends exactly what was proposed
    # and not an approximation of it. Read through the `plan` property below,
    # never directly: rows written before version 2 hold `{title, steps}`.
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSONDict, default=dict)

    # Non-empty when generation failed. Rendered as a styled error in the
    # thread so a failed turn is never an unexplained blank bubble.
    error: Mapped[str] = mapped_column(Text, default="")
    # False while a reply is still streaming; flipped when the stream ends.
    complete: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # True when the reader pressed Stop. Distinct from `error`: the text that
    # did arrive is kept and is perfectly usable, it is just cut short.
    stopped: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Typed while a reply was still being written, and not yet handed to a
    # model. A row rather than something held in the browser: it survives a
    # restart, it is in the transcript the moment it is typed, and it can be
    # withdrawn before it is ever sent. `build_messages` skips it; delivery --
    # `generation._drain` at the end of a reply, or `_inject` between two rounds
    # of tool calls -- is the only thing that clears it.
    queued: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Written by the application rather than by the person whose bubble this
    # would otherwise be. `wake.wake_chat` is the one writer: a scheduled run or
    # a helper's task arriving is a new turn in the *user* role, and that role is
    # load-bearing -- `_inject` sends a queued turn verbatim and `build_messages`
    # has to keep seeing a user turn -- but it is not the reader speaking, and
    # rendering it under their name with their initial beside it is the
    # application putting words in their mouth. Nothing about the request
    # changes; only the bubble does.
    machine: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Where a person's turn was typed, when not here: "terminal" for one
    # typed into the LLeMbas CLI on a linked device whose session this chat shows. The
    # reply to it is written by the device and only mirrored here -- nothing is
    # sent for it -- and the bubble says where it came from.
    origin: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    # Cut off by a shutdown rather than stopped by anybody: taken up
    # again at the next start -- see generation.resume_interrupted.
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # What a turn typed in a linked device's terminal had attached:
    # `[{name, mimeType, size}]`, shown as chips. The files are on the device and
    # nowhere here, so these are names, not `Attachment` rows. Nullable for the
    # reason `steps_json` is.
    terminal_attachments_json: Mapped[list[Any] | None] = mapped_column(
        JSONList, nullable=True, default=list
    )

    chat: Mapped[Chat] = relationship(back_populates="messages")
    attachments: Mapped[list[Attachment]] = relationship(  # noqa: F821
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="Attachment.created_at",
    )

    @property
    def images(self) -> list:
        return [a for a in self.attachments if a.is_image]

    @property
    def documents(self) -> list:
        return [a for a in self.attachments if not a.is_image]

    @property
    def plan(self) -> dict:
        """The plan, always in the current shape.

        A property for the reason `images` and `documents` are: a message bubble
        is rendered from four different handlers, and every one of them would
        otherwise have to remember to normalise. Rows written before version 2
        hold `{title, steps}` and come back through here as one phase.
        """
        from lembas.services import plans

        return plans.normalise(self.plan_json)

    def __repr__(self) -> str:
        return f"<Message {self.role} {self.content[:40]!r}>"
