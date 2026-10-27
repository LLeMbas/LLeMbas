"""The tool that makes a picture, and the loop that decides to keep it.

One call is one finished image. The alternative -- return every attempt to the
conversation and let the model decide whether to call again -- costs a full
round per retry, makes the ceiling advisory rather than enforced, and shows the
reader every reject on the way past. So the retrying happens here, and what
comes back is the image that was kept.

**Three things are ordered rather than incidental.**

*The reviewer is asked about bytes, not about a row.* An attempt that is going
to be thrown away should not leave an `Attachment` behind, so the judge is shown
a downscaled preview built in memory and only the kept image is ever written.

*Preserve VRAM swaps around the review, not around the tool.* The sequence is
unload the LLM, generate, free ComfyUI, ask the reviewer (which loads the LLM
again), and round once more if it said no. Two model loads per retry, which is
why the two settings are independent and the admin page says so.

*Nothing loads the LLM back at the end.* The reply's next request does it, and
llama-swap -- or Ollama, or anything else worth pointing this at -- loads on
demand. A step that exists in the description and not in the code looks like an
omission, so it is said here instead.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select

from lembas.services.images import comfy, workflow
from lembas.services.llm.openai_client import Endpoint, LLMError, complete
from lembas.services.tools import RISK_WRITE, ToolContext, ToolDef, ToolOutcome

log = logging.getLogger(__name__)

# What the reviewer is allowed to write back. It is one verdict and one line of
# reason, and a model that writes an essay about a picture is a model whose
# answer nobody reads.
MAX_VERDICT_TOKENS = 200

# How long to wait for a connection to admit it has unloaded. Short: this is a
# hint before a slow operation, and a machine that will not answer it is one
# where the generation should go ahead anyway rather than fail.
UNLOAD_TIMEOUT = 30.0

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        # First, and the only required one, because `tools.parse_arguments`
        # puts the whole raw string into the first required parameter when a
        # model emits arguments that are not valid JSON. That failure is common
        # with small models, and this way it degrades into a prompt rather than
        # into a seed.
        "prompt": {
            "type": "string",
            "description": "What to draw. Describe the subject, the setting and the style.",
        },
        # Every description below says what the value *does to the picture* and
        # when to move it, not what it is called. A model that is told "cfg:
        # prompt adherence, default 8" has been told nothing it can act on, and
        # the observable result is a model that sends the prompt alone and
        # leaves ten parameters at their defaults for ever.
        "negative": {
            "type": "string",
            "description": (
                "Comma-separated things to keep OUT of the picture, as plain nouns and "
                "adjectives: 'blurry, extra fingers, text, watermark'. Not a sentence, "
                "and never phrased as an instruction — 'do not add text' puts *text* in "
                "the picture. Defaults to 'text, watermark'."
            ),
        },
        "template": {
            "type": "string",
            "description": "Which workflow to use. Omit to use this chat's usual one.",
        },
        "model": {
            "type": "string",
            "description": (
                "Which checkpoint to draw with. Pick by what it is good at; omit to use "
                "this chat's usual one."
            ),
        },
        "seed": {
            "type": "integer",
            "description": (
                "Omit it, or pass -1, for a new random image. Repeat a seed you were "
                "told about to get that same image again — which is how you change one "
                "thing about a picture and keep the rest."
            ),
        },
        "steps": {
            "type": "integer",
            "description": (
                "How long to refine, 1-150. Default 20. Around 20-30 for most things; "
                "8-12 for a quick draft or when several are wanted; 40+ only for fine "
                "detail, and past about 50 it stops improving and only costs time."
            ),
        },
        "cfg": {
            "type": "number",
            "description": (
                "How literally to follow the prompt, 0-30. Default 8. 3-6 gives the "
                "model room and looks more natural; 7-9 is the usual range; 12+ forces "
                "the words through and starts to look burnt and over-saturated. Lower "
                "it if the picture looks harsh, raise it if the subject is being "
                "ignored."
            ),
        },
        "width": {
            "type": "integer",
            "description": (
                "Pixels, 64-2048, a multiple of 8. Default 512. Use the size the "
                "checkpoint was trained for — about 512 for SD1.5, about 1024 for SDXL "
                "— and change the ratio rather than the total: 512x768 for a portrait, "
                "768x512 for a landscape. Going far above what the checkpoint expects "
                "produces duplicated limbs and repeated horizons, not more detail."
            ),
        },
        "height": {
            "type": "integer",
            "description": (
                "Pixels, 64-2048, a multiple of 8. Default 512. See width: the aspect "
                "ratio is the thing to choose, and taller than wide suits a person, "
                "wider than tall suits a place."
            ),
        },
        "sampler": {
            "type": "string",
            "description": (
                "How the image is solved. Default euler. 'euler' is safe and fast; "
                "'dpmpp_2m' is a good general improvement; 'dpmpp_2m_sde' for more "
                "texture; 'ddim' for a clean flat look. Leave it out unless you have a "
                "reason."
            ),
        },
        "scheduler": {
            "type": "string",
            "description": (
                "How the steps are spaced. Default normal. 'karras' pairs well with the "
                "dpmpp samplers and usually helps at low step counts; 'normal' "
                "otherwise. Leave it out unless you are also setting the sampler."
            ),
        },
        "denoise": {
            "type": "number",
            "description": (
                "How much of the starting noise to replace, 0-1. Default 1, which is "
                "what you want for a picture drawn from nothing. Lower values only mean "
                "something for a workflow that starts from an existing image."
            ),
        },
    },
    "required": ["prompt"],
}


@dataclass(frozen=True)
class Attempt:
    """One generated image and what was decided about it."""

    number: int
    seed: int
    kept: bool
    verdict: str = ""


def config_of(context: ToolContext) -> comfy.Config:
    """The client snapshot, with the key decrypted at the last moment."""
    from lembas.services.crypto import decrypt

    values = context.image_config or {}
    return comfy.Config(
        base_url=str(values.get("base_url") or ""),
        api_key=decrypt(str(values.get("api_key_encrypted") or "")),
        timeout=float(values.get("timeout") or comfy.DEFAULT_TIMEOUT),
    )


def _choices(db, values: dict[str, Any]) -> tuple[list[Any], list[str]]:
    """The templates and checkpoints on offer, for the schema and the harness."""
    from lembas.db.models import ImageWorkflow

    rows = list(
        db.scalars(
            select(ImageWorkflow)
            .where(ImageWorkflow.enabled.is_(True))
            .order_by(ImageWorkflow.position, ImageWorkflow.slug)
        )
    )
    return rows, [str(name) for name in (values.get("checkpoints") or [])]


_DEFAULT_SENTENCE = re.compile(r"Default ([^.,]+)([.,])")


def _restate_defaults(schema: dict[str, Any], values: dict[str, Any]) -> None:
    """Rewrite each "Default 20." to say what this instance actually uses.

    Every one of those descriptions was written when there was one set of
    defaults in the world. Now an administrator can move them, and a schema
    still saying "Default 512" beside an instance that draws at 1024 is worse
    than saying nothing: the model reasons from it, decides 512 is fine for the
    SDXL checkpoint it was handed, and omits the parameter — arriving at the
    right behaviour for the wrong reason, or the wrong one silently.

    A rewrite rather than a `{default}` placeholder in the prose, because the
    sentence around it differs per parameter and half of them go on to say what
    to do *instead* of the default. The regex keeps the punctuation it found,
    since `denoise` says "Default 1, which is…" and the rest use a full stop.
    """
    resolved = workflow.resolve({}, settings=values)
    for name, spec in schema.get("properties", {}).items():
        if name not in resolved or name in ("prompt", "seed", "model", "template"):
            continue
        shown = resolved[name]
        # A float that is whole reads better as "8" than "8.0", and this is the
        # text a model reasons about.
        if isinstance(shown, float) and shown.is_integer():
            shown = int(shown)
        spec["description"] = _DEFAULT_SENTENCE.sub(
            # Bound now rather than closed over: `shown` is a loop variable, and
            # a lambda reading it later would restate every description with the
            # last parameter's value.
            lambda match, shown=shown: f"Default {shown}{match.group(2)}",
            spec["description"],
            count=1,
        )


def schema_for(db, values: dict[str, Any]) -> dict[str, Any]:
    """The parameter schema, with this instance's own choices in it.

    `template` and `model` become enums because a name that does not exist is a
    refusal from ComfyUI and a wasted round; `sampler` and `scheduler` stay
    plain strings because there are forty-four and nine of them, and an enum
    that size costs tokens on every request forever to prevent a mistake worth
    one sentence of correction.
    """
    rows, checkpoints = _choices(db, values)
    schema = json.loads(json.dumps(SCHEMA))
    _restate_defaults(schema, values)
    if rows:
        schema["properties"]["template"]["enum"] = [row.slug for row in rows]
        schema["properties"]["template"]["description"] = "Which workflow to use. " + "; ".join(
            f"{row.slug}: {row.description or row.name}" for row in rows[:12]
        )
    if checkpoints:
        schema["properties"]["model"]["enum"] = checkpoints
    return schema


def tool_def(db, values: dict[str, Any]) -> ToolDef:
    return ToolDef(
        name="image_generate",
        family="image",
        description=(
            "Draw a picture from a description and show it to the person you are "
            "talking to. Returns once the image has been made and is on screen."
        ),
        parameters=schema_for(db, values),
        run=run,
        # Not RISK_READ: it spends somebody's GPU for a minute and puts a new
        # artefact in the conversation. In an agent chat that means the mode
        # decides whether to ask first, which is the right answer for a call
        # that cannot be undone by reading something again.
        risk=RISK_WRITE,
    )


# --- Preserve VRAM -------------------------------------------------------------
async def _unload_llm(context: ToolContext) -> bool:
    """Ask this chat's own endpoint to drop its model. Best-effort.

    *This chat's own* is the whole of the design. The unload hook is a column on
    `Connection`, so a chat talking to a local llama-swap unloads that and a
    chat talking to a box on the network unloads nothing -- its VRAM is not the
    VRAM ComfyUI is about to want.
    """
    from lembas.db.models import Connection
    from lembas.db.session import session_scope

    url = ""
    method = "POST"
    try:
        with session_scope() as db:
            connection = db.get(Connection, context.connection_id)
            if connection is not None:
                url = (connection.unload_url or "").strip()
                method = (connection.unload_method or "POST").upper()
    except Exception:  # a hint that could not be looked up is not a failure
        log.debug("could not read the unload hook", exc_info=True)
        return False

    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=UNLOAD_TIMEOUT) as client:
            await client.request(method, url)
        return True
    except Exception:  # see the module docstring: a hint, not a step
        log.info("could not unload the model at %s", url, exc_info=True)
        return False


# --- The reviewer --------------------------------------------------------------
def _reviewer(context: ToolContext) -> tuple[Endpoint, str] | None:
    """The model that judges an image, or None if there is nobody to ask.

    The admin's choice first, then the chat's own model when it has vision. A
    chat on a text-only model with no reviewer configured simply keeps the first
    image, which is the behaviour with review switched off -- said here rather
    than failing, because "you asked for a picture and got an error about
    vision" is a worse answer than a picture.
    """
    from lembas.db.models import Connection, Model
    from lembas.db.session import session_scope

    values = context.image_config or {}
    if not values.get("review_enabled"):
        return None

    wanted = str(values.get("review_model_id") or "")
    try:
        with session_scope() as db:
            model = None
            # The chat's data group may name its own reviewer, because the
            # reviewer is sent the picture and the prompt that described it --
            # this group's data, going to whichever provider reviews. A group
            # that names none uses the instance's choice below.
            from lembas.services import data_groups

            group = data_groups.get(db, context.data_group)
            if group is not None and group.review_model_id:
                model = db.scalar(
                    select(Model)
                    .where(Model.model_id == group.review_model_id)
                    .order_by(
                        Model.connection_id != (group.review_connection_id or ""),
                        Model.position,
                    )
                )
            if model is None and wanted:
                # By the model's own id, and by primary key for anything stored
                # before that was the rule -- a value written by an older release
                # is a primary key and must keep working.
                model = db.scalar(
                    select(Model).where(Model.model_id == wanted).order_by(Model.position)
                ) or db.get(Model, wanted)
                if model is None:
                    # Worth a line: the fallback below quietly reviews with the
                    # chat's own model instead, which is a different picture
                    # reviewed by a different model than an administrator chose.
                    log.warning(
                        "the configured image reviewer %r no longer exists; "
                        "falling back to the chat's own model",
                        wanted,
                    )
            if model is None and context.model_id:
                model = db.scalar(
                    select(Model).where(
                        Model.model_id == context.model_id,
                        Model.connection_id == context.connection_id,
                    )
                )
            if model is None or not (model.capabilities_json or {}).get("vision"):
                return None
            connection = db.get(Connection, model.connection_id)
            if connection is None or not connection.enabled:
                return None
            return Endpoint.from_connection(connection), model.model_id
    except Exception:  # no reviewer is a degraded mode, not an error
        log.warning("could not resolve an image reviewer", exc_info=True)
        return None


async def _review(
    context: ToolContext, endpoint: Endpoint, model_id: str, prompt: str, payload: bytes
) -> tuple[bool, str]:
    """Show the reviewer the image and ask whether to keep it.

    Answers `(keep, reason)`. **Anything that goes wrong is a keep**: the
    reviewer is a second opinion on a picture that already exists, and losing an
    image because a judging request timed out would be the check destroying the
    thing it was checking.
    """
    from lembas.db.session import session_scope
    from lembas.services import files as files_service
    from lembas.services import prompts as prompts_service

    preview = files_service.preview_data_uri(payload, max_edge=768)
    if preview is None:
        return True, ""

    with session_scope() as db:
        instruction = prompts_service.resolve(db, "task.image_review")
    # An administrator who cleared the fragment has switched reviewing off, the
    # same way clearing `task.compact` switches compaction off. Nothing is asked
    # of anyone and the image is kept.
    if not instruction.strip():
        return True, ""

    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": instruction},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"The request was: {prompt}"},
                    {"type": "image_url", "image_url": {"url": preview}},
                ],
            },
        ],
        "max_tokens": MAX_VERDICT_TOKENS,
        "temperature": 0,
    }
    try:
        answer = (await complete(endpoint, body)).strip()
    except LLMError as exc:
        log.info("could not review a generated image: %s", exc.message)
        return True, ""

    verdict, _, reason = answer.partition("\n")
    keep = not verdict.strip().upper().startswith("RETRY")
    return keep, (reason or verdict).strip()[:300]


# --- The runner ----------------------------------------------------------------
def _over_quota(context: ToolContext) -> str:
    """Why this account may not draw another picture today, or "".

    Its own session, opened and closed before anything else: this runs before a
    request that takes a minute, and holding a session across one is the trade
    every long call in this codebase already refuses.
    """
    from lembas.db.models import User
    from lembas.db.session import session_scope
    from lembas.services import usage as usage_service

    if not context.owner_id:
        return ""
    with session_scope() as db:
        owner = db.get(User, context.owner_id)
        return usage_service.over_image_budget(db, owner)


async def run(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Generate one image, review it if there is anybody to ask, and keep one."""
    from lembas.db.session import session_scope
    from lembas.services import files as files_service

    event: dict[str, Any] = {
        "name": "image_generate",
        "kind": "image",
        "query": str(args.get("prompt") or "")[:200],
        "results": [],
    }

    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return ToolOutcome(
            "No prompt was given, so nothing was drawn. Say what the picture should show.",
            {**event, "status": "error", "error": "No prompt."},
        )
    if not context.chat_id:
        return ToolOutcome(
            "Images can only be generated inside a chat.",
            {**event, "status": "error", "error": "No chat."},
        )

    # Before a minute of somebody's GPU is spent. Its own quota because it is
    # its own cost: a picture is no tokens at all, so a token budget says
    # nothing about how many of them one account may make.
    over = _over_quota(context)
    if over:
        return ToolOutcome(over, {**event, "status": "error", "error": over})

    values = context.image_config or {}
    config = config_of(context)
    if not config.configured:
        return ToolOutcome(
            "No image generator is configured on this instance.",
            {**event, "status": "error", "error": "No ComfyUI configured."},
        )

    # Resolve the template and the checkpoint: what the model asked for, then
    # this chat's usual, then the instance default. Every rung is a preference
    # and none of them is a constraint, which is what lets a model that only
    # wrote a prompt still get a picture.
    try:
        with session_scope() as db:
            rows, checkpoints = _choices(db, values)
            wanted = str(args.get("template") or "")
            chosen = _pick(rows, wanted, context.image_workflow_id, values)
            if chosen is None:
                return ToolOutcome(
                    "No image workflow has been set up on this instance.",
                    {**event, "status": "error", "error": "No workflow."},
                )
            template = json.loads(json.dumps(chosen.workflow_json or {}))
            template_slug, template_name = chosen.slug, chosen.name
    except ToolOutcome:  # pragma: no cover - defensive
        raise
    except Exception as exc:
        log.exception("could not resolve an image workflow")
        return ToolOutcome(
            f"The image workflow could not be read: {exc}",
            {**event, "status": "error", "error": str(exc)},
        )

    checkpoint = _checkpoint(
        str(args.get("model") or ""),
        context.image_checkpoint,
        checkpoints,
        instance_default=str(values.get("default_checkpoint") or ""),
    )
    if checkpoint is None:
        return ToolOutcome(
            "No checkpoint is available. An administrator has to list them on the "
            "image generation page.",
            {**event, "status": "error", "error": "No checkpoint."},
        )

    given = {name: args.get(name) for name in workflow.MODEL_SETTABLE if name in args}
    given["model"] = checkpoint
    given["prompt"] = prompt

    reviewer = _reviewer(context)
    tries = int(values.get("max_tries") or 1) if reviewer else 1
    preserve = bool(values.get("preserve_vram"))

    attempts: list[Attempt] = []
    kept: tuple[bytes, dict[str, Any]] | None = None
    # What the last attempt actually asked for, so a failure can name concrete
    # numbers back at the model rather than saying "try something smaller".
    params_used: dict[str, Any] = workflow.resolve(given, settings=values)

    try:
        for number in range(1, tries + 1):
            if preserve:
                await _unload_llm(context)

            params = workflow.resolve(
                {**given, "seed": args.get("seed") if number == 1 else None}, settings=values
            )
            params_used = params
            refs = await comfy.await_images(
                config, await comfy.submit(config, workflow.fill(template, params))
            )
            if not refs:
                raise comfy.ComfyError("ComfyUI finished but saved no image.")
            payload = await comfy.fetch_image(config, refs[0])

            if preserve:
                await comfy.free(config)

            if reviewer is None:
                attempts.append(Attempt(number, params["seed"], kept=True))
                kept = (payload, params)
                break

            endpoint, model_id = reviewer
            keep, reason = await _review(context, endpoint, model_id, prompt, payload)
            last = number == tries
            attempts.append(Attempt(number, params["seed"], kept=keep or last, verdict=reason))
            if keep or last:
                kept = (payload, params)
                break
    except comfy.ComfyError as exc:
        if preserve:
            # It failed *inside* the far side, so its models are still resident
            # and the language model is still unloaded. Freeing here is what
            # lets the reply carry on and say what happened.
            await comfy.free(config)
        return ToolOutcome(
            f"The image could not be generated: {exc.message}{_advice(exc, params_used)}",
            {**event, "status": "error", "error": exc.message},
        )

    if preserve:
        await comfy.free(config)
    if kept is None:  # pragma: no cover - the loop always keeps its last attempt
        return ToolOutcome(
            "Nothing was generated.", {**event, "status": "error", "error": "No image."}
        )

    payload, params = kept
    try:
        with session_scope() as db:
            attachment = files_service.store(
                db,
                user_id=context.owner_id,
                chat_id=context.chat_id,
                payload=payload,
                filename=f"{template_slug}-{params['seed']}.png",
                # What ComfyUI made, at the size it made it. See `_keep_image`.
                keep_original=True,
                source_label="Image generation",
                source_path=f"{checkpoint} · seed {params['seed']}",
            )
            attachment_id = attachment.id
            width, height = attachment.width, attachment.height
            # Counted and priced for the chat's owner, as the reply's tokens
            # are -- once, for the image kept, not per attempt: the rejected
            # ones were this instance's own judgement, not the person's request.
            from lembas.services import pricing
            from lembas.services import usage as usage_service

            usage_service.record(
                db,
                context.owner_id,
                model_id=f"comfyui/{checkpoint}",
                cost_micro=pricing.service_cost_micro(float(values.get("per_image") or 0), 1),
                images=1,
            )
    except Exception as exc:
        log.exception("could not store a generated image")
        return ToolOutcome(
            f"The image was generated but could not be saved: {exc}",
            {**event, "status": "error", "error": str(exc)},
        )

    return ToolOutcome(
        _describe(prompt, template_name, checkpoint, params, attempts),
        {
            **event,
            "status": "ok",
            "detail": f"{template_name} · {checkpoint}",
            "text": _transcript(params, attempts),
            # Bound to the reply by `generation._persist`, the single writer. A
            # runner may create the row; only the loop may say which turn owns
            # it.
            "attachment_id": attachment_id,
            "image": {"id": attachment_id, "width": width, "height": height},
        },
    )


