"""Find every translatable string, and say what the catalogues are missing.

Run it:

    python scripts/i18n_extract.py                 # a report
    python scripts/i18n_extract.py --write sk      # fill sk.py with what is missing

A development instrument, like `shoot.py` and `fetch_vendor.py`: nothing in `src/`
imports it. What it knows is the one thing a catalogue keyed on source text cannot
know for itself -- that an English sentence has been edited, leaving its
translation stranded under the old wording. `tests/test_translations.py` asserts
the same property from the other side, so a catalogue cannot rot quietly.

The scan is deliberately simple: `t("…")` and `t('…')`, in templates and in
Python. A string built by concatenation or an f-string is not found, and that is
the point -- a sentence assembled from pieces cannot be translated, because the
order of the pieces is not the same in every language. Use `t("… %(name)s …",
name=…)`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src/lembas"
TEMPLATES = SRC / "web/templates"
CATALOGUES = SRC / "web/i18n"

# `t("…")` with either quote, allowing escaped quotes inside. Multi-line, because
# a paragraph in a template is wrapped for the width of the file.
CALL = re.compile(r"""\b(?:t|tm|N_)\(\s*(?P<q>["'])(?P<text>(?:\\.|(?!\1).)*?)\1""", re.S)

# `i18n.stamp(value, "%d %B %Y")` -- the *format* is translated too, so a language
# that puts the day first, or wants a full stop after it, says so in the
# catalogue. A second pattern rather than a looser first one: widening `t(` to
# "any call with a string in it" would sweep up every `select("…")` in the
# codebase.
STAMP = re.compile(
    # One level of nesting allowed in the first argument, because it is usually a
    # call: `stamp(clock.now_for(user), "…")`.
    r"""\bstamp\((?:[^()"']|\([^()]*\))*,\s*(?P<q>["'])(?P<text>(?:\\.|(?!\1).)*?)\1""",
    re.S,
)


def normalise(text: str) -> str:
    """The key: whitespace collapsed, escapes resolved.

    A template wraps one sentence across three lines, and the same sentence in a
    Python file across two. Keying on the exact bytes would need an entry per
    wrapping, so the key is the text with its runs of whitespace flattened -- which
    is exactly what `i18n.translate` looks up.
    """
    text = text.replace('\\"', '"').replace("\\'", "'").replace("\\n", " ")
    return " ".join(text.split())


def sources() -> list[Path]:
    files = sorted(TEMPLATES.rglob("*.html"))
    files += [
        path
        for path in sorted(SRC.rglob("*.py"))
        if "web/i18n" not in str(path) and "__pycache__" not in str(path)
    ]
    files += sorted((SRC / "web/static/js").glob("*.js"))
    return files


JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
# Python joins adjacent literals, so a long sentence in a `.py` file is written
# as several -- `N_("Let a model search " "the web.")` -- and the key is all of
# them. Only for Python: Jinja has no implicit concatenation, and a JavaScript
# sentence is written on one line rather than joined with `+`.
PY_LITERAL = r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
PY_CALL = re.compile(
    r"\b(?:t|tm|N_)\(\s*(?P<text>" + PY_LITERAL + r"(?:\s*" + PY_LITERAL + r")*)", re.S
)


def joined(text: str) -> str:
    """The value of a run of adjacent Python literals, quotes removed."""
    return "".join(part[1:-1] for part in re.findall(PY_LITERAL, text))


PY_COMMENT = re.compile(r"^[ \t]*#.*$", re.M)
JS_COMMENT = re.compile(r"/\*.*?\*/|^[ \t]*//[^\n]*$", re.S | re.M)


def strip_comments(path: Path, text: str) -> str:
    """Comments are not strings. This file's own docstrings quote `t("Save")`, and
    so does `web/templating.py`'s comment explaining the global -- both would
    otherwise arrive in the catalogue as things to translate."""
    if path.suffix == ".html":
        return JINJA_COMMENT.sub("", text)
    if path.suffix == ".js":
        return JS_COMMENT.sub("", text)
    return PY_COMMENT.sub("", text)


def found() -> dict[str, list[str]]:
    """Every string, with the files it appears in."""
    out: dict[str, list[str]] = {}
    for path in sources():
        text = strip_comments(path, path.read_text(encoding="utf-8"))
        if path.suffix == ".py":
            texts = [joined(m.group("text")) for m in PY_CALL.finditer(text)]
        else:
            texts = [m.group("text") for m in CALL.finditer(text)]
        texts += [m.group("text") for m in STAMP.finditer(text)]
        for found_text in texts:
            key = normalise(found_text)
            if not key:
                continue
            out.setdefault(key, [])
            where = str(path.relative_to(REPO))
            if where not in out[key]:
                out[key].append(where)
    return out


def catalogue(code: str) -> dict[str, str]:
    path = CATALOGUES / f"{code}.py"
    if not path.exists():
        return {}
    namespace: dict[str, object] = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), namespace)
    return dict(namespace.get("MESSAGES", {}))  # type: ignore[arg-type]


def report() -> int:
    strings = found()
    print(f"{len(strings)} translatable strings in {len(sources())} files")
    for code in ("sk",):
        have = catalogue(code)
        missing = [key for key in strings if key not in have]
        orphans = [key for key in have if key not in strings]
        done = len(strings) - len(missing)
        print(
            f"  {code}: {done}/{len(strings)} translated"
            f" ({len(missing)} missing, {len(orphans)} orphaned)"
        )
        for key in orphans[:10]:
            print(f"    orphan: {key[:80]!r}")
    return 0


def write(code: str) -> int:
    """Append the missing keys to a catalogue, each mapped to itself.

    Mapped to the English rather than to "" on purpose: an empty translation would
    render as an empty paragraph, while the English renders as what it already
    said. A catalogue half-filled is a page half-translated, never a page with
    holes in it.
    """
    strings = found()
    have = catalogue(code)
    missing = [key for key in strings if key not in have]
    if not missing:
        print(f"{code}: nothing missing")
        return 0
    path = CATALOGUES / f"{code}.py"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n# --- {len(missing)} added by scripts/i18n_extract.py ---\n")
        for key in missing:
            handle.write(f"MESSAGES[{key!r}] = {key!r}\n")
    print(f"{code}: added {len(missing)} keys to {path.relative_to(REPO)}")
    return 0


def main() -> int:
    if "--write" in sys.argv:
        return write(sys.argv[sys.argv.index("--write") + 1])
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
