"""Administration: OpenAI-compatible connections and their models."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.api.deps import AdminUser, Db
from lembas.db.models import Connection, Model, guess_kind
from lembas.db.models.connection import KIND_OPENAI, KINDS, PROVIDER_PATTERN
from lembas.services import chat as chat_service
from lembas.services import data_groups, settings_store
from lembas.services.crypto import UNCHANGED_SENTINEL, decrypt, encrypt, mask
from lembas.services.llm.anthropic_client import DEFAULT_BASE_URL as ANTHROPIC_URL
from lembas.services.llm.openai_client import Endpoint, LLMError, context_from, list_models
from lembas.web import i18n
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _connection(db: DBSession, connection_id: str) -> Connection:
    connection = db.get(Connection, connection_id)
    if connection is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, i18n.t("That connection no longer exists."))
    return connection


def _connections(db: DBSession) -> list[Connection]:
    return list(db.scalars(select(Connection).order_by(Connection.position, Connection.name)))


@router.get("")
async def admin_home(user: AdminUser):
    return RedirectResponse("/admin/general", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/general")
async def general_page(request: Request, db: Db, user: AdminUser, saved: bool = False):
    return render(
        request,
        "admin/general.html",
        {
            "values": settings_store.get_group(db),
            "languages": i18n.LANGUAGES,
            "currencies": tuple(settings_store.CURRENCIES),
            "currency": settings_store.currency(db),
            "saved": saved,
        },
    )


@router.post("/general")
async def save_general(
    db: Db,
    user: AdminUser,
    instance_name: str = Form(""),
    personalization_enabled: bool = Form(False),
    language: str = Form(""),
    system_prompt: str = Form(""),
    compact_threshold: int = Form(95),
    max_chat_rounds: int = Form(5),
    temporary_lifetime_minutes: int = Form(60),
    instance_mode: str = Form(""),
    currency: str = Form(""),
) -> Response:
    """Save instance settings.

    Unchecked checkboxes are simply absent from a form post, which is why the
    booleans default to False here -- that absence *is* the "off" signal.
    """
    # Validated rather than trusted, and an empty or unknown value keeps what is
    # stored: a form that predates the field, or a typo in a scripted POST, must
    # not flip what the instance is.
    mode = instance_mode if instance_mode in settings_store.INSTANCE_MODES else None
    if mode is None:
        mode = settings_store.instance_mode(db)
    settings_store.update(
        db,
        {
            "instance_mode": mode,
            # Absent or unknown keeps what is stored, as the mode does.
            "currency": (
                currency.upper()
                if currency.upper() in settings_store.CURRENCIES
                else settings_store.currency(db)
            ),
            "instance_name": " ".join(instance_name.split())[:60],
            "personalization_enabled": personalization_enabled,
            # Validated rather than trusted: a code this release does not have
            # would leave every page in a language nobody chose.
            "language": i18n.known(language),
            "system_prompt": system_prompt.strip()[:8000],
            # 0 is "never"; anything else is clamped into a band where it can
            # do some good. 100 is useless -- you cannot compact after
            # overflowing -- and below 50 it fires while there is plenty left.
            "compact_threshold": (
                0 if compact_threshold <= 0 else min(max(compact_threshold, 50), 99)
            ),
            # Floor of 0, not 1: zero is how "no ceiling" is said, and the loop
            # falls back to a runaway backstop rather than to this number.
            "max_chat_rounds": min(max(max_chat_rounds, 0), 100),
            "temporary_lifetime_minutes": min(
                max(temporary_lifetime_minutes, 0), chat_service.TEMPORARY_LIFETIME_MAX_MINUTES
            ),
        },
    )
    # The instance default is cached at process level, exactly as branding is, so
    # the one module that writes it is the one that drops the cache.
    i18n.forget()
    from lembas.services import branding

    branding.forget()
    log.info("general settings saved by %s", user.email)
    return RedirectResponse("/admin/general?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/connections")
async def connections_page(request: Request, db: Db, user: AdminUser, message: str = ""):
    connections = _connections(db)
    return render(
        request,
        "admin/connections.html",
        {
            "connections": connections,
            "masked": {c.id: mask(decrypt(c.api_key_encrypted)) for c in connections},
            "model_counts": {
                c.id: sum(1 for m in c.models if m.enabled) for c in connections
            },
            "message": message,
            "unchanged": UNCHANGED_SENTINEL,
            "data_group_choices": data_groups.instance_groups(db),
            "anthropic_url": ANTHROPIC_URL,
        },
    )


# Header names are a narrow set on purpose: a newline would let one field write
# a second header, and a colon in a name splits it. Anything outside it is
# dropped rather than repaired -- a header nobody can see the effect of is worse
# than one that is visibly missing.
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")


def _parse_headers(raw: str) -> dict[str, str]:
    """`Name: value` per line, into the dict the client sends verbatim."""
    headers: dict[str, str] = {}
    for line in (raw or "").splitlines()[:20]:
        name, _, value = line.partition(":")
        name = name.strip()
        value = value.strip()[:500]
        if name and value and _HEADER_NAME.match(name):
            headers[name] = value
    return headers


def _kind(raw: str) -> str:
    """The submitted dialect, or OpenAI's for anything this release does not know."""
    kind = (raw or "").strip().lower()
    return kind if kind in KINDS else KIND_OPENAI


