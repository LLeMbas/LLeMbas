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
    page and stays as it is, as does every other fenced block.

    Found by the Markdown renderer's own parse (`markdown.find_pages`): there
    is one finder, so a page has one digest wherever it is asked for."""
    from lembas.services.markdown import find_pages

    return find_pages(text)


def project(text: str) -> str:
    """A reply as text: its pages as one line each."""
    return fence(text)[1]


def pages_of(text: str) -> list[Page]:
    return fence(text)[0]


def note(title: str) -> str:
    """What a client that cannot draw a page shows in its place."""
    return spec()["note"].replace("{{title}}", title)


def offered(*, instance: bool, model: bool, chat: bool, where: str = "") -> bool:
    """Whether the pages block is in a prompt (pages.md §4): the instance's
    switch, the model's flag and the chat's switch, all three -- and never
    for a subagent, the phone assistant or an agent chat. A schedule's run
    has no chat switch: the instance's and the model's decide.

    This is the spec's rule on plain values, and `conformance/pages.json`
    drives exactly it. What this server passes in is its own policy, decided
    in `model_can` and `chat_on`: an instance that offers pages on every
    model, and a chat whose switch starts on."""
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
_COLOUR = re.compile(
    r"^(#[0-9a-fA-F]{3,8}"
    r"|(rgb|rgba|hsl|hsla|oklch|oklab|lab|lch|color)\([0-9a-zA-Z.,%/ +-]{1,80}\))$"
)

# What a page may be at most. A page is a chart or a table, not a document
# store: past any of these it is not shown at all (`TOO_LARGE`), because a
# browser asked to lay out a million elements, or an SVG that references
# itself a billion times over, is a tab that does not answer.
MAX_SOURCE = 512 * 1024  # bytes of what the model wrote
MAX_ELEMENTS = 20_000
MAX_DEPTH = 200
# How many shapes `<use>` may add up to once every reference is followed --
# ten `<use>` in a group that ten more groups use, ten deep, is 10^10.
MAX_USE_EXPANSION = 5_000
TOO_LARGE = "limit:size"

_ASCII_SPACE = " \t\n\r\f"
# What a start tag closes without being told: `<li>one<li>two` is two items,
# not one inside the other -- and a list of a thousand such is not a thousand
# deep.
_CELLS = frozenset({"td", "th"})
_ROWS = _CELLS | {"tr"}
_GROUPS = _ROWS | {"thead", "tbody", "tfoot"}
_IMPLIED = {
    "p": frozenset({"p"}), "li": frozenset({"li"}),
    "dt": frozenset({"dt", "dd"}), "dd": frozenset({"dt", "dd"}),
    "td": _CELLS, "th": _CELLS, "tr": _ROWS,
    "thead": _GROUPS, "tbody": _GROUPS, "tfoot": _GROUPS,
}


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
        re.compile(
            f"^data:image/(?:{'|'.join(rules['image_types'])});base64,[A-Za-z0-9+/]*={{0,2}}$",
            re.I,
        ),
    )


def address(value: str) -> str | None:
    """An address as it may be written into a page, or None: `#fragment`, or
    an inline picture of a listed type, spelled exactly. What is returned is
    what was checked -- ASCII white space trimmed, nothing else touched: a
    value a browser would read differently from this check is not kept."""
    value = (value or "").strip(_ASCII_SPACE)
    if re.fullmatch(r"#[A-Za-z0-9_.:-]*", value) or _allowed()[4].match(value):
        return value
    return None


def address_allowed(value: str) -> bool:
    return address(value) is not None


# --- CSS: strict, because it cannot be parsed here ------------------------------------
#
# A page's CSS must fetch nothing, and that has to be true of the *text*, not
# only of the frame it is shown in: an app shows the same document in a frame
# of its own. CSS has too many ways to spell an address for a pattern to find
# them all (`u\72l(…)`, `image-set("…")`, a quote inside a string, `@\69mport`),
# so the rule is not "find the bad ones" but "keep only what is plainly none":
#
# - no backslash at all: an escape can spell anything;
# - no `&` and no `<`: where a browser reads a style sheet as markup (inside
#   SVG) those are character references and tags, and this parser read text;
# - no at-rule but a short list -- never `@import`, `@font-face`, `@namespace`;
# - no function that takes an address (`image-set`, `image`, `cross-fade`,
#   `element`, `paint`, `src`, a typed `attr`), and `url(…)` only as
#   `url(#fragment)` or an inline picture spelled exactly;
# - and after those are set aside, the word `url` nowhere.
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_CSS_AT = re.compile(r"@\s*([a-zA-Z-]*)")
_CSS_AT_ALLOWED = frozenset({"media", "supports", "keyframes", "container", "layer"})
_CSS_FUNCTION = re.compile(r"([a-zA-Z_-][a-zA-Z0-9_-]*)\s*\(")
_CSS_ADDRESS_FUNCTIONS = frozenset({
    "image-set", "-webkit-image-set", "image", "cross-fade", "-webkit-cross-fade", "element",
    "-moz-element", "paint", "src", "type", "local", "format", "env",
})
_CSS_URL_OK = re.compile(
    r"url\(\s*(?:\"(?P<d>[^\"()\\]*)\"|'(?P<s>[^'()\\]*)'|(?P<b>[^\"'()\\\s]*))\s*\)", re.I
)
_CSS_URL_WORD = re.compile(r"(?<![a-zA-Z0-9_-])url(?![a-zA-Z0-9_-])", re.I)


def css_faults(css: str) -> list[str]:
    """What stops a piece of CSS -- a style sheet, a `style` attribute, a
    presentation attribute's value -- being kept: `css:import` (an at-rule
    that is not allowed) and `css:url` (an address, or anything that could
    hide one). Empty: it fetches nothing."""
    css = css or ""
    out: list[str] = []
    bare = _CSS_COMMENT.sub(" ", css)
    # A comment may not be what holds two halves of a word apart, either.
    joined = _CSS_COMMENT.sub("", css)
    if any(
        found.group(1).lower() not in _CSS_AT_ALLOWED
        for text in (bare, joined)
        for found in _CSS_AT.finditer(text)
    ):
        out.append("css:import")
    hidden = any(mark in css for mark in ("\\", "&", "<")) or "/*" in bare
    for text in (bare, joined):
        if hidden:
            break
        if any(
            found.group(1).lower() in _CSS_ADDRESS_FUNCTIONS
            for found in _CSS_FUNCTION.finditer(text)
        ):
            hidden = True
            break
        rest = text
        for found in _CSS_URL_OK.finditer(text):
            inner = found.group("d") or found.group("s") or found.group("b") or ""
            if address(inner) == inner and inner:
                rest = rest.replace(found.group(0), " ", 1)
        if _CSS_URL_WORD.search(rest):
            hidden = True
    if hidden:
        out.append("css:url")
    return out


def _attribute(text: str) -> str:
    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(
        ">", "&gt;"
    )


def _text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Attributes that are words for a person or for a script-less page's own
# data, never read as CSS or as an address: everything else is.
def _is_words(name: str) -> bool:
    return name in ("alt", "title", "lang", "datetime") or name.startswith(("aria-", "data-"))


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
        self.style_kept = True
        self.elements = 0
        self.open: list[tuple[str, str]] = []  # (tag, id) of the elements open now
        self.svg = 0  # how many <svg> are open
        self.too_large = False
        # `<use>`: where each stands in `out`, the ids it is inside, its target.
        self.uses: list[tuple[int, tuple[str, ...], str]] = []

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
        self.elements += 1
        closes = _IMPLIED.get(tag)
        while closes and self.open and self.open[-1][0] in closes:
            self.out.append(f"</{self.open.pop()[0]}>")
        if self.elements > MAX_ELEMENTS or len(self.open) >= MAX_DEPTH:
            self.too_large = True
            return
        kept = []
        ident = ""
        target = ""
        for name, value in attrs:
            name = (name or "").lower()
            value = "" if value is None else value
            if not _NAME.match(name) or not (
                name in attributes or name.startswith(prefixes)
            ):
                self._say(f"attribute:{name}")
                continue
            if name in addresses:
                checked = address(value)
                if checked is None:
                    self._say(f"address:{name}")
                    continue
                value = checked
                target = checked[1:] if checked.startswith("#") else target
            elif not _is_words(name) and (found := css_faults(value)):
                # A `style` attribute, and every presentation attribute too:
                # `fill`, `clip-path`, `marker-end` take `url(…)` as well.
                for fault in found:
                    self._say(fault)
                continue
            if name == "id":
                ident = value
            kept.append(f' {name}="{_attribute(value)}"')
        if tag == "style":
            # Inside SVG a browser reads a style sheet as markup and decodes
            # character references in it; this parser read it as raw text.
            # The two cannot be made to agree, so there is none there.
            self.style, self.style_kept = [], not self.svg
            if self.svg:
                self._say("element:style")
            return
        if tag not in _VOID:
            self.open.append((tag, ident))
        if tag == "svg":
            self.svg += 1
        if not self.skipping:
            if tag == "use":
                self.uses.append(
                    (len(self.out), tuple(i for _, i in self.open if i), target)
                )
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
            # A style sheet that could fetch is dropped whole.
            if not found and self.style_kept and not self.skipping:
                self.out.append(f"<style>{css}</style>")
            return
        if self.skipping:
            if tag == self.skipping:
                self.depth -= 1
                if self.depth <= 0:
                    self.skipping = ""
            return
        if not _NAME.match(tag) or tag not in _allowed()[0] or tag in _VOID:
            return
        # Close what is open down to this tag, as a browser would; an end tag
        # for nothing that is open is dropped.
        names = [name for name, _ in self.open]
        if tag not in names:
            return
        while self.open:
            name, _ = self.open.pop()
            if name == "svg":
                self.svg -= 1
            self.out.append(f"</{name}>")
            if name == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.style is not None:
            self.style.append(data)
        elif not self.skipping:
            self.out.append(_text(data))

    def use_expansion(self) -> int:
        """How many elements the page's `<use>` add up to, references
        followed (and a reference that comes back to itself counted as past
        any limit)."""
        inside: dict[str, list[str]] = {}
        for _, ancestors, target in self.uses:
            for ident in ancestors:
                inside.setdefault(ident, []).append(target)
        seen: dict[str, int] = {}

        def weight(ident: str, path: frozenset[str]) -> int:
            if ident in path:
                return MAX_USE_EXPANSION + 1
            if ident in seen:
                return seen[ident]
            total = 1
            for target in inside.get(ident, ()):
                total += weight(target, path | {ident})
                if total > MAX_USE_EXPANSION:
                    break
            seen[ident] = total
            return total

        total = 0
        for _, _, target in self.uses:
            total += weight(target, frozenset())
            if total > MAX_USE_EXPANSION:
                break
        return total


def _walk(html: str) -> _Walk:
    walk = _Walk()
    if len((html or "").encode("utf-8", "replace")) > MAX_SOURCE:
        walk.too_large = True
    else:
        walk.feed(html or "")
        walk.close()
    if not walk.too_large and walk.use_expansion() > MAX_USE_EXPANSION:
        # References that multiply: none of them is drawn.
        for index, _, _ in walk.uses:
            walk.out[index] = "<g>"
        walk.out = [part if part != "</use>" else "</g>" for part in walk.out]
        walk._say("limit:use")
    if walk.too_large:
        walk._say(TOO_LARGE)
        walk.out = ["<p>This page is too large to show.</p>"]
    else:
        # Whatever was left open is closed: the document is whole.
        walk.out.extend(f"</{name}>" for name, _ in reversed(walk.open))
    return walk


def static_faults(html: str) -> list[str]:
    """What makes a page not static, each thing once, in the order first met
    (`element:<name>`, `attribute:<name>`, `address:<attribute>`,
    `css:import`, `css:url`; and this server's own `limit:size`,
    `limit:use`); none: it is static as written."""
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


# Which models an instance that has pages on offers them on. The spec's flag
# (`models/schema.json` `html_pages`) says a model *can* write a working page,
# and is false unless somebody set it -- which made the feature three switches
# in three places before one page appeared. So the instance says how it reads
# the flag: `all` (the start: one switch is enough, every model is taken to be
# able) or `chosen` (only the models an administrator ticked). The flag itself
# keeps the spec's meaning, and `/v1/models` still sends it as it is stored.
MODELS_ALL = "all"
MODELS_CHOSEN = "chosen"
MODELS_KEY = "html_pages_models"


def models_scope(db) -> str:
    """`all` or `chosen`; anything else stored reads as `all`."""
    from lembas.services import settings_store

    stored = settings_store.get_group(db, settings_store.GENERAL).get(MODELS_KEY)
    return MODELS_CHOSEN if stored == MODELS_CHOSEN else MODELS_ALL


def model_can(db, model) -> bool:
    """The model's part of `offered`, as this instance reads it: its own flag,
    or every model where the instance says so."""
    if model is None:
        return False
    return models_scope(db) == MODELS_ALL or bool(getattr(model, "html_pages", False))


def model_counts(db) -> tuple[int, int]:
    """`(able, all)`: how many of the instance's enabled chat models are
    offered pages as things stand, of how many -- what the admin page says."""
    from sqlalchemy import select

    from lembas.db.models import Model

    rows = [
        row for row in db.scalars(select(Model))
        if getattr(row, "enabled", True) and (getattr(row, "kind", "chat") or "chat") == "chat"
    ]
    return sum(1 for row in rows if model_can(db, row)), len(rows)


def available(db, chat, model) -> bool:
    """Whether a chat on this model could have pages at all -- the instance
    and the model say so, and it is not a place that never has them. What
    decides whether the chat's switch can be moved."""
    return not why_not(db, chat, model)


