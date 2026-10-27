"""Image generation administration: the ComfyUI, and the workflows to run on it.

Two shapes on one nav entry, because they are two different kinds of thing. The
connection, the checkpoints and the switches are instance settings and get a
settings page. A workflow is an authored document with a name, a description and
a body, so the workflows are list-plus-detail -- the shape the working notes require
of any admin list, and for the reason it gives: a page that renders a ten-line
JSON textarea per row is unusable at three rows.

Route order matters and is not alphabetical. `/admin/images/workflows/new` is
registered before `/admin/images/workflows/{workflow_id}`, or "new" is captured
as an id and 404s. That has already been a bug twice here.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from lembas.api.deps import AdminUser, Db
from lembas.db.models import ImageWorkflow, Model
from lembas.services import settings_store
from lembas.services.crypto import UNCHANGED_SENTINEL, decrypt, keep_or_replace, mask
from lembas.services.images import comfy
from lembas.services.images import workflow as workflow_service
from lembas.services.llm.openai_client import LLMError
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/images", tags=["admin-images"])

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

# The placeholders a workflow has to carry to be worth having. Without a prompt
# it draws the same picture whatever anybody types, which is the one failure
# somebody would not think to look for.
REQUIRED_PLACEHOLDERS = ("prompt",)


def _lines(text: str) -> list[str]:
    """One name per line, blanks dropped. The `admin_agents` pattern."""
    seen: list[str] = []
    for line in (text or "").splitlines():
        name = line.strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def _number(raw: str, name: str, *, whole: bool = True) -> Any:
    """A filled box as a clamped number, an empty one as "".

    The empty string is load-bearing and is not a missing value: it is how an
    administrator says "no opinion about this one", which `workflow.resolve`
    reads as "fall through to the built-in floor". Turning it into a zero here
    would silently set every instance to zero steps.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        value = float(text)
    except ValueError:
        return ""
    low, high = workflow_service.LIMITS.get(name, (None, None))
    if low is not None:
        value = min(max(value, low), high)
    return int(value) if whole else value


def _config(db: Db) -> comfy.Config:
    values = settings_store.images(db)
    return comfy.Config(
        base_url=str(values.get("base_url") or ""),
        api_key=decrypt(str(values.get("api_key_encrypted") or "")),
        timeout=30.0,
    )


def _page(request: Request, db: Db, **extra) -> Response:
    values = settings_store.images(db)
    workflows = list(
        db.scalars(select(ImageWorkflow).order_by(ImageWorkflow.position, ImageWorkflow.slug))
    )
    return render(
        request,
        "admin/images.html",
        {
            "values": values,
            "workflows": workflows,
            # Only models an administrator has marked as having vision can
            # review, so the picker offers those and nothing else -- a list
            # including text-only models would be a list of choices that
            # silently do nothing.
            "vision_models": list(
                db.scalars(
                    select(Model)
                    .where(Model.enabled.is_(True))
                    .order_by(Model.position, Model.model_id)
                )
            ),
            "checkpoints_text": "\n".join(values.get("checkpoints") or []),
            "masked": mask(decrypt(values.get("api_key_encrypted") or "")),
            "unchanged": UNCHANGED_SENTINEL,
            "currency_sign": settings_store.CURRENCIES.get(settings_store.currency(db), ""),
            **extra,
        },
    )


def _whole(raw: str, fallback: int) -> int:
    try:
        return min(max(int(raw.strip()), 1), 10)
    except (AttributeError, ValueError):
        return int(fallback)


@router.get("")
async def images_page(request: Request, db: Db, user: AdminUser, saved: str = "") -> Response:
    return _page(request, db, saved=saved)


