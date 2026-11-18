"""Long generations as jobs: started, followed, finished, told.

A video, a track or a 3D model takes minutes. The tool that asks for one
(`services/media/tools.py`) therefore does not hold its reply open: it writes
a `MediaJob`, hands the work to the far side, and answers at once. This
module is what happens afterwards.

## The queue is the database

What is still to be followed is every row whose state is `queued` or
`running` -- not a list in memory. The worker reads them at each pass, so a
restart, a crash, or a job started while no worker ran loses nothing: the row
names the far side's own id (`provider_ref`: a ComfyUI prompt id, a
provider's task id) and the next pass asks about it again. Nothing is ever
*started* again by a restart; a row that was written but whose request may or
may not have reached the far side (`queued` with nothing confirmed, and old)
is given up on in words rather than repeated -- a second paid generation
nobody asked for is the worse mistake.

## One pass

For each active job, one at a time: is its deadline over; was it asked to be
cancelled; what does the far side say. **Commit before awaiting**: every
state is written and committed before the next thing that can take long, and
no session is held across a request. A finished job's file is fetched under
the kind's byte cap, checked to be what was asked for by its own bytes, and
written into the person's Files (`library.keep`) in the same transaction
that marks the job `done` and counts its price.

## Being told

When a job ends by itself -- done, failed, expired -- the chat that asked is
**woken** with a note (`wake.wake_chat`), exactly as a finished background
helper wakes its parent: a `machine` turn, and the model gets one reply to
present the result. `notified_at` is written first; a wake that then cannot
be delivered (the chat was deleted, the instance serves the API only) loses
nothing: the file is in Files and the job is in the list. A job the person
cancelled wakes nobody.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from lembas.db.models import (
    MEDIA_ACTIVE,
    MEDIA_CANCELLED,
    MEDIA_DONE,
    MEDIA_EXPIRED,
    MEDIA_FAILED,
    MEDIA_FINAL,
    MEDIA_QUEUED,
    MEDIA_RUNNING,
    Connection,
    MediaJob,
    User,
)
from lembas.db.session import session_scope
from lembas.services.images import comfy
from lembas.services.llm.openai_client import Endpoint, LLMError
from lembas.services.media import generate, library, store, video
from lembas.services.media.sources import ORIGIN_CONNECTION, Source

log = logging.getLogger(__name__)

# How often each kind of far side is asked. A ComfyUI is usually on the same
# network; a provider asks not to be asked more often than this.
POLL_SECONDS = {store.COMFYUI: 2.0, store.DASHSCOPE_VIDEO: 10.0}
IDLE_SECONDS = 30.0
# A `queued` row older than this was never confirmed by the far side.
UNCONFIRMED_SECONDS = 240.0
# How long after its end an untold job is still told at a start.
TELL_WITHIN = timedelta(hours=24)

WORDS = {
    store.CAP_VIDEO: "video", store.CAP_MUSIC: "track", store.CAP_MODEL3D: "3D model",
    store.CAP_IMAGE: "picture", store.CAP_IMAGE_EDIT: "edited picture",
}


@dataclass
class State:
    task: asyncio.Task | None = None
    wake: asyncio.Event | None = None
    next_at: dict[str, float] | None = None


_STATE = State()


def now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --- Rows ----------------------------------------------------------------------
def active_count(db, user_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(MediaJob)
            .where(MediaJob.user_id == user_id, MediaJob.state.in_(MEDIA_ACTIVE))
        )
        or 0
    )


def open_job(
    db,
    *,
    user_id: str,
    chat_id: str | None,
    message_id: str | None,
    kind: str,
    source: Source,
    params: dict[str, Any],
    minutes: int,
    ref: str = "",
) -> MediaJob:
    """Write a job as `queued` and commit it: it exists before anything is
    asked of the far side. `ref` is the id this server chose for the run,
    where the far side takes one (a ComfyUI)."""
    from lembas.services import pricing

    started = now()
    job = MediaJob(
        user_id=user_id,
        chat_id=chat_id or None,
        message_id=message_id or None,
        kind=kind,
        state=MEDIA_QUEUED,
        connection_id=source.connection_id,
        dialect=source.dialect,
        model=source.model,
        label=source.label[:300],
        provider_ref=ref,
        params_json=params,
        price_micro=pricing.service_cost_micro(source.price, 1),
        currency=source.currency,
        usage_id=source.usage_id[:300],
        started_at=started,
        deadline_at=started + timedelta(minutes=max(1, int(minutes))),
    )
    db.add(job)
    db.commit()
    return job


def confirm(job_id: str, ref: str) -> None:
    """The far side took it: `running`, with the far side's id for it."""
    with session_scope() as db:
        job = db.get(MediaJob, job_id)
        if job is not None and job.state == MEDIA_QUEUED:
            job.state = MEDIA_RUNNING
            job.provider_ref = ref[:200]
            db.commit()
    poke()


