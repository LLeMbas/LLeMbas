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

import base64
import binascii
import json
import math
import re
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


# An image whose size cannot be read; a 1600 px screenshot, roughly.
IMAGE_FALLBACK_TOKENS = 1600
# Pixels per token edge (Qwen-VL's 28 px patches; OpenAI's and Anthropic's tiles
# land near it), and the most one image is counted at.
_PATCH = 28
_IMAGE_MAX_TOKENS = 16_384
# A string this long made only of base64 characters is an image's bytes.
_BASE64_MIN = 2000
_BASE64 = re.compile(r"^[A-Za-z0-9+/=_-]+$")


def image_size(head: bytes) -> tuple[int, int] | None:
    """Width and height from an image's first bytes (PNG, GIF, JPEG, WebP)."""

    def be16(i: int) -> int:
        return int.from_bytes(head[i : i + 2], "big")

    if head[:4] == b"\x89PNG" and len(head) >= 24:
        return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    if head[:3] == b"GIF" and len(head) >= 10:
        return int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP" and len(head) >= 30:
        chunk = head[12:16]
        if chunk == b"VP8X":
            return (
                1 + int.from_bytes(head[24:27], "little"),
                1 + int.from_bytes(head[27:30], "little"),
            )
        if chunk == b"VP8 ":
            return (
                int.from_bytes(head[26:28], "little") & 0x3FFF,
                int.from_bytes(head[28:30], "little") & 0x3FFF,
            )
        if chunk == b"VP8L":
            bits = int.from_bytes(head[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        return None
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                return None
            marker = head[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                return be16(i + 7), be16(i + 5)
            i += 2 + be16(i + 2)
    return None


def image_tokens(data: str) -> int:
    """What an image costs a model, from its dimensions -- never its encoding.

    Counting the base64 as text made a 1.5 MB screenshot "half a million
    tokens" (the LLeMbas CLI then compacted after every image, at "215%"); not
    counting it at all made a chat full of pictures read nearly empty. `data`
    is base64 or a data URL; only its head is decoded.
    """
    head = data.split(",", 1)[1] if data.startswith("data:") else data
    head = head[:87_384]
    head = head[: len(head) - len(head) % 4]
    try:
        size = image_size(base64.b64decode(head, validate=False))
    except (ValueError, binascii.Error):
        size = None
    if not size or not size[0] or not size[1]:
        return IMAGE_FALLBACK_TOKENS
    width, height = size
    return min(_IMAGE_MAX_TOKENS, math.ceil(width / _PATCH) * math.ceil(height / _PATCH))


def is_image_data(text: str) -> bool:
    """A data URL of an image, or a long run of nothing but base64."""
    if text.startswith("data:image/"):
        return True
    return len(text) >= _BASE64_MIN and bool(_BASE64.match(text[:4096]))


def estimate_value(value: Any) -> int:
    """Tokens for any JSON-able value -- a request, a message -- with every image
    in it (a data URL, a base64 `data` field) counted by `image_tokens`."""
    images = 0

    def strip(item: Any) -> Any:
        nonlocal images
        if isinstance(item, str) and is_image_data(item):
            images += image_tokens(item)
            return ""
        if isinstance(item, dict):
            return {k: strip(v) for k, v in item.items()}
        if isinstance(item, list | tuple):
            return [strip(v) for v in item]
        return item

    text = json.dumps(strip(value), ensure_ascii=False, default=str)
    return len(text) // CHARS_PER_TOKEN + images


def estimate_content(content: Any) -> int:
    """A message's content, whether it is a plain string or typed parts.

    An image part counts by its dimensions (`image_tokens`). It used to count
    nothing, so a chat full of pictures read nearly empty and was compacted
    late.
    """
    if isinstance(content, str):
        return estimate(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                total += estimate(part.get("text", ""))
            elif part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url", "")
                total += image_tokens(url) if url else IMAGE_FALLBACK_TOKENS
        return total
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
