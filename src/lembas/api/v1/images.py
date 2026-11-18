"""Pictures over the API: `POST /v1/images/generations` and the file it
hands back.

OpenAI's shape -- `prompt`, `n`, `size`, `response_format`, `model` -- over the
instance's own ComfyUI, the one a chat's `image_generate` tool draws with: the
same workflow (the instance's default), the same checkpoints, the same defaults.
What differs is only what a program needs. There is no reviewer and no retry
loop, because there is no conversation to judge the picture against, and `n` is
capped by the image settings (`api_max_images`) rather than trusted.

**Gated as a chat's tool is**: the `models` scope, the *Draw pictures*
permission (`tools.image`) and the daily image limit. Each picture kept is
counted and priced at `per_image` into the caller's usage; nothing is refused
for what it costs.

**`url` by default, `b64_json` on request.** Every picture is stored as the
caller's own file whichever is asked for: that is what makes it count against
`images_per_day` (counted off stored images, as the tool's are), and it is what
`url` points at -- `GET /v1/files/{id}/content`, fetched with the same token,
never anybody else's. The file belongs to no chat, so the orphan sweep removes it
after the extraction settings' `orphan_hours` (a day unless changed): a URL that
lasts a day, where OpenAI's last an hour.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from lembas.api.deps import Db
from lembas.api.v1._common import error_response
from lembas.security import permissions
from lembas.security.api_tokens import ModelsCaller
from lembas.services import settings_store
from lembas.services.media import sources as media_sources
from lembas.services.media.store import CAP_IMAGE

log = logging.getLogger(__name__)

router = APIRouter(tags=["images"])

# What a model id from `/v1/models` looks like for a checkpoint: `comfyui/sdxl`.
PROVIDER = "comfyui"
FORMATS = ("url", "b64_json")
_SIZE = re.compile(r"^\s*(\d{1,5})\s*[xX×]\s*(\d{1,5})\s*$")


def available(db, user) -> bool:
    """Whether this person may draw through the API: the instance can draw, and
    they hold the permission a chat's tool asks for."""
    return media_sources.anywhere(db, CAP_IMAGE) and permissions.has(db, user, "tools.image")


def service_id(checkpoint: str) -> str:
    return f"{PROVIDER}/{checkpoint}"


def nearest_size(size: Any) -> tuple[int, int] | None:
    """`"1024x768"` as the nearest size a workflow takes: each side to the
    nearest multiple of 64, inside ComfyUI's 64-2048. None for no size, or one
    that does not read as WIDTHxHEIGHT -- the instance's default then."""
    from lembas.services.images.workflow import LIMITS

    match = _SIZE.match(str(size or ""))
    if not match:
        return None
    sides = []
    for raw, name in ((match.group(1), "width"), (match.group(2), "height")):
        low, high = LIMITS[name]
        sides.append(int(min(max(round(int(raw) / 64) * 64, low), high)))
    return sides[0], sides[1]


def _checkpoint(wanted: str, values: dict[str, Any]) -> tuple[str | None, bool]:
    """The checkpoint `model` names (`comfyui/<name>` or the bare name), and
    whether a name was given that this instance does not have. Anything else --
    `dall-e-3` from a client written for OpenAI, or nothing -- is the default."""
    from lembas.services.images.tool import _checkpoint as ladder

    available = [str(name) for name in values.get("checkpoints") or []]
    name = wanted.strip()
    explicit = name.startswith(f"{PROVIDER}/")
    if explicit:
        name = name[len(PROVIDER) + 1 :]
    if name in available:
        return name, False
    if explicit:
        return None, True
    fallback = str(values.get("default_checkpoint") or "")
    return ladder("", "", available, instance_default=fallback), False


