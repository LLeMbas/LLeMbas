"""Fetching a web page so it can be kept, or read to a model.

Two things this deliberately does not do.

**It does not try to be clever about extraction.** No readability heuristics, no
main-column detection: script and style go, tags are dropped, whitespace is
collapsed. A clever extractor that silently discards the part somebody wanted is
worse than a plain one that keeps everything, and it would be a dependency.

**It does not trust the URL.** This runs on a server that can very likely reach
a router's admin page, a metadata endpoint, and every other service on the same
machine -- LLeMbas itself included. A fetcher that takes a URL from a user, or
worse from a model, is a request-forgery hole unless something stops it, so
addresses are checked after resolution and redirects are followed by hand.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx
import nh3

log = logging.getLogger(__name__)

# Pages are kept as text, so the ceiling is about what is worth reading rather
# than what will fit on disk.
MAX_PAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 120_000
MAX_REDIRECTS = 5
TIMEOUT = 20.0

# Sent because a plain httpx user agent is blocked by a good number of sites,
# and being honest about what this is beats impersonating a browser.
USER_AGENT = "Mozilla/5.0 (compatible; LLeMbas/1.0; +https://llembas.eu)"

# <head> goes wholesale, which takes script, style and the title with it. The
# title is pulled out of the raw HTML first, so removing it here is what stops
# it appearing again as the opening line of the body.
_DROPPED = re.compile(
    r"<(head|script|style|noscript|template|svg)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Content types that are text but are not spelled `text/*`. The sniff below was
# written for "save this page into my library" and refused every one of them,
# which meant every JSON API there is -- wrong for the link-attach path already,
# and unusable once a model can ask for a URL itself. Widened by exactly this
# list plus the `+json` / `+xml` suffixes, and no further: images, PDFs and
# application/octet-stream still raise, because handing a model five megabytes
# of binary is the thing the refusal was for.
_TEXTUAL = frozenset(
    {
        "application/json",
        "application/xml",
        "application/xhtml+xml",
        "application/javascript",
        "application/x-ndjson",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
        "application/sql",
    }
)
# Tags that end a line of prose. Turning them into newlines before the tags are
# stripped is the difference between readable text and one enormous paragraph.
_BREAKS = re.compile(
    r"</(p|div|section|article|li|tr|h[1-6]|blockquote|pre)\s*>|<br\s*/?>",
    re.IGNORECASE,
)


class FetchError(Exception):
    """A refused or failed fetch, with a message fit to show a user."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class Fetched:
    url: str
    title: str
    text: str
    truncated: bool = False


def _is_public(address: str) -> bool:
    """Whether an IP is one this server should be willing to fetch from.

    Loopback reaches LLeMbas and every other local service. Private ranges reach
    the rest of the network the server sits on. Link-local covers cloud metadata
    endpoints, which is where credentials live.
    """
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def check_url(url: str, *, allow_private: bool = False) -> str:
    """Validate a URL and return it normalised. Raises FetchError if refused."""
    try:
        parsed = urlparse(url.strip())
    except ValueError as exc:
        raise FetchError("That does not look like a URL.") from exc

    if parsed.scheme not in ("http", "https"):
        raise FetchError("Only http and https addresses can be fetched.")
    if not parsed.hostname:
        raise FetchError("That URL has no host.")

    if not allow_private:
        try:
            # Resolved, not parsed: a hostname pointing at 127.0.0.1 is the
            # obvious way past a check that only looks at the text of the URL.
            resolved = socket.getaddrinfo(parsed.hostname, None)
        except socket.gaierror as exc:
            raise FetchError(f"Could not resolve {parsed.hostname}.") from exc

        addresses = {info[4][0] for info in resolved}
        # Every address, not any: a name resolving to one public and one private
        # address must not be usable to reach the private one.
        if not addresses or not all(_is_public(address) for address in addresses):
            raise FetchError(
                f"{parsed.hostname} resolves to a private or local address. "
                "An administrator can allow this under Admin → Web search if "
                "fetching from this network is intended."
            )

    return urlunparse(parsed)


def html_to_text(html: str) -> tuple[str, str]:
    """Reduce a page to (title, text)."""
    title_match = _TITLE.search(html)
    title = ""
    if title_match:
        title = " ".join(nh3.clean(title_match.group(1), tags=set()).split())

    body = _DROPPED.sub(" ", html)
    body = _BREAKS.sub("\n", body)
    # nh3 with no allowed tags leaves the text and escapes nothing structural;
    # it is the same sanitiser the rest of the application trusts.
    body = nh3.clean(body, tags=set(), attributes={})

    import html as html_module

    body = html_module.unescape(body)
    lines = [" ".join(line.split()) for line in body.splitlines()]
    # Collapse runs of blank lines, which a stripped page is mostly made of.
    text, blank = [], False
    for line in lines:
        if line:
            text.append(line)
            blank = False
        elif not blank:
            text.append("")
            blank = True

    return title, "\n".join(text).strip()


async def fetch(url: str, *, allow_private: bool = False) -> Fetched:
    """Retrieve a page and reduce it to text.

    Redirects are followed by hand so every hop can be checked. httpx's own
    following would validate the first address and then happily land on
    localhost.
    """
    current = check_url(url, allow_private=allow_private)

    try:
        async with httpx.AsyncClient(
            timeout=TIMEOUT, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                response = await client.get(current)

                if response.is_redirect:
                    location = response.headers.get("location", "")
                    if not location:
                        raise FetchError("That page redirected to nowhere.")
                    current = check_url(
                        str(response.url.join(location)), allow_private=allow_private
                    )
                    continue

                if response.status_code >= 400:
                    raise FetchError(
                        f"{current} returned HTTP {response.status_code}."
                    )
                break
            else:
                raise FetchError("That page redirected too many times.")
    except httpx.RequestError as exc:
        raise FetchError(f"Could not reach {current}: {exc}") from exc

    payload = response.content[:MAX_PAGE_BYTES]
    content_type = response.headers.get("content-type", "")

    bare = content_type.split(";")[0].strip().lower()
    if "html" in content_type or payload[:512].lstrip()[:1] == b"<":
        title, text = html_to_text(payload.decode(response.encoding or "utf-8", "replace"))
    elif (
        content_type.startswith("text/")
        or not content_type
        or bare in _TEXTUAL
        or bare.endswith(("+json", "+xml"))
    ):
        title, text = "", payload.decode(response.encoding or "utf-8", "replace")
    else:
        raise FetchError(
            f"That address is {content_type or 'not text'}, which cannot be saved "
            "as a page. Attach it as a file instead."
        )

    truncated = len(text) > MAX_TEXT_CHARS
    if not text.strip():
        raise FetchError(
            "Nothing readable was found at that address. It may be a page that "
            "builds itself with JavaScript, which this cannot run."
        )

    return Fetched(
        url=current,
        title=title or urlparse(current).netloc or current,
        text=text[:MAX_TEXT_CHARS],
        truncated=truncated,
    )


__all__ = ["FetchError", "Fetched", "check_url", "fetch", "html_to_text"]
