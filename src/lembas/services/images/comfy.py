"""Talking to ComfyUI.

Four calls and a discovery one, all plain httpx. `fetch.fetch` cannot be reused
for the same reasons `custom_tools` gives -- it is GET-only, bodyless, and
refuses every content type that is not HTML or text, which is both the JSON here
and the PNG at the end of it.

**The base URL is exempt from the SSRF guard, and that is deliberate rather than
forgotten.** `fetch.check_url` exists to stop a *model or a reader* pointing the
application at something on the private network; this address was typed by an
administrator into the admin page, exactly like `Connection.base_url` and the two
audio endpoints, none of which are checked either. Saying so here because the
default value is `127.0.0.1:8188`, which is precisely the shape the guard exists
to refuse and therefore looks like a hole rather than a decision.

Progress is **polled, not streamed**. ComfyUI offers a WebSocket for it, and
holding one open for the length of a generation is live-connection state that a
restart breaks; polling `/history` is self-healing across a
restart of either side, and the thing being waited for takes tens of seconds, so
a poll costs nothing anybody can measure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from lembas.services.llm.openai_client import (
    LLMError,
    describe_http_error,
)

log = logging.getLogger(__name__)

# What one generated image may weigh. A cap is required rather than tidy: this is
# the only place in the codebase where an external service hands back raw bytes
# that are then written to disk, and neither `audio.speak` nor `openai_client`
# has one to copy. Generous, because a 2048px PNG is a legitimate several
# megabytes and refusing it would be refusing the feature.
MAX_IMAGE_BYTES = 32 * 1024 * 1024

# How often to ask whether it has finished, and how long to keep asking. The
# interval is not adaptive: unlike a background job, which may run for hours,
# a generation is over in tens of seconds and the whole reply is parked on it.
POLL_INTERVAL = 1.0
# How long to wait for the queue *before* our own job starts running. A busy
# ComfyUI with somebody else's batch in front of us is not an error.
DEFAULT_TIMEOUT = 600.0


@dataclass(frozen=True)
class Config:
    """Everything a call needs, lifted out of the settings group.

    A snapshot rather than a session, for the reason `ToolContext` is one: a
    generation outlives the request that resolved it.
    """

    base_url: str
    api_key: str = ""
    timeout: float = DEFAULT_TIMEOUT

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def headers(self) -> dict[str, str]:
        # ComfyUI itself has no auth; a key is only ever for something in front
        # of it, so an empty one must not become `Authorization: Bearer `.
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


@dataclass(frozen=True)
class Ref:
    """Where a finished image lives on the far side."""

    filename: str
    subfolder: str = ""
    kind: str = "output"


class ComfyError(LLMError):
    """Anything that stopped a generation, in words worth showing somebody."""


class OutOfMemory(ComfyError):
    """The far side ran out of VRAM.

    Its own class because it is the one failure with an obvious next move --
    a smaller picture, or a smaller checkpoint -- and the model is told to make
    it. Everything else is reported and stopped at.
    """


class Interrupted(ComfyError):
    """Somebody cancelled it from ComfyUI's own interface, or it was stopped.

    Distinct because it is not a fault: retrying is reasonable, and "the
    workflow failed" would be describing a decision as a breakage.
    """


# What `exception_type` looks like when a GPU has run out. Matched on the type
# rather than on the message, which is a paragraph of allocator advice written
# for whoever is running the box and not for a model.
_OOM_TYPES = ("outofmemory", "out_of_memory", "cuda error: out of memory")


def _transport_error(exc: httpx.RequestError, config: Config) -> ComfyError:
    """The `wrap_transport_error` shape, said about ComfyUI rather than an LLM.

    Not reused directly: that one names the request timeout from the deployment
    settings, which is not the timeout in force here.
    """
    if isinstance(exc, httpx.ConnectError):
        return ComfyError(
            f"Could not reach ComfyUI at {config.base_url}. Is it running and the URL correct?"
        )
    if isinstance(exc, httpx.TimeoutException):
        return ComfyError(f"ComfyUI at {config.base_url} did not respond in time.")
    return ComfyError(f"Could not reach ComfyUI at {config.base_url}: {exc}")


async def _get_json(config: Config, path: str, *, timeout: float = 30.0) -> Any:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(config.url(path), headers=config.headers())
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        raise ComfyError(describe_http_error(exc), status_code=exc.response.status_code) from exc
    except httpx.RequestError as exc:
        raise _transport_error(exc, config) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise ComfyError(f"ComfyUI sent something that is not JSON: {exc}") from exc


async def submit(config: Config, workflow: dict[str, Any]) -> str:
    """Queue a workflow, and answer with the id it was given.

    A `node_errors` block is a refusal rather than a failure: the workflow was
    accepted as JSON and rejected as a graph, usually because a checkpoint name
    does not exist on that machine. It is reported with the node named, because
    "invalid prompt" against a twelve-node document says nothing.
    """
    body = {"prompt": workflow, "client_id": uuid.uuid4().hex}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(config.url("prompt"), headers=config.headers(), json=body)
            if response.status_code >= 400:
                raise ComfyError(_refusal(response))
            data = response.json()
    except ComfyError:
        raise
    except httpx.RequestError as exc:
        raise _transport_error(exc, config) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise ComfyError(f"ComfyUI sent something that is not JSON: {exc}") from exc

    if errors := (data.get("node_errors") or {}):
        raise ComfyError(_describe_nodes(errors))
    prompt_id = str(data.get("prompt_id") or "")
    if not prompt_id:
        raise ComfyError("ComfyUI accepted the workflow but did not say what to call it.")
    return prompt_id


def _refusal(response: httpx.Response) -> str:
    """Why ComfyUI would not take a workflow, in one sentence."""
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return f"ComfyUI refused the workflow (HTTP {response.status_code})."
    if isinstance(payload, dict):
        if errors := (payload.get("node_errors") or {}):
            return _describe_nodes(errors)
        if message := payload.get("error"):
            if isinstance(message, dict):
                message = message.get("message") or message.get("type") or ""
            return f"ComfyUI refused the workflow: {message}"
    return f"ComfyUI refused the workflow (HTTP {response.status_code})."


def _describe_nodes(errors: dict[str, Any]) -> str:
    parts: list[str] = []
    for node, detail in list(errors.items())[:4]:
        messages = detail.get("errors") if isinstance(detail, dict) else None
        first = ""
        if isinstance(messages, list) and messages:
            entry = messages[0]
            first = entry.get("message", "") if isinstance(entry, dict) else str(entry)
        parts.append(f"node {node}: {first}" if first else f"node {node}")
    return "ComfyUI refused the workflow — " + "; ".join(parts)


async def await_images(config: Config, prompt_id: str) -> list[Ref]:
    """Wait for one queued workflow and answer with what it saved.

    **The record existing is what "finished" means, not `status.completed`.**
    ComfyUI writes the history entry in `task_done` and nowhere else, so it
    appears exactly once the job is over -- but it sets `completed=e.success`,
    so a run that failed is `completed: false` for ever. Waiting on that flag
    means every out-of-memory, every cancelled job and every broken node hangs
    the reply for the whole timeout and then reports a timeout, when ComfyUI
    knew what was wrong within seconds and said so.

    So: no record means not yet, a record means done, and `status_str` says
    which kind of done.
    """
    deadline = time.monotonic() + config.timeout
    while True:
        record = (await _get_json(config, f"history/{prompt_id}")).get(prompt_id)
        if isinstance(record, dict) and record.get("status") is not None:
            status = record.get("status") or {}
            if status.get("status_str") != "success":
                raise _failure(status)
            return _refs_in(record.get("outputs") or {})
        if time.monotonic() > deadline:
            raise ComfyError(
                f"ComfyUI did not finish within {config.timeout:.0f}s. "
                "It may still be working; the queue is on its own page."
            )
        await asyncio.sleep(POLL_INTERVAL)


def _failure(status: dict[str, Any]) -> ComfyError:
    """Why a workflow stopped, out of the messages ComfyUI recorded against it.

    `status.messages` is a list of `[name, payload]` pairs -- the lifecycle of
    the run. The last `execution_error` or `execution_interrupted` in it is the
    thing that ended it, and carries the node and the exception. Without reading
    these the only thing that could be said is "error", which is what ComfyUI's
    own status string amounts to.
    """
    event, payload = "", {}
    for entry in status.get("messages") or []:
        if isinstance(entry, list | tuple) and len(entry) == 2:
            name, body = entry
            if name in ("execution_error", "execution_interrupted"):
                event, payload = str(name), body if isinstance(body, dict) else {}

    node = str(payload.get("node_type") or "").strip()
    where = f" in {node}" if node else ""

    if event == "execution_interrupted":
        return Interrupted(f"The image was cancelled on the ComfyUI side{where}.")

    kind = str(payload.get("exception_type") or "")
    detail = _first_sentence(str(payload.get("exception_message") or ""))
    if any(marker in kind.lower() for marker in _OOM_TYPES) or "out of memory" in detail.lower():
        return OutOfMemory(f"ComfyUI ran out of video memory{where}. {detail}".strip())
    if not detail and not kind:
        return ComfyError(f"ComfyUI could not finish the workflow{where}.")
    return ComfyError(f"ComfyUI could not finish the workflow{where}: {detail or kind}")


def _first_sentence(message: str) -> str:
    """Enough of an exception to act on, and no more.

    A torch OOM runs to several lines of allocator advice -- environment
    variables to set, fragmentation notes -- addressed to whoever runs the box.
    None of it means anything to a model, and all of it costs tokens in a tool
    result that is already a failure.
    """
    first = message.strip().split("\n", 1)[0].strip()
    if len(first) > 200:
        first = first[:200].rsplit(" ", 1)[0] + "…"
    return first


def _refs_in(outputs: dict[str, Any]) -> list[Ref]:
    """Every image any node saved, in node order.

    Every node is read rather than a `SaveImage` being looked for by name: a
    template is somebody else's document and may save from a node called
    anything, or from two of them.
    """
    refs: list[Ref] = []
    for node in outputs.values():
        for image in (node or {}).get("images") or []:
            if filename := str(image.get("filename") or ""):
                refs.append(
                    Ref(
                        filename=filename,
                        subfolder=str(image.get("subfolder") or ""),
                        kind=str(image.get("type") or "output"),
                    )
                )
    return refs


async def fetch_image(config: Config, ref: Ref) -> bytes:
    """The bytes of one finished image."""
    params = {"filename": ref.filename, "subfolder": ref.subfolder, "type": ref.kind}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(config.url("view"), headers=config.headers(), params=params)
            response.raise_for_status()
            payload = response.content
    except httpx.HTTPStatusError as exc:
        raise ComfyError(describe_http_error(exc), status_code=exc.response.status_code) from exc
    except httpx.RequestError as exc:
        raise _transport_error(exc, config) from exc

    if not payload:
        raise ComfyError(f"ComfyUI returned an empty file for {ref.filename}.")
    if len(payload) > MAX_IMAGE_BYTES:
        raise ComfyError(
            f"{ref.filename} is {len(payload) // (1024 * 1024)}MB, over the "
            f"{MAX_IMAGE_BYTES // (1024 * 1024)}MB limit."
        )
    return payload


async def free(config: Config) -> None:
    """Ask ComfyUI to drop its models from memory.

    Best-effort by design and never raised into the caller: this runs on the way
    out of a generation that has already produced its image, and failing the
    whole tool because a memory hint was refused would be turning a tidy-up into
    an error. The consequence of it silently not working is VRAM staying used,
    which is the state Preserve VRAM was already in before it was switched on.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.post(
                config.url("free"),
                headers=config.headers(),
                json={"unload_models": True, "free_memory": True},
            )
    except Exception:  # noqa: BLE001 - a hint that failed is not a failed generation
        log.debug("could not free ComfyUI at %s", config.base_url, exc_info=True)


async def discover(config: Config) -> tuple[list[str], list[str], list[str]]:
    """What this ComfyUI can actually do: checkpoints, samplers, schedulers.

    For the admin page only. Never called from the request path -- the tool
    reads the stored lists, exactly as the project listing is read from a cache
    rather than walked, because a keystroke must not wait on a machine.
    """
    checkpoints = _options(
        await _get_json(config, "object_info/CheckpointLoaderSimple"),
        "CheckpointLoaderSimple",
        "ckpt_name",
    )
    sampler_info = await _get_json(config, "object_info/KSampler")
    samplers = _options(sampler_info, "KSampler", "sampler_name")
    schedulers = _options(sampler_info, "KSampler", "scheduler")
    return checkpoints, samplers, schedulers


def _options(payload: Any, node: str, field: str) -> list[str]:
    """The allowed values of one input, out of an `/object_info` document.

    The shape is `{node: {input: {required: {field: [[...values], {...meta}]}}}}`
    -- a list whose first element is the list of options. Read defensively: this
    is somebody else's schema and a custom node pack can change it.
    """
    try:
        spec = payload[node]["input"]["required"][field][0]
    except (KeyError, IndexError, TypeError):
        return []
    return [str(value) for value in spec] if isinstance(spec, list) else []
