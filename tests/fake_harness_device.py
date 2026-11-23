"""A linked device that runs turns, for the tests of a turn its harness starts.

`Device` (test_device_harness_turn.py) answers what a test tells it to and
keeps no state. This one keeps the state a real device has -- the turn running
now, the prompts waiting behind it, the last one finished, the messages steered
in and not yet taken, its history -- and answers from it, in one of two ways:

- **with `harness_turns`** (harness spec 3.10.0): a turn the harness starts is
  a turn like any other -- status says busy and names it, a steer joins it, a
  prompt waits behind it, a steer it ended before taking in comes back as
  `unsent` on its `task` end, and the history marks its user turn `machine`;
- **without it** (a CLI before 3.10.0, still in use): the same turn runs and
  says the same events, but status says `busy: false` and names no turn while
  it runs, a steer is refused then, and a prompt sent then fails.

As on a real device, only the `task` and `prompt` events name their turn
(`turnId`); what a turn says in between does not.

`model_saw` is every message of a person's the device's model was given, in
order: what "reached the model exactly once" is asserted on. `said` is what
each turn's model wrote, by turn id, and `harness_ran` the turns the harness
started: what "one row and its whole reply" is asserted on.

It can also misbehave the ways a real link and a real device do:

- `late_steers`: a steer is taken, and its answer comes after the wait for it
  (the caller sees a timeout);
- `drop()` / `relink()`: the link goes down in the middle of anything and the
  device dials in again -- its turns go on meanwhile, their events said to
  nobody. As in the CLI: a harness turn that ends with nobody listening keeps
  what it had not taken in for the next turn's first step; a prompt's turn
  hands it back in an answer nobody hears;
- turns as short as a test likes, one right after the other;
- `swallow()`: the model takes in what was steered and the device never says
  so -- then hands back something else at the turn's end.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from lembas.services import device_link
from tests.test_device_harness_turn import WITH_TURNS, Device

OLD = WITH_TURNS
NEW = (*WITH_TURNS, "harness_turns")


def line(note: str) -> str:
    """What a harness turn's `prompt` event carries as `text`: the line a
    transcript shows -- for a job's note the line after its mark, for a team's
    note the mark itself."""
    lines = note.splitlines()
    first = lines[0] if lines[0].startswith("[Team ") or len(lines) < 2 else lines[1]
    return f"⏎ {first}"


class HarnessDevice(Device):
    def __init__(self, db, capabilities=NEW):
        super().__init__(db, capabilities, answer=self._answer)
        self.knows = "harness_turns" in capabilities
        self.running = ""
        self.own = False
        self.waiting: list[tuple[str, asyncio.Event]] = []
        self.last = ""
        self.inbox: list[tuple[str, str]] = []
        self.history: list[dict] = []
        self.known: set[str] = set()
        self.model_saw: list[str] = []
        self.refused: list[str] = []
        self.said: dict[str, str] = {}
        self.harness_ran: list[str] = []
        self.late_steers = False
        self.down = False
        self.hand_back: list[str] | None = None
        # Set to an Event: a prompt's turn waits on it before it ends.
        self.pause: asyncio.Event | None = None
        self.cancels: list[str] = []
        # The ids of the steered messages the model was given.
        self.taken: set[str] = set()
        self.withdrawn: set[str] = set()
        self._wire(self.link)

    # --- The link ----------------------------------------------------------------------
    def _wire(self, link) -> None:
        """Requests over this link: answered by the device, and failed with
        `LinkClosed` -- as a real one's are -- when the link drops first."""
        state: dict = {"gone": None}  # a future, made on the running loop

        async def request(method, params=None, *, timeout=60):
            if link.closed:
                raise device_link.LinkClosed("The device is not linked any more.")
            if state["gone"] is None:
                state["gone"] = asyncio.get_running_loop().create_future()
            self.asked.append((method, params or {}))

            async def serve():
                result = self.answer(method, params or {})
                return await result if asyncio.iscoroutine(result) else result

            work = asyncio.ensure_future(serve())
            await asyncio.wait({work, state["gone"]}, return_when=asyncio.FIRST_COMPLETED)
            if work.done():
                return work.result()
            # The device goes on with it; its answer reaches nobody.
            work.add_done_callback(lambda f: f.cancelled() or f.exception())
            raise device_link.LinkClosed("The device's link dropped.")

        link.request = request  # type: ignore[method-assign]
        link._fake = state
        said = link.send_text

        async def send_text(text):
            await said(text)
            message = json.loads(text)
            if message.get("method") == "session/cancel" and "id" not in message:
                await self._cancel(message.get("params") or {})

        link.send_text = send_text

    async def _cancel(self, params: dict) -> None:
        """`session/cancel`. Without a turn id (all a device before 3.10.0
        knows): the running turn stops, whoever started it. With one (spec
        3.10.0, `harness_turns`): only that turn -- stopped when it runs,
        taken out of the queue when it waits (it never runs, and its prompt
        is answered `cancelled`), nothing when it is neither."""
        turn = str(((params.get("_meta") or {}).get("lembas") or {}).get("turnId") or "")
        self.cancels.append(turn)
        if not turn or not self.knows:
            if self.running:
                await self.end(stopped=True)
            return
        if turn == self.running:
            await self.end(stopped=True)
            return
        for waiting, gate in list(self.waiting):
            if waiting == turn:
                self.waiting.remove((waiting, gate))
                self.withdrawn.add(turn)
                self.known.add(turn)
                gate.set()

    def drop(self) -> None:
        """The link goes down. The device's turns go on."""
        self.down = True
        link = self.link
        device_link.unregister(link)
        gone = link._fake["gone"]
        if gone is not None and not gone.done():
            gone.set_result(None)

    def relink(self) -> None:
        """The device dials in again: a new link, the same sessions."""
        old = self.link
        link = device_link.Link(
            token_id=old.token_id, user_id=old.user_id, name=old.name,
            send_text=old.send_text, close=old.close, hello=dict(old.hello),
        )
        link.initialized = old.initialized
        self._wire(link)
        device_link.LINKS[old.token_id] = link
        self.link = link
        self.down = False

    async def emit(self, *events, session="s1"):
        if self.down:
            return  # said to nobody
        await super().emit(*events, session=session)

    # --- What it answers -------------------------------------------------------------
    def status(self) -> dict:
        named = bool(self.running) and (self.knows or not self.own)
        out: dict = {
            "sessionId": "s1", "exists": True, "held": "here", "busy": named,
            "queued": [turn for turn, _ in self.waiting],
        }
        if named:
            out["turnId"] = self.running
        if self.last:
            out["lastTurnId"] = self.last
        return out

    async def _answer(self, method, params):
        if method == "_lembas/session/status":
            return self.status()
        if method == "_lembas/session/history":
            return {"sessionId": "s1", "title": "", "root": "/home/dev/app",
                    "turns": [dict(t) for t in self.history]}
        if method == "_lembas/steer":
            if not self.running or (self.own and not self.knows):
                self.refused.append(method)
                raise device_link.DeviceError(
                    "this session is not working on anything; send it as a prompt"
                )
            self.inbox.append((str(params["text"]).strip(), str(params.get("messageId") or "")))
            if self.late_steers:
                raise TimeoutError
            return {"queued": len(self.inbox)}
        if method == "session/prompt":
            return await self._prompt(params)
        return {}

    async def _prompt(self, params):
        meta = (params.get("_meta") or {}).get("lembas") or {}
        turn = str(meta.get("turnId") or uuid.uuid4().hex)
        text = str(params["prompt"][0]["text"])
        if turn in self.known:
            # A known id does not run again.
            return None
        # Nor a message it was steered and still knows (3.10.0): nothing runs,
        # and the answer says what became of the message.
        if self.knows:
            fate = ""
            if turn in self.taken:
                fate = "taken"
            elif any(i == turn for _, i in self.inbox):
                fate = "waiting" if self.running else "carried"
            if fate:
                return {"stopReason": "end_turn",
                        "_meta": {"lembas": {"turnId": turn, "steered": fate}}}
        if self.running:
            if self.own and not self.knows:
                self.refused.append(method := "session/prompt")
                raise device_link.DeviceError(f"busy ({method} during a turn of the harness's)")
            gate = asyncio.Event()
            self.waiting.append((turn, gate))
            await gate.wait()
            if turn in self.withdrawn:
                return {"stopReason": "cancelled", "_meta": {"lembas": {"turnId": turn}}}
        self.running, self.own = turn, False
        self.known.add(turn)
        await self.emit(
            {"type": "task", "state": "start", "turnId": turn},
            {"type": "prompt", "text": text, "turnId": turn},
        )
        self.model_saw.append(text)
        self.history.append({"role": "user", "text": text, "turnId": turn})
        self.history.append({"role": "assistant", "text": "", "reasoning": "", "tools": []})
        await self.first_step()
        await self.say(f"Answer to: {text}")
        if self.pause is not None:
            await self.pause.wait()
        unsent = await self.end()
        said: dict = {"turnId": turn}
        if unsent:
            said["unsent"] = unsent
        return {"stopReason": "end_turn", "_meta": {"lembas": said}}

    # --- What a test makes it do -----------------------------------------------------
    async def wake(self, note: str, turn: str, *, prompt: bool = True) -> None:
        """A note wakes the idle session: the harness starts a turn by itself.
        `prompt=False`: only its `task` start is said yet (`say_prompt` says
        the rest) -- the two are separate messages on the link."""
        assert not self.running, "turns never overlap"
        self.running, self.own = turn, True
        self.known.add(turn)
        self.harness_ran.append(turn)
        self.note = note
        mark = {"machine": True} if self.knows else {}
        self.history.append({"role": "user", "text": note, "turnId": turn, **mark})
        self.history.append({"role": "assistant", "text": "", "reasoning": "", "tools": []})
        await self.emit({"type": "task", "state": "start", "turnId": turn})
        if prompt:
            await self.say_prompt()
            await self.first_step()

    async def say_prompt(self) -> None:
        await self.emit(
            {"type": "prompt", "text": line(self.note), "machine": True, "turnId": self.running}
        )

    async def first_step(self) -> None:
        """What an earlier turn left waiting goes in at this one's first step."""
        if self.inbox:
            await self.take_in()

    async def say(self, text: str) -> None:
        """The model writes: an event, and the same words in the history."""
        if not self.history or self.history[-1]["role"] == "user":
            self.history.append({"role": "assistant", "text": "", "reasoning": "", "tools": []})
        self.history[-1]["text"] += text
        self.said[self.running] = self.said.get(self.running, "") + text
        await self.emit({"type": "text", "text": text})

    def swallow(self, hand_back: list[str]) -> None:
        """The model takes in what was steered, the device says no `steered`,
        and the turn's end will hand back these texts instead."""
        taken, self.inbox = self.inbox, []
        self.model_saw.extend(text for text, _ in taken)
        self.taken.update(i for _, i in taken if i)
        self.hand_back = hand_back

    async def take_in(self) -> None:
        """The turn's next step: what was steered in goes to the model."""
        taken, self.inbox = self.inbox, []
        self.model_saw.extend(text for text, _ in taken)
        self.taken.update(i for _, i in taken if i)
        await self.emit({
            "type": "steered", "texts": [t for t, _ in taken], "ids": [i for _, i in taken],
        })

    async def end(self, *, stopped: bool = False) -> list[str]:
        """The running turn ends. What it had not taken in is handed back: on
        the `task` end of a harness turn (3.10.0), else returned for the
        prompt's answer."""
        turn, own = self.running, self.own
        if self.down:
            # Nobody is listening: what it had not taken in is kept with the
            # session and goes in at the next turn's first step (said there
            # with `steered` and its id) -- never also handed back.
            unsent = []
        else:
            unsent, self.inbox = [text for text, _ in self.inbox], []
        if self.hand_back is not None:
            unsent, self.hand_back = self.hand_back, None
        end: dict = {"type": "task", "state": "end", "turnId": turn}
        if own and unsent:
            end["unsent"] = unsent
        self.running, self.own = "", False
        if self.knows or not own:
            self.last = turn
        if stopped:
            await self.emit({"type": "done", "reason": "cancelled"})
        await self.emit(end)
        if self.waiting:
            self.waiting.pop(0)[1].set()
        return unsent

    def sent_methods(self) -> list[str]:
        """Every request made of it that a turn could be made of, in order."""
        return [m for m, _ in self.asked if m in ("session/prompt", "_lembas/steer")]


