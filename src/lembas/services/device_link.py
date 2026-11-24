"""Linked devices: a LLeMbas CLI dials in, and an agent chat runs on it.

A device signed in with the `link` scope and running `lembas service` keeps a
WebSocket open to `/api/devices/link`. LLeMbas is the **ACP client** on it: it opens
a session in a directory on the device (`session/new`), sends the person's message
(`session/prompt`), and draws what comes back. The device is the agent, and runs
its own harness -- the same design as LLeMbas's own agent (the LLeMbas CLI's `harness/`),
on the chat's model (served by this instance; the device refuses a session it
cannot run on it) and with its own `remote:` limits. **Nothing runs on this
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
import binascii
import contextlib
import hashlib
import itertools
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from lembas.services import interaction
from lembas.web import i18n

log = logging.getLogger(__name__)

# The `_lembas` extension's version (harness spec `acp.md` *Versions*). 3 is
# LLeMbas 2.0 and LLeMbas CLI 2.0, and they speak it alone: a hello of 1 or 2
# is closed with CLOSE_PROTOCOL and PROTOCOL_REFUSAL, which a 1.x CLI reads as
# "stop dialling". Within 3, every feature is still gated on a capability the
# device's `initialize` lists.
PROTOCOL = 3
PROTOCOLS = (3,)
PROTOCOL_REFUSAL = "update LLeMbas CLI to 2.0"
ACP_VERSION = 1
HELLO_WAIT = 15.0

# Capability `harness_turns` (harness spec 3.10.0, `acp.md` *A turn the harness
# starts*). A note that reaches an idle session -- a finished job's, a
# subagent's or a team's report -- wakes it: the device's harness starts a turn
# by itself. A device that lists this treats that turn like any other over the
# link: `_lembas/session/status` says `busy` and names it, `_lembas/steer` joins
# it, a `session/prompt` waits behind it, what was steered too late comes back
# as `unsent` on its `task` end, and `_lembas/session/history` marks its user
# turn `machine`. A device that does not list it runs the same turns and says
# the same `task` and `prompt` events, but its status says `busy: false` while
# one runs, it refuses a steer then, and a prompt sent then fails: nothing is
# sent to such a session until the turn's `task` end.
HARNESS_TURNS = "harness_turns"

# Close codes the device reads as "stop dialling": the token is done.
CLOSE_REFUSED = 4403
CLOSE_PROTOCOL = 4400


# What a link keeps about the device's sessions and turns is bounded: a
# device is a signed-in program, and one that invents session and turn ids
# (a bug, a leaked token) must not grow this instance's memory for the life
# of the link. Past each bound the oldest is forgotten.
# Sessions tracked per link (working, in step, asked once): twice the chats a
# device may have here.
MAX_LINK_SESSIONS = 4_000
# Turns whose events are held for a reply not yet following, per link ...
MAX_HELD_TURNS = 64
# ... the events held for one of them (and the characters: a reply's own bound) ...
MAX_HELD_EVENTS = 20_000
# ... sessions being asked about a turn nobody heard start, at once ...
MAX_PROBING = 16
# ... `unsent` kept for a reply that joins a moment later: turns, messages a
# turn, characters a message (a message typed here is far shorter).
MAX_UNSENT_TURNS = 32
MAX_UNSENT_TEXTS = 50
MAX_UNSENT_CHARS = 200_000


class _Recent(dict):
    """A set that keeps its newest `limit` members (insertion order)."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def add(self, key: str) -> None:
        self[key] = None
        while len(self) > self.limit:
            self.pop(next(iter(self)))

    def discard(self, key: str) -> None:
        self.pop(key, None)


def _trim(kept: dict, limit: int) -> None:
    """Forget the oldest entries of an insertion-ordered dict past `limit`."""
    while len(kept) > limit:
        kept.pop(next(iter(kept)))


class _Held(list):
    """One turn's held events, and how many characters they carry."""

    chars = 0


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
    # Shown files that are not text are fetched one at a time (`_show_media`).
    media_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)
    # The sessions a terminal on the device shares: what it said of each
    # (cwd, title, model, mode, effort) ...
    announced: dict[str, dict] = field(default_factory=dict)
    # ... the events of a turn the device started, held until the reply
    # mirroring it is writing (the bubbles are made first): **one list per
    # turn**, (session id, turn id) -> events, "" for a device that names no
    # turns. Never per session: a short turn that ends and the next one
    # starting while the first reply is still being made here would put the
    # second turn's events into the first reply, and leave the second none ...
    buffered: dict[tuple[str, str], list] = field(default_factory=dict)
    # ... the turns whose rows are being made now (`_mirror_turn`), same key ...
    mirroring: dict[tuple[str, str], bool] = field(default_factory=dict)
    # ... and, session id -> the reply started for a message typed here in
    # that moment, which becomes the turn's reply instead of a second one in
    # the chat (`_adopt_mirror`) ...
    adopters: dict[str, Any] = field(default_factory=dict)
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
    busy: _Recent = field(default_factory=lambda: _Recent(MAX_LINK_SESSIONS))
    # Capability `turns`: the turn each session is running now, by the
    # device's `task` start ...
    current: dict[str, str] = field(default_factory=dict)
    # ... the reply writing each turn, (session id, turn id) -> generation, which
    # is what a turn's events are routed to ...
    turn_writers: dict[tuple[str, str], Any] = field(default_factory=dict)
    # ... the replies waiting for a turn the device has queued to start ...
    queued_turns: dict[tuple[str, str], asyncio.Event] = field(default_factory=dict)
    # ... and the turn ids sent from here, so the device's `prompt` event for
    # one is never taken for a turn typed in its terminal.
    # Insertion-ordered, bounded by MAX_SENT_TURNS (oldest dropped first).
    sent_turns: dict[str, None] = field(default_factory=dict)
    # The sessions running a turn their harness started (a `prompt` event with
    # `machine`), and that turn's id ("" when the device names none): until
    # its `task` end. What a device without `harness_turns` is sent nothing
    # during.
    harness_turns: dict[str, str] = field(default_factory=dict)
    # `unsent` of a harness turn's `task` end that found no reply following
    # the turn yet, (session id, turn id) -> texts: taken by the reply that
    # joined it (`_follow`) a moment later.
    unsent: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    # The sessions this link has heard a `task` event of: from then on no turn
    # of theirs starts or ends unheard, and "no turn named" means none runs ...
    synced: _Recent = field(default_factory=lambda: _Recent(MAX_LINK_SESSIONS))
    # ... the sessions not yet in step whose device has been asked, once,
    # whether a turn its harness started before this link was up is still
    # running (`_find_missed_turn`, capability `harness_turns`), and their
    # events held meanwhile ...
    asked: _Recent = field(default_factory=lambda: _Recent(MAX_LINK_SESSIONS))
    probing: dict[str, list] = field(default_factory=dict)
    # ... and the turns found that way, (session id, turn id): their start was
    # not seen here, so their reply is read whole from the history at the end.
    late_turns: _Recent = field(default_factory=lambda: _Recent(MAX_HELD_TURNS))
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

    def holding(self, session_id: str) -> bool:
        """Whether events of a turn of this session are held for a reply."""
        return any(key[0] == session_id for key in self.buffered)

    def open_buffer(self, key: tuple[str, str], events: list | None = None) -> None:
        self.buffered[key] = _Held(events or [])
        _trim(self.buffered, MAX_HELD_TURNS)

    def hold(self, key: tuple[str, str], event: dict[str, Any]) -> None:
        """Keep one event for the reply that will follow this turn -- within
        bounds: past them nothing more is kept but the turn's end, which is
        what lets the reply finish."""
        held = self.buffered.get(key)
        if held is None:
            return
        size = len(str(event.get("text") or "")) + len(str(event.get("args") or ""))
        chars = getattr(held, "chars", 0) + size
        if _ends_task(event) or (len(held) < MAX_HELD_EVENTS and chars <= MAX_REPLY_CHARS):
            held.append(event)
            if isinstance(held, _Held):
                held.chars = chars

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
        text = json.dumps({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})

        async def ask() -> Any:
            await self.send_text(text)
            return await future

        # One limit over the sending and the waiting: a socket that has
        # stopped taking bytes would otherwise hold the caller past its
        # timeout -- a chat's first message behind a two-second ask.
        try:
            return await asyncio.wait_for(ask(), timeout) if timeout else await ask()
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
                    refused = DeviceError(str(error.get("message") or "error"))
                    # Why, as data (`acp.md`: `data.reason` of a -32602):
                    # `scheduled_off`, `folder`, `terminal`, `interactive_only`…
                    data = error.get("data") if isinstance(error, dict) else None
                    if isinstance(data, dict):
                        refused.reason = str(data.get("reason") or "")[:40]
                        # The rest of what it said beside the reason (a
                        # changed file's `hash` and `mtime`), for the caller
                        # that knows the method.
                        refused.data = {
                            str(k)[:40]: v for k, v in list(data.items())[:16]
                            if isinstance(v, str | int | float | bool | type(None))
                        }
                    future.set_exception(refused)
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
        # The device's `show` tool (spec 3.0.0): the file's text, kept here
        # and drawn as the chat's own `show` card.
        if method == "_lembas/show":
            asyncio.create_task(self._show(message_id, params))
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
                    self.harness_turns.pop(session_id, None)
            if event.get("type") == "prompt" and event.get("machine"):
                self.harness_turns[session_id] = ""
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
            if (session_id, "") in self.buffered:
                self.hold((session_id, ""), event)
                return
            # A turn typed in the device's terminal, in a session it shares --
            # or one the harness started itself (a job's note, spec 2.0.0),
            # in any session a chat here shows.
            if event.get("type") == "prompt" and self._mirrors(session_id, event):
                _begin_mirror(
                    self, session_id, str(event.get("text") or ""),
                    machine=bool(event.get("machine")),
                )
            return
        if method == "session/update":
            # ACP's own updates carry what `_lembas/event` carries, and are read
            # for two things only: the session's commands, and its plan as a
            # structure (capability `plan_state`: `_meta.lembas.plan` beside
            # ACP's flat `entries`, which an editor draws and this does not).
            update = params.get("update") or {}
            if not isinstance(update, dict):
                return
            session_id = str(params.get("sessionId") or "")
            if update.get("sessionUpdate") == "available_commands_update":
                self.commands[session_id] = clean_commands(update.get("availableCommands"))
            elif update.get("sessionUpdate") == "plan" and self.can("plan_state"):
                meta = (update.get("_meta") or {}).get("lembas") or {}
                if "plan" in meta:
                    take_plan(self, session_id, meta.get("plan"))
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

    def _mirrors(self, session_id: str, event: dict[str, Any]) -> bool:
        """Whether a `prompt` event of this session is a turn to mirror here: a
        terminal's, in a session it shares; or the harness's own (`machine`,
        capability `jobs`) -- a finished job's note, a subagent's message --
        in any session, because the chat showing it would otherwise never see
        the turn the device's model was given."""
        if session_id in self.announced:
            return True
        return bool(event.get("machine")) and self.can("jobs")

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
        turn = str(event.get("turnId") or "")[:64]
        if kind == "task":
            # In step from here: every later turn's start is heard.
            self.synced.add(session_id)
        if kind == "prompt" and event.get("machine"):
            self.harness_turns[session_id] = turn
            _trim(self.harness_turns, MAX_LINK_SESSIONS)
        if kind == "task" and event.get("state") == "start":
            self.busy.add(session_id)
            if turn:
                self.current[session_id] = turn
                _trim(self.current, MAX_LINK_SESSIONS)
                waiting = self.queued_turns.get((session_id, turn))
                if waiting is not None:
                    # A reply waiting for this queued turn: held from its start.
                    self.open_buffer((session_id, turn))
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
            # Nor, on a link that has not heard this session's turns begin and
            # end, a reply whose prompt may still wait its turn on the device:
            # its own `task` start will be heard, and until then what comes
            # may be another turn's -- one that began before the link was up.
            if (
                generation is not None
                and generation.awaits_start
                and session_id not in self.synced
            ):
                generation = None
        # What is held is one turn's: the one the device runs now. The next
        # turn's `prompt` finds no list under its own id and opens one.
        held = (session_id, running)
        if generation is not None:
            if kind == "task" and event.get("state") == "start":
                generation.turn_started = True
                generation.awaits_start = False
            if (
                kind == "prompt"
                and turn
                and generation.turn_id == turn
                and turn not in self.sent_turns
                and self._mirrors(session_id, event)
            ):
                # The turn is the device's own, and a reply here joined it in
                # the moment between its `task` start and this event: the
                # turn still gets its row, and that reply is its reply.
                _note_joined(self, session_id, generation, event)
            apply_event(generation, event)
        elif held in self.buffered:
            self.hold(held, event)
        elif kind == "prompt" and self._mirrors(session_id, event) and turn not in self.sent_turns:
            # A turn typed in the device's terminal, or started by the harness
            # itself: its own row, whatever reply this session's last web turn
            # is still finishing.
            _begin_mirror(
                self, session_id, str(event.get("text") or ""), turn=turn,
                attachments=event.get("attachments"), machine=bool(event.get("machine")),
            )
        elif session_id in self.probing:
            if len(self.probing[session_id]) < MAX_HELD_EVENTS:
                self.probing[session_id].append(event)
        elif (
            self.can(HARNESS_TURNS)
            and self.can("status")
            and session_id not in self.synced
            and session_id not in self.asked
            and not running
            and kind not in ("prompt", "task")
            and len(self.probing) < MAX_PROBING
        ):
            # An event of a session this link has heard no `task` event of, and
            # no reply here is following: a turn may be running that started
            # before the link was up -- one the harness started while this
            # instance was away is in no chat yet. Asked once, the events held
            # meanwhile.
            self.asked.add(session_id)
            self.probing[session_id] = [event]
            asyncio.get_running_loop().create_task(_find_missed_turn(self, session_id))
        if _ends_task(event):
            self.busy.discard(session_id)
            self.harness_turns.pop(session_id, None)
            # Handed back by a harness turn before the reply that joined it
            # was following: kept for that reply.
            # Only under the turn's own id: an end that names no turn is
            # nobody's to match later, and kept it would be handed to whichever
            # reply next followed the session without a turn id.
            # Handed back: a message this chat showed as held by the device
            # is an ordinary waiting one again.
            _release_carried(self, session_id, _unsent_of(self, event))
            unheard = generation is None and held not in self.buffered
            if unheard and turn and (unsent := _unsent_of(self, event)):
                self.unsent[(session_id, turn)] = unsent
                _trim(self.unsent, MAX_UNSENT_TURNS)
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
        # The tool's own name where the device says it (spec 3.7.0:
        # `_meta.lembas.tool`); before that it was guessed from the command.
        said_tool = str(meta.get("tool") or "").strip()[:80]
        tool = said_tool or ("bash" if command else str(call.get("kind") or "tool"))
        options = {str(o.get("optionId")) for o in params.get("options") or []}
        # Which background subagent asks, when one does (capability `jobs`):
        # "explore (job7)", said on the card.
        asker = meta.get("job") if isinstance(meta.get("job"), dict) else {}
        job = ""
        if asker.get("id"):
            job = f"{str(asker.get('agent') or 'subagent')[:60]} ({str(asker['id'])[:32]})"
        # A subagent of the device's tree (spec 3.4.0, capability `tree`)
        # whose call nobody in the tree could allow: the card names it.
        who = meta.get("subagent") if isinstance(meta.get("subagent"), dict) else {}
        subagent: tuple[str, str, str] = ()
        if who.get("id") and who.get("name"):
            subagent = (
                str(who["id"])[:16], str(who["name"])[:40], str(who.get("position") or "")[:200]
            )
        item = interaction.Item(
            index=0,
            key="0",
            kind=interaction.KIND_APPROVAL,
            tool_name=tool,
            subagent=subagent,
            title=name if not command else "Run a command",
            detail=command or json.dumps(raw, ensure_ascii=False)[:2000],
            reason=str(meta.get("reason") or "")[: interaction.MAX_REASON_CHARS],
            purpose=str(meta.get("purpose") or ""),
            editable=bool(command),
            # Allow or don't: no "Something else" on an approval.
            allow_free_text=False,
            # "Always allow" only where the device offers it.
            always="session" in options,
            job=job,
            diffs=interaction.diffs_of(call.get("content")),
        )
        timeout = PATIENT_WAIT if patient else ASK_WAIT
        # A scheduled turn's approval (spec 3.5.0): the device waits the
        # schedule's wait and then denies it itself; the card here waits a
        # little longer, so it is settled by the device's word.
        scheduled = meta.get("scheduled") if isinstance(meta.get("scheduled"), dict) else {}
        if scheduled:
            try:
                timeout = float(scheduled.get("wait_s") or 0) + SCHEDULED_GRACE
            except (TypeError, ValueError):
                timeout = ASK_WAIT
            timeout = max(timeout, SCHEDULED_GRACE)
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
        # The corrected command is the item's answer (`answer_card` puts a
        # lone `text.<key>` there), as for a chat's own approval. Read from
        # `text.<key>` it was never found, and an edit on the card ran the
        # command the model asked for.
        edited = reply.answer_to(item)
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
        """`_lembas/plan`: the device's plan_submit -- the plan shown,
        and approved into Edit or Manual, sent back with what to change, or
        dismissed.

        The card is the structured one when the device sends `plan`
        (capability `plan_state`): the same card a chat's own `plan_submit`
        draws, with the mode choice a device's plan needs. A device without
        it sends the plan as text, drawn as rendered Markdown."""
        session_id = str(params.get("sessionId") or "")
        generation = self.writer(session_id)
        patient = _patient(params)
        if generation is None:
            await self._nobody_here(message_id, patient)
            return
        text = str(params.get("text") or "")[:MAX_PLAN_CHARS]
        plan = params.get("plan") if self.can("plan_state") else None
        structured = plan if isinstance(plan, dict) and plan else None
        timeout = PATIENT_WAIT if patient else ASK_WAIT
        call_id = str(params.get("toolCallId") or "")
        card = interaction.plan_card(
            structured,
            text=text,
            path=str(params.get("path") or ""),
            tool_name="plan_submit" if structured else "plan_exit",
            timeout=timeout,
            mode_choice=True,
            dismissable=True,
            tool_call_id=call_id,
        )
        item = card.items[0]
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

    async def _show(self, message_id: Any, params: dict[str, Any]) -> None:
        """`_lembas/show`: keep the file the device sent as a blob attached
        to the chat, and put the card on the call -- `event["show"]` once the
        device's tool call ends, the shape the chat's own `show` writes."""
        from lembas.db.session import session_scope
        from lembas.services import shown

        session_id = str(params.get("sessionId") or "")
        generation = self.writer(session_id)
        if generation is None:
            await self._answer(message_id, {
                "shown": False, "reason": "Nobody has this session open in LLeMbas right now.",
            })
            return
        if params.get("handle") and not isinstance(params.get("text"), str):
            await self._show_media(message_id, params, generation)
            return
        try:
            with session_scope() as db:
                row = shown.keep(
                    db,
                    chat_id=generation.chat_id,
                    message_id=generation.message_id,
                    params=params,
                )
                card = shown.payload(row)
                if row.kind == "html":
                    # Drawn in the conversation at once, or run on a click
                    # (`html_pages.shown_still`).
                    from lembas.services import html_pages

                    card["still"] = html_pages.shown_still(str(params.get("text") or ""))
                db.commit()
        except shown.Refused as exc:
            await self._answer(message_id, {"shown": False, "reason": str(exc)})
            return
        generation.device_shows[str(params.get("toolCallId") or "")] = card
        generation.touch()
        await self._answer(message_id, {"shown": True})

    async def _show_media(self, message_id: Any, params: dict[str, Any], generation) -> None:
        """`_lembas/show` for a file that is not text (spec 3.11.0, capability
        `media`): fetch it by its handle while the request is open, keep it in
        the person's files, and put its card on the call.

        The device's `kind`, `mimeType` and `size` are hints and a promise:
        what the file is, is read from the bytes that arrive
        (`services/media/shown.py`), and more bytes than the limit, or other
        bytes than the `hash` named, are not kept."""
        from lembas.services.media import shown as shown_media

        limits = shown_media.limits()
        path = str(params.get("path") or "").strip()[:1000]
        name = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "file"
        said = params.get("size")
        said = said if isinstance(said, int) and not isinstance(said, bool) and said >= 0 else None

        async def refuse(reason: str) -> None:
            await self._answer(message_id, {"shown": False, "reason": reason})

        if not path:
            await refuse("The request names no file.")
            return
        if said is not None and said > limits["max_bytes"]:
            await refuse(shown_media.too_large(name, said))
            return
        # One at a time on a link: each holds a whole file in memory, and a
        # device that shows ten at once would hold ten.
        async with self.media_lock:
            card = await self._fetch_media(params, generation, name, said, limits, refuse)
        if card is None:
            return
        generation.device_media[str(params.get("toolCallId") or "")] = card
        generation.touch()
        await self._answer(message_id, {"shown": True})

    async def _fetch_media(self, params, generation, name: str, said, limits, refuse):
        """The file behind a handle, fetched part by part and kept: its card,
        or None after `refuse` was told why not. The parts are hashed as they
        arrive and written one after another, in a thread: never joined into
        a second copy, and never on the event loop."""
        from lembas.services import fs
        from lembas.services.media import shown as shown_media

        handle = str(params.get("handle") or "")
        deadline = time.monotonic() + limits["wait_seconds"]
        parts: list[bytes] = []
        size, digest = 0, hashlib.sha256()
        try:
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError
                part = await self.request(
                    "_lembas/show/read",
                    {"handle": handle, "offset": size, "length": limits["chunk_bytes"]},
                    timeout=min(left, 60),
                )
                part = part if isinstance(part, dict) else {}
                if part.get("offset") not in (None, size):
                    await refuse(f"{name} was not received whole, so it is not shown.")
                    return None
                try:
                    piece = base64.b64decode(str(part.get("data") or ""), validate=True)
                except (ValueError, binascii.Error):
                    await refuse(f"{name} was not received whole, so it is not shown.")
                    return None
                size += len(piece)
                if size > limits["max_bytes"]:
                    await refuse(shown_media.too_large(name, size))
                    return None
                if piece:
                    parts.append(piece)
                    digest.update(piece)
                if part.get("eof") or not piece:
                    break
        except TimeoutError:
            await refuse(f"{name} took too long to fetch from the computer, so it is not shown.")
            return None
        except (LinkClosed, DeviceError) as exc:
            await refuse(f"{name} could not be fetched from the computer: {exc}")
            return None
        wanted = str(params.get("hash") or "")
        if (said is not None and said != size) or (
            wanted and wanted != "sha256:" + digest.hexdigest()
        ):
            await refuse(f"{name} changed while it was fetched, so it is not shown. Show it again.")
            return None
        chat_id, message_id, owner = generation.chat_id, generation.message_id, self.user_id

        def keep() -> dict | None:
            from lembas.db.models import Chat, User
            from lembas.db.session import session_scope

            with session_scope() as db:
                chat = db.get(Chat, chat_id)
                user = db.get(User, chat.user_id) if chat is not None else None
                if user is None or owner != user.id:
                    return None
                card = shown_media.keep(
                    db, user, name=name, payload=parts, chat_id=chat.id, message_id=message_id,
                )
                db.commit()
                return card

        try:
            card = await asyncio.to_thread(keep)
        except (fs.FsError, shown_media.Refused) as exc:
            await refuse(f"{name} could not be kept in the user's files: {exc}")
            return None
        if card is None:
            await refuse("Nobody has this session open in LLeMbas right now.")
        return card

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
# How much longer than its schedule's wait a scheduled turn's card stays open.
SCHEDULED_GRACE = 120.0
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
    """The device refused, in its own words. `reason` is its `data.reason`
    where it gave one."""

    reason = ""
    # Replaced per refusal, never changed in place.
    data: dict[str, Any] = {}


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


