"""Anthropic's Messages API, behind the OpenAI shapes the rest of LLeMbas speaks.

Every caller builds an OpenAI chat-completions body and reads OpenAI-style
stream chunks; nothing outside this module knows a second dialect exists.
`openai_client.list_models`, `stream_chat` and `complete` hand an endpoint whose
`kind` is `anthropic` to the functions here, and this module translates:

* **the request** -- system messages into the top-level `system`, assistant
  `tool_calls` into `tool_use` blocks, `role: tool` into `tool_result` blocks
  in a user turn, consecutive turns of one role merged, image data URLs into
  base64 image blocks, `max_tokens` always set, a reasoning effort into thinking;
* **the stream** -- `message_start`, `content_block_*`, `message_delta`,
  `message_stop` and `error` back into chunks that `delta_text`,
  `delta_reasoning`, `delta_tool_calls`, `finish_reason` and `chunk_usage`
  already read;
* **the model list** -- `GET /v1/models`, every page of it, into OpenAI's list.

Plain httpx, like the OpenAI client beside it, for the same reason: the
translation is the whole job, and it has to be visible to be tested.

Spending: the owner's credits are spent sparingly, so the system prompt and the
newest user turn carry a prompt-caching breakpoint (`cache_control`), and a
request's `max_tokens` never exceeds what the model is recorded to produce.

**Which models take what.** The Messages API is not uniform across Claude
generations, and a parameter one generation needs is a 400 on another. Read off
the model id by `_traits`; an id this cannot read is treated as the newest kind:

| | thinking | temperature / top_p | forced tool choice |
|---|---|---|---|
| before 4.6 | `enabled` + `budget_tokens` | yes, unless thinking | yes, unless thinking |
| 4.6 | `adaptive` + `output_config.effort` | yes, unless thinking | yes, unless thinking |
| 4.7 to 5.x | `adaptive` + `output_config.effort` | no | yes, unless thinking |
| Opus 5.5, Fable/Mythos 5.1, later | `adaptive` + `output_config.effort` | no | no |

Claude 4 takes `temperature` or `top_p`, never both: temperature wins.

**Thinking in a tool loop.** A budget-thinking model (before 4.6) refuses a
thinking request whose last assistant turn holds a tool call but does not open
with its thinking block. LLeMbas's own loop sends the block back
(`assistant_blocks`), but two cases cannot: a wrap-up round, which withdraws the
tools and so must strip the signatures, and a `/v1` client that rebuilt the
turn from `tool_calls` alone. For those requests thinking is simply off -- the
reply is written without it rather than refused. Adaptive models are unaffected.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from lembas.config import settings
from lembas.services.llm.openai_client import Endpoint, LLMError, wrap_transport_error

log = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"

# Anthropic requires `max_tokens` on every request. Used when neither the
# request nor the model row says anything.
DEFAULT_MAX_TOKENS = 32000

# A reasoning effort as a thinking budget, for the models that still take one.
# The smallest budget the API accepts is 1024.
THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 4000,
    "medium": 12000,
    "high": 32000,
    "xhigh": 32000,
    "max": 32000,
}
MIN_THINKING_BUDGET = 1024

# What `output_config.effort` accepts, and what an OpenAI-side effort becomes.
_EFFORT = {"minimal": "low", "low": "low", "medium": "medium", "high": "high",
           "xhigh": "xhigh", "max": "max"}

_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "pause_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "refusal": "content_filter",
}

_CACHE = {"type": "ephemeral"}

# A tool-use id the API accepts. OpenAI-side ids from other endpoints can carry
# anything, so they are rewritten -- the same way on the call and its result.
_TOOL_ID = re.compile(r"[^A-Za-z0-9_-]")

_DATA_URL = re.compile(r"^data:(image/[A-Za-z0-9.+-]+);base64,(.*)$", re.DOTALL)


# --- Which model is this -----------------------------------------------------

_MODEL_ID = re.compile(
    r"claude-(?:(?P<family>opus|sonnet|haiku|fable|mythos)-(?P<major>\d+)(?:-(?P<minor>\d)(?!\d))?"
    r"|(?P<omajor>\d+)(?:-(?P<ominor>\d)(?!\d))?-(?P<ofamily>opus|sonnet|haiku))"
)


@dataclass(frozen=True)
class Traits:
    """What a model's requests may carry. See the table in the module docstring."""

    budget_thinking: bool = False
    sampling: bool = False
    forced_tools: bool = False
    # Thinks when nothing is said about thinking (5.x and later).
    thinks_anyway: bool = True
    effort_levels: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")


