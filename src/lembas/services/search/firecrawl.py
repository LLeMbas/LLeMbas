"""Firecrawl's hosted search API.

The paid option, and the only one of the three that needs a key. Included
because it answers with cleaned page content rather than a search engine's
snippet, which is materially better material for a model to read.
"""

from __future__ import annotations

from typing import Any

import httpx

from lembas.services.crypto import decrypt
from lembas.services.search.base import SearchError, SearchResult, clean

DEFAULT_BASE_URL = "https://api.firecrawl.dev"


async def search(config: dict[str, Any], query: str, count: int) -> list[SearchResult]:
    api_key = decrypt(str(config.get("firecrawl_api_key_encrypted") or ""))
    if not api_key:
        raise SearchError("No Firecrawl API key has been configured.")

    base_url = str(config.get("firecrawl_base_url") or DEFAULT_BASE_URL).strip().rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=float(config.get("timeout") or 20.0)) as client:
            response = await client.post(
                f"{base_url}/v1/search",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "limit": count},
            )
    except httpx.RequestError as exc:
        raise SearchError(f"Could not reach {base_url}: {exc}") from exc

    if response.status_code == 401:
        raise SearchError("Firecrawl rejected the API key.")
    if response.status_code == 402:
        raise SearchError("The Firecrawl account is out of credit.")

    try:
        payload = response.json()
    except ValueError as exc:
        raise SearchError(f"Firecrawl returned HTTP {response.status_code}.") from exc

    if response.status_code >= 400 or (
        isinstance(payload, dict) and payload.get("success") is False
    ):
        detail = payload.get("error") if isinstance(payload, dict) else ""
        raise SearchError(str(detail) or f"Firecrawl returned HTTP {response.status_code}.")

    rows = payload.get("data") if isinstance(payload, dict) else None
    # Newer responses nest the list under data.web; older ones put it directly
    # in data. Both are read so an API revision does not empty the results.
    if isinstance(rows, dict):
        rows = rows.get("web")
    if not isinstance(rows, list):
        raise SearchError("Firecrawl returned a response in an unexpected shape.")

    results = []
    for row in rows[:count]:
        if not isinstance(row, dict):
            continue
        result = clean(
            row.get("title"),
            row.get("url"),
            row.get("description") or row.get("markdown") or row.get("content"),
        )
        if result is not None:
            results.append(result)
    return results
