"""The interface in another language, and the two things that rot silently.

A catalogue keyed by the English source text degrades well -- a missing entry
renders the English, so a half-finished translation is a half-translated page
rather than a page with `settings.theme.label` written across it. What it cannot
do is notice that somebody edited an English sentence, which leaves its
translation stranded under the old wording and the new sentence untranslated with
nothing said about either. So this asserts it from both sides.

The other thing here is a **template compile check**, which belongs with this work
because wrapping a thousand strings is what proved it was missing: a partial that
no test renders can carry a syntax error indefinitely, and the way it announced
itself was a 500 on two unrelated pages. One apostrophe inside a single-quoted
Jinja string did it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

import lembas
from lembas.web import i18n

ROOT = Path(lembas.__file__).parent
TEMPLATES = ROOT / "web/templates"
CATALOGUES = ROOT / "web/i18n"

# The same pattern `scripts/i18n_extract.py` uses. Duplicated rather than imported
# because `scripts/` is not a package and is not importable from the suite -- and
# a test that silently stopped finding anything would be worse than the copy.
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
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
# Python joins adjacent literals, so a long sentence in a `.py` file is written
# as several -- `N_("Let a model search " "the web.")` -- and the key is all of
# them. Only for Python: Jinja has no implicit concatenation, and a JavaScript
# sentence is written on one line rather than joined with `+`.
PY_LITERAL = r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')"""
PY_CALL = re.compile(
    r"\b(?:t|tm|N_)\(\s*(?P<text>" + PY_LITERAL + r"(?:\s*" + PY_LITERAL + r")*)", re.S
)


def _joined(text: str) -> str:
    """The value of a run of adjacent Python literals, quotes removed."""
    return "".join(part[1:-1] for part in re.findall(PY_LITERAL, text))


PY_COMMENT = re.compile(r"^[ \t]*#.*$", re.M)
JS_COMMENT = re.compile(r"/\*.*?\*/|^[ \t]*//[^\n]*$", re.S | re.M)


def _normalise(text: str) -> str:
    text = text.replace('\\"', '"').replace("\\'", "'").replace("\\n", " ")
    return " ".join(text.split())


def _sources() -> list[Path]:
    files = sorted(TEMPLATES.rglob("*.html"))
    files += [
        path
        for path in sorted(ROOT.rglob("*.py"))
        if "web/i18n" not in str(path) and "__pycache__" not in str(path)
    ]
    # The scripts ask for their words with `t("…")` too, and `base.html`
    # ships the matching entries in a JSON island -- `i18n.js_strings`.
    files += sorted((ROOT / "web/static/js").glob("*.js"))
    return files


def _used() -> dict[str, str]:
    """Every translatable string, mapped to the first file it appears in."""
    out: dict[str, str] = {}
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".html":
            text = JINJA_COMMENT.sub("", text)
        elif path.suffix == ".js":
            text = JS_COMMENT.sub("", text)
        else:
            text = PY_COMMENT.sub("", text)
        if path.suffix == ".py":
            texts = [_joined(m.group("text")) for m in PY_CALL.finditer(text)]
        else:
            texts = [m.group("text") for m in CALL.finditer(text)]
        texts += [m.group("text") for m in STAMP.finditer(text)]
        for found in texts:
            key = _normalise(found)
            if key:
                out.setdefault(key, str(path.relative_to(ROOT)))
    return out


def _templates_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)), trim_blocks=True, lstrip_blocks=True
    )
    # Enough of the real globals that a template naming one still compiles. This
    # checks syntax, not rendering: a name that exists is all a compile needs.
    env.globals.update(
        {
            "t": lambda text, **fields: text,
            "tm": lambda text, **fields: text,
            "language": lambda: "en",
            "text_direction": lambda: "ltr",
            "asset": lambda path: path,
            "brand": object(),
            "tool_label": lambda event: "",
            "tool_icon": lambda event: "",
            "message_steps": lambda message: [],
        }
    )
    return env


