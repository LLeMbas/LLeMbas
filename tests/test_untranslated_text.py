"""No reader-facing English is left in a template outside `t()`.

`test_translations.py` keeps the catalogues honest about the strings that *are*
wrapped. This keeps the other half: text typed straight into a template, which
renders in English on a page that is otherwise Slovak. Such fragments -- the
sidebar's PINNED MODELS and CHATS, every settings tab, the account card's
labels, the flash that says "language" -- are invisible to every other test,
because English is what an English test expects.

So this reads every template the way a browser would and lists what a reader
would see that did not go through `t()`/`tm()`:

* **text nodes** outside `<script>`, `<style>`, `<code>`, `<pre>`, `<kbd>`, `<svg>`;
* the **attributes a reader sees or hears** -- `title`, `placeholder`,
  `aria-label`, `alt`, `label`, the confirm and prompt texts the dialogs show,
  and the `value` of an `<input type="submit">`;
* **string literals inside `{{ … }}` / `{% … %}`** that read as prose -- the
  `{{ "Agent chats" if … else "Chats" }}` that hid two of the sidebar's labels.

What remains is either translated or on `ALLOWED` with the reason it is not
prose: a product's own name, a keystroke, a unit, an example value, a value
the server reads. Adding to `ALLOWED` is a decision; the default is `t()`.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import lembas

TEMPLATES = Path(lembas.__file__).parent / "web/templates"

# (template, the text the scan reports) -> why it stays as it is.
ALLOWED: dict[tuple[str, str], str] = {
    ("_macros.html", "LL"): "the wordmark, drawn in two colours",
    ("_macros.html", "bas"): "the wordmark, drawn in two colours",
    ("admin/_mcp_row.html", "MCP"): "the protocol's name, beside its version",
    ("admin/general.html", "LLeMbas"): "the product's name, as the empty name's placeholder",
    ("admin/audit.html", "auth"): "an example of an action prefix to filter by",
    ("admin/search.html", "fc-..."): "the shape of a Firecrawl key",
    ("admin/tool_detail.html", "https://api.example.com/weather/"): "an example URL",
    ("admin/tool_detail.html", '{"city": "Minas Tirith"}'): "example arguments, JSON",
    ("chat/_inspector_body.html", "ms"): "a unit",
    ("chat/_metrics.html", "~ tok/s"): "a unit",
    ("chat/_model_picker.html", "CTX"): "the context-window abbreviation beside a number",
    ("settings.html", "CTX"): "the context-window abbreviation beside a number",
    ("chat/_terminal.html", "Ctrl+Shift+C / V"): "keystrokes",
    ("device.html", "ABCD-EFGH"): "the shape of a device code",
    ("settings.html", "ABCD-EFGH"): "the shape of a device code",
    ("agents/_device_browse.html", "lembas remote add <dir>"): "a command, typed as it is",
    ("chat/_composer.html", "Plan"): "a mode's name, the same in every language (Working notes)",
    ("chat/_plan.html", "Plan"): "a phase title compared against, not shown: the one a "
    "version-1 plan got",
    ("chat/_plan.html", "Work"): "a phase title compared against, not shown: the one a todo "
    "list became",
}

JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
_QUOTED = r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
# String-aware, so a literal with `{{name}}` in it does not end the expression.
EXPR = re.compile(
    r"\{\{(?:" + _QUOTED + r"""|[^"'}]|\}(?!\}))*\}\}"""
    r"|\{%(?:" + _QUOTED + r"""|[^"'%]|%(?!\}))*%\}""",
    re.S,
)
LITERAL = re.compile(r"""(?P<q>["'])(?P<s>(?:\\.|(?!(?P=q)).)*?)(?P=q)""", re.S)
WORD = re.compile(r"[A-Za-z]{2,}")
PROSE = re.compile(r"^[A-Z][a-z]+(\s|$|[.,:…?!])|\s[a-z]+\s|[.?!…]$")
SEEN_ATTRIBUTES = {
    "title", "placeholder", "aria-label", "alt", "label", "aria-description",
    "hx-confirm", "hx-prompt", "data-confirm", "data-confirm-button", "data-confirm-title",
    "data-confirm-label", "data-prompt", "data-prompt-title", "data-prompt-label",
}
SKIPPED = {"script", "style", "svg", "code", "kbd", "pre"}
VOID = {"br", "input", "img", "meta", "link", "hr", "source", "use", "path", "circle",
        "rect", "line", "polyline", "polygon", "wbr", "col"}


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.open: list[str] = []
        self.found: list[tuple[int, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.open.append(tag)
        named = dict(attrs)
        for key, value in attrs:
            if value is None:
                continue
            seen = key in SEEN_ATTRIBUTES or (
                key == "value" and tag == "input" and named.get("type") in ("submit", "button")
            )
            text = value.replace("\x00", " ").strip()
            if seen and WORD.search(text):
                self.found.append((self.getpos()[0], text))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.open and self.open[-1] == tag:
            self.open.pop()

    def handle_endtag(self, tag):
        if tag in self.open:
            while self.open and self.open.pop() != tag:
                pass

    def handle_data(self, data):
        if any(tag in SKIPPED for tag in self.open):
            return
        text = " ".join(data.replace("\x00", " ").split())
        # `{% block body_attrs %} data-authenticated="true"{% endblock %}` is an
        # attribute a child template writes, not text.
        if re.fullmatch(r'[\w-]+="[^"]*"', text):
            return
        if WORD.search(text):
            self.found.append((self.getpos()[0], text))


def _literals(source: str) -> list[tuple[int, str]]:
    """Prose-shaped string literals inside Jinja that no `t()` wraps."""
    found = []
    for expression in EXPR.finditer(source):
        body = expression.group(0)
        for literal in LITERAL.finditer(body):
            text = literal.group("s")
            before = body[: literal.start()]
            if re.search(r"\b(?:t|tm|N_)\(\s*$", before):
                continue
            # A comparison (`phase.title == "Plan"`) and a date format handed to
            # `stamp()` are not shown as written.
            if re.search(r"[=!]=\s*$", before) or re.search(r"\bstamp\([^\"']*,\s*$", before):
                continue
            if WORD.search(text) and PROSE.search(text):
                line = source.count("\n", 0, expression.start())
                line += body.count("\n", 0, literal.start())
                found.append((line + 1, text))
    return found


def untranslated(path: Path) -> list[tuple[int, str]]:
    source = JINJA_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), path.read_text("utf-8"))
    found = _literals(source)
    # A statement leaves nothing (so `<body{% block %}…` is still a tag); an
    # expression leaves a marker that is never itself text.
    masked = EXPR.sub(
        lambda m: ("\x00" if m.group(0).startswith("{{") else "") + "\n" * m.group(0).count("\n"),
        source,
    )
    reader = _Reader()
    reader.feed(masked)
    return sorted(found + reader.found)


