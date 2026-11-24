"""The two cards the editor's models added, measured in a real Chromium at
1280x900 and at 390x844: "Models in an editor" on a person's settings page and
"Autocomplete" on the administrator's model page. Each field is a label, a
control and a hint stacked on the card's column -- so nothing lies outside the
window, the page never scrolls sideways whatever a model is called, and what a
thumb presses is at least 40 px.

Slow (a browser per page): run it through `heavy-guard`, by itself."""

from __future__ import annotations

import pytest

from lembas.db.models import Connection, Model
from lembas.services import editor
from lembas.services.crypto import encrypt
from tests.test_reports_layout import CHROMIUM, REQUIRED, TAP, _measure

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to measure in"),
]

LONG = "a-model-with-a-name-that-does-not-wrap-anywhere-at-all-q8_k_xl-131072-instruct"


@pytest.fixture(autouse=True)
def _a_browser_is_there():
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")


@pytest.fixture
def models(db, registered) -> dict[str, Model]:
    connection = Connection(name="A connection with a long name of its own",
                            base_url="http://upstream.test/v1", api_key_encrypted=encrypt(""))
    db.add(connection)
    db.commit()
    rows = {name: Model(connection_id=connection.id, model_id=name)
            for name in ("small", LONG)}
    db.add_all(rows.values())
    db.commit()
    editor.set_instance_default(db, rows[LONG])
    return rows


def _inside(probe: dict, width: int) -> list[dict]:
    assert probe["page"] <= probe["width"], f"the page scrolls sideways at {width}"
    boxes = probe["named"]
    assert all(b["left"] >= 0 and b["right"] <= probe["width"] + 1 for b in boxes), boxes
    assert not [b for b in boxes if b["cut"]], [b for b in boxes if b["cut"]]
    return boxes


@pytest.mark.parametrize("width", [1280, 390])
def test_the_settings_card_fits_and_its_fields_stack(client, models, tmp_path, width):
    card = "#editor-models"
    probe = _measure(
        client, "/settings?tab=models", width, tmp_path,
        f"{card} .field__label, {card} .select, {card} .field__hint, {card} .btn",
        f"{card} .select, {card} .btn",
    )
    boxes = _inside(probe, width)
    labels = [b for b in boxes if "field__label" in b["what"]]
    selects = [b for b in boxes if b["what"] == "select"]
    hints = [b for b in boxes if "field__hint" in b["what"]]
    assert len(labels) == len(selects) == len(hints) == 2, boxes
    # Label, control, hint, then the next field: one column, top to bottom.
    order = [labels[0], selects[0], hints[0], labels[1], selects[1], hints[1]]
    assert all(a["bottom"] <= b["top"] + 1 for a, b in zip(order, order[1:], strict=False)), order
    # Both controls start and end on the same lines, whatever they hold.
    assert len({(b["left"], b["right"], b["h"]) for b in selects}) == 1, selects
    if width < 500:
        assert all(tap["h"] >= TAP for tap in probe["taps"]), probe["taps"]


@pytest.mark.parametrize("width", [1280, 390])
def test_the_administrators_card_fits(client, models, tmp_path, width):
    card = '[data-testid="model-autocomplete"]'
    probe = _measure(
        client, f"/admin/models/{models['small'].id}/capabilities", width, tmp_path,
        f"{card} .card__title, {card} .checkbox, {card} .field__label, {card} .field__hint, "
        f"{card} .btn",
        f"{card} .btn, {card} .checkbox",
    )
    boxes = _inside(probe, width)
    whats = " ".join(b["what"] for b in boxes)
    for wanted in ("card__title", "checkbox", "field__hint", "btn"):
        assert wanted in whats, boxes
    # The default is the long-named model: said in a hint that wraps.
    assert any(LONG[:20] in b["text"] or "default is" in b["text"] for b in boxes), boxes
    if width < 500:
        assert all(tap["h"] >= TAP for tap in probe["taps"]), probe["taps"]
