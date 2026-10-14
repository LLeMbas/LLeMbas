"""Linked devices: a LLeMbas CLI dials in, and an agent chat runs on it.

A device signed in with the `link` scope and running `lembas service` keeps a
WebSocket open to `/api/devices/link`. LLeMbas is the **ACP client** on it: it opens
a session in a directory on the device (`session/new`), sends the person's message
(`session/prompt`), and draws what comes back. The device is the agent, and runs
its own harness -- the same design as LLeMbas's own agent (the LLeMbas CLI's `harness/`),
with the device's own model and its own `remote:` limits. **Nothing runs on this
machine**: a device chat's commands run on the device.

**The device decides.** It refuses a directory outside its `remote.roots`, lowers a
mode above its `remote.max_mode`, refuses an untrusted project, and treats an
approval nobody answers as a denial. LLeMbas cannot widen any of that; what it can
do is ask the person, on the approval card.

One JSON-RPC message per WebSocket text frame. A link lives in memory -- one worker
owns every one -- and is replaced when the same device dials again.

**One session, both sides.** A chat keeps the device's id for its session
(`Chat.device_session_id`), so a restart of either side carries it on. And a
session somebody runs in the LLeMbas CLI's terminal on the device is *announced* here:
it becomes an agent chat of its own -- its history imported, every turn typed in
the terminal mirrored into it as it happens, and anything typed here sent to the
terminal, which runs it as if typed there. An approval is asked on both sides and
the first answer counts. The device decides what is announced: its service shares
what its `remote:` limits allow, and `/remote` shares one session by name.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from typing import Any

from lembas.services import interaction
from lembas.web import i18n

log = logging.getLogger(__name__)

# The `_lembas` extension's version. A device speaking 1 still links:
# everything protocol 2 added is gated on a capability its `initialize` lists,
# and a device that lists none is served as before.
PROTOCOL = 2
PROTOCOLS = (1, 2)
ACP_VERSION = 1
HELLO_WAIT = 15.0

# Close codes the device reads as "stop dialling": the token is done.
CLOSE_REFUSED = 4403
CLOSE_PROTOCOL = 4400


@dataclass
class Link:
    """One device's open link."""

    token_id: str
    user_id: str
    name: str
    send_text: Any  # async (str) -> None
    close: Any  # async (code, reason) -> None
    hello: dict[str, Any] = field(default_factory=dict)
    initialized: dict[str, Any] = field(default_factory=dict)
    # chat id -> the device's session id for it
    sessions: dict[str, str] = field(default_factory=dict)
    # chat id -> the mode last sent, so a mode changed in the composer is sent
    # before the next prompt (the device may still lower it)
    modes: dict[str, str] = field(default_factory=dict)
    # chat id -> (model, effort) last sent, the same way
    configs: dict[str, tuple[str, str]] = field(default_factory=dict)
    # the device's terminal id -> the panel's session following it
    terminals: dict[str, Any] = field(default_factory=dict)
    # device session id -> the generation writing into it
    writing: dict[str, Any] = field(default_factory=dict)
    # The sessions a terminal on the device shares: what it said of each
    # (cwd, title, model, mode, effort) ...
    announced: dict[str, dict] = field(default_factory=dict)
    # ... the events of a turn typed there, held until the reply mirroring it is
    # writing (the bubbles are made first) ...
    buffered: dict[str, list] = field(default_factory=dict)
    # ... settled when such a turn ends ...
    mirrors: dict[str, asyncio.Future] = field(default_factory=dict)
    # ... and a chat being made for one, which a turn starting meanwhile waits on.
    adopting: dict[str, asyncio.Event] = field(default_factory=dict)
    # (session id, the device's tool call id) -> the card open for it:
    # approvals, questions and plans alike. Keyed by the call, never by the
    # session alone -- a device asks about two calls of one session at once,
    # and keyed by session the second card took the first one's place. And not
    # by the call alone: providers that number calls per response (Ollama,
    # Gemini: `call_0`) give two sessions the same id, and settling one took
    # down the other's card.
    asks: dict[tuple[str, str], Any] = field(default_factory=dict)
    # The sessions the device says are working now, from its own `task`
    # events -- the authority, where the chat's rows only guess. A message typed
    # here into one of them joins the running turn instead of being refused.
    busy: set[str] = field(default_factory=set)
    # Capability `turns`: the turn each session is running now, by the
    # device's `task` start ...
    current: dict[str, str] = field(default_factory=dict)
    # ... the reply writing each turn, (session id, turn id) -> generation, which
    # is what a turn's events are routed to ...
    turn_writers: dict[tuple[str, str], Any] = field(default_factory=dict)
    # ... the replies waiting for a turn the device has queued to start ...
    queued_turns: dict[tuple[str, str], asyncio.Event] = field(default_factory=dict)
    # ... the terminal's turn each mirror is following ...
    mirror_turns: dict[str, str] = field(default_factory=dict)
    # ... and the turn ids sent from here, so the device's `prompt` event for
    # one is never taken for a turn typed in its terminal.
    # Insertion-ordered, bounded by MAX_SENT_TURNS (oldest dropped first).
    sent_turns: dict[str, None] = field(default_factory=dict)
    # Capability `commands`: the commands each session offers, from the
    # device's `available_commands_update`, for the composer's `/` menu.
    commands: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _waiting: dict[int, asyncio.Future] = field(default_factory=dict)
    _ids: Any = field(default_factory=lambda: itertools.count(1))
    closed: bool = False

    @property
    def roots(self) -> list[str]:
        return [str(r) for r in (self.hello.get("limits") or {}).get("roots") or []]

    @property
    def host(self) -> str:
        """The device's host, as its hello says it, or ""."""
        return str((self.hello.get("device") or {}).get("host") or "").strip()

    @property
    def max_mode(self) -> str:
        return str((self.hello.get("limits") or {}).get("max_mode") or "")

    def can(self, capability: str) -> bool:
        """Whether the device's build answers this part of `_lembas/*`:
        what `initialize` listed. A device that lists
        nothing is offered only what every device answers."""
        meta = (self.initialized.get("_meta") or {}).get("lembas") or {}
        return capability in (meta.get("capabilities") or [])

    @property
    def connection(self) -> str:
        """What the device calls this instance (`_meta.lembas.connection`
        in its `initialize`): the prefix of a model reference that is ours."""
        meta = (self.initialized.get("_meta") or {}).get("lembas") or {}
        return str(meta.get("connection") or "")

    def takes(self, kind: str) -> bool:
        """Whether `session/prompt` takes this kind of content block (ACP
        `promptCapabilities`): `image`, `embeddedContext`."""
        agent = self.initialized.get("agentCapabilities") or {}
        return bool((agent.get("promptCapabilities") or {}).get(kind))

    def writer(self, session_id: str):
        """The reply writing this session's running turn: by its turn id when
        the device names turns, else whichever reply is writing -- but
        never one whose turn has ended, or that writes another turn. A terminal
        turn's approval asked before its mirror follows it would otherwise land
        on the web reply just finishing, and vanish with it; with no reply it
        takes the "nobody here" path (patient: an error, else a denial)."""
        turn = self.current.get(session_id)
        if turn and self.can("turns"):
            found = self.turn_writers.get((session_id, turn))
            if found is not None:
                return found
        generation = self.writing.get(session_id)
        if generation is None or getattr(generation, "device_turn_over", False):
            return None
        mine = getattr(generation, "turn_id", "")
        if turn and mine and mine != turn and self.can("turns"):
            return None
        return generation

    async def request(self, method: str, params: Any = None, *, timeout: float | None = 60) -> Any:
        if self.closed:
            raise LinkClosed("The device is not linked any more.")
        message_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._waiting[message_id] = future
        await self.send_text(
            json.dumps({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        )
        try:
            return await asyncio.wait_for(future, timeout) if timeout else await future
        finally:
            self._waiting.pop(message_id, None)

    async def notify(self, method: str, params: Any = None) -> None:
        if not self.closed:
            await self.send_text(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}))

    async def _answer(
        self, message_id: Any, result: Any = None, error: str = "", code: int = -32603
    ) -> None:
        body: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id}
        if error:
            body["error"] = {"code": code, "message": error}
        else:
            body["result"] = result
        await self.send_text(json.dumps(body))

    def dropped(self) -> None:
        self.closed = True
        for future in self._waiting.values():
            if not future.done():
                future.set_exception(LinkClosed("The device's link dropped."))
        self._waiting.clear()
        for generation in self.writing.values():
            for card in list(generation.waiting):
                card.resolve(interaction.CANCELLED)
        for done in self.mirrors.values():
            if not done.done():
                done.set_exception(LinkClosed("The device's link dropped."))
        for event in self.adopting.values():
            event.set()
        # A shell on the device is gone with the link (the device closes its
        # side when the link drops); every panel following one is told.
        terminals, self.terminals = list(self.terminals.values()), {}
        for terminal in terminals:
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().create_task(terminal.ended("error"))

    async def receive(self, text: str) -> None:
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return
        method = message.get("method")
        message_id = message.get("id")
        if method is None and message_id is not None:
            future = self._waiting.get(message_id)
            if future is not None and not future.done():
                if message.get("error"):
                    error = message["error"]
                    future.set_exception(DeviceError(str(error.get("message") or "error")))
                else:
                    future.set_result(message.get("result"))
            return
        params = message.get("params") or {}
        if message_id is None:
            await self._notification(str(method), params)
            return
        if method == "session/request_permission":
            # Answered in its own task: the card waits for a person, and the
            # receive loop must keep reading the device's events meanwhile.
            asyncio.create_task(self._permission(message_id, params))
            return
        # ask_user and plan_exit on the device (`ask` and `plan`): the
        # same cards, answered the same way, each in a task of its own.
        if method == "_lembas/question":
            asyncio.create_task(self._question(message_id, params))
            return
        if method == "_lembas/plan":
            asyncio.create_task(self._plan(message_id, params))
            return
        await self._answer(message_id, error=f"LLeMbas does not answer {method}", code=-32601)

    async def _notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "_lembas/hello":
            self.hello = dict(params)
            return
        if method == "_lembas/event":
            session_id = str(params.get("sessionId") or "")
            event = params.get("event") or {}
            if not isinstance(event, dict):
                return
            if self.can("turns"):
                self._turn_event(session_id, event)
                return
            if event.get("type") == "task":
                if event.get("state") == "start":
                    self.busy.add(session_id)
                else:
                    self.busy.discard(session_id)
            self._session_event(session_id, event)
            generation = self.writing.get(session_id)
            # A turn that already ended takes no more events: what
            # follows is the device's next turn -- one its terminal started by
            # itself -- not more of this reply.
            if generation is not None and not getattr(generation, "device_turn_over", False):
                apply_event(generation, event)
                if _ends_task(event):
                    generation.device_turn_over = True
                    _settle_mirror(self, session_id)
                return
            if session_id in self.buffered:
                self.buffered[session_id].append(event)
                return
            # A turn typed in the device's terminal, in a session it shares.
            if event.get("type") == "prompt" and session_id in self.announced:
                _begin_mirror(self, session_id, str(event.get("text") or ""))
            return
        if method == "session/update":
            # ACP's own updates carry what `_lembas/event` carries, and are read
            # for one thing only: the session's commands.
            update = params.get("update") or {}
            if isinstance(update, dict) and update.get("sessionUpdate") == (
                "available_commands_update"
            ):
                self.commands[str(params.get("sessionId") or "")] = clean_commands(
                    update.get("availableCommands")
                )
            return
        if method == "_lembas/session/announce":
            session_id = str(params.get("sessionId") or "")
            if session_id and (self.token_id, session_id) in DELETED:
                return
            if session_id:
                self.announced[session_id] = dict(params)
                if params.get("busy"):
                    self.busy.add(session_id)
                asyncio.get_running_loop().create_task(_announced(self, session_id))
            return
        if method == "_lembas/session/left":
            self.announced.pop(str(params.get("sessionId") or ""), None)
            return
        if method == "_lembas/session/deleted":
            # Deleted on the device (/sessions delete, /delete): the chat goes too.
            session_id = str(params.get("sessionId") or "")
            if session_id:
                DELETED.add((self.token_id, session_id))
                self.announced.pop(session_id, None)
                self.commands.pop(session_id, None)
                self.busy.discard(session_id)
                await _delete_chat_for(self, session_id)
            return
        if method == "_lembas/permission/settled":
            # Answered elsewhere -- an approval, a question or a plan:
            # the card for that call goes, and only that one.
            card = self.asks.get(
                (str(params.get("sessionId") or ""), str(params.get("toolCallId") or ""))
            )
            if card is not None:
                card.resolve(interaction.ELSEWHERE)
            return
        if method == "_lembas/terminal/output":
            terminal = self.terminals.get(str(params.get("terminalId") or ""))
            if terminal is not None:
                with contextlib.suppress(ValueError, TypeError):
                    terminal.feed(base64.b64decode(str(params.get("data") or "")))
            return
        if method == "_lembas/terminal/exit":
            terminal = self.terminals.pop(str(params.get("terminalId") or ""), None)
            if terminal is not None:
                await terminal.ended("exited")
            return
        # `session/update` carries the same as `_lembas/event` in ACP's words;
        # LLeMbas asked for its own and reads only those.

    def _session_event(self, session_id: str, event: dict[str, Any]) -> None:
        """What an event says about the session rather than the reply: its
        title, its mode, its model -- onto the chat showing it."""
        kind = event.get("type")
        if kind == "title" and session_id in self.announced:
            _retitle(self, session_id, str(event.get("title") or ""))
        if kind == "mode" and session_id in self.announced:
            _remode(self, session_id, str(event.get("mode") or ""))
        if kind == "model" and self.can("model"):
            _remodel(
                self,
                session_id,
                str(event.get("ref") or ""),
                str(event.get("effort") or ""),
                event.get("instance"),
            )

    def _turn_event(self, session_id: str, event: dict[str, Any]) -> None:
        """One event from a device that names its turns: to the reply
        writing that turn, by its id -- whatever else is writing in the session.

        Before turn ids an event went to whichever reply was writing, and a
        turn typed in the terminal right after a web turn met that web reply
        still finishing: its prompt was taken as more of the web turn, or its
        mirror refused because a reply was running, and the turn was lost.
        """
        kind = event.get("type")
        turn = str(event.get("turnId") or "")
        if kind == "task" and event.get("state") == "start":
            self.busy.add(session_id)
            if turn:
                self.current[session_id] = turn
                waiting = self.queued_turns.get((session_id, turn))
                if waiting is not None:
                    # A reply waiting for this queued turn: held from its start.
                    self.buffered[session_id] = []
                    waiting.set()
        self._session_event(session_id, event)
        running = self.current.get(session_id, "")
        generation = self.turn_writers.get((session_id, running)) if running else None
        if generation is None and not running:
            # The device named no turn (its events began before this link saw a
            # start): whichever reply is writing, as before.
            generation = self.writing.get(session_id)
            if generation is not None and generation.device_turn_over:
                generation = None
        if generation is not None:
            if kind == "task" and event.get("state") == "start":
                generation.turn_started = True
            apply_event(generation, event)
        elif session_id in self.buffered:
            self.buffered[session_id].append(event)
        elif kind == "prompt" and session_id in self.announced and turn not in self.sent_turns:
            # A turn typed in the device's terminal: its own row, whatever reply
            # this session's last web turn is still finishing.
            _begin_mirror(
                self, session_id, str(event.get("text") or ""), turn=turn,
                attachments=event.get("attachments"),
            )
        if _ends_task(event):
            self.busy.discard(session_id)
            if generation is not None:
                generation.device_turn_over = True
                _settle_mirror(self, session_id)
            if self.current.get(session_id) == turn or not turn:
                self.current.pop(session_id, None)

    async def _ask(
        self, generation, card, session_id: str, call_id: str, *, timeout: float
    ):
        """Show one card and wait for its answer: kept under its session and
        call, so `_lembas/permission/settled` takes down this card and no
        other, and the status line says what is still open when it goes."""
        key = (session_id, call_id)
        if call_id:
            self.asks[key] = card
        _status_for_cards(generation, card)
        try:
            return await interaction.wait_for(generation, card, timeout=timeout)
        finally:
            if call_id and self.asks.get(key) is card:
                self.asks.pop(key, None)
            _status_for_cards(generation)

    async def _permission(self, message_id: Any, params: dict[str, Any]) -> None:
        session_id = str(params.get("sessionId") or "")
        generation = self.writer(session_id)
        deny = {"outcome": {"outcome": "selected", "optionId": "deny"}}
        call = params.get("toolCall") or {}
        meta = (params.get("_meta") or {}).get("lembas") or {}
        # Asked on both sides (a terminal's session): somebody may answer there,
        # so nothing here times it out -- and with no reply to show the card on,
        # this side says it cannot ask rather than refusing on their behalf.
        patient = bool(meta.get("patient"))
        if generation is None:
            if patient:
                await self._answer(message_id, error="no reply here to show the approval on")
            else:
                await self._answer(message_id, deny)
            return
        raw = call.get("rawInput") or {}
        command = str(raw.get("command") or "") if isinstance(raw, dict) else ""
        name = str(call.get("title") or "a tool")
        tool = "bash" if command else str(call.get("kind") or "tool")
        options = {str(o.get("optionId")) for o in params.get("options") or []}
        item = interaction.Item(
            index=0,
            key="0",
            kind=interaction.KIND_APPROVAL,
            tool_name=tool,
            title=name if not command else "Run a command",
            detail=command or json.dumps(raw, ensure_ascii=False)[:2000],
            reason=str(meta.get("reason") or "")[: interaction.MAX_REASON_CHARS],
            purpose=str(meta.get("purpose") or ""),
            editable=bool(command),
        )
        timeout = PATIENT_WAIT if patient else ASK_WAIT
        call_id = str(call.get("toolCallId") or "")
        pause = interaction.build(uuid.uuid4().hex, [item], timeout=timeout)
        pause.tool_call_id = call_id
        reply = await self._ask(generation, pause, session_id, call_id, timeout=timeout)
        if reply.outcome == interaction.ELSEWHERE:
            # Answered in the terminal; this answer is not read.
            await self._answer(message_id, deny)
            return
        if reply.ended:
            generation.stopped = True
        if not reply.permitted:
            answer: dict[str, Any] = dict(deny)
            if reply.reason:
                answer["_meta"] = {"lembas": {"feedback": reply.reason}}
            await self._answer(message_id, answer)
            return
        chosen = (
            "session"
            if reply.outcome == interaction.ALLOW_ALWAYS and "session" in options
            else "once"
        )
        answer = {"outcome": {"outcome": "selected", "optionId": chosen}}
        edited = (reply.answers.get(f"text.{item.key}") or "").strip()
        if command and edited and edited != command:
            answer["_meta"] = {"lembas": {"command": edited}}
        await self._answer(message_id, answer)

    async def _question(self, message_id: Any, params: dict[str, Any]) -> None:
        """`_lembas/question`: the device's ask_user, on the card a
        question here uses -- every question of the call on one card, answered
        at once, "Something else" on each, and Dismiss."""
        session_id = str(params.get("sessionId") or "")
        generation = self.writer(session_id)
        patient = _patient(params)
        if generation is None:
            await self._nobody_here(message_id, patient)
            return
        questions = [q for q in params.get("questions") or [] if isinstance(q, dict)]
        items = [
            interaction.Item(
                index=0,
                key=f"q{n}",
                kind=interaction.KIND_QUESTION,
                tool_name="ask_user",
                title=str(q.get("question") or "").strip()[:2000] or "A question for you",
                header=str(q.get("header") or "").strip()[:60],
                options=tuple(
                    interaction.Option(
                        label=str(o.get("label") or "")[: interaction.MAX_OPTION_CHARS],
                        description=str(o.get("description") or "")[
                            : interaction.MAX_OPTION_CHARS
                        ],
                        recommended=bool(o.get("recommended")),
                    )
                    for o in (q.get("options") or [])[: interaction.MAX_OPTIONS]
                    if isinstance(o, dict) and str(o.get("label") or "").strip()
                ),
                multiple=bool(q.get("multiple")),
            )
            for n, q in enumerate(questions[: interaction.MAX_QUESTIONS])
        ]
        if not items:
            await self._answer(message_id, {"outcome": "dismissed"})
            return
        timeout = PATIENT_WAIT if patient else ASK_WAIT
        call_id = str(params.get("toolCallId") or "")
        card = interaction.build(uuid.uuid4().hex, items, timeout=timeout)
        card.tool_call_id = call_id
        card.dismissable = True
        card.why = str(params.get("why") or "").strip()[: interaction.MAX_REASON_CHARS]
        reply = await self._ask(generation, card, session_id, call_id, timeout=timeout)
        if reply.outcome == interaction.ELSEWHERE:
            await self._answer(message_id, {"outcome": "dismissed"})
            return
        if reply.ended:
            generation.stopped = True
        if reply.outcome != interaction.ANSWER:
            # Dismissed, timed out, stopped or the link went: the model is told
            # nobody answered, and carries on with its own judgement.
            await self._answer(message_id, {"outcome": "dismissed"})
            return
        await self._answer(
            message_id, {"outcome": "answered", "answers": [_answer_of(i, reply) for i in items]}
        )

    async def _plan(self, message_id: Any, params: dict[str, Any]) -> None:
        """`_lembas/plan`: the device's plan_exit -- the plan shown,
        and approved into Edit or Manual, sent back with what to change, or
        dismissed."""
        from lembas.services.markdown import render_markdown

        session_id = str(params.get("sessionId") or "")
        generation = self.writer(session_id)
        patient = _patient(params)
        if generation is None:
            await self._nobody_here(message_id, patient)
            return
        text = str(params.get("text") or "")[:MAX_PLAN_CHARS]
        item = interaction.Item(
            index=0,
            key="plan",
            kind=interaction.KIND_PLAN,
            tool_name="plan_exit",
            title="The plan",
            detail=str(params.get("path") or "")[:1000],
            detail_html=render_markdown(text) if text.strip() else "",
            allow_free_text=False,
        )
        timeout = PATIENT_WAIT if patient else ASK_WAIT
        call_id = str(params.get("toolCallId") or "")
        card = interaction.build(uuid.uuid4().hex, [item], timeout=timeout)
        card.tool_call_id = call_id
        card.dismissable = True
        reply = await self._ask(generation, card, session_id, call_id, timeout=timeout)
        if reply.ended:
            generation.stopped = True
        if reply.permitted:
            mode = (reply.answers.get(item.key) or "edit").strip().lower()
            await self._answer(
                message_id,
                {"outcome": "approve", "mode": mode if mode in ("edit", "manual") else "edit"},
            )
        elif reply.outcome == interaction.DENY and reply.reason:
            await self._answer(message_id, {"outcome": "revise", "feedback": reply.reason})
        else:
            await self._answer(message_id, {"outcome": "dismissed"})

    async def _nobody_here(self, message_id: Any, patient: bool) -> None:
        """A question or a plan for a session no reply here is writing: patient
        (somebody may answer at the terminal) is an error, so the terminal's
        answer is the one that counts; otherwise dismissed."""
        if patient:
            await self._answer(message_id, error="no reply here to show the question on")
        else:
            await self._answer(message_id, {"outcome": "dismissed"})


