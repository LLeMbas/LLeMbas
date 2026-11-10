"""Progress: one way to say what a reply is doing (harness spec 3.4.0, `progress.md`).

Every client -- the web page, Desktop, the CLI, Android -- draws the same line
for the same object, from the same words. The object is
`{"phase", "since", ...data}`: one of the spec's thirteen phases, when it began
(Unix milliseconds) and the phase's data, a field left out when it is not known.
The texts are `loop.json` `progress`, and they are the shared i18n keys: the
English sentence with its `{{fields}}` is the key in every catalogue here.

Three parts:

* `text` and `line`, pure: the phase's words and `{{text}} · {{elapsed}}`,
  driven by the spec's `conformance/progress.json`.
* `Tracker`, one per reply (`Generation.tracker`): who knows a phase sets it,
  and `since` is kept while the phase is the same one for the same thing.
* `current(generation)`: the object the streams send -- an open card overrides
  whatever the tracker says (the reply waits for the person), and a finished
  reply says how it ended.

What a model reads is never this: progress is for a person.
"""

from __future__ import annotations

import math
import time
from typing import Any

from lembas.services import harness_spec


def spec() -> dict[str, Any]:
    return harness_spec.loop()["progress"]


def phases() -> list[str]:
    return list(spec()["phases"])


def now_ms() -> int:
    return int(time.time() * 1000)


def _fill(text: str, values: dict[str, Any]) -> str:
    return harness_spec.fill(text, {k: str(v) for k, v in values.items()})


def _rate(value: float) -> str:
    # Half up at one decimal, as JavaScript's toFixed does for the cases the
    # spec writes (12.46 → 12.5); `round` alone is banker's rounding.
    return f"{math.floor(float(value) * 10 + 0.5) / 10:.1f}"


