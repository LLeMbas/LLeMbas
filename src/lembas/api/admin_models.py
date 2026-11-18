"""Model administration: the one list of models, and a chat model's page.

The list (`/admin/models`) is every model with its capability
(`services/media/catalog.py`): chat and embedding models, which are rows, and
speech and picture models, which their connection keeps. A line opens the
model's own page. A chat model's is here, in link tabs with addresses of
their own -- General (`/admin/models/{id}`), Limits and reasoning (`…/limits`),
Prices (`…/prices`), Capabilities (`…/capabilities`); a media model's is
`api/admin_media.py`'s. One Save per tab: the save says which tab it is
(`tab`) and only that tab's fields are written.
"""

from __future__ import annotations

import contextlib
import logging
from urllib.parse import quote, urlencode

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import AdminUser, Db, RequiredUser
from lembas.db.models import MODEL_CHAT, MODEL_EMBEDDING, MODEL_KINDS, Connection, Group, Model
from lembas.services import chat as chat_service
from lembas.services import pricing as pricing_service
from lembas.services import settings_store, uploads
from lembas.services.llm import capabilities as capabilities_service
from lembas.services.llm import providers
from lembas.services.llm.openai_client import MAX_CONTEXT
from lembas.services.media import catalog
from lembas.services.media import store as media_store
from lembas.web import i18n
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(tags=["admin-models"])

# The most "Requests at once" a model page takes: more than any one server's
# slots, few enough that a typo is not taken for a number.
MAX_PARALLEL = 256

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
        ("tool_files", i18n.t("Files")),
        ("tool_web_search", i18n.t("Web search")),
        ("tool_fetch", i18n.t("Fetch a page")),
        ("tool_memory", i18n.t("Memory")),
        ("tool_skills", i18n.t("Skills")),
        ("tool_custom", i18n.t("Custom tools")),
        ("tool_mcp", i18n.t("MCP servers")),
        ("tool_ask", i18n.t("Ask the reader")),
        ("tool_image", i18n.t("Image generation")),
        ("tool_schedule", i18n.t("Scheduling")),
        ("tool_subagent", i18n.t("Subagents")),
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
STATES = ("on", "off")
TABS = ("general", "limits", "prices", "capabilities")


