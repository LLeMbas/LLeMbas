"""Reading server-sent events -- the one parser every stream in LLeMbas reads with.

`services/sse.py` is the writer; this is the reader, and it exists because four
hand-written ones had each learned a different lesson about what servers
really send. Everything they learned is here:

- `data:` lines are gathered until a blank line and joined with newlines, as
  the SSE specification says; an `event:` line names the frame; a line
  starting with `:` is a comment (a keep-alive, usually) and is passed on as
  one so a relay can forward it.
- **The last line may have no newline after it** -- llama.cpp behind llama-swap
  ends a failed stream exactly that way -- so whatever is pending when the
  lines run out is dispatched like any other frame.
- **An error may be a bare `{"error": ...}` line inside a 200**, with no
  `data:` in front of it (quirks.json `error-in-200-stream`). By the letter of
  the specification that is a field called `{"error"` and would be ignored,
  silently ending the reply as an empty one. It is dispatched as an `error`
  event instead, and so is a line whose field is literally `error:`.
- Some servers omit the blank line between frames. A pending frame that is
  already complete JSON is dispatched when the next `data:` line arrives,
  rather than being joined to it into something that parses as nothing.

`[DONE]` is dispatched as ordinary data: what it means is the caller's
business, and a relay wants to pass it on.

The LLeMbas CLI's `provider/sse.ts` is the same parser in TypeScript; a case
one of them learns goes into the other.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from typing import Any

# The reader's own event names begin with ":", which an `event:` line never
# yields (see `SseParser.feed`), so a server's `event: error` -- Anthropic names
# its error frames that -- reaches the caller as the named data frame it is.
# A comment line.
COMMENT = ":"
# An error a server wrote outside the frame format.
ERROR = ":error"
# What an OpenAI-compatible server sends to say the reply is over.
DONE = "[DONE]"


class SseParser:
    """Incremental: feed it lines, collect frames. `flush()` when the lines end."""

    def __init__(self) -> None:
        self._event = ""
        self._data: list[str] = []

    def feed(self, line: str) -> list[tuple[str, str]]:
        """The frames this line completes: (event name or "", data)."""
        if line.endswith("\r"):
            line = line[:-1]
        if not line:
            return self._dispatch()
        if line.startswith(":"):
            return [(COMMENT, line[1:])]
        if line.startswith("{") and _is_error_object(line):
            return [*self._dispatch(), (ERROR, line)]

        field, colon, value = line.partition(":")
        if colon and value.startswith(" "):
            value = value[1:]
        if field == "data":
            out = self._dispatch() if self._data and _is_json("\n".join(self._data)) else []
            self._data.append(value)
            return out
        if field == "event":
            # A name beginning with ":" is reserved for this reader's own frames.
            self._event = "" if value.startswith(":") else value
            return []
        if field == "error":
            return [*self._dispatch(), (ERROR, value)]
        return []  # `id`, `retry`, and anything this reader has no use for

    def flush(self) -> list[tuple[str, str]]:
        """Whatever is pending: the frame an unterminated last line belongs to."""
        return self._dispatch()

    def _dispatch(self) -> list[tuple[str, str]]:
        if not self._data:
            self._event = ""
            return []
        frame = (self._event, "\n".join(self._data))
        self._event = ""
        self._data = []
        return [frame]


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """Every frame in `lines`, which carry no line endings (`str.splitlines()`)."""
    parser = SseParser()
    for line in lines:
        yield from parser.feed(line)
    yield from parser.flush()


async def iter_sse(lines: AsyncIterable[str]) -> AsyncIterator[tuple[str, str]]:
    """`parse_sse` over a response's `aiter_lines()`."""
    parser = SseParser()
    async for line in lines:
        for frame in parser.feed(line):
            yield frame
    for frame in parser.flush():
        yield frame


def error_object(data: str) -> dict[str, Any]:
    """The error an `error` frame carries, in OpenAI's shape: `{"error": {...}}`.

    A bare JSON line already has that shape. An `error:` field may carry JSON
    or a sentence; either way the result has an `error` member to read.
    """
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and "error" in parsed:
        return parsed
    return {"error": parsed if parsed is not None else data}


def error_status(error: Any) -> int | None:
    """The HTTP status an error object inside a stream stands for.

    A numeric `code` or `status` is one. llama-swap's "model unloaded" frame
    (quirks.json `model-unloaded`) carries `type: "server_error"` and a string
    code; that, `overloaded_error`, `api_error` and any code naming something
    internal are a 500, so the server-error rule (retried once, before anything
    was streamed) applies to them.
    """
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    status = error.get("status")
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    if error.get("type") in ("server_error", "overloaded_error", "api_error"):
        return 500
    if "internal" in str(code or "").lower():
        return 500
    return None


def error_message(error: Any) -> str:
    """The words of an error object, whatever shape it came in."""
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return json.dumps(error, ensure_ascii=False)
    return str(error).strip()


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


def _is_error_object(line: str) -> bool:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and "error" in parsed
