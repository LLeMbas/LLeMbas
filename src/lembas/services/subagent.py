"""Handing a piece of a reply to a second model that runs on its own.

## What it is

`task` creates a hidden chat, puts one self-contained prompt into it, lets the
ordinary generation loop answer it, and gives the answer back -- as the tool
result when the call waits, or as a message to the parent when the helper runs
as a **background job** (`background: true`, spec 2.0.0). That is the whole
mechanism. Nothing about streaming, rounds, budgets, metrics, steps or tools is
re-implemented here, because a second implementation of any of those is a
second thing to keep correct.

Two shapes were rejected on the way.

A **nested `Generation` in the parent's chat** would mean two replies writing one
transcript, which `services/wake.py` exists to make impossible: a chat has one
generation at a time, and the Stop button points at whichever bubble comes first
in the document.

A **one-shot `complete()`** -- the shape `generate_title` uses -- has no tools and
no rounds, which `schedule/runner.py` already records as useless for exactly this
case. A helper that cannot search is not a helper.

So the pattern is `runner.fire`'s: `wake_chat`, then poll `running_for` until it
stops. The child chat is `temporary`, so it is in no listing; a synchronous
helper's chat is deleted when its answer has been handed over unless an
administrator asked to keep it, a background job's is kept while the job can
still be spoken to (`job_send` starts a finished helper again with its context
intact) and swept a day later with every other temporary chat.

## The job (spec 2.0.0, `loop.json` jobs)

Every helper has a row in `chat_jobs` (`services/jobs.py`): the id the model
knows it by (`job1`, `job2`, ... per chat), its state, how far the model has
read its output. A background `task` returns that id at once; `job_list`,
`job_output`, `job_stop` and `job_send` work on it, across requests and
restarts; the strip above the composer shows the chat's running helpers from
it. When a background helper ends, the parent is **woken** with the spec's note
(`jobs.note`): `wake_chat` writes it as a `machine` user turn -- queued, and
taken in at the parent's next round boundary by `_inject`, when the parent is
answering; a reply of its own, when it is idle. `report`, offered to the helper
alone, reaches the parent the same way before the helper's final reply.

## What makes it safe with nobody watching

The rule this codebase holds is that restriction happens **at tool resolution,
never in the prompt** -- a model can be talked out of a system message by a page
it just read, and a subagent's task text is written by a model that has been
reading pages. So every restriction below is a property of the child's row,
applied by `resolve_tools` after every gate:

- **Nothing may ask.** `Chat.unattended` withdraws `ask_user`, and
  `generation._authorise` answers an approval with a refusal instead of pausing.
  Without the second half a helper in Manual mode would sit on a card nobody can
  see until `approval_timeout`, which is fifteen minutes of doing nothing.
- **No recursion.** A helper's chat is handed `report` and nothing else of the
  `subagent` family (`tools._subagent_defs`), and `_run_subagent` refuses a
  call from one that arrived by any other path.
- **No writing, unless the task said so.** The `explore` agent (the default)
  sets `scope_json["write"] = False`, which drops every tool whose declared risk
  is `RISK_WRITE` -- notes, memories, reports, skills, the canvas, file writes,
  schedules, images. The `general` agent keeps them, and is refused outright in
  a chat whose own mode would have stopped to ask before writing, because a
  subagent that writes where its parent had to ask is the mode being laundered
  through a tool call.
- **Commands from a fixed list only, in every mode including Auto.** The child
  runs in Plan or Edit mode, both of which resolve `RISK_EXECUTE` to ASK, and
  ASK in an unattended chat is a refusal. What runs is what matches
  `SAFE_COMMANDS` -- and `policy.decide` allows a line only when every command
  in it is allowed, so `git log; curl … | sh` is not. Auto is deliberately
  **not** inherited: the task text can have come from a page, and that is the
  injection path this list exists to close.

## What bounds it

`settings_store.subagents` -- how many one reply may spawn, how many run at once
across the instance, and what one of them may spend. The per-reply count lives on
the parent's `Generation`, which is the only object that knows what "this reply"
means; the instance count is a set here, because a counter that a restart clears
is the correct shape for a thing that cannot survive a restart anyway.

The wall clock is the honest bound. When it runs out the child is **stopped**,
not abandoned: `request_stop` keeps whatever it had written and marks the message
`stopped`, so the parent gets a partial answer and a sentence saying it is one,
rather than silence or a wait that outlives the reply that asked for it. The
parent's own Stop reaches a helper the same way: the wait watches the parent's
`cancel`, and a stopped parent stops the helpers it was waiting on -- not its
background jobs, which it was not.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from lembas.db.models import (
    JOB_DONE,
    JOB_ERROR,
    JOB_RUNNING,
    JOB_STOPPED,
    KIND_CHAT,
    Chat,
    User,
)
from lembas.db.session import session_scope
from lembas.security import permissions
from lembas.services import data_groups, settings_store
from lembas.services import jobs as jobs_service
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

# The two kinds of helper, the harness spec's built-in agents. `explore` reads
# only and is the default; `general` has the full toolset the parent's chat
# allows -- what `subagent_run`'s `write: true` used to mean.
AGENT_EXPLORE = "explore"
AGENT_GENERAL = "general"
AGENTS = (AGENT_EXPLORE, AGENT_GENERAL)

# The two sentences `blocks/subagent.md` takes from this side: `{{report}}`,
# when the helper has the `report` tool, and `{{read_only}}`, for a helper that
# may change nothing. Each begins with the space that joins it to the sentence
# before, as the spec's text places them.
REPORT_SENTENCE = (
    " Before that, the report tool sends the main agent a message — a finding it should "
    "act on at once, or a question you cannot settle yourself — and a message from it "
    "may reach you meanwhile."
)
READ_ONLY_SENTENCE = " You can read and search; you cannot change anything."

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

# Helpers running right now, across the instance, by child chat id. In-process
# and cleared by a restart, which is correct: a restart abandons replies in
# flight, so there is nothing for a durable count to describe.
_LIVE: set[str] = set()

# The watchers of background jobs, held so the event loop does not collect a
# task nothing else refers to; each removes itself when it ends.
_WATCHERS: set[asyncio.Task] = set()


def live_count() -> int:
    return len(_LIVE)


def clear() -> None:
    """For tests. The set and the watchers are the only state this module holds."""
    _LIVE.clear()
    for task in list(_WATCHERS):
        task.cancel()
    _WATCHERS.clear()


# --- Building the child --------------------------------------------------------
def _child_scope(parent: Chat, *, write: bool, agent: str) -> dict[str, Any]:
    """What the helper's chat is narrowed to.

    `families` names the withdrawal that is absolute (asking); `write` is the
    risk-class narrowing; `allow` is the command list; `agent` is which kind of
    helper this is, for its prompt. Everything else the parent had, the child
    has -- searching, fetching, reading the library -- because a helper that
    cannot look things up is a slower way of asking the same model the same
    question.

    The parent's own narrowing is carried across whole. A chat with web search
    switched off must not be able to reach it by delegating.
    """
    inherited = dict((parent.scope_json or {}).get("families") or {})
    inherited.update({"ask": False})
    return {
        "families": inherited,
        "skills": dict((parent.scope_json or {}).get("skills") or {}),
        "write": bool(write),
        "allow": list(SAFE_COMMANDS),
        "agent": agent,
    }


def _create_child(
    db,
    parent: Chat,
    *,
    title: str,
    write: bool,
    agent: str = AGENT_EXPLORE,
    model=None,
) -> Chat:
    """The hidden chat one helper runs in.

    A helper inherits the parent's model, connection and reasoning effort, and
    nothing else -- the parent's model unless `task` named another the reader
    may use (`model`, a `Model` row in the parent's data group). The effort has
    to be **seeded onto the row** rather than left to be inherited at request
    time: `chat_service.resolved_effort` reads the chat's own `params_json` and
    deliberately consults no fallback, so a helper of a high-effort reply would
    otherwise quietly run at none.
    """
    from lembas.services import chat as chat_service

    child = Chat(
        user_id=parent.user_id,
        kind=KIND_CHAT,
        title=title[:200] or "Helper",
        model_id=model.model_id if model is not None else parent.model_id,
        connection_id=model.connection_id if model is not None else parent.connection_id,
        data_group_id=data_groups.for_chat(db, parent),
        # Never in a listing, and swept a day later even if it is kept.
        temporary=True,
        parent_chat_id=parent.id,
        unattended=True,
        scope_json=_child_scope(parent, write=write, agent=agent),
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


def agent_of(chat: Chat | None) -> str:
    """Which kind of helper a child chat runs as (`explore` where unsaid)."""
    scope = getattr(chat, "scope_json", None) or {}
    name = str(scope.get("agent") or "")
    return name if name in AGENTS else AGENT_EXPLORE


def _task_turn(task: str, context: str) -> str:
    """The one turn a helper is given: the task as the main agent wrote it.

    What being a helper means is in its system prompt (`core.subagent`, the
    spec's `blocks/subagent.md`), so the turn carries the work and nothing
    else. `subagent_run`'s former `context` is a section after it.
    """
    text = task.strip()
    if context.strip():
        text += "\n\n## What you have been told about it\n" + context.strip()
    return text


# --- Waiting on one ------------------------------------------------------------
FINISHED = "finished"
TIMED_OUT = "timed_out"
CANCELLED = "cancelled"


async def _await_reply(chat_id: str, deadline: float, parent=None) -> str:
    """Wait for the helper's chat to go quiet: FINISHED when nothing runs in it
    any more, TIMED_OUT at the deadline, CANCELLED when the parent reply that
    is waiting was stopped meanwhile.

    Polled rather than awaited on the task, the same reasoning
    `schedule/runner._await_reply` writes down: `generation` owns its registry
    and reaching into it from here would couple this to internals whose whole
    job is to be replaceable. "Nothing runs" rather than "this reply ended":
    a message sent to the helper in its last round is drained into a reply of
    its own when the first ends, and that reply is the same job's work.
    """
    from lembas.services import generation as generation_service

    while time.monotonic() < deadline:
        if parent is not None and getattr(parent, "cancel", False):
            return CANCELLED
        if generation_service.running_for(chat_id) is None:
            return FINISHED
        await asyncio.sleep(POLL_SECONDS)
    return TIMED_OUT


async def _stop(chat_id: str) -> None:
    """End a helper that has run out of clock, or whose parent stopped, keeping
    what it wrote.

    `request_stop` rather than cancelling the task: it sets the flag the
    producer checks between chunks, so the partial reply is persisted and marked
    `stopped` rather than `error`. An abandoned generation would go on spending
    the endpoint after the parent had stopped caring.
    """
    from lembas.services import generation as generation_service

    running = generation_service.running_for(chat_id)
    if running is None:
        return
    generation_service.request_stop(running.message_id)
    deadline = time.monotonic() + STOP_GRACE
    while time.monotonic() < deadline:
        if generation_service.running_for(chat_id) is None:
            return
        await asyncio.sleep(POLL_SECONDS)
    log.warning("subagent %s did not stop within the grace period", chat_id)


def _harvest(db, chat_id: str) -> tuple[str, str, bool]:
    """The helper's answer -- its last reply -- what went wrong if anything
    did, and whether it was stopped short.

    Read from the row rather than from the `Generation`, because `_persist` is
    the single writer and the row is authoritative the moment `done` is set --
    the same order `_follow` depends on.
    """
    from sqlalchemy import select

    from lembas.db.models import ROLE_ASSISTANT, Message

    message = db.scalars(
        select(Message)
        .where(Message.chat_id == chat_id, Message.role == ROLE_ASSISTANT)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).first()
    if message is None:
        return "", "The helper's reply could not be found.", False
    text = (message.content or "").strip()
    stopped = bool(message.stopped)
    if message.error:
        return text, str(message.error), stopped
    if not text:
        return "", "The helper produced no answer.", stopped
    return text, "", stopped


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
    except Exception:  # tidying up is not the result
        log.debug("could not remove subagent chat %s", chat_id, exc_info=True)


# --- The job row ---------------------------------------------------------------
def _finish_job(job_row_id: str, outcome: str, *, answer: str, problem: str, stopped: bool):
    """Close the job's row from what the helper's chat says, and return the
    facts the end note is made of."""
    with session_scope() as db:
        from lembas.db.models import ChatJob

        job = db.get(ChatJob, job_row_id)
        if job is None:
            return None
        steps, tools = jobs_service.counts(db, job.child_chat_id or "")
        if outcome == TIMED_OUT:
            reason, state = "budget", JOB_STOPPED
        elif outcome == CANCELLED or stopped or (job.data_json or {}).get("stopping"):
            # Stopped: by the parent's wait, by the row, or by `stop_job`'s
            # mark -- a person's or the model's stop is a fact the end note
            # states, whatever the child's last row happens to say.
            reason, state = "stopped", JOB_STOPPED
        elif problem and not answer:
            reason, state = "error", JOB_ERROR
        else:
            reason, state = "stop", JOB_DONE
        job.state = state
        job.ended = datetime.now(UTC)
        job.data_json = {
            **(job.data_json or {}),
            "report": answer,
            "reason": reason,
            "error": problem if reason == "error" else "",
            "steps": steps,
            "tools": tools,
        }
        db.commit()
        return {
            "kind": jobs_service.KIND_SUBAGENT,
            "id": job.job_id,
            "agent": job.agent,
            "description": job.description,
            "report": answer,
            "steps": steps,
            "tools": tools,
            "reason": reason,
            "error": problem if reason == "error" else "",
            "seconds": jobs_service.seconds_run(job),
            "chat_id": job.chat_id,
        }


def _show_job(parent_id: str, job_row_id: str) -> None:
    """The job as the parent's running reply shows it: onto `generation.jobs`,
    which the stream turns into the strip above the composer. After the
    reply, the strip polls the rows for itself."""
    from lembas.services import generation as generation_service

    running = generation_service.running_for(parent_id)
    if running is None:
        return
    try:
        with session_scope() as db:
            from lembas.db.models import ChatJob

            job = db.get(ChatJob, job_row_id)
            if job is not None:
                running.jobs[job.job_id] = jobs_service.info(db, job)
                running.touch()
    except Exception:  # the strip is a convenience, never the result
        log.debug("could not show job %s on chat %s", job_row_id, parent_id, exc_info=True)


async def _watch(job_row_id: str, child_id: str, parent_id: str, deadline: float) -> None:
    """Follow a background helper to its end, then wake the parent with the
    spec's note. The parent is not waited on and is never stopped by this:
    a background job outlives the turn that started it, by definition."""
    _LIVE.add(child_id)
    try:
        outcome = await _await_reply(child_id, deadline)
        if outcome == TIMED_OUT:
            await _stop(child_id)
        with session_scope() as db:
            answer, problem, stopped = _harvest(db, child_id)
        facts = _finish_job(job_row_id, outcome, answer=answer, problem=problem, stopped=stopped)
    except asyncio.CancelledError:
        raise
    except Exception:  # a job that dies silently is worse
        log.exception("background helper %s failed", child_id)
        facts = _finish_job(
            job_row_id, FINISHED, answer="", problem="The helper failed.", stopped=False
        )
    finally:
        _LIVE.discard(child_id)
    if facts is None:
        return
    _show_job(parent_id, job_row_id)
    from lembas.services import wake as wake_service

    await wake_service.wake_chat(parent_id, jobs_service.note(facts)["text"])


def _start_task(coroutine) -> None:
    task = asyncio.get_running_loop().create_task(coroutine)
    _WATCHERS.add(task)
    task.add_done_callback(_WATCHERS.discard)


def _start_watch(job_row_id: str, child_id: str, parent_id: str, deadline: float) -> None:
    # Counted live from here, not from the watcher's first step: two `task`
    # calls in one round must both see the first one's helper.
    _LIVE.add(child_id)
    task = asyncio.get_running_loop().create_task(_watch(job_row_id, child_id, parent_id, deadline))
    _WATCHERS.add(task)
    task.add_done_callback(_WATCHERS.discard)


async def _abandon(job_row_id: str, child_id: str) -> None:
    """The parent's reply was cancelled outright (a regeneration, a shutdown)
    while it waited on this helper: the helper is stopped and its row closed,
    or it would run on with nothing waiting and read `running` for ever."""
    with contextlib.suppress(Exception):
        await _stop(child_id)
        with session_scope() as db:
            answer, problem, stopped = _harvest(db, child_id)
        _finish_job(job_row_id, CANCELLED, answer=answer, problem=problem, stopped=stopped)


# --- The tool ------------------------------------------------------------------
def _outcome(text: str, event: dict[str, Any]) -> ToolOutcome:
    """Imported inside the call: `services/tools.py` imports this module to build
    the definition, so a top-level import back is a cycle."""
    from lembas.services.tools import ToolOutcome

    return ToolOutcome(text, event)


def _error(message: str, *, task: str = "", name: str = "task") -> ToolOutcome:
    return _outcome(
        message,
        {"name": name, "status": "error", "query": task[:120], "error": message},
    )


def _pick_model(db, owner: User, parent: Chat, wanted: str):
    """The model a `task` names, among those this person may use in the
    parent's data group: `connection/model`, or the model id alone when that
    is unambiguous, or the label the pickers show. None when there is no such
    model."""
    from lembas.services import chat as chat_service

    candidates = chat_service.available_models(db, owner, data_groups.for_chat(db, parent))
    connection, _, model_id = wanted.rpartition("/")
    matches = [
        m
        for m in candidates
        if m.model_id == (model_id if connection else wanted)
        and (
            not connection
            or (m.connection is not None and m.connection.name.casefold() == connection.casefold())
        )
    ]
    if len(matches) != 1:
        matches = [m for m in candidates if m.label == wanted]
    return matches[0] if len(matches) == 1 else None


def model_ref(model) -> str:
    """`connection/model`, as the spec names a model and `job_list` shows it."""
    if model is None:
        return ""
    connection = getattr(model, "connection", None)
    name = getattr(connection, "name", "") if connection is not None else ""
    return f"{name}/{model.model_id}" if name else str(model.model_id)


def helper_models(db, owner: User, chat: Chat) -> str:
    """The models a `task` may name here, as the prompt lists them: every one
    the reader may use in this chat's data group, `connection/model`."""
    from lembas.services import chat as chat_service

    models = chat_service.available_models(db, owner, data_groups.for_chat(db, chat))
    return ", ".join(ref for ref in (model_ref(m) for m in models) if ref)


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


def _started_text(job_id: str, agent: str, title: str) -> str:
    return (
        f"Started subagent {agent} as {job_id} ({title}). Carry on: its report comes to you "
        f"as a message when it ends. Meanwhile `job_output {job_id}` shows its progress, "
        f"`job_send {job_id}` gives it a message, and `job_stop {job_id}` stops it."
    )


async def _run_subagent(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """`task`, the spec's name: `prompt` is the whole job, `description` what the
    reader sees, `agent` which kind of helper, `background` whether to wait.
    `subagent_run` arrives here too, converted by `tools.resolve_alias`, and
    may still carry `write` -- which means the `general` agent. `worktree` is
    the CLI's: accepted and ignored, with a note in the result so the model is
    not left believing it got it."""
    from lembas.services import generation as generation_service
    from lembas.services import tools as tools_service
    from lembas.services import wake as wake_service

    task = str(args.get("prompt") or args.get("task") or "").strip()
    title = str(args.get("description") or args.get("title") or "").strip() or task[:60]
    briefing = str(args.get("context") or "")
    wanted_model = str(args.get("model") or "").strip()
    background = bool(args.get("background"))
    agent = str(args.get("agent") or "").strip() or AGENT_EXPLORE
    if args.get("write") and not args.get("agent"):
        agent = AGENT_GENERAL
    if agent not in AGENTS:
        return _error(
            f"There is no subagent called {agent!r} here. The agents are: "
            + "; ".join(f"{n} — {d}" for n, d in tools_service.HELPER_AGENTS)
            + ".",
            task=task,
        )
    write = agent == AGENT_GENERAL
    ignored = []
    if args.get("worktree"):
        ignored.append("a helper here works in no checkout, so worktree was ignored")

    if not task:
        return _error(
            "A helper needs a prompt: what to find out or do, written out in full. "
            "It starts with none of this conversation, so say everything it needs."
        )

    parent_id = context.chat_id
    if not parent_id:
        return _error("There is no conversation to delegate from.", task=task)

    with session_scope() as db:
        parent = db.get(Chat, parent_id)
        if parent is None:
            return _error("That conversation no longer exists.", task=task)
        # Enforced here as well as by the tool's absence, because this is the
        # cheaper half to get right and the two failures look different: the
        # absence means the model is never offered the tool, and this means a
        # call that arrived by some other path is refused rather than opening
        # a third level.
        if parent.parent_chat_id or parent.unattended:
            return _error("A helper cannot ask for a helper of its own.", task=task)

        # A temporary chat writes nothing, and a helper is not a way round that:
        # it would write on the chat's behalf into the same library. Refused
        # rather than quietly downgraded, so the model knows to send a reader.
        from lembas.services.chat import read_only_temporary

        if write and read_only_temporary(parent):
            return _error(
                "This is a temporary chat, which may read but not change anything, "
                "so a helper cannot write either. Send the explore agent, or ask the "
                "reader to keep the chat first.",
                task=task,
            )
        # A chat planning may not change anything, and a helper is not a way
        # round that either.
        if write and tools_service.plan_mode(parent):
            return _error(
                "This chat is in Plan mode, where nothing is changed — so a helper "
                "cannot write either. Send the explore agent, and make the changes "
                "once the plan is approved.",
                task=task,
            )
        owner = db.get(User, parent.user_id)
        if owner is None:  # pragma: no cover - a chat outliving its owner
            return _error("That account no longer exists.", task=task)

        # Another model the reader may use, named as the spec says
        # (`connection/model`, or the model id alone); else the parent's own.
        model_override = None
        if wanted_model:
            model_override = _pick_model(db, owner, parent, wanted_model)
            if model_override is None:
                return _error(
                    f"There is no model called {wanted_model!r} that this chat may use. "
                    "Leave `model` out to run the helper on this chat's own model.",
                    task=task,
                )

        # The spec's capacity rule: not on a model the server cannot hold
        # beside this conversation's, or beside a helper already running.
        refusal = capacity_refusal(db, parent, model_override)
        if refusal:
            return _error(refusal, task=task)

        # After the refusals above and before anything is created. The order is
        # the design: a call that could never have worked should be told *why*
        # rather than told it has run out of helpers, and the counter should
        # only move for a call that is about to spend one.
        values = settings_store.subagents(db)
        # A group's ceiling narrows the instance's, never widens it. Zero on
        # either side means "no opinion", so the two cannot be folded with a
        # bare `min`: only a set allowance narrows.
        allowance = permissions.limit(db, owner, "helpers_per_reply")
        if allowance:
            values = {**values, "max_per_reply": min(int(values["max_per_reply"]), allowance)}
        parent_generation = generation_service.running_for(parent_id)
        refusal = _budget(parent_generation, values)
        if refusal:
            return _error(refusal, task=task)

        child = _create_child(
            db, parent, title=title, write=write, agent=agent, model=model_override
        )
        child_id = child.id
        from lembas.services import chat as chat_service

        model_row = model_override or chat_service.model_row(
            db, chat_service.speaker_for(db, parent)
        )
        ref = model_ref(model_row)
        model_id = child.model_id or ""
        connection_id = child.connection_id or ""

    started = time.monotonic()
    message_id = await wake_service.wake_chat(child_id, _task_turn(task, briefing))
    if not message_id:
        _cleanup(child_id, keep=False)
        return _error("The helper could not be started.", task=task)

    with session_scope() as db:
        job = jobs_service.create(
            db,
            parent_id,
            child_chat_id=child_id,
            description=title,
            agent=agent,
            model=ref,
            message_id=message_id,
            background=background,
            model_id=model_id,
            connection_id=connection_id,
        )
        job_row_id, job_id = job.id, job.job_id
    _show_job(parent_id, job_row_id)
    deadline = started + float(values["wall_seconds"])

    if background:
        _start_watch(job_row_id, child_id, parent_id, deadline)
        text = _started_text(job_id, agent, title)
        if ignored:
            text += "\n\n(Note: " + "; ".join(ignored) + ".)"
        return _outcome(
            text,
            {
                "name": "task",
                "status": "ok",
                "query": title,
                "detail": f"{job_id} · {agent} · running in the background",
                "job": job_id,
                "background": True,
            },
        )

    _LIVE.add(child_id)
    try:
        outcome = await _await_reply(child_id, deadline, parent_generation)
        if outcome != FINISHED:
            await _stop(child_id)
        with session_scope() as db:
            answer, problem, stopped = _harvest(db, child_id)
            used = _tool_names(db, child_id)
    except asyncio.CancelledError:
        _start_task(_abandon(job_row_id, child_id))
        raise
    finally:
        _LIVE.discard(child_id)

    _finish_job(job_row_id, outcome, answer=answer, problem=problem, stopped=stopped)
    _show_job(parent_id, job_row_id)
    elapsed = time.monotonic() - started
    keep = bool(values.get("keep_transcript"))
    _cleanup(child_id, keep=keep)

    if not answer:
        return _error(problem or "The helper produced no answer.", task=task)

    # The account of what it did goes in the *event*, where the transcript shows
    # it; the answer goes to the model. Putting the tool list in front of the
    # model as well would be spending its window on our own bookkeeping.
    cut = outcome != FINISHED or stopped
    note = ""
    if outcome == TIMED_OUT:
        note = "\n\n(It ran out of time; this is as far as it got.)"
    elif cut:
        note = "\n\n(It was stopped; this is as far as it got.)"
    if ignored:
        note += "\n\n(Note: " + "; ".join(ignored) + ".)"
    how = {TIMED_OUT: ", stopped at the time limit", CANCELLED: ", stopped"}.get(outcome, "")
    if not how and stopped:
        how = ", stopped"
    return _outcome(
        f"The helper answered:\n\n{answer}{note}\n\n"
        "This is another model's work, not yours and not the reader's. Check it "
        "against what you know before relying on it, and say what came from it.",
        {
            "name": "task",
            "status": "stopped" if cut else "ok",
            "query": title,
            "detail": f"{job_id} · {agent} · {len(used)} tool call(s), {elapsed:.0f}s{how}",
            "text": answer,
            "why": ", ".join(used[:8]) if used else "",
            "job": job_id,
        },
    )


# --- The job tools ---------------------------------------------------------------
def _job_for(db, context: ToolContext, args: dict[str, Any], name: str):
    """The job this chat calls `id`, or the refusal to give the model."""
    job_id = str(args.get("id") or "").strip()
    if not context.chat_id:
        return None, _error("There is no conversation here to have jobs in.", name=name)
    job = jobs_service.by_id(db, context.chat_id, job_id) if job_id else None
    if job is None:
        return None, _error(
            f"There is no background job called {job_id!r} in this conversation. "
            "job_list says which there are.",
            name=name,
        )
    return job, None


async def _run_job_list(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    if not context.chat_id:
        return _error("There is no conversation here to have jobs in.", name="job_list")
    with session_scope() as db:
        shown = [
            j
            for j in jobs_service.for_chat(db, context.chat_id)
            if (j.data_json or {}).get("background", True)
        ]
        text = jobs_service.listing(db, shown)
        count = len([j for j in shown if j.state == JOB_RUNNING])
    return _outcome(
        text,
        {"name": "job_list", "status": "ok", "query": f"{count} running", "text": text},
    )


async def _run_job_output(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    wait = min(max(float(args.get("wait") or 0), 0.0), 60.0)
    with session_scope() as db:
        job, refused = _job_for(db, context, args, "job_output")
        if refused is not None:
            return refused
        job_row_id, child_id, state = job.id, job.child_chat_id, job.state
    if wait and state == JOB_RUNNING and child_id:
        # More first, up to a minute -- the spec's `wait`.
        await _await_reply(child_id, time.monotonic() + wait)
    with session_scope() as db:
        from lembas.db.models import ChatJob

        job = db.get(ChatJob, job_row_id)
        if job is None:
            return _error("That job is gone.", name="job_output")
        read = int((job.data_json or {}).get("read_rows") or 0)
        text, now = jobs_service.output(db, job, since_rows=read)
        job.data_json = {**(job.data_json or {}), "read_rows": now}
        db.commit()
        state = job.state
        job_id = job.job_id
    head = f"{job_id} is {state}."
    body = text.strip() or "Nothing new since you last looked."
    return _outcome(
        f"{head}\n{body}",
        {"name": "job_output", "status": "ok", "query": job_id, "text": body},
    )


async def stop_job(chat_id: str, job_id: str) -> str:
    """Stop a helper where it is: from the model (`job_stop`) or from the strip.
    Returns the state afterwards, or "" for an id the chat does not have. The
    watcher of a background job sees the chat go quiet and wakes the parent
    with the end note; a synchronous one returns to its caller the same way."""
    with session_scope() as db:
        job = jobs_service.by_id(db, chat_id, job_id)
        if job is None:
            return ""
        if job.state != JOB_RUNNING:
            return job.state
        child_id = job.child_chat_id or ""
        row_id = job.id
        job.data_json = {**(job.data_json or {}), "stopping": True}
        db.commit()
    await _stop(child_id)
    if not _watching(child_id):
        # Nothing waits on this helper -- its caller's wait or its watcher
        # would close the row -- so it was between two runs of a `job_send`,
        # or the instance restarted under it. Closed here.
        with session_scope() as db:
            answer, problem, stopped = _harvest(db, child_id)
        _finish_job(row_id, CANCELLED, answer=answer, problem=problem, stopped=stopped)
    return JOB_STOPPED


def _watching(child_id: str) -> bool:
    return child_id in _LIVE


async def _run_job_stop(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    with session_scope() as db:
        job, refused = _job_for(db, context, args, "job_stop")
        if refused is not None:
            return refused
        job_id, state = job.job_id, job.state
    if state != JOB_RUNNING:
        return _outcome(
            f"{job_id} had already ended ({state}).",
            {"name": "job_stop", "status": "ok", "query": job_id},
        )
    await stop_job(context.chat_id, job_id)
    return _outcome(
        f"{job_id} is being stopped where it is. What it had worked out comes to you as "
        "its report.",
        {"name": "job_stop", "status": "ok", "query": job_id},
    )


async def send_to_job(chat_id: str, job_id: str, text: str) -> str:
    """A message to a helper: given to it at its next step while it runs
    (`_inject` takes the queued turn), or, to a finished one, starts it again
    with its context intact under the same id. Returns the state afterwards,
    "" for an id the chat does not have, or "gone" when the helper's chat was
    removed (a synchronous helper's is, once its answer is handed over)."""
    from lembas.services import wake as wake_service

    with session_scope() as db:
        job = jobs_service.by_id(db, chat_id, job_id)
        if job is None:
            return ""
        child_id = job.child_chat_id or ""
        if not child_id or db.get(Chat, child_id) is None:
            return "gone"
        row_id, parent_id, was = job.id, job.chat_id, job.state
        wall = float(settings_store.subagents(db)["wall_seconds"])
    note = jobs_service.note({"kind": jobs_service.KIND_TO_SUBAGENT, "text": text})["text"]
    message_id = await wake_service.wake_chat(child_id, note)
    if not message_id:
        # Queued into the running reply; nothing else to do.
        return JOB_RUNNING if was == JOB_RUNNING else was
    # A reply of its own started: the job runs again under the same id.
    with session_scope() as db:
        from lembas.db.models import ChatJob

        job = db.get(ChatJob, row_id)
        if job is None:
            return ""
        job.state = JOB_RUNNING
        job.ended = None
        job.data_json = {
            **(job.data_json or {}),
            "message_id": message_id,
            "runs": int((job.data_json or {}).get("runs") or 1) + 1,
            "background": True,
            "stopping": False,
        }
        db.commit()
    _show_job(parent_id, row_id)
    if not _watching(child_id):
        _start_watch(row_id, child_id, parent_id, time.monotonic() + wall)
    return JOB_RUNNING


async def _run_job_send(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    text = str(args.get("text") or "").strip()
    if not text:
        return _error("job_send needs the text to send.", name="job_send")
    with session_scope() as db:
        job, refused = _job_for(db, context, args, "job_send")
        if refused is not None:
            return refused
        job_id, was = job.job_id, job.state
    state = await send_to_job(context.chat_id, job_id, text)
    if state == "gone":
        return _error(
            f"{job_id}'s conversation is gone (a helper that waited for its answer is "
            "removed once it has answered), so it cannot be spoken to. Start a new task.",
            name="job_send",
        )
    if was == JOB_RUNNING:
        said = f"Sent to {job_id}; it reads it at its next step."
    else:
        said = (
            f"{job_id} has been started again with your message; its new report comes "
            "to you when it ends."
        )
    return _outcome(said, {"name": "job_send", "status": "ok", "query": job_id, "text": text})


async def _run_report(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """`report`, the helper's side: a message to the main agent now, delivered
    as a `machine` turn into the parent -- taken in at its next round while it
    answers, a reply of its own while it is idle (`wake_chat`)."""
    from lembas.services import wake as wake_service

    text = str(args.get("text") or "").strip()
    if not text:
        return _error("report needs the text to send.", name="report")
    with session_scope() as db:
        chat = db.get(Chat, context.chat_id) if context.chat_id else None
        if chat is None or not chat.parent_chat_id:
            return _error("Only a subagent can report to a main agent.", name="report")
        parent_id = chat.parent_chat_id
        job = jobs_service.for_child(db, chat.id)
        facts = {
            "kind": jobs_service.KIND_MESSAGE,
            "id": job.job_id if job is not None else "",
            "agent": job.agent if job is not None else agent_of(chat),
            "description": job.description if job is not None else chat.title,
            "text": text,
        }
    await wake_service.wake_chat(parent_id, jobs_service.note(facts)["text"])
    return _outcome(
        "Sent to the main agent. Carry on with the task; an answer, if one comes, "
        "arrives as a message to you.",
        {"name": "report", "status": "ok", "query": text[:120], "text": text},
    )


def capacity_refusal(db, chat: Chat, target=None) -> str:
    """Why a helper cannot run on `target` (the chat's own model when None)
    beside this chat's model and the helpers it already has running, or ""
    when it can -- the spec's capacity rule (`loop.json`): not another model
    of a connection that holds one model at a time, not the same model when
    it serves one request at a time."""
    from lembas.services import chat as chat_service

    own = chat_service.model_row(db, chat_service.speaker_for(db, chat))
    target = target if target is not None else own
    if target is None:
        return ""
    beside: list[tuple[Any, str]] = [(own, "this conversation")]
    for job in jobs_service.running_for(db, chat.id):
        data = job.data_json or {}
        model = chat_service.model_row(
            db, chat_service.Speaker(str(data.get("model_id") or ""), data.get("connection_id"))
        )
        beside.append((model, f"the helper {job.job_id}"))
    for other, what in beside:
        if other is None or other.connection_id != target.connection_id:
            continue
        if other.model_id == target.model_id:
            if target.single_session:
                return (
                    f"{target.label} serves one request at a time, so it cannot run a "
                    f"helper beside {what}: the helper would take the server's only slot "
                    "and push the cached prompt out of it."
                )
        elif getattr(other.connection, "one_model_at_a_time", False):
            return (
                f"{target.label} and {other.label} are on a connection that holds one "
                f"model at a time, so a helper on {target.label} cannot run beside {what}."
            )
    return ""


def tool_defs() -> list[ToolDef]:
    """The helper tools an ordinary chat gets, as the harness spec defines them:
    `task` and the four job tools. `subagent_run`, the name `task` had, still
    runs through `tools.resolve_alias`."""
    from lembas.services.tools import (
        FAMILY_SUBAGENT,
        RISK_EXECUTE,
        RISK_INTERACT,
        RISK_READ,
        ToolDef,
        spec_description,
        spec_parameters,
    )

    return [
        ToolDef(
            name="task",
            family=FAMILY_SUBAGENT,
            description=spec_description("task"),
            parameters=spec_parameters("task"),
            run=_run_subagent,
            risk=RISK_INTERACT,
        ),
        ToolDef(
            name="job_list",
            family=FAMILY_SUBAGENT,
            description=spec_description("job_list"),
            parameters=spec_parameters("job_list"),
            run=_run_job_list,
            risk=RISK_READ,
        ),
        ToolDef(
            name="job_output",
            family=FAMILY_SUBAGENT,
            description=spec_description("job_output"),
            parameters=spec_parameters("job_output"),
            run=_run_job_output,
            risk=RISK_READ,
        ),
        ToolDef(
            name="job_stop",
            family=FAMILY_SUBAGENT,
            description=spec_description("job_stop"),
            parameters=spec_parameters("job_stop"),
            run=_run_job_stop,
            risk=RISK_EXECUTE,
        ),
        ToolDef(
            name="job_send",
            family=FAMILY_SUBAGENT,
            description=spec_description("job_send"),
            parameters=spec_parameters("job_send"),
            run=_run_job_send,
            risk=RISK_INTERACT,
        ),
    ]


def child_tool_defs() -> list[ToolDef]:
    """What a helper's own chat gets of the family: `report`, and nothing else
    (spec 2.0.0, `offered: subagent`), in its own family `subagent:report`
    (`tools.FAMILY_REPORT_BACK`)."""
    from lembas.services.tools import (
        FAMILY_REPORT_BACK,
        RISK_INTERACT,
        ToolDef,
        spec_description,
        spec_parameters,
    )

    return [
        ToolDef(
            name="report",
            family=FAMILY_REPORT_BACK,
            description=spec_description("report"),
            parameters=spec_parameters("report"),
            run=_run_report,
            risk=RISK_INTERACT,
        ),
    ]


def all_tool_defs() -> list[ToolDef]:
    """Every tool of this module, for the ungated registry."""
    return [*tool_defs(), *child_tool_defs()]


async def stop_helpers_of(chat_id: str) -> int:
    """Stop the helpers a chat's reply is waiting on -- the synchronous ones.
    Its background jobs run on: the parent's Stop ends the parent's turn, and a
    background job was never part of it. Returns how many were told."""
    with session_scope() as db:
        waiting = [
            (j.job_id, j.child_chat_id)
            for j in jobs_service.running_for(db, chat_id)
            if not (j.data_json or {}).get("background", True)
        ]
    for _job_id, child_id in waiting:
        if child_id:
            with contextlib.suppress(Exception):
                await _stop(child_id)
    return len(waiting)


__all__ = [
    "AGENTS",
    "AGENT_EXPLORE",
    "AGENT_GENERAL",
    "MODE_READING",
    "MODE_WRITING",
    "READ_ONLY_SENTENCE",
    "REPORT_SENTENCE",
    "SAFE_COMMANDS",
    "agent_of",
    "all_tool_defs",
    "capacity_refusal",
    "child_tool_defs",
    "clear",
    "helper_models",
    "live_count",
    "model_ref",
    "send_to_job",
    "stop_helpers_of",
    "stop_job",
    "tool_defs",
]
