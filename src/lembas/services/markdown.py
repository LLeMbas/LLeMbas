"""Render assistant messages from Markdown to sanitised HTML.

Rendering happens on the server, in Python, so there is no JavaScript Markdown
library to vendor and the streamed and final views cannot disagree about how
something should look.

The output is sanitised with nh3 (Rust ammonia). Model output is untrusted
input: it routinely contains HTML, and a model can be talked into emitting a
script tag, so this is a real boundary and not a formality.
"""

from __future__ import annotations

import functools
import html
import re

import nh3
from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, get_lexer_for_filename, guess_lexer
from pygments.util import ClassNotFound

from lembas.services import prototypes

# Class-based highlighting; the colours come from theme tokens in chat.css, so
# code blocks follow the active theme instead of carrying their own palette.
_FORMATTER = HtmlFormatter(nowrap=True, classprefix="pg-")

ALLOWED_TAGS = {
    "p", "br", "hr", "div", "span",
    "strong", "em", "del", "sub", "sup", "mark",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "img",
    # A prototype's source sits under one of these, closed. Harmless in a
    # model's own Markdown too: neither element has an attribute allowed.
    "details", "summary",
}

ALLOWED_ATTRIBUTES = {
    # "rel" is intentionally absent: nh3 rejects it here when link_rel is set,
    # because link_rel below is what writes it.
    "a": {"href", "title", "target"},
    # `width` and `height` so a sized image reserves its box before it has
    # loaded: with both, the browser lays the image out at its aspect ratio at
    # once, and the paragraph under it does not drop by the picture's height a
    # moment later -- mid-stream, that drop is the transcript jumping. Kept to
    # plain pixel counts by `_attribute_filter`.
    "img": {"src", "alt", "title", "width", "height"},
    "code": {"class"},
    "pre": {"class"},
    "span": {"class"},
    # The `data-proto-*` four are the placeholder a prototype fence becomes (see
    # `_render_prototype`). A model can write the same attributes by hand, and
    # gains nothing by it: `prototypes.js` checks the digest's shape, and the
    # route serves only a block that is really in this message with that digest.
    "div": {"class", "data-proto", "data-proto-hash", "data-proto-title", "data-proto-lines"},
    "details": {"class"},
    "summary": {"class"},
    "td": {"align"},
    "th": {"align"},
}

# javascript: and data: URLs are the obvious injection route through a link.
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def _render_fence(tokens, idx, _options, env) -> str:
    """Render a fenced code block.

    This replaces the renderer's `fence` rule outright rather than using
    markdown-it's `highlight` option, because that option re-wraps whatever it
    is given in <pre><code> unless the string already starts with "<pre" --
    which would nest a second <pre> inside the wrapper this returns.
    """
    token = tokens[idx]
    code = token.content

    wanted = prototypes.parse_info(token.info)
    if wanted is not None:
        return _render_prototype(token, *wanted, closed=_closed(token, env.get("lines")))

    language = (token.info or "").strip().split()[0] if token.info else ""

    lexer = None
    if language:
        try:
            lexer = get_lexer_by_name(language, stripall=False)
        except (ClassNotFound, ValueError):
            lexer = None
    elif code.strip():
        # Guessing is only worth it for a decent sample; on two lines of text
        # Pygments guesses confidently and wrongly.
        try:
            lexer = guess_lexer(code) if len(code) > 80 else None
        except (ClassNotFound, ValueError):
            lexer = None

    if lexer is None:
        body = nh3.clean_text(code)
        label = language
    else:
        body = highlight(code, lexer, _FORMATTER)
        label = language or (lexer.aliases[0] if lexer.aliases else "")

    return _code_block(body, label)


def _code_block(body: str, label: str) -> str:
    label_html = (
        f'<div class="code-block__label">{nh3.clean_text(label)}</div>' if label else ""
    )
    return (
        f'<div class="code-block">{label_html}'
        f'<pre class="code-block__pre"><code>{body}</code></pre></div>'
    )


def _render_prototype(token, kind: str, given: str, *, closed: bool) -> str:
    """A prototype fence, as the placeholder `prototypes.js` mounts a frame on.

    Nothing here runs anything. The HTML the model wrote appears in exactly one
    form -- highlighted, which escapes it -- under a closed `<details>`, so the
    source can be read and copied. The frame itself is built in the browser,
    because `<iframe>` is not a tag this sanitiser lets through and must not
    become one: everything else a model writes passes the same allowlist.

    A block still being written gets no digest: its contents change with every
    token, and a frame mounted on it would reload a dozen times a second. It
    says how far it has got instead, and becomes a real placeholder the moment
    its closing fence arrives.

    The words on the card -- "Prototype", "Open", "Writing…" -- are not here.
    This runs inside a reply's stream as well as on a page, and the stream has
    no reader's language to hand; `prototypes.js` fills them from a template the
    page rendered in the right one.
    """
    code = token.content
    title = html.escape(prototypes.title_for(code, given), quote=True)
    head = f'<div class="proto__head"><span class="proto__title">{title}</span></div>'
    if not closed:
        lines = code.count("\n")
        return (
            f'<div class="proto proto--{kind} proto--writing" data-proto="{kind}" '
            f'data-proto-title="{title}" data-proto-lines="{lines}">{head}</div>'
        )

    source = _code_block(highlight(code, _html_lexer(), _FORMATTER), "html")
    return (
        f'<div class="proto proto--{kind}" data-proto="{kind}" '
        f'data-proto-hash="{prototypes.digest(code)}" data-proto-title="{title}">'
        f'{head}<div class="proto__stage"></div>'
        f'<details class="proto__source"><summary class="proto__summary">HTML</summary>'
        f"{source}</details></div>"
    )


