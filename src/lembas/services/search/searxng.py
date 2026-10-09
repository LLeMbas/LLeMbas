"""SearXNG, a self-hosted metasearch instance.

The right answer for anyone already running one: no third party sees the
queries, and it aggregates several engines. It needs one thing switched on
first, which a stock install does not have, so that case is detected and named
rather than reported as "search failed".
"""

from __future__ import annotations

from typing import Any

import httpx

from lembas.services.search.base import SearchError, SearchResult, clean

# What a stock settings.yml is missing. Worth quoting exactly: it is the whole
# fix, and hunting for it in the documentation takes longer than reading it.
JSON_DISABLED = (
    "This SearXNG instance will not answer in JSON. Add \"- json\" under "
    "search.formats in its settings.yml and restart it."
)


async def search(config: dict[str, Any], query: str, count: int) -> list[SearchResult]:
    base_url = str(config.get("searxng_base_url") or "").strip().rstrip("/")
    if not base_url:
        raise SearchError("No SearXNG instance has been configured.")

    params = {
        "q": query,
        "format": "json",
        "categories": "general",
        "safesearch": {"off": "0", "moderate": "1", "strict": "2"}.get(
            str(config.get("safesearch") or "moderate"), "1"
        ),
    }

    try:
        async with httpx.AsyncClient(
            timeout=float(config.get("timeout") or 20.0), follow_redirects=True
        ) as client:
            response = await client.get(f"{base_url}/search", params=params)
    except httpx.RequestError as exc:
        raise SearchError(f"Could not reach {base_url}: {exc}") from exc

    # 403 on an otherwise working instance means the JSON format is not in the
    # allowed list -- SearXNG refuses the format rather than the request.
    if response.status_code == 403:
        raise SearchError(JSON_DISABLED)
    if response.status_code >= 400:
        raise SearchError(f"{base_url} returned HTTP {response.status_code}.")

    try:
        payload = response.json()
    except ValueError as exc:
        # An HTML page where JSON was asked for is the same misconfiguration
        # wearing a different status code.
        raise SearchError(JSON_DISABLED) from exc

    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise SearchError("SearXNG returned a response in an unexpected shape.")

    results = []
    for row in rows[:count]:
        if not isinstance(row, dict):
            continue
        result = clean(row.get("title"), row.get("url"), row.get("content"))
        if result is not None:
            results.append(result)
    return results