def _traits(model_id: str) -> Traits:
    match = _MODEL_ID.search((model_id or "").lower())
    if match is None:
        # Not a Claude id this can read: the newest rules, which refuse least.
        return Traits()
    family = match.group("family") or match.group("ofamily")
    major = int(match.group("major") or match.group("omajor"))
    minor = int(match.group("minor") or match.group("ominor") or 0)
    version = major + minor / 10
    if version < 4.6:
        return Traits(
            budget_thinking=True, sampling=True, forced_tools=True, thinks_anyway=False
        )
    if version < 4.7:
        return Traits(
            sampling=True,
            forced_tools=True,
            thinks_anyway=False,
            effort_levels=("low", "medium", "high", "max"),
        )
    newest = version >= 5.5 or (family in ("fable", "mythos") and version >= 5.1)
    return Traits(forced_tools=not newest, thinks_anyway=version >= 5)


# --- The request -------------------------------------------------------------


def headers(endpoint: Endpoint) -> dict[str, str]:
    out = {
        "content-type": "application/json",
        "anthropic-version": ANTHROPIC_VERSION,
        **endpoint.extra_headers,
    }
    if endpoint.api_key:
        out["x-api-key"] = endpoint.api_key
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") in ("text", "input_text")
        )
    return ""


def _image_block(url: str) -> dict[str, Any] | None:
    found = _DATA_URL.match(url or "")
    if found:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": found.group(1), "data": found.group(2)},
        }
    if url.startswith(("http://", "https://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    """An OpenAI message's content as Anthropic content blocks. Empty text is
    dropped, because the API refuses an empty text block."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    blocks: list[dict[str, Any]] = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        if kind in ("text", "input_text"):
            text = str(part.get("text") or "")
            if text.strip():
                blocks.append({"type": "text", "text": text})
        elif kind == "image_url":
            image = part.get("image_url")
            url = image.get("url") if isinstance(image, dict) else image
            block = _image_block(str(url or ""))
            if block is not None:
                blocks.append(block)
    return blocks


def _tool_id(raw: Any, fallback: str) -> str:
    cleaned = _TOOL_ID.sub("_", str(raw or ""))
    return cleaned or fallback


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"arguments": str(raw)}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _assistant_blocks(message: dict[str, Any], index: int) -> list[dict[str, Any]]:
    """An assistant turn. When it carries its own blocks (`assistant_blocks`, an
    Anthropic reply echoed in a tool loop) those go back in their order, with
    thinking and its signature untouched -- only a tool call's input is taken
    from `tool_calls`, which is where a command corrected on the approval card
    is written. Otherwise it is rebuilt from text and `tool_calls`."""
    calls = [c for c in message.get("tool_calls") or [] if isinstance(c, dict)]
    by_id: dict[str, dict[str, Any]] = {}
    for number, call in enumerate(calls):
        function = call.get("function") or {}
        tool_id = _tool_id(call.get("id"), f"call_{index}_{number}")
        by_id[tool_id] = {
            "type": "tool_use",
            "id": tool_id,
            "name": str(function.get("name") or "tool"),
            "input": _arguments(function.get("arguments")),
        }

    native = message.get("assistant_blocks")
    if isinstance(native, list) and native:
        blocks: list[dict[str, Any]] = []
        used: set[str] = set()
        for block in native:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                tool_id = _tool_id(block.get("id"), "")
                if calls and tool_id not in by_id:
                    continue  # no longer one of the turn's calls
                blocks.append(by_id.get(tool_id) or dict(block))
                used.add(tool_id)
            elif kind == "text":
                if str(block.get("text") or "").strip():
                    blocks.append({"type": "text", "text": block["text"]})
            elif kind in ("thinking", "redacted_thinking"):
                blocks.append(dict(block))
        blocks.extend(block for tool_id, block in by_id.items() if tool_id not in used)
        return blocks

    blocks = _content_blocks(message.get("content"))
    blocks.extend(by_id.values())
    return blocks


def _tool_result(message: dict[str, Any], index: int) -> dict[str, Any]:
    content = message.get("content")
    if isinstance(content, list):
        parts = _content_blocks(content)
        result: Any = parts or ""
    else:
        result = "" if content is None else str(content)
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": _tool_id(message.get("tool_call_id"), f"call_{index}_0"),
        "content": result,
    }
    if message.get("is_error"):
        block["is_error"] = True
    return block