def hello_protocol(hello: dict[str, Any]) -> int:
    """The protocol a device's hello says, or 0 for none or nonsense."""
    try:
        return int(hello.get("protocol") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


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
                # `show`: this client draws a file the device sends with
                # `_lembas/show`, so the device offers its `show` tool.
                # `media` (spec 3.11.0): a file that is not text is fetched
                # with `_lembas/show/read` and drawn in the conversation.
                # `editor` (spec 3.11.0): an edit asked about arrives with
                # its diff as items (`path`, `oldText`, `newText`) for the
                # card an app draws, and a prompt may carry what the person
                # is looking at in their editor.
                "_meta": {"lembas": {"protocol": PROTOCOL, "show": True, "media": True,
                                     "editor": True}},
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


def _plan_onto(generation, raw: Any) -> dict[str, Any] | None:
    """The device's plan onto the reply being written: normalised, or cleared
    for `null`. The structure wins over a todo list the same turn sent."""
    from lembas.services import plans

    plan = plans.normalise(raw) if isinstance(raw, dict) and raw else None
    generation.plan = plan or None
    generation.todos = None
    generation.touch()
    return generation.plan


def take_plan(link: Link, session_id: str, raw: Any) -> dict[str, Any] | None:
    """A `plan` session update (capability `plan_state`): onto the reply
    writing the session, and onto the chat itself -- the device sends one
    after `session/new`, after a session is opened from the store and after
    every change, and only some of those have a reply writing. The chat row
    is what the plan panel reads, and `_persist` never *clears* it, so a
    `null` has to reach the row from here."""
    from lembas.services import plans

    plan = plans.normalise(raw) if isinstance(raw, dict) and raw else None
    generation = link.writer(session_id)
    if generation is not None:
        _plan_onto(generation, raw)
    _store_plan(link, session_id, plan)
    return plan


def _store_plan(link: Link, session_id: str, plan: dict[str, Any] | None) -> None:
    """The chat's plan as the device has it now; `None` clears it, the todo
    list of an older turn included."""
    from lembas.db.session import session_scope

    with session_scope() as db:
        chat = _chat_for(db, link, session_id)
        if chat is None:
            return
        if plan:
            chat.plan_json = plan
        else:
            chat.plan_json = {}
            chat.plan_message_id = None
            chat.todos_json = None
        db.commit()


def _mark_steered(generation, event: dict[str, Any]) -> None:
    """The device's model took in messages sent from here: marked into the reply
    at this point. By the ids the device hands back; when it names
    none, they go in the order they were sent."""
    said = [str(i)[:64] for i in (event.get("ids") or [])[:MAX_UNSENT_TEXTS]]
    ids = [i for i in said if i in generation.sent_steers]
    texts = [str(t) for t in event.get("texts") or [] if str(t).strip()]
    # A message of this chat's that an *earlier* turn was handed and ended
    # without taking in, with no link up to hand it back over: the device kept
    # it and gives it to the model now, at this turn's first step, saying so
    # with the message's id (spec 3.10.0). It is this chat's own row -- shown
    # where it went in, and no longer waiting -- not words typed in the
    # terminal, which would show it a second time.
    carried = _carried_in(generation, [i for i in said if i not in generation.sent_steers])
    if carried:
        generation.mark_steer(carried)
        texts = [t for t in texts if not any(_same(c["text"], t) for c in carried)]
        if not ids and not texts:
            return
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


def _carried_in(generation, ids: list[str]) -> list[dict]:
    """The rows of this reply's chat that these ids name -- a person's
    messages handed to the device before this reply -- as steer marks, each
    marked delivered: it has reached the model, here and now."""
    if not ids:
        return []
    from lembas.db.models import ROLE_USER, Message
    from lembas.db.session import session_scope

    marks: list[dict] = []
    try:
        with session_scope() as db:
            for message_id in ids:
                row = db.get(Message, message_id)
                if (
                    row is None
                    or row.chat_id != generation.chat_id
                    or row.role != ROLE_USER
                    or devices_own(row)
                ):
                    continue
                row.queued = False
                if row.origin in (ORIGIN_UNSURE, ORIGIN_CARRIED):
                    row.origin = ""
                marks.append({"id": row.id, "text": row.content or ""})
            db.commit()
    except Exception:  # the reply is written either way
        log.exception("could not mark carried messages of chat %s", generation.chat_id)
        return []
    return marks


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


# How much of a job's note the transcript keeps where it went in, and how many
# of a subagent's tool lines are kept under its task block or in its panel.
MAX_NOTE_CHARS = 8000

# How long a stopped reply waits for the device to say it has stopped, before it
# ends here regardless. Was ten seconds; the device's own tool or prefill is no
# reason for the person here to keep watching a reply they stopped. Anything
# the device sends after this is not written into the reply.
DEVICE_STOP_GRACE = 2.0
MAX_SUB_TOOLS = 50
MAX_RECENT_SUB_TOOLS = 8


def _retract(generation) -> None:
    """Drop what the current step streamed: the text and thinking appended
    since the last mark. The marks are cumulative lengths, so everything up to
    the last one is kept whole and the open tail alone goes."""
    # Marks the retried step wrote itself (where its thinking ended) go with it.
    del generation.steps[getattr(generation, "device_step_marks", 0) :]
    last = generation.steps[-1] if generation.steps else {}
    text_to = int(last.get("text_to") or 0)
    thinking_to = int(last.get("thinking_to") or 0)
    generation.content[:] = [generation.text[:text_to]] if text_to else []
    generation.reasoning[:] = [generation.thinking[:thinking_to]] if thinking_to else []
    generation.device_think_span = None


def _sub_tool(generation, event: dict[str, Any]) -> None:
    """A subagent's tool call finished (spec 2.0.0): kept under the `task` call
    it belongs to, to be drawn under that block when the call ends, and on its
    job's record for the panel. Bounded: it came off another machine."""
    line = {
        "name": str(event.get("name") or "tool")[:60],
        "title": str(event.get("title") or "")[:200],
        "isError": bool(event.get("isError")),
    }
    call_id = str(event.get("callId") or "")
    if call_id:
        lines = generation.sub_tools.setdefault(call_id, [])
        if len(lines) < MAX_SUB_TOOLS:
            lines.append(line)
    job_id = str(event.get("jobId") or "")
    if job_id and job_id in generation.jobs:
        recent = generation.jobs[job_id].setdefault("recent", [])
        recent.append(line)
        del recent[:-MAX_RECENT_SUB_TOOLS]


def device_progress(raw: Any) -> dict[str, Any] | None:
    """A device's progress object (spec 3.4.0, `phase` event and
    `_lembas/session/status`), kept only as the spec shapes it: a known
    phase, `since` in ms, the phase's own fields. None for anything else."""
    from lembas.services import progress as progress_service

    if not isinstance(raw, dict) or raw.get("phase") not in progress_service.phases():
        return None
    try:
        since = int(raw.get("since") or 0)
    except (TypeError, ValueError):
        return None
    fields = progress_service.clean(str(raw["phase"]), dict(raw))
    for key, value in list(fields.items()):
        if isinstance(value, str):
            fields[key] = value[:300]
        elif not isinstance(value, int | float) or isinstance(value, bool):
            fields.pop(key)
    return {"phase": raw["phase"], "since": since, **fields}


_TREE_TEXT = {"id": 16, "name": 40, "position": 200, "parent": 40, "state": 20, "model": 300,
              "mode": 16, "task": 2000}


def clean_tree(raw: Any) -> dict[str, Any] | None:
    """A device's subagent tree (spec 3.4.0, subagents.md §12) as the server
    keeps and passes it on: the spec's fields only, strings bounded, numbers
    numbers. A model chose the names, positions and tasks: they are text."""
    if not isinstance(raw, dict):
        return None

    def number(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def agent(row: Any) -> dict[str, Any] | None:
        if not isinstance(row, dict) or not row.get("id") or not row.get("name"):
            return None
        out: dict[str, Any] = {}
        for key, limit in _TREE_TEXT.items():
            value = row.get(key)
            if isinstance(value, str):
                out[key] = value[:limit]
            elif key == "parent":
                out[key] = None
        out["request"] = [str(k)[:16] for k in (row.get("request") or []) if isinstance(k, str)][:3]
        out["manager"] = bool(row.get("manager"))
        context = row.get("context")
        if isinstance(context, dict) and number(context.get("window")):
            out["context"] = {"used": number(context.get("used")),
                              "window": number(context.get("window"))}
        out["progress"] = device_progress(row.get("progress"))
        out["findings"] = number(row.get("findings"))
        out["messages"] = number(row.get("messages"))
        if row.get("ended") is not None:
            out["ended"] = number(row.get("ended"))
        return out

    agents = [a for a in (agent(r) for r in (raw.get("agents") or [])[:200]) if a]
    ended = [a for a in (agent(r) for r in (raw.get("ended") or [])[:200]) if a]
    return {
        "min": number(raw.get("min")) or 1,
        "max": number(raw.get("max")) or 1,
        "size": number(raw.get("size")),
        "open_slots": number(raw.get("open_slots")),
        "agents": agents,
        "ended": ended,
    }


def _take_tree(generation, raw: Any) -> None:
    """The device's tree (`team` event): onto the reply for its stream, and
    kept with the chat so it is drawn at once and after the device has gone."""
    tree = clean_tree(raw) if raw is not None else None
    generation.team_tree = tree
    keep_tree(generation.chat_id, tree)


def _tree_progress(generation, agent_id: str, found: dict[str, Any] | None) -> None:
    tree = getattr(generation, "team_tree", None)
    if not tree:
        return
    for row in tree.get("agents") or []:
        if row.get("id") == agent_id:
            row["progress"] = found
            generation.team_tree = {**tree}
            return


def keep_tree(chat_id: str, tree: dict[str, Any] | None) -> None:
    """Store a device chat's latest tree on the chat row."""
    from lembas.db.models import Chat as ChatRow
    from lembas.db.session import session_scope

    with contextlib.suppress(Exception), session_scope() as db:
        chat = db.get(ChatRow, chat_id)
        if chat is not None:
            chat.team_tree_json = tree or {}
            db.commit()


def _derived_phase(generation, kind: str, event: dict[str, Any]) -> None:
    """A device without the `progress` capability: the phase read off its
    engine events, as the CLI's own tracker reads them (progress.md §4)."""
    from lembas.services import progress as progress_service

    if getattr(generation, "device_phases", False):
        return
    if kind == "reasoning":
        generation.tracker.set("thinking")
    elif kind in ("text", "tool_call_delta"):
        generation.tracker.set("writing")
    elif kind == "tool_start":
        name = str(event.get("name") or "tool")
        if name == "task":
            generation.tracker.set("waiting_for_subagents", running=1)
        else:
            what = progress_service.what_of(event.get("args")) or None
            generation.tracker.set("running_tool", tool=name, what=what)
    elif kind == "step" or kind == "compacted":
        generation.tracker.set("reading_prompt")


def apply_event(generation, event: dict[str, Any]) -> None:
    """One engine event from the device, into the reply being written."""
    kind = event.get("type")
    if kind == "phase":
        # Spec 3.4.0 (capability `progress`): the device says its phase; the
        # main agent's is the reply's, a subagent's (`agent`) goes to the tree.
        found = device_progress(event.get("progress"))
        if event.get("agent"):
            _tree_progress(generation, str(event["agent"]), found)
        else:
            generation.device_phases = True
            generation.tracker.current = found
            generation.tracker.version += 1
        generation.touch()
        return
    if kind == "team":
        _take_tree(generation, event.get("tree"))
        generation.touch()
        return
    _derived_phase(generation, str(kind or ""), event)
    # How long it thought: measured here as the device's events arrive,
    # the way `_run` measures a reply of its own -- from the first reasoning
    # delta of a round to the last, closed by whatever comes after. Without it
    # a device chat's thinking block said only how many tokens it held.
    if kind == "reasoning":
        _think(generation)
    elif kind in ("text", "tool_call_delta", "tool_start") and generation.device_think_span:
        # The model moved on to prose or a call: the thinking ends here as a
        # step of its own, so the block says "Thought for …" now and not when
        # the step or the reply ends (the server's own replies, and the CLI).
        _close_thinking(generation)
        generation.close_step()
    elif kind in ("step", "usage", "task", "done"):
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
        generation.device_step_marks = len(generation.steps)
    elif kind == "done" and event.get("reason") == "cancelled":
        generation.stopped = True
    elif kind == "task" and event.get("state") == "end" and event.get("unsent"):
        # A turn the harness started ended before taking these in (spec
        # 3.10.0): `_follow` queues them again, for a device that lists
        # `harness_turns`.
        generation.device_unsent = _texts(event.get("unsent"))
    elif kind == "todos":
        # A device without `plan_state`: its todo list, which `_persist`
        # keeps as a one-phase plan (`plans.from_todos`).
        generation.todos = todo_items(event.get("items"))
    elif kind == "plan" and "plan" in event:
        # The structured plan, as a `plan` engine event carries it (the
        # `session/update` of the same name goes through `take_plan`).
        _plan_onto(generation, event.get("plan"))
    elif kind == "retract":
        # The device retries a step: what it streamed since the last round
        # closed is not the reply, and goes -- as the CLI's own view drops it.
        _retract(generation)
    elif kind == "job" and isinstance(event.get("job"), dict):
        # A background job of the session began, has news, or ended (spec
        # 2.0.0): onto the reply, which the stream turns into the strip above
        # the composer. What the device said of it last is kept beside it.
        from lembas.services import jobs as jobs_service

        facts = jobs_service.device_info(event["job"])
        if facts["id"]:
            previous = generation.jobs.get(facts["id"]) or {}
            if previous.get("recent"):
                facts["recent"] = previous["recent"]
            generation.jobs[facts["id"]] = facts
    elif kind == "job_note":
        # The note about a job was given to the model inside this turn: shown
        # where it went in, as a message taken in is.
        generation.mark_steer([{"id": "", "text": str(event.get("text") or "")[:MAX_NOTE_CHARS]}])
    elif kind == "sub_tool":
        _sub_tool(generation, event)
    elif kind == "tool_call_delta" and event.get("name"):
        from lembas.services.generation import _preparing_status

        generation.status = _preparing_status((str(event["name"]),))
    elif kind == "tool_start":
        from lembas.services import tool_labels

        generation.status = f"Running {tool_labels.label_for(str(event.get('name') or 'tool'))}…"
        # What it was called with, for the block it ends as: the
        # command a shell block shows, the path an edit or a read shows.
        call_id = str(event.get("id") or "")
        generation.device_args[call_id] = _bounded_args(event.get("args"))
        # The call is running: on its card from now until its result (spec
        # 3.7.0, display.md §2), not only in the progress line.
        generation.start_call(
            call_id, str(event.get("name") or "tool"), generation.device_args[call_id]
        )
    elif kind in ("tool_end", "tool_denied"):
        generation.status = ""
        result = event.get("result") or {}
        denied = kind == "tool_denied"
        failed = denied or bool(result.get("isError"))
        output = str(result.get("output") or event.get("reason") or "")
        call_id = str(event.get("id") or "")
        args = generation.device_args.pop(call_id, None)
        generation.end_call(call_id)
        finished = tool_event(
            str(event.get("name") or "tool"),
            args,
            title=str(result.get("title") or ""),
            output=output,
            failed=failed,
            meta=result.get("meta"),
        )
        # Refused, not run: the state `denied`, which shows no duration. A
        # call that ran says how long it took (`ms`, from spec 3.7.0).
        if denied:
            finished["status"] = "denied"
        elif isinstance(event.get("ms"), int | float) and not isinstance(event.get("ms"), bool):
            finished["ms"] = max(0, int(event["ms"]))
        # A `task` call ends: the subagent's tool lines it collected are drawn
        # under its block, as the CLI draws them.
        children = generation.sub_tools.pop(call_id, None)
        if children:
            finished["children"] = children
        # What the call showed (`_lembas/show`): the same card a chat's own
        # `show` makes.
        show = generation.device_shows.pop(call_id, None)
        if show is not None and not failed:
            finished["show"] = show
        media = generation.device_media.pop(call_id, None)
        if media is not None and not failed:
            finished["media"] = media
        generation.tool_events.append(finished)
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
    elif kind == "notice":
        # A warning or a plain notice (an agent file that would not parse, a
        # job moved to the background): said in the status line either way.
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
    harness: bool = False,
    device_turn: str = "",
    reply_turn: str = "",
    unconfirmed: dict[str, str] | None = None,
) -> None:
    """Write one reply from the device. Sets `generation.error` on every "no".

    `session_id` is the one the chat kept: used when this link has none
    for the chat yet, so the device carries on the session it already has.
    `mirror`: the turn is the device's own -- typed in its terminal, or started
    by its harness (`harness`) -- and this reply only follows it: nothing is
    sent, ever. `device_turn`: the device's id for that turn, where the row
    kept it. `attach`: this reply was cut off here
    (an update, a dropped link) while the device went on; it is followed again,
    never sent a second time. `message_id`: the person's message this answers --
    and, to a device that lists `turns`, the turn's id. `attachments`:
    the files sent with it, as `prompt_blocks` reads them. `reply_turn`: the
    device turn the reply's own row says it was writing (`_keep_reply_turn`) --
    its prompt's, or one it joined. `unconfirmed`: the messages handed into
    that turn that the reply had not seen taken in when it was cut off,
    id -> text."""
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
        # A scheduled run waits for it like a busy model (spec 3.5.0).
        generation.device_away = True
        return
    if mirror:
        await _follow_mirror(
            link, generation, chat_id, session_id, device_name,
            harness=harness, device_turn=device_turn,
        )
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
    current: str | None = link.sessions.get(chat_id) or kept or None
    session_id = kept if attach else ""
    try:
        if attach and kept:
            # The turn this reply was writing, by its own row's word: the
            # message it answers is not it when the reply joined a turn
            # already running, or took a later message in.
            if reply_turn and link.can("turns"):
                generation.turn_id = "" if reply_turn == JOINED_UNNAMED else reply_turn
            joined = bool(reply_turn) and reply_turn != message_id
            for steered, text in (unconfirmed or {}).items():
                generation.sent_steers[steered] = text
                generation.injected_ids.append(steered)
            resend = await _reattach(
                link, generation, chat_id, kept, device_name, machine=joined
            )
            _settle_unheard(generation, resend)
            if not resend:
                return
        session_id = ""
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
        # A turn of the device's own whose rows are still being made here (its
        # `prompt` came a moment ago): this reply becomes that turn's reply,
        # and the message it was started for waits in the queue -- handed to
        # the turn like any message typed while a reply runs. A reply of its
        # own would take the turn's events, and the turn would have no row.
        if message_id and not resend:
            taken = await _adopt_mirror(link, generation, session_id, message_id)
            if taken is not None:
                await _follow_mirror(
                    link, generation, chat_id, session_id, device_name,
                    harness=taken[1], device_turn=taken[0],
                )
                return
        # Working already -- a turn typed in its terminal, or one it
        # started by itself: what was typed here joins that turn, as a message
        # typed into the terminal mid-task would, and this reply follows it.
        # Sending it as a prompt was what came back "already working". Not for
        # a turn being sent again: the device queues that one by its id.
        # Nor into a turn the harness started on a device without
        # `harness_turns`, which refuses it: that one is waited out below.
        if (
            not resend
            and session_id in link.busy
            and message_id
            and link.can("steer")
            and _takes_messages(link, session_id)
        ):
            # Handed over like a message from the queue: handed back
            # (`unsent`), it is queued again and answered by a reply of its
            # own, so this one holds the joined turn's words and no other's.
            generation.sent_steers[message_id] = prompt
            generation.injected_ids.append(message_id)
            if link.can("turns"):
                # The reply follows the turn it joins, not a turn of its own.
                generation.turn_id = link.current.get(session_id, "")
            _keep_reply_turn(generation, joined=True)
            joined = True
            # The turn's writer from this moment, not from when the steer is
            # answered: what the turn says meanwhile -- its `prompt` among it,
            # which is what makes its row -- is this reply's to hear.
            before = (
                list(generation.content), list(generation.reasoning),
                list(generation.tool_events), list(generation.steps),
            )
            link.writing[session_id] = generation
            if generation.turn_id:
                link.turn_writers[(session_id, generation.turn_id)] = generation
            try:
                await link.request(
                    "_lembas/steer",
                    {"sessionId": session_id, "text": prompt, "messageId": message_id},
                    timeout=15,
                )
            except DeviceError:
                # It finished a moment ago: an ordinary prompt after all.
                joined = False
                generation.sent_steers.pop(message_id, None)
                generation.injected_ids.remove(message_id)
                if link.turn_writers.get((session_id, generation.turn_id)) is generation:
                    link.turn_writers.pop((session_id, generation.turn_id), None)
                # The last words of the turn that had just ended are not the
                # answer to this message.
                generation.content, generation.reasoning = before[0], before[1]
                generation.tool_events, generation.steps = before[2], before[3]
                generation.device_turn_over = False
                generation.turn_started = False
                generation.turn_id = message_id if link.can("turns") else ""
            except TimeoutError:
                # No answer in time is not a refusal: the link delivers in
                # order, so the device almost surely has it and answered late.
                # Sending it again as a prompt ran the same message twice. It
                # is followed exactly as an answered one is -- the turn's end
                # may still hand it back (`unsent`), and then it is sent.
                pass
            if joined:
                await _follow(link, generation, chat_id, session_id)
                return
        _keep_reply_turn(generation)
        # A device without `harness_turns` fails a prompt sent while a turn its
        # harness started runs (and loses track of that turn): sent after it.
        await _harness_turn_over(link, generation, session_id)
        if generation.cancel:
            # Stopped while it waited to be sent: withdrawn here, never sent.
            generation.stopped = True
            return
        await _prompt(
            link, generation, chat_id, session_id, content, device_name, message_id=message_id
        )
        _settle_steered(generation, message_id)
    except DeviceError as exc:
        generation.error = f"{device_name} refused: {exc}"
        generation.device_reason = exc.reason
    except LinkClosed as exc:
        link.sessions.pop(chat_id, None)
        link.writing.pop(session_id, None)
        await _after_drop(
            token_id, generation, chat_id, session_id, device_name, content, message_id, exc
        )
    except TimeoutError:
        generation.error = f"{device_name} did not answer in time."
    finally:
        if session_id and link.writing.get(session_id) is generation:
            link.writing.pop(session_id, None)
        _close_thinking(generation)
        generation.status = ""
        generation.touch()


# How many times in a row one reply waits for a dropped link to come back.
MAX_RELINKS = 3


async def _after_drop(
    token_id: str,
    generation,
    chat_id: str,
    session_id: str,
    device_name: str,
    content: list,
    message_id: str,
    exc: Exception,
) -> None:
    """A link that blinks does not end the device's turn: the reply waits for
    the device to dial in again and takes its turn up by what the device then
    says of it (`_reattach`) -- followed on, read whole from the history when
    it ended meanwhile, or (a prompt the device never had) sent again by its
    id. A turn this reply only *joined* is never sent."""
    for _ in range(MAX_RELINKS):
        again = await _wait_for_link(token_id) if session_id and not generation.cancel else None
        if again is None or not again.can("status"):
            break
        joined = generation.turn_id != message_id or bool(
            message_id and message_id in generation.sent_steers
        )
        try:
            resend = await _reattach(
                again, generation, chat_id, session_id, device_name, machine=joined
            )
            _settle_unheard(generation, resend)
            if resend:
                # The device never had it: sent again, by its id.
                await _prompt(again, generation, chat_id, session_id, content, device_name)
                _settle_steered(generation, message_id)
            return
        except LinkClosed as dropped:
            exc = dropped
        except DeviceError as refused:
            generation.error = str(refused)
            return
        except TimeoutError:
            generation.error = f"{device_name} did not answer in time."
            return
        finally:
            if again.writing.get(session_id) is generation:
                again.writing.pop(session_id, None)
    generation.error = str(exc)


# On a reply's row (`Message.device_turn_id`): it joined a turn the device did
# not name. Never a turn id -- only "not the message's own turn".
JOINED_UNNAMED = "?"


def _keep_reply_turn(generation, *, joined: bool = False) -> None:
    """The reply's own row remembers which turn of the device it writes: its
    prompt's (the message's id) or, `joined`, the one it joined. After a
    restart that is the turn the device is asked about -- the message the
    reply answers is the wrong one to ask about, and to send again, once the
    reply joined another turn or took a later message in."""
    turn = generation.turn_id[:64] or (JOINED_UNNAMED if joined else "")
    if not turn:
        return
    from lembas.db.models import Message
    from lembas.db.session import session_scope

    try:
        with session_scope() as db:
            row = db.get(Message, generation.message_id)
            if row is not None and row.device_turn_id != turn:
                row.device_turn_id = turn
                db.commit()
    except Exception:  # the reply is written either way
        log.exception("could not keep the turn of reply %s", generation.message_id)


# Message.origin of a person's message that was handed to the device (into a
# running turn) and whose fate there cannot be known: the turn ended, or the
# device forgot it, while nobody here was listening. It waits (`queued`) with
# that said on it, is never sent by itself, and "Send now" sends it.
ORIGIN_UNSURE = "unsure"


# ... and of one the device says it still holds: handed to a turn that ended
# without taking it in, it waits there for the next turn's first step (or in
# the running turn's queue). It waits here too, saying so, and is neither
# sent nor offered to a running turn: the turn that takes it in says
# `steered` with its id (`_carried_in`), or hands it back (`_release_carried`).
ORIGIN_CARRIED = "carried"
# What a device answers a prompt whose id is a message it was steered.
STEERED_FATES = ("taken", "carried", "waiting")


def _settle_steered(generation, message_id: str) -> None:
    """The prompt just sent did not run: its id is a message the device was
    steered and still knows, and the answer says what became of it. **A
    message uses one id whether it is steered or sent as a prompt** (the
    row's), which is what makes "send it again" safe.

    `taken`: the model has been given it -- the row is delivered. `carried`
    or `waiting`: the device holds it for a turn that will take it in -- the
    row waits, saying so. Either way the reply started for this prompt has
    nothing to hold and its row is removed: the message's reply is the reply
    of the turn that takes it in, where it is shown at the point it went in."""
    fate = getattr(generation, "device_steered", "")
    if not fate or not message_id:
        return
    from lembas.db.models import ROLE_USER, Message
    from lembas.db.session import session_scope

    with session_scope() as db:
        row = db.get(Message, message_id)
        if row is not None and row.role == ROLE_USER and not devices_own(row):
            row.queued = fate != "taken"
            row.origin = "" if fate == "taken" else ORIGIN_CARRIED
        reply = db.get(Message, generation.message_id)
        if reply is not None:
            db.delete(reply)
        db.commit()


def _release_carried(link: Link, session_id: str, texts: list[str]) -> None:
    """A turn handed back (`unsent`) messages this chat shows as held by the
    device: the device has let go of them, so each is an ordinary waiting
    message again -- and is sent, as the next turn, when nothing is being
    written in the chat."""
    if not texts:
        return
    from sqlalchemy import select

    from lembas.db.models import ROLE_USER, Message
    from lembas.db.session import session_scope
    from lembas.services import generation as generation_service

    chat_id = ""
    try:
        with session_scope() as db:
            chat = _chat_for(db, link, session_id)
            if chat is None:
                return
            held = db.scalars(
                select(Message).where(
                    Message.chat_id == chat.id,
                    Message.role == ROLE_USER,
                    Message.origin == ORIGIN_CARRIED,
                )
            ).all()
            for row in held:
                if any(_same(row.content or "", text) for text in texts):
                    row.origin = ""
                    row.queued = True
                    chat_id = chat.id
            db.commit()
    except Exception:  # the turn's end is handled either way
        log.exception("could not release carried messages of %s", session_id)
        return
    if chat_id:
        generation_service.start_waiting(chat_id)


def _settle_unheard(generation, resend: bool) -> None:
    """After taking a turn up again: the messages handed into it that were
    never seen taken in. Followed to its end, the turn said which came back
    (`unsent`) and the rest went in. Ended unheard, or unknown to the device,
    it said nothing this instance heard -- and a message is never sent twice
    on a guess: each is marked, for its person to send again or not."""
    if not (resend or getattr(generation, "device_unheard", False)):
        return
    ids = list(generation.sent_steers)
    generation.sent_steers.clear()
    generation.injected_ids = [i for i in generation.injected_ids if i not in ids]
    if not ids:
        return
    from lembas.db.models import ROLE_USER, Message
    from lembas.db.session import session_scope

    with session_scope() as db:
        for message_id in ids:
            row = db.get(Message, message_id)
            if row is not None and row.role == ROLE_USER and not devices_own(row):
                row.queued = True
                row.origin = ORIGIN_UNSURE
        db.commit()


def _editor_of(message_id: str) -> dict | None:
    """What the person was looking at in their editor when they sent this
    message (`Message.editor_json`), to go with its prompt."""
    from lembas.db.models import Message
    from lembas.db.session import session_scope

    if not message_id:
        return None
    with session_scope() as db:
        row = db.get(Message, message_id)
        return dict(row.editor_json) if row is not None and row.editor_json else None


async def _prompt(
    link: Link, generation, chat_id: str, session_id: str, content: list, device_name: str,
    message_id: str = "",
) -> None:
    """Send the prompt and write the reply from its events until the device
    answers it. Stop here stops it there; a message typed meanwhile joins it."""
    turn = generation.turn_id if link.can("turns") else ""
    link.writing[session_id] = generation
    if turn:
        link.turn_writers[(session_id, turn)] = generation
        generation.awaits_start = not generation.turn_started
    params: dict[str, Any] = {"sessionId": session_id, "prompt": content}
    # The turn's id, and what only this instance knows -- the chat's
    # instructions and the person's personalization -- sent again on every
    # prompt so a change made here between two turns reaches the device's
    # next one.
    meta: dict[str, Any] = {"turnId": turn} if turn else {}
    meta.update(session_context(link, chat_id))
    # The editor's context of this message (spec 3.11.0, capability
    # `editor`): the device says it to its model, after the person's words.
    if link.can("editor") and (editor := _editor_of(message_id)):
        meta["editor"] = editor
    if meta:
        params["_meta"] = {"lembas": meta}
    stopping = asyncio.ensure_future(generation.stop_event.wait())
    try:
        task = asyncio.create_task(link.request("session/prompt", params, timeout=None))
        while not task.done():
            # Stop is waited on with the answer, not found by the next poll.
            done, _ = await asyncio.wait({task, stopping}, timeout=0.5)
            done.discard(stopping)
            # A message typed meanwhile joins this turn -- once it runs. While
            # the prompt still waits behind another turn there (the harness's
            # own, a terminal's), a steer would go into *that* turn, whose
            # events are not this reply's: the message stays in the queue.
            if (
                not done
                and not generation.cancel
                and link.can("steer")
                and (generation.turn_started or not turn)
            ):
                await _pass_on_queued(link, generation, chat_id, session_id)
            if not done and generation.cancel:
                await _cancel(link, generation, session_id)
                generation.stopped = True
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(task), DEVICE_STOP_GRACE)
                break
        if task.done() and not task.cancelled():
            result = task.result() or {}
            if result.get("stopReason") == "cancelled":
                generation.stopped = True
            # What the device never took in comes back: queued again, so the
            # drain after this reply sends it as the next turn rather than it
            # being marked delivered and lost.
            said = (result.get("_meta") or {}).get("lembas") or {}
            # A scheduled turn's outcome (spec 3.5.0): ok, silent, failed or
            # cancelled, and the final reply; the run is recorded from it.
            if isinstance(said.get("scheduled"), dict):
                generation.scheduled_outcome = dict(said["scheduled"])
            unsent = _texts(said.get("unsent"))
            if unsent:
                _hand_back(generation, unsent)
                _release_carried(link, session_id, unsent)
            # The prompt's id is a message the device was steered and still
            # knows: nothing ran (spec 3.10.0, *A steered message is given to
            # the model once*). What became of it is said instead.
            fate = str(said.get("steered") or "")
            if fate in STEERED_FATES and link.can(HARNESS_TURNS) and not generation.turn_started:
                generation.device_steered = fate
                return
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
        stopping.cancel()
        generation.awaits_start = False
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


