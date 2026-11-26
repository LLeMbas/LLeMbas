"""Fill-in-the-middle: the text an editor inserts at its cursor.

One question -- "what goes between this prefix and this suffix?" -- asked the
way the model's server can answer it:

| the connection | asked as |
|---|---|
| llama.cpp, or llama-swap in front of one | `POST /infill` (`input_prefix`, `input_suffix`) |
| Ollama | `POST /v1/completions` with `suffix` |
| DeepSeek | `POST /beta/completions` with `suffix` (its FIM completion) |
| anything else, Anthropic included | a chat request with a strict prompt |

A server of the first three kinds answers a model that has no fill-in tokens
or template with a plain refusal (llama.cpp's is a 501). That is remembered
for the model for an hour, and the request is asked again as a chat: nothing
was written, so nothing is asked for twice. Only an answer that means "no
such call here" counts: a 404, 405 or 501, or a 400 or 422 that says in
words that it is not supported. A 400 about this one request -- too long for
the loaded context -- is that request's error and demotes nobody.

**The chat way is a fallback, and it is strict.** Thinking is switched off
where the model has the switch, else its lowest effort is asked for; the
prompt says to answer with the inserted text and nothing else; and what comes
back is cleaned by `Inserted` -- reasoning dropped, a wrapping code fence
dropped, a repetition of the line being completed dropped from the start, a
repetition of what follows the cursor dropped from the end. A server's own
fill-in answer is passed on as written: a model trained for it already
answers with the middle and nothing else.

The upstream request is always streamed, whatever the caller asked for, so
that closing the generator closes the connection: a completion nobody is
waiting for any more stops costing.

What a model is told is English and is not translated, like every prompt.
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
from lembas.services import reasoning
from lembas.services.llm import detect, providers, sse
from lembas.services.llm.openai_client import (
    Endpoint,
    LLMError,
    http_error,
    stream_chat,
    wrap_transport_error,
)

log = logging.getLogger(__name__)

WAY_INFILL = "infill"
WAY_COMPLETIONS = "completions"
WAY_DEEPSEEK = "deepseek"
WAY_CHAT = "chat"

DELTA = "delta"
END = "end"

FINISH_STOP = "stop"
FINISH_LENGTH = "length"

# How much of the file a model is sent, in characters: the text nearest the
# cursor, three parts before it to one after. A model's own context bounds it
# further where it is known (`window`).
WINDOW_CHARS = 32_000
WINDOW_FLOOR = 2_000
# The tokens a model that cannot stop thinking gets on top of what was asked
# for, so that its thinking does not eat the completion.
THINKING_ROOM = 1024

# What a server answers when it has no such call, or the model cannot fill
# in: asked again as a chat. A 400 or a 422 means that only when it says so
# (`_SAYS_UNSUPPORTED`) -- otherwise it is about *this* request (too long for
# the loaded context, a field refused) and says nothing about the model.
_NO_SUCH_CALL = (404, 405, 501)
_MAYBE = (400, 422)
_SAYS_UNSUPPORTED = re.compile(
    r"not supported|unsupported|does not support|doesn't support|no fim|"
    r"infill is not|not implemented",
    re.IGNORECASE,
)
# (base URL, model id) -> when it refused a fill-in request (monotonic). A
# refusal is forgotten after `FORGET_AFTER`: a model server restarted with a
# model that can fill in is asked that way again.
_NO_FIM: dict[tuple[str, str], float] = {}
FORGET_AFTER = 3600.0


def _demoted(key: tuple[str, str]) -> bool:
    since = _NO_FIM.get(key)
    if since is None:
        return False
    if time.monotonic() - since >= FORGET_AFTER:
        _NO_FIM.pop(key, None)
        return False
    return True

SYSTEM = (
    "You complete text at the cursor of a file that is open in an editor. You are "
    "given the text before the cursor and the text after it. Answer with exactly the "
    "text to insert at the cursor and nothing else: no explanation, no Markdown code "
    "fence, and none of the text that is already before or after the cursor. Keep the "
    "file's own style and indentation. Insert only what is missing at the cursor, "
    "usually the rest of the line or a few lines. If nothing should be inserted, "
    "answer with nothing."
)


class _Unsupported(Exception):
    """The server cannot fill in with this model. Raised before any text."""

    def __init__(self, status_code: int, detail: str = "") -> None:
        super().__init__(f"HTTP {status_code}")
        self.detail = detail


class _WithoutStreamOptions(Exception):
    """The server refused `stream_options`: asked once more without it.
    Raised before any text, and says nothing about the model."""


@dataclass(frozen=True)
class ContextChunk:
    """One related file excerpt sent beside the cursor for repository-aware FIM."""

    path: str
    text: str


@dataclass(frozen=True)
class Ask:
    """One completion, with everything the upstream request needs."""

    endpoint: Endpoint
    model_id: str
    # What detection found the server to be (`detect.SERVER_*`), or "".
    server: str
    prefix: str
    suffix: str
    max_tokens: int
    path: str = ""
    language: str = ""
    # Related files/snippets in a stable session order for KV-cache prefill reuse.
    context: tuple[ContextChunk, ...] = ()
    # The model's sampling defaults, an administrator's (`Model.params_json`).
    params: dict[str, Any] | None = None
    # What switches the model's thinking off, or asks for its lowest effort,
    # in a chat request (`chat.level_fields`); empty when it takes neither.
    quiet: dict[str, Any] | None = None
    # Whether the model thinks and cannot be told not to.
    thinks: bool = False
    # The model's context window in tokens, 0 when unknown.
    context_length: int = 0


def reset() -> None:
    """Forget which models refused a fill-in request (tests)."""
    _NO_FIM.clear()


def way_for(endpoint: Endpoint, server: str, model_id: str = "") -> str:
    """How a completion is asked of this connection."""
    if _demoted((endpoint.base_url, model_id)) or endpoint.is_anthropic:
        return WAY_CHAT
    provider = providers.get(endpoint.provider_type)
    if provider.id == providers.DEEPSEEK:
        return WAY_DEEPSEEK
    if provider.id != providers.OPENAI_COMPATIBLE:
        return WAY_CHAT
    if server in (detect.SERVER_LLAMA_CPP, detect.SERVER_LLAMA_SWAP):
        return WAY_INFILL
    if server == detect.SERVER_OLLAMA:
        return WAY_COMPLETIONS
    return WAY_CHAT


def window(prefix: str, suffix: str, context: int = 0) -> tuple[str, str]:
    """The part of the file a model is sent: the end of the prefix and the
    start of the suffix, each cut at a line where there is one to cut at."""
    budget = WINDOW_CHARS
    if context > 0:
        # About two characters a token, and half the window: room for the
        # prompt around it and for the answer.
        budget = max(WINDOW_FLOOR, min(WINDOW_CHARS, context))
    before, after = (budget * 3) // 4, budget // 4
    if len(prefix) > before:
        prefix = prefix[-before:]
        cut = prefix.find("\n")
        if 0 <= cut < len(prefix) // 4:
            prefix = prefix[cut + 1 :]
    if len(suffix) > after:
        suffix = suffix[:after]
        cut = suffix.rfind("\n")
        if cut > (len(suffix) * 3) // 4:
            suffix = suffix[:cut]
    return prefix, suffix


def messages_for(ask: Ask, prefix: str, suffix: str) -> list[dict[str, str]]:
    """The strict chat prompt. Related context chunks come first in a stable
    order so prefix KV caching survives across keystrokes."""
    parts: list[str] = []
    if ask.context:
        chunks = [
            f"<file path=\"{chunk.path}\">\n{chunk.text}\n</file>"
            for chunk in ask.context
            if chunk.text
        ]
        if chunks:
            body = "\n\n".join(chunks)
            parts.append(f"Related project context:\n<context>\n{body}\n</context>")
    where = []
    if ask.path:
        where.append(f"File: {ask.path}")
    if ask.language:
        where.append(f"Language: {ask.language}")
    if where:
        parts.append("\n".join(where))
    parts.append(
        f"Text before the cursor:\n<before>\n{prefix}</before>\n\n"
        f"Text after the cursor:\n<after>\n{suffix}</after>\n\n"
        "Answer with only the text to insert at the cursor."
    )
    user = "\n\n".join(parts)
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


class Inserted:
    """A chat model's answer, cleaned into the text to insert -- as a stream.

    What `feed` and `finish` return, joined, is the cleaned text: nothing
    handed out is ever taken back. So the start is held until it is known
    whether it opens with a fence or repeats the line being completed, and a
    tail of `HOLD` characters is held to the end, where a closing fence and a
    repetition of what follows the cursor are cut off.
    """

    HOLD = 96
    # A repetition shorter than this is not one: a `)` the insertion ends
    # with and the suffix starts with may well both be wanted.
    MIN_OVERLAP = 3

    def __init__(self, prefix: str, suffix: str) -> None:
        self._line = prefix.rsplit("\n", 1)[-1]
        self._suffix = suffix
        self._splitter = reasoning.ReasoningSplitter()
        self._buffer = ""
        self._started = False
        self._fenced = False
        self._fence_settled = False
        self._thought = False

    def _add(self, pieces) -> None:
        for kind, text in pieces:
            if kind == reasoning.CONTENT:
                self._buffer += text
            else:
                self._thought = True

    def feed(self, chunk: str) -> str:
        self._add(self._splitter.feed(chunk))
        return self._take(final=False)

    def finish(self) -> str:
        self._add(self._splitter.flush())
        return self._take(final=True)

    def _open(self, *, final: bool) -> bool:
        """Settle how the answer starts. False: not enough of it yet."""
        if not self._fence_settled:
            text = self._buffer
            if self._thought:
                # Thinking that was cut out leaves its blank lines in front.
                text = text.lstrip("\n")
            lead = text.lstrip()
            if lead.startswith("```"):
                _fence, newline, rest = lead.partition("\n")
                if not newline and not final:
                    return False
                # The fence's own line goes, with its language word.
                text, self._fenced = rest, True
            elif not final and len(lead) < 3 and "```".startswith(lead):
                return False  # nothing yet, or what may become a fence
            self._buffer, self._fence_settled = text, True
        # The line being completed, said again in front of its completion.
        text, line = self._buffer, self._line
        if len(line.strip()) >= self.MIN_OVERLAP:
            bare = line.lstrip()
            if not final and len(text) < len(line) and (
                line.startswith(text) or bare.startswith(text)
            ):
                return False  # may still be that line
            if text.startswith(line):
                text = text[len(line) :]
            elif text.startswith(bare):
                text = text[len(bare) :]
        self._buffer = text
        self._started = True
        return True

    def _close(self, text: str) -> str:
        """The end of the answer: without a closing fence, and without what
        already follows the cursor."""
        if self._fenced:
            trimmed = text.rstrip()
            if trimmed.endswith("```"):
                text = trimmed[:-3]
                if text.endswith("\n"):
                    text = text[:-1]
        elif text.rstrip().endswith("\n```"):
            text = text.rstrip()[:-4]
        suffix = self._suffix
        for size in range(min(len(text), len(suffix), self.HOLD), 0, -1):
            if text.endswith(suffix[:size]):
                if len(suffix[:size].strip()) >= self.MIN_OVERLAP:
                    text = text[: len(text) - size]
                break
        return text

    def _take(self, *, final: bool) -> str:
        if not self._started and not self._open(final=final):
            return ""
        if final:
            out, self._buffer = self._close(self._buffer), ""
            return out
        if len(self._buffer) <= self.HOLD:
            return ""
        out, self._buffer = self._buffer[: -self.HOLD], self._buffer[-self.HOLD :]
        return out


def clean(text: str, prefix: str, suffix: str) -> str:
    """`Inserted` over a whole answer."""
    cleaner = Inserted(prefix, suffix)
    return cleaner.feed(text) + cleaner.finish()


async def stream(ask: Ask) -> AsyncIterator[tuple[str, Any]]:
    """Ask, and yield `(DELTA, text)` as the text comes and one closing
    `(END, {"finish", "usage", "way"})`. `usage` is the server's own object in
    OpenAI's shape, or None where it said nothing. Raises `LLMError`."""
    prefix, suffix = window(ask.prefix, ask.suffix, ask.context_length)
    way = way_for(ask.endpoint, ask.server, ask.model_id)
    if way != WAY_CHAT:
        try:
            async for event in _native(way, ask, prefix, suffix):
                yield event
            return
        except _Unsupported as exc:
            _NO_FIM[(ask.endpoint.base_url, ask.model_id)] = time.monotonic()
            log.info(
                "%s cannot fill in with %s (%s); asking as a chat from now on",
                ask.endpoint.base_url, ask.model_id, exc,
            )
    async for event in _chat(ask, prefix, suffix):
        yield event


