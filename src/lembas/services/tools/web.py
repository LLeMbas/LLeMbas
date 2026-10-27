"""`web_search` and `web_fetch`."""

from __future__ import annotations

import logging
from typing import Any

from lembas.services import search as search_service
from lembas.services.search.base import SearchError
from lembas.services.tools.core import (
    FAMILY_FETCH,
    FAMILY_SEARCH,
    MAX_FETCH_CHARS,
    ToolContext,
    ToolDef,
    ToolOutcome,
    spec_description,
    spec_parameters,
)

log = logging.getLogger("lembas.services.tools")


# --- Web search --------------------------------------------------------------
async def _run_web_search(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolOutcome(
            "No search query was given.",
            {"name": "web_search", "status": "error", "error": "No query was given."},
        )

    limit = args.get("max_results")
    try:
        limit = int(limit) if limit is not None else None
    except (TypeError, ValueError):
        limit = None

    try:
        results = await search_service.run(context.search_config, query, limit=limit)
    except SearchError as exc:
        log.info("web search failed for %r: %s", query[:60], exc.message)
        return ToolOutcome(
            f"The search failed: {exc.message}",
            {"name": "web_search", "query": query, "status": "error", "error": exc.message},
        )

    event = {
        "name": "web_search",
        "query": query,
        "status": "ok",
        "results": [
            {"title": r.title, "url": r.url, "snippet": r.snippet, "host": r.host}
            for r in results
        ],
    }
    if not results:
        return ToolOutcome(f"No results were found for {query!r}.", event)

    lines = [f"Search results for {query!r}:"]
    for index, result in enumerate(results, start=1):
        lines.append(f"\n[{index}] {result.title}\n{result.url}\n{result.snippet}")
    return ToolOutcome("\n".join(lines), event)


# --- Fetching one page ---------------------------------------------------------
async def _run_web_fetch(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    """Retrieve one URL and hand back a part of it as markdown.

    Straight through `services/fetch.py`, which owns the SSRF guard, the
    hand-rolled redirect loop that re-checks every hop, and the content-type
    sniff. Deliberately not a second HTTP client: the working notes already name three
    places that follow redirects by hand as the ceiling, and a fourth is how one
    of them loses its check.

    Read in parts the way the LLeMbas CLI's `web_fetch` reads a page: up to
    `MAX_FETCH_CHARS` from `offset`, the title as a heading, then the address,
    and a closing line naming the offset to call again with while more is left.
    """
    from lembas.services import fetch as fetch_service

    url = str(args.get("url") or "").strip()
    if not url:
        return ToolOutcome(
            "No address was given.",
            {"name": "web_fetch", "status": "error", "error": "No URL."},
        )
    try:
        offset = max(int(args.get("offset") or 0), 0)
    except (TypeError, ValueError):
        offset = 0

    try:
        page = await fetch_service.fetch(
            url,
            allow_private=bool(context.search_config.get("allow_private_fetch")),
            markdown=True,
        )
    except fetch_service.FetchError as exc:
        # Its messages are already written to be shown to a person, which is
        # close enough to being written for a model to act on.
        return ToolOutcome(
            f"That page could not be read: {exc.message}",
            {"name": "web_fetch", "query": url, "status": "error", "error": exc.message},
        )

    text = page.text
    part = text[offset : offset + MAX_FETCH_CHARS]
    left = len(text) - offset - MAX_FETCH_CHARS
    if left > 0:
        more = (
            f"\n\n({left} more characters — call again with offset "
            f"{offset + MAX_FETCH_CHARS})"
        )
    elif page.truncated:
        more = "\n\n(The page was longer than this and has been cut off.)"
    else:
        more = ""
    event = {
        "name": "web_fetch",
        "kind": "fetch",
        "query": page.title or url,
        "detail": page.url,
        "status": "ok",
        "results": [],
        "text": part[:2000],
    }
    heading = f"# {page.title}\n" if page.title else ""
    return ToolOutcome(f"{heading}{page.url}\n\n{part}{more}", event)


TOOLS: tuple[ToolDef, ...] = (
    # The two web tools are the harness spec's, word for word, as the
    # LLeMbas CLI sends them.
    ToolDef(
        name="web_search",
        family=FAMILY_SEARCH,
        description=spec_description("web_search"),
        parameters=spec_parameters("web_search"),
        run=_run_web_search,
    ),
    ToolDef(
        name="web_fetch",
        family=FAMILY_FETCH,
        description=spec_description("web_fetch"),
        parameters=spec_parameters("web_fetch"),
        run=_run_web_fetch,
    ),
)
