"""The tools for what is made beyond a picture: `image_edit`,
`video_generate`, `music_generate`, `model_generate`.

Beside `image_generate` (`services/images/tool.py`, unchanged in what it
takes). Each is offered only when a source for its capability resolves for
the chat (`sources.for_chat` -- the one resolution, asked again by the call
that runs, so what is offered and what runs cannot name two places), and
never to scheduled work on a connection its provider sells for a person's
own turns.

**Short ones answer; long ones start a job.** An edited picture comes back
in the call, like a drawn one. A video, a track and a 3D model take minutes:
the call writes a job (`services/media/jobs.py`), hands the work over and
answers at once with the job's id; the reply ends; the chat is woken when it
is ready.

**A failure is not an invitation.** Every refusal a model reads here ends by
telling it not to start again unless the person asks: on a live instance a
model answered a provider's error by ordering the same paid picture three
more times.

Server-only tools, like `image_generate`: they are not in the harness
specification the terminal agent shares.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select

from lembas.services.images import comfy
from lembas.services.llm.openai_client import LLMError
from lembas.services.media import generate, jobs, library, store, video
from lembas.services.media import sources as media_sources
from lembas.services.media.sources import Source
from lembas.services.tools import RISK_WRITE, ToolContext, ToolDef, ToolOutcome

log = logging.getLogger(__name__)

FAMILY = "image:media"
INPUT_MAX_BYTES = 20 * 1024 * 1024
LAST = ("", "last", "latest", "the last one", "previous")

NO_RETRY = (
    " Do not try again, with this or any other tool, unless the person asks you to: "
    "every attempt can cost them money."
)
# What a failure whose outcome nobody knows says: the request left, and no
# answer came back that says whether it was taken.
MAYBE_CHARGED = (
    " The request was sent and no answer came back that says whether it was taken: it "
    "may have been made and charged there all the same, and it cannot be followed from "
    "here. Nothing else was tried in its place. Tell the person exactly that."
)
NAMES = {
    store.CAP_IMAGE_EDIT: "image_edit",
    store.CAP_VIDEO: "video_generate",
    store.CAP_MUSIC: "music_generate",
    store.CAP_MODEL3D: "model_generate",
}
WORDS = jobs.WORDS

_PICTURE = (
    "Which picture: the file name of one the person attached or that was made in this "
    "conversation, or a path in their files. Omit it for the most recent picture in "
    "this conversation."
)
_SEED = {
    "type": "integer",
    "description": "Omit for a new random result; repeat one to get the same again.",
}
LATER = (
    " This takes minutes: the call answers at once with a job id, the result arrives "
    "later by itself, and you are told in this conversation when it is ready. After "
    "calling it, tell the person it has been started and end your reply."
)

SCHEMAS: dict[str, dict[str, Any]] = {
    store.CAP_IMAGE_EDIT: {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": (
                "What to change in the picture, as an instruction: 'make the sky a sunset', "
                "'remove the car', 'turn it into a watercolour'.")},
            "image": {"type": "string", "description": _PICTURE},
            "image2": {"type": "string", "description": (
                "A second picture to take something from, where the request involves two. "
                "Named like `image`.")},
            "negative": {"type": "string", "description": (
                "Things to keep out of the result, as plain nouns.")},
            "seed": _SEED,
        },
        "required": ["prompt"],
    },
    store.CAP_VIDEO: {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": (
                "What happens in the clip: the subject, what it does, how the camera moves, "
                "the setting, the light and the style.")},
            "image": {"type": "string", "description": (
                "A picture the clip starts from. Give it only when the person wants a "
                "picture animated. " + _PICTURE.replace("Omit it for", "Say `last` for"))},
            "seconds": {"type": "integer", "description": "How long, in seconds. Default 5."},
            "aspect": {"type": "string", "enum": list(video.RATIOS[:3]), "description": (
                "The shape: 16:9 wide (default), 9:16 tall, 1:1 square. Ignored when a "
                "picture is given: the clip takes the picture's shape.")},
            "negative": {"type": "string", "description": "Things to keep out, as plain nouns."},
            "seed": _SEED,
        },
        "required": ["prompt"],
    },
    store.CAP_MUSIC: {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": (
                "The music as tags, comma separated: genre, instruments, mood, tempo, "
                "voice. 'lo-fi hip hop, mellow piano, vinyl crackle, 80 bpm, instrumental'.")},
            "lyrics": {"type": "string", "description": (
                "The words to sing, with [verse] and [chorus] lines where wanted. Omit for "
                "an instrumental.")},
            "seconds": {"type": "integer", "description": "How long, in seconds. Default 60."},
            "seed": _SEED,
        },
        "required": ["prompt"],
    },
    store.CAP_MODEL3D: {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": (
                "The picture to make a 3D model of: one object on a plain background works "
                "best. " + _PICTURE)},
            "prompt": {"type": "string", "description": (
                "A note about the object. Most sources go by the picture alone.")},
            "seed": _SEED,
        },
        "required": [],
    },
}
DESCRIPTIONS = {
    store.CAP_IMAGE_EDIT: (
        "Change an existing picture as described and show the result to the person. "
        "Returns once the edited picture is on screen."
    ),
    store.CAP_VIDEO: "Make a short video from a description, optionally starting from a picture."
    + LATER,
    store.CAP_MUSIC: "Make a piece of music from tags and, optionally, lyrics." + LATER,
    store.CAP_MODEL3D: "Make a 3D model of the object in a picture." + LATER,
}
UNITS = {
    store.CAP_IMAGE_EDIT: "per picture", store.CAP_VIDEO: "per second of video",
    store.CAP_MUSIC: "per track", store.CAP_MODEL3D: "per model",
}


def price_hint(source: Source, instance_currency: str = "") -> str:
    """One sentence about what a use costs, when that is known."""
    if source.price > 0:
        currency = (source.currency or instance_currency or "").upper()
        return (
            f" It costs the person about {source.price:g} {currency} "
            f"{UNITS[source.capability]}."
        ).replace("  ", " ")
    if source.origin == media_sources.ORIGIN_CONNECTION:
        return " It is made by a provider and counts against the person's account there."
    return ""


def tool_def(capability: str, source: Source | None = None, currency: str = "") -> ToolDef:
    """One tool. `source` is where this chat's would be made first: it is in
    the description as a price, when there is one to say."""
    hint = price_hint(source, currency) if source is not None else ""
    return ToolDef(
        name=NAMES[capability],
        family=FAMILY,
        description=DESCRIPTIONS[capability] + hint,
        parameters=json.loads(json.dumps(SCHEMAS[capability])),
        run=RUNNERS[capability],
        risk=RISK_WRITE,
    )


def all_defs() -> list[ToolDef]:
    """Every one, ungated: for the registry, which maps a name to its family."""
    return [tool_def(capability) for capability in NAMES]


# --- What a call works with -----------------------------------------------------------
def _chain(context: ToolContext, capability: str) -> list[Source]:
    from lembas.db.models import Chat
    from lembas.db.session import session_scope
    from lembas.services.chat import Speaker

    with session_scope() as db:
        chat = db.get(Chat, context.chat_id) if context.chat_id else None
        speaker = (
            Speaker(context.model_id, context.connection_id or None) if context.model_id else None
        )
        return media_sources.for_chat(db, capability, chat, speaker)


def _settings() -> dict[str, Any]:
    from lembas.db.session import session_scope
    from lembas.services import settings_store

    with session_scope() as db:
        return settings_store.images(db)


class NoPicture(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def picture_named(context: ToolContext, name: Any) -> tuple[bytes, str]:
    """The bytes of the picture a call names, and what it is called: an
    attachment of this chat by file name (or id), a file in the tree by path
    -- read as the chat may read, nothing more -- or, with no name, the most
    recent picture in the conversation. Checked to be a picture by its own
    bytes."""
    from lembas.db.models import Attachment, Chat, User
    from lembas.db.session import session_scope
    from lembas.services import files as files_service
    from lembas.services import fs

    wanted = str(name or "").strip()
    with session_scope() as db:
        rows = list(
            db.scalars(
                select(Attachment)
                .where(
                    Attachment.chat_id == context.chat_id,
                    Attachment.user_id == context.owner_id,
                    Attachment.kind == "image",
                )
                .order_by(Attachment.created_at.desc(), Attachment.id.desc())
            )
        )
        chosen = None
        if wanted.lower() in LAST:
            chosen = rows[0] if rows else None
            if chosen is None:
                raise NoPicture(
                    "There is no picture in this conversation yet. Ask the person to attach "
                    "one, or name a file."
                )
        else:
            chosen = next(
                (r for r in rows if r.id == wanted or r.filename.lower() == wanted.lower()), None
            )
        if chosen is not None:
            path = files_service.stored_path(chosen.stored_name)
            if path is None:
                raise NoPicture(f"{chosen.filename} is no longer on disk.")
            payload, label = path.read_bytes(), chosen.filename
        else:
            user = db.get(User, context.owner_id)
            chat = db.get(Chat, context.chat_id)
            if user is None or chat is None:
                raise NoPicture("That picture could not be found.")
            try:
                view = fs.view(db, user, chat)
                node = fs.stat(view, wanted)
                if node is None or int(node.size or 0) > INPUT_MAX_BYTES:
                    raise NoPicture(
                        f"There is no picture called {wanted!r} here"
                        + (", or it is too large." if node is not None else ".")
                    )
                payload, label = fs.read(view, wanted), node.name
            except fs.FsError as exc:
                raise NoPicture(
                    f"{exc.message}. Name a picture attached in this conversation, or a "
                    "file path."
                ) from exc
    if len(payload) > INPUT_MAX_BYTES or files_service._detect_image(payload) is None:
        raise NoPicture(f"{label} is not a picture that can be used (PNG, JPEG or WebP).")
    return payload, label


def _event(capability: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": NAMES[capability],
        "kind": "image",
        "query": str(args.get("prompt") or args.get("image") or "")[:200],
        "results": [],
    }


def _fail(event: dict[str, Any], text: str, *, retry_note: bool = True) -> ToolOutcome:
    return ToolOutcome(
        text + (NO_RETRY if retry_note else ""), {**event, "status": "error", "error": text}
    )


def _seed(args: dict[str, Any]) -> int | None:
    try:
        seed = int(args["seed"]) if args.get("seed") is not None else None
    except (TypeError, ValueError):
        return None
    return seed if seed is not None and seed >= 0 else None


def _workflow(source: Source) -> dict[str, Any] | None:
    """The template of the ComfyUI workflow a source names, by slug and kind."""
    from lembas.db.models import ImageWorkflow
    from lembas.db.session import session_scope

    with session_scope() as db:
        row = db.scalar(
            select(ImageWorkflow).where(
                ImageWorkflow.slug == source.model,
                ImageWorkflow.kind == source.capability,
                ImageWorkflow.enabled.is_(True),
            )
        )
        return json.loads(json.dumps(row.workflow_json or {})) if row is not None else None


def _shape(aspect: str) -> tuple[int, int]:
    """A ComfyUI video's size for an aspect: about 480p, sides in sixteens."""
    return {"9:16": (480, 832), "1:1": (624, 624)}.get(aspect, (832, 480))


