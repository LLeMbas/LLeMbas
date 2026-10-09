"""Separating a reasoning model's thinking from its answer.

Endpoints do this two different ways and LLeMbas has to cope with both:

1. A dedicated ``reasoning_content`` field in the streamed delta. This is what
   llama.cpp, llama-swap, vLLM and DeepSeek emit, and it is unambiguous.
2. ``<think>...</think>`` tags inline in ``content``. Ollama and various
   proxies do this, and it is a nuisance: the tags arrive split across chunks,
   so the text has to be scanned as a stream rather than with a regex at the
   end.

The splitter below handles the second case. It buffers only as much as a
partial tag could occupy, so latency is unaffected in the overwhelmingly common
case where no tag is present at all.
"""

from __future__ import annotations

from collections.abc import Iterator

# Tag spellings seen in the wild. Checked longest-first so <thinking> is not
# mistaken for <think> followed by "ing>".
_TAGS: tuple[tuple[str, str], ...] = (
    ("<thinking>", "</thinking>"),
    ("<think>", "</think>"),
    ("<reasoning>", "</reasoning>"),
)

REASONING = "reasoning"
CONTENT = "content"

# Longest opening tag, minus one: the most that can ever need holding back
# while waiting to see whether a partial "<thi" turns into a real tag.
_MAX_PARTIAL = max(len(open_tag) for open_tag, _ in _TAGS) - 1


class ReasoningSplitter:
    """Splits a stream of content chunks into reasoning and answer runs.

    Feed it whatever arrives; it yields ``(kind, text)`` pairs. Call
    :meth:`flush` at the end to release anything still buffered.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._in_reasoning = False
        self._closing = ""

    def feed(self, chunk: str) -> Iterator[tuple[str, str]]:
        self._buffer += chunk
        yield from self._drain(final=False)

    def flush(self) -> Iterator[tuple[str, str]]:
        yield from self._drain(final=True)

    def _drain(self, *, final: bool) -> Iterator[tuple[str, str]]:
        while self._buffer:
            if self._in_reasoning:
                index = self._buffer.find(self._closing)
                if index == -1:
                    # Hold back enough that a closing tag split across chunks is
                    # still recognised once the rest arrives.
                    keep = 0 if final else len(self._closing) - 1
                    emit, self._buffer = self._split(keep)
                    if emit:
                        yield (REASONING, emit)
                    return
                if index:
                    yield (REASONING, self._buffer[:index])
                self._buffer = self._buffer[index + len(self._closing) :]
                self._in_reasoning = False
                self._closing = ""
                continue

            opening_at, opening, closing = self._find_opening()
            if opening_at == -1:
                keep = 0 if final else _MAX_PARTIAL
                emit, self._buffer = self._split(keep)
                if emit:
                    yield (CONTENT, emit)
                return

            if opening_at:
                yield (CONTENT, self._buffer[:opening_at])
            self._buffer = self._buffer[opening_at + len(opening) :]
            self._in_reasoning = True
            self._closing = closing

    def _find_opening(self) -> tuple[int, str, str]:
        best = (-1, "", "")
        for opening, closing in _TAGS:
            index = self._buffer.find(opening)
            if index != -1 and (best[0] == -1 or index < best[0]):
                best = (index, opening, closing)
        return best

    def _split(self, keep: int) -> tuple[str, str]:
        """Emit everything except the last `keep` characters."""
        if keep <= 0:
            return self._buffer, ""
        if len(self._buffer) <= keep:
            return "", self._buffer
        return self._buffer[:-keep], self._buffer[-keep:]


def strip_reasoning(text: str) -> tuple[str, str]:
    """Split a complete string into (answer, reasoning).

    The non-streaming counterpart, used when replaying stored content.
    """
    splitter = ReasoningSplitter()
    answer: list[str] = []
    thinking: list[str] = []
    for kind, piece in splitter.feed(text):
        (thinking if kind == REASONING else answer).append(piece)
    for kind, piece in splitter.flush():
        (thinking if kind == REASONING else answer).append(piece)
    return "".join(answer), "".join(thinking)


def format_duration(milliseconds: int) -> str:
    """Human phrasing for the 'Thought for ...' label."""
    if milliseconds <= 0:
        return ""
    seconds = milliseconds / 1000
    if seconds < 1:
        return "less than a second"
    if seconds < 60:
        return f"{seconds:.0f} second{'' if round(seconds) == 1 else 's'}"
    minutes, remainder = divmod(int(seconds), 60)
    if remainder == 0:
        return f"{minutes} minute{'' if minutes == 1 else 's'}"
    return f"{minutes}m {remainder}s"
