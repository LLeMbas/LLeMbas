"""Prototypes: HTML a model writes, run in the reader's browser and nowhere else.

A model asks for one by writing a fenced block whose language is
`prototype-chat` or `prototype-page`. The Markdown renderer turns that block
into a placeholder carrying a digest of its contents, and the browser mounts an
`<iframe>` on it whose address is `api/prototypes.py`'s route -- which reads the
same message, finds the block with the same digest, and serves it with a
Content-Security-Policy that seals it off.

**Why a route and not `srcdoc`.** A `srcdoc` frame inherits the policy of the
page that holds it, so the day this application sends a Content-Security-Policy
of its own (the security pass in the roadmap), every prototype would stop
running -- or the application's policy would have to allow inline script for
the sake of a frame. A document served from its own address carries its own
header and inherits nothing, and the header's `sandbox` directive makes it an
opaque origin even when somebody opens the address in a tab of its own, which an
`iframe sandbox` attribute alone would not.

**Why a digest and not a position.** A reply is rendered in pieces -- one per
step -- so "the third prototype in this message" is a count every piece would
have to agree on. The digest is computed from the block alone, by the renderer
and by the route alike, so neither needs to know what else the message holds.

**What it may reach.** Nothing, by default: no request leaves the frame, and
every resource has to be inline or a `data:` URL. An administrator can open
`https:` for libraries and images (`prototypes_network`). It can never reach
this application -- the frame is a different origin, so it has no cookie, no
DOM of ours and no same-origin request -- and it cannot open a window or
navigate the page around it.

**What it can still do**, said out loud because a sandbox is not a promise of
silence: it can navigate *its own frame* to another address, which a link in an
ordinary reply can do too. With the network closed that is the one way out, and
it carries only what the model wrote into the page.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Chat, User
from lembas.security import permissions
from lembas.services import settings_store

PERMISSION = "chat.prototypes"

# The scope-menu switch. A gate name like the tool families', so a chat that
# turns prototypes off stores it exactly where it stores "no web search", and
# `tools.scoped_off` answers both.
GATE = "prototypes"

KIND_CHAT = "chat"
KIND_PAGE = "page"

# The fence languages, and what each means. `prototype` alone is accepted as the
# inline kind: a model that forgot the suffix wanted something shown, and the
# inline frame is the smaller thing to get wrong.
LANGUAGES = {
    "prototype-chat": KIND_CHAT,
    "prototype-page": KIND_PAGE,
    "prototype": KIND_CHAT,
}

# Long enough to be unique within a message by a wide margin, short enough to
# sit in a URL without wrapping a log line.
DIGEST_LENGTH = 16
DIGEST = re.compile(rf"^[0-9a-f]{{{DIGEST_LENGTH}}}$")

MAX_TITLE = 80

# The largest block served. A prototype is a page somebody wrote in one reply,
# and a megabyte of it is a model stuck in a loop rather than a design.
MAX_BYTES = 1_000_000

_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Prototype:
    kind: str
    title: str
    code: str
    digest: str


def parse_info(info: str) -> tuple[str, str] | None:
    """`"prototype-page Bakery landing"` -> `("page", "Bakery landing")`.

    None for any other fence, which then renders as the code block it is.
    """
    words = (info or "").strip().split(None, 1)
    if not words:
        return None
    kind = LANGUAGES.get(words[0].lower())
    if kind is None:
        return None
    return kind, (words[1] if len(words) > 1 else "")


def digest(code: str) -> str:
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def title_for(code: str, given: str = "") -> str:
    """The name on the card: what the fence said, else the page's own <title>."""
    title = _SPACE.sub(" ", given or "").strip()
    if not title:
        found = _TITLE_TAG.search(code or "")
        if found:
            title = _SPACE.sub(" ", re.sub(r"<[^>]+>", "", found.group(1))).strip()
    return title[:MAX_TITLE]


def info(info_string: str, code: str) -> dict[str, str] | None:
    """A fence's info string and body read as a prototype: `{kind, title}`, or
    None for the code block it is. `parse_info` and `title_for` together, in
    the shape `harness_spec/conformance/prototype.json` pins."""
    parsed = parse_info(info_string)
    if parsed is None:
        return None
    kind, given = parsed
    return {"kind": kind, "title": title_for(code, given)}


def find(text: str, wanted: str) -> Prototype | None:
    """The block in `text` whose digest is `wanted`, if there is one.

    Parsed with the renderer's own parser, so a block the renderer saw is a
    block this finds -- the same tokens, the same contents, the same digest. A
    block that is still being written is not served: its digest would change
    with the next token, and half a page is not the page.
    """
    if not text or not DIGEST.match(wanted or ""):
        return None
    from lembas.services import markdown

    for token, closed in markdown.fences(text):
        parsed = parse_info(token.info)
        if parsed is None or not closed:
            continue
        if digest(token.content) != wanted:
            continue
        kind, given = parsed
        return Prototype(
            kind=kind, title=title_for(token.content, given), code=token.content, digest=wanted
        )
    return None


# --- Who gets them --------------------------------------------------------------
def enabled(db: DBSession) -> bool:
    return bool(settings_store.get(db, "prototypes_enabled"))


def network(db: DBSession) -> bool:
    return bool(settings_store.get(db, "prototypes_network"))


def allowed(db: DBSession, user: User | None) -> bool:
    """Whether this person may see prototypes at all. The route's question."""
    if user is None or not enabled(db):
        return False
    return permissions.has(db, user, PERMISSION)


