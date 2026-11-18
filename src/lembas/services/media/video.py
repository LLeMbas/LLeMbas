"""Video from a connection: Alibaba Cloud Model Studio's task API.

A video is never an answer held open. The provider takes the request and
names a **task** (`POST …/video-generation/video-synthesis` with
`X-DashScope-Async: enable`), and is asked about it afterwards (`GET
/api/v1/tasks/{id}`) until it has succeeded, failed or been forgotten; a
finished task names an MP4 by a link that lasts a day, which is fetched at
once (`dashscope.collect`, with a byte cap) and never passed on. The task id
is what a job keeps (`services/media/jobs.py`), so a restart asks again
rather than starts again.

Two request shapes, by the model's generation -- the provider changed the
field names between them, and a model id is the only thing that says which:

- **current** (`wan2.7…`, `wan3…`, and anything not recognised as older):
  `parameters.resolution` (`720P`) with `parameters.ratio` (`16:9`), a first
  picture as `input.media: [{"type": "first_frame", "url": …}]`;
- **older** (`wan2.1` to `wan2.6`): text to video by `parameters.size`
  (`1280*720`), a first picture as `input.img_url` with
  `parameters.resolution`.

A picture is sent as a `data:` address, so nothing of the person's has to be
reachable from outside.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from lembas.services.llm.openai_client import LLMError
from lembas.services.media import dashscope, pictures, sources, store
from lembas.services.media.sources import Source
from lembas.web.i18n import t

SYNTHESIS = "services/aigc/video-generation/video-synthesis"
TASKS = "tasks"
ASYNC = {"X-DashScope-Async": "enable"}
SUBMIT_TIMEOUT = 120.0
POLL_TIMEOUT = 30.0

RATIOS = ("16:9", "9:16", "1:1", "4:3", "3:4")
# The older models' `size`, by resolution and ratio.
SIZES: dict[tuple[str, str], str] = {
    ("480P", "16:9"): "832*480", ("480P", "9:16"): "480*832", ("480P", "1:1"): "624*624",
    ("720P", "16:9"): "1280*720", ("720P", "9:16"): "720*1280", ("720P", "1:1"): "960*960",
    ("720P", "4:3"): "1088*832", ("720P", "3:4"): "832*1088",
    ("1080P", "16:9"): "1920*1080", ("1080P", "9:16"): "1080*1920",
    ("1080P", "1:1"): "1440*1440", ("1080P", "4:3"): "1632*1248", ("1080P", "3:4"): "1248*1632",
}
_OLDER = re.compile(r"^wan2\.[1-6]\b|^wanx")
FIRST_FRAME_BYTES = 10 * 1024 * 1024

RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
GONE = "gone"


@dataclass(frozen=True)
class Task:
    """What the provider says about a task, in this server's words."""

    state: str
    url: str = ""
    seconds: float = 0.0
    resolution: str = ""
    error: str = ""


def older(model: str) -> bool:
    return bool(_OLDER.match(model.lower()))


def resolution_for(source: Source, wanted: str = "") -> str:
    """The resolution to ask for: the one named when the model takes it, else
    the model's first, else 720P -- the one every model here documents."""
    offered = [str(r).upper() for r in source.options.get("resolutions") or []]
    wanted = str(wanted or "").upper()
    if wanted in store.RESOLUTIONS and (not offered or wanted in offered):
        return wanted
    return offered[0] if offered else "720P"


def seconds_for(source: Source, wanted: Any, ceiling: int) -> int:
    try:
        number = int(float(wanted))
    except (TypeError, ValueError):
        number = 5
    most = min(int(source.options.get("max_seconds") or ceiling), ceiling)
    return min(max(number, 2), max(most, 2))