def text(progress: dict[str, Any], now: int | None = None) -> str:
    """The phase's text (progress.md §2), its fields filled."""
    texts = spec()["texts"]
    phase = progress.get("phase")
    if phase == "queued":
        if progress.get("ahead") is not None:
            return _fill(texts["queued.ahead"], {"ahead": int(progress["ahead"])})
        return texts["queued"]
    if phase == "waiting_for_model":
        reason = str(progress.get("reason") or "")
        return _fill(
            texts["waiting_for_model"],
            {"model": progress.get("model") or "", "reason": spec()["reason"].get(reason, reason)},
        )
    if phase == "loading_model":
        return _fill(texts["loading_model"], {"model": progress.get("model") or ""})
    if phase == "reading_prompt":
        done, total = progress.get("done"), progress.get("total")
        if done is None or not total or int(total) <= 0:
            return texts["reading_prompt.bare"]
        percent = int(done) * 100 // int(total)
        return _fill(
            texts["reading_prompt"], {"done": int(done), "total": int(total), "percent": percent}
        )
    if phase == "writing":
        rate = progress.get("rate")
        if rate is None or not math.isfinite(float(rate)):
            return texts["writing.bare"]
        return _fill(texts["writing"], {"rate": _rate(rate)})
    if phase == "running_tool":
        if progress.get("what"):
            values = {"tool": progress.get("tool") or "", "what": progress["what"]}
            return _fill(texts["running_tool"], values)
        return _fill(texts["running_tool.bare"], {"tool": progress.get("tool") or ""})
    if phase == "waiting_for_person":
        words = spec()["what"]
        what = str(progress.get("what") or "")
        if what == "approval":
            said = (
                _fill(words["approval"], {"tool": progress["tool"]})
                if progress.get("tool")
                else words["approval.bare"]
            )
        else:
            said = words.get(what, what)
        return _fill(texts["waiting_for_person"], {"what": said})
    if phase == "waiting_for_subagents":
        return _fill(texts["waiting_for_subagents"], {"running": int(progress.get("running") or 0)})
    if phase == "retrying":
        since = int(progress.get("since") or 0)
        clock = now_ms() if now is None else now
        left = max(0, int(progress.get("in") or 0) - (clock - since) // 1000)
        return _fill(texts["retrying"], {"in": left, "reason": progress.get("reason") or ""})
    if phase == "stopped":
        reason = progress.get("reason")
        if reason:
            said = spec()["stopped_reason"].get(str(reason), str(reason))
            return _fill(texts["stopped.reason"], {"reason": said})
        return texts["stopped"]
    if phase == "failed":
        return _fill(texts["failed"], {"reason": progress.get("reason") or ""})
    return texts.get(str(phase), str(phase))


def elapsed(milliseconds: int) -> str:
    """Whole seconds, as a ticking clock writes durations: 5s, 1m 05s, 1h 02m."""
    total = max(0, int(milliseconds) // 1000)
    if total < 60:
        return f"{total}s"
    minutes, rest = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def line(progress: dict[str, Any], now: int | None = None) -> str:
    """The line a client draws (progress.md §3)."""
    clock = now_ms() if now is None else now
    said = text(progress, clock)
    if progress.get("phase") in spec()["no_elapsed"]:
        return said
    return _fill(spec()["line"], {"text": said, "elapsed": elapsed(clock - int(progress["since"]))})


def what_of(arguments: Any) -> str:
    """A call's short title (`running_tool`'s `what`): the command, the path,
    the query -- one line, at most `what_max` characters, or ""."""
    if not isinstance(arguments, dict):
        return ""
    for key in ("command", "path", "file_path", "pattern", "query", "url", "description", "name"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            first = value.strip().split("\n")[0].strip()
            limit = int(spec()["what_max"])
            return first if len(first) <= limit else first[: limit - 1] + "…"
    return ""


def i18n_keys() -> list[str]:
    """Every English sentence a client draws progress with: the phases'
    texts, the reasons, the words for `what`, the stopped reasons and the line.
    The catalogues are keyed by them, `{{fields}}` and all."""
    shape = spec()
    keys = [*shape["texts"].values(), *shape["reason"].values(), *shape["what"].values()]
    keys += [*shape["stopped_reason"].values(), shape["line"]]
    return list(dict.fromkeys(keys))


def strings(translate) -> dict[str, Any]:
    """What a page's script needs to draw the line in its language: the
    spec's tables with each sentence translated (`translate(english)`)."""
    shape = spec()
    return {
        "texts": {k: translate(v) for k, v in shape["texts"].items()},
        "reason": {k: translate(v) for k, v in shape["reason"].items()},
        "what": {k: translate(v) for k, v in shape["what"].items()},
        "stopped_reason": {k: translate(v) for k, v in shape["stopped_reason"].items()},
        "line": translate(shape["line"]),
        "no_elapsed": list(shape["no_elapsed"]),
    }


# --- One reply's progress --------------------------------------------------------------
_SAME_THING = {
    "running_tool": ("tool", "what"),
    "waiting_for_model": ("model", "reason"),
    "loading_model": ("model",),
    "waiting_for_person": ("what", "tool"),
    "retrying": ("attempt", "reason"),
}


def same_phase(current: dict[str, Any] | None, phase: str, data: dict[str, Any]) -> bool:
    """Whether `phase` with `data` is the phase already running, for the same
    thing: then `since` is kept (progress.md §1)."""
    if not current or current.get("phase") != phase:
        return False
    return all(current.get(k) == data.get(k) for k in _SAME_THING.get(phase, ()))


def clean(phase: str, data: dict[str, Any]) -> dict[str, Any]:
    """Only the phase's own fields, and none that is unknown."""
    fields = spec()["fields"].get(phase, {})
    return {k: v for k, v in data.items() if k in fields and v is not None and v != ""}


class Tracker:
    """The progress of one reply, as whoever knows the phase sets it.

    `version` moves on every change so a follower can tell; `data_at` is when
    the data last changed within the same phase, so followers can keep a
    data-only change to `pace_per_second`."""

    def __init__(self) -> None:
        self.current: dict[str, Any] | None = None
        self.version = 0

    def set(self, phase: str | None, **data: Any) -> None:
        if phase is None:
            if self.current is not None:
                self.current = None
                self.version += 1
            return
        fields = clean(phase, data)
        if same_phase(self.current, phase, fields):
            merged = {"phase": phase, "since": self.current["since"], **fields}
            if merged != self.current:
                self.current = merged
                self.version += 1
            return
        self.current = {"phase": phase, "since": now_ms(), **fields}
        self.version += 1

    def clear(self) -> None:
        self.set(None)


def current(generation: Any) -> dict[str, Any] | None:
    """What a reply is doing now, as the streams send it.

    An open card is the person's turn whatever else is going on; a finished
    reply says how it ended (`stopped`, `failed`) and one that finished
    normally has none."""
    if getattr(generation, "done", False):
        tracker = getattr(generation, "tracker", None)
        last = tracker.current if tracker is not None else None
        if last and last.get("phase") in ("stopped", "failed"):
            return last
        return None
    waiting = list(getattr(generation, "waiting", None) or [])
    if waiting:
        return _waiting_for_person(generation, waiting)
    tracker = getattr(generation, "tracker", None)
    return tracker.current if tracker is not None else None


def _waiting_for_person(generation: Any, waiting: list) -> dict[str, Any]:
    from lembas.services import interaction

    first = waiting[0]
    items = list(getattr(first, "items", ()) or ())
    item = items[0] if items else None
    kind = getattr(item, "kind", "")
    if kind == interaction.KIND_APPROVAL:
        data = {"what": "approval", "tool": getattr(item, "tool_name", "") or None}
    elif kind == getattr(interaction, "KIND_PLAN", "plan") or getattr(first, "plan", None):
        data = {"what": "plan"}
    else:
        data = {"what": "question"}
    tracker = getattr(generation, "tracker", None)
    if tracker is None:
        fields = clean("waiting_for_person", data)
        return {"phase": "waiting_for_person", "since": now_ms(), **fields}
    tracker.set("waiting_for_person", **data)
    return tracker.current


__all__ = [
    "Tracker",
    "clean",
    "current",
    "elapsed",
    "i18n_keys",
    "line",
    "now_ms",
    "phases",
    "same_phase",
    "spec",
    "strings",
    "text",
    "what_of",
]