# --- image_edit: in the call ------------------------------------------------------------
async def run_edit(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from lembas.db.models import User
    from lembas.db.session import session_scope
    from lembas.services import files as files_service
    from lembas.services.images.tool import _over_quota
    from lembas.services.media import pictures as pictures_service

    capability = store.CAP_IMAGE_EDIT
    event = _event(capability, args)
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return _fail(event, "Say what to change in the picture.", retry_note=False)
    if not context.chat_id:
        return _fail(event, "A picture can only be edited inside a chat.", retry_note=False)
    if over := _over_quota(context):
        return _fail(event, over)
    try:
        chain = _chain(context, capability)
    except Exception:
        log.exception("could not resolve the sources for %s", capability)
        chain = []
    if not chain:
        return _fail(event, "Nothing is configured to edit pictures on this instance.")
    try:
        first, label = picture_named(context, args.get("image"))
        inputs = {"image": first}
        if str(args.get("image2") or "").strip():
            inputs["image2"], _ = picture_named(context, args.get("image2"))
    except NoPicture as exc:
        return _fail(event, exc.message, retry_note=False)

    settings = _settings()
    note, payload, used, seed_used = "", b"", None, None
    for source in chain[:2]:
        try:
            if source.dialect == store.COMFYUI:
                template = _workflow(source)
                if template is None:
                    raise LLMError("Its workflow no longer exists.")
                config = generate.config_of(source.config)
                given = {"prompt": prompt, "negative": args.get("negative"), "seed": _seed(args)}
                doc, params = await generate.fill(
                    config, capability, template, given, settings=settings, pictures=inputs
                )
                found = await comfy.await_outputs(config, await comfy.submit(config, doc))
                payload, _ = await generate.collect(
                    config, capability, found, limit=generate.limit_for(settings, capability)
                )
                payload = pictures_service.checked(payload)
                seed_used = params["seed"]
            else:
                payload = await pictures_service.edit(
                    source, prompt, list(inputs.values()),
                    negative=str(args.get("negative") or ""), seed=_seed(args),
                )
            used = source
            break
        except LLMError as exc:
            log.info("%s on %s failed: %s", capability, source.label, exc.message)
            if source is chain[:2][-1]:
                return _fail(event, f"The picture could not be edited: {exc.message}")
            if _stops_here(source, chain[1], exc):
                # A provider that may have made and charged it: the next
                # provider is not asked for the same picture as well.
                return _fail(
                    event, f"The picture could not be edited: {exc.message}{MAYBE_CHARGED}"
                )
            note = f"{source.label} did not do it ({exc.message[:200]}), so the default did."
    assert used is not None
    try:
        with session_scope() as db:
            stamp = uuid.uuid4().hex[:8]
            attachment = files_service.store(
                db, user_id=context.owner_id, chat_id=context.chat_id, payload=payload,
                filename=f"edit-{stamp}.png", keep_original=True,
                source_label="Image generation", source_path=used.label,
            )
            attachment_id, width, height = attachment.id, attachment.width, attachment.height
            media_sources.record(db, context.owner_id, used, images=1)
            # The picture and what it cost are kept before the copy into
            # Files is tried: that copy failing rolls back only itself.
            db.commit()
            media = None
            try:
                media = library.keep(
                    db, db.get(User, context.owner_id), kind=capability, payload=payload,
                    chat_id=context.chat_id, message_id=context.message_id,
                    width=width, height=height,
                )
            except Exception:
                db.rollback()
                log.exception("an edited picture could not be put into Files")
    except Exception as exc:
        log.exception("could not store an edited picture")
        return _fail(event, f"The picture was edited but could not be saved: {exc}")
    lines = [
        "The edited picture is shown to them now. It is not a link and needs no further "
        "action.",
        f"Edited from {label} by {used.label}, {width}x{height}."
        + (f" Seed {seed_used}." if seed_used is not None else ""),
    ]
    if media:
        lines.append(f"It is also in their Files: {media['path']}")
    if note:
        lines.append(note)
    return ToolOutcome(
        "\n".join(lines),
        {
            **event, "status": "ok", "detail": used.label,
            "text": "\n".join(p for p in (f"{width}x{height}", note) if p),
            "source": used.label, "fallback": bool(note),
            "attachment_id": attachment_id,
            "image": {"id": attachment_id, "width": width, "height": height},
            **({"media": media} if media else {}),
        },
    )


# --- The long ones: a job ---------------------------------------------------------------
async def _start(context: ToolContext, capability: str, args: dict[str, Any]) -> ToolOutcome:
    from lembas.db.session import session_scope

    event = _event(capability, args)
    what = WORDS[capability]
    prompt = str(args.get("prompt") or "").strip()
    if capability != store.CAP_MODEL3D and not prompt:
        return _fail(event, f"Describe the {what} to make.", retry_note=False)
    if not context.chat_id:
        return _fail(event, "This can only be started inside a chat.", retry_note=False)
    try:
        chain = _chain(context, capability)
    except Exception:
        log.exception("could not resolve the sources for %s", capability)
        chain = []
    if not chain:
        return _fail(event, f"Nothing is configured to make a {what} on this instance.")
    settings = _settings()
    most = int(settings.get("jobs_per_person") or 1)
    with session_scope() as db:
        running = jobs.active_count(db, context.owner_id)
    if running >= most:
        return _fail(event, _too_many(running))
    inputs: dict[str, bytes] = {}
    wants_picture = capability == store.CAP_MODEL3D or str(args.get("image") or "").strip()
    if wants_picture:
        try:
            inputs["image"], _ = picture_named(context, args.get("image"))
        except NoPicture as exc:
            return _fail(event, exc.message, retry_note=False)

    def started(source: Source, job_id: str, content: str, state: str) -> ToolOutcome:
        return ToolOutcome(
            content,
            {
                **event, "status": "ok", "detail": source.label, "source": source.label,
                "text": f"{what} · job {job_id}",
                "media_job": {"id": job_id, "kind": capability, "state": state},
            },
        )

    last_error = ""
    for source in chain[:2]:
        try:
            job_id = await _submit(context, capability, source, args, prompt, inputs, settings)
        except jobs.TooMany as exc:
            # Counted again where the job is written: another call of theirs
            # got there first while this one was preparing.
            return _fail(event, _too_many(exc.running))
        except Unconfirmed as exc:
            # Sent, and no answer: the run carries the id this server chose,
            # so it is followed like any other and found if it is there.
            # **Not** started anywhere else meanwhile.
            return started(
                source, exc.job_id,
                f"The request for the {what} was sent to {source.label}, but its answer did "
                f"not arrive ({exc.message[:200]}). It is being followed as media job "
                f"{exc.job_id}: if {source.label} took it, the result appears on its card in "
                "this conversation and in their Files, and you are told here; if it did "
                "not, you are told that instead. Tell the person so and end your reply. Do "
                "not call this again for the same request, and do not use another tool in "
                "its place: it may already be being made.",
                "queued",
            )
        except Unknown as exc:
            return _fail(event, f"The {what} could not be started: {exc.message}")
        except LLMError as exc:
            last_error = exc.message
            log.info("%s on %s could not be started: %s", capability, source.label, last_error)
            continue
        return started(
            source, job_id,
            f"Started. The {what} is being made by {source.label} as media job {job_id}. "
            "It takes a few minutes. The person sees its progress on a card in this "
            "conversation and can cancel it there; when it is ready it appears on that card "
            "and in their Files, and you are told here. Now tell them it has been started "
            "and end your reply. Do not wait for it and do not call this again for the "
            "same request.",
            "running",
        )
    return _fail(event, f"The {what} could not be started: {last_error}")


def _too_many(running: int) -> str:
    return (
        f"{running} generation(s) of theirs are still being made, which is as many as "
        "this instance runs for one person at a time. Tell them; they can wait for one "
        "to finish or cancel it on its card."
    )


def _stops_here(failed: Source, following: Source, exc: LLMError) -> bool:
    """Whether a failure on one source ends the call rather than moving it to
    the next: a provider that did not certainly refuse (`sources.refused`)
    may have made and charged it, and the next provider would be a second
    paid one. A ComfyUI is this instance's own machine and costs nobody a
    second payment, on either side."""
    if media_sources.refused(exc):
        return False
    return store.COMFYUI not in (failed.dialect, following.dialect)


class Unconfirmed(LLMError):
    """A request that was sent, got no answer, and can still be found: its
    job is kept open and followed by the id this server chose."""

    def __init__(self, job_id: str, message: str) -> None:
        super().__init__(message)
        self.job_id = job_id


class Unknown(LLMError):
    """A request that was sent, got no answer, and cannot be found again: it
    may have been taken. Never a reason to ask another source."""


async def _submit(
    context: ToolContext, capability: str, source: Source, args: dict[str, Any], prompt: str,
    inputs: dict[str, bytes], settings: dict[str, Any],
) -> str:
    """Write the job, hand the work over, confirm it. Returns the job's id.

    Raises `LLMError` when the far side **certainly** did not take it (the
    job is then `failed`, and says why) or nothing was asked of it yet: only
    then may the caller go on to another source. A request that left and got
    no answer is `Unconfirmed` (a ComfyUI: the job stays open and is looked
    for by its id) or `Unknown` (a provider: no task id, nothing to look
    for), and is never a reason to start it somewhere else."""
    from lembas.db.session import session_scope

    minutes = int(settings.get("job_minutes") or 30)
    ceiling = generate.max_seconds(settings, capability)
    default_seconds = 60 if capability == store.CAP_MUSIC else 5
    try:
        seconds = int(float(args.get("seconds") or default_seconds))
    except (TypeError, ValueError):
        seconds = default_seconds
    seconds = min(max(seconds, 1), ceiling)
    aspect = str(args.get("aspect") or "16:9")
    params: dict[str, Any] = {"prompt": prompt[:2000]}
    if capability != store.CAP_MODEL3D:
        params["seconds"] = seconds

    def opened(ref: str = "") -> str:
        # The count and the row are one step (`open_job`, `most`): what was
        # counted before a picture was uploaded is long out of date here.
        with session_scope() as db:
            return jobs.open_job(
                db, user_id=context.owner_id, chat_id=context.chat_id,
                message_id=context.message_id, kind=capability, source=source, params=params,
                minutes=minutes, ref=ref, most=int(settings.get("jobs_per_person") or 1),
            ).id

    if source.dialect == store.COMFYUI:
        template = _workflow(source)
        if template is None:
            raise LLMError("Its workflow no longer exists.")
        from lembas.services.images import workflow

        if inputs and "image" not in workflow.placeholders_in(template):
            # Asked to start from a picture, on a workflow that takes none:
            # said, rather than making something the picture is not in.
            raise LLMError(
                f"{source.label} makes this from a description only and cannot start "
                "from a picture."
            )
        config = generate.config_of(source.config)
        given: dict[str, Any] = {
            "prompt": prompt, "negative": args.get("negative"), "seed": _seed(args),
            "seconds": seconds, "lyrics": args.get("lyrics"),
        }
        if capability == store.CAP_VIDEO:
            given["width"], given["height"] = _shape(aspect)
            params["width"], params["height"] = given["width"], given["height"]
        doc, filled = await generate.fill(
            config, capability, template, given, settings=settings, pictures=inputs
        )
        params["seed"] = filled["seed"]
        ref = str(uuid.uuid4())
        job_id = opened(ref)
        try:
            answered = await comfy.submit(config, doc, prompt_id=ref)
        except LLMError as exc:
            if media_sources.refused(exc):
                jobs.refuse(job_id, exc.message)
                raise
            # No answer, or one that could not be read: the ComfyUI may have
            # queued it under the id it was given. The job stays open and the
            # worker asks `/history` and `/queue` for that id.
            jobs.poke()
            raise Unconfirmed(job_id, exc.message) from exc
        jobs.confirm(job_id, answered)
        return job_id
    if source.dialect == store.DASHSCOPE_VIDEO:
        seconds = video.seconds_for(source, seconds, ceiling)
        resolution = video.resolution_for(source, str(args.get("resolution") or ""))
        params.update({"seconds": seconds, "resolution": resolution, "aspect": aspect})
        body = video.body_for(
            source, prompt, picture=inputs.get("image"), seconds=seconds,
            resolution=resolution, ratio=aspect, negative=str(args.get("negative") or ""),
            seed=_seed(args),
        )
        job_id = opened()
        try:
            task_id = await video.submit(source, body)
        except LLMError as exc:
            if media_sources.refused(exc):
                jobs.refuse(job_id, exc.message)
                raise
            # No task id came back, so there is nothing to ask about later:
            # said as it is, and nothing is asked of anybody else.
            said = f"{exc.message}{MAYBE_CHARGED}"
            jobs.refuse(job_id, said)
            raise Unknown(said) from exc
        jobs.confirm(job_id, task_id)
        return job_id
    raise LLMError("This source cannot make that.")


async def run_video(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    return await _start(context, store.CAP_VIDEO, args)


async def run_music(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    return await _start(context, store.CAP_MUSIC, args)


async def run_model(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    return await _start(context, store.CAP_MODEL3D, args)


RUNNERS = {
    store.CAP_IMAGE_EDIT: run_edit,
    store.CAP_VIDEO: run_video,
    store.CAP_MUSIC: run_music,
    store.CAP_MODEL3D: run_model,
}

__all__ = ["FAMILY", "NAMES", "NO_RETRY", "all_defs", "picture_named", "price_hint", "tool_def"]