def invariant(device: HarnessDevice, rows: list, withdrawn: tuple = ()) -> None:
    """Every message the chat shows as the person's reaches the model exactly
    once; every harness turn the device ran has one row and its whole reply;
    no reply row holds another turn's text.

    A message the chat shows as *waiting* has reached it at most once: one
    still in the queue, and one marked as of unknown fate (`unsure`), which
    the chat says is not known to have arrived."""
    for row in rows:
        if row.role != "user" or row.machine or row.origin not in ("", "unsure"):
            continue
        seen = device.model_saw.count(row.content)
        if row.id in withdrawn:
            # Its person stopped it before it ran: the chat shows that reply
            # as stopped, and the model never read the message.
            assert seen == 0, f"{row.content!r} was withdrawn and reached the model"
        elif row.queued:
            assert seen <= 1, f"{row.content!r} reached the model {seen} times while waiting"
        else:
            assert seen == 1, f"{row.content!r} reached the model {seen} times"
    for turn in device.harness_ran:
        if turn == device.running:
            continue
        at = [n for n, row in enumerate(rows) if row.role == "user" and row.device_turn_id == turn]
        assert len(at) == 1, f"harness turn {turn} has {len(at)} rows"
        reply = next((row for row in rows[at[0] + 1:] if row.role == "assistant"), None)
        assert reply is not None, f"harness turn {turn} has no reply"
        assert reply.content == device.said.get(turn, ""), f"harness turn {turn}: {reply.content!r}"
    whole = set(device.said.values()) | {""}
    for row in rows:
        if row.role == "assistant":
            assert row.content in whole, f"a reply holds {row.content!r}: not one turn's text"