async def _chat(ask: Ask, prefix: str, suffix: str) -> AsyncIterator[tuple[str, Any]]:
    from lembas.services.llm import openai_client

    body: dict[str, Any] = {
        key: value
        for key, value in (ask.params or {}).items()
        if key not in ("reasoning_effort", "max_tokens", "max_completion_tokens")
    }
    body["model"] = ask.model_id
    body["messages"] = messages_for(ask, prefix, suffix)
    body["max_tokens"] = ask.max_tokens + (THINKING_ROOM if ask.thinks else 0)
    body.update(ask.quiet or {})
    cleaner = Inserted(prefix, suffix)
    usage: dict[str, Any] | None = None
    finish = FINISH_STOP
    chunks = stream_chat(ask.endpoint, body)
    try:
        async for chunk in chunks:
            if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
                usage = chunk["usage"]
            if openai_client.finish_reason(chunk) == "length":
                finish = FINISH_LENGTH
            text = openai_client.delta_text(chunk)
            if text and (out := cleaner.feed(text)):
                yield DELTA, out
    finally:
        await chunks.aclose()
    if out := cleaner.finish():
        yield DELTA, out
    yield END, {"finish": finish, "usage": usage, "way": WAY_CHAT}


def _request_for(way: str, ask: Ask, prefix: str, suffix: str) -> tuple[str, dict[str, Any]]:
    """The address and body of a server's own fill-in request."""
    if way == WAY_INFILL:
        infill_body: dict[str, Any] = {
            # llama-swap and llama.cpp's router choose the model by this.
            "model": ask.model_id,
            "input_prefix": prefix,
            "input_suffix": suffix,
            "n_predict": ask.max_tokens,
            "cache_prompt": True,
            "stream": True,
        }
        if ask.context:
            infill_body["input_extra"] = [
                {"filename": chunk.path, "text": chunk.text}
                for chunk in ask.context
                if chunk.text
            ]
        return ask.endpoint.root_url("infill"), infill_body
    prompt = prefix
    if ask.context:
        header = "\n\n".join(
            f"# File: {chunk.path}\n{chunk.text}" for chunk in ask.context if chunk.text
        )
        if header:
            prompt = f"{header}\n\n# File: {ask.path or 'current'}\n{prefix}"
    body: dict[str, Any] = {
        "model": ask.model_id,
        "prompt": prompt,
        "max_tokens": ask.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if suffix:
        body["suffix"] = suffix
    if way == WAY_DEEPSEEK:
        return ask.endpoint.root_url("beta/completions"), body
    return ask.endpoint.url("completions"), body


async def _native(
    way: str, ask: Ask, prefix: str, suffix: str
) -> AsyncIterator[tuple[str, Any]]:
    """A server's own fill-in answer, as written."""
    url, body = _request_for(way, ask, prefix, suffix)
    try:
        async for event in _native_once(way, ask, url, body):
            yield event
        return
    except _WithoutStreamOptions:
        body = {key: value for key, value in body.items() if key != "stream_options"}
    async for event in _native_once(way, ask, url, body):
        yield event


async def _native_once(
    way: str, ask: Ask, url: str, body: dict[str, Any]
) -> AsyncIterator[tuple[str, Any]]:
    usage: dict[str, Any] | None = None
    finish = FINISH_STOP
    try:
        async with (
            httpx.AsyncClient(timeout=settings.request_timeout) as client,
            client.stream("POST", url, headers=ask.endpoint.headers(), json=body) as response,
        ):
            if response.status_code >= 400:
                await response.aread()
                said = response.text[:2000]
                if response.status_code in _NO_SUCH_CALL:
                    raise _Unsupported(response.status_code, said)
                if response.status_code in _MAYBE:
                    if "stream_options" in body and "stream_options" in said:
                        raise _WithoutStreamOptions
                    if _SAYS_UNSUPPORTED.search(said):
                        raise _Unsupported(response.status_code, said)
                response.raise_for_status()
            async for event, data in sse.iter_sse(response.aiter_lines()):
                if event == sse.COMMENT:
                    continue
                if event == sse.ERROR:
                    error = sse.error_object(data)["error"]
                    raise LLMError(
                        f"The model's server stopped the completion: "
                        f"{sse.error_message(error)}",
                        status_code=sse.error_status(error),
                    )
                data = data.strip()
                if not data or data == sse.DONE:
                    continue
                try:
                    frame = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict):
                    continue
                if frame.get("error"):
                    error = sse.error_object(data)["error"]
                    raise LLMError(
                        f"The model's server stopped the completion: "
                        f"{sse.error_message(error)}",
                        status_code=sse.error_status(error),
                    )
                text, ended, said = _read(way, frame)
                if said is not None:
                    usage = said
                if ended:
                    finish = ended
                if text:
                    yield DELTA, text
    except httpx.HTTPStatusError as exc:
        raise http_error(exc, ask.endpoint) from exc
    except httpx.RequestError as exc:
        raise wrap_transport_error(exc, ask.endpoint) from exc
    yield END, {"finish": finish, "usage": usage, "way": way}