def _texts(raw: Any) -> list[str]:
    """A list of messages as a device sent it: strings, none empty -- and no
    more of them, and no longer, than messages typed here could be."""
    if not isinstance(raw, list):
        return []
    return [str(t)[:MAX_UNSENT_CHARS] for t in raw[:MAX_UNSENT_TEXTS] if str(t).strip()]


def _same(sent: str, back: str) -> bool:
    """Whether a message handed back is this one handed over (the device
    trims what it is given)."""
    return sent.strip()[:MAX_UNSENT_CHARS] == back.strip()[:MAX_UNSENT_CHARS]


def _unsent_of(link: Link, event: dict[str, Any]) -> list[str]:
    """What a harness turn's `task` end hands back (`unsent`), from a device
    that lists `harness_turns`; nothing from any other."""
    if not _ends_task(event) or not link.can(HARNESS_TURNS):
        return []
    return _texts(event.get("unsent"))


def _takes_messages(link: Link, session_id: str) -> bool:
    """Whether a message may be sent to the session now. Not while a turn its
    harness started runs on a device without `harness_turns`: that device
    refuses a steer then and fails a prompt, so nothing is sent until the
    turn's end (`acp.md`, *A turn the harness starts*)."""
    return link.can(HARNESS_TURNS) or session_id not in link.harness_turns