def _advice(exc: comfy.ComfyError, params: dict[str, Any]) -> str:
    """What to do about a failure, when there is something to do about it.

    Only for the two that have an obvious next move. Everything else gets the
    reason and nothing else -- a model told to "try again" after a broken
    workflow will try the identical thing, and a suggestion invented for a
    failure nobody understands is a guess wearing the application's authority.

    The numbers are concrete on purpose. "Use a lower resolution" against a
    request that was already 512x512 is advice that cannot be followed, so the
    halved size is worked out here where the request is known.
    """
    if isinstance(exc, comfy.Interrupted):
        return (
            " Somebody stopped it deliberately, so do not simply start it again — say so and ask."
        )
    if not isinstance(exc, comfy.OutOfMemory):
        return ""

    width, height = int(params.get("width") or 512), int(params.get("height") or 512)
    smaller = f"{max(256, width // 2)}x{max(256, height // 2)}"
    return (
        f" Try once more at a smaller size — {smaller} instead of {width}x{height} — "
        "or with a lighter checkpoint if one is offered. Do not repeat the same "
        "request unchanged; it will run out of memory again."
    )


def _pick(rows: list[Any], wanted: str, chat_default: str, values: dict[str, Any]) -> Any:
    """The workflow to use: asked for, then the chat's, then the instance's."""
    by_slug = {row.slug: row for row in rows}
    if wanted and wanted in by_slug:
        return by_slug[wanted]
    by_id = {row.id: row for row in rows}
    if chat_default and chat_default in by_id:
        return by_id[chat_default]
    fallback = str(values.get("default_workflow_id") or "")
    if fallback and fallback in by_id:
        return by_id[fallback]
    return rows[0] if rows else None