def _count(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _read(way: str, frame: dict[str, Any]) -> tuple[str, str, dict[str, Any] | None]:
    """One frame of a fill-in answer: its text, how it ended ("" while it has
    not), and the usage it carries."""
    if way == WAY_INFILL:
        text = frame.get("content")
        text = text if isinstance(text, str) else ""
        if not frame.get("stop"):
            return text, "", None
        limited = frame.get("stop_type") == "limit" or bool(frame.get("stopped_limit"))
        prompt, written = _count(frame.get("tokens_evaluated")), _count(
            frame.get("tokens_predicted")
        )
        usage: dict[str, Any] | None = None
        if prompt or written:
            usage = {
                "prompt_tokens": prompt,
                "completion_tokens": written,
                "total_tokens": prompt + written,
            }
            timings = frame.get("timings")
            cached = _count(timings.get("cache_n")) if isinstance(timings, dict) else 0
            if cached:
                usage["cached_tokens"] = cached
        return text, FINISH_LENGTH if limited else FINISH_STOP, usage
    usage = frame["usage"] if isinstance(frame.get("usage"), dict) and frame["usage"] else None
    choices = frame.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices else None
    if not isinstance(choice, dict):
        return "", "", usage
    text = choice.get("text")
    reason = choice.get("finish_reason")
    ended = ""
    if reason:
        ended = FINISH_LENGTH if reason == "length" else FINISH_STOP
    return (text if isinstance(text, str) else ""), ended, usage


__all__ = [
    "DELTA",
    "END",
    "FINISH_LENGTH",
    "FINISH_STOP",
    "WAY_CHAT",
    "WAY_COMPLETIONS",
    "WAY_DEEPSEEK",
    "WAY_INFILL",
    "Ask",
    "ContextChunk",
    "Inserted",
    "clean",
    "messages_for",
    "reset",
    "stream",
    "way_for",
    "window",
]
