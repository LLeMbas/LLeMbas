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
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

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


# What `html_to_markdown` reads a page as, the way the LLeMbas CLI's
# `web_fetch` does with turndown: ATX headings, fenced code, `-` bullets, `_`
# and `**` for emphasis, and these elements dropped with everything in them.
_MD_DROPPED = frozenset(
    {"head", "title", "script", "style", "noscript", "iframe", "svg", "form", "nav", "footer",
     "template"}
)
_MD_BLOCKS = frozenset(
    {
        "p", "div", "section", "article", "main", "header", "aside", "figure",
        "figcaption", "table", "dl", "dd", "dt", "details", "summary", "address",
    }
)
_MD_VOID = frozenset({"br", "hr", "img", "input", "meta", "link", "area", "source", "wbr"})


_TAG_ENDS = (">", " ", "\n", "\t", "\r", "/")


def _element(html: str, lower: str, tag: str) -> str | None:
    """The first `<tag …>…</tag>`, found by searching rather than by a lazy
    regex, which is quadratic on a page of unclosed tags (the CLI's reason)."""
    start = lower.find(f"<{tag}")
    # `<main` must be the whole tag name, not the start of `<mainframe`.
    after = len(tag) + 1
    while start >= 0 and lower[start + after : start + after + 1] not in _TAG_ENDS:
        start = lower.find(f"<{tag}", start + 1)
    if start < 0:
        return None
    end = lower.find(f"</{tag}>", start)
    return None if end < 0 else html[start : end + len(tag) + 3]


class _Markdown(HTMLParser):
    """HTML to markdown, plainly: structure, links and code kept, nothing
    clever. A stack of buffers, so a link or a quote can wrap what it held."""

    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.parts: list[list[str]] = [[]]
        self.links: list[str] = []
        self.lists: list[list] = []  # [ordered, next number]
        self.skip = 0
        self.pre = 0

    def _out(self, text: str) -> None:
        self.parts[-1].append(text)

    def _push(self) -> None:
        self.parts.append([])

    def _pop(self) -> str:
        return "".join(self.parts.pop()) if len(self.parts) > 1 else ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip:
            if tag in _MD_DROPPED and tag not in _MD_VOID:
                self.skip += 1
            return
        if tag in _MD_DROPPED:
            self.skip = 1
            return
        a = dict(attrs)
        if self.pre:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._out(f"\n\n{'#' * int(tag[1])} ")
        elif tag in _MD_BLOCKS:
            self._out("\n\n")
        elif tag == "br":
            self._out("\n")
        elif tag == "hr":
            self._out("\n\n---\n\n")
        elif tag == "pre":
            self.pre = 1
            self._out("\n\n```\n")
        elif tag in ("ul", "ol"):
            if not self.lists:
                self._out("\n")
            self.lists.append([tag == "ol", 1])
        elif tag == "li":
            indent = "  " * max(len(self.lists) - 1, 0)
            if self.lists and self.lists[-1][0]:
                marker = f"{self.lists[-1][1]}. "
                self.lists[-1][1] += 1
            else:
                marker = "- "
            self._out(f"\n{indent}{marker}")
        elif tag == "blockquote":
            self._push()
        elif tag == "a":
            self.links.append(a.get("href") or "")
            self._push()
        elif tag in ("strong", "b"):
            self._out("**")
        elif tag in ("em", "i"):
            self._out("_")
        elif tag == "code":
            self._out("`")
        elif tag == "img":
            src = (a.get("src") or "").strip()
            if src and not src.startswith("data:"):
                alt = " ".join((a.get("alt") or "").split())
                self._out(f"![{alt}]({urljoin(self.base, src)})")
        elif tag == "tr":
            self._out("\n")
        elif tag in ("td", "th"):
            self._out(" | ")

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag in _MD_DROPPED:
                self.skip -= 1
            return
        if self.pre:
            if tag == "pre":
                self.pre = 0
                self._out("\n```\n\n")
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") or tag in _MD_BLOCKS:
            self._out("\n\n")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self._out("\n\n" if not self.lists else "")
        elif tag == "blockquote":
            inner = _tidy(self._pop())
            if inner:
                quoted = "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))
                self._out(f"\n\n{quoted}\n\n")
        elif tag == "a":
            text = " ".join(self._pop().split())
            href = (self.links.pop() if self.links else "").strip()
            if text and href and not href.startswith(("#", "javascript:", "mailto:")):
                self._out(f"[{text}]({urljoin(self.base, href)})")
            else:
                self._out(text)
        elif tag in ("strong", "b"):
            self._out("**")
        elif tag in ("em", "i"):
            self._out("_")
        elif tag == "code":
            self._out("`")
        elif tag == "tr":
            self._out(" |")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.pre:
            self._out(data)
            return
        text = re.sub(r"\s+", " ", data)
        # A lone space is kept only between two words, never after a break.
        last = self.parts[-1][-1] if self.parts[-1] else "\n"
        if text.strip() or (text and not last.endswith((" ", "\n"))):
            self._out(text)

    def result(self) -> str:
        while len(self.parts) > 1:
            inner = self._pop()
            self._out(inner)
        return _tidy("".join(self.parts[0]))


_LIST_LINE = re.compile(r"\s*(?:- |> |\d+\. )")


def _tidy(text: str) -> str:
    """Trailing spaces off every line outside code, and no more than one blank
    line in a row -- what a stripped page is mostly made of."""
    out, fenced = [], False
    for line in text.split("\n"):
        if line.startswith("```"):
            fenced = not fenced
            out.append(line.strip())
        elif fenced:
            out.append(line.rstrip())
        elif _LIST_LINE.match(line):
            out.append(line.rstrip())  # the indent is the nesting
        else:
            out.append(" ".join(line.split()))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def html_to_markdown(html: str, base: str = "") -> tuple[str, str]:
    """Reduce a page to (title, markdown), the way the LLeMbas CLI's `web_fetch`
    reads one: `<main>` if there is one, else `<article>`, else the page."""
    title_match = _TITLE.search(html)
    title = " ".join(nh3.clean(title_match.group(1), tags=set()).split()) if title_match else ""
    import html as html_module

    title = html_module.unescape(title)
    lower = html.lower()
    body = _element(html, lower, "main") or _element(html, lower, "article") or html
    parser = _Markdown(base)
    parser.feed(body)
    parser.close()
    return title, parser.result()


async def fetch(url: str, *, allow_private: bool = False, markdown: bool = False) -> Fetched:
    """Retrieve a page and reduce it to text -- or, with `markdown`, to
    markdown, which is what a model reading it is given (`web_fetch`).

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
        decoded = payload.decode(response.encoding or "utf-8", "replace")
        if markdown:
            title, text = html_to_markdown(decoded, current)
        else:
            title, text = html_to_text(decoded)
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


__all__ = ["FetchError", "Fetched", "check_url", "fetch", "html_to_markdown", "html_to_text"]
