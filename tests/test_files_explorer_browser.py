"""The explorer's selection, batch bar and folder picker, in a real Chromium.

What only a browser shows: that the batch bar stands where the tools stood
without moving the list, that the selection boxes are one column under the
head row's, that everything fits a phone (390x844) with controls a thumb can
hit and no sideways scroll, and that Move goes through the folder picker --
a branch opened in it is fetched, a folder chosen, the row moved in place.

Slow (a server and a browser): run it through `heavy-guard`, by itself.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import time
from urllib.parse import quote

import pytest
from sqlalchemy import select

from lembas.db.models import User
from lembas.services import fs
from tests.test_show_in_chat_browser import Site, _browser_class, _wait

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
REQUIRED = bool(os.environ.get("CI") or os.environ.get("LEMBAS_REQUIRE_BROWSER"))
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to look in"),
]
TAP = 40
LONG = "a very long name for a file that somebody really did not want to shorten at all.md"


@pytest.fixture
def rig(client, db, registered):
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    home = fs.ensure_home(db, owner).path
    view = fs.view(db, owner)
    for name in ("a.md", "b.md", LONG):
        fs.write(view, f"{home}/{name}", b"# x")
    fs.mkdir(view, f"{home}/into/deeper")
    db.commit()

    from lembas.main import app

    site = Site(app)
    browser = None
    try:
        browser = _browser_class()(width=1280, height=900)
        browser.call("Network.setCookie", name="lembas_session",
                     value=client.cookies.get("lembas_session"), url=site.base)
        yield {"site": site, "browser": browser, "home": home,
               "url": f"{site.base}/files?path={quote(home)}"}
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.close()
        site.close()


def _open(rig, width: int, height: int) -> None:
    browser = rig["browser"]
    browser.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                 deviceScaleFactor=1, mobile=width < 600)
    browser.call("Page.navigate", url=rig["url"])
    browser.wait_for("document.readyState === 'complete'", timeout=30)
    browser.js(
        "(function(){var b=[].slice.call(document.querySelectorAll('dialog[open] button'))"
        ".find(function(e){return /Not now/.test(e.textContent)}); if(b) b.click();})()"
    )
    time.sleep(0.8)


LOOK = """JSON.stringify((function () {
  function box(e) { var r = e.getBoundingClientRect();
    return {l: Math.round(r.left * 2) / 2, r: Math.round(r.right * 2) / 2,
            t: Math.round(r.top * 2) / 2, w: Math.round(r.width), h: Math.round(r.height)}; }
  var bar = document.querySelector('.fx__bar'), batch = document.querySelector('[data-batch-bar]');
  return {
    width: window.innerWidth, page: document.documentElement.scrollWidth,
    bar: box(bar), batchShown: !batch.hidden && batch.getBoundingClientRect().width > 0,
    count: batch.querySelector('[data-batch-count]').textContent,
    buttons: [].map.call(batch.querySelectorAll('button'), box),
    tools: [].filter.call(document.querySelectorAll('.fx__tools > :not(.fx__batch)'),
      function (e) { return getComputedStyle(e).display !== 'none'; }).length,
    head: box(document.querySelector('.file-row--head .file-row__pick')),
    picks: [].map.call(
      document.querySelectorAll('.file-row:not(.file-row--head) .file-row__pick'), box),
    rows: [].map.call(document.querySelectorAll('.file-row:not(.file-row--head)'), box),
    actions: [].map.call(document.querySelectorAll('.file-row__actions'), box),
    all: document.querySelector('[data-select-all]').indeterminate
  };
})())"""


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_selecting_swaps_the_tools_for_the_batch_bar_and_moves_nothing(rig, width, height):
    browser = rig["browser"]
    _open(rig, width, height)
    before = json.loads(browser.js(LOOK))
    assert before["page"] <= before["width"], before
    assert not before["batchShown"] and before["tools"] >= 1
    # One column of boxes, under the head row's, every one the same square.
    assert len(before["picks"]) == 5
    assert {p["l"] for p in before["picks"]} == {before["head"]["l"]}
    square = (before["head"]["w"], before["head"]["h"])
    assert {(p["w"], p["h"]) for p in before["picks"]} == {square}
    # The long name gives; the row's menu stays inside the window.
    assert all(a["r"] <= before["width"] for a in before["actions"])

    browser.js("document.querySelectorAll('input[data-select]')[2].click()")
    browser.js("document.querySelectorAll('input[data-select]')[3].click()")
    time.sleep(0.3)
    after = json.loads(browser.js(LOOK))
    assert after["batchShown"] and after["tools"] == 0 and after["all"] is True
    assert after["count"] == "2 selected"
    assert after["page"] <= after["width"], after
    # The bar is as tall as it was, so the list did not move.
    assert after["bar"]["h"] == before["bar"]["h"]
    assert [r["t"] for r in after["rows"]] == [r["t"] for r in before["rows"]]
    assert len(after["buttons"]) == 5
    for button in after["buttons"]:
        assert after["bar"]["l"] <= button["l"] and button["r"] <= after["bar"]["r"], after
    # One line, in order, none on top of another.
    lefts = [b["l"] for b in after["buttons"]]
    assert lefts == sorted(lefts) and len({b["t"] for b in after["buttons"]}) == 1
    pairs = zip(after["buttons"], after["buttons"][1:], strict=False)
    assert all(a["r"] <= b["l"] for a, b in pairs)
    if width < 600:
        assert all(b["h"] >= TAP and b["w"] >= TAP for b in after["buttons"]), after
        assert all(p["h"] >= TAP and p["w"] >= TAP for p in after["picks"]), after

    browser.js("document.querySelector('[data-batch=\"clear\"]').click()")
    time.sleep(0.2)
    cleared = json.loads(browser.js(LOOK))
    assert not cleared["batchShown"] and cleared["tools"] >= 1


PICKER = """JSON.stringify((function () {
  var d = document.querySelector('dialog.fpick-dialog[open]');
  if (!d) return {open: false};
  var r = d.getBoundingClientRect();
  return {open: true, l: r.left, r: r.right, t: r.top, b: r.bottom,
          dirs: [].map.call(d.querySelectorAll('[data-pick-dir]'),
                            function (e) { return e.getAttribute('data-pick-dir'); }),
          heights: [].map.call(d.querySelectorAll('[data-pick-dir]'),
            function (e) { return Math.round(e.getBoundingClientRect().height); }),
          accept: d.querySelector('[data-testid="folder-accept"]').disabled,
          width: window.innerWidth, height: window.innerHeight,
          page: document.documentElement.scrollWidth};
})())"""


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_move_goes_through_the_folder_picker(rig, width, height):
    browser, home = rig["browser"], rig["home"]
    _open(rig, width, height)
    browser.js("document.querySelectorAll('input[data-select]')[2].click()")
    browser.js("document.querySelectorAll('input[data-select]')[3].click()")
    browser.js("document.querySelector('[data-batch=\"move\"]').click()")
    _wait(lambda: len(json.loads(browser.js(PICKER)).get("dirs", [])) >= 2)
    picker = json.loads(browser.js(PICKER))
    # The dialog is inside the window, and nothing can be confirmed yet.
    assert picker["l"] >= 0 and picker["r"] <= picker["width"], picker
    assert picker["t"] >= 0 and picker["b"] <= picker["height"], picker
    assert picker["accept"] is True and picker["page"] <= picker["width"]
    assert f"{home}/into" in picker["dirs"] and f"{home}/into/deeper" not in picker["dirs"]
    if width < 600:
        assert all(h >= TAP for h in picker["heights"]), picker
    # A branch is fetched when it is opened.
    browser.js(
        f"document.querySelector('dialog [data-pick-dir=\"{home}/into\"]')"
        ".closest('details').open = true"
    )
    _wait(lambda: f"{home}/into/deeper" in json.loads(browser.js(PICKER))["dirs"])
    assert rig["site"].count("/files/dirs", "pick=1") == 1
    browser.js(f"document.querySelector('dialog [data-pick-dir=\"{home}/into\"]').click()")
    assert json.loads(browser.js(PICKER))["accept"] is False
    browser.js("document.querySelector('[data-testid=\"folder-accept\"]').click()")
    _wait(lambda: browser.js(
        "document.querySelectorAll('.file-row:not(.file-row--head)').length") == 3)
    names = browser.js(
        "[].map.call(document.querySelectorAll('.file-row__label'),"
        "function (e) { return e.textContent; }).join('|')"
    )
    # The two selected (the long name and a.md, by the list's order) went.
    assert names.split("|") == ["into", "skills", "b.md"]
    assert rig["site"].count("/files/batch") == 1
    # Saved in place: the same page, and the selection is gone with the rows.
    assert not json.loads(browser.js(LOOK))["batchShown"]