def translate_messages(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """OpenAI messages as (system blocks, Anthropic messages).

    Every system message, wherever it stands, goes to the top-level `system`, in
    order. Turns of one role in a row become one turn, because the API wants
    them to alternate -- which is also what puts several tool results, and a
    message typed while the tools ran, into the one user turn they belong in.
    """
    system: list[dict[str, Any]] = []
    turns: list[tuple[str, list[dict[str, Any]]]] = []
    for index, message in enumerate(messages or []):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role in ("system", "developer"):
            text = _text_of(message.get("content"))
            if text.strip():
                system.append({"type": "text", "text": text})
            continue
        if role == "assistant":
            turns.append(("assistant", _assistant_blocks(message, index)))
        elif role == "tool":
            turns.append(("user", [_tool_result(message, index)]))
        elif role == "user":
            turns.append(("user", _content_blocks(message.get("content"))))

    merged: list[dict[str, Any]] = []
    for role, blocks in turns:
        if not blocks:
            continue
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"].extend(blocks)
        else:
            merged.append({"role": role, "content": list(blocks)})
    if merged and merged[0]["role"] != "user":
        # The conversation has to open with a person. Only reachable from a
        # history that starts on a reply, such as a continued greeting.
        merged.insert(0, {"role": "user", "content": [{"type": "text", "text": "…"}]})
    return system, merged


def _tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") if tool.get("type") == "function" else tool
        if not isinstance(function, dict) or not function.get("name"):
            continue
        schema = function.get("parameters") or function.get("input_schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        entry = {"name": str(function["name"]), "input_schema": schema}
        if function.get("description"):
            entry["description"] = str(function["description"])
        out.append(entry)
    return out


def _tool_choice(choice: Any, *, forced_ok: bool) -> dict[str, Any] | None:
    if choice in (None, ""):
        return None
    if choice == "none":
        return {"type": "none"}
    if choice == "auto":
        return {"type": "auto"}
    forced: dict[str, Any] | None = None
    if choice == "required":
        forced = {"type": "any"}
    elif isinstance(choice, dict):
        name = (choice.get("function") or {}).get("name") or choice.get("name")
        if name:
            forced = {"type": "tool", "name": str(name)}
    # Forcing a call is refused by the newest models and by any request that
    # thinks; the turn still says in words which tool it wants, so `auto` loses
    # only the weaker half of the steering (see chat.build_request).
    return forced if forced is not None and forced_ok else {"type": "auto"}


def _used_tool_names(messages: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for message in messages:
        for block in message["content"] if message["role"] == "assistant" else []:
            if block.get("type") == "tool_use" and block["name"] not in names:
                names.append(block["name"])
    return names


_THINKING = ("thinking", "redacted_thinking")


def _strip_thinking(messages: list[dict[str, Any]]) -> None:
    for message in messages:
        if message["role"] == "assistant":
            kept = [b for b in message["content"] if b.get("type") not in _THINKING]
            message["content"] = kept or [{"type": "text", "text": "…"}]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _estimate(*parts: Any) -> int:
    """Tokens, roughly: characters over four, as `services/tokens.py` counts."""
    return len(json.dumps(parts, ensure_ascii=False, default=str)) // 4


def _lacks_thinking(messages: list[dict[str, Any]]) -> bool:
    """Whether the last assistant turn holds a tool call but does not open with
    a thinking block -- what a budget-thinking model refuses while thinking is
    on. A turn rebuilt from `tool_calls` by a client that did not send back
    `assistant_blocks` looks exactly like this."""
    for message in reversed(messages):
        if message["role"] != "assistant":
            continue
        content = message["content"]
        if not any(block.get("type") == "tool_use" for block in content):
            return False
        return not content or content[0].get("type") not in _THINKING
    return False


def build_request(endpoint: Endpoint, payload: dict[str, Any], *, stream: bool) -> dict[str, Any]:
    """The Messages API body for an OpenAI chat-completions payload."""
    model = str(payload.get("model") or "")
    traits = _traits(model)
    system, messages = translate_messages(list(payload.get("messages") or []))
    if not messages:
        raise LLMError("There is nothing to send: the conversation has no message with text in it.")

    tools = _tools(payload.get("tools") or [])
    wrapping_up = False
    if not tools and (used := _used_tool_names(messages)):
        # Tools withdrawn while the transcript still holds calls (a loop asked
        # to wrap up). The API refuses tool_use blocks with no tool defined, so
        # each is declared and none may be called. The tool set changed, which
        # invalidates the turn's thinking signatures: those go, as the API's
        # own recovery advises.
        tools = [
            {"name": name, "input_schema": {"type": "object", "properties": {}}} for name in used
        ]
        wrapping_up = True
        _strip_thinking(messages)

    # max_tokens: what the request asked for, else a default; never above the
    # model's recorded output ceiling, and never above what its context window
    # has left once the prompt is in it, where both are known.
    limit = endpoint.output_limit(model)
    asked = payload.get("max_tokens") or payload.get("max_completion_tokens")
    asked = int(asked) if _number(asked) and asked > 0 else 0
    max_tokens = asked or DEFAULT_MAX_TOKENS
    if limit:
        max_tokens = min(max_tokens, limit)
    if context := endpoint.context_limit(model):
        room = context - _estimate(system, messages, tools)
        max_tokens = max(MIN_THINKING_BUDGET, min(max_tokens, room))

    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if stream:
        body["stream"] = True

    # Thinking, from the OpenAI-side `reasoning_effort` (or the copy in
    # `chat_template_kwargs` that llama.cpp reads).
    effort = str(
        payload.get("reasoning_effort")
        or (payload.get("chat_template_kwargs") or {}).get("reasoning_effort")
        or ""
    ).lower()
    thinking = False
    if traits.budget_thinking:
        # A budget-thinking model wants the last assistant turn of a tool loop
        # to open with its thinking block. A wrap-up round has just stripped it,
        # and a /v1 client may never have sent it: thinking is off for that one
        # request rather than refused (see the module docstring).
        if effort in THINKING_BUDGETS and not wrapping_up and not _lacks_thinking(messages):
            # Kept below max_tokens, which the budget is part of; no thinking at
            # all if that leaves less than the API's minimum budget.
            budget = min(THINKING_BUDGETS[effort], max_tokens - MIN_THINKING_BUDGET)
            if budget >= MIN_THINKING_BUDGET:
                body["thinking"] = {"type": "enabled", "budget_tokens": budget}
                thinking = True
    else:
        level = _EFFORT.get(effort, "")
        if level and level not in traits.effort_levels:
            level = "high"
        # `summarized`, so the reasoning reaches the reader rather than arriving
        # as empty blocks (the newer models' default is to omit it).
        if level:
            body["thinking"] = {"type": "adaptive", "display": "summarized"}
            body["output_config"] = {"effort": level}
            thinking = True
        elif traits.thinks_anyway:
            # These think whether asked or not; show what they think.
            body["thinking"] = {"type": "adaptive", "display": "summarized"}
            thinking = True

    if traits.sampling and not thinking:
        # Claude 4 takes temperature or top_p, not both: temperature wins.
        temperature = _number(payload.get("temperature"))
        top_p = _number(payload.get("top_p"))
        if temperature is not None:
            body["temperature"] = min(temperature, 1.0)
        elif top_p is not None:
            body["top_p"] = top_p
        top_k = _number(payload.get("top_k"))
        if top_k is not None:
            body["top_k"] = top_k

    stop = payload.get("stop")
    if isinstance(stop, str) and stop:
        body["stop_sequences"] = [stop]
    elif isinstance(stop, list) and stop:
        body["stop_sequences"] = [str(s) for s in stop if s][:8]

    if tools:
        body["tools"] = tools
        if wrapping_up:
            body["tool_choice"] = {"type": "none"}
        else:
            choice = _tool_choice(
                payload.get("tool_choice"), forced_ok=traits.forced_tools and not thinking
            )
            if choice is not None:
                body["tool_choice"] = choice

    # Prompt caching: one breakpoint on the system prompt (which, being after
    # the tools, covers them too) and one on the newest user turn's last block.
    if system:
        system[-1] = {**system[-1], "cache_control": _CACHE}
        body["system"] = system
    for message in reversed(messages):
        if message["role"] == "user" and message["content"]:
            message["content"][-1] = {**message["content"][-1], "cache_control": _CACHE}
            break
    return body


# --- Errors ------------------------------------------------------------------

_FRIENDLY = {
    400: "Anthropic refused the request.",
    401: "The Anthropic API key was rejected.",
    402: "The Anthropic account has no credit left.",
    403: "The Anthropic API key is not permitted to do this.",
    404: "Anthropic does not know this model, or the base URL is wrong.",
    413: "The request is too large for Anthropic's API.",
    429: "Rate limited by Anthropic. Wait a little and try again.",
    500: "Anthropic had an internal error. Try again.",
    529: "Anthropic's API is overloaded right now. Try again shortly.",
}


def describe_error(status: int, body: Any) -> str:
    detail = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
        elif isinstance(error, str):
            detail = error
        detail = detail or str(body.get("message") or "")
    elif isinstance(body, str):
        detail = body[:300]
    friendly = _FRIENDLY.get(status)
    if friendly and detail:
        return f"{friendly} {detail}"
    return friendly or detail or f"Anthropic returned HTTP {status}."


def _redirect_error(response: httpx.Response) -> LLMError:
    """A 3xx: the base URL points somewhere that moved. Named by host only --
    the full URL could carry anything, and the request carried the key."""
    target = response.headers.get("location") or ""
    host = httpx.URL(target).host if target else ""
    where = f" to {host}" if host else ""
    return LLMError(
        f"The Anthropic endpoint redirected{where} (HTTP {response.status_code}) "
        "instead of answering. Check the connection's base URL.",
        status_code=response.status_code,
    )


def _error_from(response: httpx.Response) -> LLMError:
    if 300 <= response.status_code < 400:
        return _redirect_error(response)
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    return LLMError(describe_error(response.status_code, body), status_code=response.status_code)


# --- The stream --------------------------------------------------------------


class _Translator:
    """Anthropic stream events in, OpenAI-style chunks out. One per reply."""

    def __init__(self, model: str) -> None:
        self.id = f"chatcmpl-{int(time.time() * 1000)}"
        self.model = model
        self.created = int(time.time())
        self.usage: dict[str, int] = {}
        # block index -> the block being assembled
        self.blocks: dict[int, dict[str, Any]] = {}
        self.json_parts: dict[int, list[str]] = {}
        # block index -> its position among the reply's tool calls
        self.tool_index: dict[int, int] = {}
        self.order: list[int] = []
        self.finished = False

    def chunk(self, delta: dict[str, Any] | None = None, finish: str | None = None) -> dict:
        return {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
        }

    def _usage_chunk(self) -> dict[str, Any]:
        read = self.usage.get("cache_read_input_tokens", 0)
        written = self.usage.get("cache_creation_input_tokens", 0)
        prompt = self.usage.get("input_tokens", 0) + read + written
        completion = self.usage.get("output_tokens", 0)
        return {
            "id": self.id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "prompt_tokens_details": {"cached_tokens": read, "cache_write_tokens": written},
            },
        }

    def _take_usage(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                self.usage[key] = value

    def native(self) -> list[dict[str, Any]]:
        """The reply's content blocks in order, as the API would want them back."""
        out = []
        for index in self.order:
            block = self.blocks[index]
            if block["type"] == "tool_use":
                raw = "".join(self.json_parts.get(index, []))
                block = {**block, "input": _arguments(raw) if raw else block.get("input") or {}}
            out.append(block)
        return out

    def feed(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message") or {}
            self.id = str(message.get("id") or self.id)
            self._take_usage(message.get("usage"))
            return [self.chunk({"role": "assistant", "content": ""})]
        if kind == "content_block_start":
            index = int(event.get("index") or 0)
            block = dict(event.get("content_block") or {})
            block_type = block.get("type")
            if block_type == "tool_use":
                position = len(self.tool_index)
                self.tool_index[index] = position
                block["input"] = block.get("input") or {}
                self.blocks[index] = block
                self.order.append(index)
                return [
                    self.chunk(
                        {
                            "tool_calls": [
                                {
                                    "index": position,
                                    "id": block.get("id"),
                                    "type": "function",
                                    "function": {"name": block.get("name"), "arguments": ""},
                                }
                            ]
                        }
                    )
                ]
            if block_type == "text":
                self.blocks[index] = {"type": "text", "text": block.get("text") or ""}
                self.order.append(index)
                return [self.chunk({"content": block["text"]})] if block.get("text") else []
            if block_type == "thinking":
                self.blocks[index] = {
                    "type": "thinking",
                    "thinking": block.get("thinking") or "",
                    "signature": block.get("signature") or "",
                }
                self.order.append(index)
                text = block.get("thinking") or ""
                return [self.chunk({"reasoning_content": text})] if text else []
            if block_type == "redacted_thinking":
                self.blocks[index] = {"type": "redacted_thinking", "data": block.get("data") or ""}
                self.order.append(index)
            return []
        if kind == "content_block_delta":
            index = int(event.get("index") or 0)
            delta = event.get("delta") or {}
            delta_type = delta.get("type")
            block = self.blocks.get(index)
            if delta_type == "text_delta":
                text = str(delta.get("text") or "")
                if block is not None:
                    block["text"] = block.get("text", "") + text
                return [self.chunk({"content": text})] if text else []
            if delta_type == "thinking_delta":
                text = str(delta.get("thinking") or "")
                if block is not None:
                    block["thinking"] = block.get("thinking", "") + text
                return [self.chunk({"reasoning_content": text})] if text else []
            if delta_type == "signature_delta":
                if block is not None:
                    block["signature"] = block.get("signature", "") + str(
                        delta.get("signature") or ""
                    )
                return []
            if delta_type == "input_json_delta":
                partial = str(delta.get("partial_json") or "")
                self.json_parts.setdefault(index, []).append(partial)
                if index not in self.tool_index or not partial:
                    return []
                fragment = {"index": self.tool_index[index], "function": {"arguments": partial}}
                return [self.chunk({"tool_calls": [fragment]})]
            return []
        if kind == "message_delta":
            self._take_usage(event.get("usage"))
            reason = (event.get("delta") or {}).get("stop_reason")
            out = []
            if reason:
                finish = _STOP_REASONS.get(str(reason), "stop")
                closing = self.chunk({}, finish)
                closing["assistant_blocks"] = self.native()
                out.append(closing)
            out.append(self._usage_chunk())
            return out
        if kind == "message_stop":
            self.finished = True
            return []
        if kind == "error":
            error = event.get("error") or {}
            status = 529 if error.get("type") == "overloaded_error" else None
            message = str(error.get("message") or "")
            if status == 529:
                raise LLMError(describe_error(529, {"error": error}), status_code=529)
            raise LLMError(
                f"Anthropic stopped the reply: {message or error.get('type') or 'unknown error'}"
            )
        return []  # ping, and anything newer than this module


async def stream_chat(
    endpoint: Endpoint, payload: dict[str, Any]
) -> AsyncIterator[dict[str, Any]]:
    """Stream a reply, yielding OpenAI-style chunks."""
    body = build_request(endpoint, payload, stream=True)
    translator = _Translator(str(payload.get("model") or ""))
    try:
        async with (
            httpx.AsyncClient(timeout=settings.request_timeout) as client,
            client.stream(
                "POST", endpoint.url("messages"), headers=headers(endpoint), json=body
            ) as response,
        ):
            if not 200 <= response.status_code < 300:
                await response.aread()
                raise _error_from(response)
            async for line in response.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue  # `event:` lines repeat the type the data carries
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    log.warning("skipping unparseable Anthropic frame: %.120s", data)
                    continue
                if not isinstance(event, dict):
                    continue
                for chunk in translator.feed(event):
                    yield chunk
                if translator.finished:
                    return
    except httpx.RequestError as exc:
        raise wrap_transport_error(exc, endpoint) from exc


async def complete_response(endpoint: Endpoint, payload: dict[str, Any]) -> dict[str, Any]:
    """A whole reply as OpenAI's non-streaming response object.

    Streamed underneath, through the same translation: a long reply is not then
    at the mercy of one HTTP request's timeout, and there is one parser, not two.
    """
    from lembas.services.llm.openai_client import (
        delta_reasoning,
        delta_text,
        delta_tool_calls,
        finish_reason,
    )

    text: list[str] = []
    reasoning: list[str] = []
    calls: dict[int, dict[str, Any]] = {}
    finish = "stop"
    usage: dict[str, Any] | None = None
    blocks: list[dict[str, Any]] = []
    response_id = ""
    async for chunk in stream_chat(endpoint, payload):
        response_id = chunk.get("id") or response_id
        text.append(delta_text(chunk))
        reasoning.append(delta_reasoning(chunk))
        for fragment in delta_tool_calls(chunk):
            slot = calls.setdefault(
                int(fragment.get("index") or 0),
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if fragment.get("id"):
                slot["id"] = fragment["id"]
            function = fragment.get("function") or {}
            if function.get("name"):
                slot["function"]["name"] = function["name"]
            slot["function"]["arguments"] += function.get("arguments") or ""
        finish = finish_reason(chunk) or finish
        if chunk.get("usage"):
            usage = chunk["usage"]
        if chunk.get("assistant_blocks"):
            blocks = chunk["assistant_blocks"]
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
    if any(reasoning):
        message["reasoning_content"] = "".join(reasoning)
    if calls:
        for call in calls.values():
            # A tool called with no input streams no JSON at all; an OpenAI
            # client parses `arguments`, and "" is not JSON.
            call["function"]["arguments"] = call["function"]["arguments"] or "{}"
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    if blocks:
        message["assistant_blocks"] = blocks
    out: dict[str, Any] = {
        "id": response_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(payload.get("model") or ""),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if usage:
        out["usage"] = usage
    return out


async def complete(endpoint: Endpoint, payload: dict[str, Any]) -> str:
    """The text of a whole reply -- `openai_client.complete`'s contract."""
    response = await complete_response(endpoint, payload)
    return response["choices"][0]["message"]["content"] or ""


# --- The model list ----------------------------------------------------------

MODELS_PAGE = 100
# A model list that never says it has ended is not followed forever.
MAX_PAGES = 50


async def list_models(endpoint: Endpoint) -> list[dict[str, Any]]:
    """Every model the key may use, as OpenAI-shaped list entries.

    Paginated (`has_more`, `last_id`, `after_id`). The context window and the
    output ceiling are passed on where the API gives them -- `max_input_tokens`
    as `context_length`, which `openai_client.context_from` reads, and
    `max_tokens` as `max_output`.
    """
    models: list[dict[str, Any]] = []
    after = ""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for _ in range(MAX_PAGES):
                params: dict[str, Any] = {"limit": MODELS_PAGE}
                if after:
                    params["after_id"] = after
                response = await client.get(
                    endpoint.url("models"), headers=headers(endpoint), params=params
                )
                if not 200 <= response.status_code < 300:
                    raise _error_from(response)
                try:
                    page = response.json()
                except ValueError as exc:
                    raise LLMError("Anthropic returned a model list that was not JSON.") from exc
                entries = page.get("data") if isinstance(page, dict) else None
                if not isinstance(entries, list):
                    raise LLMError("Anthropic's model list was not in the expected format.")
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("id"):
                        models.append(_model_entry(entry))
                last = page.get("last_id") or (entries[-1].get("id") if entries else "")
                if not page.get("has_more") or not last or last == after:
                    break
                after = str(last)
    except httpx.RequestError as exc:
        raise wrap_transport_error(exc, endpoint) from exc
    return models


def _model_entry(entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": str(entry["id"]),
        "object": "model",
        "owned_by": "anthropic",
    }
    if entry.get("display_name"):
        out["display_name"] = str(entry["display_name"])
    context = entry.get("max_input_tokens") or entry.get("context_window")
    if isinstance(context, int) and not isinstance(context, bool) and context > 0:
        out["context_length"] = context
    output = entry.get("max_tokens")
    if isinstance(output, int) and not isinstance(output, bool) and output > 0:
        out["max_output"] = output
    return out


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_TOKENS",
    "build_request",
    "complete",
    "complete_response",
    "describe_error",
    "list_models",
    "stream_chat",
    "translate_messages",
]