async def _harness_turn_over(link: Link, generation, session_id: str) -> None:
    """Wait out a turn the harness started on a device that takes nothing
    meanwhile. The turn's `task` end is what ends the wait; so does Stop here,
    or the link dropping."""
    while (
        not _takes_messages(link, session_id)
        and session_id in link.busy
        and not link.closed
        and not generation.cancel
    ):
        await asyncio.sleep(0.1)


def cancel_effect(turn: str, running: str, queued: list[str]) -> dict[str, str]:
    """What a `session/cancel` does on a device that lists `harness_turns`
    (spec 3.10.0, `conformance/cancel.json`): `turn` is the id the cancel
    names, "" for ACP's own. `cancelled`: `running` (that turn is stopped --
    `stops`), `queued` (that prompt is taken out of the queue and never runs
    -- `withdraws` -- and the running turn is not touched) or `none`."""
    if not turn:
        return {"cancelled": "running", "stops": running} if running else {"cancelled": "none"}
    if turn == running:
        return {"cancelled": "running", "stops": turn}
    if turn in queued:
        return {"cancelled": "queued", "withdraws": turn}
    return {"cancelled": "none"}


def cancel_params(link: Link, generation, session_id: str) -> dict[str, Any]:
    """`session/cancel` for this reply: with the reply's own turn named
    (`_meta.lembas.turnId`) for a device that lists `harness_turns`, so Stop
    on a reply whose prompt still waits there takes that prompt back and
    leaves the turn that runs alone. ACP's own cancel for any other device."""
    params: dict[str, Any] = {"sessionId": session_id}
    turn = generation.turn_id if link.can("turns") else ""
    if turn and link.can(HARNESS_TURNS):
        params["_meta"] = {"lembas": {"turnId": turn}}
    return params


