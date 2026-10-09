"""Server-sent event framing.

Small, but worth isolating: getting the wire format subtly wrong is the usual
cause of a stream that "works" until a model emits a newline.
"""

from __future__ import annotations

# Every 15s of silence, so proxies that kill idle connections (nginx defaults
# to 60s) do not drop a stream while a model is still thinking.
KEEPALIVE = ": keepalive\n\n"


def event(name: str, data: str) -> str:
    """Frame one SSE event.

    A payload containing newlines must be split across several `data:` lines;
    the browser rejoins them with "\\n". Sending a raw newline inside a single
    data line silently truncates the event, which is exactly what happens the
    first time a model emits a code block.
    """
    lines = data.split("\n")
    body = "".join(f"data: {line}\n" for line in lines)
    return f"event: {name}\n{body}\n"
