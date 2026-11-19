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
import json
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
        raise _unasked(t("Nothing is configured to do that on this instance."))
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
    return await _dashscope_picture(payload)


async def _dashscope_picture(payload: dict[str, Any]) -> bytes:
    """The one picture in a multimodal-generation answer, collected."""
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


# --- Editing a picture -----------------------------------------------------------
MAX_INPUTS = 2
EDIT_INPUT_BYTES = 10 * 1024 * 1024


def data_uri(payload: bytes) -> str:
    """A picture as the `data:` address both providers take in place of a
    link. Its type is read from its own bytes."""
    from lembas.services import files as files_service

    found = files_service._detect_image(payload)
    if found is None:
        raise LLMError(t("That is not a picture this instance can send."))
    return f"data:{found[0]};base64,{base64.b64encode(payload).decode('ascii')}"


async def edit(
    source: Source,
    prompt: str,
    pictures: list[bytes],
    *,
    negative: str = "",
    width: int | None = None,
    height: int | None = None,
    seed: int | None = None,
) -> bytes:
    """One edited picture's bytes from a connection's editing model, checked
    like a generated one. `pictures`: one or two, each a picture by its own
    bytes and no larger than a provider takes."""
    if source.endpoint is None or not pictures:
        raise _unasked(t("Nothing is configured to do that on this instance."))
    pictures = pictures[:MAX_INPUTS]
    if any(len(p) > EDIT_INPUT_BYTES for p in pictures):
        raise _unasked(t("That picture is larger than the provider takes for editing."))
    if not (width and height):
        preset = store.size_of((source.options.get("sizes") or [""])[0])
        width, height = preset if preset is not None else (None, None)
    negative = negative or str(source.options.get("negative") or "")
    try:
        if source.dialect == store.DASHSCOPE_EDIT:
            payload = await _dashscope_edit(source, prompt, pictures, negative, width, height, seed)
        elif source.dialect == store.OPENAI_EDIT:
            payload = await _openai_edit(source, prompt, pictures, width, height)
        else:
            raise _unasked(t("Nothing is configured to do that on this instance."))
    except LLMError as exc:
        raise sources.scrubbed(source, exc) from exc
    return checked(payload)


def _unasked(message: str) -> LLMError:
    """A refusal made here, before anything was asked of the provider: it
    certainly made and charged nothing (`sources.refused`)."""
    error = LLMError(message)
    error.refused = True
    return error


async def _dashscope_edit(
    source: Source, prompt: str, pictures: list[bytes], negative: str,
    width: int | None, height: int | None, seed: int | None,
) -> bytes:
    parameters: dict[str, Any] = {"n": 1, "watermark": False}
    if width and height:
        parameters["size"] = f"{width}*{height}"
    if negative:
        parameters["negative_prompt"] = negative[:500]
    if seed is not None and seed >= 0:
        parameters["seed"] = seed
    content: list[dict[str, str]] = [{"image": data_uri(p)} for p in pictures]
    content.append({"text": prompt})
    body = {
        "model": source.model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }
    payload = await dashscope.post(
        source.endpoint, dashscope.native_url(source.endpoint, dashscope.GENERATION), body,
        timeout=TIMEOUT,
    )
    return await _dashscope_picture(payload)


async def _openai_edit(
    source: Source, prompt: str, pictures: list[bytes], width: int | None, height: int | None
) -> bytes:
    """OpenAI's `POST /images/edits`, the multipart form: `model`, `prompt`,
    the picture(s) as `image` (one) or `image[]` (several). The answer is
    read like a generation's: `data[0].b64_json`, or a link."""
    import httpx

    from lembas.services import files as files_service
    from lembas.services.llm.openai_client import wrap_transport_error

    fields: dict[str, str] = {"model": source.model, "prompt": prompt, "n": "1"}
    if width and height:
        fields["size"] = f"{width}x{height}"
    name = "image" if len(pictures) == 1 else "image[]"
    files = []
    for number, picture in enumerate(pictures):
        found = files_service._detect_image(picture)
        if found is None:
            raise _unasked(t("That is not a picture this instance can send."))
        files.append((name, (f"image{number + 1}{found[1]}", picture, found[0])))
    headers = {
        k: v for k, v in source.endpoint.headers().items() if k.lower() != "content-type"
    }
    data = bytearray()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client, client.stream(
            "POST", source.endpoint.url("images/edits"), headers=headers, data=fields, files=files
        ) as response:
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > dashscope.MAX_ANSWER_BYTES:
                    raise LLMError(t("The endpoint's answer is larger than this server reads."))
            status = response.status_code
            reason = response.reason_phrase
    except httpx.RequestError as exc:
        raise wrap_transport_error(exc, source.endpoint) from exc
    if status >= 400:
        raise dashscope.error_of(
            source.endpoint,
            httpx.Response(status, content=bytes(data),
                           headers={"content-type": "application/json"},
                           extensions={"reason_phrase": reason.encode()}),
        )
    try:
        payload = json.loads(bytes(data))
    except ValueError as exc:
        raise LLMError(t("The endpoint returned a response that was not JSON.")) from exc
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


__all__ = ["SIDE_MAX", "SIDE_MIN", "TIMEOUT", "checked", "data_uri", "edit", "generate", "side"]
