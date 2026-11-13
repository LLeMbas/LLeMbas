"""A shown file and a page of an answer in the conversation, in a real Chromium:
opened, opened again, drawn anew, and measured.

`show` used to leave a card that opened the file in a window beside the
conversation. Whether the file was then seen depended on being there: the
window opened by itself only when the card arrived while the page was open on
a wide screen -- and, because a card in a reply being written had no link and
another key, only once the reply had ended. A page opened later, or on a
phone, had a card and nothing drawn. Now an HTML file is drawn in the reply
(`chat/_tool_activity.html`), and this file holds that it is:

- **there every time**: on a load, on the next load, after the reply around
  it was drawn anew (what a stream's last frame does), with a request for the
  frame each time;
- **not run unasked**: a page with scripts is not fetched until Run page is
  pressed, and then it is, and says its height;
- **on the reply's column**: the same left and right edge as a tool line, a
  compact card and a page of an answer, at 1280 px and at 390x844, with no
  sideways scroll and every control a thumb can hit;
- and that the service worker keeps none of it.

Slow (a server and a browser): run it through `heavy-guard`, by itself.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
import socket
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Message, Model, User
from lembas.services import fs, html_pages, settings_store
from lembas.services.tools import files as files_tools

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
REQUIRED = bool(os.environ.get("CI") or os.environ.get("LEMBAS_REQUIRE_BROWSER"))
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to look in"),
]

SCRIPTED = (
    "<!doctype html><html><head><title>Demo</title></head><body><h1 id='h'>Demo</h1>"
    "<canvas id='c' width='200' height='80'></canvas><button id='b'>Click</button>"
    "<script>document.getElementById('h').textContent='ran';</script>"
    + "<p>line</p>" * 80 + "</body></html>"
)
STILL = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Plain</title>'
    "<style>h1{color:#246}</style></head><body><h1>Still page</h1></body></html>"
)
PAGE = (
    "A chart.\n\n```html-view Harvest by grove\n<!doctype html><html><body><h1>Harvest</h1>"
    "</body></html>\n```\n\nThat is it.\n"
)
TAP = 40


@pytest.fixture(autouse=True)
def _a_browser_is_there():
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")


def _browser_class():
    """`scripts/perf_measure.py`'s DevTools driver. The script sets up an
    instance of its own when imported; the environment is put back."""
    before = dict(os.environ)
    try:
        path = Path(__file__).resolve().parent.parent / "scripts" / "perf_measure.py"
        spec = importlib.util.spec_from_file_location("perf_measure", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        os.environ.clear()
        os.environ.update(before)
    return module.Browser


class Site:
    """The application on a loopback port for the length of one test, counting
    what is asked of it."""

    def __init__(self, app) -> None:
        import uvicorn

        self.asked: list[str] = []
        asked = self.asked

        async def counted(scope, receive, send):
            if scope.get("type") == "http":
                query = scope.get("query_string", b"").decode()
                asked.append(scope.get("path", "") + ("?" + query if query else ""))
            await app(scope, receive, send)

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        # The test's own client has run the application's start-up already.
        self.server = uvicorn.Server(
            uvicorn.Config(counted, host="127.0.0.1", port=self.port, log_level="warning",
                           lifespan="off")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            assert time.monotonic() < deadline, "the server never started"
            time.sleep(0.05)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def count(self, *pieces: str) -> int:
        return sum(1 for url in self.asked if all(piece in url for piece in pieces))

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def rig(client, db, registered):
    """A chat with a tool line, a scripted HTML file, a still one, a Markdown
    file and a page of an answer; the server; a browser signed in."""
    settings_store.update(db, {"html_pages_enabled": True})
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    connection = Connection(name="local", base_url="http://upstream.test/v1", api_key_encrypted="")
    db.add(connection)
    db.flush()
    db.add(Model(connection_id=connection.id, model_id="m", display_name="M"))
    home = fs.ensure_home(db, owner).path
    view = fs.view(db, owner)
    cards = {}
    for name, text in (("demo.html", SCRIPTED), ("plain.html", STILL), ("notes.md", "# Notes\n")):
        node = fs.write(view, f"{home}/{name}", text.encode())
        db.flush()
        kind, language = files_tools.show_kind(node.path)
        card = {
            "path": node.path, "name": node.name, "kind": kind, "language": language,
            "line": None, "node_id": node.id,
            "version_id": fs.versions(view, node.path)[0].id, "sha": node.blob_hash,
            "size": len(text), "mime": node.mime or "text/plain",
        }
        if kind == "html":
            card["still"] = html_pages.shown_still(text)
        cards[name] = card
    assert cards["plain.html"]["still"] is True and cards["demo.html"]["still"] is False
    chat = Chat(user_id=owner.id, model_id="m", connection_id=connection.id, title="Shown",
                title_generated=True)
    db.add(chat)
    db.flush()
    events = [{"name": "read", "status": "ok", "path": cards["notes.md"]["path"],
               "query": cards["notes.md"]["path"]}]
    events += [{"name": "show", "status": "ok", "path": card["path"], "query": card["path"],
                "show": card} for card in cards.values()]
    db.add(Message(chat_id=chat.id, role="user", content="Show me."))
    reply = Message(chat_id=chat.id, role="assistant", content="Shown.", model_id="m",
                    connection_id=connection.id, tool_calls_json=events, complete=True)
    db.add(reply)
    db.add(Message(chat_id=chat.id, role="user", content="And a page."))
    db.add(Message(chat_id=chat.id, role="assistant", content=PAGE, model_id="m",
                   connection_id=connection.id, html_pages=True, complete=True))
    db.commit()

    from lembas.main import app

    site = Site(app)
    browser = None
    try:
        browser = _browser_class()(width=1280, height=900)
        browser.call("Network.setCookie", name="lembas_session",
                     value=client.cookies.get("lembas_session"), url=site.base)
        yield {"site": site, "browser": browser, "chat": chat.id, "reply": reply.id,
               "url": f"{site.base}/chat/{chat.id}"}
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.close()
        site.close()


def _open(rig, url: str | None = None) -> None:
    browser = rig["browser"]
    browser.call("Page.navigate", url=url or rig["url"])
    browser.wait_for("document.readyState === 'complete'", timeout=30)
    # The first visit's "turn notifications on?" is not what is looked at.
    browser.js(
        "(function(){var b=[].slice.call(document.querySelectorAll('dialog[open] button'))"
        ".find(function(e){return /Not now/.test(e.textContent)}); if(b) b.click();})()"
    )
    _settle(browser)


def _settle(browser, seconds: float = 0.8) -> None:
    time.sleep(seconds)


def _wait(what, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if what():
            return
        time.sleep(0.1)
    raise AssertionError("timed out waiting")


STATE = """JSON.stringify({
  blocks: [].map.call(document.querySelectorAll('[data-show-inline]'), function (b) {
    var f = b.querySelector('iframe');
    return {mode: b.getAttribute('data-mode'), frame: !!f,
            src: f ? f.getAttribute('src') : '', sandbox: f ? f.getAttribute('sandbox') : null,
            height: f ? Math.round(f.getBoundingClientRect().height) : 0,
            told: f ? f.style.getPropertyValue('--shown-h') : ''};
  }),
  cards: document.querySelectorAll('[data-testid="show-card"]').length,
  pages: [].map.call(document.querySelectorAll('[data-html-page]'), function (m) {
    return [m.getAttribute('data-ready'), !!(m.querySelector('iframe').getAttribute('src'))];
  }),
  viewer: !!document.querySelector('.viewer-dialog[open]'),
  viewerFilled: !!document.querySelector('.viewer-dialog[open] [data-testid="file-viewer"]')
})"""

EDGES = """JSON.stringify({
  width: window.innerWidth, page: document.documentElement.scrollWidth,
  rows: [].map.call(document.querySelectorAll(
      '.tool-line, [data-testid="show-card"], [data-show-inline], .html-page'), function (e) {
    var r = e.getBoundingClientRect();
    return [e.className.toString().split(' ')[0], Math.round(r.left * 2) / 2,
            Math.round(r.right * 2) / 2];
  }),
  frames: [].map.call(document.querySelectorAll('.show-inline__frame'), function (e) {
    var r = e.getBoundingClientRect(), p = e.closest('[data-show-inline]').getBoundingClientRect();
    return [Math.round(r.width), Math.round(p.width), Math.round(r.height)];
  }),
  taps: [].map.call(document.querySelectorAll(
      '.show-card__open, [data-show-run]'), function (e) {
    return Math.round(e.getBoundingClientRect().height);
  })
})"""


def _state(rig) -> dict:
    return json.loads(rig["browser"].js(STATE))


def _edges(rig) -> dict:
    return json.loads(rig["browser"].js(EDGES))


def test_a_shown_page_is_there_on_every_load_and_runs_only_when_asked(rig):
    site, browser = rig["site"], rig["browser"]
    for visit in (1, 2):
        _open(rig)
        state = _state(rig)
        run, still = state["blocks"]
        # The still page: in its frame, without script, asked for on this load.
        assert still == {**still, "mode": "still", "frame": True, "sandbox": ""}
        assert "still=1" in still["src"]
        assert site.count("/show/frame", "still=1") == visit
        # The scripted one: named, not fetched.
        assert run["mode"] == "run" and not run["frame"]
        assert site.count("/show/frame", "inline=1") == 0
        # The Markdown file keeps its card; nothing opened by itself.
        assert state["cards"] == 1 and not state["viewer"]
        # The page of the answer is in its frame too.
        assert state["pages"] == [["frame", True]]
        assert site.count("/pages/m/") == visit

    browser.js("document.querySelector('[data-show-run]').click()")
    _wait(lambda: site.count("/show/frame", "inline=1") == 1)
    _wait(lambda: _state(rig)["blocks"][0]["told"] != "")
    run = _state(rig)["blocks"][0]
    assert run["mode"] == "running" and run["sandbox"] == "allow-scripts"
    # It said how tall it is (eighty lines: more than the most it is given),
    # and the style sheet kept that inside its bounds.
    assert float(run["told"].removesuffix("px")) > run["height"] >= 100

    # The reply is drawn anew, as a stream's last frame does: the page its
    # person ran is run again, the still one is there again, and the Markdown
    # card -- seen already -- opens nothing.
    bubble = f"#msg-{rig['reply']}"
    browser.js(
        f"htmx.ajax('GET', location.href, {{target: '{bubble}', select: '{bubble}',"
        " swap: 'outerHTML'})"
    )
    _wait(lambda: site.count("/show/frame", "inline=1") == 2)
    _wait(lambda: site.count("/show/frame", "still=1") == 3)
    state = _state(rig)
    assert [b["mode"] for b in state["blocks"]] == ["running", "still"]
    assert all(b["frame"] for b in state["blocks"]) and not state["viewer"]

    # A new load asks again before running anything.
    _open(rig)
    assert _state(rig)["blocks"][0]["mode"] == "run"
    assert site.count("/show/frame", "inline=1") == 2


def test_the_viewer_opens_every_time_and_the_worker_keeps_none_of_it(rig):
    site, browser = rig["site"], rig["browser"]
    _open(rig)
    # A second load, so the page is one the service worker controls.
    browser.wait_for(
        "navigator.serviceWorker.ready.then(function () { return true; })", timeout=30
    )
    _open(rig)
    for attempt in (1, 2, 3):
        browser.js(
            "document.querySelector('[data-testid=\"show-card\"] a[data-open-viewer]').click()"
        )
        _wait(lambda: _state(rig)["viewerFilled"])
        assert "Notes" in browser.js("document.querySelector('.viewer-dialog').textContent")
        # Asked of the server each time, not handed back from a cache.
        _wait(lambda n=attempt: site.count("/show?node=") >= n)
        browser.js("document.querySelector('.viewer-dialog__close').click()")
        _wait(lambda: not _state(rig)["viewer"])
    # The HTML file opens in the viewer as well, on its person's click.
    browser.js("document.querySelector('[data-show-inline] a[data-open-viewer]').click()")
    _wait(lambda: browser.js(
        "!!document.querySelector('.viewer-dialog[open] [data-testid=\"viewer-frame\"]')"
    ))
    kept = json.loads(browser.js(
        "caches.keys().then(function (names) {"
        "  return Promise.all(names.map(function (n) {"
        "    return caches.open(n).then(function (c) { return c.keys(); }); })); })"
        ".then(function (all) { return JSON.stringify([].concat.apply([], all).map("
        "  function (r) { return new URL(r.url).pathname; })); })"
    ))
    assert kept, "the worker cached nothing at all: it was not running"
    assert [path for path in kept if not path.startswith("/static/") and path != "/offline"] == []


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_the_card_and_the_page_stand_on_the_replys_column(rig, width, height):
    browser = rig["browser"]
    browser.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                 deviceScaleFactor=1, mobile=width < 500)
    _open(rig)
    browser.js("document.querySelector('[data-show-run]').click()")
    _wait(lambda: _state(rig)["blocks"][0]["frame"])
    _settle(browser)
    edges = _edges(rig)
    assert edges["width"] == width and edges["page"] == width  # no sideways scroll
    kinds = [row[0] for row in edges["rows"]]
    assert kinds == ["tool-line", "show-inline", "show-inline", "show-card", "html-page"]
    line = edges["rows"][0]
    for row in edges["rows"]:
        assert row[1:] == line[1:], f"{row} is not on the column of {line} at {width}px"
    # A frame is as wide as its block (less the border), and has a height.
    for frame_width, block_width, frame_height in edges["frames"]:
        assert block_width - 4 <= frame_width <= block_width and frame_height >= 100
    assert edges["taps"] and all(tap >= TAP for tap in edges["taps"])
