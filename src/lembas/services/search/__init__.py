"""Web search providers.

One shape in, one shape out: a query and a limit go in, a list of SearchResult
comes back, and which service answered is a setting rather than a code path any
caller has to know about.

Everything here returns *untrusted third-party text*. A title or snippet from a
search result is exactly as much attacker-controlled as model output, and gets
the same treatment: escaped on the way into a page, and only http/https URLs
rendered as links.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from lembas.services.search import ddg, firecrawl, searxng
from lembas.services.search.base import SearchError, SearchResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    description: str
    # Whether an administrator has to configure something before it works.
    needs_setup: bool


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        "ddgs",
        "DuckDuckGo",
        "No account, no key, no server to run. Rate limited if used heavily.",
        False,
    ),
    Provider(
        "searxng",
        "SearXNG",
        "Your own metasearch instance. Needs its JSON format enabled.",
        True,
    ),
    Provider(
        "firecrawl",
        "Firecrawl",
        "Hosted search API. Needs an account and a key.",
        True,
    ),
)

_RUNNERS = {"ddgs": ddg.search, "searxng": searxng.search, "firecrawl": firecrawl.search}


def provider(key: str) -> Provider:
    return next((p for p in PROVIDERS if p.key == key), PROVIDERS[0])


def availability(key: str) -> str:
    """Why a provider cannot be used, or "" when it can.

    Checked before a search is attempted so the admin screen can say what is
    wrong while it is being configured, rather than the first chat to try it
    being where the problem surfaces.
    """
    if key == "ddgs" and not ddg.is_available():
        return (
            "The ddgs package is not installed. Install it with: "
            'pip install "lembas[search]"'
        )
    return ""


async def run(
    config: dict[str, Any], query: str, *, limit: int | None = None
) -> list[SearchResult]:
    """Search with whichever provider is configured.

    Raises SearchError with something worth reading; every provider translates
    its own failures rather than letting an httpx exception escape.
    """
    query = " ".join(query.split())[:400]
    if not query:
        raise SearchError("There was nothing to search for.")

    key = config.get("provider") or "ddgs"
    problem = availability(key)
    if problem:
        raise SearchError(problem)

    runner = _RUNNERS.get(key)
    if runner is None:
        raise SearchError(f"Unknown search provider '{key}'.")

    count = limit or int(config.get("max_results") or 5)
    # A model that asks for fifty results is asking for a prompt nobody can
    # afford; the administrator's number is the ceiling either way.
    count = min(max(count, 1), int(config.get("max_results") or 5))

    results = await runner(config, query, count)
    log.info("web search (%s) for %r: %d results", key, query[:60], len(results))
    return results[:count]


__all__ = ["PROVIDERS", "Provider", "SearchError", "SearchResult", "availability", "run"]
