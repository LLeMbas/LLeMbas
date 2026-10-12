"""Handing a piece of a reply to a second model that runs on its own.

## What it is

`subagent_run` creates a hidden chat, puts one self-contained task into it, lets
the ordinary generation loop answer it, and gives the answer back as the tool
result. That is the whole mechanism. Nothing about streaming, rounds, budgets,
metrics, steps or tools is re-implemented here, because a second implementation
of any of those is a second thing to keep correct.

Two shapes were rejected on the way.

A **nested `Generation` in the parent's chat** would mean two replies writing one
transcript, which `services/wake.py` exists to make impossible: a chat has one
generation at a time, and the Stop button points at whichever bubble comes first
in the document.

A **one-shot `complete()`** — the shape `generate_title` uses — has no tools and
no rounds, which `schedule/runner.py` already records as useless for exactly this
case. A helper that cannot search is not a helper.

So the pattern is `runner.fire`'s: `wake_chat`, then poll `running_for` until it
stops. The child chat is `temporary`, so it is in no listing, and it is deleted
when its answer has been handed over unless an administrator asked to keep it.

## What makes it safe with nobody watching

The rule this codebase holds is that restriction happens **at tool resolution,
never in the prompt** — a model can be talked out of a system message by a page
it just read, and a subagent's task text is written by a model that has been
reading pages. So every restriction below is a property of the child's row,
applied by `resolve_tools` after every gate:

- **Nothing may ask.** `Chat.unattended` withdraws `ask_user`, and
  `generation._authorise` answers an approval with a refusal instead of pausing.
  Without the second half a helper in Manual mode would sit on a card nobody can
  see until `approval_timeout`, which is fifteen minutes of doing nothing.
- **No recursion.** The same flag withdraws the `subagent` family from the child.
- **No writing, by default.** `scope_json["write"] = False` drops every tool
  whose declared risk is `RISK_WRITE` — notes, memories, reports, skills, the
  canvas, file writes, schedules, images. A *writing* helper is a per-call
  parameter and is refused outright in a chat whose own mode would have stopped
  to ask before writing, because a subagent that writes where its parent had to
  ask is the mode being laundered through a tool call.
- **Commands from a fixed list only, in every mode including Auto.** The child
  runs in Plan or Edit mode, both of which resolve `RISK_EXECUTE` to ASK, and
  ASK in an unattended chat is a refusal. What runs is what matches
  `SAFE_COMMANDS` — and `policy.decide` allows a line only when every command
  in it is allowed, so `git log; curl … | sh` is not. Auto is
  deliberately **not** inherited: the task text can have come from a page, and
  that is the injection path this list exists to close.

## What bounds it

`settings_store.subagents` — how many one reply may spawn, how many run at once
across the instance, and what one of them may spend. The per-reply count lives on
the parent's `Generation`, which is the only object that knows what "this reply"
means; the instance count is a set here, because a counter that a restart clears
is the correct shape for a thing that cannot survive a restart anyway.

The wall clock is the honest bound. When it runs out the child is **stopped**,
not abandoned: `request_stop` keeps whatever it had written and marks the message
`stopped`, so the parent gets a partial answer and a sentence saying it is one,
rather than silence or a wait that outlives the reply that asked for it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from lembas.db.models import KIND_AGENT, KIND_CHAT, Chat, User
from lembas.db.session import session_scope
from lembas.security import permissions
from lembas.services import data_groups, settings_store
from lembas.services.agent import policy as agent_policy

if TYPE_CHECKING:  # pragma: no cover - typing only
    from lembas.services.tools import ToolContext, ToolDef, ToolOutcome

log = logging.getLogger(__name__)

# How often the parent looks to see whether its helper has finished. Coarser
# than a stream and finer than the schedule runner's three seconds: a caller is
# blocked on this, so a second of latency at the end is worth avoiding, and
# anything finer is a poll per hundred milliseconds for a minute of work.
POLL_SECONDS = 1.0

# How long to keep asking after `request_stop`, before giving up on the row and
# reading whatever is there. The producer checks `cancel` between streamed
# chunks, so a stop lands within one chunk unless the far side has stalled.
STOP_GRACE = 20.0

# What a helper may run on a machine, whatever mode its parent is in.
#
# Every entry is a command that reads. There is no `find -delete`, no `git
# checkout`, no package manager: the list is short because the argument for
# adding to it is always "this one is fine", and the sum of those is a shell.
# `policy.decide` splits a line into its commands and needs every one allowed,
# so none of these can be extended with a `;` or a pipe.
#
# `read`, `list`, `glob` and `grep` are here as tool names rather than commands --
# a tool that is not `bash` is matched by its name (its name before harness spec
# v1 too).
SAFE_COMMANDS: tuple[str, ...] = (
    "read",
    "list",
    "glob",
    "grep",
    "ls",
    "ls *",
    "pwd",
    "cat *",
    "head *",
    "tail *",
    "wc *",
    "file *",
    "stat *",
    "du *",
    "df *",
    "tree *",
    "find *",
    "grep *",
    "rg *",
    "git status",
    "git log*",
    "git show*",
    "git diff*",
    "git branch",
    "git remote -v",
)

# Modes a helper may be given, and nothing else. Plan reads; Edit reads and
# writes files. Neither allows a command outside the list above, because both
# resolve RISK_EXECUTE to ASK and an unattended chat cannot ask.
MODE_READING = agent_policy.MODE_PLAN
MODE_WRITING = agent_policy.MODE_EDIT

# The parent modes from which a *writing* helper may be asked for. In Manual and
# Plan the reader is stopped before anything is written, and a helper that wrote
# on the model's own authority would be that rule going through a side door.
WRITING_ALLOWED_FROM = (agent_policy.MODE_EDIT, agent_policy.MODE_AUTO)

# Helpers running right now, across the instance, by child chat id. In-process
# and cleared by a restart, which is correct: a restart abandons replies in
# flight, so there is nothing for a durable count to describe.
_LIVE: set[str] = set()


def live_count() -> int:
    return len(_LIVE)


def clear() -> None:
    """For tests. The set is the only state this module holds."""
    _LIVE.clear()


# --- Building the child --------------------------------------------------------
def _child_scope(parent: Chat, *, write: bool) -> dict[str, Any]:
    """What the helper's chat is narrowed to.

    `families` names the two withdrawals that are absolute; `write` is the
    risk-class narrowing; `allow` is the command list. Everything else the
    parent had, the child has -- searching, fetching, reading the library --
    because a helper that cannot look things up is a slower way of asking the
    same model the same question.

    The parent's own narrowing is carried across whole. A chat with web search
    switched off must not be able to reach it by delegating.
    """
    inherited = dict((parent.scope_json or {}).get("families") or {})
    inherited.update({"ask": False, "subagent": False})
    return {
        "families": inherited,
        "skills": dict((parent.scope_json or {}).get("skills") or {}),
        "write": bool(write),
        "allow": list(SAFE_COMMANDS),
    }


def _create_child(
    db,
    parent: Chat,
    *,
    title: str,
    write: bool,
) -> Chat:
    """The hidden chat one helper runs in.

    A helper inherits the parent's model, connection and reasoning effort, and
    nothing else -- always the parent's model. The effort has to be **seeded onto the row**
    rather than left to be inherited at request time: `chat_service.resolved_effort`
    reads the chat's own `params_json` and deliberately consults no fallback, so
    a helper of a high-effort reply would otherwise quietly run at none.
    """
    from lembas.services import chat as chat_service

    child = Chat(
        user_id=parent.user_id,
        kind=KIND_CHAT,
        title=title[:200] or "Helper",
        model_id=parent.model_id,
        connection_id=parent.connection_id,
        data_group_id=data_groups.for_chat(db, parent),
        # Never in a listing, and swept a day later even if it is kept.
        temporary=True,
        parent_chat_id=parent.id,
        unattended=True,
        scope_json=_child_scope(parent, write=write),
    )
    effort = chat_service.resolved_effort(parent)
    if effort:
        child.params_json = {"reasoning_effort": effort}
    # The bases the parent is scoped to, or the helper searches everything its
    # owner can see and answers from documents the parent was not looking at.
    child.knowledge_bases = list(parent.knowledge_bases)
    db.add(child)
    db.commit()
    return child


def _task_turn(task: str, context: str) -> str:
    """The one turn a helper is given.

    Named as a delegation in *words*, for the reason `wake.py` sets out: the
    role stays `user` because `build_messages` requires one there, so the
    framing cannot live in the role. `core.subagent` is the other half — this
    says what the job is, the fragment says what being a helper means.
    """
    lines = [
        "You are answering a request from another model, not from a person. "
        "Nobody is reading this conversation; your reply is handed back whole "
        "as the result of one tool call.",
        "",
        "## The task",
        task.strip(),
    ]
    if context.strip():
        lines += ["", "## What you have been told about it", context.strip()]
    return "\n".join(lines)


# --- Running one ---------------------------------------------------------------
async def _await_reply(chat_id: str, message_id: str, deadline: float) -> bool:
    """Wait for the helper's reply. True if it finished on its own.

    Polled rather than awaited on the task, the same reasoning
    `schedule/runner._await_reply` writes down: `generation` owns its registry
    and reaching into it from here would couple this to internals whose whole
    job is to be replaceable.
    """
    from lembas.services import generation as generation_service

    while time.monotonic() < deadline:
        running = generation_service.running_for(chat_id)
        # The id check is what stops this waiting on some *later* reply in the
        # same chat -- there is nothing else to produce one here, but the same
        # loop in `runner` needed it and the cost of keeping it is nothing.
        if running is None or running.message_id != message_id:
            return True
        await asyncio.sleep(POLL_SECONDS)
    return False


async def _stop(chat_id: str, message_id: str) -> None:
    """End a helper that has run out of clock, keeping what it wrote.

    `request_stop` rather than cancelling the task: it sets the flag the
    producer checks between chunks, so the partial reply is persisted and marked
    `stopped` rather than `error`. An abandoned generation would go on spending
    the endpoint after the parent had stopped caring.
    """
    from lembas.services import generation as generation_service

    generation_service.request_stop(message_id)
    deadline = time.monotonic() + STOP_GRACE
    while time.monotonic() < deadline:
        running = generation_service.running_for(chat_id)
        if running is None or running.message_id != message_id:
            return
        await asyncio.sleep(POLL_SECONDS)
    log.warning("subagent %s did not stop within the grace period", chat_id)


def _harvest(db, chat_id: str, message_id: str) -> tuple[str, str]:
    """The helper's answer, and what went wrong if anything did.

    Read from the row rather than from the `Generation`, because `_persist` is
    the single writer and the row is authoritative the moment `done` is set --
    the same order `_follow` depends on.
    """
    from lembas.db.models import Message

    message = db.get(Message, message_id)
    if message is None or message.chat_id != chat_id:
        return "", "The helper's reply could not be found."
    text = (message.content or "").strip()
    if message.error:
        return text, str(message.error)
    if not text:
        return "", "The helper produced no answer."
    return text, ""


def _tool_names(db, chat_id: str) -> list[str]:
    """What the helper actually did, for the transcript.

    The tool names off its messages, in order, deduplicated by run. It is what
    makes a collapsed block worth expanding: an answer alone cannot say whether
    it was researched or recalled.
    """
    from sqlalchemy import select

    from lembas.db.models import Message

    names: list[str] = []
    rows = db.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.created_at)
    )
    for row in rows:
        for event in row.tool_calls_json or []:
            name = str((event or {}).get("name") or "")
            if name and (not names or names[-1] != name):
                names.append(name)
    return names


def _cleanup(chat_id: str, *, keep: bool) -> None:
    """Delete the helper's chat unless an administrator asked to keep it.

    Best-effort and outside every other session: a helper whose answer has been
    handed back has done its job, and failing to tidy up must not turn a good
    result into an error. Kept chats are `temporary`, so the day-old sweep gets
    them either way.
    """
    if keep:
        return
    try:
        with session_scope() as db:
            from lembas.services import chat as chat_service

            chat = db.get(Chat, chat_id)
            if chat is not None:
                # A writing helper can generate an image or attach a file, and
                # `db.delete` would leave both on disk with the row that named
                # them gone.
                chat_service.delete_chats(db, [chat])
    except Exception:  # noqa: BLE001 - tidying up is not the result
        log.debug("could not remove subagent chat %s", chat_id, exc_info=True)


# --- The tool ------------------------------------------------------------------
def _outcome(text: str, event: dict[str, Any]) -> ToolOutcome:
    """Imported inside the call: `services/tools.py` imports this module to build
    the definition, so a top-level import back is a cycle."""
    from lembas.services.tools import ToolOutcome

    return ToolOutcome(text, event)


def _error(message: str, *, task: str = "") -> ToolOutcome:
    return _outcome(
        message,
        {"name": "subagent_run", "status": "error", "query": task[:120], "error": message},
    )


def _budget(generation, values: dict[str, Any]) -> str:
    """Whether this reply may spawn another helper, and why not if it may not.

    Counted on the parent's `Generation` because that is the only object that
    knows what "this reply" is: a chat-keyed counter would have to be reset by
    something, and every candidate for that something is a place to forget.
    Read and incremented with no `await` between, which is what makes it safe
    against the four calls a round runs together.

    `values` is passed in rather than read here, so this can be called from
    inside the caller's session -- opening a second one underneath an open one
    is a shape this codebase does not have anywhere else and is not worth
    introducing for a settings lookup.
    """
    if generation is None:
        # No generation means no reply to bound. It happens if a runner is ever
        # reached outside the loop; refusing is the answer that cannot be wrong.
        return "This reply cannot delegate."
    if generation.subagents >= int(values["max_per_reply"]):
        return (
            f"This reply has already used its {values['max_per_reply']} helpers. "
            "Do the rest yourself, or answer with what you have."
        )
    if len(_LIVE) >= int(values["max_concurrent"]):
        return (
            "Too many helpers are running on this instance right now. "
            "Do this part yourself rather than waiting."
        )
    generation.subagents += 1
    return ""


async def _run_subagent(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from lembas.services import generation as generation_service
    from lembas.services import wake as wake_service

    task = str(args.get("task") or "").strip()
    title = str(args.get("title") or "").strip() or task[:60]
    briefing = str(args.get("context") or "")
    want_write = bool(args.get("write"))

    if not task:
        return _error(
            "A helper needs a task: what to find out or do, written out in full. "
            "It starts with none of this conversation, so say everything it needs."
        )

    parent_id = context.chat_id
    if not parent_id:
        return _error("There is no conversation to delegate from.", task=task)

    with session_scope() as db:
        parent = db.get(Chat, parent_id)
        if parent is None:
            return _error("That conversation no longer exists.", task=task)
        # Enforced here as well as by the withdrawn family, because this is the
        # cheaper half to get right and the two failures look different: the
        # family withdrawal means the model is never offered the tool, and this
        # means a call that arrived by some other path is refused rather than
        # opening a third level.
        if parent.parent_chat_id or parent.unattended:
            return _error("A helper cannot ask for a helper of its own.", task=task)

        write = want_write
        # A temporary chat writes nothing, and a helper is not a way round that:
        # it would write on the chat's behalf into the same library. Refused
        # rather than quietly downgraded, so the model knows to send a reader.
        from lembas.services.chat import read_only_temporary

        if write and read_only_temporary(parent):
            return _error(
                "This is a temporary chat, which may read but not change anything, "
                "so a helper cannot write either. Send a reading helper, or ask the "
                "reader to keep the chat first.",
                task=task,
            )
        if write and parent.kind == KIND_AGENT and parent.agent_mode not in WRITING_ALLOWED_FROM:
            return _error(
                "This chat is in "
                f"{agent_policy.MODE_LABELS.get(parent.agent_mode, parent.agent_mode)} mode, "
                "where you are stopped before anything is written — so a helper "
                "cannot write either, since nobody can be stopped to ask. Send a "
                "reading helper and make the changes yourself, or ask the reader "
                "to switch mode.",
                task=task,
            )
        owner = db.get(User, parent.user_id)
        if owner is None:  # pragma: no cover - a chat outliving its owner
            return _error("That account no longer exists.", task=task)

        # On the parent's own model, unless that serves one request at a time:
        # the helper would take its only slot and push this conversation's
        # cached prompt out of it.
        refusal = capacity_refusal(db, parent)
        if refusal:
            return _error(refusal, task=task)

        # After the refusals above and before anything is created. The order is
        # the design: a call that could never have worked should be told *why*
        # rather than told it has run out of helpers, and the counter should
        # only move for a call that is about to spend one.
        values = settings_store.subagents(db)
        # A group's ceiling narrows the instance's, never widens it. Zero on
        # either side means "no opinion", so the two cannot be folded with
        # `min` -- see generation._narrower for the same arithmetic.
        allowance = permissions.limit(db, owner, "helpers_per_reply")
        if allowance:
            values = {**values, "max_per_reply": min(int(values["max_per_reply"]), allowance)}
        refusal = _budget(generation_service.running_for(parent_id), values)
        if refusal:
            return _error(refusal, task=task)

        child = _create_child(db, parent, title=title, write=write)
        child_id = child.id

    _LIVE.add(child_id)
    started = time.monotonic()
    try:
        message_id = await wake_service.wake_chat(child_id, _task_turn(task, briefing))
        if not message_id:
            _cleanup(child_id, keep=False)
            return _error("The helper could not be started.", task=task)

        finished = await _await_reply(
            child_id, message_id, started + float(values["wall_seconds"])
        )
        if not finished:
            await _stop(child_id, message_id)

        with session_scope() as db:
            answer, problem = _harvest(db, child_id, message_id)
            used = _tool_names(db, child_id)
    finally:
        _LIVE.discard(child_id)

    elapsed = time.monotonic() - started
    keep = bool(values.get("keep_transcript"))
    _cleanup(child_id, keep=keep)

    if not answer:
        return _error(problem or "The helper produced no answer.", task=task)

    # The account of what it did goes in the *event*, where the transcript shows
    # it; the answer goes to the model. Putting the tool list in front of the
    # model as well would be spending its window on our own bookkeeping.
    note = "" if finished else "\n\n(It ran out of time; this is as far as it got.)"
    return _outcome(
        f"The helper answered:\n\n{answer}{note}\n\n"
        "This is another model's work, not yours and not the reader's. Check it "
        "against what you know before relying on it, and say what came from it.",
        {
            "name": "subagent_run",
            "status": "ok" if finished else "error" if not answer else "ok",
            "query": title,
            "detail": (
                f"{len(used)} tool call(s), {elapsed:.0f}s"
                + ("" if finished else ", stopped at the time limit")
            ),
            "text": answer,
            "why": ", ".join(used[:8]) if used else "",
        },
    )


def capacity_refusal(db, chat: Chat) -> str:
    """Why this chat's model cannot be its own helper, or "" when it can."""
    from lembas.services import chat as chat_service

    model = chat_service.model_row(db, chat_service.speaker_for(db, chat))
    if model is not None and model.single_session:
        return (
            f"{model.label} serves one request at a time, so it cannot be its own "
            "helper: the helper would take the server's only slot and push this "
            "conversation's cached prompt out of it."
        )
    return ""