def offered(db: DBSession, user: User | None, chat: Chat | None = None) -> bool:
    """Whether the model answering in this chat is told it can write one.

    The route's question plus two about the chat. A helper's chat is never
    offered them: what a helper writes is read by the model that sent it, as
    text, and a frame nobody will ever look at is a page of HTML in somebody's
    context window for nothing.
    """
    if not allowed(db, user):
        return False
    if chat is None:
        return True
    if getattr(chat, "parent_chat_id", None):
        return False
    from lembas.services import tools as tools_service

    # With the owner's own default for the chat's model counted.
    return GATE not in tools_service.effective_off(db, chat, user)


# --- The document ----------------------------------------------------------------
# Everything a frame may load, by directive. The network setting adds `https:`
# to the ones a page loads things through, and to nothing else: a frame, a form
# and a <base> stay closed either way.
_LOADS = ("script-src", "style-src", "img-src", "font-src", "media-src", "connect-src")


def policy(*, allow_network: bool) -> str:
    """The Content-Security-Policy every prototype is served under.

    `sandbox` first, and it is the half that matters: `allow-scripts` without
    `allow-same-origin` makes the document an opaque origin, so it holds no
    cookie of ours and cannot read or call this application -- including when
    the address is opened in a tab of its own, where no iframe attribute would
    apply. `allow-forms` because a prototype with a form in it calls
    `preventDefault` on submit, and a sandbox without it never fires the event;
    `form-action 'none'` is what stops one actually being sent anywhere.
    """
    sources = {
        "script-src": "'unsafe-inline' 'unsafe-eval' data: blob:",
        "style-src": "'unsafe-inline' data: blob:",
        "img-src": "data: blob:",
        "font-src": "data: blob:",
        "media-src": "data: blob:",
        "connect-src": "'none'",
    }
    if allow_network:
        for name in _LOADS:
            current = sources[name]
            sources[name] = "https:" if current == "'none'" else f"{current} https:"
    parts = [
        "sandbox allow-scripts allow-forms",
        "default-src 'none'",
        *(f"{name} {value}" for name, value in sources.items()),
        "worker-src blob:",
        "frame-src 'none'",
        "object-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        # Only this application may put it in a frame. Not a security boundary
        # for the reader -- the document is sealed whoever frames it -- but a
        # page of somebody's conversation has no business inside another site.
        "frame-ancestors 'self'",
    ]
    return "; ".join(parts)


# Runs first in every prototype, before anything the model wrote.
#
# Two jobs. It tells the frame around it how tall the page is, because an
# opaque origin cannot be measured from outside and an inline prototype should
# be as tall as what it draws. And it takes the reader's theme when the page
# sends it, as `--lembas-*` variables on the root, so a chart drawn in
# `var(--lembas-ink)` matches the conversation it sits in.
#
# Messages are accepted only from the parent window, and only these two shapes.
# What it sends is a number. Neither direction carries anything worth reading,
# which is why `"*"` is an acceptable target: an opaque origin has no name to
# address it by.
_BOOT = """(function () {
  var root = document.documentElement;
  var last = 0;
  function size() {
    var body = document.body;
    var height = Math.ceil(Math.max(
      body ? body.scrollHeight : 0,
      body ? body.getBoundingClientRect().bottom : 0
    ));
    if (height === last) return;
    last = height;
    try { parent.postMessage({ lembasPrototype: "size", height: height }, "*"); } catch (e) {}
  }
  addEventListener("message", function (event) {
    if (event.source !== parent || !event.data || event.data.lembasPrototype !== "theme") return;
    var tokens = event.data.tokens || {};
    for (var name in tokens) {
      if (!/^[a-z0-9-]+$/.test(name)) continue;
      root.style.setProperty("--lembas-" + name, String(tokens[name]));
    }
    if (event.data.scheme === "dark" || event.data.scheme === "light") {
      root.style.colorScheme = event.data.scheme;
    }
  });
  function watch() {
    size();
    if (window.ResizeObserver && document.body) new ResizeObserver(size).observe(document.body);
  }
  if (document.readyState === "loading") addEventListener("DOMContentLoaded", watch);
  else watch();
  addEventListener("load", size);
  try { parent.postMessage({ lembasPrototype: "ready" }, "*"); } catch (e) {}
})();"""

SCHEMES = ("dark", "light")


def document(code: str, *, scheme: str = "") -> str:
    """The page a frame is given: our head, then exactly what the model wrote.

    Prepended rather than spliced into the model's own <head>. A browser parses
    a second doctype as nothing, folds a second <html> or <body> tag's
    attributes into the ones it already has, and keeps a <style> or <meta> that
    turns up in the body -- so a full document and a bare fragment both come out
    as one page, without this module parsing HTML to find where a head ends.

    `color-scheme` is what lets an inline prototype be transparent. A frame
    whose scheme differs from the page around it is painted with an opaque
    backdrop, so a dark conversation with a light frame in it shows a white box
    behind every chart that did not set its own background.
    """
    meta = ""
    if scheme in SCHEMES:
        meta = f'<meta name="color-scheme" content="{scheme}">'
    return (
        "<!doctype html>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"{meta}"
        f"<script>{_BOOT}</script>\n"
        f"{code}"
    )


def refusal(message: str) -> str:
    """What a frame shows instead, when there is nothing it may show.

    A page rather than an error status alone: the frame is the only place the
    reader is looking, and an empty rectangle says nothing about why.
    """
    return (
        '<!doctype html><meta charset="utf-8">'
        "<style>body{font-family:system-ui,sans-serif;color:var(--lembas-ink-muted)}</style>"
        f"<script>{_BOOT}</script>"
        f"<p>{html.escape(message)}</p>"
    )


def filename_for(title: str) -> str:
    """A download name: the title, lower-cased and hyphenated, or `prototype`."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")[:60]
    return f"{slug or 'prototype'}.html"