@router.get("/admin/models")
async def models_page(
    request: Request,
    db: Db,
    user: AdminUser,
    saved: str = "",
    q: str = "",
    capability: str = "",
    connection: str = "",
    state: str = "",
    page: int = 1,
    filter: str = "",
):
    """The model list: one plain line per model, whatever its capability.

    Compact rows only -- everything about a model is on a page of its own. A
    connection can advertise a hundred models, and a list that renders a form
    for each of them is unusable at that size. The filters are in the address,
    so a filtered view is one that can be kept or shared.
    """
    # The older address's `filter=enabled|disabled` still means what it did.
    state = state if state in STATES else {"enabled": "on", "disabled": "off"}.get(filter, "")
    capability = capability if capability in media_store.EVERY else ""
    everything = catalog.items(db)
    matching = catalog.items(
        db, capability=capability, connection_id=connection, q=q, state=state
    )
    pages = max(1, -(-len(matching) // PAGE_SIZE))
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    counted = catalog.counts(everything)
    query = {"q": q.strip(), "capability": capability, "connection": connection, "state": state}

    return render(
        request,
        "admin/models.html",
        {
            "items": matching[start : start + PAGE_SIZE],
            "total": len(everything),
            "matched": len(matching),
            "page": page,
            "pages": pages,
            "page_start": start,
            "connections": list(db.scalars(select(Connection).order_by(Connection.name))),
            # Only the capabilities this instance has a model of are offered
            # as a filter (and the one in force, so it can be seen and left).
            "capabilities": [
                c for c in catalog.LABELS if counted.get(c) or c == capability
            ],
            "counts": counted,
            "q": q,
            "capability": capability,
            "connection_id": connection,
            "state": state,
            "filtered": bool(q.strip() or capability or connection or state),
            # Dragging puts the pickers in order: only where the whole list
            # of rows is on the page, unfiltered.
            "sortable": not (q.strip() or capability or connection or state),
            "base": "/admin/models?" + urlencode({k: v for k, v in query.items() if v}),
            "saved": saved,
            **catalog.context(),
        },
    )


def _money_prices(db: DBSession, model: Model) -> dict[str, str]:
    """The model's prices as the form shows them: money, in its connection's
    currency."""
    return {key: f"{price:.12g}" for key, price in pricing_service.prices(model).items()}


def _currency_sign(db: DBSession, model: Model | None = None) -> str:
    code = pricing_service.currency_of(db, model) if model is not None else (
        settings_store.currency(db)
    )
    return settings_store.CURRENCIES.get(code, code)


def _cap_choice(model: Model) -> dict[str, str]:
    """What each capability's select shows: the override when there is one,
    "as detected" when the endpoint said, and otherwise what the model has --
    so a value set before detection existed is kept by saving the page."""
    override = capabilities_service.clean(model.capabilities_override_json)
    detected = capabilities_service.clean(model.capabilities_detected_json)
    current = model.capabilities_json or {}
    out: dict[str, str] = {}
    for name in PROTOCOL_CAPABILITIES:
        if name in override:
            out[name] = "yes" if override[name] else "no"
        elif name in detected:
            out[name] = "auto"
        else:
            out[name] = "yes" if current.get(name) else "auto"
    return out


def _fallbacks(db: DBSession, model: Model) -> tuple[list[dict], list[dict]]:
    """The model's fallback list as the page shows it, and what may be added:
    chat models on the same data group's connections, never itself."""
    from lembas.services import data_groups

    def label(m: Model) -> str:
        return f"{m.label} · {m.connection.name}" if m.connection is not None else m.label

    # The instance's group of each connection; a person's own remapping is
    # checked again when a reply uses the list.
    group = data_groups.group_of(model.connection)
    candidates = [
        m for m in _ordered(db)
        if m.id != model.id and (m.kind or MODEL_CHAT) == MODEL_CHAT
        and data_groups.group_of(m.connection) == group
    ]
    by_id = {m.id: m for m in candidates}
    items = [
        {"id": row_id, "label": label(by_id[row_id])}
        for row_id in (model.fallback_model_ids or [])
        if row_id in by_id
    ]
    return items, [{"id": m.id, "label": label(m)} for m in candidates]


def model_tabs(model: Model) -> list[tuple[str, str, str]]:
    """`(key, address, label)` of each tab of a chat model's page. An
    embedding model answers no chat, so it has no reasoning, tools or pages
    to set: its Capabilities tab is not shown."""
    base = f"/admin/models/{model.id}"
    out = [
        ("general", base, i18n.t("General")),
        ("limits", f"{base}/limits", i18n.t("Limits and reasoning")),
        ("prices", f"{base}/prices", i18n.t("Prices")),
    ]
    if catalog.capability_of(model) != media_store.CAP_EMBEDDING:
        out.append(("capabilities", f"{base}/capabilities", i18n.t("Capabilities")))
    return out


def _tab_href(model: Model, tab: str) -> str:
    return f"/admin/models/{model.id}" + ("" if tab == "general" else f"/{tab}")


@router.get("/admin/models/{model_id}")
async def model_general(
    request: Request, db: Db, user: AdminUser, model_id: str,
    saved: str = "", detected: str = "", message: str = "",
):
    return await model_detail(request, db, user, model_id, "general", saved, detected, message)


@router.get("/admin/models/{model_id}/{tab}")
async def model_detail(
    request: Request,
    db: Db,
    user: AdminUser,
    model_id: str,
    tab: str,
    saved: str = "",
    detected: str = "",
    message: str = "",
):
    """One tab of a model's own page. `…/edit` is the page's older address
    and shows General."""
    tab = "general" if tab == "edit" else tab
    model = _model(db, model_id)
    tabs = model_tabs(model)
    if tab in TABS and tab not in {key for key, _, _ in tabs}:
        # A tab this model does not have (Previous and Next keep the tab):
        # its General.
        return RedirectResponse(f"/admin/models/{model.id}", status_code=303)
    if tab not in TABS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That page does not exist."))
    ordered = _ordered(db)
    index = next((i for i, m in enumerate(ordered) if m.id == model.id), 0)
    fallback_items, fallback_choices = _fallbacks(db, model)

    return render(
        request,
        "admin/model_detail.html",
        {
            "model": model,
            "tabs": tabs,
            "tab": tab,
            "model_capability": catalog.capability_of(model),
            # From the connection, shown and not set here: a plan's terms.
            "interactive_only": providers.interactive_only(
                model.connection.provider_type, model.connection.base_url
            ),
            **catalog.context(),
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
            "subscription": pricing_service.subscription(model),
            "currency_sign": _currency_sign(db, model),
            "detected_caps": {
                **capabilities_service.clean(model.capabilities_detected_json),
                "source": str((model.capabilities_detected_json or {}).get("source") or ""),
            },
            "override_caps": capabilities_service.clean(model.capabilities_override_json),
            "cap_choice": _cap_choice(model),
            "fallback_items": fallback_items,
            "fallback_choices": fallback_choices,
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
            # What a chat offers on this model (spec 3.2.0 levels), for the
            # default's choices.
            "model_levels": chat_service.levels_for(model),
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
            # Previous and next stay on the tab being worked through, where
            # the neighbour has it.
            "tab_suffix": "" if tab == "general" else f"/{tab}",
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
    parallel: str | None = Form(None),
    thinking_switch: bool = Form(False),
    html_pages: bool = Form(False),
    position: str = Form(""),
    context_length: str = Form(""),
    default_effort: str = Form(""),
    reasoning_efforts: list[str] = Form(default=[]),
    group_ids: list[str] = Form(default=[]),
    capability: list[str] = Form(default=[]),
    cap_reasoning: str | None = Form(None),
    cap_vision: str | None = Form(None),
    cap_tools: str | None = Form(None),
    cap_embeddings: str | None = Form(None),
    fallback_model_ids: list[str] = Form(default=[]),
    fallback_submitted: str = Form(""),
    price_input: str = Form(""),
    price_cached_input: str = Form(""),
    price_output: str = Form(""),
    price_reasoning: str = Form(""),
    price_per_request: str = Form(""),
    kind: str = Form(""),
    tab: str = Form(""),
) -> Response:
    model = _model(db, model_id)
    # One Save per tab: a post that names its tab writes that tab's fields
    # and leaves the rest of the model alone. A post that names none (an
    # older page, a script) is the whole model, as it always was.
    tab = tab if tab in TABS else ""

    def on(name: str) -> bool:
        return not tab or tab == name

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
    # A subscription's models are not priced, and the form shows no price
    # fields for them: what was stored before stays, for the day it moves.
    if on("prices") and not pricing_service.subscription(model):
        model.pricing_json = pricing
    # Chat or embedding. Absent from the post (an older form) keeps
    # what is stored rather than resetting it.
    if on("general") and kind in MODEL_KINDS:
        model.kind = kind

    if on("general"):
        model.display_name = display_name.strip()[:300]
        model.description = description.strip()[:2000]
        model.notes = notes.strip()[:2000]
        model.system_prompt = system_prompt.strip()[:8000]
    # A string, so an emptied field is distinguishable and junk can be ignored
    # rather than becoming a 422 -- the same shape `position` uses below. A
    # number typed is an override (it wins over what the endpoint says); empty
    # follows the endpoint, and with nothing detected is "unknown".
    override = capabilities_service.clean(model.capabilities_override_json)
    detected = capabilities_service.clean(model.capabilities_detected_json)
    if not on("limits"):
        pass
    elif context_length.strip():
        with contextlib.suppress(ValueError):
            typed = min(max(int(context_length), 0), MAX_CONTEXT)
            if typed and typed == detected.get("context_length"):
                override.pop("context_length", None)
            elif typed:
                override["context_length"] = typed
            else:
                override.pop("context_length", None)
                model.context_length = 0
    else:
        override.pop("context_length", None)
        if "context_length" not in detected:
            model.context_length = 0
    if on("general"):
        model.enabled = enabled
        model.pinned = pinned
        model.public = public
    # Requests at once (spec 3.2.0 `capacity.parallel`): a number, or empty for
    # "no limit anybody stated". The older "one request at a time" box is the
    # same as 1, so it is kept equal for whatever reads only that column; a
    # form that posts no `parallel` (an older one) still sets the box. FastAPI
    # reads an empty field as None too, so an emptied field lands here: with
    # the box unticked it clears a 1 and keeps any larger number.
    if not on("limits"):
        pass
    elif parallel is None:
        model.single_session = single_session
        if single_session:
            model.parallel = 1
        elif model.parallel == 1:
            model.parallel = None
    else:
        typed = None
        with contextlib.suppress(ValueError):
            typed = int(parallel.strip()) if parallel.strip() else None
        model.parallel = typed if typed is not None and 1 <= typed <= MAX_PARALLEL else None
        model.single_session = model.parallel == 1
    if on("limits"):
        model.thinking_switch = thinking_switch
    if on("capabilities"):
        model.html_pages = html_pages

    # Merged rather than rebuilt, unlike the capabilities below: params_json
    # holds whatever sampling defaults an administrator has set and this form
    # only carries one of them.
    # Which efforts this model takes at all. Submitted as a list of ticked
    # values; empty means "nobody has said", and `chat.efforts_for` answers with
    # the common three. Stored in the order `EFFORTS` declares rather than the
    # order a browser happened to send.
    chosen = [value for value in chat_service.EFFORTS if value in (reasoning_efforts or [])]
    if on("limits"):
        model.reasoning_efforts = chosen

    params = dict(model.params_json or {})
    # Another tab's save carries no default effort: the stored one is checked
    # again below, because that tab may have changed which levels there are.
    wanted = (
        default_effort.strip().lower()
        if on("limits")
        else str(params.get("reasoning_effort") or "")
    )
    # Checked against the levels this model offers, not against everything this
    # application has heard of -- a default of `high` on a model whose template
    # refuses it is a chat that fails on its first turn, and `off` exists only
    # with the thinking switch. Read after the capabilities below are saved,
    # because whether the model reasons decides its levels.
    model.params_json = params


    # Absent checkboxes are simply missing from a form post, so the submitted
    # list IS the complete new state -- rebuild rather than merge. The four
    # protocol capabilities come from their selects when the form has them
    # (yes / no override, "" follows detection); an older form's checkboxes
    # are taken as overrides, as they always were the administrator's word.
    chosen_caps = {
        "reasoning": cap_reasoning, "vision": cap_vision,
        "tools": cap_tools, "embeddings": cap_embeddings,
    }
    rebuilt = {name: (name in capability) for name in CAPABILITIES}
    for name in PROTOCOL_CAPABILITIES if on("capabilities") else ():
        choice = chosen_caps[name]
        if choice is None:
            override[name] = name in capability
        elif choice in ("yes", "no"):
            override[name] = choice == "yes"
        else:  # "auto"
            override.pop(name, None)
            # Following detection, and the endpoint said nothing: off, as an
            # unticked box always was.
            rebuilt[name] = bool(detected.get(name, False))
    if on("capabilities"):
        model.capabilities_json = rebuilt
    capabilities_service.set_override(model, override)

    params = dict(model.params_json or {})
    if wanted in chat_service.levels_for(model).offered:
        params["reasoning_effort"] = wanted
    else:
        params.pop("reasoning_effort", None)
    model.params_json = params

    # The fallback list, in the order posted; only other chat models that
    # exist (validated again when a reply uses it, `services/fallback.py`).
    if on("limits") and (fallback_submitted or fallback_model_ids):
        known = {m.id for m in db.scalars(select(Model).where(Model.id != model.id))}
        model.fallback_model_ids = list(
            dict.fromkeys(i for i in fallback_model_ids if i in known)
        )[:10]

    if not on("general"):
        pass
    elif public:
        # Group rows would be dead weight and misleading in the UI.
        model.groups = []
    else:
        model.groups = list(db.scalars(select(Group).where(Group.id.in_(group_ids or []))))

    db.commit()

    # Typing a position is the only workable way to reorder a long list; the
    # up/down buttons are for nudging a model one place.
    if on("general") and position.strip():
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
    target = _tab_href(model, tab) if tab else f"/admin/models/{model.id}/edit"
    return RedirectResponse(f"{target}?saved={quote(i18n.t('Saved.'))}", status_code=303)


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
        f"/admin/models/{model.id}/limits?detected={kind}&message={quote(message)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def local_back(back: str, fallback: str) -> str:
    """Where a switch in a list returns to: the list's own address, and
    nothing that is not a path of this administration."""
    return back if back.startswith("/admin/") and "//" not in back else fallback


@router.post("/admin/models/{model_id}/toggle")
async def toggle_model(db: Db, user: AdminUser, model_id: str, back: str = "") -> Response:
    """On or off, from a list: applies at once, and comes back to the list."""
    model = _model(db, model_id)
    model.enabled = not model.enabled
    db.commit()
    log.info("model %s switched %s by %s", model.model_id,
             "on" if model.enabled else "off", user.email)
    return RedirectResponse(local_back(back, "/admin/models"), status_code=303)


@router.post("/admin/models/{model_id}/default")
async def set_default_model(
    db: Db, user: AdminUser, model_id: str, back: str = Form("")
) -> Response:
    """Make a model the instance default for new chats."""
    model = _model(db, model_id)
    settings_store.update(db, {"default_model": model.model_id})
    log.info("default model set to %s by %s", model.model_id, user.email)
    return RedirectResponse(
        back or f"/admin/models/{model.id}?saved={quote(i18n.t('Now the default model.'))}",
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