COULD_NOT_WITHDRAW = (
    "\n\n*(Stopped here. The computer already has this message waiting behind "
    "another turn, and its LLeMbas CLI cannot take it back: it will still run "
    "there, and its answer will not be shown.)*"
)


async def _cancel(link: Link, generation, session_id: str, *, ask: bool = False) -> str:
    """Stop this reply's turn on the device -- **this reply's**, not whichever
    turn runs. Returns what the device said it did (`running`, `queued`,
    `none`) when `ask`ed of a device that names turns on a cancel, else "".

    A device without `harness_turns` knows only "stop what runs". That is sent
    when what runs is this reply's turn (or the device names no turns at
    all). When another turn runs and this reply's prompt waits behind it, a
    plain cancel would stop the other turn and leave the prompt to run: it is
    not sent, and the reply says the message could not be taken back."""
    params = cancel_params(link, generation, session_id)
    if "_meta" in params:
        if not ask:
            await link.notify("session/cancel", params)
            return ""
        try:
            said = await link.request("session/cancel", params, timeout=15)
        except (DeviceError, TimeoutError, LinkClosed):
            return ""
        return str((said if isinstance(said, dict) else {}).get("cancelled") or "")
    turn = generation.turn_id if link.can("turns") else ""
    running = link.current.get(session_id, "")
    if turn and running and running != turn:
        generation.content.append(COULD_NOT_WITHDRAW)
        generation.touch()
        return ""
    await link.notify("session/cancel", params)
    return ""


# What to do with an incomplete row of turn T after a restart or a dropped
# link, from what `_lembas/session/status` says (`acp.md`; the harness spec's
# `conformance/reattach.json` is this table, and both projects answer it).
FOLLOW = "follow"  # T runs: take its events until its `task` end
WAIT = "wait"  # T is queued: follow it when it starts
IMPORT = "import"  # read the reply after the user turn with T from the history
RESEND = "resend"  # send the prompt again with T: the device runs it once


def reattach_decision(turn: str, status: dict[str, Any], *, machine: bool = False) -> str:
    """The one place reattaching decides. `machine`: the row is a turn the
    device's harness started -- **never sent**: its text is a note, not a
    person's words, and when the device no longer knows the turn (it restarted
    in the middle of it) what its history has for the id is all there is.

    `status.busy` is not read: the turn running is named (`turnId`) or there
    is none -- which is why this is only asked of a device whose status names
    the row's kind of turn (`turns`; for a harness turn, `harness_turns`)."""
    queued = status.get("queued")
    if turn and str(status.get("turnId") or "") == turn:
        return FOLLOW
    if turn and turn in [str(t) for t in (queued if isinstance(queued, list) else [])]:
        return WAIT
    if machine or (turn and str(status.get("lastTurnId") or "") == turn):
        return IMPORT
    return RESEND


async def _reattach(
    link: Link,
    generation,
    chat_id: str,
    session_id: str,
    device_name: str,
    *,
    machine: bool = False,
) -> bool:
    """A reply cut off here while the device went on: followed again
    if the turn still runs; if it finished meanwhile, its whole answer from the
    device's history. Never sent a second time -- except to a device that
    lists `turns` and says it has never seen the turn: then it is sent
    again, by its id, which the device dedupes. Returns whether to send it.

    Without turn ids, "idle" could mean the turn ended or that the device had
    not read the prompt yet; the history was imported and the row closed, and
    the prompt then ran with nobody following it. By id, the device says which.

    `machine`: the row is a turn the device's harness started, and
    `generation.turn_id` the device's id for it -- only given for a device
    that lists `harness_turns`, whose status names such a turn. It is never
    sent: this returns False for it whatever the device says
    (`reattach_decision`).
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
        generation.device_unheard = True
        return False
    if link.can("plan_state") and "plan" in status:
        # The plan as the device has it, so a reply taken up again draws it
        # without waiting for the next change. Onto this reply by hand: it
        # is not registered as the session's writer until it follows.
        _store_plan(link, session_id, _plan_onto(generation, status.get("plan")))
    if link.can("progress") and "progress" in status:
        # Spec 3.4.0: the phase as the device has it, drawn at once.
        generation.device_phases = True
        generation.tracker.current = device_progress(status.get("progress"))
        generation.tracker.version += 1
    if link.can("tree") and "tree" in status:
        _take_tree(generation, status.get("tree"))
    if link.can("jobs") and isinstance(status.get("jobs"), list):
        # The session's background jobs, for the strip, for the same reason.
        from lembas.services import jobs as jobs_service

        for raw in status["jobs"]:
            facts = jobs_service.device_info(raw)
            if facts["id"]:
                generation.jobs[facts["id"]] = facts
    if turn:
        decision = reattach_decision(turn, status, machine=machine)
        if decision == FOLLOW:
            link.busy.add(session_id)
            link.current[session_id] = turn
            if machine:
                link.harness_turns[session_id] = turn
            await _follow(link, generation, chat_id, session_id)
            # What the turn said while nobody here was listening -- between
            # the cut and now -- is in the device's history and nowhere else.
            await _whole_turn(link, generation, session_id, device_name, turn)
            return False
        if decision == WAIT:
            await _await_turn(link, generation, chat_id, session_id, turn)
            return False
        if decision == IMPORT:
            # Over on the device, and only being written here: said, so the
            # device's next turn waits for this reply (`_chat_free`).
            generation.device_turn_over = True
            # Its end was not heard here: nor was what it handed back.
            generation.device_unheard = True
            # A harness turn: by its id alone -- whatever the history has for
            # it, and the reply as it stands when it has nothing.
            await _import_turn(link, generation, session_id, device_name, turn, strict=machine)
            return False
        # Never seen, or finished before the last one: sent again, by its id.
        return True
    if machine:
        # No id to ask about: closed as it stands rather than guessed at.
        generation.device_unheard = True
        return False
    if status.get("busy") or session_id in link.busy:
        # Working, by its own word -- or by a `task` start this link has seen
        # and no end since, which is the device's word too.
        link.busy.add(session_id)
        await _follow(link, generation, chat_id, session_id)
        return False
    generation.device_unheard = True
    await _import_turn(link, generation, session_id, device_name, "")
    return False


async def _whole_turn(link: Link, generation, session_id: str, device_name: str, turn: str) -> None:
    """A turn followed to its end from part way through: its whole reply, by
    its id, in place of the part heard. Left as it is when the reply was
    stopped or failed, and when the history has nothing under the id."""
    if turn and not (generation.cancel or generation.stopped or generation.error):
        await _import_turn(link, generation, session_id, device_name, turn, strict=True, quiet=True)


async def _import_turn(
    link: Link, generation, session_id: str, device_name: str, turn: str, *,
    strict: bool = False, quiet: bool = False,
) -> None:
    """A finished turn's whole answer, from the device's history, in place of
    the part seen here: the reply after the user turn with this id when the
    history names turns, else the last reply. `strict`: only by id. `quiet`:
    a history that cannot be read leaves the reply as it is, without an error.
    What is taken is bounded as a live reply is (`MAX_REPLY_CHARS`,
    `MAX_IMPORT_TOOLS`)."""
    try:
        history = await link.request(
            "_lembas/session/history", {"sessionId": session_id}, timeout=30
        )
    except (DeviceError, TimeoutError, LinkClosed) as exc:
        if not quiet:
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
            # The reply to *this* turn: the one right after it. A turn with no
            # reply (the device restarted in the middle of it) has none -- the
            # next turn's answer is not its own.
            after = turns[asked + 1] if asked + 1 < len(turns) else None
            last = after if after is not None and after.get("role") != "user" else None
            # The history is in hand: what the harness did by itself after
            # this turn, while nobody here was listening, is taken in too.
            await _take_in_missed(link, generation.chat_id, session_id, turns[asked + 1 :])
    if last is None and not strict:
        last = turns[-1] if turns and turns[-1].get("role") != "user" else None
    if last is None:
        return
    generation.content = [str(last.get("text") or "")[:MAX_REPLY_CHARS]]
    generation.reasoning = [str(last.get("reasoning") or "")[:MAX_REPLY_CHARS]]
    generation.tool_events = _history_tools(last, generation.device_where)
    generation.steps = []
    generation.touch()


# What one read of a device's history may add to a chat: the harness turns
# nobody here heard (the newest ones), the tool calls of one reply, the
# characters of one note. A reply's text has `MAX_REPLY_CHARS`.
MAX_MISSED_TURNS = 20
MAX_IMPORT_TOOLS = 500
MAX_NOTE_CHARS = 200_000


def _history_tools(reply: dict[str, Any] | None, where: str) -> list:
    """A history reply's tool calls as blocks, no more than a reply keeps."""
    tools = (reply or {}).get("tools")
    tools = tools if isinstance(tools, list) else []
    return [_tool_event(t, where) for t in tools[:MAX_IMPORT_TOOLS] if isinstance(t, dict)]


def _harness_turn(turn: dict[str, Any]) -> str:
    """The id of a history turn the harness started (`machine` on a user turn,
    capability `harness_turns`), or "" for any other turn."""
    if turn.get("role") != "user" or turn.get("machine") is not True:
        return ""
    return str(turn.get("turnId") or "")[:64]


async def _take_in_missed(link: Link, chat_id: str, session_id: str, turns: list[dict]) -> None:
    """Turns the device's harness started and finished while this instance was
    not listening -- it was restarting, the link was down -- from the device's
    history into the chat: the note as the harness's row, and its reply.

    Each is matched by the device's id for the turn (`Message.device_turn_id`),
    so one that was mirrored live is never stored a second time. The turn
    running now is left alone: its events are still coming. Only from a device
    that lists `harness_turns` -- no other marks such a turn in its history."""
    if not link.can(HARNESS_TURNS) or not chat_id:
        return
    wanted = [(n, _harness_turn(t)) for n, t in enumerate(turns)]
    # The newest few: a history that lists thousands is not written into a chat.
    wanted = [(n, turn) for n, turn in wanted if turn][-MAX_MISSED_TURNS:]
    if not wanted:
        return
    try:
        status = await link.request("_lembas/session/status", {"sessionId": session_id}, timeout=15)
    except (DeviceError, TimeoutError, LinkClosed):
        # Which of them still runs is not known: none is taken for finished.
        return
    running = str((status if isinstance(status, dict) else {}).get("turnId") or "")
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from lembas.db.models import ROLE_ASSISTANT, ROLE_USER, Chat, Message
    from lembas.db.session import session_scope

    try:
        with session_scope() as db:
            chat = db.get(Chat, chat_id)
            if chat is None:
                return
            ids = [turn for _, turn in wanted]
            known = set(
                db.scalars(
                    select(Message.device_turn_id).where(
                        Message.chat_id == chat.id, Message.device_turn_id.in_(ids)
                    )
                )
            )
            where = f"{short_name(link)}:{chat.project_dir}"
            when = datetime.now(UTC)
            added = False
            for n, turn in wanted:
                if turn == running or turn in known:
                    continue
                known.add(turn)
                reply = next(iter(turns[n + 1 : n + 2]), None)
                if reply is not None and reply.get("role") == "user":
                    reply = None
                when += timedelta(milliseconds=1)
                db.add(
                    Message(
                        chat_id=chat.id,
                        role=ROLE_USER,
                        content=str(turns[n].get("text") or "")[:MAX_NOTE_CHARS],
                        origin=ORIGIN_HARNESS,
                        machine=True,
                        device_turn_id=turn,
                        created_at=when,
                    )
                )
                when += timedelta(milliseconds=1)
                db.add(
                    Message(
                        chat_id=chat.id,
                        role=ROLE_ASSISTANT,
                        content=str((reply or {}).get("text") or "")[:MAX_REPLY_CHARS],
                        reasoning=str((reply or {}).get("reasoning") or "")[:MAX_REPLY_CHARS],
                        tool_calls_json=_history_tools(reply, where),
                        model_id=chat.model_id,
                        device_turn_id=turn,
                        created_at=when,
                    )
                )
                added = True
            if added:
                # Nobody was reading when it happened: the chat has news.
                chat.unread = True
            db.commit()
    except Exception:  # what was missed stays missed; the reply in hand is still written
        log.exception("could not take in the harness turns of %s", session_id)


# The catch-up when a device links: how many of its chats are asked -- the
# ones most recently changed -- and how recently one must have changed.
CATCH_UP_CHATS = 5
CATCH_UP_WITHIN_HOURS = 24