@router.post("/v1/images/generations")
async def generations(request: Request, db: Db, caller: ModelsCaller):
    from lembas.api.v1.services import _chat_connection, _interactive
    from lembas.db.session import session_scope
    from lembas.services import files as files_service
    from lembas.services import usage as usage_service
    from lembas.services.images import comfy, workflow
    from lembas.services.llm.openai_client import LLMError
    from lembas.services.media import pictures

    user = caller.user
    if not available(db, user):
        return error_response(404, "Image generation is not on for this account on this instance.")
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        return error_response(400, "The body must be a JSON object.")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return error_response(400, "`prompt` is required: what to draw.")
    values = settings_store.images(db)
    try:
        count = int(body.get("n") or 1)
    except (TypeError, ValueError):
        return error_response(400, "`n` must be a whole number.")
    cap = int(values["api_max_images"])
    if not 1 <= count <= cap:
        return error_response(400, f"`n` is 1 to {cap} on this instance.")
    response_format = str(body.get("response_format") or "url")
    if response_format not in FORMATS:
        return error_response(400, "`response_format` is url or b64_json.")

    # Where it is drawn: the connection's own image model of the chat this is
    # for (`chat_id` or `chat_model`, as on `/v1/audio/*`), else the
    # instance's default -- its ComfyUI, or the connection's model an
    # administrator made the default.
    chain = media_sources.chain(
        db,
        CAP_IMAGE,
        _chat_connection(db, caller, body.get("chat_id"), body.get("chat_model")),
        interactive=_interactive(caller),
    )
    plan: dict[str, Any] | None = None
    comfy_first = bool(chain) and chain[0].dialect == media_sources.COMFYUI
    if any(source.dialect == media_sources.COMFYUI for source in chain):
        plan = _comfy_plan(db, body, values)
        # A checkpoint this instance does not have is said at once, as it
        # always was; a missing workflow only after the daily limit (below).
        if "error" in plan and not plan.get("late") and comfy_first:
            return plan["error"]
        if "error" in plan and not comfy_first:
            chain = [source for source in chain if source.dialect != media_sources.COMFYUI]
    if not chain:
        return error_response(404, "Image generation is not on for this account on this instance.")

    # The daily limit, before a minute of somebody's GPU is spent.
    if over := usage_service.over_image_budget(db, user):
        return error_response(429, over, "rate_limit_error")
    ceiling = permissions.limit(db, user, "images_per_day")
    if ceiling and usage_service.images_today(db, user.id) + count > ceiling:
        return error_response(
            429, f"That would be more than this account's {ceiling} image(s) for today.",
            "rate_limit_error",
        )
    if plan is not None and "error" in plan and comfy_first:
        return plan["error"]
    size = nearest_size(body.get("size"))
    user_id = user.id

    # Nothing is read from the request's session past here: a generation takes
    # a minute, and holding a transaction open across one is the trade every
    # long call in this codebase refuses. What is written after is written in
    # a session of its own.
    db.rollback()

    async def draw(source) -> list[bytes]:
        pictures_made: list[bytes] = []
        if source.dialect != media_sources.COMFYUI:
            while len(pictures_made) < count:
                try:
                    pictures_made.append(
                        await pictures.generate(
                            source, prompt,
                            width=size[0] if size else None, height=size[1] if size else None,
                        )
                    )
                except LLMError:
                    if not pictures_made:
                        raise
                    break
            return pictures_made
        try:
            while len(pictures_made) < count:
                params = workflow.resolve(plan["given"], settings=values)
                refs = await comfy.await_images(
                    plan["config"],
                    await comfy.submit(plan["config"], workflow.fill(plan["template"], params)),
                )
                if not refs:
                    raise comfy.ComfyError("ComfyUI finished but saved no image.")
                for ref in refs[: count - len(pictures_made)]:
                    pictures_made.append(await comfy.fetch_image(plan["config"], ref))
        except comfy.ComfyError as exc:
            log.info("an image through the API failed: %s", exc.message)
            if not pictures_made:
                raise LLMError(f"The image could not be generated: {exc.message}") from exc
        return pictures_made

    try:
        used = await media_sources.first_that_works(chain, draw)
    except LLMError as exc:
        return error_response(502, exc.message, "upstream_error")
    source = used.source
    comfyui = source.dialect == media_sources.COMFYUI
    slug = plan["slug"] if comfyui else "image"
    made_with = plan["checkpoint"] if comfyui else source.label

    base = str(request.base_url).rstrip("/")
    data: list[dict[str, Any]] = []
    with session_scope() as session:
        for number, payload in enumerate(used.result, start=1):
            try:
                attachment = files_service.store(
                    session,
                    user_id=user_id,
                    chat_id=None,
                    payload=payload,
                    filename=f"{slug}-{int(time.time())}-{number}.png",
                    keep_original=True,
                    source_label="Image generation",
                    source_path=f"{made_with} · API",
                )
            except files_service.FileError as exc:
                log.warning("a generated image could not be stored: %s", exc)
                continue
            item: dict[str, Any] = {"revised_prompt": prompt}
            if response_format == "url":
                item["url"] = f"{base}/v1/files/{attachment.id}/content"
            else:
                item["b64_json"] = base64.b64encode(payload).decode("ascii")
            data.append(item)
        kept = len(data)
        # Against the source that drew them, at its own price.
        media_sources.record(
            session, user_id,
            source if not comfyui else _priced(source, float(values["per_image"])),
            images=kept,
            usage_id=service_id(plan["checkpoint"]) if comfyui else "",
        )
        session.commit()
    if not data:
        return error_response(
            502, "The images were generated but could not be saved.", "upstream_error"
        )
    from lembas.services.media import speech as media_speech

    return JSONResponse(
        {"created": int(time.time()), "data": data}, headers=media_speech.headers(used)
    )


