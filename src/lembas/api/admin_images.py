"""Image generation administration: the workflows a ComfyUI draws with.

A workflow is an authored document with a name, a description and a body, so
it has a page of its own; the list of them is a tab of the ComfyUI connection
they are for (`api/admin_connections.py`). The ComfyUI's address and
checkpoints are that connection's; what is instance-wide is under Admin ›
Media (`api/admin_media.py`), where `/admin/images` itself now leads.

Route order matters and is not alphabetical. `/admin/images/workflows/new` is
registered before `/admin/images/workflows/{workflow_id}`, or "new" is captured
as an id and 404s. That has already been a bug twice here.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from lembas.api.deps import AdminUser, Db
from lembas.db.models import Connection, ImageWorkflow
from lembas.services import settings_store
from lembas.services.images import importer, starters
from lembas.services.images import workflow as workflow_service
from lembas.services.llm import providers
from lembas.services.media import catalog
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/images", tags=["admin-images"])

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")

# The placeholders a workflow has to carry to be worth having. Without a prompt
# it draws the same picture whatever anybody types, which is the one failure
# somebody would not think to look for.
REQUIRED_PLACEHOLDERS = ("prompt",)


# --- Workflows -----------------------------------------------------------------
def _workflow(db: Db, workflow_id: str) -> ImageWorkflow:
    row = db.get(ImageWorkflow, workflow_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, t("That workflow no longer exists."))
    return row


def _kind(value) -> str:
    kind = str(value or "").strip()
    return kind if kind in workflow_service.KINDS else "image"


def _media_help(kind: str, text: str) -> dict:
    """What a workflow's page says about the workflow as it stands: the
    placeholders it uses, the ones its kind needs and lacks, the kind's other
    ones, and the model files it names."""
    try:
        parsed = json.loads(text or "{}")
    except json.JSONDecodeError:
        parsed = {}
    used = workflow_service.placeholders_in(parsed)
    return {
        "used": [n for n in workflow_service.ALL_PLACEHOLDERS if n in used],
        "required": list(workflow_service.REQUIRED.get(kind, ())),
        "missing": workflow_service.missing_for(kind, parsed),
        "extra": [
            (name, workflow_service.DESCRIPTIONS[name][1])
            for name in workflow_service.MEDIA_PLACEHOLDERS
            if name in workflow_service.USABLE.get(kind, ())
        ],
        "files": workflow_service.model_files(parsed),
    }


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


def _comfyuis(db: Db) -> list[Connection]:
    return list(
        db.scalars(
            select(Connection)
            .where(Connection.provider_type == providers.COMFYUI)
            .order_by(Connection.position, Connection.name)
        )
    )


def _list_of(db: Db, row: ImageWorkflow) -> str:
    """Where a workflow's list is: its ComfyUI's Workflows tab; for one that
    belongs to every ComfyUI, the first one's; with no ComfyUI at all, the
    connections."""
    connection = db.get(Connection, row.connection_id) if row.connection_id else None
    if connection is None:
        connection = next(iter(_comfyuis(db)), None)
    return (
        f"/admin/connections/{connection.id}/workflows"
        if connection is not None
        else "/admin/connections"
    )


def _detail(
    request: Request, db: Db, row: ImageWorkflow, *, is_new: bool, error: str = "", **extra
):
    text = extra.pop("workflow_text", None)
    if text is None:
        text = json.dumps(row.workflow_json or {}, indent=2)
    return render(
        request,
        "admin/workflow_detail.html",
        {
            "workflow": row,
            "is_new": is_new,
            "error": error,
            "comfyuis": _comfyuis(db),
            "back": _list_of(db, row),
            "placeholders": workflow_service.PLACEHOLDERS,
            "placeholder_help": _placeholder_help(db),
            "workflow_text": text,
            "kinds": workflow_service.KINDS,
            "kind": row.makes,
            "about": _media_help(row.makes, text),
            "starters": starters.STARTERS,
            **catalog.context(),
            **extra,
        },
    )


def _populate(row: ImageWorkflow, form) -> None:
    row.name = str(form.get("name") or "").strip()[:120]
    row.description = str(form.get("description") or "").strip()[:2000]
    row.enabled = "enabled" in form
    if "kind" in form:
        row.kind = _kind(form.get("kind"))
    # Which ComfyUI it is for; empty is every one. Absent from the post (an
    # older page) leaves it as it is.
    if "connection_id" in form:
        row.connection_id = str(form.get("connection_id") or "").strip()[:32] or None


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
    missing = workflow_service.missing_for(row.makes, parsed)
    if missing and row.makes == "image":
        return t(
            "The workflow never uses %(placeholder)s, so every image would be "
            "the same. Put it where the text prompt goes.",
            placeholder="{{" + missing[0] + "}}",
        )
    if missing:
        return t(
            "A workflow of this kind needs %(placeholder)s, and this one never uses it.",
            placeholder="{{" + missing[0] + "}}",
        )
    unknown = found - set(workflow_service.ALL_PLACEHOLDERS)
    if unknown:
        first = sorted(unknown)[0]
        return t("Unknown placeholder %(placeholder)s.", placeholder="{{" + first + "}}")

    row.workflow_json = parsed
    return ""


@router.get("/workflows/new")
async def new_workflow(
    request: Request, db: Db, user: AdminUser, connection: str = "", starter: str = "",
    kind: str = "",
) -> Response:
    """A draft, never persisted -- the `admin_tools` shape.

    Registered before `/workflows/{workflow_id}`: FastAPI matches in
    registration order, and with the parameterised route first "new" is an id.
    """
    from pathlib import Path

    chosen = starters.get(starter)
    if chosen is not None:
        # A starter: its kind, its words and its graph, switched off until
        # somebody has seen Test pass against their own ComfyUI.
        draft = ImageWorkflow(
            slug=chosen.key.replace("_", "-"),
            name=chosen.name,
            description=chosen.description,
            workflow_json=chosen.template(),
            kind=chosen.kind,
            enabled=False,
            connection_id=connection.strip()[:32] or None,
        )
        return _detail(request, db, draft, is_new=True, starter=chosen)
    base = Path(__file__).resolve().parent.parent / "services/images/base_workflow.json"
    draft = ImageWorkflow(
        slug="",
        name="",
        description="",
        workflow_json=json.loads(base.read_text(encoding="utf-8")),
        kind=_kind(kind),
        enabled=True,
        connection_id=connection.strip()[:32] or None,
    )
    return _detail(request, db, draft, is_new=True)


# --- Importing a workflow ------------------------------------------------------------
def _import_page(request: Request, db: Db, **context):
    return render(
        request,
        "admin/workflow_import.html",
        {"comfyuis": _comfyuis(db), "back": _list_of(db, ImageWorkflow(connection_id=None)),
         "text": "", "error": "", "proposal": None, "connection": "",
         **catalog.context(), **context},
    )


@router.get("/workflows/import")
async def import_workflow(
    request: Request, db: Db, user: AdminUser, connection: str = ""
) -> Response:
    return _import_page(request, db, connection=connection.strip()[:32])


async def _import_text(form) -> str:
    """The pasted text, or the uploaded file's (the file wins when both)."""
    upload = form.get("file")
    if upload is not None and hasattr(upload, "read"):
        raw = await upload.read(importer.MAX_BYTES + 1)
        if raw:
            return raw.decode("utf-8-sig", errors="replace")
    return str(form.get("workflow") or "")