@functools.lru_cache(maxsize=1)
def _html_lexer():
    return get_lexer_by_name("html", stripall=False)


# A closing fence once a container's markers are off the front of the line: the
# same character as the opener, at least as many, and nothing after.
_CONTAINER = re.compile(r"^[ \t>]*")
_CLOSER = re.compile(r"^(`{3,}|~{3,})[ \t]*$")


def _closed(token, lines: list[str] | None) -> bool:
    """Whether a fence token has its closing line, or simply ran out of text.

    markdown-it closes an unterminated fence at the end of the document and
    records no difference, so this reads the line the token ends on. That is the
    one fact that separates a prototype still streaming from one that is done.
    With no lines to read -- a caller that did not go through `_source` -- the
    answer is yes, which renders exactly what this did before prototypes.
    """
    if lines is None or not token.map or not token.markup:
        return True
    start, end = token.map
    if end - 1 <= start or end - 1 >= len(lines):
        return False
    found = _CLOSER.match(_CONTAINER.sub("", lines[end - 1]))
    if found is None:
        return False
    marker = found.group(1)
    return marker[0] == token.markup[0] and len(marker) >= len(token.markup)


def _source(text: str) -> tuple[str, list[str]]:
    """The text as markdown-it will see it, and its lines.

    markdown-it normalises line endings before it parses, so `token.map` counts
    lines in *that* text. Normalising here first means the line numbers and the
    lines being read are the same lines.
    """
    src = text.replace("\r\n", "\n").replace("\r", "\n").replace("\0", "\ufffd")
    return src, src.split("\n")


def fences(text: str):
    """Every fenced block in `text`, with whether it was closed.

    The prototype route's way of finding a block: the same parser the renderer
    uses, so the two cannot disagree about what a block contains.
    """
    if not text:
        return
    src, lines = _source(text)
    for token in _parser().parse(src, {"lines": lines}):
        if token.type == "fence":
            yield token, _closed(token, lines)


def highlight_code(text: str, filename: str = "") -> str:
    """A whole file, class-highlighted, for the canvas panel to read.

    Here rather than in a module of its own because `markdown.py` is where
    pygments lives and `_FORMATTER` is already configured: a second formatter
    would mean a second set of class names and a second thing to theme, and the
    `.pg-*` rules would then be right about code fences and wrong about files.

    Pygments' `HtmlFormatter` escapes what it is given, which is what makes this
    the one call the canvas templates mark `|safe`. The content came off
    somebody else's disk, so that property is the whole of the argument -- if
    the lexer cannot be found the text is escaped by hand instead, never passed
    through.

    Chooses by filename, because that is what the canvas has: a lexer guessed
    from contents is confidently wrong on short files, and there is no fence
    info string here to read a language out of.
    """
    if not text:
        return ""

    lexer = None
    if filename:
        try:
            lexer = get_lexer_for_filename(filename, stripall=False)
        except (ClassNotFound, ValueError):
            lexer = None
    if lexer is None and len(text) > 200:
        try:
            lexer = guess_lexer(text)
        except (ClassNotFound, ValueError):
            lexer = None

    body = nh3.clean_text(text) if lexer is None else highlight(text, lexer, _FORMATTER)
    return f'<pre class="canvas__code"><code>{body}</code></pre>'


@functools.lru_cache(maxsize=1)
def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"linkify": True, "typographer": False})
    md.enable(["table", "strikethrough", "linkify"])
    md.renderer.rules["fence"] = _render_fence
    return md


# An image dimension is a count of pixels and nothing else. Anything a model
# writes that is not one -- a percentage, a unit, a word -- is dropped rather
# than passed to the browser to interpret.
_DIMENSION = re.compile(r"^\d{1,5}$")


def _attribute_filter(element: str, attribute: str, value: str) -> str | None:
    if element == "img" and attribute in ("width", "height"):
        return value if _DIMENSION.match(value) else None
    return value


def render_markdown(text: str) -> str:
    """Markdown to safe HTML, ready to drop into a message bubble."""
    if not text:
        return ""

    src, lines = _source(text)
    html = _parser().render(src, {"lines": lines})
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        attribute_filter=_attribute_filter,
        url_schemes=ALLOWED_URL_SCHEMES,
        # Anything opened from a model's output is untrusted; noopener stops it
        # reaching back through window.opener.
        link_rel="nofollow noopener noreferrer",
    )