# How long a card waits (one place for all three kinds): an approval,
# question or plan nobody answers here is a denial, a dismissal, after fifteen
# minutes -- unless the device says somebody may answer at its terminal.
ASK_WAIT = 900.0
PATIENT_WAIT = 7 * 86400.0
# A plan longer than this is cut on the card; the device keeps the whole file.
MAX_PLAN_CHARS = 60_000


def _patient(params: dict[str, Any]) -> bool:
    return bool(((params.get("_meta") or {}).get("lembas") or {}).get("patient"))


def _status_for_cards(generation, shown=None) -> None:
    """The status line while cards are open: the newest one's, or, once it is
    answered, the next still open -- never blank while one waits."""
    open_cards = [c for c in generation.waiting if c is not shown]
    if shown is not None:
        open_cards.append(shown)
    generation.status = interaction.summarise(open_cards[-1].items) if open_cards else ""
    generation.touch()


def _answer_of(item, reply) -> dict[str, Any]:
    """One question's answer in the device's shape (`src/tool/question.ts`):
    the labels ticked, or what was written in "Something else", or
    `{kind: "skipped"}` for a question left blank -- never null, which the
    device reads as the whole card dismissed."""
    picked = reply.picks.get(item.key) or []
    labels = [p for p in picked if p != interaction.OTHER]
    written = ((reply.picks.get(f"text.{item.key}") or [""])[0]).strip()
    if written and (interaction.OTHER in picked or not item.options):
        if labels:
            return {"kind": "custom", "text": ", ".join([*labels, written])}
        return {"kind": "custom", "text": written}
    if labels:
        return {"kind": "options", "labels": labels}
    return {"kind": "skipped"}


