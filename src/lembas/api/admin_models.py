"""Model administration: ordering, defaults, images, access and capabilities."""

from __future__ import annotations

import contextlib
import logging
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import AdminUser, Db, RequiredUser
from lembas.db.models import MODEL_CHAT, MODEL_EMBEDDING, MODEL_KINDS, Connection, Group, Model
from lembas.services import chat as chat_service
from lembas.services import pricing as pricing_service
from lembas.services import settings_store, uploads
from lembas.services.llm.openai_client import MAX_CONTEXT
from lembas.web import i18n
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(tags=["admin-models"])

# What the endpoint can do. Endpoints do not advertise any of this reliably, so
# these are an administrator's assertion.
# `embeddings` is the odd one out and is worth naming as such: the other three
# say what a model can do in a *chat*, and this one says it is not for chatting
# at all. It is what /admin/extraction picks from, and nothing else reads it.
PROTOCOL_CAPABILITIES = ("reasoning", "vision", "tools", "embeddings")

# Which tools this model is given. Distinct from the above: `tools` is whether a
# tools array may be sent at all, these are what goes in it. Every one of them is
# meaningless unless `tools` is on.
#
# The last two are gates rather than single tools: one covers every custom HTTP
# tool an administrator has defined, the other every MCP server. Which of those
# a particular person gets is the tool's own group list, not a flag here -- a
# server can advertise forty tools, and a model page listing all of them is a
# page nobody can read.
#
# A function rather than a constant because the labels are read in the language
# of whoever is looking, which a module-level value cannot know.
def tool_capabilities() -> tuple[tuple[str, str], ...]:
    return (
        ("tool_web_search", i18n.t("Web search")),
        ("tool_fetch", i18n.t("Fetch a page")),
        ("tool_knowledge", i18n.t("Knowledge")),
        ("tool_notes", i18n.t("Notes")),
        ("tool_memory", i18n.t("Memory")),
        ("tool_skills", i18n.t("Skills")),
        ("tool_custom", i18n.t("Custom tools")),
        ("tool_mcp", i18n.t("MCP servers")),
        ("tool_ask", i18n.t("Ask the reader")),
        ("tool_report", i18n.t("Reports")),
        ("tool_image", i18n.t("Image generation")),
        ("tool_scratch", i18n.t("Canvas")),
        ("tool_schedule", i18n.t("Scheduling")),
        ("tool_subagent", i18n.t("Helpers")),
    )


CAPABILITIES = PROTOCOL_CAPABILITIES + tuple(key for key, _ in tool_capabilities())


def _model(db: DBSession, model_id: str) -> Model:
    model = db.get(Model, model_id)
    if model is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That model no longer exists."))
    return model


def _ordered(db: DBSession) -> list[Model]:
    return list(
        db.scalars(
            select(Model).join(Connection).order_by(Model.position, Model.model_id)
        )
    )


def _renumber(db: DBSession) -> None:
    """Rewrite positions to 0..n-1.

    Keeps the numbers dense so a move is always a swap with a neighbour, and
    stops repeated reordering drifting into large sparse values.
    """
    for index, model in enumerate(_ordered(db)):
        model.position = index
    db.commit()


# --- Listing -----------------------------------------------------------------
PAGE_SIZE = 40

# Filters offered as tabs above the list. Each is a predicate over a Model.
FILTERS: dict[str, object] = {
    "all": lambda m: True,
    "enabled": lambda m: m.enabled,
    "disabled": lambda m: not m.enabled,
    "pinned": lambda m: m.pinned,
    "restricted": lambda m: not m.public,
}


def _filter_labels() -> dict[str, str]:
    """The tabs' names, in the reader's language (hence not in FILTERS)."""
    return {
        "all": i18n.t("All"),
        "enabled": i18n.t("Enabled"),
        "disabled": i18n.t("Disabled"),
        "pinned": i18n.t("Pinned"),
        "restricted": i18n.t("Restricted"),
    }