@router.post("/workflows/import")
async def propose_import(request: Request, db: Db, user: AdminUser) -> Response:
    """Read the document and show what would be changed. Nothing is saved
    and nothing is rewritten: the page lists each proposed placeholder with
    a box to agree to it."""
    form = await request.form()
    text = await _import_text(form)
    connection = str(form.get("connection_id") or "").strip()[:32]
    if not text.strip():
        return _import_page(request, db, connection=connection,
                            error=t("Paste the workflow, in ComfyUI's API format."))
    try:
        doc = importer.parse(text)
    except importer.ImportError_ as problem:
        return _import_page(request, db, text=text[:20000], connection=connection,
                            error=problem.message)
    return _import_page(
        request, db, text=json.dumps(doc, indent=2), connection=connection,
        proposal=importer.propose(doc),
    )


@router.post("/workflows/import/apply")
async def apply_import(request: Request, db: Db, user: AdminUser) -> Response:
    """Make the changes that were agreed to and open the result as a new
    workflow's form -- still unsaved, for a name and a look."""
    form = await request.form()
    try:
        doc = importer.parse(str(form.get("workflow") or ""))
    except importer.ImportError_ as problem:
        return _import_page(request, db, error=problem.message)
    proposal = importer.propose(doc)
    accepted = {str(value) for value in form.getlist("accept")}
    draft = ImageWorkflow(
        slug="", name="", description="",
        workflow_json=importer.apply(doc, proposal.changes, accepted),
        kind=_kind(form.get("kind") or proposal.kind),
        enabled=False,
        connection_id=str(form.get("connection_id") or "").strip()[:32] or None,
    )
    return _detail(request, db, draft, is_new=True, imported=True)


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
        f"{_list_of(db, row)}?saved={quote(t('Added %(name)s.', name=row.name))}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/workflows/{workflow_id}/edit")
