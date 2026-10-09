"""The scripts' words reach the page in the reader's language.

The files under `static/js` ask for text with `t("…")`; `base.html` writes the
matching catalogue entries into a JSON island and `window.lembasT` reads it.
Nothing compiles a bundle, so the set of keys is found by reading the scripts --
which these tests hold to: a key a script uses is shipped, an English page
ships nothing, and the island cannot be closed by a translation.
"""

from __future__ import annotations

import json
import re

from lembas.web import i18n


def test_the_keys_are_found_in_the_scripts():
    keys = i18n._js_keys()
    assert "Copied" in keys
    assert "Mode set to %(mode)s." in keys
    assert len(keys) > 60


def test_english_ships_nothing():
    assert i18n.js_strings("en") == {}


def test_a_language_ships_what_the_scripts_use():
    table = i18n.js_strings("sk")
    assert table["Copied"] == i18n.translate("Copied", "sk")
    assert set(table) <= set(i18n._js_keys())


def test_every_script_string_has_a_slovak_entry():
    """Slovak is the catalogue every new string lands in first, so nothing a
    script says is left in English there; the other languages follow it."""
    missing = [key for key in i18n._js_keys() if key not in i18n.catalogue("sk")]
    assert not missing, missing[:10]


def _island(html: str) -> dict:
    island = r'<script type="application/json" id="lembas-strings">(.*?)</script>'
    match = re.search(island, html, re.S)
    assert match, "no strings island on the page"
    return json.loads(match.group(1))


def test_the_page_carries_the_island(client, db, registered):
    from sqlalchemy import select

    from lembas.db.models import User

    assert _island(client.get("/settings").text) == {}

    user = db.scalars(select(User)).first()
    user.settings_json = {**(user.settings_json or {}), "language": "sk"}
    db.commit()
    shipped = _island(client.get("/settings").text)
    assert shipped["Copied"] == i18n.translate("Copied", "sk")
    assert "window.lembasT" in client.get("/settings").text
