"""One picture from a connection's image model: `generate(source, prompt, …)`.

Two shapes behind the one function, chosen by the source's dialect
(`store.dialect`): OpenAI's `POST /images/generations` on the connection's
base URL, and Alibaba Cloud Model Studio's native synchronous route. Both
hand back the **bytes** of one picture -- a provider's link is collected at
once (`dashscope.collect`) and never passed on.

The instance's own ComfyUI is the third source and stays where it was
(`services/images/tool.py`): it has workflows, checkpoints and a reviewer
that nothing here has.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from lembas.services.llm.openai_client import LLMError
from lembas.services.media import dashscope, sources, store
from lembas.services.media.sources import Source
from lembas.web.i18n import t

# A generation is tens of seconds; the provider's own advice for its
# synchronous route is to allow minutes.
TIMEOUT = 600.0
SIDE_MIN, SIDE_MAX = 256, 4096


def side(value: Any) -> int | None:
    """A width or a height a model asked for, or None for "the model's own"."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if SIDE_MIN <= number <= SIDE_MAX else None


async def generate(
    source: Source,
    prompt: str,
    *,
    negative: str = "",
    width: int | None = None,
    height: int | None = None,
    seed: int | None = None,
) -> bytes:
    """One picture's bytes -- checked to *be* a picture this instance can
    keep. Raises `LLMError` in the provider's words, without its key."""
    if source.endpoint is None:
        raise LLMError(t("Nothing is configured to do that on this instance."))
    # The model's own options where the request has no opinion: its first
    # size, its negative prompt.
    if not (width and height):
        preset = store.size_of((source.options.get("sizes") or [""])[0])
        width, height = preset if preset is not None else (None, None)
    negative = negative or str(source.options.get("negative") or "")
    try:
        if source.dialect == store.DASHSCOPE_IMAGE:
            payload = await _dashscope(source, prompt, negative, width, height, seed)
        else:
            payload = await _openai(source, prompt, width, height)
    except LLMError as exc:
        raise sources.scrubbed(source, exc) from exc
    return checked(payload)


def checked(payload: bytes) -> bytes:
    """`payload` when it is an image by its own bytes and no larger than an
    upload may be. Anything else is a failure of the source -- so another is
    tried, and nothing is stored or counted as a picture."""
    from lembas.services import files as files_service

    if files_service._detect_image(payload) is None:
        raise LLMError(t("The image endpoint returned something that is not a picture."))
    if len(payload) > files_service.limits().max_upload_bytes:
        raise LLMError(t("The picture is larger than this instance stores."))
    return payload


async def _openai(source: Source, prompt: str, width: int | None, height: int | None) -> bytes:
    body: dict[str, Any] = {"model": source.model, "prompt": prompt, "n": 1}
    if width and height:
        body["size"] = f"{width}x{height}"
    # No `response_format`: some image models refuse the field, and both
    # answers -- bytes or a link -- are read below.
    payload = await dashscope.post(
        source.endpoint, source.endpoint.url("images/generations"), body, timeout=TIMEOUT
    )
    first = dashscope.dig(payload, "data", 0)
    first = first if isinstance(first, dict) else {}
    encoded = first.get("b64_json")
    if isinstance(encoded, str) and encoded:
        try:
            return base64.b64decode(encoded, validate=False)
        except (binascii.Error, ValueError) as exc:
            raise LLMError(
                t("The image endpoint returned a picture that could not be read.")
            ) from exc
    if first.get("url"):
        return await dashscope.collect(
            first["url"], limit=dashscope.MAX_IMAGE_BYTES, endpoint=source.endpoint
        )
    raise LLMError(t("The image endpoint returned no picture."))


async def _dashscope(
    source: Source, prompt: str, negative: str, width: int | None, height: int | None,
    seed: int | None,
) -> bytes:
    parameters: dict[str, Any] = {"n": 1, "watermark": False}
    if width and height:
        parameters["size"] = f"{width}*{height}"
    if negative:
        parameters["negative_prompt"] = negative[:500]
    if seed is not None and seed >= 0:
        parameters["seed"] = seed
    body = {
        "model": source.model,
        "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
        "parameters": parameters,
    }
    payload = await dashscope.post(
        source.endpoint, dashscope.native_url(source.endpoint, dashscope.GENERATION), body,
        timeout=TIMEOUT,
    )
    choices = dashscope.dig(payload, "output", "choices")
    for choice in choices if isinstance(choices, list) else []:
        content = dashscope.dig(choice, "message", "content")
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and part.get("image"):
                return await dashscope.collect(part["image"], limit=dashscope.MAX_IMAGE_BYTES)
    # The older answer of the same route: `output.results[].url`.
    results = dashscope.dig(payload, "output", "results")
    for result in results if isinstance(results, list) else []:
        if isinstance(result, dict) and result.get("url"):
            return await dashscope.collect(result["url"], limit=dashscope.MAX_IMAGE_BYTES)
    raise LLMError(t("The image endpoint returned no picture."))


__all__ = ["SIDE_MAX", "SIDE_MIN", "TIMEOUT", "checked", "generate", "side"]