# --- Every template compiles ---------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [str(path.relative_to(TEMPLATES)) for path in sorted(TEMPLATES.rglob("*.html"))],
)
def test_every_template_compiles(name: str):
    """A partial no test renders can carry a syntax error for ever.

    This found the one that shipped with the first wrapping pass -- an apostrophe
    inside `t('…')` in an attribute, where the sentence's own quote closed the
    Jinja string -- and it found it as a named file rather than as a 500 on two
    pages that happened to include it.
    """
    _templates_env().get_template(name)


def test_the_scan_finds_the_strings_it_is_meant_to_police():
    """A blindness guard. Every assertion below is about a set, and a scan that
    stops matching makes all of them pass by finding nothing."""
    used = _used()
    assert len(used) > 500, f"only {len(used)} translatable strings found"


# --- The catalogues ------------------------------------------------------------
@pytest.mark.parametrize("code", [code for code in i18n.LANGUAGE_IDS if code != i18n.SOURCE])
def test_no_catalogue_entry_is_orphaned(code: str):
    """The failure this file exists for. Editing an English sentence leaves its
    translation keyed on wording nothing says any more -- so the page silently
    reverts to English there, and the old translation sits in the file looking
    done. Nothing else can notice."""
    used = _used()
    orphans = sorted(key for key in i18n.catalogue(code) if key not in used)
    assert not orphans, (
        f"{len(orphans)} {code} entries are keyed on text nothing says any more; "
        f"the first few: {orphans[:3]}"
    )


@pytest.mark.parametrize("code", [code for code in i18n.LANGUAGE_IDS if code != i18n.SOURCE])
def test_placeholders_survive_translation(code: str):
    """A translation that drops `%(name)s` renders a sentence with a hole in it.
    `t` falls back to the English when the substitution fails, so the symptom is
    one untranslated line -- visible, but only if somebody is looking."""
    fields = re.compile(r"%\((\w+)\)s")
    for key, value in i18n.catalogue(code).items():
        assert set(fields.findall(key)) == set(fields.findall(value)), key[:60]


TAG = re.compile(r"</?([a-z][a-z0-9]*)\b[^>]*>")


@pytest.mark.parametrize("code", [code for code in i18n.LANGUAGE_IDS if code != i18n.SOURCE])
def test_a_translation_carries_the_markup_its_source_does(code: str):
    """`tm()` renders a catalogue entry as markup, which is safe only
    because the catalogue is ours -- so a translation may move a tag, never add
    one, drop one or change what it is. Compared as attributes and all, so an
    `<a href>` cannot be pointed somewhere else either."""
    for key, value in i18n.catalogue(code).items():
        if "<" not in key and "<" not in value:
            continue
        assert sorted(m.group(0) for m in TAG.finditer(key)) == sorted(
            m.group(0) for m in TAG.finditer(value)
        ), f"{code}: markup differs in {key[:60]!r}"


@pytest.mark.parametrize("code", [code for code in i18n.LANGUAGE_IDS if code != i18n.SOURCE])
def test_nothing_is_translated_to_the_empty_string(code: str):
    """An empty translation renders an empty paragraph, which is worse than the
    English it replaced. `scripts/i18n_extract.py --write` maps a new key to its
    own English text for the same reason."""
    blank = [key for key, value in i18n.catalogue(code).items() if not value.strip()]
    assert not blank, f"{code}: {len(blank)} empty translations, e.g. {blank[:3]}"


def test_a_language_nobody_offers_falls_back_rather_than_raising():
    """A stored code from a release that had more languages than this one, or a
    hand-edited row. Somebody's settings page must still open."""
    assert i18n.known("kl") == i18n.SOURCE
    assert i18n.known(None) == i18n.SOURCE
    assert i18n.known("SK") == "sk"