async def catch_up(link: Link) -> int:
    """What the device's harness did by itself while nobody here was
    listening, taken into the chats it most likely happened in: when a device
    links, the `CATCH_UP_CHATS` chats of it changed most recently (within
    `CATCH_UP_WITHIN_HOURS`) have their history read once, and the harness
    turns after the last turn each chat already knows are stored
    (`_take_in_missed`). Without this such a turn was shown only when the
    chat's history was next read for another reason. Returns how many chats
    were asked. Only of a device that lists `harness_turns`: no other marks
    such a turn. Bounded on purpose -- a device may have thousands of chats
    here, and a history is not small; an older chat catches up when a reply
    of it is next taken up."""
    if not (link.can(HARNESS_TURNS) and link.can("status") and link.can("sessions")):
        return 0
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from lembas.db.models import Chat, Message
    from lembas.db.session import session_scope
    from lembas.services import generation as generation_service

    since = datetime.now(UTC) - timedelta(hours=CATCH_UP_WITHIN_HOURS)
    with session_scope() as db:
        chats = [
            (chat.id, chat.device_session_id)
            for chat in db.scalars(
                select(Chat)
                .where(
                    Chat.user_id == link.user_id,
                    Chat.device_token_id == link.token_id,
                    Chat.device_session_id.is_not(None),
                    Chat.device_session_id != "",
                    Chat.updated_at >= since,
                )
                .order_by(Chat.updated_at.desc())
                .limit(CATCH_UP_CHATS)
            )
        ]
    asked = 0
    for chat_id, session_id in chats:
        if link.closed:
            break
        if generation_service.running_for(chat_id) is not None or link.holding(session_id):
            continue  # a reply of it is at work: that one reads what it needs
        asked += 1
        try:
            history = await link.request(
                "_lembas/session/history", {"sessionId": session_id}, timeout=30
            )
        except (DeviceError, TimeoutError, LinkClosed):
            continue
        turns = [t for t in (history or {}).get("turns") or [] if isinstance(t, dict)]
        ids = [str(t.get("turnId") or "")[:64] for t in turns if t.get("role") == "user"]
        with session_scope() as db:
            known = set(
                db.scalars(select(Message.id).where(Message.chat_id == chat_id))
            ) | set(
                db.scalars(
                    select(Message.device_turn_id).where(
                        Message.chat_id == chat_id, Message.device_turn_id.is_not(None)
                    )
                )
            )
        # Only what comes after the last turn this chat knows by its id: a
        # history none of whose turns it knows gives no place to start from,
        # and a guess there would store old turns a second time.
        last = max(
            (n for n, t in enumerate(turns)
             if t.get("role") == "user" and str(t.get("turnId") or "")[:64] in known),
            default=None,
        )
        if last is None or not ids:
            continue
        await _take_in_missed(link, chat_id, session_id, turns[last + 1 :])
    return asked


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
        raise LinkClosed("The device's link dropped.")
    if not started.is_set():
        # Stopped here before it began: the device is told, and the reply ends.
        generation.stopped = True
        # Asked, not only told: a turn that had already run to its end while
        # this waited (`none`) has a reply, and the chat shows it.
        if await _cancel(link, generation, session_id, ask=True) == "none":
            await _import_turn(
                link, generation, session_id, "the device", turn, strict=True, quiet=True
            )
        return
    await _follow(link, generation, chat_id, session_id)


async def _follow(link: Link, generation, chat_id: str, session_id: str) -> None:
    """Write this reply from a turn the device is running, until it ends. Stop
    here stops it there; a message typed here meanwhile joins it."""
    if link.closed:
        raise LinkClosed("The device's link dropped.")
    done: asyncio.Future = asyncio.get_running_loop().create_future()
    link.mirrors[session_id] = done
    link.writing[session_id] = generation
    link.sessions[chat_id] = session_id
    turn = generation.turn_id if link.can("turns") else ""
    if turn:
        link.turn_writers[(session_id, turn)] = generation
    try:
        # The events held for *this* turn, and no other's.
        for event in link.buffered.pop((session_id, turn), []):
            apply_event(generation, event)
            if _ends_task(event) and not done.done():
                done.set_result(None)
        # Handed back by the turn's end before this reply was registered for
        # it: under the turn's own id, or not at all.
        if turn:
            generation.device_unsent.extend(link.unsent.pop((session_id, turn), []))
        if session_id not in link.busy and not done.done():
            # It ended between the question and now.
            done.set_result(None)
        if done.done():
            # Over on the device before this reply was following it: said, as
            # the dispatcher says it for a turn that ends while followed, so
            # the device's next turn waits for this reply instead of being
            # dropped as a second one in the chat (`_chat_free`).
            generation.device_turn_over = True
        stopping = asyncio.ensure_future(generation.stop_event.wait())
        try:
            while not done.done():
                await asyncio.wait({done, stopping}, timeout=0.5)
                if done.done():
                    break
                if generation.cancel:
                    generation.stopped = True
                    await _cancel(link, generation, session_id)
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(asyncio.shield(done), DEVICE_STOP_GRACE)
                    break
                # Not into a harness turn on a device without `harness_turns`:
                # it refuses, every time. The message waits in the queue and
                # goes as the next turn when this one has ended.
                if link.can("steer") and _takes_messages(link, session_id):
                    await _pass_on_queued(link, generation, chat_id, session_id)
        finally:
            stopping.cancel()
        if done.done() and not done.cancelled() and done.exception() is not None:
            if isinstance(done.exception(), LinkClosed):
                # The link dropped, the turn did not end: whoever follows
                # waits for the device to dial in again (`_after_drop`,
                # `_follow_mirror`) instead of closing the reply here, part
                # written, with nobody left to write the rest.
                raise done.exception()
            generation.error = str(done.exception())
            generation.device_reason = getattr(done.exception(), "reason", "")
        if generation.device_unsent and link.can(HARNESS_TURNS):
            _hand_back(generation, generation.device_unsent)
        generation.device_unsent = []
    finally:
        link.mirrors.pop(session_id, None)
        if link.writing.get(session_id) is generation:
            link.writing.pop(session_id, None)
        if turn and link.turn_writers.get((session_id, turn)) is generation:
            link.turn_writers.pop((session_id, turn), None)
        # The buffer is not dropped here. This reply took its own at the top;
        # one that exists now was made since, for the device's *next* turn (a
        # second note, a turn typed in the terminal, a queued turn starting),
        # whose reply has not begun to follow yet. Dropping it left that reply
        # empty: it found no buffer, asked the device, and wrote nothing.
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
    # What only this instance knows, under `lembas` -- beside the mode and
    # model, which protocol 1 devices read at the top of `_meta`.
    context = session_context(link, chat_id)
    if link.can("agents") and (said := said_before(chat_id, generation.message_id)):
        # The person's earlier words (subagents.md §2), for a device that has
        # not seen this chat's turns: it judges a named model from them.
        context["said"] = said
    if context:
        meta["lembas"] = context
    opened = await link.request(
        "session/new", {"cwd": project_dir, "mcpServers": [], "_meta": meta}, timeout=60
    )
    session_id = str(opened.get("sessionId") or "")
    link.sessions[chat_id] = session_id
    _keep_session_id(chat_id, session_id)
    # The chat's model, full stop (spec 3.0.0): a 2.0 device runs a session it
    # was opened with on that model or refuses `session/new` with -32602 and
    # the reason -- which `run` shows as the reply's error. There is no
    # "answered on its own model" to say any more.
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


SAID_MAX = 50
SAID_CHARS = 4000


def said_before(chat_id: str, message_id: str) -> list[str]:
    """The person's messages of this chat before the turn being answered,
    oldest first (`_meta.lembas.said`, acp.md): typed ones only, never the
    harness's (`machine`), bounded."""
    from sqlalchemy import select

    from lembas.db.models import ROLE_USER, Chat, Message
    from lembas.db.session import session_scope

    with session_scope() as db:
        chat = db.get(Chat, chat_id)
        answering = db.get(Message, message_id)
        if chat is None or answering is None:
            return []
        rows = db.scalars(
            select(Message)
            .where(
                Message.chat_id == chat_id,
                Message.role == ROLE_USER,
                Message.machine.is_(False),
                Message.created_at < answering.created_at,
            )
            .order_by(Message.created_at)
        ).all()
        texts = [str(r.content or "")[:SAID_CHARS] for r in rows if (r.content or "").strip()]
    # The last is the prompt this session is opened for: it goes as the prompt.
    return texts[:-1][-SAID_MAX:]


async def configure_agents(token_id: str | None, session_id: str | None, low: int,
                           high: int) -> bool:
    """The person changed an agent chat's Subagents setting between turns:
    `_lembas/configure` with `agents` (spec 3.4.0), when its device is linked,
    lists `agents` and has the session. Every prompt carries it anyway."""
    link = link_for(token_id)
    if link is None or not session_id or not link.can("agents"):
        return False
    try:
        await link.request(
            "_lembas/configure", {"sessionId": session_id, "agents": {"min": low, "max": high}},
            timeout=30,
        )
    except Exception:  # the next prompt carries it
        log.info("could not tell the device the new Subagents setting", exc_info=True)
        return False
    return True


def _keep_session_id(chat_id: str, session_id: str) -> None:
    from lembas.db.models import Chat
    from lembas.db.session import session_scope

    with session_scope() as db:
        chat = db.get(Chat, chat_id)
        if chat is not None and chat.device_session_id != session_id:
            chat.device_session_id = session_id
            db.commit()


# --- What only this instance knows: the session's Context ------------------
def session_context(link: Link, chat_id: str) -> dict[str, Any]:
    """`_meta.lembas`'s Context for a chat's session (harness spec `acp.md`,
    `session/new`): what the device composes its prompt from and cannot know
    by itself. Each piece is gated on the capability that says the device
    reads it; a device listing none gets `{}`, and nothing changes for it."""
    if not (link.can("context") or link.can("agents")):
        return {}
    from lembas.db.models import Chat, User
    from lembas.db.session import session_scope

    with session_scope() as db:
        chat = db.get(Chat, chat_id)
        if chat is None:
            return {}
        return context_for(db, link, db.get(User, chat.user_id), chat)


def context_for(db, link: Link, user, chat) -> dict[str, Any]:
    """The Context itself, read while a session is open.

    `system_prompt`: the chat's effective authored prompt, chat over folder over
    model over instance, "" when none -- the device appends it as
    "Instructions from <instance>". `personalization`: the account's, in the
    shape `/v1/me/personalization` gives it, or `null` for the device's own
    (the feature off here, or switched off by the person). No `library`
    (spec 3.0.0): knowledge bases are gone from this side.
    """
    from lembas.services import chat as chat_service
    from lembas.services import personalization

    context: dict[str, Any] = {}
    # Spec 3.5.0 (capability `scheduled`): a schedule's run in this chat says
    # so on every request of its session -- the mode, the wait, the run.
    if link.can("scheduled"):
        from lembas.services.schedule import device as scheduled_device

        if chat.id in scheduled_device.TURNS:
            context["scheduled"] = scheduled_device.for_link(
                scheduled_device.TURNS[chat.id], link
            )
    # Spec 3.4.0 (capability `agents`): the chat's Subagents setting, with
    # every turn; `subagents: false` while the instance has them switched off
    # (or the person may not use them), so the device offers none.
    if link.can("agents"):
        from lembas.services import team_run

        low, high = team_run.setting(db, chat)
        context["agents"] = {"min": low, "max": high}
        if not team_run.agent_offered(db, user):
            context["subagents"] = False
    if link.can("context"):
        context["system_prompt"] = chat_service.effective_system_prompt(db, chat)
        chosen = personalization.of(user) if user is not None else None
        context["personalization"] = (
            {
                "personality": chosen["personality"],
                "personality_custom": chosen["custom"],
                "instructions": chosen["instructions"],
            }
            if chosen is not None and personalization.enabled(db) and chosen["on"]
            else None
        )
    return context


# --- Rewind: editing or regenerating a turn ---------------------------------
def offers(token_id: str | None, capability: str) -> bool:
    """Whether the device a chat runs on is linked now and lists this
    capability -- what a template asks before showing a control that needs
    it (Edit and Regenerate need `rewind`)."""
    link = link_for(token_id)
    return link is not None and link.can(capability)


async def rewind(link_or_token: Link | str | None, chat, turn_id: str) -> int | None:
    """`_lembas/session/rewind` (capability `rewind`): the person is editing
    or regenerating from the turn `turn_id`, so the device drops that turn
    and every later one from the conversation its model sees -- the files
    they changed stay, and the control says so. Returns how many turns the
    device reverted, or None when nothing could be asked: no link, a device
    without the capability, a chat with no session yet, or no turn id (a turn
    typed in the device's terminal has none here). A refusal -- a turn the
    session does not have, a session still working -- is a `DeviceError`."""
    link = link_or_token if isinstance(link_or_token, Link) else link_for(link_or_token)
    session_id = str(getattr(chat, "device_session_id", "") or "")
    if link is None or not link.can("rewind") or not session_id or not turn_id:
        return None
    answer = await link.request(
        "_lembas/session/rewind", {"sessionId": session_id, "turnId": turn_id}, timeout=30
    )
    answer = answer if isinstance(answer, dict) else {}
    return int(answer.get("reverted") or 0)


# --- A session the device's terminal shares -----------------------------
# Message.origin of a user turn the *device* started, mirrored here: typed in
# its terminal ...
ORIGIN_TERMINAL = "terminal"
# ... or started by its harness -- a finished job's note, a team report --
# which the device says with `machine` on the turn's `prompt` event. `machine`
# on the row is not this mark: a note written *here* into an agent chat (a
# schedule's firing, a finished generation, `wake.wake_chat`) is `machine` too,
# and the device has never seen it, so it is sent.
ORIGIN_HARNESS = "harness"
DEVICE_ORIGINS = (ORIGIN_TERMINAL, ORIGIN_HARNESS)


def devices_own(message) -> bool:
    """Whether a user turn is the device's own, mirrored here: its reply only
    follows the device, nothing is sent for it, and its row's id is not a turn
    id the device knows (so it cannot be rewound to from here). The one place
    that tells such a turn from one typed, or written, on this instance."""
    return message is not None and (getattr(message, "origin", "") or "") in DEVICE_ORIGINS


def _ends_task(event: dict[str, Any]) -> bool:
    return event.get("type") == "task" and event.get("state") == "end"


def _settle_mirror(link: Link, session_id: str) -> None:
    done = link.mirrors.get(session_id)
    if done is not None and not done.done():
        done.set_result(None)


async def _follow_mirror(
    link: Link,
    generation,
    chat_id: str,
    session_id: str,
    device_name: str,
    *,
    harness: bool = False,
    device_turn: str = "",
) -> None:
    """Write the reply to a turn the device started -- typed in its terminal, or
    its harness's own -- from its events, until the device's task ends. Stop
    here stops it there; a message typed here meanwhile joins it there, as one
    typed in the terminal would.

    Nothing here sends the row to the device, on any path: the turn is the
    device's already. `harness` and `device_turn`: the row is a turn the
    harness started, and the device's id for it. A device that lists
    `harness_turns` is asked about exactly that turn (spec 3.10.0); for any
    other, and for a row kept before the id was, the turn is found as before
    -- by what the link has seen."""
    for _ in range(MAX_RELINKS + 1):
        try:
            await _mirror_once(
                link, generation, chat_id, session_id, device_name,
                harness=harness, device_turn=device_turn,
            )
            return
        except LinkClosed as exc:
            # The link dropped in the middle of the device's turn. The turn
            # goes on there: the reply waits for the device to dial in again,
            # as a reply to a turn sent from here does, and is taken up by
            # what the device then says of the turn.
            again = None if generation.cancel else await _wait_for_link(link.token_id)
            if again is None or not again.can("status"):
                generation.error = str(exc)
                return
            link = again
    generation.error = "The device's link dropped."


