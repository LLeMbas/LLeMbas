"""Administration: Media, and the page of one media model.

**Media** (`/admin/media/{capability}`) holds what is not a connection's and
not a model's: per capability -- speech to text, text to speech, pictures --
whether it is on at all, which added model is the default, the order sources
are tried in, and the instance-wide options (the review of generated pictures,
the limits, the instructions). No address and no key: those are connections
(`api/admin_connections.py`), and what a model is asked with is on its page.

**A media model's page** (`/admin/models/media/{connection}/{key}`): a model
kept on its connection (`services/media/store.py`), addressed by
`catalog.media_key`. General, Options and Remove are link tabs; Test and
"make default" are on General.

The old pages' addresses, `/admin/audio` and `/admin/images`, redirect here.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from lembas.api.deps import AdminUser, Db
from lembas.db.models import Connection, Model
from lembas.services import audio as audio_service
from lembas.services import settings_store
from lembas.services.images import workflow as workflow_service
from lembas.services.llm import providers
from lembas.services.llm.openai_client import Endpoint, LLMError
from lembas.services.media import catalog, instance, probe, store
from lembas.services.media import sources as media_sources
from lembas.web import i18n
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(tags=["admin-media"])

# Every capability has a tab: the first three, and the generations beyond a
# picture.
CAPABILITIES = store.ALL


def _see(target: str, word: str = "", *, error: bool = False) -> Response:
    if word:
        target += ("&" if "?" in target else "?") + f"{'error' if error else 'saved'}={quote(word)}"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


# --- Media -------------------------------------------------------------------------
@router.get("/admin/audio")
async def old_audio_page(user: AdminUser) -> Response:
    return _see("/admin/media/stt")


@router.get("/admin/images")
async def old_images_page(user: AdminUser) -> Response:
    return _see("/admin/media/image")


@router.get("/admin/media")
async def media_home(user: AdminUser) -> Response:
    return _see("/admin/media/stt")


def _values(db, capability: str) -> dict[str, Any]:
    return (
        settings_store.images(db) if capability in store.GENERATIONS
        else settings_store.audio(db)
    )


def default_choices(db, capability: str) -> list[dict[str, str]]:
    """What the default can be: every added, enabled model of the capability,
    and for a ComfyUI also "no fixed checkpoint" -- the chat's own, or the
    first in its list -- which is what an instance that never chose one has."""
    out = media_sources.choices(db, capability)
    if capability == store.CAP_IMAGE:
        seen: list[str] = []
        for choice in list(out):
            connection = db.get(Connection, choice["connection_id"])
            if (
                connection is None
                or providers.get(connection.provider_type).id != providers.COMFYUI
                or connection.id in seen
            ):
                continue
            seen.append(connection.id)
            out.append(
                {
                    "value": f"{connection.id}/",
                    "label": i18n.t(
                        "%(name)s · the chat's checkpoint, or its first", name=connection.name
                    ),
                    "connection_id": connection.id,
                    "model": "",
                }
            )
    return out


def _own_sources(db, capability: str) -> list[Connection]:
    """The connections whose chats use their own model for this capability."""
    return [
        connection
        for connection in db.scalars(
            select(Connection).where(Connection.enabled.is_(True)).order_by(Connection.position)
        )
        if not store.equipment(connection.provider_type)
        and store.supported(connection.provider_type, connection.base_url)
        and store.own(connection, capability) is not None
    ]


@router.get("/admin/media/{capability}")
async def media_page(
    request: Request, db: Db, user: AdminUser, capability: str, saved: str = ""
):
    if capability not in CAPABILITIES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That page does not exist."))
    values = _values(db, capability)
    chosen = media_sources.chosen(db, capability)
    default = media_sources.default(db, capability)
    connection_id, model = instance.split(chosen)
    context: dict[str, Any] = {
        "capability": capability,
        # What `_tabs.html` marks as the one being shown.
        "tab": capability,
        "tabs": [(c, f"/admin/media/{c}", i18n.t(catalog.LABELS[c])) for c in CAPABILITIES],
        "values": values,
        "choices": default_choices(db, capability),
        "chosen": chosen,
        "none": instance.NONE,
        "stale": media_sources.stale(db, capability),
        # An address an older release left in the settings and nothing has
        # made a connection of yet (it is carried at the next start).
        "legacy": not chosen and default is not None,
        "default": default,
        "default_href": (
            catalog.media_href(connection_id, capability, model)
            if connection_id and model and default is not None
            else ""
        ),
        "own_sources": _own_sources(db, capability),
        "enabled": media_sources.enabled(db, capability),
        "long": capability in store.LONG,
        "generation": capability in store.NAMED,
        "saved": saved,
        "currency_sign": settings_store.CURRENCIES.get(settings_store.currency(db), ""),
        **catalog.context(),
    }
    if capability == store.CAP_IMAGE:
        context["vision_models"] = list(
            db.scalars(
                select(Model)
                .where(Model.enabled.is_(True))
                .order_by(Model.position, Model.model_id)
            )
        )
    return render(request, "admin/media.html", context)


def _number(raw: Any, name: str, *, whole: bool = True) -> Any:
    """A filled box as a clamped number, an empty one as "" -- "no opinion",
    which `workflow.resolve` reads as "fall through to the built-in floor"."""
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return ""
    if value != value or value in (float("inf"), float("-inf")):
        # "nan" and "inf" are numbers to `float`: not ones to clamp or keep.
        return ""
    low, high = workflow_service.LIMITS.get(name, (None, None))
    if low is not None:
        value = min(max(value, low), high)
    return int(value) if whole else value


def _whole(raw: Any, fallback: int, low: int = 1, high: int = 10) -> int:
    try:
        return min(max(int(str(raw).strip()), low), high)
    except (TypeError, ValueError):
        return int(fallback)


def _default_changes(db, capability: str, raw: Any) -> dict[str, str]:
    """A submitted default: one on offer, `none`, or the stored one left as it
    is (a choice that has gone stays what the page says it is until somebody
    chooses again). Anything else changes nothing."""
    value = str(raw or "").strip()
    kept = media_sources.chosen(db, capability)
    if value == kept:
        return {}
    if value == instance.NONE:
        return instance.default_changes(capability, instance.NONE, None)
    choice = next((c for c in default_choices(db, capability) if c["value"] == value), None)
    if choice is None:
        return {}
    return instance.default_changes(capability, value, db.get(Connection, choice["connection_id"]))


@router.post("/admin/media/{capability}")
async def save_media(request: Request, db: Db, user: AdminUser, capability: str) -> Response:
    """One Save for the capability's tab. Unticked boxes are absent from a
    form post: that absence is the off signal."""
    if capability not in CAPABILITIES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That page does not exist."))
    form = await request.form()
    changes: dict[str, Any] = dict(_default_changes(db, capability, form.get("default")))
    if capability == store.CAP_STT:
        changes["stt_enabled"] = bool(form.get("enabled"))
        key = settings_store.AUDIO
    elif capability == store.CAP_TTS:
        changes["tts_enabled"] = bool(form.get("enabled"))
        changes["tts_autoplay"] = bool(form.get("tts_autoplay"))
        key = settings_store.AUDIO
    elif capability in store.NAMED:
        current = settings_store.images(db)
        key = settings_store.IMAGES
        changes[instance.enabled_key(capability)] = bool(form.get("enabled"))
        changes[f"{capability}_price"] = settings_store.money_amount(form.get("price"))
        for name, low, high, fallback in settings_store.MEDIA_LIMITS:
            # The limits this tab shows: its own, and for what runs as a job
            # the two every job shares.
            if name in form:
                changes[name] = _whole(form.get(name), current.get(name) or fallback, low, high)
    else:
        current = settings_store.get_group(db, settings_store.IMAGES)
        key = settings_store.IMAGES
        changes.update(
            {
                "enabled": bool(form.get("enabled")),
                "review_enabled": bool(form.get("review_enabled")),
                "review_model_id": str(form.get("review_model_id") or "").strip()[:300],
                "max_tries": _whole(form.get("max_tries"), current.get("max_tries") or 4),
                "instructions": str(form.get("instructions") or "").strip()[:4000],
                "per_image": settings_store.money_amount(form.get("per_image")),
                "api_max_images": _whole(
                    form.get("api_max_images"), current.get("api_max_images") or 4
                ),
                "default_steps": _number(form.get("default_steps"), "steps"),
                "default_cfg": _number(form.get("default_cfg"), "cfg", whole=False),
                "default_width": _number(form.get("default_width"), "width"),
                "default_height": _number(form.get("default_height"), "height"),
                "default_sampler": str(form.get("default_sampler") or "").strip()[:80],
                "default_scheduler": str(form.get("default_scheduler") or "").strip()[:80],
                "default_denoise": _number(form.get("default_denoise"), "denoise", whole=False),
                "default_negative": str(form.get("default_negative") or "").strip()[:500],
                "default_batch": _number(form.get("default_batch"), "batch"),
            }
        )
    settings_store.update(db, changes, key=key)
    audio_service.forget_voices()
    log.info("media settings (%s) saved by %s", capability, user.email)
    return _see(f"/admin/media/{capability}", i18n.t("Saved."))


# --- A media model's page ------------------------------------------------------------
def _found(db, connection_id: str, key: str) -> tuple[Connection, store.Entry]:
    connection = db.get(Connection, connection_id)
    entry = catalog.find_media(connection, key) if connection is not None else None
    if connection is None or entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That model no longer exists."))
    return connection, entry


def _href(connection: Connection, entry: store.Entry, tab: str = "") -> str:
    return catalog.media_href(connection.id, entry.capability, entry.model) + (
        f"/{tab}" if tab else ""
    )


def _is_default(db, connection: Connection, entry: store.Entry) -> bool:
    if entry.capability not in CAPABILITIES:
        return False
    return instance.split(media_sources.chosen(db, entry.capability)) == (
        connection.id, entry.model
    )


def media_tabs(connection: Connection, entry: store.Entry) -> list[tuple[str, str, str]]:
    out = [("general", _href(connection, entry), i18n.t("General"))]
    if store.OPTION_KEYS.get(entry.capability):
        out.append(("options", _href(connection, entry, "options"), i18n.t("Options")))
    out.append(("remove", _href(connection, entry, "remove"), i18n.t("Remove")))
    return out


def _option_fields(connection: Connection, entry: store.Entry) -> list[str]:
    """The options this model's page shows: its capability's, less the ones
    that mean nothing where it runs (a provider's picture model has no
    sampler; a ComfyUI checkpoint has)."""
    keys = list(store.OPTION_KEYS.get(entry.capability, ()))
    comfy = providers.get(connection.provider_type).id == providers.COMFYUI
    if entry.capability == store.CAP_IMAGE and not comfy:
        keys = [k for k in keys if k in ("sizes", "negative")]
    if entry.capability == store.CAP_TTS:
        keys = ["voice", *keys]
    return keys


def _model_page(request: Request, db, connection_id: str, key: str, tab: str, **extra):
    connection, entry = _found(db, connection_id, key)
    tabs = media_tabs(connection, entry)
    if tab not in {name for name, _, _ in tabs}:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That page does not exist."))
    siblings = [item for item in catalog.items(db) if item.media]
    index = next(
        (i for i, item in enumerate(siblings)
         if item.connection.id == connection.id and item.key == key),
        0,
    )
    type_options = store.type_options(connection)
    return render(
        request,
        "admin/media_model.html",
        {
            "connection": connection,
            "entry": entry,
            "key": key,
            "tabs": tabs,
            "tab": tab,
            "base": _href(connection, entry),
            "is_default": _is_default(db, connection, entry),
            "served": entry.capability in CAPABILITIES,
            "equipment": store.equipment(connection.provider_type),
            "comfyui": providers.get(connection.provider_type).id == providers.COMFYUI,
            "testable": probe.testable(connection, entry),
            "subscription": providers.subscription(connection.provider_type, connection.base_url),
            "interactive_only": providers.interactive_only(
                connection.provider_type, connection.base_url
            ),
            "option_fields": _option_fields(connection, entry),
            "formats": store.AUDIO_FORMATS,
            "samplers": type_options.get("samplers") or [],
            "schedulers": type_options.get("schedulers") or [],
            "previous": siblings[index - 1] if index > 0 else None,
            "next": siblings[index + 1] if index + 1 < len(siblings) else None,
            "position_of": index + 1,
            "total": len(siblings),
            **catalog.context(),
            **extra,
        },
    )


@router.get("/admin/models/media/{connection_id}/{key}")
async def media_model_general(
    request: Request, db: Db, user: AdminUser, connection_id: str, key: str,
    saved: str = "", error: str = "",
):
    return _model_page(request, db, connection_id, key, "general", saved=saved, error=error)


@router.get("/admin/models/media/{connection_id}/{key}/{tab}")
async def media_model_tab(
    request: Request, db: Db, user: AdminUser, connection_id: str, key: str, tab: str,
    saved: str = "", error: str = "",
):
    if tab not in ("options", "remove"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That page does not exist."))
    return _model_page(request, db, connection_id, key, tab, saved=saved, error=error)


@router.post("/admin/models/media/{connection_id}/{key}")
async def save_media_model(
    request: Request, db: Db, user: AdminUser, connection_id: str, key: str
) -> Response:
    """General: what it is called, whether it is on, what it costs."""
    connection, entry = _found(db, connection_id, key)
    form = await request.form()
    changed = store._entry(
        {
            **entry.as_dict(),
            "name": form.get("name"),
            "description": form.get("description"),
            "enabled": bool(form.get("enabled")),
            "price": form.get("price"),
        }
    )
    connection.media_json = store.with_entry(connection, changed)
    db.commit()
    return _see(_href(connection, entry), i18n.t("Saved."))


@router.post("/admin/models/media/{connection_id}/{key}/options")
async def save_media_model_options(
    request: Request, db: Db, user: AdminUser, connection_id: str, key: str
) -> Response:
    """Options: how this model is asked. An emptied box is "no opinion"."""
    connection, entry = _found(db, connection_id, key)
    form = await request.form()
    shown = _option_fields(connection, entry)
    options = {
        name: form.get(name) for name in shown if name != "voice"
    }
    # What the page does not show for this model is kept as it is.
    kept = {k: v for k, v in entry.options.items() if k not in shown}
    changed = store._entry(
        {
            **entry.as_dict(),
            "voice": form.get("voice") if "voice" in shown else entry.voice,
            "options": {**kept, **options},
        }
    )
    connection.media_json = store.with_entry(connection, changed)
    db.commit()
    audio_service.forget_voices()
    return _see(_href(connection, entry, "options"), i18n.t("Saved."))


@router.post("/admin/models/media/{connection_id}/{key}/voices")
async def fetch_voices(db: Db, user: AdminUser, connection_id: str, key: str) -> Response:
    """Ask the speech server which voices it has, and keep the list."""
    connection, entry = _found(db, connection_id, key)
    back = _href(connection, entry, "options")
    if entry.capability != store.CAP_TTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That page does not exist."))
    try:
        found = await audio_service.voices(Endpoint.from_connection(connection), refresh=True)
    except LLMError as exc:
        return _see(back, exc.message[:300], error=True)
    if not found:
        return _see(back, i18n.t("The server listed no voices. Type them below."), error=True)
    changed = store._entry(
        {**entry.as_dict(), "options": {**entry.options, "voices": found}}
    )
    connection.media_json = store.with_entry(connection, changed)
    db.commit()
    return _see(back, i18n.t("Found %(count)s voices.", count=len(found)))


@router.post("/admin/models/media/{connection_id}/{key}/test")
async def test_media_model(db: Db, user: AdminUser, connection_id: str, key: str) -> Response:
    connection, entry = _found(db, connection_id, key)
    message, error = await probe.model(connection, entry)
    return _see(_href(connection, entry), message[:400], error=error)


@router.post("/admin/models/media/{connection_id}/{key}/toggle")
async def toggle_media_model(
    db: Db, user: AdminUser, connection_id: str, key: str, back: str = ""
) -> Response:
    """On or off, from a list: applies at once, and comes back to the list."""
    from lembas.api.admin_models import local_back

    connection, entry = _found(db, connection_id, key)
    changed = store._entry({**entry.as_dict(), "enabled": not entry.enabled})
    connection.media_json = store.with_entry(connection, changed)
    db.commit()
    return RedirectResponse(
        local_back(back, "/admin/models"), status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/admin/models/media/{connection_id}/{key}/default")
async def make_default(db: Db, user: AdminUser, connection_id: str, key: str) -> Response:
    """Make this model the instance's default for its capability."""
    connection, entry = _found(db, connection_id, key)
    if entry.capability not in CAPABILITIES or not entry.enabled or not connection.enabled:
        return _see(
            _href(connection, entry),
            i18n.t(
                "Only a model that is switched on, on a connection that is on, "
                "can be the default."
            ),
            error=True,
        )
    group = (
        settings_store.IMAGES if entry.capability in store.GENERATIONS else settings_store.AUDIO
    )
    settings_store.update(
        db,
        instance.default_changes(entry.capability, f"{connection.id}/{entry.model}", connection),
        key=group,
    )
    audio_service.forget_voices()
    return _see(_href(connection, entry), i18n.t("Saved."))


@router.post("/admin/models/media/{connection_id}/{key}/delete")
async def delete_media_model(db: Db, user: AdminUser, connection_id: str, key: str) -> Response:
    """Take the model off its connection. Where it was a default, the choice
    is kept and shown as gone: nothing is quietly served in its place."""
    connection, entry = _found(db, connection_id, key)
    was_default = _is_default(db, connection, entry)
    connection.media_json = store.without_entry(connection, entry.capability, entry.model)
    db.commit()
    word = i18n.t("Removed.")
    if was_default:
        word = i18n.t(
            "%(name)s was a default under Media. "
            "Choose another there: until then nothing is served by default.",
            name=entry.label,
        )
    return _see(f"/admin/connections/{connection.id}/models", word)


__all__ = ["default_choices", "media_tabs", "router"]