class LinkClosed(Exception):
    pass


def short_name(link: Link) -> str:
    """What a device is called where room is short: its host, as its hello says
    it (`device-1`), else the name it signed in under."""
    return link.host or link.name


def token_hosts(db, user_id: str, token_ids: list[str]) -> dict[str, str]:
    """token id -> the host its login named (`device_host`), for this person's
    own device tokens only: what `local_device` compares with the computer a
    page is shown on."""
    from sqlalchemy import select

    from lembas.db.models import TOKEN_DEVICE, ApiToken

    if not token_ids:
        return {}
    rows = db.execute(
        select(ApiToken.id, ApiToken.device_host).where(
            ApiToken.id.in_(token_ids),
            ApiToken.user_id == user_id,
            ApiToken.kind == TOKEN_DEVICE,
        )
    )
    return {token_id: host or "" for token_id, host in rows}


class DeviceError(Exception):
    """The device refused, in its own words."""


LINKS: dict[str, Link] = {}

# (device token, session id) deleted on either side: an announcement or
# a mirrored turn of one never brings its chat back. For as long as this process
# runs, which is longer than a device keeps announcing a session it deleted.
DELETED: set[tuple[str, str]] = set()


# What one device reply may make this server hold. A device is a
# signed-in program, not an attacker by default -- but one that is buggy, or a
# token that leaked, must not be able to grow a reply, or the arguments of a
# tool call it never finishes, until the instance runs out of memory or disk.
MAX_REPLY_CHARS = 2_000_000
MAX_ARGS_CHARS = 64_000
MAX_CHATS_PER_DEVICE = 2_000


def _room(generation, text: str) -> bool:
    """Whether this much more may still be kept, counted as it arrives."""
    held = generation.device_chars + len(text)
    generation.device_chars = held
    return held <= MAX_REPLY_CHARS


def _bounded_args(args):
    try:
        size = len(json.dumps(args))
    except (TypeError, ValueError):
        return None
    return args if size <= MAX_ARGS_CHARS else {"truncated": f"{size} characters"}


def refusal(db, user, token_id: str | None = None) -> str:
    """Why this person may not run an agent turn on a device now, or "".

    The same gates a chat is created behind -- the instance switch (Admin ->
    Agents), `tools.agent`, `api.use` -- and, given a token, that it is still
    theirs and live. Asked again on every turn, not only when the chat was made:
    an administrator who switches agent chats off, or takes the permission away,
    means the chats that already exist too.
    """
    from lembas.db.models import TOKEN_DEVICE, ApiToken
    from lembas.security import api_tokens, permissions
    from lembas.services import settings_store

    if user is None or not getattr(user, "active", True):
        return i18n.t("This account cannot run agent chats.")
    if settings_store.api_only(db):
        # An API-only instance runs no chats of any kind; a linked
        # device stays linked, and its terminal's sessions are not taken in.
        return i18n.t("This instance serves the API only; agent chats are switched off.")
    if not settings_store.agents(db).get("enabled"):
        return i18n.t("Agent chats are switched off on this instance.")
    if not permissions.has(db, user, "tools.agent") or not permissions.has(db, user, "api.use"):
        return i18n.t("You no longer have permission to run agent chats.")
    if token_id:
        token = db.get(ApiToken, token_id)
        if (
            token is None
            or token.user_id != user.id
            or token.kind != TOKEN_DEVICE
            or not api_tokens.live(token)
        ):
            return i18n.t("This chat's device is no longer signed in as you.")
    return ""


def link_for(token_id: str | None) -> Link | None:
    link = LINKS.get(token_id or "")
    return link if link is not None and not link.closed else None


def linked_for(user_id: str) -> list[Link]:
    return [link for link in LINKS.values() if link.user_id == user_id and not link.closed]


async def register(link: Link) -> None:
    """A device dialled in: drop any earlier link of the same device, and open ACP."""
    earlier = LINKS.get(link.token_id)
    if earlier is not None and earlier is not link:
        earlier.dropped()
        with contextlib.suppress(Exception):
            await earlier.close(1000, "replaced by a newer link")
    LINKS[link.token_id] = link


def unregister(link: Link) -> None:
    link.dropped()
    if LINKS.get(link.token_id) is link:
        del LINKS[link.token_id]


async def handshake(link: Link) -> None:
    """After the device's hello: `initialize`, asking for the full event stream."""
    link.initialized = await link.request(
        "initialize",
        {
            "protocolVersion": ACP_VERSION,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
                # An object, not `true`: `protocol` tells a LLeMbas CLI
                # that this client answers `_lembas/question` and
                # `_lembas/plan`; a bare `_meta.lembas` does not promise that.
                "_meta": {"lembas": {"protocol": PROTOCOL}},
            },
        },
        timeout=HELLO_WAIT,
    )


# --- What goes with a prompt ----------------------------------------------
# The contract's limits: a block, and a whole prompt, past these is refused by the
# device -- so it is refused here first, with a sentence, before anything is sent.
MAX_BLOCK_BYTES = 10 * 1024 * 1024
MAX_PROMPT_BYTES = 25 * 1024 * 1024
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")


class TooLarge(Exception):
    """The files attached to a device prompt are over the contract's limits."""


def _uri(name: str) -> str:
    from urllib.parse import quote

    return "attachment:" + quote(name or "file", safe="._-")


def prompt_blocks(link: Link, text: str, attachments: list[dict[str, Any]]) -> list[dict]:
    """The prompt as ACP content blocks: the text, then each attached file.

    `attachments` are `{name, kind, media_type, path, text, size}` as
    `generation._device_turn` reads them off the message. To a device listing
    `attachments`: an image as an `image` block, a file's text as a text
    `resource`, a file with no text as a `blob` resource. To an older one, the
    text of a file still goes -- `embeddedContext` is plain ACP, and every LLeMbas
    CLI has taken it -- and what it cannot take is said in the reply instead of
    sent and lost. Over the limits nothing is sent at all (TooLarge)."""
    import base64 as b64
    from pathlib import Path

    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    left_out: list[str] = []
    missing: list[str] = []
    # The files only, as the device counts them: the text is not an attachment.
    total = 0
    full = link.can("attachments")
    for item in attachments:
        name = str(item.get("name") or "file")
        media = str(item.get("media_type") or "")
        body: bytes = b""
        if item.get("kind") == "image" or (media.startswith("image/") and not item.get("text")):
            if not full:
                left_out.append(name)
                continue
            if not _readable(item):
                missing.append(name)
                continue
            body = Path(item["path"]).read_bytes()
            if media in IMAGE_TYPES:
                block = {"type": "image", "data": b64.b64encode(body).decode(),
                         "mimeType": media, "uri": _uri(name)}
            else:
                # An SVG, a BMP...: not an image block a model takes, but still
                # the file -- a blob resource, as the spec says.
                block = {"type": "resource", "resource": {
                    "uri": _uri(name), "mimeType": media or "application/octet-stream",
                    "blob": b64.b64encode(body).decode()}}
        elif item.get("text"):
            if not (full or link.takes("embeddedContext")):
                left_out.append(name)
                continue
            body = str(item["text"]).encode()
            block = {"type": "resource", "resource": {
                "uri": _uri(name), "mimeType": "text/plain", "text": str(item["text"])}}
        else:
            if not full:
                left_out.append(name)
                continue
            if not _readable(item):
                missing.append(name)
                continue
            body = Path(item["path"]).read_bytes()
            block = {"type": "resource", "resource": {
                "uri": _uri(name), "mimeType": media or "application/octet-stream",
                "blob": b64.b64encode(body).decode()}}
        if len(body) > MAX_BLOCK_BYTES:
            raise TooLarge(
                f"{name} is {len(body) / 1048576:.1f} MB; a device takes at most "
                f"{MAX_BLOCK_BYTES // 1048576} MB per file. Nothing was sent."
            )
        total += len(body)
        if total > MAX_PROMPT_BYTES:
            raise TooLarge(
                f"These files come to more than {MAX_PROMPT_BYTES // 1048576} MB, the most a "
                "device takes with one message. Nothing was sent: send fewer at a time."
            )
        blocks.append(block)
    note = ""
    if left_out:
        note += (
            "\n\n(Attached in the web UI but not sent: " + ", ".join(left_out)
            + " -- this device's LLeMbas CLI does not take attachments. Update it there.)"
        )
    if missing:
        note += (
            "\n\n(Attached in the web UI but not sent: " + ", ".join(missing)
            + " -- the file is missing from this instance's storage.)"
        )
    if note:
        blocks[0] = {"type": "text", "text": text + note}
    return blocks