def refuse(job_id: str, error: str) -> None:
    """The far side would not take it: `failed`, and nobody is woken -- the
    tool call that asked is still there to say so."""
    with session_scope() as db:
        job = db.get(MediaJob, job_id)
        if job is not None and job.state in MEDIA_ACTIVE:
            job.state, job.error = MEDIA_FAILED, error[:1000]
            job.ended_at = job.notified_at = now()
            db.commit()


def seconds_run(job: MediaJob) -> int:
    end = _utc(job.ended_at) or now()
    return max(0, int((end - (_utc(job.started_at) or end)).total_seconds()))


def view(job: MediaJob) -> dict[str, Any]:
    """A job as every door shows it (`/api/v2/media/jobs`, the card)."""
    from lembas.services import pricing

    params = job.params_json or {}
    return {
        "id": job.id,
        "kind": job.kind,
        "state": job.state,
        "active": job.state in MEDIA_ACTIVE,
        "chat_id": job.chat_id or None,
        "message_id": job.message_id or None,
        "source": job.label,
        "prompt": str(params.get("prompt") or "")[:500],
        "started": _utc(job.started_at).isoformat() if job.started_at else None,
        "ended": _utc(job.ended_at).isoformat() if job.ended_at else None,
        "deadline": _utc(job.deadline_at).isoformat() if job.deadline_at else None,
        "seconds": seconds_run(job),
        "error": job.error or "",
        "result": dict(job.result_json) if job.state == MEDIA_DONE and job.result_json else None,
        "cost": (
            {"amount": pricing.micro_to_money(job.cost_micro), "currency": job.currency or ""}
            if job.cost_micro
            else None
        ),
        "told": job.notified_at is not None,
    }


def listing(db, user_id: str, *, chat_id: str = "", active: bool = False, limit: int = 50) -> list:
    query = select(MediaJob).where(MediaJob.user_id == user_id)
    if chat_id:
        query = query.where(MediaJob.chat_id == chat_id)
    if active:
        query = query.where(MediaJob.state.in_(MEDIA_ACTIVE))
    return list(db.scalars(query.order_by(MediaJob.started_at.desc()).limit(limit)))


def owned(db, job_id: str, user_id: str) -> MediaJob | None:
    job = db.get(MediaJob, job_id)
    return job if job is not None and job.user_id == user_id else None


# --- The far side ----------------------------------------------------------------
@dataclass(frozen=True)
class Far:
    """What is needed to ask about one job, with no session behind it."""

    job_id: str
    kind: str
    dialect: str
    ref: str
    config: comfy.Config | None = None
    source: Source | None = None
    limit: int = 0


def _far(db, job: MediaJob) -> Far | None:
    from lembas.services import settings_store
    from lembas.services.media import instance

    connection = db.get(Connection, job.connection_id) if job.connection_id else None
    if connection is None:
        return None
    limit = generate.limit_for(settings_store.images(db), job.kind)
    if job.dialect == store.COMFYUI:
        return Far(job.id, job.kind, job.dialect, job.provider_ref,
                   config=generate.config_of(instance.comfy_values(connection)), limit=limit)
    source = Source(
        capability=job.kind, origin=ORIGIN_CONNECTION, own=False, label=job.label,
        model=job.model, dialect=job.dialect, endpoint=Endpoint.from_connection(connection),
        connection_id=connection.id, provider=connection.provider_name,
    )
    return Far(job.id, job.kind, job.dialect, job.provider_ref, source=source, limit=limit)