@router.get("/admin/models")
async def models_page(
    request: Request,
    db: Db,
    user: AdminUser,
    saved: str = "",
    q: str = "",
    filter: str = "all",
    connection: str = "",
    page: int = 1,
):
    """The model list.

    Compact rows only -- editing happens on a page of its own. A connection can
    advertise a hundred models, and a list that renders a full form for each of
    them is unusable at that size.
    """
    everything = _ordered(db)

    predicate = FILTERS.get(filter, FILTERS["all"])
    needle = q.strip().lower()

    matching = [
        model
        for model in everything
        if predicate(model)
        and (not connection or model.connection_id == connection)
        and (
            not needle
            or needle in model.model_id.lower()
            or needle in (model.display_name or "").lower()
        )
    ]

    pages = max(1, -(-len(matching) // PAGE_SIZE))
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    visible = matching[start : start + PAGE_SIZE]

    return render(
        request,
        "admin/models.html",
        {
            "models": visible,
            "total": len(everything),
            "matched": len(matching),
            "page": page,
            "pages": pages,
            "page_start": start,
            "connections": list(db.scalars(select(Connection).order_by(Connection.name))),
            "default_model": settings_store.get(db, "default_model") or "",
            "counts": {
                key: sum(1 for m in everything if test(m)) for key, test in FILTERS.items()
            },
            "filters": _filter_labels(),
            "active_filter": filter if filter in FILTERS else "all",
            "q": q,
            "connection_id": connection,
            "saved": saved,
        },
    )


def _money_prices(db: DBSession, model: Model) -> dict[str, str]:
    """The model's prices as the form shows them: money, in the instance's
    currency."""
    return {key: f"{price:.12g}" for key, price in pricing_service.prices(model).items()}


def _currency_sign(db: DBSession) -> str:
    return settings_store.CURRENCIES.get(settings_store.currency(db), "")


@router.get("/admin/models/{model_id}/edit")
async def model_detail(
    request: Request,
    db: Db,
    user: AdminUser,
    model_id: str,
    saved: str = "",
    detected: str = "",
    message: str = "",
):
    """Everything about one model, on its own page."""
    model = _model(db, model_id)
    ordered = _ordered(db)
    index = next((i for i, m in enumerate(ordered) if m.id == model.id), 0)

    return render(
        request,
        "admin/model_detail.html",
        {
            "model": model,
            "price_fields": (
                ("input", i18n.t("Input"), i18n.t("Per million prompt tokens.")),
                (
                    "cached_input",
                    i18n.t("Cached input"),
                    i18n.t("Per million the server had cached; the input price when empty."),
                ),
                ("output", i18n.t("Output"), i18n.t("Per million tokens written.")),
                (
                    "reasoning",
                    i18n.t("Reasoning"),
                    i18n.t("Per million tokens of thinking; the output price when empty."),
                ),
                (
                    "per_request",
                    i18n.t("Per reply"),
                    i18n.t("Added to every reply, whatever its length."),
                ),
            ),
            "price_values": _money_prices(db, model),
            "currency_sign": _currency_sign(db),
            "model_kinds": (
                (MODEL_CHAT, i18n.t("Chat")),
                (MODEL_EMBEDDING, i18n.t("Embedding")),
            ),
            "groups": list(db.scalars(select(Group).order_by(Group.name))),
            "capabilities": PROTOCOL_CAPABILITIES,
            "tool_capabilities": tool_capabilities(),
            # Every effort this application understands, so an administrator
            # can tick the ones their model actually takes -- and the model's
            # current answer, which is the common three until somebody says.
            "efforts": chat_service.EFFORTS,
            "model_efforts": chat_service.efforts_for(model),
            # What `detect-efforts` found, if it has just run. Escaped by the
            # template like every other value; it is prose the endpoint or this
            # application wrote, not markup.
            "detected": detected if detected in ("success", "warning") else "",
            "detected_message": message[:400],
            # Rows predating the split have no tool_* keys at all. Showing them
            # unticked would be a lie: tools.enabled_tools treats absent as on
            # when `tools` is on, so that an upgrade does not silently take web
            # search away from every model already configured for it.
            "tool_default": bool((model.capabilities_json or {}).get("tools")),
            "default_model": settings_store.get(db, "default_model") or "",
            "instance_prompt": settings_store.get(db, "system_prompt") or "",
            "position_of": index + 1,
            "total": len(ordered),
            "previous": ordered[index - 1] if index > 0 else None,
            "next": ordered[index + 1] if index + 1 < len(ordered) else None,
            "saved": saved,
        },
    )


# Registered BEFORE /{model_id}: FastAPI matches in registration order, so
# with the parameterised route first, "bulk" is captured as a model id and
# the handler 404s on a model that does not exist.
@router.post("/admin/models/bulk")
async def bulk_models(
    db: Db, user: AdminUser, action: str = Form(...), model_ids: list[str] = Form(default=[])
) -> Response:
    """Enable or disable several models at once.

    A freshly refreshed connection can advertise dozens of models; turning them
    off one at a time is not a reasonable way to spend an afternoon.
    """
    models = list(db.scalars(select(Model).where(Model.id.in_(model_ids or []))))
    for model in models:
        if action == "enable":
            model.enabled = True
        elif action == "disable":
            model.enabled = False
        elif action == "public":
            model.public = True
            model.groups = []
        elif action == "private":
            model.public = False
    db.commit()
    _renumber(db)
    return RedirectResponse(
        f"/admin/models?saved={quote(i18n.t('%(n)s model(s) updated.', n=len(models)))}",
        status_code=303,
    )


@router.post("/admin/models/{model_id}")
async def update_model(
    db: Db,
    user: AdminUser,
    model_id: str,
    display_name: str = Form(""),
    description: str = Form(""),
    notes: str = Form(""),
    system_prompt: str = Form(""),
    enabled: bool = Form(False),
    pinned: bool = Form(False),
    public: bool = Form(False),
    single_session: bool = Form(False),
    position: str = Form(""),
    context_length: str = Form(""),
    default_effort: str = Form(""),
    reasoning_efforts: list[str] = Form(default=[]),
    group_ids: list[str] = Form(default=[]),
    capability: list[str] = Form(default=[]),
    price_input: str = Form(""),
    price_cached_input: str = Form(""),
    price_output: str = Form(""),
    price_reasoning: str = Form(""),
    price_per_request: str = Form(""),
    kind: str = Form(""),
) -> Response:
    model = _model(db, model_id)

    # Money per million tokens (per reply for per_request), in the instance's
    # currency. Junk and zero are absent, so an empty form is a free model
    # rather than a 422.
    pricing: dict[str, float | str] = {}
    for key, raw in (
        ("input", price_input),
        ("cached_input", price_cached_input),
        ("output", price_output),
        ("reasoning", price_reasoning),
        ("per_request", price_per_request),
    ):
        with contextlib.suppress(ValueError):
            value = float(raw.strip()) if raw.strip() else 0.0
            if 0 < value < 1e9:
                pricing[key] = value
    model.pricing_json = pricing
    # Chat or embedding. Absent from the post (an older form) keeps
    # what is stored rather than resetting it.
    if kind in MODEL_KINDS:
        model.kind = kind

    model.display_name = display_name.strip()[:300]
    model.description = description.strip()[:2000]
    model.notes = notes.strip()[:2000]
    model.system_prompt = system_prompt.strip()[:8000]
    # A string, so an emptied field is distinguishable and junk can be ignored
    # rather than becoming a 422 -- the same shape `position` uses below.
    if context_length.strip():
        with contextlib.suppress(ValueError):
            model.context_length = min(max(int(context_length), 0), MAX_CONTEXT)
    else:
        model.context_length = 0
    model.enabled = enabled
    model.pinned = pinned
    model.public = public
    model.single_session = single_session

    # Merged rather than rebuilt, unlike the capabilities below: params_json
    # holds whatever sampling defaults an administrator has set and this form
    # only carries one of them.
    # Which efforts this model takes at all. Submitted as a list of ticked
    # values; empty means "nobody has said", and `chat.efforts_for` answers with
    # the common three. Stored in the order `EFFORTS` declares rather than the
    # order a browser happened to send.
    chosen = [value for value in chat_service.EFFORTS if value in (reasoning_efforts or [])]
    model.reasoning_efforts = chosen

    params = dict(model.params_json or {})
    wanted = default_effort.strip().lower()
    # Checked against what this model takes, not against everything this
    # application has heard of -- a default of `high` on a model whose template
    # refuses it is a chat that fails on its first turn.
    if wanted in chat_service.efforts_for(model):
        params["reasoning_effort"] = wanted
    else:
        params.pop("reasoning_effort", None)
    model.params_json = params

    # Absent checkboxes are simply missing from a form post, so the submitted
    # list IS the complete new state -- rebuild rather than merge.
    model.capabilities_json = {name: (name in capability) for name in CAPABILITIES}

    if public:
        # Group rows would be dead weight and misleading in the UI.
        model.groups = []
    else:
        model.groups = list(db.scalars(select(Group).where(Group.id.in_(group_ids or []))))

    db.commit()

    # Typing a position is the only workable way to reorder a long list; the
    # up/down buttons are for nudging a model one place.
    if position.strip():
        try:
            wanted = max(1, int(position)) - 1
        except ValueError:
            wanted = None
        if wanted is not None:
            ordered = [m for m in _ordered(db) if m.id != model.id]
            ordered.insert(min(wanted, len(ordered)), model)
            for index, item in enumerate(ordered):
                item.position = index
            db.commit()

    log.info("model %s updated by %s", model.model_id, user.email)
    return RedirectResponse(
        f"/admin/models/{model.id}/edit?saved={quote(i18n.t('Saved.'))}", status_code=303
    )


@router.post("/admin/models/{model_id}/move")
async def move_model(
    db: Db,
    user: AdminUser,
    model_id: str,
    direction: str = Form(...),
    back: str = Form(""),
) -> Response:
    """Swap a model with its neighbour."""
    model = _model(db, model_id)
    ordered = _ordered(db)
    index = next((i for i, m in enumerate(ordered) if m.id == model.id), None)

    if index is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That model no longer exists."))

    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(ordered):
        ordered[index], ordered[target] = ordered[target], ordered[index]
        for position, item in enumerate(ordered):
            item.position = position
        db.commit()

    # Back to whichever filtered, paginated view the button was pressed on.
    return RedirectResponse(back or "/admin/models", status_code=303)


@router.post("/admin/models/{model_id}/detect-efforts")
async def detect_efforts(db: Db, user: AdminUser, model_id: str) -> Response:
    """Ask the endpoint which reasoning efforts this model actually takes.

    llama-server hands its loaded model's Jinja chat template over on `/props`,
    and that template is the thing that rejects an effort it does not know -- so
    the accepted set is written down in the one place that is authoritative,
    rather than having to be guessed at or discovered by a failed reply.

    Anything that is not a llama-server answers nothing here, and that is a
    normal outcome: OpenAI and vLLM have no such route, and their models are
    documented rather than introspectable. The result then says so instead of
    claiming the model accepts nothing.
    """
    from lembas.services.llm.openai_client import Endpoint, fetch_chat_template

    model = _model(db, model_id)
    connection = db.get(Connection, model.connection_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That connection no longer exists."))

    template = await fetch_chat_template(Endpoint.from_connection(connection))
    found = chat_service.efforts_from_chat_template(template)

    if found:
        model.reasoning_efforts = found
        db.commit()
        message = i18n.t("This model's template accepts: %(efforts)s.", efforts=", ".join(found))
        kind = "success"
    elif template:
        message = i18n.t(
            "The endpoint gave up its chat template, but nothing in it names a "
            "set of reasoning efforts. Either this model does not take one, or "
            "it accepts anything and never checks."
        )
        kind = "warning"
    else:
        message = i18n.t(
            "This endpoint does not publish its chat template, so there is "
            "nothing to read. llama.cpp does; OpenAI and vLLM do not."
        )
        kind = "warning"

    return RedirectResponse(
        f"/admin/models/{model.id}/edit?detected={kind}&message={quote(message)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/models/{model_id}/default")
async def set_default_model(
    db: Db, user: AdminUser, model_id: str, back: str = Form("")
) -> Response:
    """Make a model the instance default for new chats."""
    model = _model(db, model_id)
    settings_store.update(db, {"default_model": model.model_id})
    log.info("default model set to %s by %s", model.model_id, user.email)
    return RedirectResponse(
        back or f"/admin/models/{model.id}/edit?saved={quote(i18n.t('Now the default model.'))}",
        status_code=303,
    )


@router.post("/admin/models/{model_id}/image")
async def upload_model_image(
    db: Db, user: AdminUser, model_id: str, image: UploadFile = File(...)
) -> Response:
    model = _model(db, model_id)
    payload = await image.read()

    try:
        filename = uploads.save_model_image(payload, image.content_type or "")
    except uploads.UploadError as exc:
        return RedirectResponse(
            f"/admin/models/{model.id}/edit?saved={quote(str(exc))}", status_code=303
        )

    # Remove the old file rather than orphaning it in the uploads directory.
    if model.image_path:
        uploads.delete_model_image(model.image_path)

    model.image_path = filename
    db.commit()
    return RedirectResponse(
        f"/admin/models/{model.id}/edit?saved={quote(i18n.t('Image updated.'))}",
        status_code=303,
    )


@router.post("/admin/models/{model_id}/image/delete")
async def delete_model_image(db: Db, user: AdminUser, model_id: str) -> Response:
    model = _model(db, model_id)
    if model.image_path:
        uploads.delete_model_image(model.image_path)
        model.image_path = ""
        db.commit()
    return RedirectResponse(
        f"/admin/models/{model.id}/edit?saved={quote(i18n.t('Image removed.'))}",
        status_code=303,
    )


# --- Serving model images ----------------------------------------------------
@router.get("/uploads/models/{filename}")
async def model_image(user: RequiredUser, filename: str) -> Response:
    """Serve a stored model avatar.

    Behind the auth guard: these are instance assets, not public files, and
    the path resolution in uploads refuses anything outside the directory.
    """
    path = uploads.model_image_path(filename)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("No such image."))
    return FileResponse(
        path,
        media_type=uploads.media_type_for(filename),
        # Filenames are random and content-addressed in practice, so a long
        # cache is safe: a new image gets a new name.
        headers={"Cache-Control": "private, max-age=604800"},
    )
