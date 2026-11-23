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
order: what "reached the model exactly once" is asserted on.
"""

from __future__ import annotations

import asyncio
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
            self.inbox.append((str(params["text"]), str(params.get("messageId") or "")))
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
        if self.running:
            if self.own and not self.knows:
                self.refused.append(method := "session/prompt")
                raise device_link.DeviceError(f"busy ({method} during a turn of the harness's)")
            gate = asyncio.Event()
            self.waiting.append((turn, gate))
            await gate.wait()
        self.running, self.own = turn, False
        self.known.add(turn)
        await self.emit(
            {"type": "task", "state": "start", "turnId": turn},
            {"type": "prompt", "text": text, "turnId": turn},
        )
        self.model_saw.append(text)
        self.history.append({"role": "user", "text": text, "turnId": turn})
        self.history.append({"role": "assistant", "text": "", "reasoning": "", "tools": []})
        await self.say(f"Answer to: {text}")
        unsent = await self.end()
        said: dict = {"turnId": turn}
        if unsent:
            said["unsent"] = unsent
        return {"stopReason": "end_turn", "_meta": {"lembas": said}}

    # --- What a test makes it do -----------------------------------------------------
    async def wake(self, note: str, turn: str) -> None:
        """A note wakes the idle session: the harness starts a turn by itself."""
        assert not self.running, "turns never overlap"
        self.running, self.own = turn, True
        self.known.add(turn)
        mark = {"machine": True} if self.knows else {}
        self.history.append({"role": "user", "text": note, "turnId": turn, **mark})
        self.history.append({"role": "assistant", "text": "", "reasoning": "", "tools": []})
        await self.emit(
            {"type": "task", "state": "start", "turnId": turn},
            {"type": "prompt", "text": line(note), "machine": True, "turnId": turn},
        )

    async def say(self, text: str) -> None:
        """The model writes: an event, and the same words in the history."""
        if not self.history or self.history[-1]["role"] == "user":
            self.history.append({"role": "assistant", "text": "", "reasoning": "", "tools": []})
        self.history[-1]["text"] += text
        await self.emit({"type": "text", "text": text})

    async def take_in(self) -> None:
        """The turn's next step: what was steered in goes to the model."""
        taken, self.inbox = self.inbox, []
        self.model_saw.extend(text for text, _ in taken)
        await self.emit({
            "type": "steered", "texts": [t for t, _ in taken], "ids": [i for _, i in taken],
        })

    async def end(self, *, stopped: bool = False) -> list[str]:
        """The running turn ends. What it had not taken in is handed back: on
        the `task` end of a harness turn (3.10.0), else returned for the
        prompt's answer."""
        turn, own = self.running, self.own
        unsent, self.inbox = [text for text, _ in self.inbox], []
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