def test_translating_falls_back_to_the_source_text():
    """The property the whole design rests on: an untranslated string renders as
    what it already said."""
    assert i18n.translate("Something nothing has translated", "sk") == (
        "Something nothing has translated"
    )
    assert i18n.translate("Sign in", "sk") != "Sign in"


def test_whitespace_in_the_source_does_not_have_to_match():
    """A template wraps one sentence across three lines and a Python file across
    two, so the key is the text with its whitespace collapsed. Without this, a
    catalogue would need an entry per wrapping."""
    assert i18n.translate("Sign   in", "sk") == i18n.translate("Sign in", "sk")
    assert i18n.translate("Sign\n      in", "sk") == i18n.translate("Sign in", "sk")


# --- What it does to a page ----------------------------------------------------
def test_a_person_sees_their_own_language(client, db, registered):
    from sqlalchemy import select

    from lembas.db.models import User

    user = db.scalars(select(User)).first()
    user.settings_json = {**(user.settings_json or {}), "language": "sk"}
    db.commit()

    page = client.get("/settings")

    assert 'lang="sk"' in page.text
    assert "Odhlásiť sa" in page.text


def test_the_instance_default_reaches_somebody_with_no_choice(client, db, registered):
    from lembas.services import settings_store

    settings_store.update(db, {"language": "sk"})
    i18n.forget()
    try:
        page = client.get("/settings")
        assert 'lang="sk"' in page.text
    finally:
        settings_store.update(db, {"language": ""})
        i18n.forget()


def test_a_persons_choice_beats_the_instance(client, db, registered):
    from sqlalchemy import select

    from lembas.db.models import User
    from lembas.services import settings_store

    settings_store.update(db, {"language": "sk"})
    i18n.forget()
    user = db.scalars(select(User)).first()
    user.settings_json = {**(user.settings_json or {}), "language": "en"}
    db.commit()
    try:
        assert 'lang="en"' in client.get("/settings").text
    finally:
        settings_store.update(db, {"language": ""})
        i18n.forget()


def test_an_english_instance_renders_the_english_it_always_did(client, db, registered):
    """The byte-for-byte guarantee. `t()` with no catalogue is the identity, so a
    page in English is what shipped before any of this existed."""
    page = client.get("/settings")
    assert "Sign out" in page.text
    assert 'lang="en"' in page.text


def test_the_language_can_be_chosen_and_is_validated(client, db, registered):
    from sqlalchemy import select

    from lembas.db.models import User

    client.post("/api/preferences/language", data={"language": "sk"}, follow_redirects=False)
    db.expire_all()
    assert db.scalars(select(User)).first().settings_json["language"] == "sk"

    refused = client.post(
        "/api/preferences/language", data={"language": "klingon"}, follow_redirects=False
    )
    assert refused.headers["location"].endswith("error=language")
    db.expire_all()
    assert db.scalars(select(User)).first().settings_json["language"] == "sk"


def test_following_the_instance_is_a_real_answer(client, db, registered):
    """Stored as "" rather than removed, the same shape the timezone uses: absent
    and "follow the default" are different states and a form cannot tell them
    apart otherwise."""
    from sqlalchemy import select

    from lembas.db.models import User

    client.post("/api/preferences/language", data={"language": "sk"}, follow_redirects=False)
    client.post("/api/preferences/language", data={"language": ""}, follow_redirects=False)
    db.expire_all()
    assert db.scalars(select(User)).first().settings_json["language"] == ""


@pytest.mark.parametrize("code", [code for code in i18n.LANGUAGE_IDS if code != i18n.SOURCE])
def test_a_date_reads_in_the_language(code: str):
    """Month and day names come from the catalogue file itself, so a
    language that is one file is one file for its dates too."""
    from datetime import datetime

    token = i18n._active.set(code)
    try:
        text = i18n.stamp(datetime(2026, 10, 8, 21, 0), "%A %-d %B %Y")
    finally:
        i18n._active.reset(token)
    assert "October" not in text and "Thursday" not in text, text