def _readable(item: dict[str, Any]) -> bool:
    """Whether an attachment's stored file is there to read."""
    from pathlib import Path

    path = str(item.get("path") or "")
    return bool(path) and Path(path).is_file()


def attachment_chips(raw: Any) -> list[dict[str, Any]]:
    """A terminal turn's `attachments` as kept for its chips: names and sizes
    only -- the files themselves are on the device."""
    chips: list[dict[str, Any]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            continue
        size = entry.get("size")
        # A finite, non-negative number or 0: `1e999` is valid JSON to Python
        # and reads as inf, which `int()` refuses with an OverflowError.
        if not (isinstance(size, int | float) and math.isfinite(size) and size >= 0):
            size = 0
        chips.append({
            "name": str(entry["name"])[:300],
            "mimeType": str(entry.get("mimeType") or "")[:100],
            "size": int(size),
        })
    return chips[:20]


# --- Commands ----------------------------------------------------------------
MAX_COMMANDS = 200
COMMAND_KINDS = ("custom", "skill", "builtin")


def clean_commands(raw: Any) -> list[dict[str, Any]]:
    """`availableCommands` as the composer's `/` menu offers them: a name it can
    run, a line of description, an argument hint, and what kind of command it
    is. Everything else -- and anything not shaped so -- is dropped."""
    import re

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip().lstrip("/")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}", name) or name in seen:
            continue
        seen.add(name)
        given = entry.get("input")
        hint = given.get("hint") if isinstance(given, dict) else ""
        kind = str((((entry.get("_meta") or {}).get("lembas") or {}).get("kind")) or "")
        out.append({
            "name": name,
            "description": str(entry.get("description") or "").strip()[:300],
            "hint": str(hint or "").strip()[:120],
            "kind": kind if kind in COMMAND_KINDS else "",
        })
        if len(out) >= MAX_COMMANDS:
            break
    return out


# --- A reply, written from the device's events -----------------------------------
TODO_STATUSES = ("pending", "in_progress", "completed", "cancelled")


def todo_items(raw: Any) -> list[dict[str, str]]:
    """A todo list as the device sends it, kept to the spec's shape."""
    items: list[dict[str, str]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not str(entry.get("content") or "").strip():
            continue
        status = str(entry.get("status") or "pending")
        items.append(
            {
                "content": str(entry["content"]).strip()[:500],
                "status": status if status in TODO_STATUSES else "pending",
            }
        )
    return items[:30]


def _mark_steered(generation, event: dict[str, Any]) -> None:
    """The device's model took in messages sent from here: marked into the reply
    at this point. By the ids the device hands back; when it names
    none, they go in the order they were sent."""
    ids = [str(i) for i in event.get("ids") or [] if str(i) in generation.sent_steers]
    texts = [str(t) for t in event.get("texts") or [] if str(t).strip()]
    if not ids and not generation.sent_steers:
        # Typed in the device's terminal while it worked: shown as it is there.
        generation.mark_steer([{"id": "", "text": t} for t in texts])
        return
    if not ids:
        ids = list(generation.sent_steers)[: len(texts)]
    generation.mark_steer(
        [
            {"id": i, "text": generation.sent_steers.pop(i)}
            for i in ids
            if i in generation.sent_steers
        ]
    )


def _think(generation) -> None:
    from lembas.services.generation import _thought_at

    generation.device_think_span = _thought_at(generation, generation.device_think_span)


def _close_thinking(generation) -> None:
    """End the round's thinking, adding it to the reply's; its first burst is
    also the reply's `reasoning_ms`, as for a reply written here."""
    span = getattr(generation, "device_think_span", None)
    if span is None:
        return
    burst = int((span[1] - span[0]) * 1000)
    if not generation.reasoning_ms:
        generation.reasoning_ms = burst
    generation.thinking_ms += burst
    generation.round_thinking_ms = 0
    generation.device_think_span = None


def apply_event(generation, event: dict[str, Any]) -> None:
    """One engine event from the device, into the reply being written."""
    kind = event.get("type")
    # How long it thought: measured here as the device's events arrive,
    # the way `_run` measures a reply of its own -- from the first reasoning
    # delta of a round to the last, closed by whatever comes after. Without it
    # a device chat's thinking block said only how many tokens it held.
    if kind == "reasoning":
        _think(generation)
    elif kind in ("text", "tool_call_delta", "tool_start", "step", "usage", "task", "done"):
        _close_thinking(generation)
    if kind == "text":
        text = str(event.get("text") or "")
        if _room(generation, text):
            generation.content.append(text)
    elif kind == "reasoning":
        text = str(event.get("text") or "")
        if _room(generation, text):
            generation.reasoning.append(text)
    elif kind == "step" and int(event.get("n") or 0) > 1:
        generation.rounds += 1
        generation.close_step()
    elif kind == "done" and event.get("reason") == "cancelled":
        generation.stopped = True
    elif kind == "todos":
        generation.todos = todo_items(event.get("items"))
    elif kind == "retract":
        # The device retries a step: what it streamed is not the reply.
        pass
    elif kind == "tool_start":
        generation.status = f"{event.get('name') or 'tool'}…"
        # What it was called with, for the block it ends as: the
        # command a shell block shows, the path an edit or a read shows.
        generation.device_args[str(event.get("id") or "")] = _bounded_args(event.get("args"))
    elif kind in ("tool_end", "tool_denied"):
        generation.status = ""
        result = event.get("result") or {}
        failed = kind == "tool_denied" or bool(result.get("isError"))
        output = str(result.get("output") or event.get("reason") or "")
        args = generation.device_args.pop(str(event.get("id") or ""), None)
        generation.tool_events.append(
            tool_event(
                str(event.get("name") or "tool"),
                args,
                title=str(result.get("title") or ""),
                output=output,
                failed=failed,
                meta=result.get("meta"),
            )
        )
    elif kind == "usage":
        usage = event.get("usage") or {}
        if not event.get("child"):
            generation.reported_usage = True
            generation.prompt_tokens += int(usage.get("input") or 0)
            generation.completion_tokens += int(usage.get("output") or 0)
            # What the session holds now, and its window, as the device knows
            # them -- the context chip's two numbers. The sum over every
            # step would read, on a long task, as millions of tokens in a 180k
            # window.
            used = int(event.get("used") or 0) or (
                int(usage.get("input") or 0) + int(usage.get("output") or 0)
            )
            if used:
                generation.context_tokens = used
                generation.counted_chars = len(generation.text) + len(generation.thinking)
            if int(event.get("context") or 0) > 0:
                generation.context_limit = int(event["context"])
    elif kind == "steered":
        generation.status = i18n.t("Taking in what you just added…")
        _mark_steered(generation, event)
    elif kind == "notice" and event.get("level") == "warn":
        generation.status = str(event.get("message") or "")[:200]
    elif kind == "error":
        generation.error = str(event.get("message") or i18n.t("The device reported an error."))
    generation.touch()


async def run(
    generation,
    *,
    chat_id: str,
    token_id: str,
    project_dir: str,
    mode: str,
    prompt: str,
    device_name: str,
    model: str = "",
    effort: str = "",
    session_id: str = "",
    mirror: bool = False,
    attach: bool = False,
    message_id: str = "",
    attachments: list[dict] | None = None,
) -> None:
    """Write one reply from the device. Sets `generation.error` on every "no".

    `session_id` is the one the chat kept: used when this link has none
    for the chat yet, so the device carries on the session it already has.
    `mirror`: the turn was typed in the device's terminal, and this reply only
    follows it -- nothing is sent. `attach`: this reply was cut off here
    (an update, a dropped link) while the device went on; it is followed again,
    never sent a second time. `message_id`: the person's message this answers --
    and, to a device that lists `turns`, the turn's id. `attachments`:
    the files sent with it, as `prompt_blocks` reads them."""
    link = link_for(token_id)
    generation.device_where = f"{device_name}:{project_dir}"
    if link is None and (mirror or attach):
        # Just restarted: the device dials in again within seconds.
        link = await _wait_for_link(token_id)
    if link is None:
        generation.error = (
            f"{device_name} is not linked right now. Start it there with "
            "`lembas service run` (or `lembas service install`)."
        )
        return
    if mirror:
        await _follow_mirror(link, generation, chat_id, session_id, device_name)
        return
    try:
        content = prompt_blocks(link, prompt, attachments or [])
    except TooLarge as exc:
        generation.error = str(exc)
        return
    # The turn's id is the person's message id: sent with the prompt,
    # so the device dedupes a prompt sent twice and names the turn in its
    # events and its status -- two races nothing else closes.
    if link.can("turns") and message_id:
        generation.turn_id = message_id
        _remember_turn(link, message_id)
    kept = session_id if session_id and link.can("sessions") else ""
    resend = False
    if attach and kept:
        resend = await _reattach(link, generation, chat_id, kept, device_name)
        if not resend:
            return
    current: str | None = link.sessions.get(chat_id) or kept or None
    session_id = ""
    try:
        if current is not None:
            try:
                await _bring_up_to_date(
                    link, generation, chat_id, current, mode, model, effort, device_name
                )
            except DeviceError as exc:
                # Gone on the device (a build that keeps no sessions across a
                # restart, or its store lost it): a new session, said in the reply.
                if "no such session" not in str(exc).lower():
                    raise
                link.sessions.pop(chat_id, None)
                current = None
        if current is None:
            current = await _open(
                link, generation, chat_id, project_dir, mode, model, effort, device_name
            )
        session_id = current
        link.modes[chat_id] = mode
        link.configs[chat_id] = (model, effort)
        # Working already -- a turn typed in its terminal, or one it
        # started by itself: what was typed here joins that turn, as a message
        # typed into the terminal mid-task would, and this reply follows it.
        # Sending it as a prompt was what came back "already working". Not for
        # a turn being sent again: the device queues that one by its id.
        if not resend and session_id in link.busy and message_id and link.can("steer"):
            generation.sent_steers[message_id] = prompt
            if link.can("turns"):
                # The reply follows the turn it joins, not a turn of its own.
                generation.turn_id = link.current.get(session_id, "")
            try:
                await link.request(
                    "_lembas/steer",
                    {"sessionId": session_id, "text": prompt, "messageId": message_id},
                    timeout=15,
                )
            except DeviceError:
                # It finished a moment ago: an ordinary prompt after all.
                generation.sent_steers.pop(message_id, None)
                generation.turn_id = message_id if link.can("turns") else ""
            except TimeoutError:
                # No answer in time is not a refusal: the link delivers in
                # order, so the device almost surely has it and answered late.
                # Sending it again as a prompt ran the same message twice.
                await _follow(link, generation, chat_id, session_id)
                return
            else:
                await _follow(link, generation, chat_id, session_id)
                return
        await _prompt(link, generation, chat_id, session_id, content, device_name)
    except DeviceError as exc:
        generation.error = f"{device_name} refused: {exc}"
    except LinkClosed as exc:
        link.sessions.pop(chat_id, None)
        link.writing.pop(session_id, None)
        # A link that blinks does not end the device's turn: followed
        # again when it is back, rather than ended with an error here.
        again = await _wait_for_link(token_id) if session_id and not generation.cancel else None
        if again is not None and again.can("status"):
            try:
                if await _reattach(again, generation, chat_id, session_id, device_name):
                    # The device never had it: sent again, by its id.
                    await _prompt(again, generation, chat_id, session_id, content, device_name)
            except (DeviceError, LinkClosed) as again_exc:
                generation.error = str(again_exc)
            finally:
                if again.writing.get(session_id) is generation:
                    again.writing.pop(session_id, None)
        else:
            generation.error = str(exc)
    except TimeoutError:
        generation.error = f"{device_name} did not answer in time."
    finally:
        if session_id and link.writing.get(session_id) is generation:
            link.writing.pop(session_id, None)
        _close_thinking(generation)
        generation.status = ""
        generation.touch()


async def _prompt(
    link: Link, generation, chat_id: str, session_id: str, content: list, device_name: str
) -> None:
    """Send the prompt and write the reply from its events until the device
    answers it. Stop here stops it there; a message typed meanwhile joins it."""
    turn = generation.turn_id if link.can("turns") else ""
    link.writing[session_id] = generation
    if turn:
        link.turn_writers[(session_id, turn)] = generation
    params: dict[str, Any] = {"sessionId": session_id, "prompt": content}
    if turn:
        params["_meta"] = {"lembas": {"turnId": turn}}
    try:
        task = asyncio.create_task(link.request("session/prompt", params, timeout=None))
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=0.5)
            if not done and not generation.cancel and link.can("steer"):
                await _pass_on_queued(link, generation, chat_id, session_id)
            if not done and generation.cancel:
                await link.notify("session/cancel", {"sessionId": session_id})
                generation.stopped = True
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(task), 10)
                break
        if task.done() and not task.cancelled():
            result = task.result() or {}
            if result.get("stopReason") == "cancelled":
                generation.stopped = True
            # What the device never took in comes back: queued again, so the
            # drain after this reply sends it as the next turn rather than it
            # being marked delivered and lost.
            said = (result.get("_meta") or {}).get("lembas") or {}
            unsent = said.get("unsent") or []
            if unsent:
                _requeue(generation, len(unsent))
            if said.get("command"):
                # The prompt was one of the device's commands: what it
                # did, when it did it without a turn of the model's to show.
                _command_result(generation, said)
            elif turn and not generation.turn_started and not generation.stopped:
                # Answered without running: the device had finished this
                # turn already -- a prompt sent again after a restart -- and
                # deduped it. Its answer is in the device's history.
                await _import_turn(link, generation, session_id, device_name, turn, strict=True)
    finally:
        if turn and link.turn_writers.get((session_id, turn)) is generation:
            link.turn_writers.pop((session_id, turn), None)


