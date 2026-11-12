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
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound

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
    "div": {"class"},
    "td": {"align"},
    "th": {"align"},
}

# javascript: and data: URLs are the obvious injection route through a link.
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


# Guessing a language asks every lexer Pygments has to score the text, so its
# cost grows with the text: 40 ms for a few kilobytes, seconds for a pasted log.
# The opening of a block says as much about its language as the whole of it,
# so only that much is shown to the guess -- and the answer is remembered,
# because the same block is rendered again on every load of its chat.
GUESS_SAMPLE = 4000

# Past this a block is escaped rather than highlighted.
HIGHLIGHT_MAX = 200_000


@functools.lru_cache(maxsize=1024)
def _guessed_name(sample: str) -> str:
    try:
        lexer = guess_lexer(sample)
    except (ClassNotFound, ValueError):
        return ""
    return lexer.aliases[0] if lexer.aliases else ""


def _guessed(sample: str):
    """The lexer Pygments guesses for the opening of a block, or None."""
    name = _guessed_name(sample)
    if not name:
        return None
    try:
        return get_lexer_by_name(name, stripall=False)
    except (ClassNotFound, ValueError):
        return None


def _render_fence(tokens, idx, _options, env) -> str:
    """Render a fenced code block.

    This replaces the renderer's `fence` rule outright rather than using
    markdown-it's `highlight` option, because that option re-wraps whatever it
    is given in <pre><code> unless the string already starts with "<pre" --
    which would nest a second <pre> inside the wrapper this returns.
    """
    token = tokens[idx]
    code = token.content

    language = (token.info or "").strip().split()[0] if token.info else ""

    lexer = None
    if env.get("live"):
        # A reply still being written: plain, escaped code. Highlighting --
        # and above all guessing a language -- re-ran on the whole tail many
        # times a second and was most of what made a long reply lag; the
        # finished bubble is rendered again, highlighted, at the end.
        #
        # A long block is sent in parts (`LiveRender`): `--cont` on a part
        # that carries on from the one before, `--more` on one that is carried
        # on after it, so the stylesheet can draw them as the one block they are.
        extra = ""
        if env.get("cont") and not any(t.type == "fence" for t in tokens[:idx]):
            extra += " code-block--cont"
        if env.get("more") and not any(t.type == "fence" for t in tokens[idx + 1 :]):
            extra += " code-block--more"
        return _code_block(nh3.clean_text(code), language, extra)
    if language:
        try:
            lexer = get_lexer_by_name(language, stripall=False)
        except (ClassNotFound, ValueError):
            lexer = None
    elif code.strip() and len(code) > 80:
        # Guessing is only worth it for a decent sample; on two lines of text
        # Pygments guesses confidently and wrongly.
        lexer = _guessed(code[:GUESS_SAMPLE])

    if lexer is not None and len(code) > HIGHLIGHT_MAX:
        # A pasted log of half a megabyte: escaped, not coloured. The label
        # stays, since it was asked for or already worked out.
        return _code_block(
            nh3.clean_text(code), language or (lexer.aliases[0] if lexer.aliases else "")
        )
    if lexer is None:
        body = nh3.clean_text(code)
        label = language
    else:
        body = highlight(code, lexer, _FORMATTER)
        label = language or (lexer.aliases[0] if lexer.aliases else "")

    return _code_block(body, label)


def _code_block(body: str, label: str, extra: str = "") -> str:
    label_html = (
        f'<div class="code-block__label">{nh3.clean_text(label)}</div>' if label else ""
    )
    return (
        f'<div class="code-block{extra}">{label_html}'
        f'<pre class="code-block__pre"><code>{body}</code></pre></div>'
    )


def _source(text: str) -> tuple[str, list[str]]:
    """The text as markdown-it will see it, and its lines.

    markdown-it normalises line endings before it parses, so `token.map` counts
    lines in *that* text. Normalising here first means the line numbers and the
    lines being read are the same lines.
    """
    src = text.replace("\r\n", "\n").replace("\r", "\n").replace("\0", "\ufffd")
    return src, src.split("\n")


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


def render_markdown(
    text: str, *, live: bool = False, cont: bool = False, more: bool = False
) -> str:
    """Markdown to safe HTML, ready to drop into a message bubble.

    `live` is a reply still being written: code is not highlighted (see
    `_render_fence`). `cont` and `more`, live only: the text's first code block
    carries on from an earlier part, its last is carried on in a later one."""
    if not text:
        return ""

    src, lines = _source(text)
    html = _parser().render(src, {"lines": lines, "live": live, "cont": cont, "more": more})
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