async def _stop_far(far: Far) -> str:
    """Ask the far side to stop one job; what it did, in words. Never raises."""
    try:
        if far.dialect == store.COMFYUI and far.config is not None and far.ref:
            where = await comfy.cancel(far.config, far.ref)
            return {
                comfy.QUEUE_RUNNING: "It was interrupted on the ComfyUI.",
                comfy.QUEUE_PENDING: "It was taken out of the ComfyUI's queue before it began.",
            }.get(where, "It was no longer in the ComfyUI's queue.")
        if far.dialect == store.DASHSCOPE_VIDEO and far.source is not None and far.ref:
            if await video.cancel(far.source, far.ref):
                return "The provider cancelled it before it began."
            return (
                "The provider had already begun and does not stop a task that runs: it is "
                "no longer waited for here, and may still be finished and charged there."
            )
    except Exception as exc:  # a failed stop must not keep a job alive
        log.info("could not stop media job %s on the far side: %s", far.job_id, exc)
        return "The far side could not be told to stop; it is no longer waited for here."
    return ""


async def cancel(job_id: str, user_id: str) -> dict[str, Any] | None:
    """The person stops their job. Returns its view, or None when it is not
    theirs. A job that has ended stays what it was."""
    with session_scope() as db:
        job = owned(db, job_id, user_id)
        if job is None:
            return None
        if job.state not in MEDIA_ACTIVE:
            return view(job)
        job.cancel_requested = True
        db.commit()
        far = _far(db, job)
    said = await _stop_far(far) if far is not None else ""
    with session_scope() as db:
        job = db.get(MediaJob, job_id)
        if job is None:
            return None
        if job.state in MEDIA_ACTIVE:
            job.state = MEDIA_CANCELLED
            job.error = said
            # Cancelled by the person: there is nobody to tell.
            job.ended_at = job.notified_at = now()
            db.commit()
        return view(job)


# --- One job, one step ---------------------------------------------------------------
def _end(job_id: str, state: str, error: str) -> bool:
    """A job ends without a result. True when this call ended it."""
    with session_scope() as db:
        job = db.get(MediaJob, job_id)
        if job is None or job.state not in MEDIA_ACTIVE:
            return False
        if job.cancel_requested:
            state = MEDIA_CANCELLED
        job.state, job.error, job.ended_at = state, error[:1000], now()
        if state == MEDIA_CANCELLED:
            job.notified_at = job.ended_at
        db.commit()
        return state != MEDIA_CANCELLED


def _finish(job_id: str, payload: bytes, hint: str, *, seconds: float = 0.0) -> bool:
    """Store the file, count it, mark the job `done` -- one transaction.
    True when this call finished it (it may have been cancelled meanwhile)."""
    from lembas.services import pricing
    from lembas.services import usage as usage_service

    with session_scope() as db:
        job = db.get(MediaJob, job_id)
        if job is None or job.state not in MEDIA_ACTIVE or job.cancel_requested:
            return False
        user = db.get(User, job.user_id)
        if user is None:
            job.state, job.error, job.ended_at = MEDIA_FAILED, "The account is gone.", now()
            job.notified_at = job.ended_at
            db.commit()
            return False
        params = job.params_json or {}
        length = 0.0
        if job.kind != store.CAP_MODEL3D:
            length = float(seconds or params.get("seconds") or 0)
        try:
            result = library.keep(
                db, user, kind=job.kind, payload=payload, hint=hint, chat_id=job.chat_id,
                message_id=job.message_id, width=int(params.get("width") or 0),
                height=int(params.get("height") or 0), seconds=length,
            )
        except Exception as exc:
            db.rollback()
            job = db.get(MediaJob, job_id)
            message = getattr(exc, "message", "") or str(exc)
            job.state, job.ended_at = MEDIA_FAILED, now()
            job.error = f"It was made, but could not be kept: {message}"[:1000]
            db.commit()
            return True
        # A video is priced by the second, everything else by the piece.
        units = max(length, 1.0) if job.kind == store.CAP_VIDEO else 1.0
        job.cost_micro = pricing.service_cost_micro(pricing.micro_to_money(job.price_micro), units)
        usage_service.record(
            db, job.user_id, model_id=job.usage_id, cost_micro=job.cost_micro,
            currency=job.currency,
        )
        job.result_json = result
        job.state, job.ended_at = MEDIA_DONE, now()
        db.commit()
        return True