def _checkpoint(
    wanted: str, chat_default: str, available: list[str], *, instance_default: str = ""
) -> str | None:
    """The checkpoint to draw with, on the same ladder.

    Most specific first: what the model named, then this chat's own, then the
    instance default, then whatever is first in the list. The instance rung is
    the new one -- without it, "the default" was position zero in a textarea an
    administrator had typed in some order, which is a default by accident.

    A name the instance does not have is ignored at every rung rather than
    passed through: it would reach ComfyUI, be refused, and cost a round to
    discover -- and the model was shown the list it may choose from.
    """
    if wanted and wanted in available:
        return wanted
    if chat_default and chat_default in available:
        return chat_default
    if instance_default and instance_default in available:
        return instance_default
    return available[0] if available else None


def _describe(
    prompt: str, template: str, checkpoint: str, params: dict[str, Any], attempts: list[Attempt]
) -> str:
    """What the model reads back.

    It is told the image is already on screen, because otherwise the commonest
    next thing it does is offer to show it -- and there is nothing it could do
    to comply.
    """
    lines = [
        "The image has been generated and is shown to them. It is not a link and "
        "needs no further action.",
        f"Prompt: {prompt}",
        f"Template {template}, checkpoint {checkpoint}, "
        f"{params['width']}x{params['height']}, seed {params['seed']}, "
        f"{params['steps']} steps, cfg {params['cfg']}.",
    ]
    if len(attempts) > 1:
        rejected = [a for a in attempts if not a.kept]
        lines.append(
            f"It took {len(attempts)} attempts; the earlier ones were rejected on review "
            f"({'; '.join(a.verdict for a in rejected if a.verdict) or 'no reason given'})."
        )
    return "\n".join(lines)


def _transcript(params: dict[str, Any], attempts: list[Attempt]) -> str:
    """What the reader sees when they open the tool block.

    The rejected attempts are recorded here and their images are not kept. A
    transcript full of pictures somebody's model decided against is noise, and
    the disk they would occupy buys nothing -- what is worth knowing is that it
    took three goes and why the first two did not do.
    """
    lines = [
        f"seed {params['seed']} · {params['steps']} steps · cfg {params['cfg']} · "
        f"{params['sampler']}/{params['scheduler']} · denoise {params['denoise']}"
    ]
    if len(attempts) > 1:
        lines.append("")
        for attempt in attempts:
            state = "kept" if attempt.kept else "rejected"
            reason = f" — {attempt.verdict}" if attempt.verdict else ""
            lines.append(f"Attempt {attempt.number} (seed {attempt.seed}): {state}{reason}")
    return "\n".join(lines)