def _provider(db: DBSession, raw: str, connection_id: str | None = None) -> str:
    """A submitted provider name, checked: the shape `PROVIDER_PATTERN` allows,
    and not another connection's. Lowercased first, so `DeepSeek` is accepted as
    the `deepseek` it means rather than refused for a capital."""
    provider = (raw or "").strip().lower()
    if not PROVIDER_PATTERN.match(provider):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            i18n.t(
                "A provider name is 1 to 32 lowercase letters, digits and hyphens, "
                "and starts with a letter or a digit."
            ),
        )
    clash = db.scalar(
        select(Connection).where(
            Connection.provider == provider, Connection.id != (connection_id or "")
        )
    )
    if clash is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            i18n.t(
                "The connection “%(name)s” already uses the provider name “%(provider)s”.",
                name=clash.name,
                provider=provider,
            ),
        )
    return provider


@router.post("/connections")
async def create_connection(
    db: Db,
    user: AdminUser,
    name: str = Form(...),
    base_url: str = Form(...),
    api_key: str = Form(""),
    kind: str = Form(KIND_OPENAI),
    provider: str = Form(""),
) -> Response:
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            i18n.t("The base URL must start with http:// or https://"),
        )
    # Required by the form; a scripted POST that leaves it out gets one made from
    # the name, as an older row does (`Connection`'s insert hook).
    chosen = _provider(db, provider) if provider.strip() else None

    position = db.scalar(select(func.coalesce(func.max(Connection.position), -1))) + 1
    connection = Connection(
        name=name.strip()[:120] or "Connection",
        base_url=base_url,
        api_key_encrypted=encrypt(api_key.strip()),
        position=position,
        kind=_kind(kind),
        provider=chosen,
    )
    db.add(connection)
    db.commit()

    # Discover models immediately: a connection that lists nothing is
    # indistinguishable from a broken one, and finding out now is the point.
    await _refresh_models(db, connection)
    return RedirectResponse("/admin/connections", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/connections/{connection_id}")
async def update_connection(
    db: Db,
    user: AdminUser,
    connection_id: str,
    name: str = Form(...),
    base_url: str = Form(...),
    api_key: str = Form(""),
    enabled: bool = Form(False),
    unload_url: str = Form(""),
    unload_method: str = Form("POST"),
    extra_headers: str = Form(""),
    data_group_id: str = Form(""),
    one_model_at_a_time: bool = Form(False),
    kind: str = Form(""),
    provider: str = Form(""),
) -> Response:
    connection = _connection(db, connection_id)
    # Empty is "not submitted" -- a page from an older release -- and leaves both
    # alone, as `data_group_id` below does.
    if provider.strip():
        connection.provider = _provider(db, provider, connection.id)
    if kind.strip():
        connection.kind = _kind(kind)
    connection.one_model_at_a_time = one_model_at_a_time
    # Which of the instance's data groups this provider reads. Empty is "not
    # submitted" -- an older page -- and leaves it alone; a personal group is
    # somebody else's arrangement and cannot be chosen here.
    chosen = data_group_id.strip()
    if chosen:
        group = data_groups.get(db, chosen)
        if group is not None and group.owner_id is None:
            connection.data_group_id = group.id
    connection.name = name.strip()[:120] or connection.name
    connection.base_url = base_url.strip().rstrip("/")
    connection.enabled = enabled
    # How to ask this endpoint to drop its model, for image generation's
    # Preserve VRAM. Empty means it cannot be unloaded, which is the honest
    # answer for anything not running on the machine ComfyUI is on.
    connection.unload_url = unload_url.strip()[:500]
    method = unload_method.strip().upper()
    connection.unload_method = method if method in ("GET", "POST") else "POST"

    # `extra_headers_json` has been sent with every request to this endpoint
    # since it was added and written by no form in the application, so its one
    # documented use -- OpenRouter wants an `HTTP-Referer` and an `X-Title` --
    # was unreachable. One `Name: value` per line, because a JSON textarea asks
    # somebody to get braces right in a settings screen.
    connection.extra_headers_json = _parse_headers(extra_headers)

    submitted = api_key.strip()
    if submitted and submitted != UNCHANGED_SENTINEL:
        connection.api_key_encrypted = encrypt(submitted)
    elif not submitted:
        # An explicitly emptied field means "this endpoint needs no key".
        connection.api_key_encrypted = ""

    db.commit()
    return RedirectResponse("/admin/connections", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/connections/{connection_id}/test")
async def test_connection(
    request: Request, db: Db, user: AdminUser, connection_id: str
) -> Response:
    """Contact the endpoint and refresh its model list."""
    connection = _connection(db, connection_id)
    count, error = await _refresh_models(db, connection)

    if error:
        message = f"{connection.name}: {error}"
    elif count == 1:
        message = i18n.t("%(name)s: found 1 model.", name=connection.name)
    else:
        message = i18n.t("%(name)s: found %(count)s models.", name=connection.name, count=count)
    return render(
        request,
        "admin/_connection_row.html",
        {
            "connection": connection,
            "masked": mask(decrypt(connection.api_key_encrypted)),
            "message": message,
            "message_kind": "error" if error else "success",
            "unchanged": UNCHANGED_SENTINEL,
            "data_group_choices": data_groups.instance_groups(db),
            "anthropic_url": ANTHROPIC_URL,
        },
    )


async def _refresh_models(db: DBSession, connection: Connection) -> tuple[int, str]:
    """Sync the cached model list. Returns (count, error message)."""
    try:
        discovered = await list_models(Endpoint.from_connection(connection))
    except LLMError as exc:
        connection.last_error = exc.message
        connection.last_checked_at = datetime.now(UTC)
        db.commit()
        return 0, exc.message

    existing = {model.model_id: model for model in connection.models}
    seen: set[str] = set()

    # New models land after everything already ordered, rather than all at
    # position 0 where they would sort by id and shuffle the existing list.
    # No `or -1` after the coalesce: position 0 is falsy, so that idiom sent the
    # second discovered model back to 0 on top of the first.
    highest = db.scalar(select(func.coalesce(func.max(Model.position), -1)))
    next_position = int(highest if highest is not None else -1) + 1

    for entry in discovered:
        model_id = str(entry["id"])[:300]
        seen.add(model_id)
        if model_id in existing:
            # A context length is filled in only when nobody has one yet. A
            # refresh must never overwrite a number an administrator typed --
            # they are usually correcting the endpoint.
            model = existing[model_id]
            if not model.context_length:
                model.context_length = context_from(entry)
            _record_output(model, entry)
            continue
        model = Model(
            connection_id=connection.id,
            model_id=model_id,
            position=next_position,
            context_length=context_from(entry),
            display_name=str(entry.get("display_name") or "")[:300],
            # An id that says "embed" is an embedding model; anything
            # else is a chat model until the model page says otherwise.
            kind=guess_kind(model_id),
        )
        _record_output(model, entry)
        db.add(model)
        next_position += 1

    # Models that vanished upstream are dropped, so the picker never offers
    # something the endpoint will reject.
    for model_id, model in existing.items():
        if model_id not in seen:
            db.delete(model)

    connection.last_error = ""
    connection.last_checked_at = datetime.now(UTC)
    db.commit()
    log.info("connection %s: %d models", connection.name, len(seen))
    return len(seen), ""


def _record_output(model: Model, entry: dict) -> None:
    """A model's output ceiling, where the endpoint states one (Anthropic's
    `max_tokens`, passed on as `max_output`), as `Model.output_limit`: a cap on
    what a request may ask for, never what it asks for by default."""
    output = entry.get("max_output")
    if isinstance(output, int) and not isinstance(output, bool) and output > 0:
        model.output_limit = output


@router.post("/connections/{connection_id}/delete")
async def delete_connection(db: Db, user: AdminUser, connection_id: str) -> Response:
    connection = _connection(db, connection_id)
    db.delete(connection)
    db.commit()
    return RedirectResponse("/admin/connections", status_code=status.HTTP_303_SEE_OTHER)
