"""A reply as the sequence of steps it actually was.

The three stores a reply writes into -- text, reasoning and tool events -- are
each append-only and each correct. What none of them records is *interleaving*:
where round three's thinking sat relative to round three's command and to the
sentence that came after it. So a bubble was rendered as three zones, all the
thinking, then all the tools, then all the prose, which reads fine on a two-round
answer and is unusable on a forty-round one.

The fix is a table of contents rather than a fourth copy of anything. A **mark**
is written when a round's contribution ends, holding the cumulative length of
each store at that moment; the text between two marks is one step's prose, and so
on. Nothing is duplicated, so `build_messages`, compaction, titling and the copy
button all still see `message.content` as the single string it always was.

**No marks means the old layout.** Every reply written before this existed reads
back an empty list, and `_build` answers that with thinking, then tools, then
text -- exactly what those bubbles have always shown. There is no version flag
and no branch in the template.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lembas.services import tokens as tokens_service
from lembas.services.markdown import open_fence, render_markdown
from lembas.services.reasoning import format_duration

KIND_THINKING = "thinking"
KIND_TEXT = "text"
KIND_TOOLS = "tools"
# What the person added while the reply ran, at the point the model
# took it in: a mark carries `steer: [{id, text}]`, and the step sits after that
# round's tools. Before this, the message stood as a bubble of its own -- live at
# the bottom of the thread, and after a reload above the whole reply.
KIND_STEER = "steer"

# Below this a token count is printed exactly; above it, as `1.4k`. The point is
# where the digits stop meaning anything to a reader.
TOKENS_EXACT_BELOW = 200


@dataclass(frozen=True)
class Step:
    """One thing that happened, in the order it happened.

    `index` is the position of the mark this came from, and the trailing step --
    the one still being written -- takes the index one past the last mark. It is
    what every DOM id in the transcript is derived from, which is what lets an
    open block survive both a stream frame and the `done` frame that replaces the
    whole bubble: the marks are append-only, so index N always means the same
    step, live and afterwards alike.
    """

    index: int
    kind: str
    open: bool = False
    text: str = ""
    html: str = ""
    # A text step's markdown as the model wrote it, fence reopener included --
    # what `html` was rendered from. The app's JSON door sends this and never
    # the HTML; the app renders and sanitises it itself.
    markdown: str = ""
    events: tuple[dict, ...] = field(default_factory=tuple)
    # How long this block thought for and roughly how much it produced. Only on
    # a thinking step, and only where there is something to say -- a block whose
    # duration was never recorded (every row written before the marks carried
    # one) shows the word alone rather than "Thought for 0 seconds".
    label: str = ""


def format_tokens(count: int) -> str:
    """A token count as a person reads it: `86`, or `0.2k` once it is worth it.

    The threshold is where the exact number stops carrying information: nobody
    acts on the difference between 214 and 219 tokens of thinking, and four
    digits beside a spinner is noise. One helper, so the live label and the
    stored one cannot drift into two conventions.
    """
    if count <= 0:
        return ""
    if count <= TOKENS_EXACT_BELOW:
        return str(count)
    return f"{count / 1000:.1f}k"


def thinking_label(*, ms: int, tokens: int, live: bool) -> str:
    """What a thinking block says about itself.

    The two states are one function because they are one sentence with a
    different tense, and because the running one becomes the finished one in
    place -- a reader watching the numbers climb should see them settle, not be
    replaced by something formatted differently.

    The word itself is not here: the live block animates its own ellipsis in CSS
    and the template owns that. This is only what follows it.
    """
    parts = []
    if ms > 0:
        parts.append(format_duration(ms) if not live else _short_duration(ms))
    if counted := format_tokens(tokens):
        # Named: a bare "214" after "Thought for" read as a duration.
        parts.append(f"{counted} tokens")
    text = " · ".join(parts)
    if live or not text:
        return text
    # What follows "Thought": "for 12 seconds · 214 tokens", or, where the time
    # was never measured, "· 214 tokens" -- never "Thought for 214 tokens".
    return f"for {text}" if ms > 0 else f"· {text}"


def _short_duration(ms: int) -> str:
    """`6s`, `1m 04s`. Terser than `format_duration` because it sits beside an
    animating word and changes every second; "less than a second" flickering
    into "1 second" reads as a glitch rather than as a measurement."""
    seconds = max(0, ms) // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m {remainder:02d}s"


def for_message(message: Any) -> list[Step]:
    """Every step of a finished reply, read off the row.

    A Jinja global (see `web/templating.py`) for the reason `tool_label` is: the
    bubble is rendered from four different handlers, and a fifth thing each of
    them had to remember to pass is a fifth thing one of them would forget.
    """
    return _build(
        text=message.content or "",
        thinking=(message.reasoning or "") if not message.error else "",
        events=list(message.tool_calls_json or []),
        marks=list(getattr(message, "steps_json", None) or []),
        whole_ms=int(getattr(message, "reasoning_ms", 0) or 0),
        # A stored reply has a trailing step and it is not being written. Those
        # are two facts and they used to be one flag: `include_open` both
        # emitted the tail and marked it live, so every finished bubble ending
        # in prose carried `msg__body--live` and blinked a caret for ever.
        live=False,
    )


def closed_from(generation: Any, since: int) -> list[Step]:
    """The finished steps of a running reply, from mark `since` onwards.

    Only the new ones, because `_follow` keeps what it has already rendered. A
    closed step never changes again -- that is what closing one means -- so the
    whole prefix does not have to be re-rendered twelve times a second, which is
    what the tool block used to cost on a long reply.
    """
    return _build(
        text=generation.text,
        thinking=generation.thinking,
        events=list(generation.tool_events),
        marks=list(generation.steps),
        since=since,
        include_open=False,
    )


def tail(generation: Any) -> tuple[str, str]:
    """What is being written right now: `(thinking, text)` past the last mark.

    The text carries a fence reopener where one is needed, so a code block
    started before the last tool call goes on rendering as a code block instead
    of the prose underneath it briefly becoming one.
    """
    marks = list(generation.steps)
    last = marks[-1] if marks else {}
    thinking = generation.thinking[_at(last, "thinking_to") :]
    text = generation.text[_at(last, "text_to") :]
    carry = _carry_before(generation.text, marks)
    return thinking, (f"{carry}\n{text}" if carry and text else text)


def _shown(events: list[dict] | tuple[dict, ...]) -> tuple[dict, ...]:
    """The tool calls the transcript shows: all but the plan's (`plan_write`,
    `plan_update`, the former `todo`), which has its own panel above the
    composer and would otherwise repeat the whole plan as a block after every
    tick. Filtered after slicing, so the marks' offsets still count every call."""
    from lembas.services import tool_blocks

    return tuple(
        e for e in events if tool_blocks.kind_of(str(e.get("name") or "")) != tool_blocks.HIDDEN
    )


