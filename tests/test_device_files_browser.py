"""A linked computer's pages of Files, measured in a real Chromium at 1280x900
and at 390x844: the list is the explorer's own grid, so a long name is cut
and never pushes the size or the download out of line; the computers are rows
of that grid on the first page of Files, where a phone has no tree; the page
never scrolls sideways; and what a thumb presses is at least 40 px.

Slow (a browser per page): run it through `heavy-guard`, by itself."""

from __future__ import annotations

import pytest

from tests.test_device_files import ROOT, Computer
from tests.test_device_sync import _agents_on, _clean, _linked  # noqa: F401
from tests.test_reports_layout import CHROMIUM, REQUIRED, TAP, _measure

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to measure in"),
]

LONG = "a-file-with-a-name-that-does-not-wrap-anywhere-at-all-in-the-list-of-a-computer.py"


@pytest.fixture(autouse=True)
def _a_browser_is_there():
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")


@pytest.fixture
def rig(db, registered):
    computer = Computer()
    computer.files[f"{ROOT}/{LONG}"] = b"x = 1\n" * 2000
    link, _ = _linked(db, answer=computer)
    link.hello["limits"]["roots"] = [ROOT]
    link.hello["device"]["host"] = "a-workstation-with-a-long-name-of-its-own"
    link.initialized = {"_meta": {"lembas": {"capabilities": ["sessions", "fs"]}}}
    return f"/files/device/{link.token_id}"


@pytest.mark.parametrize("width", [1280, 390])
def test_a_computers_folder_fits_and_its_rows_line_up(client, rig, tmp_path, width):
    probe = _measure(
        client, f"{rig}?path={ROOT}", width, tmp_path,
        ".file-row:not(.file-row--head) > *, .file-crumbs, .files-upload__pick",
        # A whole row opens what it names (the link is stretched over it).
        ".file-row:not(.file-row--head), .file-row__actions .btn, .files-upload__pick",
    )
    assert probe["page"] <= probe["width"], f"the page scrolls sideways at {width}"
    boxes = probe["named"]
    assert all(b["left"] >= 0 and b["right"] <= probe["width"] + 1 for b in boxes), boxes
    names = [b for b in boxes if "file-row__name" in b["what"]]
    sizes = [b for b in boxes if "file-row__size" in b["what"]]
    actions = [b for b in boxes if "file-row__actions" in b["what"]]
    # Four rows; a folder's size and actions cells are there and empty, so
    # they have no box.
    assert len(names) == 4 and len(sizes) == len(actions) == 3, boxes
    # One grid: every row's size and its download start and end on the same
    # lines, whatever the name beside them holds.
    assert len({(b["left"], b["right"]) for b in sizes}) == 1, sizes
    assert len({(b["left"], b["right"]) for b in actions}) == 1, actions
    assert all(name["right"] <= sizes[0]["left"] + 1 for name in names), names
    if width < 500:
        assert all(tap["h"] >= TAP for tap in probe["taps"]), probe["taps"]


@pytest.mark.parametrize("width", [1280, 390])
def test_the_computers_are_rows_of_the_first_page_of_files(client, rig, tmp_path, width):
    probe = _measure(
        client, "/files", width, tmp_path,
        ".file-rows--places .file-row > *", ".file-rows--places .file-row",
    )
    assert probe["page"] <= probe["width"], f"the page scrolls sideways at {width}"
    boxes = probe["named"]
    assert any("file-row__name" in b["what"] for b in boxes), boxes
    assert all(b["left"] >= 0 and b["right"] <= probe["width"] + 1 for b in boxes), boxes
    if width < 500:
        assert all(tap["h"] >= TAP for tap in probe["taps"]), probe["taps"]


@pytest.mark.parametrize("width", [1280, 390])
def test_a_computers_file_fits(client, rig, tmp_path, width):
    probe = _measure(
        client, f"{rig}/view?path={ROOT}/{LONG}", width, tmp_path,
        ".fx-file__bar > *, .fx-file__tools > *, .file-crumbs", ".fx-file__tools > *",
    )
    assert probe["page"] <= probe["width"], f"the page scrolls sideways at {width}"
    boxes = probe["named"]
    assert all(b["left"] >= 0 and b["right"] <= probe["width"] + 1 for b in boxes), boxes
    assert any("editor-save" in b["what"] for b in boxes), boxes
