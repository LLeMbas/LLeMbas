"""DuckDuckGo, via the ddgs package.

The default provider because it is the only one that works with no account, no
key and no server to run: enabling web search should not also be a
configuration exercise.

Optional at install time -- see the `search` extra in pyproject.toml -- so the
import is guarded and its absence is reported as something to install rather
than as a crash.
"""

from __future__ import annotations

import asyncio
from typing import Any

from lembas.services.search.base import SearchError, SearchResult, clean

try:  # pragma: no cover - exercised by whether the extra is installed
    from ddgs import DDGS

    _IMPORT_ERROR = ""
except ImportError as exc:  # pragma: no cover
    DDGS = None
    _IMPORT_ERROR = str(exc)


def is_available() -> bool:
    return DDGS is not None


def _blocking_search(query: str, count: int, region: str, safesearch: str) -> list[dict[str, Any]]:
    with DDGS() as client:
        return list(
            client.text(
                query,
                region=region or "wt-wt",
                safesearch=safesearch or "moderate",
                max_results=count,
            )
        )


async def search(config: dict[str, Any], query: str, count: int) -> list[SearchResult]:
    if not is_available():
        raise SearchError(
            'The ddgs package is not installed. Install it with: pip install "lembas[search]"'
        )

    try:
        # ddgs is synchronous. Run it on a thread: blocking the event loop here
        # would stall every other chat in the process, including the one that
        # asked for the search.
        rows = await asyncio.wait_for(
            asyncio.to_thread(
                _blocking_search,
                query,
                count,
                str(config.get("region") or "wt-wt"),
                str(config.get("safesearch") or "moderate"),
            ),
            timeout=float(config.get("timeout") or 20.0),
        )
    except TimeoutError as exc:
        raise SearchError("DuckDuckGo did not answer in time.") from exc
    except Exception as exc:  # noqa: BLE001 - the library raises its own types
        # Rate limiting is the common failure and worth naming, because the fix
        # is to wait rather than to change anything.
        detail = str(exc)
        if "ratelimit" in detail.lower() or "202" in detail:
            raise SearchError(
                "DuckDuckGo is rate limiting this instance. Try again shortly, "
                "or configure SearXNG instead."
            ) from exc
        raise SearchError(f"DuckDuckGo search failed: {detail[:200]}") from exc

    results = []
    for row in rows:
        # ddgs renamed its fields across versions; both spellings are read so
        # an upgrade does not silently return empty snippets.
        result = clean(
            row.get("title"),
            row.get("href") or row.get("url") or row.get("link"),
            row.get("body") or row.get("description") or row.get("snippet"),
        )
        if result is not None:
            results.append(result)
    return results