class LiveRender:
    """The markdown of a text that only grows, at the cost of what is new.

    A reply's open tail was rendered whole on every frame -- 60 ms at 20k
    characters, ten times a second, on the one event loop that also serves
    Stop and every other page: O(n²) over a long answer. Here the text is cut
    after its last finished block (a blank line, outside any open code fence);
    what is before the cut is **settled**: rendered once, and -- through
    `feed` -- sent once. Only the block still being written is rendered again.
    Blocks are rendered on their own, so a loose list can show as two until
    the reply's final render joins it -- the live view only, which the `done`
    frame replaces.

    **A long open code block settles too, in parts.** A fence has no blank
    line to cut at until it closes, so a 400-line file being written stayed
    whole in the part that is re-rendered and re-sent on every frame. Once the
    open block has grown past `FENCE_CHUNK` it is cut after its last complete
    line; each part is rendered as a block of its own, marked so the
    stylesheet joins them (`code-block--cont`, `--more`).

    One per follower and per tail: a tail that does not start with what was
    kept (a round closed, the tail began again) starts over.
    """

    # How much of an open code block is left to be re-sent each frame before
    # its complete lines are settled.
    FENCE_CHUNK = 1200

    def __init__(self) -> None:
        self._kept = ""
        self._html = ""
        # The fence open at the end of what is kept: (marker, info) or ("", "").
        self._fence: tuple[str, str] = ("", "")

    @property
    def settled(self) -> str:
        """The HTML of everything settled so far."""
        return self._html

    def render(self, text: str) -> str:
        """The whole tail's HTML (the settled part and the rest together)."""
        _, rest, _ = self.feed(text)
        return self._html + rest

    def feed(self, text: str) -> tuple[str, str, bool]:
        """Take the tail as it now stands: (added, rest, restarted).

        `added` is the HTML that became settled with this call, to be appended
        to what was settled before; `rest` is the HTML of the part still being
        written, replacing the last one. `restarted`: the tail began again, so
        what was settled before is void and `added` is everything settled."""
        restarted = False
        if not text.startswith(self._kept):
            restarted = bool(self._kept)
            self._kept, self._html, self._fence = "", "", ("", "")
        added = ""
        cut = self._settled(text)
        if cut > len(self._kept):
            added, self._fence = self._piece(text[len(self._kept) : cut])
            self._html += added
            self._kept = text[:cut]
        rest = text[len(self._kept) :]
        return added, (self._piece(rest)[0] if rest.strip() else ""), restarted

    def _source(self, piece: str) -> str:
        """A piece as Markdown that stands alone: the fence it starts inside
        opened again in front of it."""
        marker, info = self._fence
        return f"{marker}{info}\n{piece}" if marker else piece

    def _piece(self, piece: str) -> tuple[str, tuple[str, str]]:
        """A piece's HTML, and the fence left open at its end."""
        source = self._source(piece)
        after = open_fence(source)
        if after[0]:
            source = f"{source}{'' if source.endswith(chr(10)) else chr(10)}{after[0]}"
        html = render_markdown(
            source, live=True, cont=bool(self._fence[0]), more=bool(after[0])
        )
        return html, after

    def _settled(self, text: str) -> int:
        """The end of the last finished block after what is kept, or what is kept."""
        start = len(self._kept)
        at = text.rfind("\n\n")
        while at >= start:
            cut = at + 2
            if not open_fence(self._source(text[start:cut]))[0]:
                return cut
            at = text.rfind("\n\n", 0, at)
        # Nothing ended at a blank line. A code block still open and grown
        # long: settle its complete lines.
        if len(text) - start > self.FENCE_CHUNK:
            cut = text.rfind("\n") + 1
            if cut > start:
                source = self._source(text[start:cut])
                # Inside the block, past its opening line: a cut that would
                # leave the opener alone would render it as an empty block.
                if open_fence(source)[0] and source.count("\n") > 1:
                    return cut
        return start


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
    stripped = html.unescape(_TAG.sub(" ", rendered))

    # Paragraph breaks survive as a single newline: speech models use them as a
    # pause, and a wall of one line is read without any.
    stripped = _WHITESPACE.sub("\n", stripped)
    lines = [" ".join(line.split()) for line in stripped.splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_SPEAKABLE]
