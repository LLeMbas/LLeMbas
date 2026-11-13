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

import functools
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


# --- The document a client is given ---------------------------------------------------
#
# **A page is static** (pages.md §6): HTML, CSS and inline SVG -- nothing that
# runs, nothing that reaches anywhere. A frame with scripts on cannot be sealed
# by policy alone (measured in Chromium, `tests/test_html_pages_sealed.py`): a
# peer connection asks a STUN server of the page's choosing, and a
# `<link rel="dns-prefetch">` -- which needs no script at all -- has the
# browser look a name up whatever the policy says. So the rule is about the
# document, and the document shown is **rebuilt from the spec's allow-list**
# (`loop.json` `pages.static`): parsed, and written back with the listed
# elements and attributes and the two kinds of address, and nothing else.
#
# There is one list, the spec's, and two readers of it here: `static_faults`
# says what in a page breaks the rule (the conformance cases), `document`
# builds what is shown. Both walk the same parse.
#
# The rebuilding is deliberately simple, because a clever sanitiser is a
# parser differential waiting to be found: every `<` in what is written comes
# from a tag this code chose to write. Text is escaped; a style sheet's `<`
# is written as a CSS escape; comments, declarations and processing
# instructions are dropped.

# Elements with no content: one that is not allowed is dropped by itself.
# Any other element that is not allowed goes with everything inside it.
_VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
    "source", "track", "wbr",
})
_NAME = re.compile(r"^[a-z][a-z0-9:_.-]*$")
_CSS_URL = re.compile(r"url\(\s*(['\"]?)([^)'\"]*)\1\s*\)", re.I)
_CSS_IMPORT = re.compile(r"@import\b", re.I)
_COLOUR = re.compile(
    r"^(#[0-9a-fA-F]{3,8}"
    r"|(rgb|rgba|hsl|hsla|oklch|oklab|lab|lch|color)\([0-9a-zA-Z.,%/ +-]{1,80}\))$"
)


def static_rules() -> dict[str, Any]:
    return spec()["static"]


@functools.cache
def _allowed() -> tuple[frozenset[str], frozenset[str], tuple[str, ...], frozenset[str], Any]:
    rules = static_rules()
    return (
        frozenset(name.lower() for name in rules["elements"]),
        frozenset(name.lower() for name in rules["attributes"]),
        tuple(rules["attribute_prefixes"]),
        frozenset(rules["address_attributes"]),
        re.compile(f"^data:image/(?:{'|'.join(rules['image_types'])});base64,", re.I),
    )


def address_allowed(value: str) -> bool:
    """One of the two addresses a page may hold: `#fragment`, or an inline
    picture of a listed type."""
    value = (value or "").strip()
    return value.startswith("#") or bool(_allowed()[4].match(value))


def css_faults(css: str) -> list[str]:
    """What a style sheet would fetch: `@import`, and a `url()` that is not
    an allowed address."""
    out = []
    if _CSS_IMPORT.search(css or ""):
        out.append("css:import")
    if any(not address_allowed(found.group(2)) for found in _CSS_URL.finditer(css or "")):
        out.append("css:url")
    return out