def _command_result(generation, said: dict[str, Any]) -> None:
    """What a `/command` the device ran reports beside its stop reason:
    a summary (`/compact`), what was undone (`/undo`), the mode now (`/plan`
    with no task), or a sentence (a command with nothing to do). Written into
    the reply only when the command left it empty -- a prompt command's own
    turn already wrote one."""
    if generation.text.strip():
        return
    if isinstance(said.get("message"), str) and said["message"].strip():
        text = said["message"].strip()
    elif isinstance(said.get("summary"), str):
        text = "*(The session was compacted.)*\n\n" + said["summary"].strip()
    elif "undone" in said:
        undone = said.get("undone")
        if not isinstance(undone, dict):
            text = "*(Nothing to undo.)*"
        else:
            files = [str(f) for f in undone.get("files") or []][:50]
            text = f"*(Undone: “{str(undone.get('prompt') or '')[:200]}”.)*" + (
                "\n\n" + "\n".join(f"- `{f}`" for f in files) if files else ""
            )
    elif isinstance(said.get("mode"), str):
        text = f"*(The session is in {said['mode']} mode now.)*"
    else:
        return
    generation.content.append(text[:20_000])
    generation.touch()


# How many turn ids sent from here a link remembers: enough to tell the device's
# `prompt` event for one of ours from a terminal's, for as long as one can still
# arrive, without growing for the life of the link.
MAX_SENT_TURNS = 500


def _remember_turn(link: Link, turn: str) -> None:
    link.sent_turns[turn] = None
    while len(link.sent_turns) > MAX_SENT_TURNS:
        link.sent_turns.pop(next(iter(link.sent_turns)))


# How long a reply cut off by a restart or a dropped link waits for the device
# to dial in again before it says so.
RELINK_WAIT = 60.0


async def _wait_for_link(token_id: str, wait: float = RELINK_WAIT) -> Link | None:
    """The device's link once it is up again, or None after `wait` seconds."""
    deadline = asyncio.get_running_loop().time() + wait
    while asyncio.get_running_loop().time() < deadline:
        link = link_for(token_id)
        if link is not None and link.initialized:
            return link
        await asyncio.sleep(1)
    return None


async def _reattach(
    link: Link, generation, chat_id: str, session_id: str, device_name: str
) -> bool:
    """A reply cut off here while the device went on: followed again
    if the turn still runs; if it finished meanwhile, its whole answer from the
    device's history. Never sent a second time -- except to a device that
    lists `turns` and says it has never seen the turn: then it is sent
    again, by its id, which the device dedupes. Returns whether to send it.

    Without turn ids, "idle" could mean the turn ended or that the device had
    not read the prompt yet; the history was imported and the row closed, and
    the prompt then ran with nobody following it. By id, the device says which.
    """
    link.sessions[chat_id] = session_id
    try:
        status = await link.request("_lembas/session/status", {"sessionId": session_id}, timeout=15)
    except (DeviceError, TimeoutError, LinkClosed):
        status = {}
    status = status if isinstance(status, dict) else {}
    turn = generation.turn_id if link.can("turns") else ""
    if not status.get("exists", True):
        generation.error = f"The session is gone from {device_name}."
        return False
    if turn:
        running = str(status.get("turnId") or "")
        queued = [str(t) for t in status.get("queued") or []]
        if running == turn:
            link.busy.add(session_id)
            link.current[session_id] = turn
            await _follow(link, generation, chat_id, session_id)
            return False
        if turn in queued:
            await _await_turn(link, generation, chat_id, session_id, turn)
            return False
        if str(status.get("lastTurnId") or "") == turn:
            await _import_turn(link, generation, session_id, device_name, turn)
            return False
        # Never seen, or finished before the last one: sent again, by its id.
        return True
    if status.get("busy"):
        link.busy.add(session_id)
        await _follow(link, generation, chat_id, session_id)
        return False
    await _import_turn(link, generation, session_id, device_name, "")
    return False


async def _import_turn(
    link: Link, generation, session_id: str, device_name: str, turn: str, *, strict: bool = False
) -> None:
    """A finished turn's whole answer, from the device's history, in place of
    the part seen here: the reply after the user turn with this id when the
    history names turns, else the last reply. `strict`: only by id."""
    try:
        history = await link.request(
            "_lembas/session/history", {"sessionId": session_id}, timeout=30
        )
    except (DeviceError, TimeoutError, LinkClosed) as exc:
        generation.error = f"Could not read how the turn ended on {device_name}: {exc}"
        return
    turns = [t for t in (history or {}).get("turns") or [] if isinstance(t, dict)]
    last = None
    if turn:
        asked = next(
            (n for n, t in enumerate(turns) if t.get("role") == "user" and t.get("turnId") == turn),
            None,
        )
        if asked is not None:
            last = next((t for t in turns[asked + 1 :] if t.get("role") != "user"), None)
    if last is None and not strict:
        last = turns[-1] if turns and turns[-1].get("role") != "user" else None
    if last is None:
        return
    generation.content = [str(last.get("text") or "")]
    generation.reasoning = [str(last.get("reasoning") or "")]
    generation.tool_events = [
        _tool_event(t, generation.device_where)
        for t in last.get("tools") or []
        if isinstance(t, dict)
    ]
    generation.steps = []
    generation.touch()


async def _await_turn(link: Link, generation, chat_id: str, session_id: str, turn: str) -> None:
    """A turn the device has queued: followed from the moment its
    `task` start arrives -- which `_notification` buffers for it -- not before,
    so the events of the turn running ahead of it are not written into it."""
    started = asyncio.Event()
    link.queued_turns[(session_id, turn)] = started
    try:
        while not started.is_set() and not link.closed and not generation.cancel:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(started.wait(), 0.5)
    finally:
        link.queued_turns.pop((session_id, turn), None)
    if link.closed:
        generation.error = "The device's link dropped."
        return
    if not started.is_set():
        # Stopped here before it began: the device is told, and the reply ends.
        generation.stopped = True
        await link.notify("session/cancel", {"sessionId": session_id})
        return
    await _follow(link, generation, chat_id, session_id)