async def edit_workflow(
    request: Request, db: Db, user: AdminUser, workflow_id: str, tested: str = ""
) -> Response:
    return _detail(request, db, _workflow(db, workflow_id), is_new=False, tested=tested)


@router.post("/workflows/{workflow_id}/test")
async def test_workflow(db: Db, user: AdminUser, workflow_id: str) -> Response:
    """Run the stored workflow once, small, against its ComfyUI, and keep
    what happened on the row. The request waits for the run: this is the one
    place a generation is held open, and it is an administrator who asked."""
    from lembas.services.images import comfy
    from lembas.services.media import generate, instance

    row = _workflow(db, workflow_id)
    connection = db.get(Connection, row.connection_id) if row.connection_id else None
    if connection is None:
        connection = next((c for c in _comfyuis(db) if c.enabled), None)
    back = f"/admin/images/workflows/{row.id}/edit"
    if connection is None:
        return RedirectResponse(
            f"{back}?tested={quote(t('There is no ComfyUI connection to test it on.'))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    config = generate.config_of(instance.comfy_values(connection))
    settings = settings_store.images(db)
    template = json.loads(json.dumps(row.workflow_json or {}))
    kind = row.makes
    from lembas.services.media import store

    first = store.find(connection, store.CAP_IMAGE)
    checkpoint = first.model if first is not None else ""
    db.rollback()  # nothing is held while the ComfyUI works
    try:
        said, error = await generate.test_run(
            config, kind, template, settings, model=checkpoint
        ), ""
    except comfy.ComfyError as exc:
        said, error = "", exc.message
    row = _workflow(db, workflow_id)
    row.last_checked_at = datetime.now(UTC)
    row.last_error = error[:2000]
    db.commit()
    log.info("%s tested workflow %s: %s", user.email, row.slug, error or said)
    message = (
        t("It did not pass: %(why)s", why=error) if error
        else t("It ran on %(name)s and made: %(what)s", name=connection.name, what=said)
    )
    return RedirectResponse(
        f"{back}?tested={quote(message)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/workflows/{workflow_id}/delete")
async def delete_workflow(db: Db, user: AdminUser, workflow_id: str) -> Response:
    row = _workflow(db, workflow_id)
    name = row.name
    back = _list_of(db, row)
    db.delete(row)
    db.commit()
    log.info("%s deleted image workflow %s", user.email, name)
    return RedirectResponse(
        f"{back}?saved={quote(t('Deleted %(name)s.', name=name))}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/workflows/{workflow_id}")
async def update_workflow(request: Request, db: Db, user: AdminUser, workflow_id: str) -> Response:
    row = _workflow(db, workflow_id)
    form = await request.form()

    # Validated against a draft, so a rejected save leaves the stored row alone
    # and the form still holds what was typed.
    draft = ImageWorkflow(
        workflow_json={}, position=row.position, connection_id=row.connection_id, kind=row.kind
    )
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
    # Changed since it was last tested: what Test said is about another graph.
    row.last_checked_at = None
    row.last_error = ""
    db.commit()
    log.info("%s updated image workflow %s", user.email, row.slug)
    return RedirectResponse(
        f"{_list_of(db, row)}?saved={quote(t('Saved %(name)s.', name=row.name))}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
