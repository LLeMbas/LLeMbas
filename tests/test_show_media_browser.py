"""The cards of files an agent showed, measured in a real Chromium at 1280x900
and at 390x844: a recording, a PDF in its sealed frame and a file with no
player are the generated media's card -- one column, the player on top and one
bar under it -- so the page never scrolls sideways, every bar's two buttons
end on one line whatever the file is called, and what a thumb presses is at
least 40 px.

Slow (a browser per page): run it through `heavy-guard`, by itself."""

from __future__ import annotations

import pytest

from tests.test_media_browser import _Unfed
from tests.test_media_jobs import world  # noqa: F401
from tests.test_reports_layout import CHROMIUM, REQUIRED, TAP, _measure
from tests.test_show_media import _media, _reply_with

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to measure in"),
]

LONG = "a-recording-with-a-name-that-does-not-wrap-anywhere-at-all-take-seventeen"
NAMED = ".media-card__bar > *, .media-card__audio, .media-card__model"
TAPS = ".media-card__bar > .btn"


@pytest.fixture(autouse=True)
def _a_browser_is_there():
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")


@pytest.mark.parametrize("width", [1280, 390])
def test_shown_files_fit_and_their_bars_line_up(client, db, world, tmp_path, width):  # noqa: F811
    chat_id = _reply_with(
        db, world,
        _media("audio", f"{LONG}.mp3"), _media("pdf", f"{LONG}.pdf"),
        _media("archive", f"{LONG}.zip"), _media("binary", "core.dump"),
    )
    probe = _measure(_Unfed(client), f"/chat/{chat_id}", width, tmp_path, NAMED, TAPS)
    assert probe["page"] <= probe["width"], f"the chat scrolls sideways at {width}"
    boxes = probe["named"]
    assert all(b["left"] >= 0 and b["right"] <= probe["width"] + 1 for b in boxes), boxes
    opens = [b for b in boxes if b["what"] == "media-open"]
    downloads = [b for b in boxes if b["what"] == "media-download"]
    names = [b for b in boxes if "media-card__name" in b["what"]]
    assert len(opens) == len(downloads) == len(names) == 4, boxes
    for name, first, second in zip(names, opens, downloads, strict=True):
        assert first["top"] == second["top"] and first["bottom"] == second["bottom"]
        assert name["right"] <= first["left"] < first["right"] <= second["left"]
    # The long names were cut, not the rows pushed: every bar ends at one x.
    assert len({b["right"] for b in downloads}) == 1, downloads
    players = [b for b in boxes if b["what"] in ("media-audio", "media-pdf")]
    assert len(players) == 2, boxes
    assert all(b["right"] - b["left"] >= 200 and b["h"] > 20 for b in players), players
    if width < 500:
        assert all(tap["h"] >= TAP for tap in probe["taps"]), probe["taps"]
