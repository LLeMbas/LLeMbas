"""Running a ComfyUI workflow of any kind: the steps a tool, a job and the
administrator's *Test* share.

`fill` turns a stored template and a request into the document `/prompt`
takes: input pictures are uploaded first (`POST /upload/image`) and their
names put where `{{image}}`, `{{image2}}` and `{{mask}}` are; everything else
is typed and clamped by `workflow.resolve_kind`. `collect` picks the file of
the right kind out of whatever the run saved and fetches it under the kind's
byte cap. Nothing here stores anything or knows what a chat is.
"""

from __future__ import annotations

import hashlib
from typing import Any

from lembas.services.images import comfy, workflow
from lembas.services.media import store

MB = 1024 * 1024
INPUT_NAMES = ("image", "image2", "mask")


def config_of(values: dict[str, Any]) -> comfy.Config:
    """The client snapshot of a ComfyUI's settings (`instance.comfy_values`,
    or the image settings' view), the key decrypted at the last moment."""
    from lembas.services.crypto import decrypt

    return comfy.Config(
        base_url=str(values.get("base_url") or ""),
        api_key=decrypt(str(values.get("api_key_encrypted") or "")),
        timeout=float(values.get("timeout") or comfy.DEFAULT_TIMEOUT),
    )


def limit_for(settings: dict[str, Any], kind: str) -> int:
    """The most bytes of one generated file of a kind that are fetched."""
    if kind in (store.CAP_IMAGE, store.CAP_IMAGE_EDIT):
        return comfy.MAX_IMAGE_BYTES
    return int(settings.get(f"{kind}_max_mb") or 64) * MB


def max_seconds(settings: dict[str, Any], kind: str) -> int:
    return int(settings.get(f"{kind}_max_seconds") or 0) or int(workflow.LIMITS["seconds"][1])


async def fill(
    config: comfy.Config,
    kind: str,
    template: dict[str, Any],
    given: dict[str, Any],
    *,
    settings: dict[str, Any] | None = None,
    pictures: dict[str, bytes] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """`(the workflow to queue, the values it was filled with)`. Raises
    `ComfyError` when the template wants a picture nobody gave, or the
    ComfyUI will not take one."""
    from lembas.services import files as files_service

    used = workflow.placeholders_in(template)
    given = dict(given or {})
    pictures = pictures or {}
    for name in INPUT_NAMES:
        if name not in used:
            continue
        payload = pictures.get(name)
        if payload is None:
            if name == "image":
                raise comfy.ComfyError("This workflow needs an input picture and none was given.")
            if name == "image2":
                # One picture given where a workflow can take two: both are it.
                given[name] = given.get("image", "")
            continue
        found = files_service._detect_image(payload)
        if found is None:
            raise comfy.ComfyError("The input is not a picture this instance can send.")
        digest = hashlib.sha256(payload).hexdigest()[:16]
        given[name] = await comfy.upload_image(config, payload, f"lembas-{digest}{found[1]}")
    if "mask" in used and not given.get("mask"):
        raise comfy.ComfyError("This workflow needs a mask picture and none was given.")
    params = workflow.resolve_kind(
        kind, given, settings=settings, max_seconds=max_seconds(settings or {}, kind)
    )
    return workflow.fill(template, params), params


async def collect(
    config: comfy.Config, kind: str, found: list[comfy.Out], *, limit: int
) -> tuple[bytes, str]:
    """`(bytes, extension)` of the file a finished run made for this kind."""
    out = comfy.pick(found, kind)
    if out is None:
        saved = ", ".join(sorted({o.extension or "?" for o in found})) or "nothing"
        raise comfy.ComfyError(
            f"ComfyUI finished but saved no file of the kind asked for (it saved: {saved}). "
            "The workflow's kind may be wrong, or it has no save node."
        )
    return await comfy.fetch_file(config, out, limit=limit), out.extension


def test_picture(side: int = 256) -> bytes:
    """A plain grey PNG, for trying a workflow that wants an input picture."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + b"\x80\x80\x80" * side
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * side))
        + chunk(b"IEND", b"")
    )


TEST_GIVEN: dict[str, Any] = {
    "prompt": "a small red apple on a white table",
    "tags": "calm piano, slow",
    "seconds": 2,
    "width": 512,
    "height": 512,
}


async def test_run(
    config: comfy.Config, kind: str, template: dict[str, Any], settings: dict[str, Any],
    *, model: str = "",
) -> str:
    """Run a workflow once, small, and say what came back: for the
    administrator's *Test*. Nothing is kept. Raises `ComfyError` in words."""
    from lembas.services.media import library

    given = {**TEST_GIVEN, "model": model}
    if kind == store.CAP_MUSIC:
        given["seconds"] = 10
    picture = test_picture()
    doc, _ = await fill(
        config, kind, template, given, settings=settings,
        pictures={"image": picture, "image2": picture, "mask": picture},
    )
    found = await comfy.await_outputs(config, await comfy.submit(config, doc))
    payload, hint = await collect(config, kind, found, limit=limit_for(settings, kind))
    extension, mime = library.sniff(kind, payload, hint)
    return f"{mime} (.{extension}), {max(1, len(payload) // 1024)} kB"


__all__ = [
    "INPUT_NAMES", "collect", "config_of", "fill", "limit_for", "max_seconds", "test_picture",
    "test_run",
]