def why_not(db, chat, model) -> str:
    """Why a chat on this model cannot have pages, or "" when it can: `off`
    (the instance's switch), one of the spec's places that never have them
    (`agent_chat`, `subagent`, `phone_assistant`), or `model` (the instance
    offers pages on chosen models only, and this is not one). The chat's
    settings say it in words rather than hiding the switch."""
    if not instance_on(db):
        return "off"
    where = where_of(chat)
    if where in spec()["never"]:
        return where
    return "" if model_can(db, model) else "model"


def chat_on(chat) -> bool:
    """The chat's own switch. It starts **on**: absent means on, as for every
    key of `Chat.scope_json`, and a person who does not want pages in a chat
    switches them off there (`False`). An instance with pages on then needs
    nothing more from anybody."""
    return (getattr(chat, "scope_json", None) or {}).get(SCOPE_KEY) is not False


def on(db, chat, model) -> bool:
    """Whether the model answering this chat is offered pages right now."""
    return offered(
        instance=instance_on(db),
        model=model_can(db, model),
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
    allow-list, static, with the frame's variables set on its root. Kept for
    a while (`_document`): a frame asks for the same page again on every
    load, and a rebuild is a parse."""
    return _document(page.title, page.html, tuple(sorted(theme(colours).items())))


@functools.lru_cache(maxsize=64)
def _document(
    title: str, html: str, colours: tuple[tuple[str, str], ...], plain: bool = False
) -> str:
    variables = "".join(f"{name}:{value};" for name, value in colours)
    base = (
        f":root{{{variables}color-scheme:light dark}}"
        "html{background:var(--page-bg,Canvas);color:var(--page-ink,CanvasText);"
        "font-family:system-ui,sans-serif}"
    )
    # A file somebody wrote as a page of its own (`shown_document`) is drawn
    # as a browser draws a page: light, in its own fonts, none of the frame's
    # variables -- it was not written for them.
    style = "<style>:root{color-scheme:light}</style>" if plain else f"<style>{base}</style>"
    return (
        "<!doctype html>\n<meta charset=\"utf-8\">"
        f"<title>{_text(title)}</title>{style}\n" + "".join(_walk(html).out)
    )


# --- An HTML file a model showed (`show`), in the conversation ------------------------
#
# The web page draws a shown HTML file in the reply, not in a window beside it.
# Such a file is not a page of an answer -- the model wrote it as a file, with
# whatever it liked in it, and the viewer runs it in the scripted sealed frame
# (`security/sealed.policy`) when somebody opens it. In the conversation nobody
# opened anything, and a frame with scripts can send out what is on the page
# (the two ways `page_policy` documents). So there are two cases:
#
# - the file is **still**: rebuilt as a page of an answer is
#   (`document`'s rules), it loses nothing that would have worked. It is shown
#   at once, rebuilt, under the page policy -- it reaches nothing, sends
#   nothing;
# - anything else (a script, a handler, a form, a canvas, a style sheet the
#   rebuild would drop): the place says so and offers **Run page**, which
#   loads the file as the viewer does, on that click and not before.
#
# What the rebuild drops without loss: `meta`, `link` and `base` (under the
# sealed policy a linked sheet never loaded anyway) and the address of a link
# (a sealed frame cannot be navigated anywhere).
_NO_LOSS = frozenset({"element:meta", "element:link", "element:base", "address:href"})


def shown_still(html: str) -> bool:
    """Whether a shown HTML file can be drawn in the conversation at once: it
    is static as written, but for what a sealed frame never honoured."""
    if len((html or "").encode("utf-8", errors="replace")) > MAX_SOURCE:
        return False
    return all(fault in _NO_LOSS for fault in static_faults(html or ""))


def shown_document(name: str, html: str) -> str:
    """A shown HTML file rebuilt as a still page: the document `document`
    gives for a page of an answer, without the frame's variables."""
    return _document(name, html or "", (), True)


__all__ = [
    "COLOURS", "MODELS_ALL", "MODELS_CHOSEN", "MODELS_KEY", "SCOPE_KEY", "Page", "available",
    "block", "chat_on", "document", "fence", "instance_on", "model_can", "model_counts",
    "models_scope", "note", "offered", "on", "pages_of", "project", "shown_document",
    "shown_still", "spec", "static_faults",
    "theme", "where_of", "why_not",
]
