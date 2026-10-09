"""The two places a person reads the list of models: the chat's picker and /settings.

Printing every capability switch as a tag would mean twenty `tool_*` entries per
model, and in /settings tags beside the name squeeze it to a word per line. So
the picker is name, context window and an eye for vision; /settings keeps the
tags, underneath the name.

The layout is by construction (the rows share the list's column tracks), and
that only holds while every row emits every slot, so a model with no context
length and no vision is asserted to still have both cells.
"""

from __future__ import annotations

import re

import pytest

from lembas.db.models import Connection, Model
from lembas.services.crypto import encrypt
from lembas.web.templating import context_size


@pytest.mark.parametrize(
    ("tokens", "shown"),
    [
        (None, ""),
        (0, ""),
        (512, "512"),
        (4096, "4K"),
        (32768, "33K"),
        (131072, "131K"),
        (262144, "262K"),
        (1_000_000, "1M"),
        (1_048_576, "1M"),
        (2_000_000, "2M"),
        (1_500_000, "1.5M"),
    ],
)
def test_a_context_window_is_shortened_the_way_it_is_quoted(tokens, shown):
    assert context_size(tokens) == shown


@pytest.fixture
def models(db, registered):
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add_all(
        [
            Model(
                connection_id=connection.id,
                model_id="sees",
                display_name="Sees",
                position=0,
                context_length=131072,
                capabilities_json={"vision": True, "tools": True, "tool_fetch": True},
            ),
            Model(
                connection_id=connection.id,
                model_id="blind",
                display_name="Blind",
                position=1,
                capabilities_json={"tools": True, "tool_fetch": True},
            ),
        ]
    )
    db.commit()


def _options(html: str) -> dict[str, str]:
    """The model picker's options by model id -- not the @-mention menu's."""
    found = {}
    for body in re.findall(r'<button class="picker__option\b.*?</button>', html, re.S):
        value = re.search(r'data-picker-value="([^"]+)"', body)
        if value:
            found[value.group(1)] = body
    return found


def test_the_picker_shows_name_context_and_vision_and_no_tags(client, models):
    html = client.get("/chat?model=sees").text
    options = _options(html)
    assert set(options) == {"sees", "blind"}

    sees = options["sees"]
    assert "Sees" in sees
    assert "CTX 131K" in sees
    assert "#i-eye" in sees
    assert 'class="tag"' not in sees
    assert "tool_fetch" not in sees


def test_every_picker_row_emits_every_slot(client, models):
    blind = _options(client.get("/chat?model=sees").text)["blind"]
    assert "#i-eye" not in blind
    assert "CTX" not in blind
    slots = ("picker__avatar", "picker__option-name", "model-ctx", "model-vision", "picker__tick")
    for slot in slots:
        assert slot in blind, slot


def test_settings_lists_the_models_with_their_tags_below_the_name(client, models):
    html = client.get("/settings").text
    listing = html[html.index('class="model-list model-list--models"'):]
    listing = listing[: listing.index("</ul>")]
    assert listing.count('class="model-list__item"') == 2
    assert "CTX 131K" in listing
    assert listing.count("#i-eye") == 1
    assert listing.count('class="model-list__more model-list__tags"') == 2
    assert "tool_fetch" in listing


def test_opening_the_picker_on_a_touchscreen_does_not_raise_the_keyboard():
    """With more than eight models the menu has a filter, and focusing it in
    `open()` would, on a phone, raise the keyboard over half the list the
    finger came to choose from. The focus is gated on `(hover: none)`, the same
    query the stylesheet uses for touch."""
    from pathlib import Path

    import lembas

    js = (Path(lembas.__file__).parent / "web/static/js/ui.js").read_text(encoding="utf-8")
    start = js.index("function open(picker)")
    body = js[start : js.index("function applyFilter", start)]
    assert "filter.focus()" in body, "the filter is no longer focused anywhere -- test is blind"
    gated = r'if \(filter && !window\.matchMedia\("\(hover: none\)"\)\.matches\)\s*\{'
    assert re.search(gated + r"\s*filter\.focus\(\)", body), (
        "the filter is focused on open without asking whether this is a touchscreen"
    )