async def _advance(job_id: str) -> None:
    """One look at one job. Never raises."""
    with session_scope() as db:
        job = db.get(MediaJob, job_id)
        if job is None or job.state not in MEDIA_ACTIVE:
            return
        far = _far(db, job)
        state = job.state
        age = (now() - (_utc(job.started_at) or now())).total_seconds()
        overdue = now() > (_utc(job.deadline_at) or now())
        cancelling = job.cancel_requested
    told = False
    try:
        if far is None:
            told = _end(job_id, MEDIA_FAILED, "Its connection no longer exists.")
        elif cancelling:
            # Asked to stop while this process was not there to do it.
            said = await _stop_far(far)
            _end(job_id, MEDIA_CANCELLED, said)
        elif overdue:
            said = await _stop_far(far)
            told = _end(
                job_id, MEDIA_EXPIRED,
                f"It was not finished in the time this instance allows. {said}".strip(),
            )
        elif state == MEDIA_QUEUED and not far.ref:
            if age > UNCONFIRMED_SECONDS:
                told = _end(
                    job_id, MEDIA_FAILED,
                    "The server was interrupted before the far side confirmed the request. "
                    "It may or may not have been started there; it is not waited for.",
                )
        elif far.dialect == store.COMFYUI:
            told = await _advance_comfy(far, state, age)
        else:
            told = await _advance_video(far)
    except LLMError as exc:
        # The far side could not be asked just now: the next pass asks again,
        # until the deadline says otherwise.
        log.info("media job %s: %s", job_id, exc.message)
    except Exception:
        log.exception("media job %s could not be followed", job_id)
    if told:
        await _tell(job_id)


async def _advance_comfy(far: Far, state: str, age: float) -> bool:
    assert far.config is not None
    try:
        found = await comfy.history_once(far.config, far.ref)
    except comfy.Interrupted as exc:
        return _end(far.job_id, MEDIA_FAILED, f"{exc.message} Somebody stopped it there.")
    except (comfy.OutOfMemory, comfy.ComfyError) as exc:
        if getattr(exc, "status_code", None) or getattr(exc, "transport", False):
            raise  # not an answer about the run: ask again
        return _end(far.job_id, MEDIA_FAILED, exc.message)
    if found is None:
        if state == MEDIA_QUEUED:
            where = await comfy.queue_state(far.config, far.ref)
            if where:
                confirm(far.job_id, far.ref)
            elif age > UNCONFIRMED_SECONDS:
                return _end(
                    far.job_id, MEDIA_FAILED,
                    "The ComfyUI does not know this run: the request did not reach it.",
                )
        return False
    try:
        payload, hint = await generate.collect(far.config, far.kind, found, limit=far.limit)
    except comfy.ComfyError as exc:
        if getattr(exc, "transport", False):
            raise
        return _end(far.job_id, MEDIA_FAILED, exc.message)
    return _finish(far.job_id, payload, hint)


async def _advance_video(far: Far) -> bool:
    from lembas.services.media import dashscope

    assert far.source is not None
    task = await video.poll(far.source, far.ref)
    if task.state == video.RUNNING:
        confirm(far.job_id, far.ref)
        return False
    if task.state == video.DONE:
        try:
            # The link is the provider's and lasts a day: fetched now, capped,
            # and never handed on.
            payload = await dashscope.collect(task.url, limit=far.limit)
        except LLMError as exc:
            return _end(far.job_id, MEDIA_FAILED, exc.message)
        return _finish(far.job_id, payload, "mp4", seconds=task.seconds)
    if task.state == video.CANCELLED:
        return _end(far.job_id, MEDIA_FAILED, "The provider cancelled it.")
    if task.state == video.GONE:
        return _end(far.job_id, MEDIA_EXPIRED, task.error)
    return _end(far.job_id, MEDIA_FAILED, task.error)