@router.post("")
async def save_images(
    request: Request,
    db: Db,
    user: AdminUser,
    enabled: bool = Form(False),
    base_url: str = Form(""),
    api_key: str = Form(""),
    timeout: float = Form(600.0),
    checkpoints: str = Form(""),
    default_workflow_id: str = Form(""),
    review_enabled: bool = Form(False),
    review_model_id: str = Form(""),
    max_tries: int = Form(4),
    preserve_vram: bool = Form(False),
    instructions: str = Form(""),
    # The generation defaults. Every one is a *string* even where it is a
    # number, because "" is how an administrator says "no opinion" and an
    # `int = Form(0)` cannot express that -- zero steps is a value, and one
    # somebody could mean. `_number` below turns a filled box into a clamped
    # number and an empty one back into "".
    default_checkpoint: str = Form(""),
    default_steps: str = Form(""),
    default_cfg: str = Form(""),
    default_width: str = Form(""),
    default_height: str = Form(""),
    default_sampler: str = Form(""),
    default_scheduler: str = Form(""),
    default_denoise: str = Form(""),
    default_negative: str = Form(""),
    default_batch: str = Form(""),
    per_image: str = Form(""),
    api_max_images: str = Form(""),
) -> Response:
    """Save the settings.

    Every toggle defaults to False because an unticked checkbox is simply absent
    from a form post -- that absence *is* the off signal, the rule
    `admin_audio` states.

    The discovered sampler and scheduler lists are deliberately not submitted
    and not cleared here: they belong to whatever ComfyUI was tested, and a save
    that only changed the instructions box has no opinion about them.
    """
    current = settings_store.images(db)
    settings_store.update(
        db,
        {
            "enabled": enabled,
            "base_url": base_url.strip().rstrip("/"),
            "api_key_encrypted": keep_or_replace(
                api_key, current.get("api_key_encrypted") or ""
            ),
            "timeout": min(max(timeout, 10.0), 3600.0),
            "checkpoints": _lines(checkpoints),
            "default_workflow_id": default_workflow_id.strip(),
            "review_enabled": review_enabled,
            "review_model_id": review_model_id.strip(),
            "max_tries": min(max(max_tries, 1), 10),
            "preserve_vram": preserve_vram,
            "instructions": instructions.strip()[:4000],
            # Clamped here to the same bounds `workflow.LIMITS` uses on the way
            # out. Twice, deliberately: a number stored by an earlier version,
            # or written straight into the settings row, still has to be safe
            # when a generation reads it.
            "default_checkpoint": default_checkpoint.strip(),
            "default_steps": _number(default_steps, "steps"),
            "default_cfg": _number(default_cfg, "cfg", whole=False),
            "default_width": _number(default_width, "width"),
            "default_height": _number(default_height, "height"),
            "default_sampler": default_sampler.strip(),
            "default_scheduler": default_scheduler.strip(),
            "default_denoise": _number(default_denoise, "denoise", whole=False),
            "default_negative": default_negative.strip()[:500],
            "default_batch": _number(default_batch, "batch"),
            # Money, never negative; and the API's `n`, 1-10.
            "per_image": settings_store.money_amount(per_image),
            "api_max_images": _whole(api_max_images, current.get("api_max_images") or 4),
        },
        key=settings_store.IMAGES,
    )
    log.info("image generation %s by %s", "enabled" if enabled else "disabled", user.email)
    return RedirectResponse(
        f"/admin/images?saved={quote(t('Saved.'))}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/test")