async def _mirror_once(
    link: Link,
    generation,
    chat_id: str,
    session_id: str,
    device_name: str,
    *,
    harness: bool,
    device_turn: str,
) -> None:
    named = bool(harness and device_turn and link.can("turns") and link.can(HARNESS_TURNS))
    # The events held for this row are its own turn's, by the device's id for
    # it -- never whichever turn the link heard a `prompt` of last. A row cut
    # off by a restart may be an earlier turn's (the first of two notes in a
    # row, while the second runs), and a row kept before the id was has none.
    turn = device_turn if link.can("turns") else ""
    live = (session_id, turn) in link.buffered and (bool(turn) or not link.can("turns"))
    if not live:
        # Not started by the device's event on this link: an instance that
        # restarted part way through a turn it was following, or a link that
        # dropped. Followed again from the device's own word, instead of
        # given up on.
        if named and link.can("status"):
            # Asked by the turn's own id: followed while it runs, read from
            # the history once it has ended, and never sent.
            generation.turn_id = device_turn
            await _reattach(link, generation, chat_id, session_id, device_name, machine=True)
            _settle_unheard(generation, False)
            return
        if link.can("status"):
            if link.can("turns"):
                # The turn it follows is whichever the device runs now.
                generation.turn_id = link.current.get(session_id, "")
            await _reattach(link, generation, chat_id, session_id, device_name)
            _settle_unheard(generation, False)
            return
        generation.error = (
            f"This turn ran in the terminal on {device_name}; its reply was not "
            "followed to the end here."
        )
        return
    # The device's turn, by its id: its events are routed here.
    generation.turn_id = turn
    await _follow(link, generation, chat_id, session_id)
    late = (session_id, generation.turn_id)
    if generation.turn_id and late in link.late_turns:
        # Its start was not seen here (`_find_missed_turn`): what was written
        # before this link was up is in the device's history, and only there.
        link.late_turns.discard(late)
        if not generation.error:
            await _import_turn(
                link, generation, session_id, device_name, generation.turn_id, strict=True
            )


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
        return "", chat_service.resolved_effort(chat) or chat_service.chat_level(db, chat)
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
    # The chat's level word as stored (harness spec 3.2.0 levels: `off`,
    # `default` or an effort): the device reads it with its model's metadata --
    # a level that model does not offer as its start level -- and says which
    # it used (acp.md). Nothing stored: the level in force here.
    return served, chat_service.resolved_effort(chat) or chat_service.chat_level(db, chat)


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
    if effort in chat_service.LEVEL_WORDS:
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


def _import_turns(
    db, chat, turns: list[dict], device_name: str, *, marked: bool = False
) -> None:
    """A terminal session's turns before it was shared, as the chat's history.
    `marked`: the device lists `harness_turns`, so a turn its harness started
    is marked in the history (`machine`) and stored as the harness's."""
    from datetime import UTC, datetime, timedelta

    from lembas.db.models import ROLE_ASSISTANT, ROLE_USER, Message

    where = f"{device_name}:{chat.project_dir}"
    start = datetime.now(UTC) - timedelta(milliseconds=len(turns) + 1)
    for n, turn in enumerate(turns):
        when = start + timedelta(milliseconds=n)
        if turn.get("role") == "user":
            # A turn the harness started (`machine`, capability
            # `harness_turns`) is stored as what it is -- nobody typed it in
            # the terminal -- with the device's id for it.
            harness = _harness_turn(turn) if marked else ""
            row = Message(
                chat_id=chat.id,
                role=ROLE_USER,
                content=str(turn.get("text") or ""),
                origin=ORIGIN_HARNESS if harness else ORIGIN_TERMINAL,
                machine=bool(harness),
                device_turn_id=harness or None,
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
        if link.holding(session_id):
            turns = _without_last_prompt(turns)
        if not turns:
            return
        with session_scope() as db:
            if _chat_for(db, link, session_id) is not None:
                return
            chat = _adopt_chat(db, link, session_id)
            if chat is not None:
                _import_turns(
                    db, chat, turns, short_name(link), marked=link.can(HARNESS_TURNS)
                )
    except Exception:  # one session that cannot be shown must not end the link
        log.exception("could not take in device session %s", session_id)
    finally:
        adopting.set()
        link.adopting.pop(session_id, None)


async def _find_missed_turn(link: Link, session_id: str) -> None:
    """A turn the device's harness started while this instance was away (it
    restarted, the link was down) and that still runs: its `prompt` event was
    said to nobody, so no chat has it, and the events arriving now belong to
    no reply. Capability `harness_turns`: the device says which turn runs
    (`status.turnId`) and its history marks the turn as the harness's, with
    the note -- so its row is made after all and its reply followed from here
    on, then read whole from the history when it ends (`link.late_turns`).

    A turn sent from here, or one this chat already has a row for, is left to
    the reply that is taken up again for it (`generation.resume_interrupted`).
    The events held meanwhile (`link.probing`) become the mirror's buffer, or
    are dropped as they would have been."""
    from sqlalchemy import select

    from lembas.db.models import Message
    from lembas.db.session import session_scope

    found = False
    try:
        with session_scope() as db:
            chat = _chat_for(db, link, session_id)
            chat_id = chat.id if chat is not None else ""
        if not chat_id:
            return
        status = await link.request("_lembas/session/status", {"sessionId": session_id}, timeout=15)
        turn = str((status if isinstance(status, dict) else {}).get("turnId") or "")
        if not turn:
            return
        with session_scope() as db:
            if db.get(Message, turn) is not None or db.scalar(
                select(Message.id).where(
                    Message.chat_id == chat_id, Message.device_turn_id == turn[:64]
                )
            ):
                return
        history = await link.request(
            "_lembas/session/history", {"sessionId": session_id}, timeout=30
        )
        turns = [t for t in (history or {}).get("turns") or [] if isinstance(t, dict)]
        note = next((t for t in turns if _harness_turn(t) == turn[:64]), None)
        if note is None or link.closed:
            return
        held = link.probing.pop(session_id, [])
        if link.holding(session_id) or link.writing.get(session_id) is not None:
            # Something else took the session up meanwhile.
            return
        found = True
        turn = turn[:64]
        link.open_buffer((session_id, turn), held)
        link.late_turns.add((session_id, turn))
        if not any(_ends_task(event) for event in held):
            link.busy.add(session_id)
            link.current[session_id] = turn
            link.harness_turns[session_id] = turn
        await _mirror_turn(
            link, session_id, str(note.get("text") or ""), turn=turn, machine=True
        )
    except (DeviceError, TimeoutError, LinkClosed):
        return
    except Exception:  # one session that cannot be caught up must not end the link
        log.exception("could not look for a missed turn of %s", session_id)
    finally:
        link.probing.pop(session_id, None)
        if found and (session_id, turn) not in link.buffered:
            # The mirror was not made (no chat any more): nothing to read late.
            link.late_turns.discard((session_id, turn))


def _begin_mirror(
    link: Link,
    session_id: str,
    text: str,
    *,
    turn: str = "",
    attachments: Any = None,
    machine: bool = False,
) -> None:
    """A turn typed in the terminal, or started by the harness (`machine`):
    hold its events, and make its bubbles."""
    link.open_buffer((session_id, turn))
    link.mirroring[(session_id, turn)] = True
    # A prompt is a turn running, whether or not its `task` start came first.
    link.busy.add(session_id)
    asyncio.get_running_loop().create_task(
        _mirror_turn(
            link,
            session_id,
            text,
            turn=turn,
            attachments=attachment_chips(attachments),
            machine=machine,
        )
    )


def _note_joined(link: Link, session_id: str, generation, event: dict[str, Any]) -> None:
    """The row of a turn the device started, made when a reply here is
    already following that turn (it joined on the `task` start, before the
    `prompt` said whose turn it is): note, the message handed in, the reply --
    as they read when a message is taken into a mirrored turn's reply."""
    from datetime import UTC, datetime, timedelta

    from lembas.db.models import ROLE_USER, Message
    from lembas.db.session import session_scope
    from lembas.services import chat as chat_service

    turn = str(event.get("turnId") or "")[:64]
    machine = bool(event.get("machine"))
    try:
        with session_scope() as db:
            chat = _chat_for(db, link, session_id)
            reply = db.get(Message, generation.message_id)
            if chat is None or reply is None or _has_turn(db, link, session_id, turn):
                return
            asked = chat_service.create_message(
                db,
                chat,
                ROLE_USER,
                str(event.get("text") or "")[:MAX_NOTE_CHARS],
                origin=ORIGIN_HARNESS if machine else ORIGIN_TERMINAL,
                machine=machine,
            )
            chips = attachment_chips(event.get("attachments"))
            if chips:
                asked.terminal_attachments_json = chips
            asked.device_turn_id = turn
            now = datetime.now(UTC)
            asked.created_at = now
            for n, message_id in enumerate(generation.injected_ids, 1):
                handed = db.get(Message, message_id)
                if handed is not None:
                    handed.created_at = now + timedelta(milliseconds=n)
            reply.created_at = now + timedelta(milliseconds=len(generation.injected_ids) + 1)
            reply.device_turn_id = turn
            db.commit()
    except Exception:  # the reply is written either way
        log.exception("could not make the row of a joined turn of %s", session_id)


# How long a turn of the device's waits for this session's previous reply to
# finish being written here.
MIRROR_WAIT = 30.0
# How long a reply started for a message typed in that moment waits to be
# made the turn's reply (the chat for a shared session may still be adopted).
ADOPT_WAIT = 60.0

FREE, ADOPT, STORE, GONE = "free", "adopt", "store", "gone"


async def _chat_free(link: Link, chat_id: str, key: tuple[str, str]) -> str:
    """What to do with a turn of the device's whose rows are to be made, once
    it can be said -- one reply at a time in a chat:

    - `FREE`: no reply is running. The one that was had ended on the device
      and was only being written here; it is waited for (`MIRROR_WAIT`).
    - `ADOPT`: the reply running was started for a message typed in the
      moment before these rows existed, and takes this turn as its own
      (`_adopt_mirror`).
    - `STORE`: a reply that is not this turn's kept running until the turn
      ended (its own prompt waits behind this turn on the device): the turn's
      rows are written, finished, from the events held.
    - `GONE`: the link dropped, or what was held is over its bound.
    """
    from lembas.services import generation as generation_service

    deadline = asyncio.get_running_loop().time() + MIRROR_WAIT
    while True:
        running = generation_service.running_for(chat_id)
        if running is None:
            return FREE
        if link.adopters.get(key[0]) is running:
            return ADOPT
        held = link.buffered.get(key)
        if held is None or link.closed:
            return GONE
        ended = bool(held) and _ends_task(held[-1])
        late = asyncio.get_running_loop().time() > deadline
        if ended and (not running.device_turn_over or late):
            return STORE
        await asyncio.sleep(0.05)


async def _adopt_mirror(
    link: Link, generation, session_id: str, message_id: str
) -> tuple[str, bool] | None:
    """A reply started for a message typed here finds a turn of the device's
    whose rows are still being made (`link.mirroring`): it offers itself as
    that turn's reply and waits to be taken. Returns the turn's id and
    whether the harness started it, or None when there is no such turn (or
    its rows were not made): the reply then goes its own way."""
    if not any(key[0] == session_id for key in link.mirroring):
        return None
    taken: asyncio.Future = asyncio.get_running_loop().create_future()
    generation.adopted = (message_id, taken)
    link.adopters[session_id] = generation
    try:
        deadline = asyncio.get_running_loop().time() + ADOPT_WAIT
        while (
            not taken.done()
            and any(key[0] == session_id for key in link.mirroring)
            and not link.closed
            and not generation.cancel
            and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.02)
    finally:
        if link.adopters.get(session_id) is generation:
            link.adopters.pop(session_id, None)
        generation.adopted = None
    return taken.result() if taken.done() else None


async def _mirror_turn(
    link: Link,
    session_id: str,
    text: str,
    *,
    turn: str = "",
    attachments: list | None = None,
    machine: bool = False,
) -> None:
    """Make the rows of a turn the device started, and start the reply that
    follows it -- or, when the chat cannot take a reply now, say why not in
    one of the ways `_chat_free` names. Every way ends with the turn's events
    held for a reply that will take them, written into rows, or let go."""
    key = (session_id, turn)
    link.mirroring[key] = True
    try:
        await _mirror_rows(link, key, text, attachments, machine)
    finally:
        link.mirroring.pop(key, None)


def _has_turn(db, link: Link, session_id: str, turn: str) -> bool:
    """Whether this turn is one a chat here already has: a turn sent from
    here (its id is the message's), or a turn of the device's own kept under
    its id -- read from its history, or mirrored before."""
    from sqlalchemy import select

    from lembas.db.models import ROLE_USER, Message

    if not turn:
        return False
    if db.get(Message, turn) is not None:
        return True
    chat = _chat_for(db, link, session_id)
    return chat is not None and bool(
        db.scalar(
            select(Message.id).where(
                Message.chat_id == chat.id,
                Message.role == ROLE_USER,
                Message.device_turn_id == turn[:64],
            )
        )
    )


async def _mirror_rows(
    link: Link, key: tuple[str, str], text: str, attachments: list | None, machine: bool
) -> None:
    from datetime import UTC, datetime, timedelta

    from lembas.db.models import ROLE_ASSISTANT, ROLE_USER, Message
    from lembas.db.session import session_scope
    from lembas.services import chat as chat_service
    from lembas.services import generation as generation_service

    session_id, turn = key
    adopting = link.adopting.get(session_id)
    if adopting is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(adopting.wait(), 40)
    with session_scope() as db:
        # Matched by its id: a turn sent from here -- this instance
        # restarted, and the device ran it before the reply was taken up again
        # -- is that message's, never a terminal turn of its own; and a turn
        # of the device's own this chat already has is not shown twice.
        if _has_turn(db, link, session_id, turn):
            link.buffered.pop(key, None)
            return
        chat = _chat_for(db, link, session_id)
        chat_id = chat.id if chat is not None else ""
    # One reply at a time in a chat. The turn before this one may still be
    # finishing here; a reply typed for in this very moment takes the turn
    # over; any other is waited out to the turn's end.
    how = await _chat_free(link, chat_id, key) if chat_id else FREE
    if how == GONE:
        link.buffered.pop(key, None)
        return
    ids: tuple[str, str] | None = None
    adopter = link.adopters.get(session_id) if how == ADOPT else None
    events = link.buffered.pop(key, []) if how == STORE else []
    try:
        with session_scope() as db:
            chat = _chat_for(db, link, session_id)
            # A harness turn in a session no chat shows is not a reason to make
            # one; a terminal's turn in a shared session is.
            if chat is None and (session_id in link.announced or not machine):
                chat = _adopt_chat(db, link, session_id)
            # (Read from the history meanwhile, by a reply taken up again.)
            if chat is not None and _has_turn(db, link, session_id, turn):
                chat = None
            running = generation_service.running_for(chat.id) if chat is not None else None
            if chat is not None and (
                running is None or (adopter is not None and running is adopter) or how == STORE
            ):
                # The harness's own turn is badged as such (`machine`, as a
                # schedule's or a helper's note here is), never as typed in the
                # terminal: nobody typed it. Its origin says the device started
                # it, which is what makes its reply follow the device instead
                # of sending the note back to it.
                asked = chat_service.create_message(
                    db,
                    chat,
                    ROLE_USER,
                    text[:MAX_NOTE_CHARS],
                    origin=ORIGIN_HARNESS if machine else ORIGIN_TERMINAL,
                    machine=machine,
                )
                if attachments:
                    asked.terminal_attachments_json = attachments
                # The device's id for the turn: what it is asked about after a
                # restart, and what keeps the turn from being stored twice.
                asked.device_turn_id = turn[:64] or None
                link.sessions[chat.id] = session_id
                if running is not None and running is adopter:
                    # The reply that was started for a message typed just now
                    # is this turn's: the rows read note, reply, message --
                    # and the message waits in the queue, to be handed to the
                    # turn (or sent after it) like any typed while it runs.
                    typed_id, taken = adopter.adopted
                    now = datetime.now(UTC)
                    asked.created_at = now
                    reply = db.get(Message, adopter.message_id)
                    typed = db.get(Message, typed_id)
                    if reply is None or typed is None:
                        db.rollback()
                    else:
                        reply.created_at = now + timedelta(milliseconds=1)
                        reply.device_turn_id = turn[:64] or None
                        typed.created_at = now + timedelta(milliseconds=2)
                        typed.queued = True
                        db.commit()
                        taken.set_result((turn, machine))
                        return
                elif how == STORE:
                    _store_turn(db, chat, asked, events, f"{short_name(link)}:{chat.project_dir}")
                    return
                else:
                    reply = chat_service.create_message(
                        db, chat, ROLE_ASSISTANT, "", complete_=False, model_id=chat.model_id
                    )
                    reply.device_turn_id = turn[:64] or None
                    db.commit()
                    ids = (chat.id, reply.id)
    except Exception:  # as `_announced`
        log.exception("could not mirror a terminal turn of %s", session_id)
    if ids is None:
        link.buffered.pop(key, None)
        return
    generation_service.ensure(*ids)


def _store_turn(db, chat, asked, events: list, where: str) -> None:
    """A turn of the device's that ran from start to end while another reply
    held the chat: its reply written, finished, from the events held -- the
    same events a reply following it live would have been written from."""
    from lembas.db.models import ROLE_ASSISTANT
    from lembas.services import chat as chat_service
    from lembas.services import generation as generation_service

    shadow = generation_service.Generation(chat_id=chat.id, message_id="")
    shadow.device_where = where
    for event in events:
        try:
            apply_event(shadow, event)
        except (AttributeError, TypeError, ValueError, KeyError, ArithmeticError):
            log.warning("an event of a stored turn could not be read", exc_info=True)
    _close_thinking(shadow)
    reply = chat_service.create_message(
        db, chat, ROLE_ASSISTANT, shadow.text, model_id=chat.model_id
    )
    reply.reasoning = shadow.thinking
    reply.tool_calls_json = list(shadow.tool_events)
    reply.steps_json = list(shadow.steps)
    reply.error = shadow.error or ""
    reply.device_turn_id = asked.device_turn_id
    chat.unread = True
    db.commit()


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
        if level in chat_service.LEVEL_WORDS:
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
        _requeue_ids(generation, [message_id])
    except TimeoutError:
        # Late is not refused: requeued, it was sent a second time.
        log.info("steer for %s answered late; taken as delivered", session_id)


def _hand_back(generation, texts: list[str]) -> None:
    """What a turn ended before taking in (`unsent`: on a harness turn's `task`
    end, spec 3.10.0, or in a prompt's answer): each such message goes to the
    model next, once.

    Each is matched **by its text** to a message this reply handed over and
    has not seen taken in (`sent_steers`), the newest first. Never by count:
    a text this reply did not hand over -- another turn's, a stale one, a
    device's invention -- puts nothing back in the queue. What is matched is
    queued again, for the drain after this reply to send as the next turn."""
    waiting = dict(generation.sent_steers)
    back: list[str] = []
    for text in reversed(texts):
        match = next((i for i in reversed(waiting) if _same(waiting[i], text)), None)
        if match is not None:
            waiting.pop(match)
            back.append(match)
    _requeue_ids(generation, [i for i in generation.sent_steers if i in back])


def _requeue_ids(generation, ids: list[str]) -> None:
    """Put these messages, handed to the device from the queue, back in it."""
    from lembas.db.models import Message
    from lembas.db.session import session_scope

    if not ids:
        return
    generation.injected_ids = [i for i in generation.injected_ids if i not in ids]
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
    except Exception:  # bookkeeping; the next link asks again
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
    except Exception as e:  # one request on one device's link
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
    except Exception:  # a link is not refused over bookkeeping
        log.warning("could not read the pending deletes of %s", link.token_id, exc_info=True)


async def send_pending_deletes(link: Link) -> None:
    """After `initialize`: the deletes this device missed, one by one. A device
    without the `delete` capability is left with them until it has it."""
    if not link.can("delete"):
        return
    try:
        pending = pending_deletes(link.token_id)
    except Exception:  # as tombstone_pending
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


def _jobs_link(token_id: str | None, session_id: str | None) -> Link:
    """The link a job request goes over, or why it cannot (`DeviceError`)."""
    link = link_for(token_id)
    if link is None:
        raise DeviceError("the device is not linked right now")
    if not session_id or not link.can("jobs"):
        raise DeviceError(
            "the device's LLeMbas CLI does not show its jobs from here; update it there"
        )
    return link


def shows_jobs(token_id: str | None) -> bool:
    """Whether a device chat's strip has anything to ask: the device is
    linked and lists `jobs`."""
    return offers(token_id, "jobs")


async def list_jobs(token_id: str | None, session_id: str | None) -> list[dict[str, Any]]:
    """`_lembas/jobs`: the session's background jobs, running ones first, as
    the strip reads them. Empty for a device that cannot say."""
    try:
        link = _jobs_link(token_id, session_id)
    except DeviceError:
        return []
    from lembas.services import jobs as jobs_service

    answer = await link.request("_lembas/jobs", {"sessionId": session_id}, timeout=15)
    raw = (answer or {}).get("jobs") if isinstance(answer, dict) else None
    jobs = [jobs_service.device_info(j) for j in raw or [] if isinstance(j, dict)]
    return [j for j in jobs if j["id"]]


async def stop_job(token_id: str | None, session_id: str | None, job_id: str) -> str:
    """`_lembas/job/stop`: the person stops a job. Returns the state after."""
    link = _jobs_link(token_id, session_id)
    answer = await link.request(
        "_lembas/job/stop", {"sessionId": session_id, "id": job_id}, timeout=30
    )
    return str((answer or {}).get("state") or "stopped") if isinstance(answer, dict) else "stopped"


async def send_job(token_id: str | None, session_id: str | None, job_id: str, text: str) -> str:
    """`_lembas/job/send`: the person's message to a subagent job -- taken in
    at its next step, or starting a finished one again. Returns the state."""
    link = _jobs_link(token_id, session_id)
    answer = await link.request(
        "_lembas/job/send", {"sessionId": session_id, "id": job_id, "text": text}, timeout=30
    )
    return str((answer or {}).get("state") or "running") if isinstance(answer, dict) else "running"


async def job_transcript(
    token_id: str | None, session_id: str | None, job_id: str
) -> list[dict[str, Any]]:
    """`_lembas/job/transcript`: a subagent's conversation so far, in the shape
    `services/jobs.transcript` gives for a helper here, so one panel draws
    both. Everything in it came off the device: bounded, and escaped where
    it is shown."""
    link = _jobs_link(token_id, session_id)
    answer = await link.request(
        "_lembas/job/transcript", {"sessionId": session_id, "id": job_id}, timeout=30
    )
    from lembas.services import tool_blocks

    turns: list[dict[str, Any]] = []
    raw_turns = (answer or {}).get("turns") if isinstance(answer, dict) else None
    for raw in raw_turns or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "")
        text = str(raw.get("text") or "")[:MAX_REPLY_CHARS]
        if role == "user":
            turns.append({"role": "user", "text": text, "machine": bool(raw.get("machine"))})
        elif role == "assistant":
            tools = []
            for tool in raw.get("tools") or []:
                if not isinstance(tool, dict):
                    continue
                event = _tool_event(tool)
                tools.append(
                    {
                        "name": str(tool.get("name") or "tool")[:60],
                        "title": tool_blocks.block_for(event).title[:200],
                        "isError": bool(tool.get("isError")),
                    }
                )
            turns.append({"role": "assistant", "text": text, "tools": tools, "error": ""})
    return turns