def body_for(
    source: Source,
    prompt: str,
    *,
    picture: bytes | None,
    seconds: int,
    resolution: str,
    ratio: str,
    negative: str = "",
    seed: int | None = None,
) -> dict[str, Any]:
    """The request, in the shape the model's generation takes."""
    ratio = ratio if ratio in RATIOS else "16:9"
    inputs: dict[str, Any] = {"prompt": prompt[:5000]}
    negative = negative or str(source.options.get("negative") or "")
    if negative:
        inputs["negative_prompt"] = negative[:500]
    parameters: dict[str, Any] = {"duration": seconds, "watermark": False}
    if seed is not None and 0 <= seed <= 2147483647:
        parameters["seed"] = seed
    if older(source.model):
        if picture is not None:
            inputs["img_url"] = pictures.data_uri(picture)
            parameters["resolution"] = resolution
        else:
            parameters["size"] = SIZES.get((resolution, ratio)) or SIZES[("720P", "16:9")]
    else:
        parameters["resolution"] = resolution
        if picture is not None:
            inputs["media"] = [{"type": "first_frame", "url": pictures.data_uri(picture)}]
        else:
            parameters["ratio"] = ratio
    return {"model": source.model, "input": inputs, "parameters": parameters}


async def submit(source: Source, body: dict[str, Any]) -> str:
    """Start a task; its id. Raises `LLMError` in the provider's words."""
    if source.endpoint is None:
        raise LLMError(t("Nothing is configured to do that on this instance."))
    try:
        payload = await dashscope.post(
            source.endpoint, dashscope.native_url(source.endpoint, SYNTHESIS), body,
            timeout=SUBMIT_TIMEOUT, headers=ASYNC,
        )
    except LLMError as exc:
        raise sources.scrubbed(source, exc) from exc
    task_id = dashscope.dig(payload, "output", "task_id")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 200:
        raise LLMError(t("The provider accepted the request but named no task."))
    return task_id.strip()


def _said(source: Source, *parts: Any) -> str:
    text = " ".join(str(p) for p in parts if isinstance(p, str | int) and str(p))
    text = "".join(ch for ch in text if ch.isprintable())[: dashscope.MESSAGE_MAX]
    return sources.scrub(source, text)


async def poll(source: Source, task_id: str) -> Task:
    """One look at a task."""
    if source.endpoint is None:
        raise LLMError(t("Nothing is configured to do that on this instance."))
    try:
        payload = await dashscope.post(
            source.endpoint, dashscope.native_url(source.endpoint, f"{TASKS}/{task_id}"), None,
            timeout=POLL_TIMEOUT, method="GET",
        )
    except LLMError as exc:
        raise sources.scrubbed(source, exc) from exc
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    status = str(output.get("task_status") or "").upper()
    if status in ("PENDING", "RUNNING"):
        return Task(RUNNING)
    if status == "SUCCEEDED":
        url = output.get("video_url")
        if not isinstance(url, str) or not url:
            return Task(FAILED, error=t("The provider's answer named no result."))
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        seconds = 0.0
        for key in ("output_video_duration", "duration", "video_duration"):
            try:
                seconds = float(usage.get(key) or 0)
            except (TypeError, ValueError):
                seconds = 0.0
            if seconds > 0:
                break
        sr = usage.get("SR")
        return Task(DONE, url=url, seconds=seconds,
                    resolution=f"{sr}P" if isinstance(sr, int) else "")
    if status == "FAILED":
        return Task(FAILED, error=_said(source, output.get("code"), output.get("message"))
                    or t("The provider could not make it."))
    if status == "CANCELED":
        return Task(CANCELLED)
    # UNKNOWN: a task the provider no longer knows (they are kept a day).
    return Task(GONE, error=t("The provider no longer knows this task."))


async def cancel(source: Source, task_id: str) -> bool:
    """Ask the provider to cancel a task. It does so only for one that has
    not started; True when it said yes. Never raises: the job stops being
    waited for either way, and says which it was."""
    if source.endpoint is None:
        return False
    try:
        await dashscope.post(
            source.endpoint,
            dashscope.native_url(source.endpoint, f"{TASKS}/{task_id}/cancel"), {},
            timeout=POLL_TIMEOUT,
        )
    except LLMError:
        return False
    return True


__all__ = [
    "CANCELLED", "DONE", "FAILED", "GONE", "RATIOS", "RUNNING", "Task", "body_for",
    "cancel", "older", "poll", "resolution_for", "seconds_for", "submit",
]