async def test_images(request: Request, db: Db, user: AdminUser) -> Response:
    """Ask ComfyUI what it can do, and remember the answer.

    Against the *saved* settings rather than the unsaved form, so what is tested
    is what a chat would actually reach -- the same rule `/admin/search/test`
    follows.

    The lists are stored rather than only shown, because the request path may
    never ask ComfyUI anything: `harness.context_variables` is synchronous and
    the tool schema is built per request, so both read what this button wrote.
    """
    config = _config(db)
    if not config.configured:
        return render(
            request,
            "admin/_images_result.html",
            {"message": t("Set a base URL first."), "message_kind": "error"},
        )
    try:
        checkpoints, samplers, schedulers = await comfy.discover(config)
    except LLMError as exc:
        return render(
            request,
            "admin/_images_result.html",
            {"message": exc.message, "message_kind": "error"},
        )

    stored = settings_store.images(db)
    changes: dict = {"samplers": samplers, "schedulers": schedulers}
    # The checkpoint list is filled in only when nobody has one yet, for the
    # reason a refreshed connection does not overwrite a context length an
    # administrator typed: they are usually narrowing it deliberately.
    if not stored.get("checkpoints"):
        changes["checkpoints"] = checkpoints
    settings_store.update(db, changes, key=settings_store.IMAGES)

    counts = {"checkpoints": len(checkpoints), "samplers": len(samplers),
              "schedulers": len(schedulers)}
    found = (
        t("Found 1 checkpoint, %(samplers)s samplers and %(schedulers)s schedulers.", **counts)
        if len(checkpoints) == 1
        else t(
            "Found %(checkpoints)s checkpoints, %(samplers)s samplers and "
            "%(schedulers)s schedulers.",
            **counts,
        )
    )
    return render(
        request,
        "admin/_images_result.html",
        {
            "message": found,
            "message_kind": "success",
            "checkpoints": checkpoints,
            "kept": bool(stored.get("checkpoints")),
        },
    )


