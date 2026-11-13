"""HTML pages in answers (harness spec 3.8.0, `pages.md`): the text side.

A reply stays Markdown. Where pages are switched on, a model may put a whole
HTML document into a fenced block whose info word is `html-view`, with the
page's title after it. This module is everything that reads such a reply *as
text*: it finds the finished blocks, and gives the reply's **projection** --
each page replaced by one line, `[page: Title]` -- which is what search,
titles, summaries, notifications, read-aloud and a client that cannot draw a
page see instead of a screenful of markup.

Pure, and held to `conformance/pages.json` (`tests/test_harness_spec.py`).
Drawing a page is another matter entirely (a sealed frame; see the wiki's
security page) and is not here.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from lembas.services import harness_spec


def spec() -> dict[str, Any]:
    return harness_spec.loop()["pages"]


@dataclass(frozen=True)
class Page:
    title: str
    html: str

    @property
    def digest(self) -> str:
        """What a page is found by: a hash of its title and its source."""
        return hashlib.sha256(f"{self.title}\n{self.html}".encode()).hexdigest()[:32]


_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(\S*)[ \t]*(.*?)[ \t]*$")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.I | re.S)


def _title(said: str, html: str) -> str:
    shape = spec()
    title = " ".join(said.split())
    if not title:
        found = _TITLE.search(html)
        title = " ".join(found.group(1).split()) if found else ""
    title = title or shape["untitled"]
    return title[: int(shape["title_max"])].rstrip()


def fence(text: str) -> tuple[list[Page], str]:
    """`(pages, projection)` of a reply: every **closed** `html-view` block in
    order, and the reply with each replaced by its `[page: title]` line. A
    block still open (the reply is being written, or was cut off) is not a
    page and stays as it is, as does every other fenced block."""
    shape = spec()
    word = shape["fence"]
    lines = (text or "").split("\n")
    pages: list[Page] = []
    out: list[str] = []
    index = 0
    while index < len(lines):
        opened = _OPEN.match(lines[index])
        if not opened:
            out.append(lines[index])
            index += 1
            continue
        marker, info, rest = opened.group(1), opened.group(2), opened.group(3)
        # The block's end: a line of the same character, at least as long,
        # with nothing after it.
        end = None
        for later in range(index + 1, len(lines)):
            closing = lines[later].strip()
            if (
                closing
                and set(closing) == {marker[0]}
                and len(closing) >= len(marker)
                and len(lines[later]) - len(lines[later].lstrip(" ")) <= 3
            ):
                end = later
                break
        if end is None:
            out.extend(lines[index:])
            break
        if info == word:
            html = "\n".join(lines[index + 1 : end])
            page = Page(_title(rest, html), html)
            pages.append(page)
            out.append(shape["projection"].replace("{{title}}", page.title))
        else:
            out.extend(lines[index : end + 1])
        index = end + 1
    return pages, "\n".join(out)


def project(text: str) -> str:
    """A reply as text: its pages as one line each."""
    if not text or spec()["fence"] not in text:
        return text or ""
    return fence(text)[1]


def pages_of(text: str) -> list[Page]:
    if not text or spec()["fence"] not in text:
        return []
    return fence(text)[0]


def note(title: str) -> str:
    """What a client that cannot draw a page shows in its place."""
    return spec()["note"].replace("{{title}}", title)


def offered(*, instance: bool, model: bool, chat: bool, where: str = "") -> bool:
    """Whether the pages block is in a prompt: all three switches on -- the
    instance's, the model's flag, the chat's -- and never for a subagent, the
    phone assistant or an agent chat. A schedule's run has no chat switch: the
    instance's and the model's decide."""
    if where in spec()["never"]:
        return False
    if not (instance and model):
        return False
    return True if where == "scheduled" else bool(chat)


# --- The document a browser is given --------------------------------------------------
#
# A page is shown **static**: its HTML, CSS and inline SVG, and no script. That
# is not the first design. A page with scripts in a sealed frame reaches
# nothing of this application -- but two ways of *sending* cannot be closed
# from a web page's policy (measured in Chromium, `tests/test_html_pages_sealed.py`):
# a peer connection asks a STUN server of the page's choosing (`webrtc 'block'`
# is not enforced), and a `<link rel="dns-prefetch">` -- which a script can make
# at any time -- has the browser look a name up, whatever the policy says. A
# page that anything the model read may have steered would carry a
# conversation out in either. Without scripts the first is gone, and the
# second is closed here: the document is rebuilt from what a parser saw, and
# nothing that names another host is written back.
#
# The rebuilding is deliberately simple, because a clever sanitiser is a
# parser differential waiting to be found: every `<` in what is written comes
# from a tag this code chose to write. Text is escaped; a style sheet's `<`
# is written as a CSS escape; comments, declarations and processing
# instructions are dropped.

# Elements that are dropped with everything inside them, and those dropped
# alone (they have no content, or their content is kept as it is).
_DROP_WITH_CONTENT = frozenset({
    "script", "iframe", "frame", "frameset", "object", "embed", "applet", "noscript",
    "template", "portal", "fencedframe", "audio", "video",
})
_DROP = frozenset({"link", "base", "meta", "source", "track", "param"})
_VOID = frozenset({
    "area", "br", "col", "hr", "img", "input", "wbr",
})
# Attributes whose value is an address. Kept only when it points inside the
# page (`#id`) or is an inline image.
_URL_ATTRIBUTES = frozenset({
    "href", "src", "xlink:href", "action", "formaction", "poster", "data", "background",
    "srcset", "imagesrcset", "ping", "cite", "manifest", "codebase", "longdesc", "usemap",
    "icon", "lowsrc", "dynsrc", "profile", "classid", "archive", "attributionsrc",
})
_NAME = re.compile(r"^[a-z][a-z0-9:_.-]*$")
_INLINE_IMAGE = re.compile(r"^data:image/(png|gif|jpeg|webp|svg\+xml|avif)[;,]", re.I)
_COLOUR = re.compile(
    r"^(#[0-9a-fA-F]{3,8}"
    r"|(rgb|rgba|hsl|hsla|oklch|oklab|lab|lch|color)\([0-9a-zA-Z.,%/ +-]{1,80}\))$"
)


def _attribute(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(
        ">", "&gt;"
    )


def _text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _local(value: str) -> bool:
    value = value.strip()
    return value.startswith("#") or bool(_INLINE_IMAGE.match(value))


class _Rebuild(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skipping = ""  # the dropped element being skipped, with its content
        self.depth = 0
        self.raw = False  # inside <style>: text goes out as CSS

    def handle_starttag(self, tag: str, attrs) -> None:
        if self.skipping:
            if tag == self.skipping:
                self.depth += 1
            return
        if not _NAME.match(tag) or tag in _DROP:
            return
        if tag in _DROP_WITH_CONTENT:
            self.skipping, self.depth = tag, 1
            return
        kept = []
        for name, value in attrs:
            name = (name or "").lower()
            if not _NAME.match(name) or name.startswith("on"):
                continue
            value = "" if value is None else value
            if name in _URL_ATTRIBUTES and not _local(value):
                continue
            kept.append(f' {name}="{_attribute(value)}"')
        self.out.append(f"<{tag}{''.join(kept)}>")
        self.raw = tag == "style"

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if not self.skipping and tag not in _VOID and _NAME.match(tag) and tag not in _DROP:
            self.out.append(f"</{tag}>")
        if self.skipping == tag:
            self.handle_endtag(tag)
        self.raw = False

    def handle_endtag(self, tag: str) -> None:
        if self.skipping:
            if tag == self.skipping:
                self.depth -= 1
                if self.depth <= 0:
                    self.skipping = ""
            return
        self.raw = False
        if _NAME.match(tag) and tag not in _DROP and tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.skipping:
            return
        if self.raw:
            # A style sheet: never a `<` (a browser may be reading it as
            # markup where this parser read text), and never an `@import`.
            css = data.replace("<", "\\3c ")
            self.out.append(re.sub(r"@import\b", "@-no-import", css, flags=re.I))
        else:
            self.out.append(_text(data))


def theme(values: dict[str, str] | None) -> dict[str, str]:
    """The frame's variables as given by the page that shows it, each kept
    only when it is plainly a colour: it is written into a style sheet."""
    out: dict[str, str] = {}
    for name in spec()["variables"]:
        value = " ".join(str((values or {}).get(name.removeprefix("--page-")) or "").split())
        if _COLOUR.match(value):
            out[name] = value
    return out


def document(page: Page, colours: dict[str, str] | None = None) -> str:
    """The document a browser is given for a page: rebuilt, static, with the
    frame's variables set on its root."""
    rebuilt = _Rebuild()
    rebuilt.feed(page.html)
    rebuilt.close()
    variables = "".join(f"{name}:{value};" for name, value in theme(colours).items())
    base = (
        f":root{{{variables}color-scheme:light dark}}"
        "html{background:var(--page-bg,Canvas);color:var(--page-ink,CanvasText);"
        "font-family:system-ui,sans-serif}"
    )
    return (
        "<!doctype html>\n<meta charset=\"utf-8\">"
        f"<title>{_text(page.title)}</title><style>{base}</style>\n" + "".join(rebuilt.out)
    )


__all__ = [
    "Page", "document", "fence", "note", "offered", "pages_of", "project", "spec", "theme",
]
