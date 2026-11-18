"""Administration: the list of connections, and one page per connection.

A connection is anything this instance reaches at an address: a provider of
chat models, a ComfyUI, a speech server (`services/llm/providers.py`). The list
is one line each; a line opens the connection's own page, whose tabs are links
with addresses of their own:

| tab | address | what it holds |
|---|---|---|
| Overview | `/admin/connections/{id}` | type, address, key, the type's options, Test |
| Models | `…/models` | its models -- every capability -- and refresh |
| Media | `…/media` | the two "use its own" switches and which media models are on |
| Workflows | `…/workflows` | a ComfyUI's workflows and its default one |
| Remove | `…/remove` | deleting it |

Saving the Overview, testing, reading a balance and deleting are
`api/admin.py`'s routes, as they were; registered there, so `/connections/new`
below has to be -- and is -- a GET, which none of those are.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from lembas.api import admin as admin_api
from lembas.api.deps import AdminUser, Db
from lembas.db.models import Connection, ImageWorkflow
from lembas.services import settings_store
from lembas.services.llm import providers
from lembas.services.media import catalog, store
from lembas.services.media import sources as media_sources
from lembas.web import i18n
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/connections", tags=["admin-connections"])

PAGE_SIZE = 50


def _back(connection: Connection, tab: str, word: str = "", *, error: bool = False) -> Response:
    target = f"/admin/connections/{connection.id}" + (f"/{tab}" if tab else "")
    if word:
        target += f"?{'error' if error else 'saved'}={quote(word)}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def tabs_for(connection: Connection) -> list[tuple[str, str, str]]:
    """`(key, address, label)` of each tab this connection has. Only what
    applies: Media where the type can carry media beside chat, Workflows on a
    ComfyUI; Remove always, and always last."""
    base = f"/admin/connections/{connection.id}"
    type_id = providers.get(connection.provider_type).id
    out = [("overview", base, i18n.t("Overview")), ("models", f"{base}/models", i18n.t("Models"))]
    if providers.serves_chat(type_id) and store.supported(type_id, connection.base_url):
        out.append(("media", f"{base}/media", i18n.t("Media")))
    if type_id == providers.COMFYUI:
        out.append(("workflows", f"{base}/workflows", i18n.t("Workflows")))
    out.append(("remove", f"{base}/remove", i18n.t("Remove")))
    return out


def _page(request: Request, db, connection: Connection, tab: str, **extra) -> Response:
    tabs = tabs_for(connection)
    if tab not in {key for key, _, _ in tabs}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That page does not exist."))
    return render(
        request,
        "admin/connection_detail.html",
        {
            "connection": connection,
            "row": admin_api._row_context(db, connection),
            "tabs": tabs,
            "tab": tab,
            "offers": catalog.offers(connection),
            "defaults_naming": media_sources.defaults_naming(db, connection.id),
            **catalog.context(),
            **extra,
        },
    )


@router.get("")
async def connections_page(request: Request, db: Db, user: AdminUser, message: str = ""):
    connections = admin_api._connections(db)
    return render(
        request,
        "admin/connections.html",
        {
            "connections": connections,
            "offers": {c.id: catalog.offers(c) for c in connections},
            "model_counts": {
                c.id: sum(1 for m in c.models if m.enabled)
                + len(store.entries(c, enabled=True))
                for c in connections
            },
            "message": message,
            **catalog.context(),
        },
    )


# What the type choice shows for each kind of connection, in its order. The
# generic type first (most instances start with a server of their own), the
# instance's own equipment last.
TYPE_ORDER = (
    providers.OPENAI_COMPATIBLE, providers.OPENAI, providers.ANTHROPIC, providers.DEEPSEEK,
    providers.OPENROUTER, providers.ALIBABA, providers.ALIBABA_PLAN, providers.SPEECH,
    providers.COMFYUI,
)


def _type_notes() -> dict[str, str]:
    return {
        providers.OPENAI_COMPATIBLE: i18n.t(
            "A server of your own, or any cloud that speaks OpenAI's API. Chat and "
            "embedding models; speech and pictures too where it has them."
        ),
        providers.OPENAI: i18n.t("Chat models, and its speech and picture models."),
        providers.ANTHROPIC: i18n.t("Chat models."),
        providers.DEEPSEEK: i18n.t("Chat models, with a balance to read."),
        providers.OPENROUTER: i18n.t("Many providers' chat models behind one key."),
        providers.ALIBABA: i18n.t("Qwen and more, paid as used: chat, speech and picture models."),
        providers.ALIBABA_PLAN: i18n.t("A subscription, for a person's own turns only."),
        providers.SPEECH: i18n.t(
            "A server that transcribes and reads aloud, such as whisper.cpp or Kokoro. "
            "No chat models."
        ),
        providers.COMFYUI: i18n.t(
            "Pictures drawn with your own workflows and checkpoints. No chat models."
        ),
    }


@router.get("/new")
async def new_connection(request: Request, db: Db, user: AdminUser, type: str = ""):
    """Adding a connection starts with what it is. `?type=` is the form for
    that type; without it, the choice."""
    chosen = type.strip().lower()
    return render(
        request,
        "admin/connection_new.html",
        {
            "chosen": providers.TYPES.get(chosen),
            "type_order": [providers.TYPES[key] for key in TYPE_ORDER if key in providers.TYPES],
            "type_notes": _type_notes(),
            **admin_api.form_context(db),
            **catalog.context(),
        },
    )


@router.get("/{connection_id}")
async def connection_overview(
    request: Request, db: Db, user: AdminUser, connection_id: str, saved: str = ""
):
    connection = admin_api._connection(db, connection_id)
    return _page(request, db, connection, "overview", saved=saved)


@router.get("/{connection_id}/models")
async def connection_models(
    request: Request,
    db: Db,
    user: AdminUser,
    connection_id: str,
    saved: str = "",
    error: str = "",
    page: int = 1,
):
    connection = admin_api._connection(db, connection_id)
    found = catalog.items(db, connection_id=connection.id)
    pages = max(1, -(-len(found) // PAGE_SIZE))
    page = max(1, min(page, pages))
    return _page(
        request, db, connection, "models",
        saved=saved, error=error,
        items=found[(page - 1) * PAGE_SIZE : page * PAGE_SIZE],
        matched=len(found), page=page, pages=pages,
        # The capabilities a model can be added under by hand here.
        addable=list(store.capabilities_of(connection.provider_type, connection.base_url)),
        offered=[
            entry for entry in store.offers(connection.provider_type, connection.base_url)
            if store.get_entry(connection, entry.capability, entry.model) is None
        ],
    )


@router.post("/{connection_id}/models/refresh")
async def refresh_models(db: Db, user: AdminUser, connection_id: str) -> Response:
    """Ask the provider what it has, as Test does, and come back to the list."""
    connection = admin_api._connection(db, connection_id)
    message, error = await admin_api.refresh(db, connection)
    return _back(connection, "models", message, error=error)


@router.post("/{connection_id}/models/add")
async def add_model(request: Request, db: Db, user: AdminUser, connection_id: str) -> Response:
    """A media model typed by hand: a provider's model id, a checkpoint's
    file name. Added switched on, and opened, so its options can be set."""
    connection = admin_api._connection(db, connection_id)
    form = await request.form()
    capability = str(form.get("capability") or "").strip().lower()
    model = str(form.get("model") or "").strip()
    if capability not in store.capabilities_of(connection.provider_type, connection.base_url):
        return _back(
            connection, "models",
            i18n.t("This connection cannot have a model of that kind."), error=True,
        )
    offer = next(
        (e for e in store.offers(connection.provider_type, connection.base_url)
         if (e.capability, e.model) == (capability, model)),
        None,
    )
    entry = store._entry(
        {"capability": capability, "model": model, "enabled": True,
         "voice": offer.voice if offer else ""}
    )
    if entry is None:
        return _back(connection, "models", i18n.t("Type the model's id."), error=True)
    if store.get_entry(connection, entry.capability, entry.model) is not None:
        return _back(connection, "models", i18n.t("That model is already here."), error=True)
    if len(store.entries(connection)) >= store.MAX_MODELS:
        return _back(
            connection, "models",
            i18n.t("This connection has as many models as it can keep."), error=True,
        )
    connection.media_json = store.with_entry(connection, entry)
    db.commit()
    return RedirectResponse(
        catalog.media_href(connection.id, entry.capability, entry.model),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{connection_id}/media")
async def connection_media(
    request: Request, db: Db, user: AdminUser, connection_id: str, saved: str = ""
):
    connection = admin_api._connection(db, connection_id)
    return _page(
        request, db, connection, "media", saved=saved,
        media=store.of(connection),
        media_items=[
            item for item in catalog.items(db, connection_id=connection.id) if item.media
        ],
    )


@router.post("/{connection_id}/media")
async def save_connection_media(
    request: Request, db: Db, user: AdminUser, connection_id: str
) -> Response:
    """The two switches, and which of its media models are on. A model's own
    options and price are on the model's page."""
    connection = admin_api._connection(db, connection_id)
    form = await request.form()
    ticked = {str(value) for value in form.getlist("enabled")}
    media = store.of(connection)
    media["prefer_audio"] = bool(form.get("prefer_audio"))
    media["prefer_image"] = bool(form.get("prefer_image"))
    media["models"] = [
        {**item, "enabled": catalog.media_key(item["capability"], item["model"]) in ticked}
        for item in media["models"]
    ]
    connection.media_json = store.normalise(media)
    db.commit()
    return _back(connection, "media", i18n.t("Saved."))


