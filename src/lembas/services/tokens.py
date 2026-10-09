"""A rough token count, for when the endpoint does not give a real one.

Deliberately crude. Counting tokens properly means the model's own tokeniser,
which means shipping one per model family and a dependency that has to be kept
in step with them -- for a number that is displayed beside a `~` and used to
decide when to summarise.

Four characters per token is the usual English approximation. It is optimistic
on code and badly wrong on CJK, which is why anything derived from it is marked
as an estimate everywhere it is shown.
"""

from __future__ import annotations

from typing import Any

CHARS_PER_TOKEN = 4

# What a message costs beyond its text: the role, the delimiters a chat template
# wraps each turn in. Small, but a hundred-turn conversation is a hundred of them.
PER_MESSAGE_OVERHEAD = 4


def estimate(text: str) -> int:
    """Roughly how many tokens a piece of text is."""
    if not text:
        return 0
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def estimate_chars(count: int) -> int:
    """The same conversion, for a length somebody has already measured.

    `estimate` floors at one token for any non-empty string, which is right for
    a piece of text and wrong for a difference between two lengths: a reply that
    had grown by nothing would report a token. Zero means zero here.
    """
    if count <= 0:
        return 0
    return round(count / CHARS_PER_TOKEN)


def estimate_content(content: Any) -> int:
    """A message's content, whether it is a plain string or typed parts.

    An image part contributes nothing: its cost depends on the model's tiling,
    and a number invented here would be worse than the omission.
    """
    if isinstance(content, str):
        return estimate(content)
    if isinstance(content, list):
        return sum(
            estimate(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return 0


def estimate_messages(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        total += PER_MESSAGE_OVERHEAD + estimate_content(message.get("content"))
        # A tool result the model reads back is part of the window too.
        for call in message.get("tool_calls") or []:
            function = (call or {}).get("function") or {}
            total += estimate(str(function.get("name", "")))
            total += estimate(str(function.get("arguments", "")))
    return total


def estimate_request(payload: dict[str, Any]) -> int:
    """What a whole request body costs, tools included.

    The tools array is sent on every request when tools are offered and is not
    small -- thirteen schemas is a meaningful slice of a short window, and
    leaving it out would make the percentage read low exactly when it matters.
    """
    total = estimate_messages(payload.get("messages") or [])
    for tool in payload.get("tools") or []:
        function = (tool or {}).get("function") or {}
        total += estimate(str(function.get("name", "")))
        total += estimate(str(function.get("description", "")))
        total += estimate(str(function.get("parameters", "")))
    return total