async def _follow(link: Link, generation, chat_id: str, session_id: str) -> None:
    """Write this reply from a turn the device is running, until it ends. Stop
    here stops it there; a message typed here meanwhile joins it."""
    done: asyncio.Future = asyncio.get_running_loop().create_future()
    link.mirrors[session_id] = done
    link.writing[session_id] = generation
    link.sessions[chat_id] = session_id
    turn = generation.turn_id if link.can("turns") else ""
    if turn:
        link.turn_writers[(session_id, turn)] = generation
    try:
        for event in link.buffered.pop(session_id, []):
            apply_event(generation, event)
            if _ends_task(event) and not done.done():
                done.set_result(None)
        if session_id not in link.busy and not done.done():
            # It ended between the question and now.
            done.set_result(None)
        while not done.done():
            await asyncio.wait({done}, timeout=0.5)
            if done.done():
                break
            if generation.cancel:
                generation.stopped = True
                await link.notify("session/cancel", {"sessionId": session_id})
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(done), 10)
                break
            if link.can("steer"):
                await _pass_on_queued(link, generation, chat_id, session_id)
        if done.done() and not done.cancelled() and done.exception() is not None:
            generation.error = str(done.exception())
    finally:
        link.mirrors.pop(session_id, None)
        if link.writing.get(session_id) is generation:
            link.writing.pop(session_id, None)
        if turn and link.turn_writers.get((session_id, turn)) is generation:
            link.turn_writers.pop((session_id, turn), None)
        link.buffered.pop(session_id, None)
        _close_thinking(generation)
        generation.status = ""
        generation.touch()


async def _open(
    link: Link,
    generation,
    chat_id: str,
    project_dir: str,
    mode: str,
    model: str,
    effort: str,
    device_name: str,
) -> str:
    """A new session on the device for this chat; its id is kept on the chat."""
    meta: dict[str, Any] = {"mode": mode}
    if model:
        meta["lembas_model"] = model
    if effort:
        meta["effort"] = effort
    opened = await link.request(
        "session/new", {"cwd": project_dir, "mcpServers": [], "_meta": meta}, timeout=60
    )
    session_id = str(opened.get("sessionId") or "")
    link.sessions[chat_id] = session_id
    _keep_session_id(chat_id, session_id)
    # What the device actually started on. One that does not take the
    # chat's model uses its own default: said in the reply
    # rather than shown under a name that is not true.
    running = str(((opened.get("_meta") or {}).get("lembas") or {}).get("model") or "")
    if model and running and not running.endswith("/" + model):
        generation.content.append(
            f"*({device_name} answered on its own model, {running}: its LLeMbas CLI "
            "does not take the chat's model. Update it there.)*\n\n"
        )
    if link_restarted(generation):
        generation.content.append(
            "*(The device's session started afresh: it does not remember the "
            "turns before this one.)*\n\n"
        )
    return session_id


async def _bring_up_to_date(
    link: Link,
    generation,
    chat_id: str,
    session_id: str,
    mode: str,
    model: str,
    effort: str,
    device_name: str,
) -> None:
    """The mode, model and effort the composer shows, sent before the prompt
    when they changed since the last one (the device may still lower the mode)."""
    if mode and link.modes.get(chat_id) != mode:
        await link.request(
            "session/set_mode", {"sessionId": session_id, "modeId": mode}, timeout=30
        )
    if (model or effort) and link.configs.get(chat_id) != (model, effort):
        try:
            await link.request(
                "_lembas/configure",
                {"sessionId": session_id, "model": model, "effort": effort},
                timeout=30,
            )
        except DeviceError as exc:
            if "not found" not in str(exc).lower():
                raise
            generation.content.append(
                f"*({device_name}'s LLeMbas CLI cannot "
                "change model or effort mid-chat; it keeps the ones it started "
                "with.)*\n\n"
            )
    link.sessions[chat_id] = session_id


def _keep_session_id(chat_id: str, session_id: str) -> None:
    from lembas.db.models import Chat
    from lembas.db.session import session_scope

    with session_scope() as db:
        chat = db.get(Chat, chat_id)
        if chat is not None and chat.device_session_id != session_id:
            chat.device_session_id = session_id
            db.commit()


# --- A session the device's terminal shares -----------------------------
ORIGIN_TERMINAL = "terminal"


def _ends_task(event: dict[str, Any]) -> bool:
    return event.get("type") == "task" and event.get("state") == "end"


def _settle_mirror(link: Link, session_id: str) -> None:
    done = link.mirrors.get(session_id)
    if done is not None and not done.done():
        done.set_result(None)


async def _follow_mirror(
    link: Link, generation, chat_id: str, session_id: str, device_name: str
) -> None:
    """Write the reply to a turn typed in the device's terminal, from its events,
    until the terminal's task ends. Stop here stops it there; a message typed
    here meanwhile joins it there, as one typed in the terminal would."""
    if session_id not in link.buffered:
        # Not started by the terminal's event on this link: an instance that
        # restarted part way through a turn it was following. Followed again
        # from the device's own word, instead of given up on.
        if link.can("status"):
            if link.can("turns"):
                # The turn it follows is whichever the device runs now.
                generation.turn_id = link.current.get(session_id, "")
            await _reattach(link, generation, chat_id, session_id, device_name)
            return
        generation.error = (
            f"This turn ran in the terminal on {device_name}; its reply was not "
            "followed to the end here."
        )
        return
    # The terminal's turn, by its id: its events are routed here.
    generation.turn_id = link.mirror_turns.pop(session_id, "") if link.can("turns") else ""
    await _follow(link, generation, chat_id, session_id)


def _chat_for(db, link: Link, session_id: str):
    """The chat showing this device session, if one does."""
    from sqlalchemy import select

    from lembas.db.models import Chat

    return db.scalars(
        select(Chat).where(
            Chat.user_id == link.user_id,
            Chat.device_token_id == link.token_id,
            Chat.device_session_id == session_id,
        )
    ).first()


def _instance_ref(link: Link, ref: str, instance: Any, *, strict: bool = False) -> str:
    """The served id a device's model reference names on this instance, or ""
    when the reference is the device's own model.

    A CLI may say so itself: `instance: true` on its `model` event and
    its announce means the reference *is* the served id
    (`deepseek/deepseek-flash`), and `instance: false` means it is not ours,
    whatever it looks like. When it says nothing, this rule
    holds: the reference is `<the device's name for this instance>/<served id>`,
    and a prefix that is not that name is the device's own. `strict` is
    `_remodel`'s reading of that rule, which wants the prefix present and named.
    """
    ref = (ref or "").strip()
    if not ref or instance is False:
        return ""
    if instance is True:
        return ref
    prefix, _, name = ref.partition("/")
    if not name:
        return "" if strict else ref
    if link.connection:
        return name if prefix == link.connection else ""
    return "" if strict else name


def _model_for(db, user, wanted: str) -> tuple[str, str] | None:
    """The account's model a served id names (`deepseek/deepseek-flash`), as
    this instance's (model id, connection id). The older form -- the bare
    upstream id -- still finds the model it names."""
    from lembas.api.v1.openai import find

    if not wanted or user is None:
        return None
    served = find(db, user, wanted)
    return (served.model.model_id, served.connection.id) if served is not None else None


def chat_config(db, chat) -> tuple[str, str]:
    """The chat's model by the name this instance's /v1 serves it under -- the
    name the device's webui connection knows it by -- and its effort."""
    from lembas.api.v1.openai import served_models
    from lembas.db.models import User
    from lembas.services import chat as chat_service

    if getattr(chat, "device_model", ""):
        # Switched on the device to a model of its own: no model is
        # sent, or the next prompt would switch it back.
        return "", chat_service.resolved_effort(chat) or "off"
    owner = db.get(User, chat.user_id)
    served = next(
        (
            s.id
            for s in (served_models(db, owner) if owner is not None else [])
            if s.model.model_id == chat.model_id
            and (not chat.connection_id or s.connection.id == chat.connection_id)
        ),
        chat.model_id or "",
    )
    return served, chat_service.resolved_effort(chat) or "off"


def _apply_announced(db, link: Link, chat, info: dict[str, Any]) -> None:
    """What the terminal says the session runs on, onto the chat: its mode, and
    its model and effort where this instance serves that model. Remembered as
    already sent, so the next prompt from here does not switch the terminal's
    session to something else."""
    from lembas.db.models import User
    from lembas.services import chat as chat_service
    from lembas.services.agent import policy as agent_policy

    mode = str(info.get("mode") or "")
    # Not for a temporary chat, which the device runs at Plan whatever was
    # chosen (as `_remode`): Keep this chat gives back the chosen mode.
    if mode in agent_policy.MODES and not chat.temporary:
        chat.agent_mode = mode
    # Only a model from the login's own connection is this instance's: the
    # device's `ollama/qwen3` is not a `qwen3` served here (`_instance_ref`).
    wanted = _instance_ref(link, str(info.get("model") or ""), info.get("instance"))
    mapped = _model_for(db, db.get(User, chat.user_id), wanted)
    if mapped is not None:
        chat.model_id, chat.connection_id = mapped
    effort = str(info.get("effort") or "").lower()
    if effort == "off":
        chat.params_json = {
            k: v for k, v in (chat.params_json or {}).items() if k != "reasoning_effort"
        }
    elif effort in chat_service.EFFORTS:
        chat.params_json = {**(chat.params_json or {}), "reasoning_effort": effort}
    db.commit()
    link.sessions[chat.id] = str(chat.device_session_id or "")
    link.modes[chat.id] = chat.agent_mode
    link.configs[chat.id] = chat_config(db, chat)


def _adopt_chat(db, link: Link, session_id: str):
    """An agent chat for a session the terminal shares, or None when this account
    may not have one on this device."""
    from lembas.api.chats import _device_target, _new_chat
    from lembas.db.models import KIND_AGENT, User

    if (link.token_id, session_id) in DELETED:
        return None
    user = db.get(User, link.user_id)
    if refusal(db, user):
        # API-only, agent chats off, or the permission gone: a terminal's
        # session is not made into a chat this instance would refuse to run.
        return None
    if user is None or _device_target(db, user, KIND_AGENT, f"device:{link.token_id}") is None:
        return None
    # Each announced session is a chat; a device announcing without end must not
    # fill the database with them.
    from sqlalchemy import func, select

    from lembas.db.models import Chat

    held = db.scalar(
        select(func.count()).select_from(Chat).where(Chat.device_token_id == link.token_id)
    )
    if (held or 0) >= MAX_CHATS_PER_DEVICE:
        log.warning("device %s announced a session past %d chats; not adopted",
                    link.name, MAX_CHATS_PER_DEVICE)
        return None
    info = link.announced.get(session_id) or {}
    mapped = _model_for(
        db, user, _instance_ref(link, str(info.get("model") or ""), info.get("instance"))
    )
    chat = _new_chat(
        db,
        user,
        model_id=mapped[0] if mapped else "",
        kind=KIND_AGENT,
        ssh_profile_id=f"device:{link.token_id}",
        project_dir=str(info.get("cwd") or ""),
        agent_mode=str(info.get("mode") or ""),
    )
    chat.device_session_id = session_id
    title = str(info.get("title") or "").strip()
    if title:
        chat.title = title[:300]
        chat.title_generated = True
    # Announced in the sidebar like a reply that arrived: a dot, and a toast.
    chat.unread = True
    db.commit()
    _apply_announced(db, link, chat, info)
    return chat