def _priced(source, price: float):
    from dataclasses import replace

    return replace(source, price=price)


def _comfy_plan(db, body: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """What the instance's ComfyUI is asked for by this request -- or
    `{"error": response}` when it cannot be: an unknown checkpoint, none
    listed, no workflow."""
    from lembas.services.images.tool import _choices, _pick, config_of

    checkpoint, unknown = _checkpoint(str(body.get("model") or ""), values)
    if unknown:
        return {"error": error_response(
            404, f"There is no model {body.get('model')!r} here. GET /v1/models lists them.",
            "not_found_error",
        )}
    if checkpoint is None:
        return {"error": error_response(404, "No checkpoint is listed on this instance.")}
    rows, _ = _choices(db, values)
    chosen = _pick(rows, "", "", values)
    if chosen is None:
        return {"late": True, "error": error_response(
            404, "No image workflow has been set up on this instance."
        )}
    given: dict[str, Any] = {"prompt": str(body.get("prompt") or "").strip(), "model": checkpoint}
    if size := nearest_size(body.get("size")):
        given["width"], given["height"] = size
    return {
        "checkpoint": checkpoint,
        "template": json.loads(json.dumps(chosen.workflow_json or {})),
        "slug": chosen.slug,
        "given": given,
        "config": config_of(_Context(values)),
    }


class _Context:
    """What `images.tool.config_of` reads off a tool context: the settings."""

    def __init__(self, values: dict[str, Any]) -> None:
        self.image_config = values


@router.get("/v1/files/{attachment_id}/content")
async def file_content(db: Db, caller: ModelsCaller, attachment_id: str) -> Response:
    """One of the caller's own files -- a picture `/v1/images/generations` made
    -- fetched with the same token. Anybody else's is a 404, as on the web."""
    from lembas.db.models import Attachment
    from lembas.services import files as files_service

    attachment = db.get(Attachment, attachment_id)
    if attachment is None or attachment.user_id != caller.user.id:
        return error_response(404, "There is no such file.", "not_found_error")
    path = files_service.stored_path(attachment.stored_name)
    if path is None:
        return error_response(404, "That file is no longer on disk.", "not_found_error")
    return FileResponse(
        path,
        media_type=attachment.media_type if attachment.is_image else "application/octet-stream",
        headers={
            "Content-Disposition": files_service.content_disposition(
                "inline" if attachment.is_image else "attachment", attachment.filename
            ),
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["available", "nearest_size", "router", "service_id"]
