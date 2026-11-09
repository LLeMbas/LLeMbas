"""Chat orchestration: building requests, streaming replies, naming chats."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    KIND_AGENT,
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_USER,
    Chat,
    Connection,
    Message,
    Model,
    User,
)
from lembas.services import files as files_service
from lembas.services.llm.openai_client import Endpoint, LLMError, complete
from lembas.web.i18n import t

log = logging.getLogger(__name__)

# Sampling keys forwarded upstream. Anything else a user puts in params_json is
# ignored rather than passed through, so a typo cannot produce a 400 from the
# provider that looks like a LLeMbas bug.
FORWARDED_PARAMS = frozenset(
    {"temperature", "top_p", "max_tokens", "presence_penalty", "frequency_penalty",
     "seed", "stop"}
)

MAX_TITLE_LENGTH = 60

# What one title call may spend. A title is a handful of words; the rest of this
# is headroom for a model that thinks before it answers, which is most of the
# interesting local ones. Too small is not a shorter title -- it is no title at
# all, because the thinking consumes the budget and the content field comes back
# empty or holding an unclosed `<think>`.
TITLE_MAX_TOKENS = 512

# How long a helper's kept chat survives after the last thing said in it. A
# person's own temporary chat is the administrator's `temporary_lifetime_minutes`
# instead (see `temporary_lifetime`); a helper's transcript is kept for working
# out why it answered oddly, which a ten-minute lifetime would defeat.
TEMPORARY_LIFETIME = timedelta(hours=24)

# The longest an administrator may set, and the floor the sweep keeps when the
# setting is 0 ("when you leave it"). The floor is the safety net for a leave the
# browser never reported -- a closed laptop, a killed tab -- and is long enough
# that a reload or a dropped connection never loses a conversation in use.
TEMPORARY_LIFETIME_MAX_MINUTES = 1440
LEAVE_FLOOR = timedelta(minutes=10)
# How long after a reported leave the chat is deleted, unless it is opened again
# first. A reload reports a leave too -- `pagehide` cannot tell the two apart --
# so the delete waits for the page that would have come back.
LEAVE_GRACE = timedelta(seconds=60)


@dataclass(frozen=True)
class Speaker:
    """Which model is answering one reply, and through which connection.

    The pair and not the id, because `Model` is unique on
    `(connection_id, model_id)`: the same name can live behind two endpoints and
    an id alone does not say which. `images/tool.py:_reviewer` already resolves a
    model this way.

    Frozen, and passed rather than re-derived, for the reason `Endpoint` is a
    snapshot: a generation outlives the request that started it, and "who is
    answering" must not be able to change underneath a reply that is already
    streaming.
    """

    model_id: str
    connection_id: str | None = None


def speaker_for(db: DBSession, chat: Chat, message: Message | None = None) -> Speaker:
    """Who is answering: the message being written into, or else the chat.

    **The row names the model and the chat is only the default.** Answering
    with `chat.model_id` alone, while `Message.model_id` is written on every
    placeholder, would let the bubble's avatar and the request disagree:
    `wake_chat` accepts a `model_id` override and `schedule/runner` passes
    `schedule.model_id or chat.model_id`, which reaches the row. A schedule
    naming another model would get the chat's model wearing the other one's
    name.

    Reading it off the row is also what makes a reply survive a restart, because
    `_follow` calls `ensure`, which starts a *new* generation against the same
    row -- so anything the request depends on has to be durable, and the registry
    is not. This is the rule the reload story was won with: the row is the
    authority.
    """
    if message is not None and (message.model_id or "").strip():
        return Speaker(message.model_id, getattr(message, "connection_id", None) or None)
    return Speaker(chat.model_id, chat.connection_id)


def resolve_endpoint(
    db: DBSession, chat: Chat, speaker: Speaker | None = None
) -> tuple[Endpoint, str]:
    """Find the connection and model a reply should use.

    Chats store the model id as text rather than a foreign key so history
    survives an admin deleting a connection, which means the mapping back to a
    live connection has to be resolved at send time and can legitimately fail.

    `speaker` defaults to the chat's own model, so every existing caller behaves
    exactly as it did.
    """
    speaker = speaker or speaker_for(db, chat)
    if not speaker.model_id:
        raise LLMError(t("This chat has no model selected."))
    # Whether resolving a fallback may be *written back* to the chat. It may only
    # when the speaker is the chat's own model: a schedule's model finding its
    # way to another connection must not repoint the chat.
    speaks_for_chat = speaker.model_id == chat.model_id

    connection: Connection | None = None
    if speaker.connection_id:
        connection = db.get(Connection, speaker.connection_id)

    if connection is None or not connection.enabled:
        # The original connection is gone or disabled. Any enabled connection
        # still offering this model id will do -- **in the chat's own data
        # group**, for the chat's own model. Any connection at all would repoint
        # the conversation onto whichever provider happened to serve the same
        # id, and hand it the whole history on the way.
        from lembas.services import data_groups

        candidates = list(
            db.scalars(
                select(Model)
                .join(Connection)
                .where(
                    Model.model_id == speaker.model_id,
                    Model.enabled.is_(True),
                    Connection.enabled.is_(True),
                )
                .order_by(Connection.position)
            )
        )
        if speaks_for_chat and candidates:
            owner = db.get(User, chat.user_id) if chat.user_id else None
            groups = data_groups.connection_groups(db, owner)
            wanted = data_groups.for_chat(db, chat)
            candidates = [
                m
                for m in candidates
                if groups.get(m.connection_id, data_groups.DEFAULT_GROUP) == wanted
            ]
        model = candidates[0] if candidates else None
        if model is None:
            raise LLMError(
                t(
                    "No enabled connection currently offers the model "
                    "'%(model)s'. Pick another model for this chat.",
                    model=speaker.model_id,
                )
            )
        connection = model.connection
        if speaks_for_chat:
            chat.connection_id = connection.id
            db.commit()

    return Endpoint.from_connection(connection), speaker.model_id


def document_context(message: Message) -> str:
    """Extracted text from a message's non-image attachments.

    Wrapped in named tags so the model can tell one document from another, and
    tell all of them from what the user actually typed. Truncation is stated
    inline rather than silently, so a model asked about page 400 of a 300-page
    extract can say it did not see it.
    """
    blocks: list[str] = []
    for attachment in message.documents:
        if not attachment.extracted_text.strip():
            continue
        note = " (truncated)" if attachment.truncated else ""
        # Where it came from, when there is a where. A model handed `main.py`
        # cannot tell which of four it is looking at, and cannot name the file
        # back when asked to change something -- so a file read off a machine
        # says which machine and which path. Quotes are stripped rather than
        # escaped: these are attribute values in a tag the model reads, and a
        # path containing one would otherwise close it early.
        where = ""
        if attachment.source_path:
            where += f' path="{_attr(attachment.source_path)}"'
        if attachment.source_label:
            where += f' from="{_attr(attachment.source_label)}"'
        blocks.append(
            f'<document name="{_attr(attachment.filename)}"{where}{note}>\n'
            f"{attachment.extracted_text.strip()}\n"
            f"</document>"
        )
    return "\n\n".join(blocks)


def _attr(value: str) -> str:
    """A value safe to sit inside the double quotes of a tag we are writing."""
    return value.replace('"', "").replace("<", "").replace(">", "").replace("\n", " ")


def message_payload(message: Message, *, vision: bool) -> dict[str, Any]:
    """One history entry in the shape the endpoint expects.

    Plain text stays a plain string: sending the multimodal list form to an
    endpoint that does not implement it is a reliable way to get a 400, and
    most local runners do not.
    """
    text = message.content.strip()

    documents = document_context(message)
    if documents:
        # Documents lead so the question that follows has its material already
        # in view, which is how these models are trained to read a prompt.
        text = f"{documents}\n\n{text}" if text else documents

    # Images ride on a *user* turn and nowhere else. Until image generation
    # existed no assistant message had ever carried one, so this was never a
    # distinction worth drawing -- and the moment one does, the multimodal list
    # form on an `assistant` turn is rejected outright by OpenAI and by most
    # local runners, which would break not that turn but every later one in the
    # chat. What follows from it, and is worth knowing rather than discovering:
    # a model cannot see the picture it made on a *subsequent* turn (tool
    # results are not replayed either), so "make it bluer" regenerates rather
    # than edits. Honest for a text-to-image workflow with no img2img path.
    images = message.images if (vision and message.role == ROLE_USER) else []
    if not images:
        return {"role": message.role, "content": text}

    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for attachment in images:
        uri = files_service.data_uri(attachment)
        if uri is None:
            # The row survived but the file did not. Better to say so than to
            # send a turn that silently lost its picture.
            log.warning("attachment %s has no file on disk", attachment.id)
            continue
        parts.append({"type": "image_url", "image_url": {"url": uri}})

    if not parts:
        return {"role": message.role, "content": text}
    return {"role": message.role, "content": parts}


def folder_system_prompt(db: DBSession, chat: Chat) -> str:
    """The nearest prompt on the chat's folder, or on a folder above it.

    Walks up rather than reading one level, because folders nest and a project's
    prompt belongs on the project rather than on each sub-folder of it. The
    nearest one wins, which is the same rule the ladder as a whole follows.

    Bounded and cycle-safe the way `api/folders.py:_depth_of` is. Reparenting
    already refuses to build a cycle, but this runs on the request path for
    every reply and a row written by something else must not be able to hang it.
    """
    from lembas.db.models import Folder

    folder = chat.folder
    seen: set[str] = set()
    while folder is not None and folder.id not in seen:
        seen.add(folder.id)
        if (folder.system_prompt or "").strip():
            return folder.system_prompt.strip()
        folder = db.get(Folder, folder.parent_id) if folder.parent_id else None
    return ""


def effective_system_prompt(
    db: DBSession, chat: Chat, speaker: Speaker | None = None
) -> str:
    """The system prompt a chat actually runs with.

    Four layers, most specific wins outright:

        chat  >  folder  >  model  >  instance

    Precedence rather than concatenation. Stacking them reads well in a
    settings screen and badly in practice: the moment two layers disagree the
    model gets contradictory instructions and nobody can tell which one is
    losing. With precedence, "why is it behaving like this" has one answer.

    The folder sits above the model because it is the more specific statement:
    a model's prompt describes the model wherever it is used, and a folder's
    describes this piece of work whichever model is pointed at it.
    """
    from lembas.services import settings_store

    if chat.system_prompt.strip():
        return chat.system_prompt.strip()

    if inherited := folder_system_prompt(db, chat):
        return inherited

    # The *answering* model's layer, which is not always the chat's: a
    # member speaking in somebody else's chat brings its own prompt with it.
    model = model_row(db, speaker or Speaker(chat.model_id, chat.connection_id))
    if model is not None and (model.system_prompt or "").strip():
        return model.system_prompt.strip()

    return (settings_store.get(db, "system_prompt") or "").strip()


def window(
    db: DBSession,
    chat: Chat,
    *,
    upto: Message | None = None,
    through: Message | None = None,
) -> tuple[list[Message], list[Message]]:
    """(the history in front of the model, the rows of it sent as turns).

    The history runs from the compaction boundary to the end of the request:
    up to `upto`, which is left out with everything after it (the placeholder
    being written into), or up to and including `through` (the compaction
    request, which ends on the last finished reply). The sent rows are those of
    it that become turns, one turn each -- the rows `build_messages` sends and
    `services/prefix.py` attaches notes to, by one definition.
    """
    from lembas.services import compaction as compaction_service

    cutoff = compaction_service.cutoff_message(db, chat)
    history = db.scalars(
        select(Message).where(Message.chat_id == chat.id).order_by(Message.created_at)
    ).all()
    shown: list[Message] = []
    sent: list[Message] = []
    for message in history:
        if upto is not None and message.id == upto.id:
            break
        if cutoff is not None and compaction_service.moment(
            message
        ) <= compaction_service.moment(cutoff):
            continue
        shown.append(message)
        last = through is not None and message.id == through.id
        # Typed while the previous reply was still being written, and not yet
        # handed to a model. It is in the transcript and it is not in the
        # request; delivery is what moves it from one to the other.
        # Turns that failed or produced nothing are skipped too -- but a
        # message carrying only an attachment has no text and must still be
        # sent.
        if not (
            message.queued
            or message.error
            or (not message.content.strip() and not message.attachments)
        ):
            sent.append(message)
        if last:
            break
    return shown, sent


def build_messages(
    db: DBSession,
    chat: Chat,
    *,
    upto: Message | None = None,
    through: Message | None = None,
    vision: bool = False,
    system_prompt: str | None = None,
    speaker: Speaker | None = None,
    notes: dict[str, str] | None = None,
) -> list[dict]:
    """Assemble the message list to send upstream.

    `upto` excludes the placeholder assistant row being generated into, and
    everything after it; `through` ends the list on that row instead (see
    `window`). `system_prompt` overrides what would otherwise be resolved,
    which is how the harness gets in front of the authored prompt without this
    function knowing anything about tools. `notes` are the session notes by
    message id (`services/prefix.py`), each after that turn's words.
    """
    from lembas.services import compaction as compaction_service
    from lembas.services import prompts as prompts_service

    payload: list[dict[str, Any]] = []
    system = effective_system_prompt(db, chat) if system_prompt is None else system_prompt
    if system:
        payload.append({"role": ROLE_SYSTEM, "content": system})

    # Compacted turns are replaced by a summary carried in two turns rather than
    # one. A leading `assistant` breaks templates that require the first
    # non-system message to be `user`; a lone leading `user` produces user, user
    # whenever the kept history starts on a user turn -- which it always does,
    # because the cutoff lands on a finished reply. The pair alternates
    # correctly in both directions and keeps exactly one system message.
    cutoff = compaction_service.cutoff_message(db, chat)
    if cutoff is not None:
        lead = prompts_service.resolve(db, "task.compact_lead").strip()
        ack = prompts_service.resolve(db, "task.compact_ack").strip()
        summary = chat.compact_summary.strip()
        payload.append(
            {"role": ROLE_USER, "content": f"{lead}\n\n{summary}" if lead else summary}
        )
        if ack:
            payload.append({"role": ROLE_ASSISTANT, "content": ack})

    _shown, sent = window(db, chat, upto=upto, through=through)
    # The current turn's replies carry their reasoning back (harness spec 3.2.0,
    # context.md §8): the rows after the last user turn -- a reply continued, or
    # resumed after an answer it waited for. A compaction request asks for a
    # summary in a turn of its own, so it carries none.
    current = turn_start(m.role for m in sent) if through is None else len(sent)
    for index, message in enumerate(sent):
        entry = message_payload(message, vision=vision)
        if notes and (note := notes.get(message.id)) and message.role == ROLE_USER:
            entry = with_note(entry, note)
        if index > current and message.role == ROLE_ASSISTANT and message.reasoning:
            entry["reasoning_content"] = message.reasoning
        payload.append(entry)

    if speaker is not None:
        payload = _as_one_speaker_sees_it(db, payload, sent, speaker)

    return payload


def turn_start(roles, turns=None) -> int:
    """The index of the message that started the current turn: the last user
    message that starts one (`turns`, per message, False for a delivery at a
    step boundary -- a job's note, words added to a running turn; absent, every
    user message does). -1 when there is none."""
    roles = list(roles)
    flags = list(turns) if turns is not None else [True] * len(roles)
    for index in range(len(roles) - 1, -1, -1):
        if roles[index] == ROLE_USER and flags[index] is not False:
            return index
    return -1


def reasoning_back(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The OpenAI chat messages for a history whose assistant messages have
    stored `reasoning` (harness spec 3.2.0, context.md §8; pure, conformance
    `reasoning.json`): every assistant message of the current turn carries its
    reasoning as `reasoning_content`, earlier ones none, and none an empty one."""
    start = turn_start((m.get("role") for m in messages), (m.get("turn") for m in messages))
    out: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        entry = {"role": message.get("role"), "content": message.get("content")}
        if index > start and message.get("role") == ROLE_ASSISTANT and message.get("reasoning"):
            entry["reasoning_content"] = message["reasoning"]
        out.append(entry)
    return out