def _at(mark: dict, key: str) -> int:
    value = mark.get(key, 0)
    return value if isinstance(value, int) and value > 0 else 0


def _build(
    *,
    text: str,
    thinking: str,
    events: list[dict],
    marks: list[dict],
    since: int = 0,
    include_open: bool = True,
    live: bool = True,
    whole_ms: int = 0,
) -> list[Step]:
    """The shared walk.

    Two flags, because they are two questions and conflating them put a blinking
    caret on every finished reply:

    * `include_open` -- emit the trailing step at all. `closed_from` says no,
      because the step still being written is carried by its own frames.
    * `live` -- mark that trailing step as still being written. Only ever true
      of a running generation. A stored reply has a tail and it is finished.

    Every offset is clamped and nothing here raises. A `steps_json` that
    disagrees with the three stores -- a row half-written when the process died,
    a hand-edited one -- has to degrade to a slightly odd order, never to a
    transcript that will not render at all.
    """
    steps: list[Step] = []

    if not marks:
        # The compatibility layout, and the layout of any reply that called
        # nothing: for that one the two orders are the same list, because there
        # are no tool blocks to sit between the prose.
        if thinking:
            # No marks means no per-round timing was ever recorded, so the
            # duration falls back to whatever the row knows about the reply as a
            # whole -- which for a single-round answer is exactly right, and is
            # what every row written before the marks existed carries.
            steps.append(
                Step(
                    index=0,
                    kind=KIND_THINKING,
                    text=thinking,
                    # Nothing baked in while the reply runs: the live block's
                    # numbers come from the `think` frame, which knows the
                    # clock. A label rendered here would be one that never
                    # moved again.
                    label=""
                    if live
                    else thinking_label(
                        ms=whole_ms, tokens=tokens_service.estimate(thinking), live=False
                    ),
                )
            )
        if _shown(events):
            steps.append(Step(index=0, kind=KIND_TOOLS, events=_shown(events)))
        if text:
            steps.append(
                Step(
                    index=0,
                    kind=KIND_TEXT,
                    open=live,
                    html=render_markdown(text),
                    markdown=text,
                )
            )
        return steps

    thought_from = 0
    text_from = 0
    tools_from = 0
    thought_ms_from = 0
    carry = ""

    for index, mark in enumerate(marks):
        thought_to = min(max(_at(mark, "thinking_to"), thought_from), len(thinking))
        text_to = min(max(_at(mark, "text_to"), text_from), len(text))
        tools_to = min(max(_at(mark, "tools_to"), tools_from), len(events))

        thought = thinking[thought_from:thought_to]
        said = text[text_from:text_to]
        ran = events[tools_from:tools_to]

        # Computed for every step even when this one is not being returned:
        # `closed_from` renders a suffix, and whether a fence is open depends on
        # everything before it.
        source = f"{carry}\n{said}" if carry and said else said
        marker, info = open_fence(source)
        if marker:
            source = f"{source}\n{marker}"

        if index >= since:
            # Thinking, then prose, then tools -- the order a model emits them.
            if thought:
                steps.append(
                    Step(
                        index=index,
                        kind=KIND_THINKING,
                        text=thought,
                        label=thinking_label(
                            ms=_at(mark, "thinking_ms") - thought_ms_from,
                            tokens=tokens_service.estimate(thought),
                            live=False,
                        ),
                    )
                )
            if said:
                steps.append(
                    Step(
                        index=index, kind=KIND_TEXT, html=render_markdown(source), markdown=source
                    )
                )
            if _shown(ran):
                steps.append(Step(index=index, kind=KIND_TOOLS, events=_shown(ran)))
            if steered := _steered(mark):
                steps.append(Step(index=index, kind=KIND_STEER, events=steered))

        carry = f"{marker}{info}" if marker else ""
        thought_from, text_from, tools_from = thought_to, text_to, tools_to
        # Cumulative on the mark, so a block's own duration is the difference --
        # the same rule the three lengths above follow.
        thought_ms_from = max(_at(mark, "thinking_ms"), thought_ms_from)

    if not include_open:
        return steps

    # Everything past the last mark. Implicit rather than written, in both the
    # live path and the stored one -- one rule instead of two that could drift.
    index = len(marks)
    if trailing_thought := thinking[thought_from:]:
        # The last round closes no mark -- it is the round that stopped calling
        # tools -- so its duration is whatever the reply spent thinking beyond
        # the last one that did. Zero while the reply is running, where the live
        # block carries its own label instead.
        steps.append(
            Step(
                index=index,
                kind=KIND_THINKING,
                text=trailing_thought,
                label=""
                if live
                else thinking_label(
                    ms=max(0, whole_ms - thought_ms_from),
                    tokens=tokens_service.estimate(trailing_thought),
                    live=False,
                ),
            )
        )
    if trailing_text := text[text_from:]:
        source = f"{carry}\n{trailing_text}" if carry else trailing_text
        steps.append(
            Step(
                index=index,
                kind=KIND_TEXT,
                open=live,
                html=render_markdown(source),
                markdown=source,
            )
        )
    if trailing_events := _shown(events[tools_from:]):
        steps.append(Step(index=index, kind=KIND_TOOLS, events=trailing_events))

    return steps