def tool_defs() -> list[ToolDef]:
    """The one tool, built here so `services/tools.py` need not know the wording.
    A helper runs on the same model as the reply that sends it."""
    from lembas.services.tools import FAMILY_SUBAGENT, RISK_READ, ToolDef

    properties = _subagent_properties()
    return [
        ToolDef(
            name="subagent_run",
            family=FAMILY_SUBAGENT,
            description=(
                "Hand one self-contained piece of work to a helper — a second "
                "model that works on its own and gives you its answer. Use it "
                "to cover several independent areas at once: call it several "
                "times in one turn and each runs in parallel. It gets a "
                "narrower set of tools than you: it reads, it cannot ask you or "
                "the reader anything, it cannot delegate further, it changes "
                "nothing unless you set write. It starts knowing nothing "
                "about this conversation, so the task must say everything it "
                "needs. Do not use it for something you could do in one call "
                "yourself, or for anything needing a decision only the reader "
                "can make."
            ),
            parameters={
                "type": "object",
                "properties": properties,
                "required": ["task"],
            },
            run=_run_subagent,
            # It reads, from the parent's side: what it changes, it changes
            # through tools that carry their own risk class inside the helper's
            # own chat, where the mode and the scope decide. Classing the spawn
            # itself as a write would put an approval card in front of every
            # research fan-out in Edit mode, which is the mode that permits
            # writing anyway.
            risk=RISK_READ,
        ),
    ]


__all__ = [
    "MODE_READING",
    "MODE_WRITING",
    "SAFE_COMMANDS",
    "WRITING_ALLOWED_FROM",
    "capacity_refusal",
    "clear",
    "live_count",
    "tool_defs",
]


def _subagent_properties() -> dict[str, Any]:
    """The arguments `subagent_run` takes."""
    return {
        "task": {
            "type": "string",
            "description": (
                "What the helper is to do, written out in full and as "
                "an instruction. Say what a good answer contains and "
                "how long it should be. It is read on its own, with "
                "none of this conversation around it."
            ),
        },
        "title": {
            "type": "string",
            "description": "A few words naming this piece of work.",
        },
        "context": {
            "type": "string",
            "description": (
                "Facts the helper needs that it cannot look up — what "
                "the reader asked for, decisions already made, names "
                "and paths. Not a summary of the conversation."
            ),
        },
        "write": {
            "type": "boolean",
            "description": (
                "True if the helper must change something: write a "
                "file, keep a note, file a report. Leave it out for "
                "anything that only reads, which is nearly always. A "
                "writing helper is refused where you would have been "
                "stopped for approval yourself."
            ),
        },
    }
