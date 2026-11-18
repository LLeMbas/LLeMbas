"""A connection's media rows, and the two settings pages that name the
defaults, measured in a real Chromium at 1280 and at 390 px.

The rows are a grid whose three tracks every row shares, so a column cannot
drift when a label is longer in another language; what a suite can hold is
that this is still so in a browser: every row's kind, model and price start
where every other row's do, the model's id has room, nothing lies outside
the window, and the page never scrolls sideways. On a phone a row is one
column beside its switch.

Slow (a browser per page): run it through `heavy-guard`, by itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import Connection
from lembas.services.crypto import encrypt
from lembas.services.llm import providers
from lembas.services.media import store
from tests.test_reports_layout import CHROMIUM, REQUIRED, _measure

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to measure in"),
]

CONTROLS = (
    ".media-row select[name=media_capability], .media-row input[name=media_model], "
    ".media-row input[name=media_price]"
)
TAPS = ".media-cell--on"


@pytest.fixture(autouse=True)
def _a_browser_is_there():
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")


@pytest.fixture
def seeded(client, db, registered) -> Connection:
    row = Connection(
        name="A provider with a rather long name for a card's header",
        base_url="https://api.openai.com/v1", provider_type=providers.OPENAI,
        api_key_encrypted=encrypt("k"),
    )
    row.media_json = store.normalise(
        {"prefer_audio": True, "prefer_image": True, "models": [
            {"capability": "tts", "model": "a-speech-model-with-a-long-identifier-2026-11",
             "enabled": True, "price": 15, "voice": "ada"},
            {"capability": "image", "model": "draw-one", "enabled": True, "price": 0.04},
        ]}
    )
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize("language", ["en", "sk"])
@pytest.mark.parametrize("width", [1280, 390])
def test_the_media_rows_line_up_and_fit(client, db, seeded, tmp_path, width, language):
    from lembas.db.models import User

    user = db.scalars(select(User)).first()
    user.settings_json = {**(user.settings_json or {}), "language": language}
    db.commit()
    probe = _measure(client, "/admin/connections", width, tmp_path, CONTROLS, TAPS)
    assert probe["page"] <= probe["width"], f"the page scrolls sideways at {width}"
    boxes = probe["named"]
    assert all(b["left"] >= 0 and b["right"] <= probe["width"] for b in boxes), boxes
    assert not [b for b in boxes if b["cut"]], "something is cut off"
    # Three controls a row that are always shown: kind, model, price.
    rows = [boxes[i : i + 3] for i in range(0, len(boxes), 3)]
    assert len(rows) >= 5 and all(len(row) == 3 for row in rows)
    if width >= 1280:
        for column in range(3):
            lefts = {row[column]["left"] for row in rows}
            assert len(lefts) == 1, f"column {column} starts at {sorted(lefts)}"
        for kind, model, price in rows:
            # Kind beside the model's id; the price under the id, on its track.
            assert kind["right"] <= model["left"] and price["left"] == model["left"]
            # The id is what an administrator reads and types: it has room.
            assert model["right"] - model["left"] >= 300, model
    else:
        # A row is one column: its controls share one left edge.
        assert len({b["left"] for row in rows for b in row}) == 1
        assert all(tap["h"] >= 40 for tap in probe["taps"]), probe["taps"]


@pytest.mark.parametrize("path", ["/admin/audio", "/admin/images"])
@pytest.mark.parametrize("width", [1280, 390])
def test_the_default_source_choice_fits(client, db, seeded, tmp_path, width, path):
    probe = _measure(
        client, path, width, tmp_path, "select[name$=connection], [data-testid$=defaults-note], "
        "[data-testid=image-defaults]", "select[name$=connection]",
    )
    assert probe["page"] <= probe["width"], f"{path} scrolls sideways at {width}"
    assert probe["named"], "the default-source choice is not on the page"
    assert all(b["left"] >= 0 and b["right"] <= probe["width"] for b in probe["named"])
