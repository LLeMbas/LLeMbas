"""The pages of 2.0.0-beta.12 at a phone's width and a desktop's, measured.

Reports (the list, one report with a page in it), the schedule form's "Notify
when", a task's history, the sidebar's two new entries and a chat with a page
mount -- each rendered in a real Chromium at 390 and 1280 px and asked three
things: does the page scroll sideways, does anything named lie outside the
viewport or get cut off, and is everything a thumb presses at least 40 px
tall where the screen is a phone's.

Slow (a browser per page): run it through `heavy-guard`, by itself.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Message, Model, ScheduleRun, User
from lembas.services import reports as reports_service
from lembas.services import schedules as schedules_service
from lembas.services import settings_store
from tests.test_prefix import _chat

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
# Without a browser this file measures nothing. On a developer's machine that
# is a skip; where it is run to be relied on -- CI, a release (`CI` or
# `LEMBAS_REQUIRE_BROWSER` set) -- it is a failure.
REQUIRED = bool(os.environ.get("CI") or os.environ.get("LEMBAS_REQUIRE_BROWSER"))
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to measure in"),
]


@pytest.fixture(autouse=True)
def _a_browser_is_there():
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")

LONG = "The eastern grove of mallorn trees is three weeks late this year and nobody knows why"
PAGE = "```html-view Harvest by grove, with a rather long title for a narrow bar\n<p>x</p>\n```\n"
TABLE = "| Grove | Late |\n|---|---|\n| East | 3 |\n"
TAP = 40

PROBE = """
<style>*, *::before, *::after { transition: none !important; animation: none !important; }</style>
<script>
window.addEventListener('load', function () {
  setTimeout(function () {
    function shown(el) {
      var r = el.getBoundingClientRect(), s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    }
    function box(el) {
      var r = el.getBoundingClientRect();
      return {what: (el.getAttribute('data-testid') || el.className.toString() || el.tagName)
                      .slice(0, 60),
              text: (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 50),
              left: Math.round(r.left), right: Math.round(r.right), h: Math.round(r.height),
              /* A field whose value is longer than itself scrolls: that is a field. */
              cut: el.tagName !== 'INPUT' && el.scrollWidth > el.clientWidth + 1 &&
                   getComputedStyle(el).textOverflow !== 'ellipsis' &&
                   getComputedStyle(el).overflowX !== 'auto'};
    }
    function all(sel) {
      return Array.prototype.filter.call(document.querySelectorAll(sel), shown).map(box);
    }
    window.__probe = {
      width: window.innerWidth,
      page: document.documentElement.scrollWidth,
      named: all(NAMED), taps: all(TAPS),
    };
  }, 600);
});
</script>
"""


def _measure(client: TestClient, path: str, width: int, tmp: Path, named: str, taps: str) -> dict:
    spec = importlib.util.spec_from_file_location(
        "shoot", Path(__file__).resolve().parent.parent / "scripts/shoot.py"
    )
    shoot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shoot)
    response = client.get(path)
    assert response.status_code == 200, path
    assets = tmp / "assets"
    assets.mkdir(exist_ok=True)
    consts = f"<script>var NAMED = {json.dumps(named)}, TAPS = {json.dumps(taps)};</script>"
    html = shoot.rewrite(response.text, client, assets).replace(
        "</body>", consts + PROBE + "</body>", 1
    )
    name = re.sub(r"\W+", "-", path).strip("-")
    page = tmp / f"{name}-{width}.html"
    page.write_text(html, encoding="utf-8")
    frame = tmp / f"frame-{name}-{width}.html"
    frame.write_text(
        "<!doctype html><meta charset=utf-8><style>html,body{margin:0}"
        f"iframe{{width:{width}px;height:900px;border:0;display:block}}</style>"
        f'<iframe id="f" src="{page.name}"></iframe><pre id="out"></pre><script>'
        "window.addEventListener('load',function(){setTimeout(function(){"
        "document.getElementById('out').textContent="
        "JSON.stringify(document.getElementById('f').contentWindow.__probe||null);"
        "},2500);});</script>",
        encoding="utf-8",
    )
    dom = subprocess.run(
        [CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu",
         "--allow-file-access-from-files", f"--window-size={max(width, 520)},940",
         "--virtual-time-budget=8000", "--dump-dom", f"file://{frame.resolve()}"],
        capture_output=True, text=True, timeout=120,
    ).stdout
    found = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    assert found and found.group(1).strip() not in ("", "null"), "the probe did not report"
    import html as html_module

    return json.loads(html_module.unescape(found.group(1)))


@pytest.fixture
def seeded(client: TestClient, db, registered) -> dict:
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    chat = _chat(db, owner.id, title="Harvest")
    settings_store.update(db, {"html_pages_enabled": True})
    for model in db.scalars(select(Model)):
        model.html_pages = True
    schedule = schedules_service.create(
        db, owner=owner, title=LONG, instruction="Look at the groves.",
        rule={"at": {"times": ["09:00"]}},
        settings={"notify": "when", "notify_when": LONG},
    )
    run = ScheduleRun(schedule_id=schedule.id, user_id=owner.id, status="ok",
                      chat_id=schedule.chat_id or "", model_label="A model with a long name",
                      note=LONG, cost_micro=12000, currency="EUR")
    quiet = ScheduleRun(schedule_id=schedule.id, user_id=owner.id, status="ok", note=LONG)
    db.add_all([run, quiet])
    db.commit()
    report = reports_service.file(
        db, owner_id=owner.id, schedule=schedule, run=run, model_id="a-model-with-a-long-id",
        report={"title": LONG, "summary": LONG + ". " + LONG, "importance": "high",
                "body": f"## Groves\n\n{LONG}.\n\n{PAGE}\n{TABLE}"},
    )
    db.add(Message(chat_id=chat.id, role="user", content="Show me."))
    db.add(Message(chat_id=chat.id, role="assistant", content=f"Here.\n\n{PAGE}", complete=True,
                   html_pages=True))
    report.html_pages = True
    db.commit()
    reports_service.reply_chat(db, owner, report)
    return {"chat": chat.id, "report": report.id, "schedule": schedule.id}


PAGES = {
    "list": ("/reports", ".reports-filter, .reports-list__link, .reports-list__title, "
                         ".reports-list__summary, .reports-list__meta",
             ".reports-list__link, .reports-filter .select, .reports-filter .checkbox"),
    "report": ("/reports/{report}", ".report, .report__meta, .report__summary, .html-page, "
                                    ".html-page__bar, .html-page__title, .html-page__larger, "
                                    ".report__body table, .page-header, .page-header .btn",
               ".page-header .btn, .html-page__larger, .report__continued .btn"),
    "form": ("/scheduled/{schedule}/edit", ".notify-when, .notify-when__choice, "
                                           ".notify-when__words, .notify-when .input",
             ".notify-when__choice, .notify-when .input"),
    "task": ("/scheduled/{schedule}", ".schedule-run, .schedule-run__head, "
                                      ".schedule-run__preview, .schedule-run .btn",
             ".schedule-run .btn"),
    "chat": ("/chat/{chat}", ".html-page, .html-page__bar, .html-page__title, "
                             ".html-page__larger, .html-page__source",
             ".html-page__larger"),
}


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("name", sorted(PAGES))
def test_nothing_is_cut_off_or_scrolls_sideways(client, seeded, tmp_path, name, width):
    path, named, taps = PAGES[name]
    got = _measure(client, path.format(**seeded), width, tmp_path, named, taps)
    assert got["named"], f"{name}: nothing named was drawn -- the test went blind"
    assert got["page"] <= got["width"] + 1, f"{name} at {width}: the page scrolls sideways"
    outside = [b for b in got["named"] if b["left"] < -1 or b["right"] > got["width"] + 1]
    assert outside == [], outside
    cut = [b for b in got["named"] if b["cut"]]
    assert cut == [], cut
    if width <= 480:
        small = [b for b in got["taps"] if b["h"] < TAP]
        assert small == [], small


@pytest.mark.parametrize("width", [390, 1280])
def test_the_sidebars_two_entries_share_a_row_and_fit(client, seeded, tmp_path, width):
    got = _measure(
        client, "/reports", width, tmp_path,
        ".sidebar__sections .nav-item", ".sidebar__sections .nav-item",
    )
    if not got["named"]:
        pytest.skip("the sidebar is a closed drawer at this width")
    assert len(got["named"]) == 2
    first, second = got["named"]
    assert first["right"] <= second["left"] + 1 and first["h"] == second["h"]
    assert all(not b["cut"] for b in got["named"])