# --- Workflows -----------------------------------------------------------------
def _workflow(db: Db, workflow_id: str) -> ImageWorkflow:
    row = db.get(ImageWorkflow, workflow_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That workflow no longer exists."))
    return row


def _placeholder_help(db: Db) -> list[tuple[str, str, str, str]]:
    """Every placeholder, what it fills, and what it resolves to *today*.

    The last column is the point. A legend listing names answers "what may I
    write"; the question somebody actually has, standing in front of a workflow
    that came out wrong, is "what happens if I leave this out" -- and the answer
    moved the day instance defaults arrived. Resolved through the same call a
    generation makes, so the two cannot disagree.
    """
    resolved = workflow_service.resolve({}, settings=settings_store.images(db))
    out: list[tuple[str, str, str, str]] = []
    for name in workflow_service.PLACEHOLDERS:
        kind, what = workflow_service.DESCRIPTIONS.get(name, ("text", ""))
        if name == "prompt":
            shown = "whatever is asked for"
        elif name == "seed":
            shown = "a fresh random one"
        elif name == "model":
            shown = str(resolved.get("model") or "") or "the first checkpoint listed"
        else:
            shown = str(resolved.get(name, ""))
        out.append((name, kind, what, shown))
    return out


def _detail(
    request: Request, db: Db, row: ImageWorkflow, *, is_new: bool, error: str = "", **extra
):
    return render(
        request,
        "admin/workflow_detail.html",
        {
            "workflow": row,
            "is_new": is_new,
            "error": error,
            "placeholders": workflow_service.PLACEHOLDERS,
            "placeholder_help": _placeholder_help(db),
            "workflow_text": extra.pop(
                "workflow_text", json.dumps(row.workflow_json or {}, indent=2)
            ),
            **extra,
        },
    )


def _populate(row: ImageWorkflow, form) -> None:
    row.name = str(form.get("name") or "").strip()[:120]
    row.description = str(form.get("description") or "").strip()[:2000]
    row.enabled = "enabled" in form


def _problem(db: Db, row: ImageWorkflow, form, *, existing_id: str = "") -> str:
    """Why this cannot be saved, or an empty string.

    A sentence rather than a 422, so a rejected save re-renders the form with
    what was typed still in it -- losing forty lines of JSON to a validation
    error is not a thing to do to somebody.
    """
    if not row.name:
        return t("A workflow needs a name.")

    slug = str(form.get("slug") or "").strip().lower()
    if not SLUG_PATTERN.match(slug):
        return t(
            "The name the model uses must be lowercase letters, digits, "
            "hyphens or underscores, and start with a letter or digit."
        )
    clash = db.scalar(select(ImageWorkflow).where(ImageWorkflow.slug == slug))
    if clash is not None and clash.id != existing_id:
        return t("There is already a workflow called “%(slug)s”.", slug=slug)
    row.slug = slug

    raw = str(form.get("workflow") or "").strip()
    if not raw:
        return t("Paste the workflow, in ComfyUI's API format.")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return t("That is not valid JSON: %(error)s", error=exc)
    if not isinstance(parsed, dict) or not parsed:
        return t(
            "A ComfyUI API workflow is a JSON object keyed by node id. Use "
            "“Export (API)” in ComfyUI rather than “Save”."
        )

    # The check worth having: a workflow with no {{prompt}} in it draws the same
    # picture whatever anybody types, and would look like a broken model rather
    # than an unparameterised template.
    found = workflow_service.placeholders_in(parsed)
    missing = [name for name in REQUIRED_PLACEHOLDERS if name not in found]
    if missing:
        return t(
            "The workflow never uses %(placeholder)s, so every image would be "
            "the same. Put it where the text prompt goes.",
            placeholder="{{" + missing[0] + "}}",
        )
    unknown = found - set(workflow_service.PLACEHOLDERS)
    if unknown:
        first = sorted(unknown)[0]
        return t("Unknown placeholder %(placeholder)s.", placeholder="{{" + first + "}}")

    row.workflow_json = parsed
    return ""


@router.get("/workflows/new")
async def new_workflow(request: Request, db: Db, user: AdminUser) -> Response:
    """A draft, never persisted -- the `admin_tools` shape.

    Registered before `/workflows/{workflow_id}`: FastAPI matches in
    registration order, and with the parameterised route first "new" is an id.
    """
    from pathlib import Path

    base = Path(__file__).resolve().parent.parent / "services/images/base_workflow.json"
    draft = ImageWorkflow(
        slug="",
        name="",
        description="",
        workflow_json=json.loads(base.read_text(encoding="utf-8")),
        enabled=True,
    )
    return _detail(request, db, draft, is_new=True)


@router.post("/workflows")
async def create_workflow(request: Request, db: Db, user: AdminUser) -> Response:
    form = await request.form()
    row = ImageWorkflow(workflow_json={})
    _populate(row, form)
    problem = _problem(db, row, form)
    if problem:
        return _detail(
            request,
            db,
            row,
            is_new=True,
            error=problem,
            workflow_text=str(form.get("workflow") or ""),
        )
    row.position = (
        db.scalar(select(func.coalesce(func.max(ImageWorkflow.position), -1))) or -1
    ) + 1
    db.add(row)
    db.commit()
    log.info("%s added image workflow %s", user.email, row.slug)
    return RedirectResponse(
        f"/admin/images?saved={quote(t('Added %(name)s.', name=row.name))}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/workflows/{workflow_id}/edit")
async def edit_workflow(request: Request, db: Db, user: AdminUser, workflow_id: str) -> Response:
    return _detail(request, db, _workflow(db, workflow_id), is_new=False)


@router.post("/workflows/{workflow_id}/delete")
async def delete_workflow(db: Db, user: AdminUser, workflow_id: str) -> Response:
    row = _workflow(db, workflow_id)
    name = row.name
    db.delete(row)
    db.commit()
    log.info("%s deleted image workflow %s", user.email, name)
    return RedirectResponse(
        f"/admin/images?saved={quote(t('Deleted %(name)s.', name=name))}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/workflows/{workflow_id}")
async def update_workflow(request: Request, db: Db, user: AdminUser, workflow_id: str) -> Response:
    row = _workflow(db, workflow_id)
    form = await request.form()

    # Validated against a draft, so a rejected save leaves the stored row alone
    # and the form still holds what was typed.
    draft = ImageWorkflow(workflow_json={}, position=row.position)
    _populate(draft, form)
    problem = _problem(db, draft, form, existing_id=row.id)
    if problem:
        draft.id = row.id
        return _detail(
            request,
            db,
            draft,
            is_new=False,
            error=problem,
            workflow_text=str(form.get("workflow") or ""),
        )

    _populate(row, form)
    row.slug = draft.slug
    row.workflow_json = draft.workflow_json
    row.last_checked_at = datetime.now(UTC)
    row.last_error = ""
    db.commit()
    log.info("%s updated image workflow %s", user.email, row.slug)
    return RedirectResponse(
        f"/admin/images?saved={quote(t('Saved %(name)s.', name=row.name))}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
