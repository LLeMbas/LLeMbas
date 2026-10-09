"""Translating the interface, without a build step.

## Why not gettext

`.po` files compiled to `.mo` are a build step, and this project does not have
one (hard rule 1). So a catalogue is a committed Python module: a dict, keyed by
**the English source text**, loaded at import.

Keying on the source has one large advantage and one cost, and the advantage is
what decides it: **a missing entry renders the key**, which is the English. An
untranslated string therefore looks exactly as it did before, an instance running
in English is byte-for-byte what shipped, and a half-finished catalogue degrades
into a half-translated page rather than into `settings.appearance.theme.label`
written across somebody's screen. The cost is that editing an English sentence
orphans its translation silently -- which is what
`tests/test_translations.py` exists to catch, in both directions.

This is the shape `branding.FLAVOUR` and `services/prompts.py` already use:
defaults in code, overrides beside them, and a test that the two agree.

## Why a ContextVar

`t()` has to be reachable from a Jinja **global**, not from the template context.
`web/templating.render` is bypassed by 25 direct `TemplateResponse` calls and 8
`get_template().render()` calls, and the second group is the SSE frame path, which
has no `Request` object at all -- so threading a language through the context
would leave a third of the application untranslated, and `brand`'s docstring
records that lesson already.

But a global is bound once at import and the language is **per person**, so the
active language cannot live in the global. It lives in a `ContextVar` that
`LanguageMiddleware` sets per request. That is the one piece of genuinely new
machinery here; `brand` avoids needing it only because instance branding is the
same for everybody.

⚠ A `ContextVar` is per *task*, and a background reply is a task of its own. It
therefore does not inherit a request's language, which is correct rather than
unfortunate: nothing a generation writes is interface text, and the one place it
matters -- a fragment telling a model which language to answer in -- is a prompt
variable, not a `t()` call.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Mapping
from contextvars import ContextVar

log = logging.getLogger(__name__)

# The language every string is written in, and the key every catalogue uses.
SOURCE = "en"

# What an instance may be set to. Ordered, because it is also the order the
# settings screens offer.
#
# Found rather than listed (2.0.0): every catalogue module beside this one that
# says `NAME = "…"` (its own name for itself) is a language, and `DIRECTION =
# "rtl"` makes it right-to-left -- so adding a language is adding one file.
# Read from the file's text, not imported: a catalogue is thousands of lines and
# is loaded only when somebody reads in it.
def _catalogues() -> tuple[tuple[tuple[str, str], ...], dict[str, str]]:
    import re
    from pathlib import Path

    found: list[tuple[str, str]] = []
    directions = {SOURCE: "ltr"}
    for path in sorted(Path(__file__).parent.glob("*.py")):
        code = path.stem
        if code.startswith("_") or not re.fullmatch(r"[a-z]{2,3}(_[A-Za-z]{2,4})?", code):
            continue
        head = path.read_text(encoding="utf-8")[:4000]
        name = re.search(r'^NAME\s*=\s*"([^"]+)"', head, re.M)
        if name is None:
            continue
        found.append((code, name.group(1)))
        rtl = re.search(r'^DIRECTION\s*=\s*"rtl"', head, re.M)
        directions[code] = "rtl" if rtl else "ltr"
    return ((SOURCE, "English"), *sorted(found, key=lambda pair: pair[1])), directions


LANGUAGES, _DIRECTIONS = _catalogues()

LANGUAGE_IDS = tuple(code for code, _name in LANGUAGES)

# Text direction, so `<html dir>` is answered from one place.
DIRECTIONS: Mapping[str, str] = _DIRECTIONS

_active: ContextVar[str] = ContextVar("lembas_language", default=SOURCE)

# Loaded lazily and cached: a catalogue is a module, and importing every language
# at start would read files an instance in English never needs.
_catalogues: dict[str, Mapping[str, str]] = {}


# The instance default, cached at process level exactly as `branding.snapshot()`
# is and invalidated the same way -- by the one module that writes it calling
# `forget()`. Without the cache, every request would need a settings read before
# it could decide what language to render in, including the ones that never touch
# the database otherwise.
_DEFAULT: str | None = None


def instance_default() -> str:
    """What this instance renders in when nobody has said otherwise.

    Never raises: a language that cannot be read is not a reason to fail a page,
    and English is a usable answer. The same argument `branding.snapshot` makes.
    """
    global _DEFAULT
    if _DEFAULT is not None:
        return _DEFAULT
    try:
        from lembas.db.session import session_scope
        from lembas.services import settings_store

        with session_scope() as db:
            _DEFAULT = known(settings_store.get(db, "language"))
    except Exception:  # noqa: BLE001 - defaults are a usable answer
        log.debug("could not read the instance language; using %s", SOURCE, exc_info=True)
        return SOURCE
    return _DEFAULT


def forget() -> None:
    """Drop the cached instance default. Called by whoever saves it."""
    global _DEFAULT
    _DEFAULT = None


def for_user(user) -> str:
    """The language one person sees: their own choice, else the instance's.

    A `User` or None, so a signed-out page -- the sign-in screen, an error page --
    is rendered in the instance's language rather than in English by accident.
    """
    chosen = ""
    if user is not None:
        chosen = str((getattr(user, "settings_json", None) or {}).get("language") or "")
    return known(chosen) if chosen else instance_default()


def known(code: str | None) -> str:
    """A language this application has, from whatever was stored or requested."""
    value = (code or "").strip().lower()
    if value in LANGUAGE_IDS:
        return value
    # A stored value from a release that offered more languages than this one, or
    # a hand-edited row. English rather than an error: a preference nobody can
    # satisfy is not a reason to refuse somebody their settings page.
    return SOURCE


def catalogue(code: str) -> Mapping[str, str]:
    """Every translation for one language, keyed by its English source."""
    code = known(code)
    if code == SOURCE:
        return {}
    if code not in _catalogues:
        try:
            module = __import__(f"lembas.web.i18n.{code}", fromlist=["MESSAGES"])
            _catalogues[code] = dict(getattr(module, "MESSAGES", {}))
        except Exception:  # noqa: BLE001 - a broken catalogue must not break the page
            log.exception("could not load the %s catalogue", code)
            _catalogues[code] = {}
    return _catalogues[code]


def active() -> str:
    return _active.get()


def activate(code: str | None) -> str:
    """Set the language for this request. Returns what was actually set."""
    code = known(code)
    _active.set(code)
    return code


def direction(code: str | None = None) -> str:
    return DIRECTIONS.get(known(code) if code else active(), "ltr")


def translate(text: str, code: str | None = None) -> str:
    """One string in the active language, or the English it was written in.

    Whitespace is collapsed for the *lookup* and not for the output. A template
    wraps its prose across lines for the width of the file, so the same sentence
    reaches here with different newlines in it depending on where it sits -- and a
    catalogue keyed on the exact bytes would need an entry per wrapping. What is
    returned is the translation as written in the catalogue, or the original text
    untouched.
    """
    if not text:
        return text
    entries = catalogue(code or active())
    if not entries:
        return text
    return entries.get(" ".join(text.split()), text)


def t(text: str, **fields: object) -> str:
    """The function templates and routes call.

    `t("Saved %(name)s", name=x)` rather than an f-string, because a translator
    needs the whole sentence and because the order of the parts is not the same in
    every language. Percent-named rather than `str.format`, so a stray brace in a
    translation cannot raise.
    """
    out = translate(text)
    if not fields:
        return out
    try:
        return out % fields
    except (KeyError, TypeError, ValueError):
        # A catalogue whose placeholders do not match the source is a bug in the
        # catalogue, and the English sentence is a better answer than a traceback
        # in the middle of somebody's page.
        log.warning("placeholder mismatch translating %r", text[:60])
        try:
            return text % fields
        except (KeyError, TypeError, ValueError):
            return text


def N_(text: str) -> str:
    """Mark a literal for the catalogue without translating it here (2.2.0).

    For text written once at import -- a table of permission labels, the
    flavour lines -- and translated where it is shown, with `t(value)`. Calling
    `t()` at import would translate into whatever language happened to be
    active then, which is English, for every reader for ever. The gettext name,
    so it reads as what it is; the scanner finds it as it finds `t(`.
    """
    return text


def tm(text: str, **fields: object):
    """A sentence with inline markup in it -- `<code>`, `<em>`, `<a href>` -- whole.

    `t()` returns a plain string, which Jinja escapes, so a hint that names
    `<code>0</code>` in the middle of a sentence could only be translated in
    pieces around the tag -- and a sentence in pieces cannot be translated,
    because the order of the parts is not the same in every language. That is
    why the first bulk pass left ~400 such sentences in English (2.2.0).

    Safe for the reason `brand` is: **the text is not data.** The source is a
    literal a developer wrote in a template, the translation is a committed
    catalogue entry, and neither is ever model output or anything a person typed
    (hard rule 6 stands: nothing from an endpoint goes through here). The fields
    are the part that may be data, and `Markup.__mod__` escapes each one --
    a `%(name)s` filled with `<script>` arrives as text. A field that is already
    `Markup` (an `icon()` from a macro) is kept as it is.

    `tests/test_translations.py` checks that every translation of a key with
    tags in it carries the same tags, so a catalogue cannot add one.
    """
    from markupsafe import Markup

    out = Markup(translate(text))
    if not fields:
        return out
    try:
        return out % fields
    except (KeyError, TypeError, ValueError):
        log.warning("placeholder mismatch translating %r", text[:60])
        try:
            return Markup(text) % fields
        except (KeyError, TypeError, ValueError):
            return Markup(text)


# --- The browser's half ----------------------------------------------------------
#
# The JavaScript renders text too -- toasts, dialogs, the command menu, the
# terminal's status lines -- and until 2.2.0 every word of it was English on a
# page that was otherwise not. There is still no build step to compile a
# catalogue into a bundle, so the page carries the part of the catalogue its
# scripts use: `base.html` writes `js_strings()` into a JSON island and a
# six-line `window.lembasT` reads it, with the same `%(name)s` fields as `t()`.
#
# Which strings those are is found the way `scripts/i18n_extract.py` finds them
# everywhere else -- a `t("…")` call with a literal in it -- so a script names
# what it needs by using it, and there is no second list to keep in step. An
# English page carries nothing at all: a missing key is its own English.
@functools.cache
def _js_keys() -> tuple[str, ...]:
    import re
    from pathlib import Path

    call = re.compile(r"""\bt\(\s*(?P<q>["'])(?P<text>(?:\\.|(?!(?P=q)).)*?)(?P=q)""", re.S)
    comment = re.compile(r"/\*.*?\*/|^[ \t]*//[^\n]*$", re.S | re.M)
    keys: list[str] = []
    for path in sorted((Path(__file__).parent.parent / "static/js").glob("*.js")):
        text = comment.sub("", path.read_text(encoding="utf-8"))
        for match in call.finditer(text):
            key = match.group("text").replace('\\"', '"').replace("\\'", "'")
            key = " ".join(key.split())
            if key and key not in keys:
                keys.append(key)
    return tuple(keys)


def js_strings(code: str | None = None) -> dict[str, str]:
    """The translations the page's scripts ask for, in the active language."""
    entries = catalogue(code or active())
    if not entries:
        return {}
    return {key: entries[key] for key in _js_keys() if key in entries}


__all__ = [
    "DIRECTIONS",
    "N_",
    "LANGUAGES",
    "LANGUAGE_IDS",
    "SOURCE",
    "activate",
    "active",
    "catalogue",
    "direction",
    "forget",
    "for_user",
    "instance_default",
    "js_strings",
    "known",
    "stamp",
    "t",
    "tm",
    "translate",
]


# --- Dates ---------------------------------------------------------------------
#
# `strftime("%A")` and `%B` emit C-locale English whatever the page is in, which is
# invisible until the day a second language ships and then wrong on every screen
# showing a date. Setting a process locale is not an option: it is global, it is
# not thread-safe, and this application renders two people's pages at once.
#
# So the names are a table and the *format* is itself translatable -- Slovak wants
# "26. septembra 2026", not "26 September 2026", and that is a different pattern
# rather than different words in the same one.
#
# ⚠ Only for what a **person** reads. `harness.py`, `schedule/runner.py` and
# `schedule/compile.py` all put dates in front of a *model*, and those stay
# English: the prompts are written in English, a model is not the reader, and a
# background task has no request language anyway.
_ENGLISH_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_ENGLISH_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


@functools.cache
def _date_names(code: str) -> tuple[tuple[str, str], ...]:
    """English month and day names paired with the language's own (2.1.0: from
    the catalogue file, `MONTHS` -- twelve, in the form a date with a day number
    wants -- and `DAYS`, Monday first; a file without them keeps English)."""
    if code == SOURCE:
        return ()
    try:
        module = __import__(f"lembas.web.i18n.{code}", fromlist=["MONTHS"])
    except ImportError:
        return ()
    pairs: list[tuple[str, str]] = []
    for english, local in ((_ENGLISH_MONTHS, getattr(module, "MONTHS", ())),
                           (_ENGLISH_DAYS, getattr(module, "DAYS", ()))):
        if len(local) == len(english):
            pairs.extend(zip(english, local, strict=True))
    return tuple(pairs)


def stamp(value, fmt: str) -> str:
    """One date, in the reader's language.

    `fmt` is an English `strftime` pattern and is translated like any other
    string, so a language that puts the day before the month -- or wants a full
    stop after it -- says so in the catalogue rather than here.
    """
    if value is None:
        return ""
    code = active()
    text = value.strftime(translate(fmt, code))
    for english, local in _date_names(code):
        text = text.replace(english, local)
    return text


# --- Numbers (2.4.0) ------------------------------------------------------------
# How each language writes 1234.5: what groups the thousands and what marks the
# decimals. Here rather than in each catalogue because it is not a translation
# and none of them has a sentence to put it in -- and in the code, not in a
# locale database, because nothing else in this application needs one and the
# eight answers fit in a dict. A narrow no-break space groups where the language
# groups with a space, so `1 234` never breaks across a line.
NUMBER_MARKS: dict[str, tuple[str, str]] = {
    "en": (",", "."),
    "sk": (" ", ","),
    "cs": (" ", ","),
    "pl": (" ", ","),
    "hu": (" ", ","),
    "fr": (" ", ","),
    "de": (".", ","),
    "es": (".", ","),
    "it": (".", ","),
}
# Where the currency sign goes: before the number in English (`€1.23`), after it
# with a space everywhere else here (`1,23 €`).
SIGN_FIRST = frozenset({"en"})


def number(value: float, decimals: int = 2, code: str | None = None) -> str:
    """`value` with `decimals` places, written the way the language writes it."""
    group, point = NUMBER_MARKS.get(code or active(), NUMBER_MARKS[SOURCE])
    text = f"{value:,.{max(0, decimals)}f}"
    return text.replace(",", "\0").replace(".", point).replace("\0", group)


def sign_first(code: str | None = None) -> bool:
    return (code or active()) in SIGN_FIRST
