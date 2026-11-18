"""Connections, Models, Media and the pages of one item, measured in a real
Chromium at 1280x900 and at 390x844.

What a suite can hold of "lines up by construction": the page never scrolls
sideways, nothing named lies outside the window, the columns of a list start
at one x on every line, the tab bar is one line (it scrolls, it does not
wrap), and what a thumb has to hit is at least 40 px.

With `LEMBAS_SHOTS_DIR` set, each page measured is also saved there as a PNG
at both widths, for a person to look at.

Slow (a browser per page): run it through `heavy-guard`, by itself.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from lembas.db.models import Connection, Model
from lembas.services.media import catalog, instance
from tests.test_reports_layout import CHROMIUM, REQUIRED, _measure
from tests.test_unified_connections import _older_release

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to measure in"),
]

SHOTS = os.environ.get("LEMBAS_SHOTS_DIR", "")
TABS = ".tabs__bar--page .tabs__tab"


@pytest.fixture(autouse=True)
def _a_browser_is_there():
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")


@pytest.fixture
def pages(client, db, registered) -> dict[str, tuple[str, str, str]]:
    """Made-up data, and for each page: its address, what to measure, and
    what a thumb presses."""
    ids = _older_release(db)
    instance.carry(db)
    rows = {row.name: row for row in db.scalars(select(Connection))}
    cloud = rows["Cloud"]
    cloud.name = "A provider with a rather long name for one line"
    for number in range(6):
        db.add(Model(connection_id=cloud.id, position=10 + number,
                     model_id=f"a-chat-model-with-a-long-identifier-2026-11-{number}",
                     capabilities_json={"tools": True, "vision": True}))
    db.commit()
    chat = db.scalar(select(Model).where(Model.connection_id == ids["plan"]))
    line = ".catalog__mark, .catalog__main, .catalog__state"
    return {
        "connections": ("/admin/connections", line, ".btn--primary"),
        "connection-new": ("/admin/connections/new", ".type-card", ".type-card"),
        "connection-comfyui": (f"/admin/connections/{rows['ComfyUI'].id}",
                               f"{TABS}, .item-head, .input, .select", TABS),
        "connection-comfyui-models": (f"/admin/connections/{rows['ComfyUI'].id}/models",
                                      f"{TABS}, {line}", ".onoff"),
        "connection-media": (f"/admin/connections/{cloud.id}/media",
                             f"{TABS}, .catalog__main, .checkbox", TABS),
        "models": ("/admin/models", line, ".onoff"),
        "model-chat": (f"/admin/models/{chat.id}", f"{TABS}, .input, .textarea", TABS),
        "model-chat-limits": (f"/admin/models/{chat.id}/limits", f"{TABS}, .input, .select", TABS),
        "model-speech": (
            catalog.media_href(rows["Text to speech"].id, "tts", "kokoro") + "/options",
            f"{TABS}, .field-grid > .field > .field__label, .field-grid > .field > .input, "
            ".field-grid > .field > .select", TABS,
        ),
        "media-image": ("/admin/media/image", f"{TABS}, .select, .input", TABS),
        "media-tts": ("/admin/media/tts", f"{TABS}, .select", TABS),
    }


def _shoot(tmp: Path, name: str, width: int) -> None:
    """The page `_measure` just wrote, as a PNG: tall, so that more of a long
    page than one screen is seen. A phone's width is below what a headless
    window can be, so the page is framed at its width and the frame is shot."""
    found = sorted(tmp.glob(f"frame-*-{width}.html"), key=lambda p: p.stat().st_mtime)
    height = 1500 if width >= 1280 else 1900
    frame = tmp / f"shot-{name}-{width}.html"
    frame.write_text(
        found[-1].read_text(encoding="utf-8").replace("height:900px", f"height:{height}px"),
        encoding="utf-8",
    )
    out = Path(SHOTS)
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{name}-{width}.png"
    subprocess.run(
        [CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
         "--allow-file-access-from-files", f"--window-size={max(width, 520)},{height}",
         "--virtual-time-budget=8000", f"--screenshot={target}", f"file://{frame.resolve()}"],
        capture_output=True, timeout=120, check=False,
    )


NAMES = ("connections", "connection-new", "connection-comfyui", "connection-comfyui-models",
         "connection-media", "models", "model-chat", "model-chat-limits", "model-speech",
         "media-image", "media-tts")


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("width", [1280, 390])
def test_the_page_fits_and_lines_up(client, db, pages, tmp_path, width, name):
    path, named, taps = pages[name]
    probe = _measure(client, path, width, tmp_path, named, taps)
    if SHOTS:
        _shoot(tmp_path, name, width)
    assert probe["page"] <= probe["width"], f"{path} scrolls sideways at {width}"
    boxes = probe["named"]
    assert boxes, f"nothing named was found on {path}"
    tabs = [b for b in boxes if "tabs__tab" in b["what"]]
    rest = [b for b in boxes if b not in tabs]
    # The tab bar may run past the window (it scrolls); nothing else may.
    assert all(b["left"] >= 0 and b["right"] <= probe["width"] + 1 for b in rest), rest
    if tabs:
        assert len({b["top"] for b in tabs}) == 1, "the tab bar wrapped"
        # Exactly one tab is marked as the one being shown, on every page
        # that has link tabs -- the Media pages once marked none.
        active = [b for b in tabs if "is-active" in b["what"]]
        assert len(active) == 1, f"{path}: {len(active)} active tabs"
        if name.startswith("media-"):
            wanted = {"media-image": "Pictures", "media-tts": "Text to speech"}[name]
            assert active[0]["text"] == wanted, active
    if width < 500:
        assert all(tap["h"] >= 40 for tap in probe["taps"]), probe["taps"]
    if name in ("connections", "models", "connection-comfyui-models") and width >= 1280:
        # One grid: every line's mark, name and state start where every
        # other line's do.
        for cls in ("catalog__mark", "catalog__main"):
            lefts = {b["left"] for b in boxes if cls in b["what"]}
            assert len(lefts) == 1, f"{cls} starts at {sorted(lefts)}"
        rights = {b["right"] for b in boxes if "catalog__state" in b["what"]}
        assert len(rights) == 1, f"the state column ends at {sorted(rights)}"
    if name == "model-speech" and width >= 1280:
        # Fields in one row of the grid: labels end on one line, controls
        # start on one line, whatever the hints say.
        labels = [b for b in boxes if "field__label" in b["what"]]
        row = [b for b in labels if b["top"] == labels[0]["top"]]
        assert len(row) >= 2 and len({b["bottom"] for b in row}) == 1, row


def test_the_names_cover_the_pages(pages):
    assert set(NAMES) == set(pages)
    assert all(re.match(r"^/admin/", path) for path, _, _ in pages.values())