# A fence opener: three or more backticks or tildes at the start of a line,
# optionally indented, with whatever info string follows. Deliberately shallow --
# it does not know about lists, block quotes or indented code, and it does not
# have to. See `open_fence`.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(.*)$")


def open_fence(text: str) -> tuple[str, str]:
    """The marker and info string of a fence left open, or ``("", "")``.

    A reply is rendered in pieces now -- one per step, split where the model
    stopped to call a tool -- and a fence opened in one piece and never closed
    would run to the end of that piece and then leave every later fence in the
    reply paired up wrongly. `services/steps.py` uses this to close such a fence
    at the end of its own segment and reopen it at the start of the next.

    Deliberately not a second Markdown parser. It has to be right about one
    thing: a model that opened a fence and then called a tool. Where it is
    unsure it says "no fence", which renders exactly as the whole-text version
    always did.
    """
    marker = ""
    info = ""
    for line in text.splitlines():
        found = _FENCE.match(line)
        if found is None:
            continue
        fence, rest = found.group(1), found.group(2).strip()
        if not marker:
            marker, info = fence, rest
        elif fence[0] == marker[0] and len(fence) >= len(marker) and not rest:
            # A closer is the same character, at least as long, and carries no
            # info string. Anything else inside an open fence is just text.
            marker, info = "", ""
    return marker, info


# A mention is `@` followed by a run of non-space, claimed only at the start of
# the text or after whitespace. That last part is the whole rule: without it
# every email address in a message becomes a highlighted file reference, which
# is both wrong and ugly. It matches what composer.js recognises while typing,
# and the two must stay in step or the box and the transcript disagree.
_MENTION = re.compile(r"(?:(?<=\s)|^)@([^\s@]+)")


def highlight_tokens(text: str) -> str:
    """A user's own message, escaped, with `@mentions` marked.

    User turns have no render step at all -- the template prints the column and
    relies on `white-space: pre-wrap` -- so this is it, and it must escape
    before it injects or it is an XSS hole in the one place a person controls
    the bytes exactly.

    Only mentions. A `/command` never survives to a message: commands are
    intercepted in the composer and never posted, so anything beginning with a
    slash in a transcript is text somebody meant as text, and marking it as a
    command would be marking it as something it is not.
    """
    if not text:
        return ""
    escaped = html.escape(text, quote=False)
    # Applied to the *escaped* string, so the span is the only markup that can
    # exist. `@` and the path characters are untouched by html.escape, and a
    # `&amp;` it produced contains no whitespace -- which is why the pattern is
    # anchored on whitespace rather than on a character class.
    return _MENTION.sub(r'<span class="tok-mention">@\1</span>', escaped)


def escape_text(text: str) -> str:
    """Escape a plain-text run for insertion as HTML element content.

    Used for user messages and for partial assistant text mid-stream, where the
    content is not yet complete enough to parse as Markdown.

    html.escape rather than nh3.clean_text: escaping the three structural
    characters is all that is needed for a text node, and it escapes character
    by character, so escaping a stream chunk-by-chunk gives the same result as
    escaping the whole string at once. nh3.clean_text also escapes spaces and
    slashes, which triples the size of a streamed token for no benefit.
    """
    return html.escape(text, quote=False)


# Code blocks are dropped whole rather than read out. A speech model given a
# code fence pronounces every bracket and underscore, which is unlistenable and
# takes longer than the prose it was buried in.
#
# Matched on <pre> rather than on the .code-block wrapper: the wrapper also
# contains a label div, so a non-greedy match for its closing tag stops at the
# label's and leaves the code behind. <pre> cannot nest, so this is exact.
_CODE_BLOCK = re.compile(r"<pre\b[^>]*>.*?</pre>", re.DOTALL)
_CODE_LABEL = re.compile(r"<div class=\"code-block__label\">.*?</div>", re.DOTALL)
# A prototype's "HTML" disclosure: a control's name, not something the reply says.
_PROTO_SUMMARY = re.compile(r"<summary class=\"proto__summary\">.*?</summary>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t]*\n\s*\n\s*")

# Speech endpoints reject or truncate very long inputs, and a reply long enough
# to hit this is not one anybody is listening to in full.
MAX_SPEAKABLE = 8000


def speakable_text(text: str) -> str:
    """Markdown reduced to something worth reading aloud.

    Goes through the renderer rather than stripping the Markdown source
    directly, so tables, lists and links come out as their text instead of as
    punctuation, and there is one definition of what a message *says*.
    """
    if not text:
        return ""

    rendered = _CODE_LABEL.sub(" ", _CODE_BLOCK.sub("\n", render_markdown(text)))
    rendered = _PROTO_SUMMARY.sub(" ", rendered)
    stripped = html.unescape(_TAG.sub(" ", rendered))

    # Paragraph breaks survive as a single newline: speech models use them as a
    # pause, and a wall of one line is read without any.
    stripped = _WHITESPACE.sub("\n", stripped)
    lines = [" ".join(line.split()) for line in stripped.splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_SPEAKABLE]
