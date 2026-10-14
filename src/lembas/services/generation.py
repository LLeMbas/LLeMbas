"""Background reply generation.

Generation used to be driven by the SSE request: the browser opening the stream
was what produced the tokens, so navigating away cancelled the reply mid-
sentence. Here it runs as its own task instead, and the SSE endpoint merely
*follows* it. Closing the page, opening another chat, or starting a new one
leaves the answer being written; coming back attaches to it and immediately
receives everything produced so far.

The registry is in-process, which is right for the single-worker deployment
this ships with. Several workers would need the state in the database or a
broker, because the request that follows a generation would not necessarily
land in the process running it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from lembas.db.models import KIND_AGENT, ROLE_ASSISTANT, ROLE_USER, Chat, Message, User
from lembas.db.session import session_scope
from lembas.services import canvas as canvas_service
from lembas.services import chat as chat_service
from lembas.services import compaction as compaction_service
from lembas.services import data_groups, interaction, settings_store, tokens, tool_labels
from lembas.services import metrics as metrics_service
from lembas.services import prompts as prompts_service
from lembas.services import push as push_service
from lembas.services import tools as tools_service
from lembas.services import usage as usage_service
from lembas.services.llm.openai_client import (
    LLMError,
    assistant_blocks,
    chunk_usage,
    delta_reasoning,
    delta_text,
    delta_tool_calls,
    stream_chat,
)
from lembas.services.reasoning import REASONING, ReasoningSplitter
from lembas.services.tools import ToolOutcome
from lembas.web import i18n

log = logging.getLogger(__name__)

# How often the partial answer is offered to followers. Markdown is re-rendered
# whole each time -- a list or a code fence is only correct once its context
# exists -- so this trades a little work for formatting that appears as the
# model writes. 100ms is below the threshold where the eye reads it as stepping.
RENDER_INTERVAL = 0.1

# Message.origin of a device reply taken up again after a shutdown: it
# follows the device's turn instead of sending its prompt a second time.
ORIGIN_ATTACH = "attach"
# Message.origin of the turn that carries an ordinary reply on after a shutdown.
ORIGIN_RESUME = "resume"
# How old an interrupted reply may be and still be taken up again.
RESUME_WITHIN = timedelta(hours=12)

# Finished generations linger so a follower attaching at the last moment still
# gets the final frames, then are pruned.
KEEP_FINISHED = timedelta(minutes=5)

# What "no ceiling" resolves to. A setting of 0 means an administrator does not
# want a round limit, but a loop needs *some* stop or a model stuck calling one
# cheap tool runs until the process does. This is high enough never to be
# reached by anything but that.
MAX_TOOL_ROUNDS = 200

# How much of the window a request may occupy before the next round is refused.
# A tool round appends an assistant turn and a tool turn per call, so a reply
# that keeps calling tools grows its own request until the endpoint refuses it --
# and `_maybe_compact` runs once, before the first round, so nothing was watching
# it after that.
#
# The tenth left over is room to answer in. Stopping with an explanation beats an
# upstream error that says only that the request was too long.
CONTEXT_HEADROOM = 0.9


@dataclass
class Generation:
    """The live state of one reply being written."""

    chat_id: str
    message_id: str

    content: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    reasoning_ms: int = 0
    # Milliseconds spent thinking, summed over rounds. Distinct from
    # `reasoning_ms`, which is the reply's *first* burst and is written once --
    # right for "Thought for 8 seconds" on a single-round answer, and unable to
    # say anything about round seven of forty. Stamped on each mark by
    # `close_step` and diffed by services/steps.py into a per-block figure.
    thinking_ms: int = 0
    # How long the round *currently* running has been thinking. Read by the live
    # block's label, and written by the producer rather than computed from a
    # start time by the follower: a model that has stopped thinking and moved on
    # to a tool should show a settled number, not a clock that keeps running.
    round_thinking_ms: int = 0

    # One entry per tool call made while producing this reply, in order. Shown
    # live as the model works and kept on the message afterwards.
    tool_events: list[dict] = field(default_factory=list)

    # Where each round's contribution ended, so the three stores above can be
    # rendered as the one sequence they were. Written by `close_step`, and
    # marks rather than copies -- see services/steps.py.
    steps: list[dict] = field(default_factory=list)

    # --- What it cost --------------------------------------------------------
    # Prompt and completion are summed across tool rounds: what the reply cost.
    # context_tokens is overwritten each round with that round's prompt plus
    # completion, because a three-round reply pays for its prompt three times
    # but only ever occupies the window once.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Of `prompt_tokens`, how many the server answered from its cache; priced
    # apart by credits.
    cached_tokens: int = 0
    # Of `prompt_tokens`, how many were written to the cache (Anthropic),
    # which credits price above ordinary input.
    cache_write_tokens: int = 0
    context_tokens: int = 0
    context_limit: int = 0
    # Filled from the assembled request before the first chunk, so a follower
    # has a percentage to show while the reply is still being written -- real
    # usage only arrives in a single chunk at the very end.
    prompt_estimate: int = 0
    # Every round's estimate added up, against `prompt_estimate` being only the
    # latest. The two answer different questions and both are wanted: what the
    # reply *cost* is the sum, because a three-round reply pays for its prompt
    # three times; what it *occupies* is the last one. That is exactly the split
    # the reported figures already use between `prompt_tokens` and
    # `context_tokens`, so the fallback mirrors it rather than inventing a
    # second convention.
    prompt_estimate_total: int = 0
    # Whether any round's usage block ever arrived. The one fact that decides
    # whether these numbers are counted or worked out, recorded where it is
    # known instead of inferred downstream from "are both counts non-zero?" --
    # which the end-of-reply fallback makes true of a reply nobody counted, so
    # the `~` vanished at exactly the moment everything became an estimate.
    reported_usage: bool = False
    # How many characters of text and reasoning had been written when that usage
    # block arrived. What is written past it is this round's, uncounted until the
    # round ends -- so it is the gap the metrics interpolate across, and it is
    # what keeps the counts moving between one usage chunk and the next instead
    # of standing still for a whole round.
    counted_chars: int = 0
    rounds: int = 0
    # time.monotonic() at the start. A field rather than a local in `_run`
    # because `_follow` is a different function that sees only this object, and
    # without it there is nothing to compute a live tokens/second against.
    started_at: float = 0.0
    elapsed_ms: int = 0

    error: str = ""
    stopped: bool = False
    done: bool = False

    # Bumped on every change. Followers compare against it rather than being
    # woken individually: with a 100ms cadence a short poll is simpler than
    # future bookkeeping, and cannot drop a wakeup.
    version: int = 0
    # What the reply is doing when it is not producing tokens. Shown in the
    # streaming bubble, because a silent multi-second pause before the first
    # token is what a hang looks like.
    status: str = ""
    # Number of browsers currently watching. Decides whether a finished reply
    # counts as unread.
    followers: int = 0
    finished_at: datetime | None = None
    cancel: bool = False

    # Every card the reply is stopped on -- an approval, or a question the model
    # asked. Empty at every other moment. Read by `_follow`, which sends the
    # cards, and by `request_stop`, which resolves them: `cancel` is otherwise
    # only ever read between streamed chunks, and there are none while one is
    # open. A list, because a linked device can ask several things at
    # once -- two approvals from two tool calls running together, a question
    # beside one -- and each is its own card with its own answer. Read the first
    # through `pending`, as everything did when there could only be one.
    waiting: list[interaction.Interruption] = field(default_factory=list)
    # A plan proposed in Plan mode, or one being kept current while it is
    # carried out. See services/plans.py for the shape. Written onto the
    # message, so the Execute button sends exactly what was proposed rather than
    # something parsed back out of prose.
    plan: dict | None = None
    # Whether that plan came from `plan_submit`, which ends the turn, rather
    # than from `plan_update`, which does not. Both write `plan` so that
    # `_persist` stays one writer with one rule; only this decides whether the
    # tools are withdrawn for a final round.
    plan_final: bool = False
    # Which files this reply has put in the canvas panel. Seeded once from
    # `chat.canvas_json` where `_run` already has the chat loaded, then mutated
    # in place -- two `file_read` calls in one round that each re-read the row
    # would leave only the second, which is the lost update `plan` above
    # documents. Folded back by `_persist`, the single writer.
    canvas: dict = field(default_factory=dict)
    # The queue, seen from the reply's side. `drained` says this reply's ending
    # handed the next waiting prompt to a fresh one; `injected_ids` names the
    # prompts taken into *this* reply between two rounds of tool calls. Both are
    # read only by `_follow`, which turns them into bubbles on the `done` frame
    # -- the one frame that reaches a browser after a reply is over.
    drained: bool = False
    injected_ids: list[str] = field(default_factory=list)
    # A device reply's messages handed to the device but not yet taken in by its
    # model, id -> text: the `steered` event names them, and only then
    # are they marked into the reply, at the point they went in.
    sent_steers: dict[str, str] = field(default_factory=dict)
    # A device reply's tool calls started and not yet ended, id -> arguments:
    # kept until the call ends, for the block it is drawn as.
    device_args: dict[str, object] = field(default_factory=dict)
    # Characters of reply and thinking a device has sent, against
    # `device_link.MAX_REPLY_CHARS`.
    device_chars: int = 0
    # Images this reply produced, waiting to be bound to its message row. The
    # runner writes the file and the `Attachment`; only `_persist` may say which
    # turn it belongs to, which is the same division of labour `canvas` above
    # follows and for the same reason.
    attachment_ids: list[str] = field(default_factory=list)
    # How many times *in a row* this reply has ended with plan tasks still open
    # and been told to carry on. Reset the moment it calls a tool again, so the
    # count is of consecutive stops rather than of stops in total.
    nudges: int = 0
    # How many helpers this reply has spawned. Here rather than keyed on the
    # chat because this object is the only one that knows what "this reply"
    # means -- a chat-keyed counter would need resetting, and every candidate
    # for doing the resetting is a place to forget. Read and incremented with
    # nothing awaited in between, which is what makes it safe against the four
    # calls a round runs together. See services/subagent.py:_budget.
    subagents: int = 0
    # A tool this reply must call, set by `/image` and by nothing else. It goes
    # into the *first* request only -- `_run` rebuilds the payload's messages
    # per round but keeps this body, and `tool_choice` left in place would make
    # every later round call the tool again, which is a loop rather than a
    # command. Cleared once the first round has gone out.
    force_tool: str = ""
    # A device chat: where its tools ran, for the transcript, and
    # whether the chat had turns before the device's session was opened.
    device_where: str = ""
    earlier_turns: bool = False
    # This round's thinking interval on a device reply, widened as its
    # reasoning arrives and closed by what follows -- `_thought_at`'s span, kept
    # here because the device's events reach `apply_event` one at a time.
    device_think_span: tuple[float, float] | None = None
    # The todo list as this reply last set it; None while it has not
    # touched it. Folded onto the chat by `_persist`, streamed to the panel live.
    todos: list | None = None
    # A device reply's turn has ended on the device (its `task` end): later
    # events of that session are a new turn, not more of this reply.
    device_turn_over: bool = False
    # The device's id for the turn this reply writes (capability
    # `turns`): the person's message id for a turn sent from here, the
    # device's own for one typed in its terminal. Events are routed to the
    # reply by it rather than by which reply happens to be writing, and
    # `turn_started` says the device's `task` start for it arrived -- a prompt
    # sent again that the device had already finished answers with no events.
    turn_id: str = ""
    turn_started: bool = False
    # Cut off by a shutdown, not stopped by anybody: written on the row,
    # and taken up again when the instance is back.
    interrupted: bool = False

    def touch(self) -> None:
        self.version += 1

    def close_step(self) -> None:
        """End the step being written. Everything appended from here is the next.

        Called where a round's contribution ends and nowhere else, so the list
        stays append-only and an index into it means the same step for ever --
        which is what the transcript's DOM ids are built from, and therefore
        what lets a block somebody opened survive both a stream frame and the
        `done` frame that replaces the whole bubble.

        There is deliberately no closing mark at the end of a reply. The
        trailing step is implicit in both the live path and the stored one, and
        one rule is one thing to get right.
        """
        self.steps.append(
            {
                "round": self.rounds,
                "thinking_to": len(self.thinking),
                "text_to": len(self.text),
                "tools_to": len(self.tool_events),
                # Cumulative, like the three above it, and diffed the same way.
                "thinking_ms": self.thinking_ms,
            }
        )

    def mark_steer(self, entries: list[dict]) -> None:
        """What the person added, taken in here: the step so far ends,
        and the mark says what went in, so the transcript shows it at this point
        -- live, and the same after a reload."""
        entries = [e for e in entries if str(e.get("text") or "").strip()]
        if not entries:
            return
        self.close_step()
        self.steps[-1]["steer"] = [
            {"id": str(e.get("id") or ""), "text": str(e.get("text") or "")} for e in entries
        ]
        self.touch()

    @property
    def pending(self) -> interaction.Interruption | None:
        """The first card open, or None."""
        return self.waiting[0] if self.waiting else None

    @pending.setter
    def pending(self, value: interaction.Interruption | None) -> None:
        self.waiting = [value] if value is not None else []

    def card(self, interaction_id: str) -> interaction.Interruption | None:
        """The open card with this id."""
        return next((w for w in self.waiting if w.id == interaction_id), None)

    @property
    def text(self) -> str:
        return "".join(self.content)

    @property
    def thinking(self) -> str:
        return "".join(self.reasoning)


_RUNNING: dict[str, Generation] = {}
_TASKS: dict[str, asyncio.Task] = {}


def get(message_id: str) -> Generation | None:
    return _RUNNING.get(message_id)


def request_stop(message_id: str) -> bool:
    """Ask a running generation to stop. Returns whether one was found."""
    generation = _RUNNING.get(message_id)
    if generation is None or generation.done:
        return False
    generation.cancel = True
    # A paused reply produces no chunks, and the chunk loop is the only place
    # `cancel` is ever read -- so without this, Stop does nothing at all while
    # an approval card is on screen. Resolving the pause is the wakeup; `_run`
    # then takes its ordinary stopped path rather than needing a second branch.
    for card in list(generation.waiting):
        card.resolve(interaction.CANCELLED)
    return True


def answer(
    chat_id: str,
    interaction_id: str,
    *,
    verdict: str = "",
    answers: dict[str, str] | None = None,
    reason: str = "",
    picks: dict[str, list[str]] | None = None,
) -> bool:
    """Resolve whichever running reply is parked on this interruption.

    A linear scan of the registry: it holds one entry per reply in flight, and
    this runs at human speed. Scoped to the chat because the caller has already
    checked that this reader owns *that* chat, and an id alone would not.
    """
    for generation in _RUNNING.values():
        pending = generation.card(interaction_id) if generation.chat_id == chat_id else None
        if pending is None:
            continue
        outcome = verdict if verdict in _VERDICTS else interaction.ANSWER
        return pending.resolve(outcome, answers=answers, reason=reason, picks=picks)
    return False


def pending_items(chat_id: str, interaction_id: str) -> tuple[interaction.Item, ...]:
    """What the card this chat is waiting on is asking about.

    For the route that has to record what "always" meant. It must be read
    *before* the pause is resolved: `interaction.wait_for` clears
    `generation.pending` in its `finally`, so a moment later there is nothing
    left to read and "always" would silently remember nothing.

    Empty when there is no such pause -- already answered, timed out, or the
    server restarted -- which is the same answer `answer` gives, and means a
    stale card records nothing rather than half of something.
    """
    for generation in _RUNNING.values():
        pending = generation.card(interaction_id) if generation.chat_id == chat_id else None
        if pending is None:
            continue
        return pending.items
    return ()


def running_for(chat_id: str) -> Generation | None:
    """The reply being written in this chat, if there is one.

    A linear scan for the reason `answer` gives above: one entry per reply in
    flight, consulted at human speed. `_prune` first, because a finished
    generation lingers `KEEP_FINISHED` so that late followers still get the
    final frames -- and without the sweep those five minutes would look like a
    chat that is permanently busy, and queue everything typed into it.
    """
    _prune()
    for generation in _RUNNING.values():
        if generation.chat_id == chat_id and not generation.done:
            return generation
    return None


_VERDICTS = (interaction.ALLOW, interaction.ALLOW_ALWAYS, interaction.DENY)


def _prune() -> None:
    cutoff = datetime.now(UTC) - KEEP_FINISHED
    now = time.monotonic()
    for message_id, generation in list(_RUNNING.items()):
        # A paused reply is deliberately not `done` -- a page reload has to be
        # able to reattach to it. Its timeout is what stops it lingering, and
        # this is the belt to that pair of braces: a deadline long past means
        # the timeout did not fire, and a task parked forever is worse than one
        # that gives up.
        for pending in list(generation.waiting):
            if now > pending.expires_at + KEEP_FINISHED.total_seconds():
                log.warning("resolving a stuck interaction on message %s", message_id)
                pending.resolve(interaction.EXPIRED)
        if generation.done and generation.finished_at and generation.finished_at < cutoff:
            _RUNNING.pop(message_id, None)
            _TASKS.pop(message_id, None)


def ensure(chat_id: str, message_id: str, *, force_tool: str = "") -> Generation:
    """Start generating this reply if it is not already under way.

    Idempotent, because more than one thing can ask for it: the route that
    created the message, and any page load that finds the message unfinished.

    `_prune` runs first, not after the lookup. Below it, a stale entry could
    never expire: the early return is the only path a repeated id takes, so the
    sweep was unreachable for exactly the message that needed it.
    """
    _prune()
    existing = _RUNNING.get(message_id)
    if existing is not None:
        return existing

    generation = Generation(chat_id=chat_id, message_id=message_id, force_tool=force_tool)
    _RUNNING[message_id] = generation
    _TASKS[message_id] = asyncio.create_task(_run(generation))
    return generation


def restart(chat_id: str, message_id: str) -> Generation:
    """Produce this reply again, discarding any finished attempt at it.

    `ensure` is idempotent on purpose, and that is load-bearing: a page load
    finding an unfinished reply must attach to it rather than start a second
    one, and `_follow` calls it too. Regeneration is the one caller that means
    the opposite.

    It is also the one caller that reuses an existing Message row -- blanked and
    marked incomplete -- rather than creating a new one. The finished Generation
    for that id is still in the registry, because finished ones linger
    KEEP_FINISHED so a follower arriving at the last moment still gets the final
    frames. `ensure` handed that one straight back: no request was made,
    `_follow` replayed the previous answer, and the `done` frame re-rendered a
    streaming shell because the row said incomplete. That was the reconnect loop,
    and the Send button stuck on Stop.
    """
    previous = _RUNNING.pop(message_id, None)
    task = _TASKS.pop(message_id, None)
    if previous is not None and not previous.done:
        previous.cancel = True
        if task is not None:
            task.cancel()
    return ensure(chat_id, message_id)


async def resume_interrupted(delay: float = 20.0) -> int:
    """Take up again the replies the last shutdown cut off: an update
    waits for the instance to be idle, but a restart by hand does not, and a
    reply should outlast either.

    A device chat's reply follows the device again (it went on working while
    this was down) -- after `delay`, so the device has dialled back in. Any other
    reply carries on from what it had written, as a turn of its own marked
    `resume`. Older than RESUME_WITHIN is left as it is: nobody is waiting.

    A device reply left unfinished by a crash (kill -9, out of memory) was never
    marked interrupted, and a page opened meanwhile would have started it as a
    new turn -- the same prompt sent to the device twice. So before the
    wait, every such row is marked to follow the device instead."""
    try:
        with session_scope() as db:
            since = datetime.now(UTC) - RESUME_WITHIN
            for row in db.scalars(
                select(Message)
                .join(Chat, Chat.id == Message.chat_id)
                .where(
                    Message.role == ROLE_ASSISTANT,
                    Message.complete.is_(False),
                    Message.created_at >= since,
                    Chat.device_token_id.is_not(None),
                    Chat.device_token_id != "",
                )
            ):
                row.origin = ORIGIN_ATTACH
                row.interrupted = True
            db.commit()
    except Exception:  # noqa: BLE001 - never stops the instance from starting
        log.exception("could not mark unfinished device replies")
    await asyncio.sleep(delay)
    started: list[tuple[str, str]] = []
    try:
        with session_scope() as db:
            since = datetime.now(UTC) - RESUME_WITHIN
            rows = list(
                db.scalars(
                    select(Message).where(
                        Message.interrupted.is_(True), Message.created_at >= since
                    )
                )
            )
            for row in rows:
                row.interrupted = False
                chat = db.get(Chat, row.chat_id)
                if chat is None or running_for(chat.id) is not None:
                    continue
                if chat.device_token_id:
                    row.complete = False
                    row.stopped = False
                    row.origin = ORIGIN_ATTACH
                    started.append((chat.id, row.id))
                    continue
                from lembas.services import harness_spec

                carry_on = harness_spec.prompt("tasks/continue.md")
                chat_service.create_message(db, chat, ROLE_USER, carry_on, origin=ORIGIN_RESUME)
                reply = chat_service.create_message(
                    db,
                    chat,
                    ROLE_ASSISTANT,
                    "",
                    complete_=False,
                    model_id=row.model_id or chat.model_id,
                )
                started.append((chat.id, reply.id))
            db.commit()
    except Exception:  # noqa: BLE001 - never stops the instance from starting
        log.exception("could not take up interrupted replies")
        return 0
    for chat_id, message_id in started:
        ensure(chat_id, message_id)
    if started:
        log.info("took up %s interrupted repl%s", len(started), "y" if len(started) == 1 else "ies")
    return len(started)


async def shutdown() -> None:
    """Stop every running generation, keeping what each has produced."""
    for task in list(_TASKS.values()):
        task.cancel()
    for task in list(_TASKS.values()):
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


# --- A reasoning effort the model will not take ------------------------------
#
# `chat_template_kwargs.reasoning_effort` is not advisory. It reaches the
# model's Jinja chat template, and a template that does not know the value does
# not ignore it -- gpt-oss and Bonsai both call `raise_exception`, which fails
# the whole request. The reader sees their reply die with a Jinja traceback in
# it, having chosen a perfectly ordinary-looking option from a menu this
# application drew.
#
# So the value is checked against the model's own vocabulary before it is sent
# (`chat.apply_effort`), and this is the second line: when it is refused anyway
# -- an endpoint upgraded underneath us, a model whose list nobody has set --
# the reply is retried once without it rather than lost, and the model's list is
# narrowed so the menu stops offering something that does not work.


def _effort_was_refused(message: str) -> bool:
    """Whether this error is the chat template refusing the effort we sent.

    Deliberately narrow. Anything that merely mentions reasoning would also
    match a model politely declining to think, and retrying *that* silently
    would hide a real failure behind a second request.
    """
    lowered = message.lower()
    return "effort" in lowered and ("unexpected" in lowered or "supported" in lowered)


def _advertised_efforts(message: str) -> list[str]:
    """The efforts an error message says it will take, if it says.

    Bonsai's is "Unexpected reasoning effort high. Supported types are xhigh
    (default), medium, and low." -- which is the answer, written out, in the
    failure. Read only from the part after "supported", so the *rejected* value
    named in the first sentence is not collected as a supported one.

    Best-effort by design: it only ever narrows what is offered, an
    administrator can set the list by hand, and anything unrecognised is
    dropped by `efforts_for` on the way out.
    """
    lowered = message.lower()
    if "supported" not in lowered:
        return []
    tail = lowered.split("supported", 1)[1]
    # Whole words. `"high" in "xhigh"` is true, so a substring test reads
    # Bonsai's "Supported types are xhigh (default), medium, and low" as
    # advertising `high` -- the very value it has just refused -- and the list
    # would learn the opposite of what the endpoint said.
    words = set(re.findall(r"[a-z]+", tail))
    return [effort for effort in chat_service.EFFORTS if effort in words]


def _learn_refused_effort(model_id: str, refused: str, message: str) -> None:
    """Write what the endpoint just taught us onto the model.

    Its own session: this runs from inside a generation, which outlives the
    request's session, and the whole point is that it survives to the next turn.
    """
    from lembas.db.models import Model

    if not model_id:
        return
    try:
        with session_scope() as db:
            models = list(db.scalars(select(Model).where(Model.model_id == model_id)))
            for model in models:
                advertised = _advertised_efforts(message)
                current = list(model.reasoning_efforts or chat_service.DEFAULT_EFFORTS)
                # What the endpoint advertised, when it did; otherwise simply
                # the list it had, minus the one it has just refused.
                wanted = advertised or [e for e in current if e != refused]
                wanted = [e for e in wanted if e in chat_service.EFFORTS and e != refused]
                if wanted and wanted != list(model.reasoning_efforts or []):
                    model.reasoning_efforts = wanted
                    log.info(
                        "model %s refused reasoning effort %r; efforts narrowed to %s",
                        model_id, refused, wanted,
                    )
    except Exception:  # noqa: BLE001 - never let bookkeeping fail a reply
        log.exception("could not record the refused effort for model %s", model_id)


async def _stream_once(endpoint, payload, generation, model_id: str):
    """`stream_chat`, retried once without the reasoning effort if that is what
    the endpoint objected to.

    ⚠ The retry is only safe because the template is rendered *before* any token
    is produced, so a refusal arrives with nothing yet emitted. `sent` is the
    guard that keeps it that way: once a single chunk has reached the caller,
    the reply is under way and a second request would duplicate it.
    """
    sent = False
    try:
        async for chunk in stream_chat(endpoint, payload):
            sent = True
            yield chunk
        return
    except LLMError as exc:
        refused = str((payload.get("chat_template_kwargs") or {}).get("reasoning_effort") or "")
        if sent or not refused or not _effort_was_refused(exc.message):
            raise
        log.info("retrying without reasoning effort %r: %s", refused, exc.message)
        _learn_refused_effort(model_id, refused, exc.message)

    retry = dict(payload)
    retry.pop("reasoning_effort", None)
    kwargs = dict(retry.get("chat_template_kwargs") or {})
    kwargs.pop("reasoning_effort", None)
    if kwargs:
        retry["chat_template_kwargs"] = kwargs
    else:
        retry.pop("chat_template_kwargs", None)

    async for chunk in stream_chat(endpoint, retry):
        yield chunk


async def _run(generation: Generation) -> None:
    """Produce one reply, then persist it. Never raises into the task.

    A reply is not necessarily one request. When tools are offered and the
    model asks to use one, the loop below runs it, appends the result to the
    conversation and asks again -- up to the round budget, after which the
    model has to answer with what it has. Text produced before a tool
    call is kept, so a model that narrates what it is about to look up does not
    lose that when the results come back.
    """
    splitter = ReasoningSplitter()
    started = time.monotonic()
    generation.started_at = started
    reasoning_started: float | None = None
    question = ""
    endpoint = model_id = None
    needs_title = False
    title_from_prompt = False
    title_prompt = ""
    device: dict | None = None
    # Bound before the try, because the finally clears the credential on it and
    # a chat that has been deleted returns before it would otherwise be set.
    tool_context = None

    try:
        # Before the request is assembled, so build_request is called once and
        # what goes out is the compacted conversation -- there is no second
        # assembly path. Here rather than in post_message because that route's
        # whole contract is to return immediately, and a three-second
        # summarisation in front of it would break exactly that.
        # An update waiting for the instance to go quiet holds new replies;
        # the ones already running finish.
        from lembas.services import updates_auto

        if held := updates_auto.refusing():
            generation.error = held
            return

        # A chat on a linked device is written by the device, not by
        # this loop: its harness, its model, its context and its limits -- so no
        # compaction here and no project listing. The finally persists the reply
        # as for any other.
        with session_scope() as db:
            chat = db.get(Chat, generation.chat_id)
            message = db.get(Message, generation.message_id)
            device = _device_turn(db, chat, message, generation) if chat and message else None
            if device is not None:
                from lembas.services import device_link

                # Every turn, not only the chat's first: the switch,
                # the permission and the token are asked again here, which
                # covers sending, Continue, regenerate, a resumed reply and a
                # queued message alike.
                refused = device_link.refusal(db, db.get(User, chat.user_id), chat.device_token_id)
                if refused:
                    generation.error = refused
                    return
                needs_title = not chat.title_generated
                question = device["prompt"]
                title_from_prompt = True
        if device is not None:
            from lembas.services import device_link

            await device_link.run(generation, **device)
            return

        await _maybe_compact(generation)

        with session_scope() as db:
            chat = db.get(Chat, generation.chat_id)
            message = db.get(Message, generation.message_id)
            if chat is None or message is None:
                generation.error = i18n.t("That chat no longer exists.")
                return

            # Who is answering, from the row being written into rather than
            # from the chat. The row is durable and this generation is not: a
            # restart turns `_follow` into `ensure`, which starts a brand new
            # `_run` against the same message, and everything the request depends
            # on has to survive that. It is also the only thing that can make the
            # bubble's avatar and the model actually asked agree.
            speaker = chat_service.speaker_for(db, chat, message)
            owner = db.get(User, chat.user_id)
            # Before the endpoint is even resolved: a chat whose model has since
            # been moved into another data group must not be sent to it, or that
            # provider is handed the whole history the group was keeping from it.
            moved = data_groups.refusal(db, owner, chat, speaker)
            if moved:
                generation.error = moved
                return
            endpoint, model_id = chat_service.resolve_endpoint(db, chat, speaker)

            # Before the request is built, not while it streams. Every other
            # budget here can only be noticed part way through and so ends with
            # `_wrap_up` asking for a final answer; this one is knowable in
            # advance, and a reply that trails off because a month ran out mid
            # sentence would be the failure `_wrap_up` exists to prevent.
            over = usage_service.over_token_budget(db, owner)
            if over:
                generation.error = over
                return
            # And credits, when the instance uses them: a positive
            # balance and every plan window under its limit, for a model that
            # costs anything. Same place, same reason.
            from lembas.services import credits as credits_service

            short = credits_service.refusal(
                db, owner, chat_service.model_row(db, speaker or chat_service.speaker_for(db, chat))
            )
            if short:
                generation.error = short
                return

            # Read while the session is open: everything below outlives it.
            # Resolved once, so that what the loop is allowed to *run* is the
            # same set the endpoint was *offered* -- not whatever happens to
            # exist by the time a call comes back.
            toolset = tools_service.resolve_tools(db, chat, owner, speaker)
            offered = toolset.schemas
            payload = chat_service.build_request(
                db,
                chat,
                upto=message,
                tools=offered,
                user=owner,
                force_tool=generation.force_tool,
                speaker=speaker,
            )
            question = _question_from(payload)
            needs_title = not chat.title_generated
            # An agent chat is titled from its opening words and never costs a
            # model call for it. That prompt is a good title already -- somebody
            # starting one states an objective, not a topic -- while an ordinary
            # chat opens with a question, whose answer is what makes a title
            # worth asking for. Read here with the rest, because titling happens
            # after this session has closed.
            # A temporary chat is titled the same way: it should leave as little
            # trace as it can, and that includes a model call made only to name a
            # conversation nobody will see listed.
            title_from_prompt = chat.kind == KIND_AGENT or chat_service.read_only_temporary(chat)
            # Seeded once, here, where the chat is already loaded. Mutated from
            # then on; see the field's own note.
            generation.canvas = {
                "tabs": list((chat.canvas_json or {}).get("tabs") or []),
                "active": (chat.canvas_json or {}).get("active") or "",
            }
            # Read here, with the rest, because titling happens after this
            # session has closed and must not open another one.
            title_prompt = prompts_service.resolve(db, "task.title")
            tool_context = tools_service.context_for(
                db, owner, chat, tools=toolset, speaker=speaker
            )

            # The answering model's window, not the chat's. `_too_big` is the one
            # budget that stops a reply dead rather than asking it to wrap up, so
            # judging a small model's request against a large model's ceiling is
            # how a reply fails with no explanation in it.
            model = chat_service.model_row(db, speaker)
            generation.context_limit = model.context_length if model is not None else 0
            # Kept for `_inject`, which builds a user turn after this session
            # has closed. A turn taken in mid-reply has to be shaped exactly as
            # the same words typed a moment later would have been -- images to a
            # vision model, a plain string to anything else, or the endpoint
            # rejects the whole request.
            vision = chat_service.model_supports(db, chat, "vision", speaker=speaker)
            chat_rounds = settings_store.chat_rounds(db)
            # A helper's chat is bounded by its own number, not the instance's.
            # Only reached in an *ordinary* helper chat -- an agent one runs on
            # a linked device, which sets its own limits. Without this the round
            # ceiling on a helper is whatever an ordinary chat has, which by
            # default is none at all, and the only thing left holding it is the
            # parent's wall clock.
            if chat.parent_chat_id:
                chat_rounds = int(settings_store.subagents(db)["max_rounds"])

        # A ceiling, not a schedule -- the loop below ends the moment a round
        # produces no tool calls, which is the model saying it is done. Zero
        # means no ceiling of the instance's own, and MAX_TOOL_ROUNDS is then
        # the runaway backstop. An agent chat's work runs on its device, which
        # sets its own limits.
        budget = chat_rounds or MAX_TOOL_ROUNDS

        # Set once a budget has run out, holding the last round open with the
        # tools withdrawn so the reply ends in an answer rather than in silence.
        # See `_wrap_up`. `budget + 2` rather than `+ 1` is that extra round:
        # the iteration at `budget` is where the overrun is noticed, and the one
        # after it is where the model gets to say what it found.
        wrapping_up = False

        for round_number in range(budget + 2):
            generation.rounds = round_number + 1

            # Recomputed every round, against once before the loop. The request
            # grows by an assistant turn and a tool turn per call each time, so
            # a single estimate taken up front described the first round and
            # nothing after it -- and for the endpoints that send no usage block
            # at all (llama.cpp, Ollama and friends) that estimate *is* the
            # figure everything downstream reports. A forty-round reply showed
            # the first round's prompt as the whole reply's.
            generation.prompt_estimate = tokens.estimate_request(payload)
            generation.prompt_estimate_total += generation.prompt_estimate

            # Before spending a request that cannot fit. Outside the agent
            # branch below on purpose: an ordinary chat with a round budget can
            # fill a small window too, and `context_limit` is what decides,
            # not what kind of chat it is.
            # The one budget that still stops dead rather than asking for a final
            # answer. Every other one can afford one more request; this one is
            # the finding that there is no room for a request, and a wrap-up
            # round would be the same overflow with an upstream error instead of
            # an explanation.
            if round_number and _too_big(generation):
                _gave_up(generation, "with no room left in the context window")
                break

            accumulator = tools_service.ToolCallAccumulator()
            # Text the model produced in *this* round, needed separately from
            # generation.content when echoing the assistant turn back.
            round_text: list[str] = []
            # When this round's thinking started and when it was last seen, so
            # the interval can be added to `generation.thinking_ms` at the
            # round's end. Per round, because the thinking block is per round:
            # `reasoning_ms` is the whole reply's first burst, written once, and
            # cannot say how long round seven thought for. `None` until the
            # round thinks at all -- plenty of rounds do not.
            round_thinking: tuple[float, float] | None = None
            # The round in the endpoint's own words, when it sends them
            # (Anthropic's thinking blocks with their signatures, which a tool
            # loop has to hand back unchanged with the calls they led to).
            round_blocks: list[dict] = []

            async for chunk in _stream_once(endpoint, payload, generation, model_id):
                if native := assistant_blocks(chunk):
                    round_blocks = native
                counts = chunk_usage(chunk)
                if counts is not None:
                    generation.reported_usage = True
                    generation.prompt_tokens += counts.get("prompt_tokens", 0)
                    generation.completion_tokens += counts.get("completion_tokens", 0)
                    generation.cached_tokens += counts.get("cached_tokens", 0)
                    generation.cache_write_tokens += counts.get("cache_write_tokens", 0)
                    # Overwritten, not summed: this round's prompt already
                    # contains every earlier round.
                    generation.context_tokens = counts.get("prompt_tokens", 0) + counts.get(
                        "completion_tokens", 0
                    )
                    generation.touch()

                thought = delta_reasoning(chunk)
                if thought:
                    if reasoning_started is None:
                        reasoning_started = time.monotonic()
                    round_thinking = _thought_at(generation, round_thinking)
                    generation.reasoning.append(thought)
                    generation.touch()

                if offered:
                    fragments = delta_tool_calls(chunk)
                    if fragments:
                        accumulator.feed(fragments)

                text = delta_text(chunk)
                if text:
                    for kind, piece in splitter.feed(text):
                        if kind == REASONING:
                            if reasoning_started is None:
                                reasoning_started = time.monotonic()
                            round_thinking = _thought_at(generation, round_thinking)
                            generation.reasoning.append(piece)
                        else:
                            if reasoning_started is not None and not generation.reasoning_ms:
                                generation.reasoning_ms = int(
                                    (time.monotonic() - reasoning_started) * 1000
                                )
                            generation.content.append(piece)
                            round_text.append(piece)
                    generation.touch()

                if generation.cancel:
                    generation.stopped = True
                    break

                # Let followers and other tasks run between chunks.
                await asyncio.sleep(0)

            # The round is over, so anything the splitter is still holding back
            # against a `<think>` tag split across chunks is not a tag. Flushed
            # here rather than only after the loop, because `round_text` is
            # echoed back as an assistant turn -- for a tool round and for a
            # nudge alike -- and a turn missing its last few words is a turn the
            # model is asked to continue from having apparently trailed off.
            for kind, piece in splitter.flush():
                if kind == REASONING:
                    generation.reasoning.append(piece)
                else:
                    generation.content.append(piece)
                    round_text.append(piece)

            # Here, and not where the usage chunk was read. The usage block
            # arrives while the splitter is still holding this round's last few
            # characters back, so stamping it there left those characters looking
            # uncounted and the stored figure came out a token or two above what
            # the endpoint actually said. A round's usage covers a round's
            # output, so the mark belongs at the round's end.
            # See metrics._since_counted.
            _mark_counted(generation)
            # Before `close_step` below, which stamps the total this adds to.
            round_thinking = _close_thinking(generation, round_thinking)

            calls = accumulator.calls
            if generation.stopped or not calls:
                # The model says it is done. Believe it.
                break

            # Something was called, so whatever it said it had finished, it had
            # not. The count is of *consecutive* stops.
            generation.nudges = 0

            if round_number >= budget:
                # Out of rounds with the model still asking for tools.
                #
                # The tools are withdrawn and it is asked once more, rather than
                # the reply simply ending here. A model that goes straight to
                # tool calls has written no prose at all by this point, so
                # breaking produced an empty bubble with an error line under it
                # -- somebody watching a good piece of research get to its sixth
                # search saw the whole thing thrown away. What it has gathered is
                # in the transcript either way; one more request turns it into an
                # answer.
                #
                # `budget`, not `MAX_ROUNDS`. The loop is sized by the budget
                # above and the message below has always reported it, but the
                # comparison was against the global 3 -- so an agent chat allowed
                # forty steps stopped after three and said it had taken forty.
                # Two numbers, one of them wrong, in code whose whole job is to
                # say what happened.
                howmany = "one round" if budget == 1 else f"{budget} rounds"
                offered, payload = _wrap_up(
                    generation,
                    f"after {howmany} of tool calls",
                    payload,
                    name=calls[0]["name"],
                )
                if wrapping_up:
                    # Already asked, and it called a tool anyway -- which it
                    # cannot do, since none were offered. A backstop, not a path.
                    break
                wrapping_up = True
                continue

            # Parsed once, here, and shared by everything below: the approval
            # card, `policy.decide`, and the runner. See `_arguments_for`.
            arguments = _arguments_for(tool_context, calls)

            # Decided before anything runs, never during. A round's calls run
            # together under a semaphore, and four people-shaped pauses inside
            # that gather would queue behind each other invisibly -- see
            # services/interaction.py.
            decided, allowed, edited = await _authorise(
                generation, tool_context, calls, arguments
            )
            if generation.stopped:
                break

            messages = [
                # The **raw** arguments string, not the parsed dict: the
                # endpoint has to see back exactly what it sent, or an
                # id-matching server pairs its own call with something it does
                # not recognise.
                #
                # Built after `_authorise` rather than before it, because a
                # command corrected on the approval card is written back into
                # `calls` there. The other order sent the model the command it
                # proposed while a different one ran, and every later round
                # reasoned from a transcript that was quietly false.
                *payload["messages"],
                tools_service.assistant_turn(calls, "".join(round_text), round_blocks),
            ]

            generation.status = _tool_status(calls)
            generation.touch()
            try:
                outcomes = await _run_calls(
                    tool_context, calls, arguments, decided=decided, allowed=allowed
                )
            finally:
                generation.status = ""
                generation.touch()

            for index, (call, outcome) in enumerate(zip(calls, outcomes, strict=True)):
                if index in edited:
                    # A command somebody corrected on the card is theirs, not
                    # the model's. Shown as such, for the same reason a plan
                    # goes back quoted and attributed: text must not arrive
                    # wearing an authorship it does not have, in either
                    # direction.
                    outcome.event["edited"] = True
                generation.tool_events.append(outcome.event)
                if outcome.event.get("name") == "todo" and isinstance(
                    outcome.event.get("todos"), list
                ):
                    generation.todos = list(outcome.event["todos"])
                messages.append(tools_service.tool_turn(call, outcome.content))
                if opened := outcome.event.get("canvas"):
                    # A runner cannot write the message row, so the loop carries
                    # this exactly as it carries a merged plan. Never activated:
                    # an agent reads forty files in a long reply, and dragging
                    # somebody through all of them -- or away from a file they
                    # are editing -- is what makes a panel like this unusable.
                    canvas_service.open_tab(generation.canvas, opened, activate=False)
                if attachment_id := outcome.event.get("attachment_id"):
                    # A generated image. The runner wrote the row and the bytes;
                    # binding it to this reply is the loop's job for the reason
                    # the canvas tab above is -- a runner cannot write the
                    # message row, and `_persist` is the single writer.
                    generation.attachment_ids.append(str(attachment_id))
                if outcome.event.get("plan"):
                    generation.plan = outcome.event["plan"]
                    # Only `plan_submit` sets this. `plan_update` writes the
                    # same key -- so `_persist` stays one writer with one
                    # rule -- but is bookkeeping mid-work and must not end the
                    # reply, or the turn would stop dead every time a task was
                    # ticked off.
                    if outcome.event.get("plan_final"):
                        generation.plan_final = True
            # This round is over: its thinking, its prose and its tool calls are
            # all in. Anything appended from here belongs to the next step, and
            # that is what makes the bubble a sequence rather than three zones.
            generation.close_step()
            generation.touch()

            # Something typed while this reply was working. Taken in here, at a
            # round boundary, rather than made to wait for the whole reply: an
            # agent that has just finished one loop and is about to start
            # another is exactly when "actually, do it the other way" is worth
            # having.
            #
            # Only while there is a round left to answer in. Injecting into the
            # last one would deliver the prompt into a reply that then runs out
            # of budget without addressing it -- and it is marked delivered, so
            # nothing would ever send it again. Below that line it waits for
            # `_drain`, which always gives it a reply of its own.
            if round_number + 1 < budget and (
                added := _inject(generation, generation.chat_id, vision)
            ):
                messages.append(added)

            payload = {**payload, "messages": messages}
            # `/image` forces the first round to call the tool. Leaving it set
            # would force *every* round to, so the reply could never finish --
            # it would draw a picture, be asked again, and draw another.
            payload.pop("tool_choice", None)

            # A plan ends the turn. One more request so the model can say what
            # it proposed and why -- a bubble containing only a card reads as
            # though it had nothing to add -- but with the tools withdrawn, so
            # "one more round" cannot become three rounds of it changing its
            # mind about a plan the reader is being asked to approve.
            if generation.plan_final:
                offered = []
                payload.pop("tools", None)

        for kind, piece in splitter.flush():
            (generation.reasoning if kind == REASONING else generation.content).append(piece)
        _mark_counted(generation)
        generation.touch()

    except LLMError as exc:
        generation.error = exc.message
        log.info("generation failed for chat %s: %s", generation.chat_id, exc.message)
    except asyncio.CancelledError:
        # Shutdown, not a reader navigating away -- that no longer reaches here.
        generation.stopped = True
        generation.interrupted = True
        raise
    except Exception:  # noqa: BLE001 - a task that dies silently is worse
        generation.error = i18n.t("Something went wrong while generating this reply.")
        log.exception("unexpected generation failure for chat %s", generation.chat_id)
    finally:
        if reasoning_started is not None and not generation.reasoning_ms:
            generation.reasoning_ms = int((time.monotonic() - reasoning_started) * 1000)

        generation.elapsed_ms = int((time.monotonic() - started) * 1000)
        # There used to be a fallback here filling `completion_tokens`,
        # `prompt_tokens` and `context_tokens` from the estimates when the
        # endpoint had reported nothing. It is gone, and nothing is lost:
        # `metrics.from_generation` now takes `max(reported, estimated)` for
        # every one of the three, so the same figures come out and the row is
        # written through the same code the live chips are rendered from.
        #
        # Two copies of one rule was the actual fault, not an accident of
        # placement. They disagreed -- the fallback used `prompt_estimate_total`
        # where the live path used `prompt_estimate` -- so the numbers jumped at
        # the `done` frame; and writing into these fields made "did the endpoint
        # count this?" unanswerable afterwards, which is what `reported_usage`
        # now records instead.

        # Naming the chat is a second, short completion, so it has to happen
        # here rather than in the synchronous persist step below. Best-effort:
        # a chat title is never worth surfacing an error for.
        title = ""
        if needs_title and question:
            if title_from_prompt or generation.error or endpoint is None:
                title = chat_service.fallback_title(question)
            else:
                with contextlib.suppress(Exception):
                    title = await chat_service.generate_title(
                        endpoint,
                        model_id,
                        question,
                        generation.text,
                        template=title_prompt,
                    )
                title = title or chat_service.fallback_title(question)

        # Written *before* `done`, because `_follow` breaks out of its loop the
        # moment it sees that flag and immediately re-renders the bubble from
        # the row. The other order left a window in which the finished frame
        # showed the previous turn's stored values.
        _persist(generation, title, time.monotonic() - started)
        # After the row is authoritative and before `done`, for the same reason
        # `_persist` is: `_follow` breaks the instant it sees that flag, and the
        # frame it then sends is the one that has to carry the next turn's
        # bubbles. There is no push channel that outlives a single reply.
        #
        _drain(generation)
        generation.done = True
        generation.finished_at = datetime.now(UTC)
        generation.touch()


def _device_turn(db, chat: Chat, message: Message, generation: Generation) -> dict | None:
    """What a device chat's reply needs, read while the session is open; None for
    every other chat."""
    token_id = getattr(chat, "device_token_id", None)
    if not token_id:
        return None
    from lembas.db.models import ApiToken

    token = db.get(ApiToken, token_id)
    prior = list(
        db.scalars(
            select(Message)
            .where(Message.chat_id == chat.id, Message.role == ROLE_USER)
            .order_by(Message.created_at.desc())
        )
    )
    asked = next((m for m in prior if m.created_at <= message.created_at), None)
    generation.earlier_turns = len(prior) > 1
    if message.origin == ORIGIN_ATTACH:
        # What was written before the shutdown, kept: following the device on
        # adds to it (and a turn finished meanwhile replaces it whole).
        generation.content = [message.content or ""]
        generation.reasoning = [message.reasoning or ""]
        generation.tool_events = list(message.tool_calls_json or [])
        generation.steps = list(message.steps_json or [])
    # The chat's model and effort as the device knows them.
    from lembas.services import device_link

    served, effort = device_link.chat_config(db, chat)
    return {
        "chat_id": chat.id,
        "token_id": token_id,
        "project_dir": chat.project_dir or "",
        # A temporary chat reads and changes nothing, on a device too: it is
        # held at Plan whatever the composer says, and "Keep this chat" gives
        # it back the mode it was set to.
        "mode": "plan" if chat.temporary else chat.agent_mode,
        "model": served,
        "effort": effort,
        "prompt": (asked.content if asked is not None else "") or "",
        "device_name": (token.device_host or token.name) if token is not None else "The device",
        # The session the chat keeps, and whether this turn was typed in the
        # device's terminal -- then the reply only follows it.
        "session_id": chat.device_session_id or "",
        "mirror": asked is not None and asked.origin == device_link.ORIGIN_TERMINAL,
        # A reply cut off here while the device went on is followed
        # again, never sent twice; and the message it answers, by id, so a
        # message joining a turn already running is shown where it went in.
        "attach": message.origin == ORIGIN_ATTACH,
        "message_id": asked.id if asked is not None else "",
        # The files sent with it, so a picture attached in a device chat
        # reaches the device.
        "attachments": _device_attachments(asked) if asked is not None else [],
    }


def _device_attachments(message: Message) -> list[dict]:
    """A message's attachments as `device_link.prompt_blocks` reads them: what
    each is, and where its bytes are -- read when the prompt is built, not here,
    so a session is not held open over a file read."""
    from lembas.services import files as files_service

    out: list[dict] = []
    for attachment in message.attachments:
        path = files_service.stored_path(attachment.stored_name)
        out.append(
            {
                "name": attachment.filename,
                "kind": attachment.kind,
                "media_type": attachment.media_type or "",
                "path": str(path) if path is not None else "",
                "text": attachment.extracted_text or "",
                "size": attachment.size_bytes,
            }
        )
    return out


async def _maybe_compact(generation: Generation) -> None:
    """Summarise the earlier turns if the window is about to be full.

    Never raises. A failed compaction logs and sends the uncompacted request,
    which either works or fails upstream with a message that says what actually
    happened -- refusing to answer because the summariser was unavailable would
    be a worse trade.

    The awaited call is deliberately outside any session, the same shape titling
    uses: read everything needed, close, ask, reopen to write.
    """
    try:
        with session_scope() as db:
            chat = db.get(Chat, generation.chat_id)
            message = db.get(Message, generation.message_id)
            if chat is None or message is None:
                return

            pending = _pending_text(db, message)
            if not compaction_service.should_compact(db, chat, pending=pending):
                return

            template = prompts_service.resolve(db, "task.compact")
            upto = compaction_service.last_complete(db, chat)
            if not template.strip() or upto is None:
                return

            endpoint, model_id = chat_service.resolve_endpoint(db, chat)
            transcript = compaction_service.transcript(db, chat, upto=upto)
            previous = compaction_service.previous_summary_block(chat)
            upto_id = upto.id

        generation.status = i18n.t("Summarising earlier messages…")
        generation.touch()

        summary = await chat_service.summarise_for_compaction(
            endpoint,
            model_id,
            transcript=transcript,
            previous_summary=previous,
            template=template,
        )
        if not summary:
            return

        with session_scope() as db:
            chat = db.get(Chat, generation.chat_id)
            upto = db.get(Message, upto_id)
            if chat is None or upto is None:
                return
            compaction_service.apply(chat, summary=summary, upto=upto)
            db.commit()
            log.info("chat %s compacted automatically through %s", chat.id, upto_id)
    except Exception:  # noqa: BLE001 - the reply matters more than the tidy-up
        log.exception("automatic compaction failed for chat %s", generation.chat_id)
    finally:
        generation.status = ""
        generation.touch()


# How many of a round's tool calls may be in flight at once. A bound rather
# than none: a model that asks for eight would otherwise open eight sockets and
# eight database sessions at the same moment.
MAX_PARALLEL_TOOLS = 4


def _gave_up(generation, why: str) -> None:
    """Stop, and leave something in the transcript saying why.

    A reply that simply stopped would look like the model losing interest. The
    event is the same shape the out-of-rounds branch uses, so it renders with
    everything else.
    """
    generation.tool_events.append(
        {
            "name": "budget",
            "kind": "agent",
            "status": "error",
            "results": [],
            "error": f"Stopped {why}. Ask again to carry on from here.",
        }
    )
    # Its own step. This event is appended outside the round loop, so without a
    # mark it would fall into the open tail -- where the live view has no tools
    # slot -- and the one line saying why the reply stopped would be the one
    # line nobody saw.
    generation.close_step()
    generation.touch()


def _wrap_up(generation, why: str, payload: dict, *, name: str = "budget") -> tuple[list, dict]:
    """A budget has run out. Withdraw the tools and ask for an answer.

    Returns the empty tool list and the payload without its `tools` array, so
    the next request is one the model can only answer.

    Every budget used to end the reply where it was noticed, which is fine for a
    model that narrates as it works and produces nothing at all for one that goes
    straight to tool calls: an empty bubble with a red line under it, and a good
    piece of research thrown away at its sixth search. What it has gathered is
    already in the transcript, so one more request without tools turns it into
    an answer. That is the same move `plan_submit` makes -- a turn should not end
    mid-sentence -- and it is why the loop runs to `budget + 2`.

    The event still goes in the transcript. The reader has to be able to tell an
    answer the model chose to give from one it gave because it ran out of room,
    and those read identically otherwise.
    """
    generation.tool_events.append(
        {
            "name": name,
            "kind": "agent",
            "status": "error",
            "results": [],
            "error": (
                f"Stopped {why}. What follows is an answer from what had been "
                "gathered by then; ask again to carry on."
            ),
        }
    )
    # Same reason as `_gave_up`: appended outside the round loop, so it needs a
    # mark of its own or it lands in the step still being written.
    generation.close_step()
    generation.touch()
    return [], {key: value for key, value in payload.items() if key != "tools"}


def _too_big(generation: Generation) -> bool:
    """Whether the request about to go out leaves no room to answer in.

    `context_limit` of 0 is *unknown*, not small -- the rule this codebase
    already applies to the context percentage and to automatic compaction -- so
    a model nobody has declared a window for is never stopped by this. That is
    the honest answer: the alternative is refusing to work on every model an
    administrator has not filled a number in for.
    """
    if not generation.context_limit:
        return False
    return generation.prompt_estimate > generation.context_limit * CONTEXT_HEADROOM


def _thought_at(generation: Generation, span: tuple[float, float] | None) -> tuple[float, float]:
    """Widen this round's thinking interval to now.

    First call in a round opens it; every later one moves its end. The interval
    rather than a running sum, because reasoning arrives in a burst of small
    deltas and adding a gap per delta would count the network's latency as the
    model's thinking.
    """
    now = time.monotonic()
    span = (now, now) if span is None else (span[0], now)
    generation.round_thinking_ms = int((span[1] - span[0]) * 1000)
    return span


def _close_thinking(
    generation: Generation, span: tuple[float, float] | None
) -> tuple[float, float] | None:
    """Add this round's thinking to the reply's total. Returns None to reopen.

    Called where the round ends, so `close_step` can stamp a cumulative figure
    that `services/steps.py` diffs into a per-block duration -- the same shape
    as the three lengths it already stamps.
    """
    if span is not None:
        generation.thinking_ms += int((span[1] - span[0]) * 1000)
    generation.round_thinking_ms = 0
    return None


def _mark_counted(generation: Generation) -> None:
    """Record that everything written so far is covered by a reported count.

    A no-op until some usage block has arrived, because until then there is
    nothing to interpolate from and `metrics.from_generation` falls back to
    estimating the lot. After that it is what makes a reported figure be shown
    verbatim rather than with an estimate added on top of it.
    """
    if generation.reported_usage:
        generation.counted_chars = len(generation.text) + len(generation.thinking)


def _tool_status(calls: list[dict]) -> str:
    """What to show while tools run.

    A remote tool -- an HTTP endpoint, an MCP server -- can take seconds with
    nothing streaming, and a silent pause is exactly what a hang looks like.
    """
    if len(calls) == 1:
        return f"Running {tool_labels.label_for(calls[0]['name'])}…"
    return f"Running {len(calls)} tools…"


def _book(context) -> dict:
    """The tools this request may call, keyed by name.

    `context.tools` is authoritative *even when empty* -- a dict means somebody
    resolved a set. Only `None` means nobody did, which is the one case that
    falls back to the import-time registry.
    """
    return context.tools if context.tools is not None else tools_service.REGISTRY


def _arguments_for(context, calls: list[dict]) -> list[dict]:
    """Every call's arguments, parsed once and shared by the question card and
    the runner, so the card describes what the runner is given."""
    book = _book(context)
    return [
        tools_service.parse_arguments(book.get(call["name"]), call["arguments"]) for call in calls
    ]


def _ask_items(context, calls: list[dict], arguments: list[dict]) -> list[interaction.Item]:
    """Which of this round's calls need a person, and what to show about each.

    Looked up through `context.tools`, the map of what was actually offered --
    the same authority `run_tool` uses. A name that is not in it is left alone
    here and refused there, so an unknown tool cannot smuggle itself past by
    being unclassifiable.
    """
    book = _book(context)
    items: list[interaction.Item] = []

    for index, call in enumerate(calls):
        tool = book.get(call["name"])
        if tool is None or tool.risk != tools_service.RISK_ASK:
            continue
        args = arguments[index]

        for asked in _questions_in(args):
            items.append(
                interaction.Item(
                    index=index,
                    key=f"q{len(items)}",
                    kind=interaction.KIND_QUESTION,
                    tool_name=call["name"],
                    title=str(asked.get("question") or "").strip() or "A question for you",
                    options=_options_in(asked),
                    multiple=bool(asked.get("multiple")),
                )
            )
    return items


def _options_in(asked: dict) -> tuple[interaction.Option, ...]:
    """The choices offered for one question, however they were spelled.

    The schema asks for objects with a `label` and an optional `description`,
    and a capable model sends that. A small one sends a list of bare strings --
    which is what the schema asked for until recently and is what most examples
    of this pattern look like -- so that is read as a label with no description
    rather than refused. Anything else in the list is dropped rather than
    stringified, because `{'a': 1}` rendered as a choice is worse than one
    choice fewer.

    An option meaning "something else" is **not** added here. It belongs to the
    template, which adds it to every question and owns the box behind it; adding
    it to the data would make it indistinguishable from one the model wrote.
    """
    out: list[interaction.Option] = []
    for raw in asked.get("options") or []:
        if isinstance(raw, str):
            label, description = raw.strip(), ""
        elif isinstance(raw, dict):
            label = str(raw.get("label") or raw.get("name") or raw.get("value") or "").strip()
            description = str(raw.get("description") or "").strip()
        else:
            continue
        if not label:
            continue
        out.append(
            interaction.Option(
                label=label[: interaction.MAX_OPTION_CHARS],
                description=description[: interaction.MAX_OPTION_CHARS],
            )
        )
        if len(out) >= interaction.MAX_OPTIONS:
            break
    return tuple(out)


def _questions_in(args: dict) -> list[dict]:
    """The questions in one `ask_user` call, however it was spelled.

    The schema asks for a list of objects, and a capable model sends that. A
    small one sends a bare `question` string, or a list of plain strings, or
    one object where a list belonged -- all of which mean something obvious, so
    they are read rather than refused. Getting this wrong costs a whole round
    trip and produces a card saying "A question for you" and nothing else.
    """
    raw = args.get("questions")
    if raw is None:
        raw = args.get("question")
    if raw is None:
        return []
    if isinstance(raw, str | dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for entry in raw[: interaction.MAX_QUESTIONS]:
        if isinstance(entry, str) and entry.strip():
            # A bare string, possibly alongside a sibling `options` that was
            # meant to go with it -- which only makes sense for a lone question.
            out.append({"question": entry, "options": args.get("options") if len(raw) == 1 else []})
        elif isinstance(entry, dict) and str(entry.get("question") or "").strip():
            out.append(entry)
    return out


async def _authorise(
    generation, context, calls: list[dict], arguments: list[dict]
) -> tuple[dict[int, ToolOutcome], set[int], set[int]]:
    """Which of this round's calls may run, and what the others answer instead.

    Returns outcomes keyed by the call's index, the indices a person allowed,
    and the indices whose command they corrected on the way. Every index the
    caller does not find in the first is cleared to run; every index it does
    find is answered without the runner being reached at all. That is what keeps
    `zip(calls, outcomes, strict=True)` aligned -- an endpoint matching on
    `tool_call_id` pairs the wrong content with the right id otherwise.

    Also returns the indices a person explicitly allowed, so the runners can be
    told. They re-check the mode as a backstop and would otherwise refuse the
    very thing that was just approved -- the mode says "ask", and asking is what
    happened.

    A command corrected on the card is written back into `arguments` **in
    place**, because that same list is what `_run_calls` hands to `run_tool` as
    `parsed=` and `run_tool` never re-parses. Editing the item would do nothing:
    `Item` is display-only and frozen. This is the one place the two meet.
    """
    refused: dict[int, ToolOutcome] = {}
    items = _ask_items(context, calls, arguments)
    if not items:
        return refused, set(), set()

    # Nobody can answer, so nothing waits. A scheduled task and a subagent both
    # run with no reader, and a card built for one of them is a reply doing
    # nothing for fifteen minutes and then giving up -- indistinguishable, from
    # every screen, from the feature not working. Answered immediately instead,
    # in the same shape a refusal takes, so the model reads a sentence it can
    # act on and the round carries on with everything else in it.
    #
    # This is what makes the modes usable here at all: a helper runs in Plan or
    # Edit, both of which resolve a command to ASK, and ASK arriving here means
    # "not in this chat" rather than "hold everything". See
    # services/subagent.py.
    if getattr(context, "unattended", False):
        return {**refused, **{item.index: _unanswerable(item) for item in items}}, set(), set()

    timeout = float(context.interaction_timeout or 900)
    pause = interaction.build(uuid.uuid4().hex, items, timeout=timeout)
    generation.status = interaction.summarise(pause.items)
    reply = await interaction.wait_for(generation, pause, timeout=timeout)
    generation.status = ""

    if reply.ended:
        generation.stopped = True
        return refused, set(), set()

    decided: dict[int, ToolOutcome] = dict(refused)
    allowed: set[int] = set()
    edited: set[int] = set()

    # Questions are grouped back by call, because one `ask_user` call may have
    # carried several and the endpoint expects exactly one tool turn per call.
    grouped: dict[int, list[interaction.Item]] = {}
    for item in items:
        grouped.setdefault(item.index, []).append(item)
    for index, asked in grouped.items():
        decided[index] = _answered(asked, reply)

    return decided, allowed, edited


def _unanswerable(item: interaction.Item) -> ToolOutcome:
    """What a call gets back in a chat where nobody can be asked.

    Deliberately not worded as a refusal by a person: nobody refused, and a
    model told "they declined" reasons about a reader who is not there. It says
    the thing that is actually true and the thing that follows from it -- this
    cannot happen here, so do the rest without it -- because the alternative a
    model reaches for otherwise is to ask again in different words.

    Two shapes arrive here. An approval, which is a command or a write outside
    what this chat allows; and a question, which should not exist at all because
    `ask_user` is withdrawn from an unattended chat -- it is answered anyway, so
    that a call arriving by some path that skipped `resolve_tools` is refused
    rather than left to hang.
    """
    if item.kind == interaction.KIND_APPROVAL:
        return ToolOutcome(
            "That is not something you may do here: this conversation runs with "
            "nobody present, so there is no one to approve it. Do what you can "
            "without it and say plainly in your answer what you could not do.",
            {
                "name": item.tool_name,
                "kind": "agent",
                "label": item.title,
                "query": item.detail,
                "results": [],
                "status": "error",
                "error": "Not permitted here — nobody is present to approve it.",
            },
        )
    return ToolOutcome(
        "There is nobody to ask: this conversation runs on its own. Choose the "
        "most reasonable reading, carry on, and say in your answer what you "
        "assumed.",
        {
            "name": item.tool_name,
            "kind": "ask",
            "label": "Nobody to ask",
            "query": item.title,
            "results": [],
            "status": "error",
            "error": "Nobody is present to answer.",
        },
    )


def _answered(items: list[interaction.Item], reply: interaction.Reply) -> ToolOutcome:
    """What one `ask_user` call gets back, however many questions it put."""
    event = {
        "name": items[0].tool_name,
        "kind": "ask",
        "label": "Asked you",
        "query": "; ".join(item.title for item in items),
        "results": [],
    }

    if reply.outcome == interaction.EXPIRED:
        return ToolOutcome(
            "They did not answer. Carry on as best you can without it, or say "
            "what you still need.",
            {**event, "status": "error", "error": "No answer.", "text": ""},
        )

    answered = [(item, reply.answer_to(item)) for item in items]
    given = [(item, text) for item, text in answered if text]
    if not given:
        return ToolOutcome(
            "They closed the question without answering.",
            {**event, "status": "error", "error": "No answer.", "text": ""},
        )

    # Each answer is quoted next to the question it belongs to. With four
    # questions on one card, a bare list of answers would leave the model
    # matching them up by position and sometimes getting it wrong.
    lines = [f"{item.title}\n  → {text}" for item, text in given]
    skipped = [item for item, text in answered if not text]
    if skipped:
        lines.append(
            "They left unanswered: " + "; ".join(item.title for item in skipped)
        )

    body = "\n\n".join(lines)
    return ToolOutcome(f"They answered:\n\n{body}", {**event, "status": "ok", "text": body})


async def _run_calls(
    context,
    calls: list[dict],
    arguments: list[dict],
    *,
    decided: dict[int, ToolOutcome] | None = None,
    allowed: set[int] | None = None,
) -> list:
    """Run one round's calls together, results in call order.

    Sequential was right when every tool was a local database read. A remote one
    is latency-bound, and three two-second calls in a row are six seconds of a
    reply looking hung -- while the model has already been told it may ask for
    several at once.

    The results are indexed rather than appended as they finish, because each
    tool turn has to line up with the assistant turn's `tool_calls`: an endpoint
    matching on `tool_call_id` would otherwise pair the right id with the wrong
    content the moment two calls came back out of order.

    Safe to run together because `run_tool` never raises, so no failure cancels
    its siblings, and each runner opens its own `session_scope()` against a
    database in WAL mode with a busy timeout.
    """
    limit = asyncio.Semaphore(MAX_PARALLEL_TOOLS)

    async def one(index: int, call: dict):
        # Already answered by a person, or refused before it got here. It still
        # occupies its index, because the tool turns have to line up.
        if decided and index in decided:
            return decided[index]

        # A per-call copy for anything a person allowed, so the runner's own
        # check does not undo their decision. A copy rather than a flag on the
        # shared context, because a round runs its calls together and only some
        # of them were approved.
        ctx = context

        async with limit:
            return await tools_service.run_tool(
                ctx, call["name"], call["arguments"], parsed=arguments[index]
            )

    return list(await asyncio.gather(*(one(i, c) for i, c in enumerate(calls))))


def _pending_text(db, message: Message) -> str:
    """The user turn this reply is answering, for the size estimate."""
    previous = db.scalars(
        select(Message)
        .where(Message.chat_id == message.chat_id, Message.created_at < message.created_at)
        .order_by(Message.created_at.desc())
        .limit(1)
    ).first()
    return previous.content if previous is not None else ""


def _question_from(payload: dict) -> str:
    """The last thing the user said, for auto-titling."""
    for entry in reversed(payload.get("messages", [])):
        if entry.get("role") != ROLE_USER:
            continue
        content = entry.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ).strip()
    return ""


def _next_waiting(db, chat_id: str) -> Message | None:
    """The oldest prompt in this chat that has not been sent."""
    return db.scalars(
        select(Message)
        .where(
            Message.chat_id == chat_id,
            Message.role == ROLE_USER,
            Message.queued.is_(True),
        )
        .order_by(Message.created_at)
        .limit(1)
    ).first()


def _drain(generation: Generation) -> None:
    """Hand the next waiting prompt to a reply of its own, if there is one.

    Exactly one, not all of them. Draining the lot would put two consecutive
    user turns into the next request, which several local chat templates refuse
    outright -- `build_messages` already goes to some trouble over that around
    the compaction lead. "One after another" is also what was asked for: the
    second waiting prompt is drained by the reply the first one starts, and so
    on down the chain.

    Three refusals, and none of them is a special case:

    - **Superseded.** The same test `_persist` makes, for the same reason: a
      regeneration cancels its predecessor and the predecessor's `finally:`
      still runs. Without this, regenerating would drain the queue *and* leave
      a third generation running.
    - **Stopped.** Stop means stop, and the queue stays visible and
      undelivered with Send now beside it. This is also what makes shutdown
      safe -- cancellation sets `stopped`, so a restart never fires off a reply
      with nobody watching.
    - **Errored.** The endpoint has just failed. Feeding the next prompt into it
      produces a second failure and spends somebody's words to do it.
    """
    owner = _RUNNING.get(generation.message_id)
    if owner is not None and owner is not generation:
        return
    if generation.stopped or generation.error:
        return

    try:
        with session_scope() as db:
            chat = db.get(Chat, generation.chat_id)
            if chat is None:
                return
            waiting = _next_waiting(db, chat.id)
            if waiting is None:
                return

            waiting.queued = False
            assistant = chat_service.create_message(
                db, chat, ROLE_ASSISTANT, "", complete_=False, model_id=chat.model_id
            )
            chat_id, assistant_id = chat.id, assistant.id
    except Exception:  # noqa: BLE001 - the reply is over either way
        log.exception("could not drain the queue for chat %s", generation.chat_id)
        return

    # Outside the session: this starts a task, and a task is not something to
    # hold a database session open across.
    ensure(chat_id, assistant_id)
    generation.drained = True


def entry_text(entry: dict) -> str:
    """The words of a prompt as `message_payload` built it."""
    content = entry.get("content")
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        ).strip()
    return str(content or "").strip()


def _inject(
    generation: Generation, chat_id: str, vision: bool, *, mark: bool = True
) -> dict | None:
    """Take the oldest waiting prompt into this reply, between two rounds.

    This restamps the placeholder's `created_at` so the reply sorts after the
    prompt it answers in the model's history; the page shows the prompt inside
    the reply, where it went in.

    Marked delivered and committed *before* the request goes out, so this is
    at-most-once. A crash in between loses the turn, which is recoverable --
    the words are still in the transcript with Send now beside them. The other
    way round would ask the same question twice and let an agent act on it
    twice, which is not.

    Sent verbatim, in the user role, with no framing. Everything else this
    codebase injects is quoted and attributed because it came out of a file, a
    page or a machine; this one genuinely *is* the person at the keyboard,
    authenticated by the session cookie and stored as a `Message` whose role
    says so. Wrapping it would teach a model that a user turn can be a
    quotation, which is the exact distinction the other two rely on. What the
    model needs -- that this can happen at all -- is one sentence in the
    harness, where authored wording lives.
    """
    try:
        with session_scope() as db:
            waiting = _next_waiting(db, chat_id)
            if waiting is None:
                return None

            waiting.queued = False
            entry = chat_service.message_payload(waiting, vision=vision)
            # The reply that answers it must sort *before* it, or the next
            # turn's transcript reads "answer, then the question it answered"
            # and a small model dutifully answers again. Moving the placeholder
            # rather than the prompt keeps several interjections in the order
            # they were typed.
            placeholder = db.get(Message, generation.message_id)
            if placeholder is not None:
                placeholder.created_at = datetime.now(UTC)
            generation.injected_ids.append(waiting.id)
            waiting_id = waiting.id
    except Exception:  # noqa: BLE001 - a lost interjection is not a failed reply
        log.exception("could not take a queued prompt into chat %s", chat_id)
        return None

    # Shown inside the reply where it went in. A device reply marks it
    # when the device says its model took it in, not when it was handed over.
    if mark:
        generation.mark_steer([{"id": waiting_id, "text": entry_text(entry)}])

    generation.status = i18n.t("Taking in what you just added…")
    generation.touch()
    return entry


def _bind_attachments(db, chat, message, ids: list[str]) -> None:
    """Bind images this reply produced to the bubble that produced them.

    The narrowing is the point. The ids arrive on a tool event, and an event is
    a dict a runner built -- so the query names this chat and refuses a row that
    is already bound, exactly as `files.claim` does for an upload, and for the
    identical reason: without it a forged id would attach somebody else's file
    to this conversation.
    """
    from lembas.db.models import Attachment

    rows = db.scalars(
        select(Attachment).where(
            Attachment.id.in_(ids),
            Attachment.chat_id == chat.id,
            Attachment.message_id.is_(None),
        )
    )
    for attachment in rows:
        attachment.message_id = message.id


def _persist(generation: Generation, title: str, elapsed: float) -> None:
    """Write the finished reply, name the chat, and set the unread flag.

    A generation another one has replaced may not write. A regeneration cancels
    its predecessor, whose `finally:` then runs this on the same row -- and it
    would overwrite the fresh reply with the abandoned one.

    The test is "someone else owns this row now", not "this one is registered":
    an unregistered generation still writes, because that is a direct call
    rather than a superseded one.
    """
    owner = _RUNNING.get(generation.message_id)
    if owner is not None and owner is not generation:
        log.debug("skipping persist for superseded generation %s", generation.message_id)
        return

    try:
        with session_scope() as db:
            message = db.get(Message, generation.message_id)
            chat = db.get(Chat, generation.chat_id)
            if message is None or chat is None:
                return

            message.content = generation.text
            message.reasoning = generation.thinking
            # `thinking_ms` in preference: it is the same measurement done
            # properly, summed over every round rather than stopping at the
            # first burst, and it is what the trailing block's duration is
            # derived from. Falls back for a reply that produced no marks.
            message.reasoning_ms = generation.thinking_ms or generation.reasoning_ms
            message.tool_calls_json = generation.tool_events
            # Written together with the three stores it indexes, by the one
            # writer, so a row can never carry marks that describe a different
            # reply's text. Regeneration reuses the `Message` row and overwrites
            # all four for the same reason.
            message.steps_json = generation.steps
            message.plan_json = generation.plan or {}
            if generation.canvas.get("tabs"):
                # A union with whatever the row says *now*, not an overwrite:
                # the snapshot above was seeded when the reply began, and
                # somebody may have opened a tab by hand since.
                chat.canvas_json = canvas_service.merge(chat.canvas_json, generation.canvas)
            if generation.todos is not None:
                chat.todos_json = list(generation.todos)
            if generation.attachment_ids:
                # Images this reply made, bound to it here because this is the
                # only writer. Scoped to rows this chat owns and still unbound,
                # for the reason `files.claim` is scoped: an id that came back
                # on an event must not be able to pull in somebody else's file.
                _bind_attachments(db, chat, message, generation.attachment_ids)
            if generation.plan:
                # This bubble now carries the plan in force, and the chat points
                # at it so the harness can find it with one primary-key lookup
                # rather than a scan. Older bubbles keep the plan as it was then,
                # which is what a transcript is for -- the card is never
                # re-rendered in place.
                chat.plan_message_id = message.id
            message.usage_json = metrics_service.to_json(
                metrics_service.from_generation(generation)
            )
            message.error = generation.error
            message.stopped = generation.stopped
            message.complete = True
            # Cut off by a shutdown: taken up again at the next start.
            message.interrupted = generation.interrupted
            if message.origin == ORIGIN_ATTACH:
                message.origin = ""

            # What this reply cost, against the account's month. Here because
            # this is the single writer and it runs for a reply that finished, a
            # reply that was stopped and a reply that errored alike -- an
            # endpoint charges for tokens it generated whether or not anybody
            # wanted them, and a quota that only counted happy paths is one a
            # Stop button can walk past. `metrics.from_generation` is the one
            # place the three figures are worked out, so this is the same
            # arithmetic the bubble shows.
            spent = metrics_service.from_generation(generation)
            # A device chat's tokens were spent by the device on its own model --
            # through this instance's /v1 when it is logged in here, where they
            # were already counted and charged. Counting them again is the one
            # thing this must not do.
            on_device = bool(getattr(chat, "device_token_id", None))
            usage_service.record(
                db,
                chat.user_id if not on_device else "",
                prompt_tokens=spent.prompt_tokens,
                completion_tokens=spent.completion_tokens,
                images=len(generation.attachment_ids),
            )
            # Credits, charged to the chat's owner -- a helper's reply to the
            # person whose reply sent it, since the helper's chat is theirs -- on
            # the model that spoke. Nothing when credits are off or it is free.
            from lembas.services import credits as credits_service

            credits_service.charge(
                db,
                chat.user_id,
                None if on_device else chat_service.model_row(
                    db, chat_service.speaker_for(db, chat, message)
                ),
                usage={
                    "prompt_tokens": spent.prompt_tokens,
                    "completion_tokens": spent.completion_tokens,
                    "cached_tokens": generation.cached_tokens,
                    "cache_write_tokens": generation.cache_write_tokens,
                },
                message_id=message.id,
                commit=False,
            )

            if title and not chat.title_generated:
                chat.title = title
                chat.title_generated = True

            # Nobody watching when it landed, so it is news. The chat page
            # clears this when it is next opened. Not for a temporary chat:
            # there is no sidebar row for the dot, and the toast would name a
            # chat nobody can navigate to.
            if generation.followers == 0 and not chat.temporary:
                chat.unread = True
                chat.unread_notified = False
                # And out to any browser that asked to be told, which is the
                # only channel that reaches somebody with nothing of ours open.
                # Here rather than in the unread poll because the poll needs a
                # page, and this is exactly the case where there is not one:
                # `followers == 0` says so. Fire and forget -- the reply is
                # finished and nothing about it should wait on a push service.
                push_service.announce_later(
                    chat.user_id,
                    title=chat.title or i18n.t("New reply"),
                    body=i18n.t("Your reply is ready."),
                    url=f"/chat/{chat.id}",
                    kind="chat",
                )

            db.commit()
            log.debug(
                "chat %s finished: %d chars, %d reasoning, %.1fs",
                generation.chat_id,
                len(message.content),
                len(message.reasoning),
                elapsed,
            )
    except Exception:  # noqa: BLE001 - the task is ending either way
        log.exception("could not persist generation for chat %s", generation.chat_id)


__all__ = [
    "RENDER_INTERVAL",
    "Generation",
    "ROLE_ASSISTANT",
    "ensure",
    "get",
    "request_stop",
    "restart",
    "shutdown",
]
