"""What a reply cost, how fast it arrived, and how full the window is.

One shape, built either from a generation still being written or from the row
it left behind. That matters more than it looks: the finished bubble is
re-rendered from the database the instant the stream ends, so if the live
numbers and the stored ones came from different code they would visibly jump at
exactly the moment the reader is looking at them. Here the only thing that
changes when a reply finishes is that an estimate may become exact.

Nothing here is authoritative about tokens. `estimated` says which kind of
number this is, and every surface that shows one has to say so too.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lembas.services import tokens

# Where the context bar changes colour. Not thresholds anyone tunes: they mark
# "worth noticing" and "about to be a problem", and the second is deliberately
# below the default compaction threshold so the warning arrives first.
WARNING_AT = 80
DANGER_AT = 95


@dataclass(frozen=True)
class Metrics:
    """Token counts and timing for one reply."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # What the window holds after this turn: the last round's prompt plus its
    # completion. Distinct from prompt+completion summed over tool rounds, which
    # is what the reply *cost* -- a three-round reply pays for its prompt three
    # times but only ever occupies the window once.
    context_tokens: int = 0
    context_limit: int = 0
    estimated: bool = False
    elapsed_ms: int = 0
    rounds: int = 1
    # How long of `elapsed_ms` the model spent thinking, summed over rounds
    # -- shown beside the time, which was before only a tooltip.
    thinking_ms: int = 0

    @property
    def percent(self) -> int:
        """How full the window is, or 0 when nobody has said how big it is."""
        if self.context_limit <= 0 or self.context_tokens <= 0:
            return 0
        return min(100, round(self.context_tokens * 100 / self.context_limit))

    @property
    def tokens_per_second(self) -> float:
        if self.elapsed_ms <= 0 or self.completion_tokens <= 0:
            return 0.0
        return self.completion_tokens / (self.elapsed_ms / 1000)

    @property
    def pressure(self) -> str:
        """"", "warning" or "danger" -- the class the context chip takes."""
        percent = self.percent
        if not percent:
            return ""
        if percent >= DANGER_AT:
            return "danger"
        if percent >= WARNING_AT:
            return "warning"
        return ""

    @property
    def has_anything(self) -> bool:
        return bool(self.total_tokens or self.completion_tokens or self.elapsed_ms)


def from_generation(generation: Any) -> Metrics:
    """Metrics for a reply still being written.

    A reported count is never second-guessed. Where the endpoint has said a
    number, that number is what is shown; our own estimate is four characters to
    a token and is wrong enough on code and CJK that overriding an exact figure
    with it would be a downgrade dressed as a fix.

    What the estimate is for is the gap *between* reported counts. Usage arrives
    once per round, so on a forty-round agent reply the counts used to stand
    still for minutes at a time while text streamed underneath them -- reported
    was non-zero from round one onwards, so the `or` below never reached its
    fallback again. `_since_counted` closes that gap: it is what has been written
    since the last usage chunk, and it is zero at the moment one lands. So the
    figures climb while a round runs and land exactly on the reported total when
    it ends, which is the same property in both directions.

    The prompt is deliberately not treated that way. It does not grow within a
    round -- it is the request that was sent -- so there is nothing to interpolate
    and nothing that would freeze.
    """
    import time

    # Zero the instant a usage chunk lands, so a reported figure is passed
    # through untouched and only the interval between them is filled in.
    extra = _since_counted(generation)

    completion = (generation.completion_tokens + extra) or tokens.estimate(
        generation.text + generation.thinking
    )
    # `prompt_estimate_total`, not `prompt_estimate`. The two answer different
    # questions -- every round's prompt against the latest round's -- and this
    # chip is what the reply cost, which is the sum. Reading the latest one here
    # while the end-of-reply path stored the total made the number visibly jump
    # at the `done` frame on any reply that called a tool.
    prompt = generation.prompt_tokens or generation.prompt_estimate_total
    elapsed = generation.elapsed_ms or (
        int((time.monotonic() - generation.started_at) * 1000) if generation.started_at else 0
    )

    return Metrics(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        context_tokens=(generation.context_tokens + extra)
        or (generation.prompt_estimate + completion),
        context_limit=generation.context_limit,
        # One recorded fact rather than an inference from two counts. Inferring
        # it read `False` once the end-of-reply fallback had filled both fields
        # in, so a reply estimated from beginning to end showed `~` throughout
        # and then dropped it at the moment it was stored -- the tilde vanishing
        # exactly where it was most needed.
        estimated=not generation.reported_usage,
        elapsed_ms=elapsed,
        rounds=max(1, generation.rounds),
        thinking_ms=generation.thinking_ms + generation.round_thinking_ms,
    )


def _since_counted(generation: Any) -> int:
    """Tokens written since the last usage chunk, estimated.

    Zero before any usage has been reported -- the `or` fallbacks in
    `from_generation` cover that case whole -- and zero again the moment each
    chunk lands, because `counted_chars` is stamped there. In between it is the
    only thing that moves.
    """
    if not generation.reported_usage:
        return 0
    written = len(generation.text) + len(generation.thinking)
    return tokens.estimate_chars(max(0, written - generation.counted_chars))


def from_message(usage_json: dict[str, Any] | None) -> Metrics:
    """Metrics for a finished reply, read back off the row."""
    stored = usage_json or {}

    def _int(key: str) -> int:
        value = stored.get(key)
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    return Metrics(
        prompt_tokens=_int("prompt_tokens"),
        completion_tokens=_int("completion_tokens"),
        total_tokens=_int("total_tokens"),
        context_tokens=_int("context_tokens"),
        context_limit=_int("context_limit"),
        estimated=bool(stored.get("estimated")),
        elapsed_ms=_int("elapsed_ms"),
        rounds=max(1, _int("rounds")),
        thinking_ms=_int("thinking_ms"),
    )


def to_json(metrics: Metrics) -> dict[str, Any]:
    """The shape stored in Message.usage_json."""
    return {
        "prompt_tokens": metrics.prompt_tokens,
        "completion_tokens": metrics.completion_tokens,
        "total_tokens": metrics.total_tokens,
        "context_tokens": metrics.context_tokens,
        "context_limit": metrics.context_limit,
        "estimated": metrics.estimated,
        "elapsed_ms": metrics.elapsed_ms,
        "rounds": metrics.rounds,
        "thinking_ms": metrics.thinking_ms,
    }