# Argument values longer than this are cut in the stored event: a block shows a
# command or a path, and a `write` call's whole file is in its diff anyway.
ARG_CHARS = 2000
# A diff longer than this is cut: the block shows its densest dozen lines.
DIFF_CHARS = 200_000


def _small_args(args: Any) -> dict[str, Any]:
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            return {}
    if not isinstance(args, dict):
        return {}
    kept: dict[str, Any] = {}
    for key, value in list(args.items())[:20]:
        if isinstance(value, str):
            kept[str(key)] = value[:ARG_CHARS]
        elif isinstance(value, int | float | bool) or value is None:
            kept[str(key)] = value
    return kept


def tool_event(
    name: str,
    args: Any,
    *,
    title: str = "",
    output: str = "",
    failed: bool = False,
    meta: Any = None,
) -> dict[str, Any]:
    """A device's tool call as the transcript keeps it: what it was
    called with and what it reported beside its output -- a diff, an exit code,
    a path, a number of hits -- so each kind is drawn as the CLI draws it. No
    host or directory line: the chat says where it works."""
    small = _small_args(args)
    meta = meta if isinstance(meta, dict) else {}
    query = title or name
    if not title:
        query = str(
            small.get("command")
            or small.get("path")
            or small.get("file_path")
            or small.get("pattern")
            or small.get("url")
            or small.get("query")
            or name
        )
    event: dict[str, Any] = {
        "name": name,
        "kind": "agent",
        "query": query[:500],
        "args": small,
        "results": [],
        "text": output[:20_000],
        "status": "error" if failed else "ok",
    }
    if failed:
        event["error"] = output[:2000]
    if isinstance(meta.get("diff"), str) and meta["diff"]:
        event["diff"] = meta["diff"][:DIFF_CHARS]
    if isinstance(meta.get("exit"), int):
        event["exit"] = meta["exit"]
    if isinstance(meta.get("hits"), int):
        event["hits"] = meta["hits"]
    if isinstance(meta.get("path"), str):
        span = ""
        if isinstance(meta.get("from"), int) and isinstance(meta.get("to"), int):
            span = f" · {meta['from']}–{meta['to']}"
            if isinstance(meta.get("of"), int):
                span += f" of {meta['of']}"
        event["path"] = meta["path"] + span
    return event


def _tool_event(tool: dict[str, Any], where: str = "") -> dict[str, Any]:
    """A tool call from the device's stored history, in the shape `apply_event`
    writes one live."""
    return tool_event(
        str(tool.get("name") or "tool"),
        tool.get("args"),
        output=str(tool.get("output") or ""),
        failed=bool(tool.get("isError")),
        meta=tool.get("meta"),
    )


def _import_turns(db, chat, turns: list[dict], device_name: str) -> None:
    """A terminal session's turns before it was shared, as the chat's history."""
    from datetime import UTC, datetime, timedelta

    from lembas.db.models import ROLE_ASSISTANT, ROLE_USER, Message

    where = f"{device_name}:{chat.project_dir}"
    start = datetime.now(UTC) - timedelta(milliseconds=len(turns) + 1)
    for n, turn in enumerate(turns):
        when = start + timedelta(milliseconds=n)
        if turn.get("role") == "user":
            row = Message(
                chat_id=chat.id,
                role=ROLE_USER,
                content=str(turn.get("text") or ""),
                origin=ORIGIN_TERMINAL,
                created_at=when,
                # What the turn had attached there, as chips.
                terminal_attachments_json=attachment_chips(turn.get("attachments")) or None,
            )
        else:
            row = Message(
                chat_id=chat.id,
                role=ROLE_ASSISTANT,
                content=str(turn.get("text") or ""),
                reasoning=str(turn.get("reasoning") or ""),
                tool_calls_json=[
                    _tool_event(t, where) for t in turn.get("tools") or [] if isinstance(t, dict)
                ],
                model_id=chat.model_id,
                created_at=when,
            )
        db.add(row)
    db.commit()


def _without_last_prompt(turns: list[dict]) -> list[dict]:
    """The history without the turn now starting: its prompt is mirrored live."""
    for n in range(len(turns) - 1, -1, -1):
        if turns[n].get("role") == "user":
            return turns[:n]
    return turns


async def _announced(link: Link, session_id: str) -> None:
    """A session the terminal shares: the chat showing it brought up to date, or
    one made with its history. An empty session gets its chat at its first
    prompt, so a terminal opened and closed leaves nothing behind."""
    from lembas.db.session import session_scope

    info = link.announced.get(session_id) or {}
    with session_scope() as db:
        chat = _chat_for(db, link, session_id)
        if chat is not None:
            _apply_announced(db, link, chat, info)
            return
    adopting = link.adopting[session_id] = asyncio.Event()
    try:
        try:
            history = await link.request(
                "_lembas/session/history", {"sessionId": session_id}, timeout=30
            )
        except (DeviceError, LinkClosed, TimeoutError):
            return
        turns = [t for t in (history or {}).get("turns") or [] if isinstance(t, dict)]
        if session_id in link.buffered:
            turns = _without_last_prompt(turns)
        if not turns:
            return
        with session_scope() as db:
            if _chat_for(db, link, session_id) is not None:
                return
            chat = _adopt_chat(db, link, session_id)
            if chat is not None:
                _import_turns(db, chat, turns, short_name(link))
    except Exception:  # noqa: BLE001 - one session that cannot be shown must not end the link
        log.exception("could not take in device session %s", session_id)
    finally:
        adopting.set()
        link.adopting.pop(session_id, None)


def _begin_mirror(
    link: Link, session_id: str, text: str, *, turn: str = "", attachments: Any = None
) -> None:
    """A turn typed in the terminal: hold its events, and make its bubbles."""
    link.buffered[session_id] = []
    if turn:
        link.mirror_turns[session_id] = turn
    # A prompt is a turn running, whether or not its `task` start came first.
    link.busy.add(session_id)
    asyncio.get_running_loop().create_task(
        _mirror_turn(link, session_id, text, turn=turn, attachments=attachment_chips(attachments))
    )


# How long a terminal's turn waits for this session's previous reply to finish
# being written here before it is shown anyway or given up on.
MIRROR_WAIT = 30.0


async def _previous_reply_done(chat_id: str) -> bool:
    """Whether the chat is free for a new reply: none running, or the one
    running has ended on the device and is only being written here -- waited
    for, up to MIRROR_WAIT."""
    from lembas.services import generation as generation_service

    deadline = asyncio.get_running_loop().time() + MIRROR_WAIT
    while True:
        running = generation_service.running_for(chat_id)
        if running is None:
            return True
        if not running.device_turn_over or asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.1)


async def _mirror_turn(
    link: Link, session_id: str, text: str, *, turn: str = "", attachments: list | None = None
) -> None:
    from lembas.db.models import ROLE_ASSISTANT, ROLE_USER, Message
    from lembas.db.session import session_scope
    from lembas.services import chat as chat_service
    from lembas.services import generation as generation_service

    adopting = link.adopting.get(session_id)
    if adopting is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(adopting.wait(), 40)
    if turn:
        # Matched by its id: a turn sent from here -- this instance
        # restarted, and the device ran it before the reply was taken up again
        # -- is that message's, never a terminal turn of its own.
        with session_scope() as db:
            if db.get(Message, turn) is not None:
                link.buffered.pop(session_id, None)
                return
            chat = _chat_for(db, link, session_id)
            chat_id = chat.id if chat is not None else ""
        # The web turn before it may still be finishing here: its turn has
        # ended on the device, so this one waits for it rather than being
        # dropped as a second reply in the chat.
        if chat_id:
            await _previous_reply_done(chat_id)
    ids: tuple[str, str] | None = None
    try:
        with session_scope() as db:
            chat = _chat_for(db, link, session_id) or _adopt_chat(db, link, session_id)
            # One reply at a time in a chat: with one running here (it cannot be
            # this session's, or the event would have gone to it), the terminal's
            # turn is not shown rather than shown twice.
            if chat is not None and generation_service.running_for(chat.id) is None:
                asked = chat_service.create_message(
                    db, chat, ROLE_USER, text, origin=ORIGIN_TERMINAL
                )
                if attachments:
                    asked.terminal_attachments_json = attachments
                reply = chat_service.create_message(
                    db, chat, ROLE_ASSISTANT, "", complete_=False, model_id=chat.model_id
                )
                ids = (chat.id, reply.id)
                link.sessions[chat.id] = session_id
    except Exception:  # noqa: BLE001 - as `_announced`
        log.exception("could not mirror a terminal turn of %s", session_id)
    if ids is None:
        link.buffered.pop(session_id, None)
        return
    generation_service.ensure(*ids)


def _retitle(link: Link, session_id: str, title: str) -> None:
    """The terminal named its session: the chat takes the name."""
    from lembas.db.session import session_scope

    title = title.strip()
    if not title:
        return
    with session_scope() as db:
        chat = _chat_for(db, link, session_id)
        if chat is not None and chat.title != title[:300]:
            chat.title = title[:300]
            chat.title_generated = True
            db.commit()


def _remode(link: Link, session_id: str, mode: str) -> None:
    """The terminal changed the session's mode: the composer shows it."""
    from lembas.db.session import session_scope
    from lembas.services.agent import policy as agent_policy

    if mode not in agent_policy.MODES:
        return
    with session_scope() as db:
        chat = _chat_for(db, link, session_id)
        if chat is not None:
            link.modes[chat.id] = mode
            # A temporary chat is held at Plan (generation._device_turn), which
            # the device reports back; the chat's own mode stays what was chosen.
            if chat.agent_mode != mode and not chat.temporary:
                chat.agent_mode = mode
                db.commit()


def _remodel(link: Link, session_id: str, ref: str, effort: str, instance: Any = None) -> None:
    """The device's session changed model or effort (capability
    `model`): the chat shows it live. A model of this instance's -- the event
    says `instance: true` and `ref` is its served id, or (an older CLI) `ref` is
    `<the device's name for this instance>/<served id>`, and the account may use
    it -- becomes the chat's model; anything else is the device's own, recorded
    as a label ("on the device: <ref>") and the chat's model is no longer sent.
    No row is added to the transcript: the header is what changes."""
    from lembas.db.models import User
    from lembas.db.session import session_scope
    from lembas.services import chat as chat_service

    ref = ref.strip()[:300]
    with session_scope() as db:
        chat = _chat_for(db, link, session_id)
        if chat is None:
            return
        wanted = _instance_ref(link, ref, instance, strict=True)
        mapped = _model_for(db, db.get(User, chat.user_id), wanted) if wanted else None
        if mapped is not None:
            chat.model_id, chat.connection_id = mapped
            chat.device_model = ""
        elif ref:
            chat.device_model = ref
        level = effort.strip().lower()
        if level == "off":
            chat.params_json = {
                k: v for k, v in (chat.params_json or {}).items() if k != "reasoning_effort"
            }
        elif level in chat_service.EFFORTS:
            chat.params_json = {**(chat.params_json or {}), "reasoning_effort": level}
        db.commit()
        # Remembered as sent, so the next prompt does not switch it back.
        link.configs[chat.id] = chat_config(db, chat)