@router.get("/{connection_id}/workflows")
async def connection_workflows(
    request: Request, db: Db, user: AdminUser, connection_id: str, saved: str = ""
):
    connection = admin_api._connection(db, connection_id)
    workflows = list(
        db.scalars(
            select(ImageWorkflow)
            .where(
                (ImageWorkflow.connection_id == connection.id)
                | ImageWorkflow.connection_id.is_(None)
                | (ImageWorkflow.connection_id == "")
            )
            .order_by(ImageWorkflow.position, ImageWorkflow.slug)
        )
    )
    return _page(request, db, connection, "workflows", saved=saved, workflows=workflows)


@router.post("/{connection_id}/workflows")
async def save_connection_workflows(
    request: Request, db: Db, user: AdminUser, connection_id: str
) -> Response:
    """Which workflow this ComfyUI draws with when nothing names another."""
    connection = admin_api._connection(db, connection_id)
    if providers.get(connection.provider_type).id != providers.COMFYUI:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That page does not exist."))
    form = await request.form()
    chosen = str(form.get("default_workflow_id") or "").strip()
    known = chosen and db.get(ImageWorkflow, chosen) is not None
    connection.options_json = {
        **(connection.options_json or {}), "default_workflow_id": chosen if known else ""
    }
    db.commit()
    return _back(connection, "workflows", i18n.t("Saved."))


@router.get("/{connection_id}/remove")
async def connection_remove(request: Request, db: Db, user: AdminUser, connection_id: str):
    connection = admin_api._connection(db, connection_id)
    return _page(
        request, db, connection, "remove",
        chat_models=len(connection.models),
        media_models=len(store.entries(connection)),
        currency=settings_store.currency(db),
    )


__all__ = ["router", "tabs_for"]