def with_note(entry: dict[str, Any], note: str) -> dict[str, Any]:
    """A user turn with its session note after the person's words: appended to
    a plain string, or as a last text part of a turn with images."""
    content = entry.get("content")
    if isinstance(content, list):
        return {**entry, "content": [*content, {"type": "text", "text": note}]}
    text = str(content or "")
    return {**entry, "content": f"{text}\n\n{note}" if text else note}


def _as_one_speaker_sees_it(
    db: DBSession,
    payload: list[dict[str, Any]],
    history: list[Message],
    speaker: Speaker,
) -> list[dict[str, Any]]:
    """Rewrite a transcript from one model's point of view: a chat whose model
    was changed holds turns another model wrote.

    Two problems, one pass.

    **Another speaker's reply must not arrive as this one's own prior turn.** Sent
    verbatim, every assistant message in the payload reads as something *this*
    model said -- so it defends sentences it never wrote, and cannot disagree with
    them, which is the whole point of the backward pass. Each other speaker's turn
    is therefore relabelled as user content behind a fragment-driven "«Label»
    said:".

    **Consecutive assistant turns break strict-alternation chat templates**, which
    this project already knows: `task.compact_ack` exists so a compacted history
    still alternates, and several templates reject one that does not. Relabelling
    fixes that by construction, and the adjacent user turns it creates are merged.

    ⚠ The relabelled entry is built here rather than by calling `message_payload`
    with a swapped role. That function attaches image parts when the role is
    `user` and the model has vision, so a swapped assistant turn carrying a
    generated image would silently become a multimodal list -- and an endpoint
    that rejects one rejects every later turn with it.
    """
    from lembas.services import prompts as prompts_service

    # Nothing to do for the ordinary case: one model, and every assistant turn in
    # the payload is its own.
    others = {
        message.model_id
        for message in history
        if message.role == ROLE_ASSISTANT
        and (message.model_id or "")
        and message.model_id != speaker.model_id
    }
    if not others:
        return payload

    labels = {
        model_id: (row.label if (row := model_row(db, Speaker(model_id))) else model_id)
        for model_id in others
    }
    template = prompts_service.resolve(db, "chat.other_model_said") or "{{other_model}} answered:"

    # The payload and the history line up only over the message rows: the system
    # turn and a compaction pair come first and belong to nobody. Walking from the
    # end is what pairs them without counting -- `history` is the rows that were
    # sent, one turn each (`window`), so the two end together.
    rows = list(history)
    rewritten: list[dict[str, Any]] = []
    for index, entry in enumerate(payload):
        row = None
        offset = index - (len(payload) - len(rows))
        if 0 <= offset < len(rows):
            row = rows[offset]
        if (
            row is not None
            and entry.get("role") == ROLE_ASSISTANT
            and (row.model_id or "") in others
        ):
            lead = template.replace("{{other_model}}", labels[row.model_id])
            body = entry.get("content")
            rewritten.append(
                {"role": ROLE_USER, "content": f"{lead}\n\n{body if isinstance(body, str) else ''}"}
            )
            continue
        rewritten.append(entry)

    return _merge_user_turns(rewritten)