async def _pass_on_queued(link: Link, generation, chat_id: str, session_id: str) -> None:
    """A message typed while the device works, handed to it now: the
    device gives it to its model at the next step, as its own TUI does. The same
    `_inject` an ordinary reply takes one with -- marked delivered first, and the
    reply moved after it so the transcript reads in order."""
    from lembas.services import generation as generation_service

    entry = generation_service._inject(generation, chat_id, False, mark=False)
    if entry is None:
        return
    text = generation_service.entry_text(entry)
    message_id = generation.injected_ids[-1]
    generation.sent_steers[message_id] = text
    try:
        # Its id goes along and comes back in the `steered` event, so
        # the message is shown where the device's model took it in.
        await link.request(
            "_lembas/steer",
            {"sessionId": session_id, "text": text, "messageId": message_id},
            timeout=15,
        )
    except DeviceError:
        # Not taken (the task ended a moment ago): back in the queue, for the
        # drain to send as the next turn.
        _requeue(generation, 1)
    except TimeoutError:
        # Late is not refused: requeued, it was sent a second time.
        log.info("steer for %s answered late; taken as delivered", session_id)


def _requeue(generation, count: int) -> None:
    """Put the last `count` messages handed to the device back in the queue."""
    from lembas.db.models import Message
    from lembas.db.session import session_scope

    ids = generation.injected_ids[-count:] if count else []
    if not ids:
        return
    del generation.injected_ids[-count:]
    for message_id in ids:
        generation.sent_steers.pop(message_id, None)
    with session_scope() as db:
        for message_id in ids:
            message = db.get(Message, message_id)
            if message is not None:
                message.queued = True
        db.commit()


async def _delete_chat_for(link: Link, session_id: str) -> None:
    """The device deleted a session: the chat showing it goes too."""
    from lembas.db.session import session_scope
    from lembas.services import chat as chat_service
    from lembas.services import generation as generation_service

    with session_scope() as db:
        chat = _chat_for(db, link, session_id)
        if chat is None:
            return
        live = generation_service.running_for(chat.id)
        if live is not None:
            generation_service.request_stop(live.message_id)
        link.sessions.pop(chat.id, None)
        chat_service.delete_chats(db, [chat])
        db.commit()


def forget_session(token_id: str | None, session_id: str | None, db=None) -> None:
    """A chat on the device was deleted here: the device deletes the
    session too, in the background -- the chat is gone whatever it answers.

    One attempt on the link open at that moment is not enough: a device that
    is off, or that says no, would keep the session with nothing anywhere to
    say so. So, given the `db` the chat is being deleted in, the request is
    also written down
    (`DeviceSessionDelete`, in the same transaction), sent again each time the
    device links (`send_pending_deletes`), and only crossed off once the device
    has answered it. A refusal or a failure is logged."""
    if not token_id or not session_id:
        return
    if (token_id, session_id) in DELETED:
        return
    # The tombstone first, and whatever happens after: it is what stops the
    # device's next announcement from bringing the chat back as a new one.
    DELETED.add((token_id, session_id))
    if db is not None:
        _remember_delete(db, token_id, session_id)
    link = link_for(token_id)
    if link is not None:
        link.commands.pop(session_id, None)
    if link is None or not link.can("delete"):
        # Not linked, or a device that does not know the request yet: the
        # record above is what reaches it, the next time it links.
        return
    link.announced.pop(session_id, None)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # a sweep in a worker thread: the record goes on the next link
        return
    loop.create_task(_tell_delete(link, session_id))


# How many times a device may decline a delete before this instance stops asking.
# A refusal is usually for good -- the session is outside what the
# device lets this instance reach -- but a device that is upgraded, or whose
# limits are widened, deletes it on a later link, so a few tries are worth it.
MAX_DELETE_ATTEMPTS = 5


def _remember_delete(db, token_id: str, session_id: str) -> None:
    """The pending delete, written in the caller's transaction, so it commits
    or rolls back with the chat it is for. Only for a device token that still
    exists: a revoked one will never link again to be told."""
    from sqlalchemy import select

    from lembas.db.models import ApiToken, DeviceSessionDelete

    if db.get(ApiToken, token_id) is None:
        return
    held = db.scalar(
        select(DeviceSessionDelete).where(
            DeviceSessionDelete.token_id == token_id,
            DeviceSessionDelete.session_id == session_id,
        )
    )
    if held is None:
        db.add(DeviceSessionDelete(token_id=token_id, session_id=session_id))


def _settle_delete(token_id: str, session_id: str, error: str = "") -> None:
    """The device answered a delete: done (no `error`), the record goes; not
    done, it is counted, and goes after `MAX_DELETE_ATTEMPTS`."""
    from sqlalchemy import select

    from lembas.db.models import DeviceSessionDelete
    from lembas.db.session import session_scope

    try:
        with session_scope() as db:
            row = db.scalar(
                select(DeviceSessionDelete).where(
                    DeviceSessionDelete.token_id == token_id,
                    DeviceSessionDelete.session_id == session_id,
                )
            )
            if row is None:
                return
            if not error:
                db.delete(row)
                return
            row.attempts += 1
            row.last_error = error[:2000]
            if row.attempts >= MAX_DELETE_ATTEMPTS:
                log.warning(
                    "device %s: giving up deleting session %s after %d attempts (%s)",
                    token_id, session_id, row.attempts, error,
                )
                db.delete(row)
    except Exception:  # noqa: BLE001 - bookkeeping; the next link asks again
        log.warning("device %s: could not record the delete of %s", token_id, session_id,
                    exc_info=True)


async def _tell_delete(link: Link, session_id: str) -> bool:
    """Ask the device to delete one session, and settle the record by what it
    says. Whatever goes wrong is said in the log: a refusal swallowed whole
    would leave a deleted chat's session on a device with nothing on either
    side to show why."""
    name = short_name(link)
    try:
        await link.request("_lembas/session/delete", {"sessionId": session_id}, timeout=30)
    except DeviceError as e:
        log.warning("device %s refused to delete session %s: %s", name, session_id, e)
        _settle_delete(link.token_id, session_id, f"refused: {e}")
        return False
    except (TimeoutError, LinkClosed) as e:
        log.warning(
            "device %s did not answer deleting session %s (%s); asked again on its next link",
            name, session_id, type(e).__name__,
        )
        _settle_delete(link.token_id, session_id, f"no answer: {type(e).__name__}")
        return False
    except Exception as e:  # noqa: BLE001 - one request on one device's link
        log.warning("deleting session %s on device %s failed", session_id, name, exc_info=True)
        _settle_delete(link.token_id, session_id, f"failed: {e}")
        return False
    # `{deleted: false}` is done too: the device has no such session (any more).
    _settle_delete(link.token_id, session_id)
    return True


def pending_deletes(token_id: str) -> list[str]:
    """The sessions deleted here that this device has not yet deleted."""
    from sqlalchemy import select

    from lembas.db.models import DeviceSessionDelete
    from lembas.db.session import session_scope

    with session_scope() as db:
        return list(
            db.scalars(
                select(DeviceSessionDelete.session_id)
                .where(DeviceSessionDelete.token_id == token_id)
                .order_by(DeviceSessionDelete.created_at)
            )
        )


def tombstone_pending(link: Link) -> None:
    """Before the device can announce anything: every session still waiting to
    be deleted on it is a tombstone again -- the in-memory set does not survive
    a restart of this instance, and without this the first announcement of a
    session deleted here before the restart brought its chat back."""
    try:
        for session_id in pending_deletes(link.token_id):
            DELETED.add((link.token_id, session_id))
    except Exception:  # noqa: BLE001 - a link is not refused over bookkeeping
        log.warning("could not read the pending deletes of %s", link.token_id, exc_info=True)


async def send_pending_deletes(link: Link) -> None:
    """After `initialize`: the deletes this device missed, one by one. A device
    without the `delete` capability is left with them until it has it."""
    if not link.can("delete"):
        return
    try:
        pending = pending_deletes(link.token_id)
    except Exception:  # noqa: BLE001 - as tombstone_pending
        log.warning("could not read the pending deletes of %s", link.token_id, exc_info=True)
        return
    for session_id in pending:
        if link.closed:
            return
        DELETED.add((link.token_id, session_id))
        link.announced.pop(session_id, None)
        if await _tell_delete(link, session_id):
            log.info("device %s: deleted session %s, deleted here while it was away",
                     short_name(link), session_id)


def rename_session(token_id: str | None, session_id: str | None, title: str) -> None:
    """A device chat renamed here: the session on the device takes the name."""
    link = link_for(token_id)
    if link is None or not session_id or not link.can("title"):
        return

    async def tell() -> None:
        with contextlib.suppress(DeviceError, TimeoutError, LinkClosed):
            await link.request(
                "_lembas/session/title", {"sessionId": session_id, "title": title}, timeout=15
            )

    asyncio.get_running_loop().create_task(tell())


async def compact_session(token_id: str | None, session_id: str | None) -> str:
    """/compact on a device chat: the device compacts its own session --
    that is the context its model reads. Returns the summary."""
    link = link_for(token_id)
    if link is None:
        raise DeviceError("the device is not linked right now")
    if not session_id or not link.can("compact"):
        raise DeviceError(
            "the device's LLeMbas CLI cannot compact from here; update it there"
        )
    if session_id in link.busy:
        raise DeviceError("the session is working -- compact it when the reply is done")
    answer = await link.request("_lembas/session/compact", {"sessionId": session_id}, timeout=600)
    return str((answer or {}).get("summary") or "")


def link_restarted(generation) -> bool:
    """Whether this chat had turns before the session just opened."""
    return bool(getattr(generation, "earlier_turns", False))


def reset() -> None:
    for link in list(LINKS.values()):
        link.dropped()
    LINKS.clear()


__all__ = [
    "CLOSE_PROTOCOL",
    "CLOSE_REFUSED",
    "LINKS",
    "PROTOCOL",
    "PROTOCOLS",
    "DeviceError",
    "Link",
    "LinkClosed",
    "apply_event",
    "handshake",
    "link_for",
    "linked_for",
    "register",
    "reset",
    "run",
    "unregister",
]