def _steered(mark: dict) -> tuple[dict, ...]:
    """The messages a mark says were taken in there, as `{id, text}`."""
    entries = mark.get("steer") if isinstance(mark, dict) else None
    if not isinstance(entries, list):
        return ()
    return tuple(
        {"id": str(e.get("id") or ""), "text": str(e.get("text") or "")}
        for e in entries
        if isinstance(e, dict) and str(e.get("text") or "").strip()
    )


def steered_ids(marks: list[dict] | None) -> set[str]:
    """Every message id a reply's marks show inline, so the thread does not draw
    them a second time as bubbles of their own."""
    return {e["id"] for mark in marks or [] for e in _steered(mark) if e["id"]}


def _carry_before(text: str, marks: list[dict]) -> str:
    """The fence still open when the last mark was written, if any."""
    if not marks:
        return ""
    carry = ""
    start = 0
    for mark in marks:
        end = min(max(_at(mark, "text_to"), start), len(text))
        source = f"{carry}\n{text[start:end]}" if carry else text[start:end]
        marker, info = open_fence(source)
        carry = f"{marker}{info}" if marker else ""
        start = end
    return carry


__all__ = ["KIND_TEXT", "KIND_THINKING", "KIND_TOOLS", "Step", "closed_from", "for_message", "tail"]