def _attribute(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(
        ">", "&gt;"
    )


def _text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _Walk(HTMLParser):
    """One parse of a page, for both readers: `faults` is what breaks the
    rule, in the order first met; `out` the document rebuilt without it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.faults: list[str] = []
        self.skipping = ""  # the element being dropped with its content
        self.depth = 0
        self.style: list[str] | None = None  # the text of the <style> being read

    def _say(self, fault: str) -> None:
        if fault not in self.faults:
            self.faults.append(fault)

    def handle_starttag(self, tag: str, attrs) -> None:
        elements, attributes, prefixes, addresses, _ = _allowed()
        if self.skipping and tag == self.skipping:
            self.depth += 1
        if not _NAME.match(tag) or tag not in elements:
            self._say(f"element:{tag}")
            if not self.skipping and tag not in _VOID:
                self.skipping, self.depth = tag, 1
            return
        kept = []
        for name, value in attrs:
            name = (name or "").lower()
            value = "" if value is None else value
            if not _NAME.match(name) or not (
                name in attributes or name.startswith(prefixes)
            ):
                self._say(f"attribute:{name}")
                continue
            if name in addresses and not address_allowed(value):
                self._say(f"address:{name}")
                continue
            if name == "style" and (found := css_faults(value)):
                for fault in found:
                    self._say(fault)
                continue
            kept.append(f' {name}="{_attribute(value)}"')
        if tag == "style":
            self.style = []
            return
        if not self.skipping:
            self.out.append(f"<{tag}{''.join(kept)}>")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag == "style" and self.style is not None:
            css, self.style = "".join(self.style), None
            found = css_faults(css)
            for fault in found:
                self._say(fault)
            # A style sheet that fetches is dropped whole. One that is kept
            # never holds a `<`: a browser may be reading as markup what this
            # parser read as text.
            if not found and not self.skipping:
                self.out.append(f"<style>{css.replace('<', chr(92) + '3c ')}</style>")
            return
        if self.skipping:
            if tag == self.skipping:
                self.depth -= 1
                if self.depth <= 0:
                    self.skipping = ""
            return
        if _NAME.match(tag) and tag in _allowed()[0] and tag not in _VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.style is not None:
            self.style.append(data)
        elif not self.skipping:
            self.out.append(_text(data))


def _walk(html: str) -> _Walk:
    walk = _Walk()
    walk.feed(html or "")
    walk.close()
    return walk


def static_faults(html: str) -> list[str]:
    """What makes a page not static, each thing once, in the order first met
    (`element:<name>`, `attribute:<name>`, `address:<attribute>`,
    `css:import`, `css:url`); none: it is static as written."""
    return _walk(html).faults


# The frame's variables by the short names they travel under in a query.
COLOURS = ("bg", "ink", "muted", "accent", "line")


# --- The three switches (pages.md §4) ----------------------------------------------
SCOPE_KEY = "html_pages"


def instance_on(db) -> bool:
    """The instance's switch: off until an administrator turns it on."""
    from lembas.services import settings_store

    return bool(settings_store.get_group(db, settings_store.GENERAL).get("html_pages_enabled"))


def where_of(chat) -> str:
    """Which of the spec's places a chat is, for `offered`: `agent_chat`,
    `subagent`, `scheduled`, or "" for a person's own chat."""
    from lembas.db.models import KIND_AGENT, KIND_TASK

    if chat is None:
        return ""
    if chat.kind == KIND_AGENT:
        return "agent_chat"
    if getattr(chat, "parent_chat_id", None):
        return "subagent"
    if chat.kind == KIND_TASK or getattr(chat, "unattended", False):
        return "scheduled"
    # A chat the person's phone app has written into is the phone assistant's
    # from then on (`phone_action` is offered there, spec 3.6.0): spoken, and
    # shown on a small screen.
    if getattr(chat, "phone_message_id", ""):
        return "phone_assistant"
    return ""


def available(db, chat, model) -> bool:
    """Whether a chat on this model could have pages at all -- the instance
    and the model say so, and it is not a place that never has them. What
    decides whether the chat's switch is shown."""
    return (
        instance_on(db)
        and bool(getattr(model, "html_pages", False))
        and where_of(chat) not in spec()["never"]
    )


def chat_on(chat) -> bool:
    """The chat's own switch: off until its person turns it on."""
    return (getattr(chat, "scope_json", None) or {}).get(SCOPE_KEY) is True


def on(db, chat, model) -> bool:
    """Whether the model answering this chat is offered pages right now."""
    return offered(
        instance=instance_on(db),
        model=bool(getattr(model, "html_pages", False)),
        chat=chat_on(chat),
        where=where_of(chat),
    )


def block() -> str:
    """What the model is told (`prompts/blocks/html-pages.md`)."""
    return harness_spec.prompt("blocks/html-pages.md")


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
    """The document a client is given for a page: rebuilt from the spec's
    allow-list, static, with the frame's variables set on its root."""
    variables = "".join(f"{name}:{value};" for name, value in theme(colours).items())
    base = (
        f":root{{{variables}color-scheme:light dark}}"
        "html{background:var(--page-bg,Canvas);color:var(--page-ink,CanvasText);"
        "font-family:system-ui,sans-serif}"
    )
    return (
        "<!doctype html>\n<meta charset=\"utf-8\">"
        f"<title>{_text(page.title)}</title><style>{base}</style>\n"
        + "".join(_walk(page.html).out)
    )


__all__ = [
    "COLOURS", "SCOPE_KEY", "Page", "available", "block", "chat_on", "document", "fence",
    "instance_on", "note", "offered", "on", "pages_of", "project", "spec", "static_faults",
    "theme", "where_of",
]