# --- Spec 3.4.0: an agent chat's tree, read and stopped through the link ----------------
ON_THE_DEVICE = "it is kept on the device, which is not linked right now"


def _tree_link(token_id: str | None, session_id: str | None) -> Link:
    """The link a tree request goes over (capability `tree`), or why it
    cannot (`DeviceError`)."""
    link = link_for(token_id)
    if link is None:
        raise DeviceError(ON_THE_DEVICE)
    if not session_id or not link.can("tree"):
        raise DeviceError(
            "the device's LLeMbas CLI does not show its subagents from here; update it there"
        )
    return link


def _turns(raw_turns: Any) -> list[dict[str, Any]]:
    """`_lembas/session/history`-shaped turns, bounded, as `job_transcript`
    reads them: one panel draws a helper here and a device's subagent alike."""
    from lembas.services import tool_blocks

    turns: list[dict[str, Any]] = []
    for raw in raw_turns or []:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "")
        text = str(raw.get("text") or "")[:MAX_REPLY_CHARS]
        if role == "user":
            turns.append({"role": "user", "text": text, "machine": bool(raw.get("machine"))})
        elif role == "assistant":
            tools = []
            for tool in (raw.get("tools") or [])[:500]:
                if not isinstance(tool, dict):
                    continue
                event = _tool_event(tool)
                tools.append(
                    {
                        "name": str(tool.get("name") or "tool")[:60],
                        "title": tool_blocks.block_for(event).title[:200],
                        "isError": bool(tool.get("isError")),
                    }
                )
            turns.append({"role": "assistant", "text": text, "tools": tools, "error": ""})
    return turns


ROOM_KINDS = ("message", "report", "assignment", "request", "decision")


async def team_view(
    token_id: str | None, session_id: str | None, since: int = 0
) -> dict[str, Any]:
    """`_lembas/team`: the tree and the room's messages above `since`, as the
    device answers them, cleaned. `{tree, room}`; tree None for no subagents."""
    link = _tree_link(token_id, session_id)
    answer = await link.request(
        "_lembas/team", {"sessionId": session_id, "since": max(0, int(since))}, timeout=15
    )
    answer = answer if isinstance(answer, dict) else {}
    room = []
    for raw in (answer.get("room") or [])[:1000]:
        if not isinstance(raw, dict):
            continue
        try:
            n = int(raw.get("n") or 0)
        except (TypeError, ValueError):
            continue
        kind = str(raw.get("kind") or "message")
        room.append(
            {
                "n": n,
                "from": str(raw.get("from") or "")[:40],
                "to": [str(x)[:40] for x in (raw.get("to") or [])][:50],
                "text": str(raw.get("text") or "")[:MAX_NOTE_CHARS],
                "report": bool(raw.get("report")),
                "kind": kind if kind in ROOM_KINDS else "message",
                "time": raw.get("time") if isinstance(raw.get("time"), int) else None,
            }
        )
    return {"tree": clean_tree(answer.get("tree")), "room": room}


async def team_transcript(
    token_id: str | None, session_id: str | None, agent_id: str
) -> dict[str, Any]:
    """`_lembas/team/transcript`: a subagent's conversation, read-only."""
    link = _tree_link(token_id, session_id)
    answer = await link.request(
        "_lembas/team/transcript", {"sessionId": session_id, "id": agent_id}, timeout=30
    )
    answer = answer if isinstance(answer, dict) else {}
    return {
        "id": str(answer.get("id") or agent_id)[:16],
        "state": str(answer.get("state") or "")[:20],
        "turns": _turns(answer.get("turns")),
    }


async def team_findings(
    token_id: str | None, session_id: str | None, agent_id: str
) -> dict[str, Any]:
    """`_lembas/team/findings`: a subagent's findings, ended ones' too."""
    link = _tree_link(token_id, session_id)
    answer = await link.request(
        "_lembas/team/findings", {"sessionId": session_id, "id": agent_id}, timeout=15
    )
    answer = answer if isinstance(answer, dict) else {}
    findings = []
    for raw in (answer.get("findings") or [])[:500]:
        if isinstance(raw, dict):
            findings.append(
                {
                    "n": int(raw.get("n") or 0) if str(raw.get("n") or "0").isdigit() else 0,
                    "text": str(raw.get("text") or "")[:MAX_NOTE_CHARS],
                    "time": raw.get("time") if isinstance(raw.get("time"), int) else None,
                }
            )
    return {"id": str(answer.get("id") or agent_id)[:16], "findings": findings}


async def team_stop(token_id: str | None, session_id: str | None, agent_id: str) -> list[str]:
    """`_lembas/team/stop`: the person stops a subagent and its subtree."""
    link = _tree_link(token_id, session_id)
    answer = await link.request(
        "_lembas/team/stop", {"sessionId": session_id, "id": agent_id}, timeout=30
    )
    stopped = (answer or {}).get("stopped") if isinstance(answer, dict) else None
    return [str(x)[:16] for x in stopped or []]


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
    "DEVICE_ORIGINS",
    "ORIGIN_CARRIED",
    "ORIGIN_UNSURE",
    "HARNESS_TURNS",
    "LINKS",
    "ORIGIN_HARNESS",
    "ORIGIN_TERMINAL",
    "PROTOCOL",
    "PROTOCOLS",
    "PROTOCOL_REFUSAL",
    "DeviceError",
    "Link",
    "LinkClosed",
    "apply_event",
    "cancel_effect",
    "cancel_params",
    "handshake",
    "hello_protocol",
    "link_for",
    "linked_for",
    "reattach_decision",
    "register",
    "reset",
    "run",
    "unregister",
]
