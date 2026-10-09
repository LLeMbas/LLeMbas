"""Web search administration: which provider, and how to reach it."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from lembas.api.deps import AdminUser, Db
from lembas.services import search as search_service
from lembas.services import settings_store
from lembas.services.crypto import UNCHANGED_SENTINEL, decrypt, keep_or_replace, mask
from lembas.services.search.base import SearchError
from lembas.web.i18n import t
from lembas.web.templating import render

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/search", tags=["admin-search"])

SAFESEARCH = ("off", "moderate", "strict")


@router.get("")
async def search_page(request: Request, db: Db, user: AdminUser, saved: bool = False):
    values = settings_store.search(db)
    return render(
        request,
        "admin/search.html",
        {
            "values": values,
            "providers": search_service.PROVIDERS,
            # Keyed by provider so the form can show an install hint against
            # the one that needs it, without the template knowing why.
            "problems": {
                p.key: search_service.availability(p.key) for p in search_service.PROVIDERS
            },
            "safesearch_options": SAFESEARCH,
            "masked": mask(decrypt(values.get("firecrawl_api_key_encrypted") or "")),
            "unchanged": UNCHANGED_SENTINEL,
            "saved": saved,
        },
    )


@router.post("")
async def save_search(
    db: Db,
    user: AdminUser,
    enabled: bool = Form(False),
    provider: str = Form("ddgs"),
    max_results: int = Form(5),
    region: str = Form("wt-wt"),
    safesearch: str = Form("moderate"),
    searxng_base_url: str = Form(""),
    firecrawl_base_url: str = Form(""),
    firecrawl_api_key: str = Form(""),
    timeout: float = Form(20.0),
    allow_private_fetch: bool = Form(False),
    fetch_enabled: bool = Form(False),
) -> Response:
    current = settings_store.search(db)
    known = {p.key for p in search_service.PROVIDERS}

    settings_store.update(
        db,
        {
            "enabled": enabled,
            "provider": provider if provider in known else "ddgs",
            # An upper bound on what any single search may put in the prompt.
            # Twenty results is already more than a model reads carefully.
            "max_results": min(max(max_results, 1), 20),
            "region": region.strip()[:16] or "wt-wt",
            "safesearch": safesearch if safesearch in SAFESEARCH else "moderate",
            "searxng_base_url": searxng_base_url.strip().rstrip("/"),
            "firecrawl_base_url": firecrawl_base_url.strip().rstrip("/")
            or "https://api.firecrawl.dev",
            "firecrawl_api_key_encrypted": keep_or_replace(
                firecrawl_api_key, current.get("firecrawl_api_key_encrypted") or ""
            ),
            "timeout": min(max(timeout, 5.0), 120.0),
            "allow_private_fetch": allow_private_fetch,
            "fetch_enabled": fetch_enabled,
        },
        key=settings_store.SEARCH,
    )
    log.info("web search %s by %s", "enabled" if enabled else "disabled", user.email)
    return RedirectResponse("/admin/search?saved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/test")
async def test_search(request: Request, db: Db, user: AdminUser, query: str = Form("")):
    """Run one real search and show what came back.

    Against the stored settings rather than the unsaved form, so what is tested
    is what a chat would actually do.
    """
    config = settings_store.search(db)
    query = query.strip() or "lembas"

    try:
        results = await search_service.run(config, query)
        label = search_service.provider(config.get("provider")).label
        message = (
            t("%(provider)s returned 1 result.", provider=label)
            if len(results) == 1
            else t("%(provider)s returned %(n)s results.", provider=label, n=len(results))
        )
        kind = "success"
    except SearchError as exc:
        results, message, kind = [], exc.message, "error"

    return render(
        request,
        "admin/_search_result.html",
        {"results": results, "message": message, "message_kind": kind, "query": query},
    )