def _merge_user_turns(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold adjacent user turns into one, so the history still alternates.

    Only where both are plain strings: a turn carrying content parts is a
    multimodal message and joining one to a string would destroy it.
    """
    merged: list[dict[str, Any]] = []
    for entry in payload:
        last = merged[-1] if merged else None
        if (
            last is not None
            and last.get("role") == ROLE_USER
            and entry.get("role") == ROLE_USER
            and isinstance(last.get("content"), str)
            and isinstance(entry.get("content"), str)
        ):
            merged[-1] = {
                **last,
                "content": f"{last['content']}\n\n{entry['content']}",
            }
            continue
        merged.append(entry)
    return merged


def model_row(db: DBSession, speaker: Speaker) -> Model | None:
    """The Model row a speaker names, or None if it has gone.

    Looked up by id rather than held as a foreign key, for the same reason
    resolve_endpoint does: chats store the model as text so history survives an
    administrator deleting a connection. The connection narrows it when one is
    named, because two connections may offer the same id and their capabilities,
    context length and effort lists are separate rows.
    """
    if not speaker.model_id:
        return None
    if speaker.connection_id:
        exact = db.scalar(
            select(Model).where(
                Model.model_id == speaker.model_id,
                Model.connection_id == speaker.connection_id,
            )
        )
        if exact is not None:
            return exact
    return db.scalar(
        select(Model).where(Model.model_id == speaker.model_id).order_by(Model.position)
    )


def model_for(db: DBSession, chat: Chat) -> Model | None:
    """The Model row a chat is using. The display answer; see `model_row`."""
    return model_row(db, Speaker(chat.model_id, chat.connection_id))


def model_supports(
    db: DBSession, chat: Chat, capability: str, speaker: Speaker | None = None
) -> bool:
    """Whether the answering model is marked as having a capability.

    ⚠ Worth getting right per speaker rather than per chat: `vision` decides
    whether image parts go into the body, and an endpoint sent an image by a
    model that cannot take one rejects **the whole request**, not the image.
    """
    model = model_row(db, speaker) if speaker is not None else model_for(db, chat)
    return bool(model and (model.capabilities_json or {}).get(capability))


def build_request(
    db: DBSession,
    chat: Chat,
    *,
    upto: Message | None = None,
    tools: list[dict[str, Any]] | None = None,
    user=None,
    force_tool: str = "",
    speaker: Speaker | None = None,
    persist: bool = False,
    through: Message | None = None,
    new_note: bool = True,
) -> dict[str, Any]:
    """The whole request body, tools and harness included.

    Composed here rather than in the generation loop so that "what gets sent"
    has one answer, and so the harness cannot be forgotten by a future caller
    that offers tools.

    `speaker` is who is answering; it defaults to the chat's own model, so a
    caller that does not care behaves exactly as it did. Everything that differs
    per model is resolved from it and not from the chat: the model name sent, the
    vision decision, the authored prompt's model layer, `{{model_name}}`, the
    personality, and the reasoning-effort vocabulary.

    The system prompt is the session's frozen one and each user turn carries
    its session note (harness spec 3.1.0, `services/prefix.py`). `persist` is
    what a reply passes: a prompt made here, and the note made for the newest
    turn, are stored and sent identically from then on. Without it nothing is
    written (the inspector shows what would go). `through` and `new_note` are
    the compaction request's: the conversation up to its last finished reply,
    with no new note.
    """
    from lembas.services import prefix

    params = {
        key: value
        for key, value in (chat.params_json or {}).items()
        if key in FORWARDED_PARAMS and value not in (None, "")
    }
    speaker = speaker or speaker_for(db, chat, upto)
    # Images are only sent to a model an administrator has marked as having
    # vision. Sending them to one that has not is not a graceful degradation:
    # most endpoints reject the whole request.
    vision = model_supports(db, chat, "vision", speaker=speaker)

    if user is None:
        from lembas.db.models import User

        user = db.get(User, chat.user_id)

    # The harness describes the tools; the authored prompt describes the
    # behaviour (see services/harness.py for why these are joined rather than
    # being two competing layers), and the person's own custom instructions and
    # personality go last. Made once per session (`prefix.compose`).
    shown, sent = window(db, chat, upto=upto, through=through)
    prepared = prefix.prepare(
        db,
        chat,
        user,
        speaker,
        tools,
        window=shown,
        sent=sent,
        persist=persist,
        new_note=new_note,
    )

    body: dict[str, Any] = {
        "model": speaker.model_id,
        "messages": build_messages(
            db,
            chat,
            upto=upto,
            through=through,
            vision=vision,
            system_prompt=prepared.system,
            speaker=speaker,
            notes=prepared.notes,
        ),
        **params,
    }

    if tools:
        body["tools"] = tools
        # Making the model call one particular tool, for `/image` -- the whole
        # of what that command is. Only ever sent alongside a tools array and
        # only when something asked for it, so a provider strict about unknown
        # parameters sees exactly the request it always did until somebody types
        # a slash command.
        #
        # An endpoint that ignores `tool_choice` is not a failure here: the turn
        # still carries the instruction in words, so the model is being steered
        # twice and the weaker half is the one that can be dropped.
        if force_tool and any(
            (tool.get("function") or {}).get("name") == force_tool for tool in tools
        ):
            body["tool_choice"] = {"type": "function", "function": {"name": force_tool}}

    # The *answering* model's own vocabulary, looked up here rather than passed
    # in: every caller of `build_request` would otherwise have to remember, which
    # is the trap `audio_service.template_flags` fell into.
    #
    # ⚠ Per speaker and not per chat, and this one is not cosmetic: the
    # vocabularies genuinely differ -- gpt-oss takes low/medium/high, a Bonsai
    # takes low/medium/xhigh and *raises inside its chat template* on high -- so
    # a chat's effort handed to another model fails the whole reply rather than
    # being ignored. `_learn_refused_effort` then narrows every Model row sharing
    # that id, so getting this wrong would also corrupt other models' lists as a
    # side effect.
    speaking_model = model_row(db, speaker)
    apply_level(body, speaking_model, resolved_effort(chat))
    return body


# Reasoning effort, and why it goes out twice.
#
# There is no one field that works. OpenAI and vLLM read a plain
# `reasoning_effort`. llama.cpp reads it too and, per its own documentation,
# "other values (e.g. 'low', 'max') have no effect" -- its maintainer is blunter
# still: "llama-server cannot support reasoning_effort at all", and the field
# "simply gets dropped without error or logging". What *does* reach a gpt-oss
# behind llama.cpp is `chat_template_kwargs`, which it accepts per request.
#
# So both are sent, and only when an effort has actually been chosen. That
# second half is what keeps this from being a regression: a chat nobody has set
# an effort on sends neither field and is byte-for-byte what it was. An endpoint
# strict about unknown parameters will refuse the extra one -- but on a chat
# somebody deliberately set an effort on, not on every chat in the instance.
# Every reasoning effort this application understands, and the subset a model
# gets when nobody has said otherwise.
#
# 🚨 These are two different questions and conflating them is what broke a
# chat on Bonsai: `EFFORTS` was `("low", "medium", "high")` and was used both to
# validate what somebody chose *and* to decide what to offer, so a model whose
# vocabulary is low/medium/**xhigh** could not be given its own top setting,
# and the one it was given -- `high` -- made its chat template call
# `raise_exception` and took the whole reply with it.
#
# The known list is the union across providers, which have not agreed: OpenAI
# has added `minimal`, `xhigh` and `max` at different points; gpt-oss takes
# low/medium/high; Bonsai takes low/medium/xhigh and refuses high. `none` is
# deliberately absent -- this application already spells that `off`, and two
# spellings of off is the failure this codebase keeps cataloguing.
EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "max")

# What a model is offered when its own list is empty. The three every reasoning
# model since the first one has understood.
DEFAULT_EFFORTS = ("low", "medium", "high")


def efforts_for(model) -> tuple[str, ...]:
    """The efforts this model accepts, in the order they should be offered.

    A model's own list when an administrator has set one or the endpoint has
    taught us one (see `generation._narrow_efforts`), and the common three
    otherwise. Filtered against `EFFORTS` on the way out, so a value stored by
    an older release -- or learned from an endpoint that advertised something
    this application has never heard of -- cannot reach a request body.
    """
    stored = list(getattr(model, "reasoning_efforts", None) or [])
    chosen = [value for value in stored if value in EFFORTS]
    if not chosen:
        return DEFAULT_EFFORTS
    return tuple(value for value in EFFORTS if value in chosen)


def resolved_effort(chat) -> str:
    """The level this chat has stored, or "" for none.

    Its own value, and nothing else: which of the *model's* levels it means is
    `level_of`'s answer (a stored level the model does not offer reads as the
    model's start level, harness spec 3.2.0). The model's default is a **seed**
    applied when the chat is created (`api/chats.py:_new_chat`) and on a model
    change, so a chat's request is a function of the chat row and its model.
    A helper copies the stored word onto its own row (`subagent.py`).
    """
    value = (getattr(chat, "params_json", None) or {}).get("reasoning_effort")
    return value if value in LEVEL_WORDS else ""


# --- Levels (harness spec 3.2.0, `models/schema.json` `levels`) ------------------
#
# What a chat offers for a model: `off` when the model's server turns thinking
# off for a request that asks (`thinking_switch`), then the model's efforts --
# or, when it takes none, the one level `default`, the model's own. "No effort"
# is not "no thinking": a Qwen3.x or Nemotron 3.x behind llama.cpp thinks at the
# effort its server was started with (quirks.json `thinking-default`), so the
# only honest `off` is the one that sends `enable_thinking: false`.
LEVEL_OFF = "off"
LEVEL_DEFAULT = "default"
LEVEL_WORDS = (*EFFORTS, LEVEL_OFF, LEVEL_DEFAULT)
# How a set of levels is shown: one level -- nothing; two, one of them off -- a
# Thinking on/off switch; anything else -- the slider.
CONTROL_NONE = "none"
CONTROL_SWITCH = "switch"
CONTROL_SLIDER = "slider"
# Where an effort goes in an OpenAI chat request (`effort_style`).
STYLE_TOP = "top"
STYLE_KWARGS = "kwargs"
STYLE_BOTH = "both"


def levels(thinking_switch: bool, efforts) -> list[str]:
    """The levels, in offering order, from the metadata alone (pure)."""
    taken = list(dict.fromkeys(e for e in (efforts or ()) if e in EFFORTS))
    return ([LEVEL_OFF] if thinking_switch else []) + (taken or [LEVEL_DEFAULT])


def start_level(offered: list[str], efforts, effort: str | None) -> str:
    """The level a new conversation starts with: the model's own `effort` when
    it offers it, else medium where taken, else the first effort, else default."""
    taken = [e for e in (efforts or ()) if e in EFFORTS]
    if effort and effort in offered:
        return effort
    if "medium" in taken:
        return "medium"
    return taken[0] if taken else LEVEL_DEFAULT


def control(offered: list[str]) -> str:
    if len(offered) <= 1:
        return CONTROL_NONE
    if len(offered) == 2 and LEVEL_OFF in offered:
        return CONTROL_SWITCH
    return CONTROL_SLIDER


def level_fields(
    level: str | None,
    *,
    thinking_switch: bool = False,
    efforts=(),
    effort: str | None = None,
    effort_style: str = STYLE_BOTH,
) -> dict[str, Any]:
    """What an OpenAI chat request carries for a level (pure; conformance
    `levels.json` `op: request`). A level the model does not offer is read as
    its start level."""
    offered = levels(thinking_switch, efforts)
    chosen = level if level in offered else start_level(offered, efforts, effort)
    if chosen == LEVEL_OFF:
        # Only ever in chat_template_kwargs: the template is what reads it.
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if chosen == LEVEL_DEFAULT:
        return {}
    out: dict[str, Any] = {}
    if effort_style != STYLE_KWARGS:
        out["reasoning_effort"] = chosen
    if effort_style != STYLE_TOP:
        out["chat_template_kwargs"] = {"reasoning_effort": chosen}
    return out


def model_efforts(model) -> tuple[str, ...]:
    """The efforts a model row takes: its own list (or the common three) on a
    model an administrator marked as reasoning, none on any other."""
    if model is None:
        return DEFAULT_EFFORTS
    if not (getattr(model, "capabilities_json", None) or {}).get("reasoning"):
        return ()
    return efforts_for(model)


def model_meta(model) -> dict[str, Any]:
    """The spec's level metadata of a model row: `thinking_switch`, `efforts`
    and `effort` (the administrator's start level, `params_json`)."""
    effort = ((getattr(model, "params_json", None) or {}) if model is not None else {}).get(
        "reasoning_effort"
    )
    return {
        "thinking_switch": bool(getattr(model, "thinking_switch", False)),
        "efforts": list(model_efforts(model)),
        "effort": effort if effort in LEVEL_WORDS else None,
    }


@dataclass(frozen=True)
class Levels:
    """A model's levels as a chat shows them: what is offered, where a chat
    starts, and which control (`none`, `switch`, `slider`)."""

    offered: tuple[str, ...]
    start: str
    control: str

    @property
    def on(self) -> str:
        """The switch's "on" level: the one that is not off."""
        return next((value for value in self.offered if value != LEVEL_OFF), LEVEL_DEFAULT)

    def read(self, stored: str | None) -> str:
        """A stored level as this model reads it: itself when offered, else the
        start level -- an `off` from before 3.2.0, a level another model took."""
        return stored if stored in self.offered else self.start


def levels_for(model) -> Levels:
    meta = model_meta(model)
    offered = levels(meta["thinking_switch"], meta["efforts"])
    return Levels(
        offered=tuple(offered),
        start=start_level(offered, meta["efforts"], meta["effort"]),
        control=control(offered),
    )


def chat_level(db: DBSession, chat: Chat, model: Model | None = None) -> str:
    """The level in force on a chat: what the picker shows and what
    `build_request` sends."""
    model = model if model is not None else model_for(db, chat)
    return levels_for(model).read(resolved_effort(chat))


def efforts_from_chat_template(template: str) -> list[str]:
    """Which efforts a model's Jinja chat template will actually accept.

    The template is where the truth lives: the one on a Bonsai reads roughly

        {%- if reasoning_effort not in ('xhigh', 'medium', 'low') %}
          {{- raise_exception('Unexpected reasoning effort ' ~ reasoning_effort ...

    so the accepted set is written out beside the thing that rejects everything
    else. `llama-server` hands the whole template over on `/props`, which makes
    this readable rather than guessable.

    Deliberately conservative, because a wrong answer here silently removes a
    level somebody is entitled to:

    - only quoted literals within a short window of a `reasoning_effort`
      mention are considered, so an unrelated list elsewhere in a four-hundred
      line template cannot contribute;
    - the result is intersected with `EFFORTS`, so an unknown token is dropped
      rather than stored;
    - fewer than two survivors is treated as "the template did not say". One
      match is far more likely to be a default assignment
      (`{%- set reasoning_effort = 'medium' %}`) than a vocabulary.

    Returns [] when nothing can be read, which every caller treats as "ask
    somebody" rather than as "this model accepts nothing".
    """
    if not template or "reasoning_effort" not in template:
        return []

    found: set[str] = set()

    # Shape one: the values sit in the statement that tests them.
    #     {%- if reasoning_effort not in ('xhigh', 'medium', 'low') %}
    for match in re.finditer(r"reasoning_effort", template):
        window = template[match.start() : match.start() + 400]
        # Stop at the end of the statement that mentions it, so a later,
        # unrelated block cannot leak in.
        window = window.split("%}")[0] if "%}" in window else window
        for literal in re.findall(r"""['"]([a-z]{3,8})['"]""", window):
            if literal in EFFORTS:
                found.add(literal)

    # Shape two: the values are a named list somewhere else, and the test says
    #     {%- if reasoning_effort not in valid_efforts %}
    # so nothing near the mention names them. Any group of quoted literals in
    # which *every* token is a known effort and there are at least two is taken
    # -- that is a strong enough signal on its own, and a list of nothing but
    # effort names that is not the effort vocabulary would be a strange thing
    # for a chat template to contain.
    for group in re.findall(r"[\[(]((?:\s*['\"][a-z]{3,8}['\"]\s*,?)+)[\])]", template):
        literals = re.findall(r"""['"]([a-z]{3,8})['"]""", group)
        if len(literals) >= 2 and all(value in EFFORTS for value in literals):
            found.update(literals)

    if len(found) < 2:
        return []
    return [effort for effort in EFFORTS if effort in found]


def apply_effort(
    body: dict[str, Any], effort: str | None, supported: tuple[str, ...] | None = None
) -> None:
    """Put a chosen reasoning effort into a request body, in both forms.

    `supported` is the model's own vocabulary. An effort outside it is dropped
    rather than sent, because the second form below is not advisory: it reaches
    the model's Jinja chat template, and a template that does not know the value
    raises rather than ignoring it -- which fails the whole request, not the
    parameter. (A chat's request goes through `apply_level`, which knows the
    model's levels; this is for a caller with an effort and no model row.)
    """
    allowed = supported or DEFAULT_EFFORTS
    if not effort or effort not in allowed:
        return
    body["reasoning_effort"] = effort
    kwargs = dict(body.get("chat_template_kwargs") or {})
    kwargs["reasoning_effort"] = effort
    body["chat_template_kwargs"] = kwargs


def apply_level(body: dict[str, Any], model, stored: str | None) -> None:
    """Put a chat's level into a request body (harness spec 3.2.0): `off` is
    `chat_template_kwargs.enable_thinking: false` and no effort, `default` is
    nothing, an effort goes in both of its forms (`providers.shape_request`
    moves it to where the provider reads it). A stored level the model does not
    offer is read as its start level.

    Without a model row (a model removed since) only a stored effort of the
    common three is sent, as before: there is nothing to say what it takes."""
    if model is None:
        apply_effort(body, stored if stored in EFFORTS else None)
        return
    fields = level_fields(stored, **model_meta(model))
    if "reasoning_effort" in fields:
        body["reasoning_effort"] = fields["reasoning_effort"]
    if fields.get("chat_template_kwargs"):
        body["chat_template_kwargs"] = {
            **(body.get("chat_template_kwargs") or {}),
            **fields["chat_template_kwargs"],
        }


def default_model(db: DBSession, user=None) -> tuple[str, str] | None:
    """The model a new chat should start with, as (model_id, connection_id).

    Preference order: the user's own choice, then the instance default, then
    whatever is first in the admin's ordering. Each is checked against what the
    user may actually reach, so a default they have lost access to falls
    through rather than producing a chat they cannot use.
    """
    from lembas.security import permissions
    from lembas.services import settings_store

    reachable = permissions.models_visible_to(db, user)
    if not reachable:
        return None

    by_id = {model.model_id: model for model in reachable}

    preferred = (user.settings_json or {}).get("default_model") if user is not None else None
    if preferred and preferred in by_id:
        return preferred, by_id[preferred].connection_id

    instance_default = settings_store.get(db, "default_model")
    if instance_default and instance_default in by_id:
        return instance_default, by_id[instance_default].connection_id

    # First in the administrator's ordering. Pinning is a sidebar shortcut, not
    # a reordering, so it deliberately does not influence this.
    chosen = sorted(reachable, key=lambda m: (m.position, m.model_id))[0]
    return chosen.model_id, chosen.connection_id


def available_models(db: DBSession, user=None, group: str | None = None) -> list[Model]:
    """Models this user may start a chat with, in the administrator's order.

    Pinning does NOT hoist a model up this list: pinned models get their own
    shortcuts in the sidebar, and a picker whose order silently differs from
    the one configured in the admin screen is just confusing.

    `group` narrows to the models whose connection is in one data group for this
    person -- what a chat that already exists may switch to, and who may be
    asked or added to it. `None` is the new-chat screen, where any model can
    start a chat and the chat then takes that model's group.
    """
    from lembas.security import permissions

    reachable = permissions.models_visible_to(db, user)
    if group is not None:
        from lembas.services import data_groups

        groups = data_groups.connection_groups(db, user)
        reachable = [
            m for m in reachable if groups.get(m.connection_id, data_groups.DEFAULT_GROUP) == group
        ]
    return sorted(reachable, key=lambda m: (m.position, m.model_id))


def fallback_title(text: str) -> str:
    """Derive a chat title from the opening message, without calling a model."""
    cleaned = " ".join(text.split())
    if not cleaned:
        return "New chat"
    if len(cleaned) <= MAX_TITLE_LENGTH:
        return cleaned
    # Prefer a word boundary, but only if it does not cut the title in half.
    clipped = cleaned[:MAX_TITLE_LENGTH]
    space = clipped.rfind(" ")
    if space > MAX_TITLE_LENGTH * 0.6:
        clipped = clipped[:space]
    return clipped.rstrip(" ,.;:-") + "…"


async def generate_title(
    endpoint: Endpoint,
    model_id: str,
    question: str,
    answer: str,
    *,
    template: str,
    thinking_off: bool = False,
) -> str:
    """Ask the model for a short chat title.

    Best-effort by design: any failure falls back to trimming the first
    message. Naming a chat is never worth surfacing an error for.

    `template` is passed in rather than read here because this runs after the
    generation's session has closed -- see `generation._run`. An empty one means
    an administrator cleared the fragment, which is how auto-titling is turned
    off: no request is made at all.
    """
    from lembas.services import prompts as prompts_service

    if not template.strip():
        return fallback_title(question)

    from lembas.services.reasoning import strip_reasoning

    prompt = prompts_service.substitute(
        template, {"question": question[:500], "answer": answer[:500]}
    )
    body = {
        "model": model_id,
        "messages": [{"role": ROLE_USER, "content": prompt}],
        # Enough that a model which thinks before answering can do both. It was
        # 24, which is ample for six words and nowhere near enough for a
        # reasoning model: the whole budget went on thinking and the reply came
        # back either empty or as an unclosed `<think>`, so every chat on such a
        # model silently fell back to its first prompt and looked as though
        # titling had never run.
        "max_tokens": TITLE_MAX_TOKENS,
        "temperature": 0.2,
    }
    # Deliberately *not* `apply_effort(body, "low")`, tempting as it is: naming
    # a chat does not reward deliberation and a low effort would make this call
    # much cheaper. But `reasoning_effort` and `chat_template_kwargs` appear
    # only when somebody has opted in, precisely so a provider strict about
    # unknown parameters sees exactly the request it always did — and sending
    # them here would put them on every instance's title call, where a 400 is
    # caught and turned into a fallback title. That is titling silently
    # switching itself off, which is the failure this whole change is fixing.
    # The token budget above is what makes room for the thinking instead.
    #
    # The one exception is a model an administrator marked as one whose thinking
    # can be switched off (`thinking_switch`, harness spec 3.2.0): that marking
    # *is* the opt-in for `chat_template_kwargs.enable_thinking`, so the title is
    # asked for with thinking off -- an answer in a second instead of a reasoned
    # paragraph about six words.
    if thinking_off:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    try:
        raw = await complete(endpoint, body)
    except LLMError as exc:
        log.debug("auto-title failed, using fallback: %s", exc)
        return fallback_title(question)

    # `complete` hands back `message.content` as it arrived. A model that emits
    # `<think>` tags inline puts them in exactly that field, so without this the
    # title was "<think>Okay, the user wants a short title for". Reasoning sent
    # in a separate `reasoning_content` field is ignored by `complete` already.
    answered, _thinking = strip_reasoning(raw)

    title = " ".join(answered.split()).strip().strip('"“”\'')
    # Small models sometimes ignore the instruction and answer the question
    # instead; an over-long reply is a better signal of that than anything else.
    if not title or len(title) > MAX_TITLE_LENGTH * 1.5:
        return fallback_title(question)
    return title[:MAX_TITLE_LENGTH]


def create_message(
    db: DBSession,
    chat: Chat,
    role: str,
    content: str = "",
    *,
    complete_: bool = True,
    model_id: str = "",
    queued: bool = False,
    machine: bool = False,
    origin: str = "",
) -> Message:
    message = Message(
        chat_id=chat.id,
        role=role,
        content=content,
        complete=complete_,
        model_id=model_id,
        queued=queued,
        machine=machine,
        origin=origin,
    )
    db.add(message)
    db.commit()
    return message


def delete_chats(db: DBSession, chats) -> int:
    """Delete chats, and the files their attachments point at.

    **The one way to delete a chat.** `db.delete(chat)` cascades to its messages
    and to its attachment *rows*, and leaves every file on disk -- a generated
    image, an uploaded PDF, a photo -- with nothing that will ever look at them
    again: `sweep_orphans` only considers uploads that were never attached.

    `files_service.remove_files_for_chats` was written for exactly this and was
    called from one place, the temporary sweep. The delete button, a schedule's
    task chat, a helper's hidden chat and deleting an account all went straight
    to `db.delete`, so four of the five ways a chat can end leaked its files.
    That is `sharing.forget_principal` again: a helper that exists, is correct,
    and is not called on the path that needs it.

    The order matters and is why this is a function rather than a note. The
    files have to be unlinked **while the rows still say which they are**, so it
    happens before the delete and in the same session.

    Does not commit -- the caller decides, because some of them are deleting
    other things in the same transaction.
    """
    live = [chat for chat in chats if chat is not None]
    if not live:
        return 0
    # A lead chat's team goes with it (spec 3.2.0, team.md §4): its members
    # are stopped, its rows deleted, and the members' hidden chats join this
    # delete.
    from lembas.services import team_run

    members = team_run.forget_for_chats(db, [chat.id for chat in live])
    if members:
        known = {chat.id for chat in live}
        live.extend(m for m in members if m.id not in known)
    files_service.remove_files_for_chats(db, [chat.id for chat in live])
    # Files a device showed in them: their blobs released while the rows name them.
    from lembas.services import shown

    shown.forget_for_chats(db, [chat.id for chat in live])
    # A chat on a device: its session goes on the device too, and the
    # tombstone stops the device announcing it back as a new chat -- a swept
    # temporary chat would otherwise return as a permanent one. An open shell
    # on it is closed too, where there is an event loop to do it on.
    import asyncio

    from lembas.services import device_link
    from lembas.services.agent import terminal as terminal_service

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    for chat in live:
        if getattr(chat, "device_token_id", None):
            # With `db`: the request to the device is also written down, in
            # this transaction, so a device that is off now is told when it
            # links.
            device_link.forget_session(chat.device_token_id, chat.device_session_id, db)
            if loop is not None:
                loop.create_task(terminal_service.close_chat(chat.id))
    for chat in live:
        db.delete(chat)
    return len(live)


RECENT_DIRS = 8


def recent_dirs(
    db: DBSession, user_id: str, token_ids: list[str], *, limit: int = RECENT_DIRS
) -> dict[str, list[str]]:
    """The directories this person's agent chats have used on each device,
    most recent first, at most `limit` per device.

    What the new-agent-chat composer offers before anything else: the
    directory somebody worked in last on that machine is nearly always the one
    they want next, and a path is something you would rather pick than spell.
    Recency is the chat's own `updated_at` -- every turn touches it -- and a
    directory used by several chats counts at its latest. Temporary chats are
    left out: one is a scratch conversation (or a helper's), and its directory
    is not one somebody chose to come back to.

    One query for every device, grouped, rather than one per device.
    """
    found: dict[str, list[str]] = {token_id: [] for token_id in token_ids}
    if not token_ids:
        return found
    latest = func.max(Chat.updated_at)
    rows = db.execute(
        select(Chat.device_token_id, Chat.project_dir, latest)
        .where(
            Chat.user_id == user_id,
            Chat.kind == KIND_AGENT,
            Chat.device_token_id.in_(token_ids),
            Chat.project_dir != "",
            Chat.temporary.is_(False),
        )
        .group_by(Chat.device_token_id, Chat.project_dir)
        .order_by(latest.desc(), Chat.project_dir)
    ).all()
    for token_id, path, _when in rows:
        bucket = found[token_id]
        if len(bucket) < limit:
            bucket.append(path)
    return found


def read_only_temporary(chat: Chat | None) -> bool:
    """Whether this is a person's temporary chat, which may read but not write.

    Derived rather than stored: `Keep this chat` clears `temporary` and with it
    every restriction, with no second flag to forget. A helper's chat is also
    `temporary` -- so it is in no listing and is swept -- and is told apart by its
    parent: its writing is decided by the helper's own `scope_json["write"]`.
    """
    if chat is None:
        return False
    return bool(getattr(chat, "temporary", False)) and not getattr(chat, "parent_chat_id", None)


def temporary_lifetime(db: DBSession) -> int:
    """Minutes a person's temporary chat lives after its last message; 0 = until
    they leave it. Clamped, so a hand-edited setting cannot keep one for ever."""
    from lembas.services import settings_store

    try:
        minutes = int(settings_store.get(db, "temporary_lifetime_minutes"))
    except (TypeError, ValueError):
        minutes = 60
    return min(max(minutes, 0), TEMPORARY_LIFETIME_MAX_MINUTES)


def lifetime_words(minutes: int, *, translate: bool = True) -> str:
    """The lifetime as the badge says it: "when you leave it", "1 hour after the
    last message". `translate=False` for the system prompt, which is English
    whatever the reader's interface language is."""
    from lembas.web.i18n import t as _t

    def t(text: str, **fields: object) -> str:
        return _t(text, **fields) if translate else (text % fields if fields else text)

    if minutes <= 0:
        return t("when you leave it") if translate else "when the person leaves it"
    if minutes % 60 == 0:
        hours = minutes // 60
        return t("1 hour after the last message") if hours == 1 else t(
            "%(n)s hours after the last message", n=hours
        )
    return t("%(n)s minutes after the last message", n=minutes)


# Chats whose page reported a leave, and when. In memory on purpose: a single
# worker owns every chat, the sweep's floor catches anything a restart forgets,
# and a row written on every page-hide would be a write per navigation.
_LEFT: dict[str, datetime] = {}


def mark_left(chat_id: str, *, now: datetime | None = None) -> None:
    _LEFT[chat_id] = now or datetime.now(UTC)


def mark_back(chat_id: str) -> None:
    """The chat was opened again -- a reload, or a second tab -- so the leave did
    not happen."""
    _LEFT.pop(chat_id, None)


def _generating(chat_id: str) -> bool:
    from lembas.services import generation

    return generation.running_for(chat_id) is not None


def sweep_temporary(
    db: DBSession,
    older_than: timedelta | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Delete temporary chats whose time is up.

    A person's temporary chat lives `temporary_lifetime` minutes after its last
    message (with `LEAVE_FLOOR` when that is 0, "until you leave it"), and is
    also deleted `LEAVE_GRACE` after its page reported a leave when the setting is
    0. A helper's kept chat lives `TEMPORARY_LIFETIME`. `older_than` overrides
    both, for a caller that wants one age for everything.

    Age is measured from the newest message rather than from the chat row's own
    timestamps. `created_at` would destroy a conversation still in use, and
    `updated_at` does not move when a message is inserted -- `onupdate` fires on
    an UPDATE of the chat, and adding a message is not one.

    A chat with a reply being written is never deleted: the reply would land in
    a row that no longer exists. It is picked up on a later pass.

    Runs at startup and from the ticker, about once a minute.
    """
    now = now or datetime.now(UTC)
    minutes = temporary_lifetime(db)
    if older_than is not None:
        person_age = helper_age = older_than
    else:
        person_age = timedelta(minutes=minutes) if minutes else LEAVE_FLOOR
        helper_age = TEMPORARY_LIFETIME

    newest = (
        select(Message.chat_id, func.max(Message.created_at).label("last"))
        .group_by(Message.chat_id)
        .subquery()
    )
    last = func.coalesce(newest.c.last, Chat.created_at)
    stale = list(
        db.scalars(
            select(Chat)
            .outerjoin(newest, newest.c.chat_id == Chat.id)
            .where(
                Chat.temporary.is_(True),
                (
                    (Chat.parent_chat_id.is_(None) & (last < now - person_age))
                    | (Chat.parent_chat_id.is_not(None) & (last < now - helper_age))
                ),
            )
        )
    )
    if not minutes and older_than is None:
        gone = [chat_id for chat_id, at in _LEFT.items() if at <= now - LEAVE_GRACE]
        seen = {chat.id for chat in stale}
        for chat_id in gone:
            chat = db.get(Chat, chat_id)
            if chat is None or not read_only_temporary(chat):
                _LEFT.pop(chat_id, None)
                continue
            if chat_id not in seen:
                stale.append(chat)
    stale = [chat for chat in stale if not _generating(chat.id)]
    if not stale:
        return 0

    for chat in stale:
        _LEFT.pop(chat.id, None)
    delete_chats(db, stale)
    db.commit()
    log.info("swept %d temporary chat(s)", len(stale))
    return len(stale)


__all__ = [
    "ROLE_ASSISTANT",
    "ROLE_USER",
    "available_models",
    "build_request",
    "create_message",
    "default_model",
    "fallback_title",
    "generate_title",
    "resolve_endpoint",
]