def everything() -> list[str]:
    out = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        name = str(path.relative_to(TEMPLATES))
        for line, text in untranslated(path):
            if (name, text) not in ALLOWED:
                out.append(f"{name}:{line}: {text[:100]}")
    return out


def test_every_template_says_its_words_through_t():
    left = everything()
    assert not left, (
        f"{len(left)} reader-facing strings are not translated -- wrap them in t()/tm(), "
        "or add them to ALLOWED with the reason they are not prose:\n" + "\n".join(left[:40])
    )


def test_the_reader_finds_what_it_is_meant_to_find(tmp_path):
    """A blindness guard: a reader that stopped finding anything would pass."""
    sample = tmp_path / "sample.html"
    sample.write_text(
        '<p>Plain words</p><a title="A tooltip">{{ t("Fine") }}</a>'
        '<span>{{ "Agent chats" if x else t("Chats") }}</span>'
        '<button data-prompt="What is it called?"></button>'
        "{# a comment is not text #}<code>x = 1</code>",
        encoding="utf-8",
    )
    found = [text for _line, text in untranslated(sample)]
    expected = ["Plain words", "A tooltip", "Agent chats", "What is it called?"]
    assert sorted(found) == sorted(expected)


def test_the_allow_list_has_no_dead_entries():
    """An exception that no longer matches anything is a hole waiting to be
    reused by a different string with the same text."""
    reported = {
        (str(path.relative_to(TEMPLATES)), text)
        for path in sorted(TEMPLATES.rglob("*.html"))
        for _line, text in untranslated(path)
    }
    assert not set(ALLOWED) - reported, sorted(set(ALLOWED) - reported)