# --- Telling the chat ----------------------------------------------------------------
def note(job: MediaJob) -> str:
    """What the chat's model reads when a job ends: prose that names itself
    (`wake.py`: a machine turn's framing lives in its words)."""
    what = WORDS.get(job.kind, job.kind)
    if job.state == MEDIA_DONE:
        result = job.result_json or {}
        facts = [str(result.get("path") or "")]
        if result.get("seconds"):
            facts.append(f"{result['seconds']:g} s")
        if result.get("width") and result.get("height"):
            facts.append(f"{result['width']}x{result['height']}")
        return (
            f"[Media job {job.id} has finished. The {what} you started with {job.label} is "
            f"ready: {', '.join(f for f in facts if f)}. It is already shown to the person "
            "in the conversation, on the card of the call that started it, and it is saved "
            "in their Files. Tell them briefly that it is ready. Do not generate it again, "
            "and do not start another generation unless they ask for one.]"
        )
    why = job.error or "No reason was given."
    return (
        f"[Media job {job.id} did not finish ({job.state}). The {what} you started with "
        f"{job.label} was not made: {why} Tell the person what happened, in a sentence. "
        "Do not start it again, with this or any other tool, unless they ask you to: "
        "a generation can cost them money each time.]"
    )


async def _tell(job_id: str) -> None:
    from lembas.services import wake as wake_service

    with session_scope() as db:
        job = db.get(MediaJob, job_id)
        if job is None or job.notified_at is not None or job.state not in MEDIA_FINAL:
            return
        # Written first: a wake that is not delivered loses a note, never the
        # result; one delivered twice would be a second reply nobody asked for.
        job.notified_at = now()
        chat_id, text = job.chat_id, note(job)
        db.commit()
    if chat_id:
        await wake_service.wake_chat(chat_id, text)


# --- The worker ----------------------------------------------------------------------
def _due() -> list[tuple[str, str]]:
    with session_scope() as db:
        return [
            (row.id, row.dialect)
            for row in db.scalars(
                select(MediaJob)
                .where(MediaJob.state.in_(MEDIA_ACTIVE))
                .order_by(MediaJob.started_at)
            )
        ]


def _untold() -> list[str]:
    since = now() - TELL_WITHIN
    with session_scope() as db:
        return [
            row.id
            for row in db.scalars(
                select(MediaJob).where(
                    MediaJob.state.in_((MEDIA_DONE, MEDIA_FAILED, MEDIA_EXPIRED)),
                    MediaJob.notified_at.is_(None),
                    MediaJob.ended_at > since,
                )
            )
        ]


async def tick(*, everything: bool = False) -> int:
    """One pass over the active jobs, one at a time. Returns how many there
    are. `everything`: ask about each now, whatever its own interval says."""
    due = _due()
    clock = time.monotonic()
    waits = _STATE.next_at if _STATE.next_at is not None else {}
    _STATE.next_at = waits
    for job_id, dialect in due:
        if not everything and waits.get(job_id, 0.0) > clock:
            continue
        waits[job_id] = clock + POLL_SECONDS.get(dialect, 5.0)
        await _advance(job_id)
    for gone in set(waits) - {job_id for job_id, _ in due}:
        waits.pop(gone, None)
    return len(due)


async def reconcile() -> None:
    """At a start: tell the chats of jobs that ended while nobody could be
    told, then look at everything still active."""
    for job_id in _untold():
        await _tell(job_id)
    await tick(everything=True)


async def _loop() -> None:
    try:
        await reconcile()
    except Exception:
        log.exception("could not reconcile the media jobs")
    while True:
        try:
            active = await tick()
        except Exception:
            log.exception("the media job pass failed")
            active = 0
        assert _STATE.wake is not None
        _STATE.wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_STATE.wake.wait(), 1.0 if active else IDLE_SECONDS)


def poke() -> None:
    """Something changed: do not wait out the idle sleep."""
    if _STATE.wake is not None:
        _STATE.wake.set()


def start() -> None:
    """Start the worker in the running loop. Idempotent."""
    if _STATE.task is not None and not _STATE.task.done():
        return
    _STATE.wake = asyncio.Event()
    _STATE.next_at = {}
    _STATE.task = asyncio.get_running_loop().create_task(_loop(), name="media-jobs")


async def shutdown() -> None:
    task, _STATE.task = _STATE.task, None
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


__all__ = [
    "active_count", "cancel", "confirm", "listing", "note", "open_job", "owned", "poke",
    "reconcile", "refuse", "shutdown", "start", "tick", "view",
]
