"""Where the chat stands while things happen to it, measured in a real Chromium.

The rule: **somebody at the bottom stays at the bottom; somebody who is not
sees the content they are looking at stay exactly where it is, whatever is
added above or below it.**

It has been broken, reported and reasoned about more than once, and reasoning
is what kept missing it: the position is the sum of a dozen things that each
change the page's height at their own moment (a frame of the stream, a round
closing, the live bubble replaced by the finished one, a title arriving, a
block opened, a picture or a player getting its size, a poll appending a turn,
the composer growing, the window changing). So this file does not reason. It
drives one scripted conversation through all of them, at 1280x900 and at
390x844, and records the scroller on **every painted frame**:

- `gap`: how far the view is from the bottom (`scrollHeight - scrollTop -
  clientHeight`), for the phases where the person is at the bottom;
- `drift`: how far a marked piece of content has moved on the screen since the
  phase began, for the phases where the person is somewhere else. The mark is
  found by its text, so it survives its node being replaced.

A frame is sampled right after it was painted, so a sample is what was on the
screen. One frame out of place is a visible jump and is counted.

`LEMBAS_SCROLL_REPORT=<file>` writes every measurement as JSON, which is how
the numbers in the wiki's Working notes were taken.

Slow (a server and a browser): run it through `heavy-guard`, by itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import queue
import shutil
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Message, Model, User
from lembas.services import chat as chat_service
from lembas.services import fs
from lembas.services import generation as generation_service
from lembas.services.media import library
from tests.test_media_jobs import MP3, MP4
from tests.test_show_in_chat_browser import Site, _browser_class

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
REQUIRED = bool(os.environ.get("CI") or os.environ.get("LEMBAS_REQUIRE_BROWSER"))
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to look in"),
]

# What counts as "did not move": a pixel, for layout that lands on half pixels.
GAP_MAX = 1.0
DRIFT_MAX = 1.0

SIZES = [(1280, 900), (390, 844)]


@pytest.fixture(autouse=True)
def _a_browser_is_there():
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")


# --- What the model says, said here ------------------------------------------------------
def paragraph(n: int) -> str:
    """A paragraph that can be found again by its number."""
    return (
        f"Mark{n:03d} is where this paragraph starts. It goes on for a few sentences so "
        "that it wraps on a phone and on a desk alike, and so that a screen holds only a "
        "handful of them. Nothing in it means anything.\n\n"
    )


CODE = (
    "```python\n"
    + "".join(f"def step_{i}(value):\n    return value + {i}\n" for i in range(12))
    + "```\n\n"
)
TABLE = (
    "| Grove | Leaves |\n|---|---|\n"
    + "".join(f"| Grove {i} | {i * 7} |\n" for i in range(6))
    + "\n"
)


def long_reply(first: int, count: int) -> str:
    parts = []
    for n in range(first, first + count):
        parts.append(paragraph(n))
        if n % 4 == 1:
            parts.append(CODE)
        if n % 5 == 2:
            parts.append(TABLE)
    return "".join(parts)


class FakeModel:
    """The chat's model, fed from the test: text a piece at a time, a tool
    call, the end of the reply. `generation.stream_chat` reads it."""

    def __init__(self) -> None:
        self.said: queue.Queue = queue.Queue()
        self.title = "Named by the model"
        self.title_after = 1.0
        self.calls = 0

    def say(self, text: str, *, piece: int = 48, pace: float = 0.012) -> None:
        for start in range(0, len(text), piece):
            self.said.put(text[start : start + piece])
            time.sleep(pace)

    def tool(self, name: str = "skills_list", arguments: str = "{}") -> None:
        self.said.put(("tool", name, arguments))

    def end(self) -> None:
        self.said.put(None)

    async def stream_chat(self, _endpoint, _payload, **_):
        self.calls += 1
        while True:
            try:
                item = self.said.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.005)
                continue
            if item is None:
                yield {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 400}}
                return
            if isinstance(item, tuple):
                _, name, arguments = item
                yield {"choices": [{"delta": {"tool_calls": [{
                    "index": 0, "id": f"call{self.calls}",
                    "function": {"name": name, "arguments": arguments},
                }]}}]}
                return
            yield {"choices": [{"delta": {"content": item}}]}

    async def generate_title(self, *_a, **_k) -> str:
        await asyncio.sleep(self.title_after)
        return self.title


# --- The page's sampler -------------------------------------------------------------------
SAMPLER = r"""
(function () {
  if (window.__lt) return;
  /* The first visit's "turn notifications on?" is a modal over everything. */
  try { window.localStorage.setItem("lembas-notifications-asked", "1"); } catch (error) {}
  var lt = window.__lt = { on: true, frames: {}, order: [], find: "", id: "" };
  function marked() {
    if (lt.id) return document.getElementById(lt.id);
    if (!lt.find) return null;
    var found = null;
    var all = document.querySelectorAll(
      "#thread p, #thread li, #thread summary, #thread .msg__body");
    for (var i = 0; i < all.length; i++) {
      if (all[i].textContent.indexOf(lt.find) !== -1) { found = all[i]; }
    }
    return found;
  }
  function sample(t) {
    if (!lt.on) return;
    var s = document.getElementById("thread-scroll");
    if (!s) return;
    var prompts = s.querySelectorAll(".msg--user:not(.msg--machine)");
    var last = prompts.length ? prompts[prompts.length - 1] : null;
    var m = marked();
    if (!(t in lt.frames)) lt.order.push(t);
    lt.frames[t] = {
      t: Math.round(t), top: s.scrollTop, h: s.scrollHeight, c: s.clientHeight,
      prompt: last ? last.getBoundingClientRect().top : null,
      promptId: last ? last.id : "",
      mark: m ? m.getBoundingClientRect().top : null,
      reading: s.classList.contains("is-reading")
    };
  }
  /* A frame is sampled once it has been painted: a message posted from the
     frame's animation callbacks is handled after the browser has laid the
     frame out, run its resize observers and painted it. So a sample is what
     was on the screen -- including what an observer put right before the
     paint, which a sample taken in the callbacks themselves would miss.

     Unless the page changed in between (a stream frame that arrived while
     the browser was painting is handled first): then what is read is not
     what was painted, and the frame is left out. */
  var changes = 0, posted = false, seen = 0, frame = 0;
  var channel = new MessageChannel();
  channel.port1.onmessage = function () {
    posted = false;
    if (changes === seen) sample(frame);
  };
  function watch() {
    var thread = document.getElementById("thread");
    if (!thread) return;
    new MutationObserver(function () { changes++; })
      .observe(thread, { childList: true, subtree: true, characterData: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watch);
  else watch();
  function painted(t) {
    frame = t;
    if (posted) return;
    posted = true;
    /* After this turn's microtasks: the observer above has counted whatever
       a callback of this frame changed. */
    Promise.resolve().then(function () { seen = changes; channel.port2.postMessage(0); });
  }
  var raf = window.requestAnimationFrame;
  window.requestAnimationFrame = function (callback) {
    return raf.call(window, function (t) { callback(t); painted(t); });
  };
  (function loop() { raf.call(window, function (t) { painted(t); loop(); }); })();
  lt.take = function () {
    var out = lt.order.map(function (t) { return lt.frames[t]; });
    lt.frames = {}; lt.order = [];
    return JSON.stringify(out);
  };
})();
"""

NEAR_TOP = r"""
(function () {
  var s = document.getElementById("thread-scroll").getBoundingClientRect();
  var all = document.querySelectorAll("#thread p");
  for (var i = 0; i < all.length; i++) {
    var r = all[i].getBoundingClientRect();
    var found = /Mark\d{3}/.exec(all[i].textContent);
    if (found && r.top > s.top + %d && r.top < s.bottom - 60) return found[0];
  }
  return "";
})()
"""


STATE = r"""
JSON.stringify({
  typed: (document.querySelector("[data-composer-input]") || {}).value,
  bubbles: [].map.call(document.querySelectorAll("#thread .msg"), function (m) {
    return m.className.replace("msg ", "") + (m.hasAttribute("sse-connect") ? " LIVE" : "");
  }).slice(-4),
  focus: document.activeElement && document.activeElement.tagName,
  dialogs: [].map.call(document.querySelectorAll("dialog[open]"), function (d) {
    return d.textContent.replace(/\s+/g, " ").trim().slice(0, 120);
  }),
  toast: (document.querySelector(".toast") || {}).textContent || ""
})
"""


class Page:
    """The browser on the chat, and what was measured there."""

    def __init__(self, browser, site, width: int, height: int) -> None:
        self.browser = browser
        self.site = site
        self.width, self.height = width, height
        self.rows: list[dict] = []
        browser.call("Page.addScriptToEvaluateOnNewDocument", source=SAMPLER)
        # A headless window is not "focused": without this nothing takes focus.
        browser.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.size(width, height)

    def size(self, width: int, height: int) -> None:
        self.browser.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                          deviceScaleFactor=1, mobile=False)

    def open(self, url: str) -> None:
        self.browser.call("Page.navigate", url=url)
        self.browser.wait_for("document.readyState === 'complete' && !!window.__lt", timeout=30)
        self.browser.js(
            "(function(){var b=[].slice.call(document.querySelectorAll('dialog[open] button'))"
            ".find(function(e){return /Not now/.test(e.textContent)}); if(b) b.click();})()"
        )

    def js(self, expression: str):
        return self.browser.js(expression)

    def wait(self, expression: str, what: str, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.js(expression):
                return
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {what}; the page: {self.js(STATE)}")

    def still(self, seconds: float = 0.6) -> None:
        """Until the scroller has not moved for a while (a wheel scroll is
        animated), then that long again."""
        last, since = None, time.monotonic()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            now = self.js("document.getElementById('thread-scroll').scrollTop")
            if now != last:
                last, since = now, time.monotonic()
            elif time.monotonic() - since >= seconds:
                return
            time.sleep(0.05)

    # --- The person ---------------------------------------------------------------------
    def wheel(self, delta: int) -> None:
        box = json.loads(self.js(
            "JSON.stringify(document.getElementById('thread-scroll').getBoundingClientRect())"
        ))
        self.browser.call(
            "Input.dispatchMouseEvent", type="mouseWheel",
            x=box["left"] + box["width"] / 2, y=box["top"] + box["height"] / 2,
            deltaX=0, deltaY=delta,
        )

    def to_bottom(self) -> None:
        for _ in range(12):
            self.wheel(4000)
            time.sleep(0.25)
            if self.js("(function(){var s=document.getElementById('thread-scroll');"
                       "return s.scrollHeight-s.scrollTop-s.clientHeight<2})()"):
                break
        self.still()

    def type(self, text: str) -> None:
        focused = self.js(
            "(function(){var e=document.querySelector('[data-composer-input]');e.focus();"
            "return document.activeElement===e;})()"
        )
        assert focused, self.js(STATE)
        self.browser.call("Input.insertText", text=text)

    def clear(self) -> None:
        self.js(
            "(function(){var e=document.querySelector('[data-composer-input]');e.focus();"
            "e.select();})()"
        )
        self.browser.call("Input.dispatchKeyEvent", type="keyDown", key="Backspace",
                          code="Backspace", windowsVirtualKeyCode=8, nativeVirtualKeyCode=8)
        self.browser.call("Input.dispatchKeyEvent", type="keyUp", key="Backspace",
                          code="Backspace", windowsVirtualKeyCode=8, nativeVirtualKeyCode=8)

    def send(self, text: str) -> None:
        self.type(text)
        time.sleep(0.15)
        self.browser.enter()

    def click(self, selector_js: str) -> None:
        """A real click at the centre of the element the expression gives."""
        box = json.loads(self.js(f"JSON.stringify(({selector_js}).getBoundingClientRect())"))
        x, y = box["left"] + min(box["width"] / 2, 40), box["top"] + box["height"] / 2
        for kind in ("mousePressed", "mouseReleased"):
            self.browser.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left",
                              clickCount=1)

    # --- The measuring ------------------------------------------------------------------
    def begin(self, *, find: str = "", element: str = "") -> None:
        self.js(f"window.__lt.find = {json.dumps(find)}; window.__lt.id = {json.dumps(element)};"
                "window.__lt.take(); 0")

    def frames(self) -> list[dict]:
        return json.loads(self.js("window.__lt.take()"))

    def shot(self, name: str) -> None:
        """`LEMBAS_SHOTS_DIR=<directory>`: the page as it looks now, saved."""
        target = os.environ.get("LEMBAS_SHOTS_DIR")
        if target:
            import base64

            data = self.browser.call("Page.captureScreenshot", format="png")["data"]
            with open(os.path.join(target, f"scroll-{self.width}-{name}.png"), "wb") as handle:
                handle.write(base64.b64decode(data))

    def keep(self, case: str, frames: list[dict]) -> None:
        """`LEMBAS_SCROLL_TRACE=<directory>`: every frame of every phase, to
        read when a number needs explaining."""
        target = os.environ.get("LEMBAS_SCROLL_TRACE")
        if target:
            name = "".join(c if c.isalnum() else "-" for c in case)[:80]
            with open(os.path.join(target, f"{self.width}-{name}.json"), "w") as handle:
                json.dump(frames, handle)

    def at_bottom(self, case: str, frames: list[dict] | None = None, *,
                  slide: bool = True) -> None:
        """Every frame of the phase at the bottom; and the prompt above never
        sliding *down* the screen, which is what a shrinking page does to
        somebody held at its bottom. `slide` false: the phase changes the
        scroller's own box (the composer, the window), and holding the bottom
        of a box that got taller *is* everything above it moving down."""
        frames = self.frames() if frames is None else frames
        assert frames, f"no frames were painted during: {case}"
        self.keep(case, frames)
        gaps = [f["h"] - f["top"] - f["c"] for f in frames]
        off = sum(1 for g in gaps if g > GAP_MAX)
        self.row(case, "gap", max(gaps), GAP_MAX, frames=len(frames), off=off)
        down, where = 0.0, {}
        for before, after in zip(frames, frames[1:], strict=False):
            same = before["promptId"] and before["promptId"] == after["promptId"]
            if same and before["prompt"] is not None and after["prompt"] is not None:
                moved = after["prompt"] - before["prompt"]
                if moved > down:
                    down = moved
                    where = {"height": [before["h"], after["h"]],
                             "top": [before["top"], after["top"]]}
        if slide:
            self.row(case, "prompt slid down", down, DRIFT_MAX, frames=len(frames),
                     where=where)

    def held(self, case: str) -> None:
        """Every frame of the phase with the marked content where it was."""
        frames = [f for f in self.frames() if f["mark"] is not None]
        assert frames, f"the marked content was never on the page during: {case}"
        self.keep(case, frames)
        first = frames[0]["mark"]
        drifts = [abs(f["mark"] - first) for f in frames]
        worst = frames[drifts.index(max(drifts))]
        self.row(case, "drift", max(drifts), DRIFT_MAX, frames=len(frames),
                 off=sum(1 for d in drifts if d > DRIFT_MAX),
                 ended=round(frames[-1]["mark"] - first, 1),
                 where={"height": [frames[0]["h"], worst["h"]],
                        "top": [frames[0]["top"], worst["top"]],
                        "reading": worst["reading"]})

    def row(self, case: str, metric: str, value: float, limit: float, **more) -> None:
        self.rows.append({"case": case, "width": self.width, "metric": metric,
                          "px": round(float(value), 1), "limit": limit,
                          "broken": float(value) > limit, **more})
        # As it is measured, so a run that stops half-way has still said
        # everything up to there.
        _report(self.rows[-1:])

    def bring_closed_block(self) -> None:
        """Scroll, as a person would, until a closed block (a tool's, a
        thought's) is on the screen: the nearest one."""
        where = (
            "(function(){var s=document.getElementById('thread-scroll').getBoundingClientRect();"
            "var mid=(s.top+s.bottom)/2,best=null;document.querySelectorAll("
            "'#thread details:not([open]) > summary').forEach(function(e){var r=e."
            "getBoundingClientRect();if(!r.height)return;var d=(r.top+r.bottom)/2-mid;"
            "if(best===null||Math.abs(d)<Math.abs(best))best=d;});"
            "return best===null?null:[best,(s.bottom-s.top)/2-60];})()"
        )
        for _ in range(40):
            found = self.js(where)
            assert found is not None, "the chat has no closed block at all"
            away, reach = found
            if abs(away) < reach:
                return
            self.wheel(int(max(-500, min(500, away))))
            self.still(0.3)
        raise AssertionError("no closed block could be brought onto the screen")

    def mark_near_top(self, below: int = 60) -> str:
        """A marked paragraph that is on the screen now; a little further up
        when what is on it is one long block of code."""
        for _ in range(6):
            found = self.js(NEAR_TOP % below) or self.js(NEAR_TOP % 0)
            if found:
                return found
            self.wheel(-200)
            self.still()
        raise AssertionError("no marked paragraph is on the screen")


# --- The conversation ----------------------------------------------------------------------
@pytest.fixture
def world(client, db, registered, monkeypatch):
    """A chat with enough in it to scroll -- long replies, tool lines, a
    thinking block, a picture, a player for sound and one for video -- the
    model answered here, the server, and one browser."""
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    connection = Connection(name="local", base_url="http://upstream.test/v1", api_key_encrypted="")
    db.add(connection)
    db.flush()
    db.add(Model(connection_id=connection.id, model_id="m", display_name="M",
                 capabilities_json={"tools": True}))
    fs.ensure_home(db, owner)
    chat = Chat(user_id=owner.id, model_id="m", connection_id=connection.id, title="Scrolling",
                title_generated=True)
    db.add(chat)
    db.flush()
    video = library.keep(db, owner, kind="video", payload=MP4, chat_id=chat.id, width=832,
                         height=480, seconds=5)
    music = library.keep(db, owner, kind="music", payload=MP3, chat_id=chat.id, seconds=60)
    picture = library.keep(db, owner, kind="image", payload=_png(640, 400), chat_id=chat.id,
                           width=640, height=400)
    start = datetime.now(UTC) - timedelta(hours=1)
    tools = [{"name": "read", "status": "ok", "path": "/home/notes.md", "query": "/home/notes.md",
              "text": "line\n" * 40}]

    def add(n: int, role: str, content: str, **more) -> None:
        db.add(Message(chat_id=chat.id, role=role, content=content, complete=True,
                       created_at=start + timedelta(seconds=n),
                       model_id="m" if role == "assistant" else None, **more))

    add(1, "user", "Tell me about the groves.")
    add(2, "assistant", long_reply(1, 6), reasoning="Thinking about groves. " * 30,
        tool_calls_json=tools)
    add(3, "user", "And draw one like this, with a tune and a clip.")
    add(4, "assistant", long_reply(10, 3), tool_calls_json=[
        {"name": "image_generate", "kind": "image", "status": "ok", "media": picture},
        {"name": "music_generate", "kind": "image", "status": "ok", "media": music},
        {"name": "video_generate", "kind": "image", "status": "ok", "media": video},
    ])
    add(5, "user", "More about the leaves.")
    add(6, "assistant", long_reply(20, 5), tool_calls_json=tools)
    db.commit()
    # A picture the person attached, on their second message.
    from lembas.db.models import Attachment

    sent = client.post("/api/files", files={"file": ("sketch.png", _png(480, 360), "image/png")})
    assert sent.status_code < 300, sent.text
    upload = db.scalars(select(Attachment).order_by(Attachment.created_at.desc())).first()
    upload.message_id = db.scalars(
        select(Message.id).where(Message.chat_id == chat.id, Message.content.like("And draw%"))
    ).first()
    db.commit()
    assert upload.width == 480 and upload.height == 360

    model = FakeModel()
    monkeypatch.setattr(generation_service, "stream_chat", model.stream_chat)
    monkeypatch.setattr(chat_service, "generate_title", model.generate_title)

    from lembas.main import app

    served = _Looped(app)
    site = Site(served)
    browsers: list = []

    def page(width: int, height: int) -> Page:
        browser = _browser_class()(width=max(width, 500), height=height)
        browsers.append(browser)
        browser.call("Network.setCookie", name="lembas_session",
                     value=client.cookies.get("lembas_session"), url=site.base)
        return Page(browser, site, width, height)

    try:
        yield {"site": site, "model": model, "chat": chat.id, "owner": owner.id, "page": page,
               "connection": connection.id, "url": f"{site.base}/chat/{chat.id}",
               "served": served}
    finally:
        model.end()
        for browser in browsers:
            with contextlib.suppress(Exception):
                browser.close()
        site.close()
        generation_service._RUNNING.clear()
        generation_service._TASKS.clear()


class _Looped:
    """The application, remembering the event loop it is served on: what a
    linked device says has to be handed to that loop, as its socket would."""

    def __init__(self, app) -> None:
        self.app = app
        self.loop: asyncio.AbstractEventLoop | None = None

    async def __call__(self, scope, receive, send) -> None:
        self.loop = asyncio.get_running_loop()
        await self.app(scope, receive, send)

    def run(self, coroutine, timeout: float = 10.0):
        assert self.loop is not None, "nothing has been served yet"
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout)


def _png(width: int, height: int) -> bytes:
    """A real picture of that size: the browser must decode it to size it."""
    import struct
    import zlib

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + b"\x40\x80\x60" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height, 9))
        + chunk(b"IEND", b"")
    )


def _streaming(page: Page) -> str:
    return "!!document.querySelector('.msg[sse-connect]')"


def _idle(page: Page) -> str:
    return "!document.querySelector('.msg[sse-connect]')"


def _report(rows: list[dict]) -> None:
    target = os.environ.get("LEMBAS_SCROLL_REPORT")
    if not target:
        return
    known: list[dict] = []
    with contextlib.suppress(Exception), open(target) as handle:
        known = json.load(handle)
    with open(target, "w") as handle:
        json.dump(known + rows, handle, indent=1)


def _broken(rows: list[dict]) -> list[str]:
    return [
        f"{r['case']} @ {r['width']}: {r['metric']} {r['px']} px (limit {r['limit']})"
        for r in rows if r["broken"]
    ]


# --- One conversation, every way the page changes ------------------------------------------
@pytest.mark.parametrize(("width", "height"), SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
def test_the_view_holds_through_a_whole_conversation(world, db, width, height):
    model = world["model"]
    page = world["page"](width, height)

    # --- Opening the chat: at the bottom, and still there once the picture
    # and the players have their sizes.
    page.open(world["url"])
    page.wait("document.querySelectorAll('.media-card').length >= 3", "the media cards")
    time.sleep(2.0)
    opened = page.frames()
    settled = opened[len(opened) // 2 :]
    page.at_bottom("opening the chat (after its media loaded)", settled)

    # --- The composer grows as text is typed, and shrinks when it is cleared.
    page.begin()
    page.type("one\ntwo\nthree\nfour\nfive\nsix\nseven")
    time.sleep(0.6)
    page.at_bottom("the composer growing, at the bottom", slide=False)
    page.begin()
    page.clear()
    time.sleep(0.6)
    page.at_bottom("the composer shrinking, at the bottom", slide=False)

    # --- Sending from the bottom, and the reply streaming past a screen.
    page.begin()
    page.send("Question one, sent from the bottom.")
    page.wait(_streaming(page), "the reply's bubble")
    time.sleep(0.4)
    page.at_bottom("sending a message from the bottom")
    page.begin()
    model.say(long_reply(100, 8))
    time.sleep(0.5)
    page.at_bottom("a reply streaming past one screen")

    # --- The person scrolls up mid-reply: from here the view does not move.
    page.wheel(-500)
    page.still()
    assert page.js("document.getElementById('thread-scroll').classList.contains('is-reading')")
    mark = page.mark_near_top()
    page.begin(find=mark)
    model.say(long_reply(120, 4))
    time.sleep(0.4)
    page.held("the reply streaming on after scrolling up")
    page.begin(find=mark)
    model.tool()
    time.sleep(1.2)
    model.say(long_reply(130, 3))
    time.sleep(0.5)
    page.held("a tool running and its round closing, scrolled up")

    # --- The reply ends: the live bubble is replaced, the buttons appear.
    page.begin(find=mark)
    model.end()
    page.wait(_idle(page), "the reply to end")
    time.sleep(1.5)
    page.held("the reply ending, scrolled up")

    # --- The composer, while reading.
    page.begin(find=mark)
    page.type("one\ntwo\nthree\nfour\nfive\nsix")
    time.sleep(0.6)
    page.clear()
    time.sleep(0.6)
    page.held("the composer growing and shrinking, scrolled up")

    # --- A block opened and closed: the line that was pressed stays put.
    summary = ("(function(){var s=document.getElementById('thread-scroll')"
               ".getBoundingClientRect();var all=document.querySelectorAll("
               "'#thread details:not([open]) > summary');for(var i=0;i<all.length;i++){var r=all[i]"
               ".getBoundingClientRect();if(r.top>s.top+20&&r.bottom<s.bottom-20)"
               "return all[i];}return null;})()")
    page.bring_closed_block()
    assert page.js(f"!!{summary}"), "no closed block on the screen"
    page.js(f"{summary}.id = 'pressed-summary'; 0")
    page.begin(element="pressed-summary")
    page.click("document.getElementById('pressed-summary')")
    time.sleep(0.7)
    page.held("a block opened, scrolled up")
    page.begin(element="pressed-summary")
    page.click("document.getElementById('pressed-summary')")
    time.sleep(0.7)
    page.held("a block closed, scrolled up")

    # --- A turn that arrives by itself (a finished job's note, a schedule):
    # the poll appends it and its reply streams, below the person.
    mark = page.mark_near_top()
    page.begin(find=mark)
    chat = db.get(Chat, world["chat"])
    chat_service.create_message(db, chat, "user", "[A background job you started has finished:]\n"
                                "job1 (make test) exited with 0 after 5s.", machine=True)
    chat_service.create_message(db, chat, "assistant", "", complete_=False, model_id="m")
    db.commit()
    page.wait("!!document.querySelector('.msg--machine')", "the note to arrive by itself", 12)
    page.wait(_streaming(page), "its reply's bubble")
    model.say(long_reply(140, 3))
    model.end()
    page.wait(_idle(page), "the note's reply to end")
    time.sleep(1.0)
    page.held("a turn arriving by itself and its reply, scrolled up")

    # --- The window changes height (a phone's keyboard) while reading.
    page.begin(find=mark)
    page.size(width, height - 260)
    time.sleep(0.6)
    page.size(width, height)
    time.sleep(0.6)
    page.held("the window's height changing, scrolled up")

    # --- Back at the bottom: a second reply, a message queued behind it, its
    # own reply after it, and each ending.
    page.to_bottom()
    assert not page.js("document.getElementById('thread-scroll').classList.contains('is-reading')")
    page.begin()
    page.send("Question two.")
    page.wait(_streaming(page), "the second reply's bubble")
    model.say(long_reply(150, 2))
    page.send("Queued behind it.")
    page.wait("!!document.querySelector('.msg--queued')", "the queued message")
    model.say(long_reply(160, 2))
    time.sleep(0.4)
    page.at_bottom("a second reply and a queued message, at the bottom")
    page.begin()
    model.end()
    page.wait("!document.querySelector('.msg--queued')", "the queued message to be sent", 15)
    time.sleep(0.6)
    page.at_bottom("a reply ending and the queued turn starting, at the bottom")
    page.begin()
    model.say(long_reply(170, 2))
    model.tool()
    time.sleep(1.2)
    model.say(paragraph(180))
    model.end()
    page.wait(_idle(page), "the last reply to end")
    time.sleep(1.5)
    page.at_bottom("a reply with a tool round ending, at the bottom")

    # --- A turn arriving by itself while at the bottom.
    page.begin()
    chat_service.create_message(db, chat, "user", "[A background job you started has finished:]\n"
                                "job2 (make lint) exited with 0 after 2s.", machine=True)
    chat_service.create_message(db, chat, "assistant", "", complete_=False, model_id="m")
    db.commit()
    page.wait("document.querySelectorAll('.msg--machine').length >= 2", "the second note", 12)
    page.wait(_streaming(page), "its reply's bubble")
    model.say(paragraph(190))
    model.tool()
    time.sleep(1.2)
    model.say(paragraph(191))
    model.end()
    page.wait(_idle(page), "that reply to end")
    time.sleep(1.0)
    page.at_bottom("a turn arriving by itself and its reply, at the bottom")

    # --- A block opened at the bottom: the pressed line stays, and the page
    # stops following (the person is reading the block). Closed again, the
    # page is at its end again and follows again -- with no scroll in between
    # to say so.
    page.js(f"(function(){{var e={summary}; if(e) e.id='pressed-low';}})(); 0")
    assert page.js("!!document.getElementById('pressed-low')"), "no closed block on the screen"
    page.begin(element="pressed-low")
    page.click("document.getElementById('pressed-low')")
    time.sleep(0.7)
    page.held("a block opened, from the bottom")
    page.begin(element="pressed-low")
    page.click("document.getElementById('pressed-low')")
    time.sleep(0.7)
    page.held("the block closed again, from the bottom")
    page.begin()
    time.sleep(0.3)
    page.at_bottom("after the block was closed: at the bottom again")
    assert not page.js("document.getElementById('thread-scroll').classList.contains('is-reading')")

    # --- The window resized, at the bottom.
    page.begin()
    page.size(width, height - 260)
    time.sleep(0.7)
    page.at_bottom("the window made shorter, at the bottom", slide=False)
    page.begin()
    page.size(width, height)
    time.sleep(0.7)
    page.at_bottom("the window made taller again, at the bottom", slide=False)
    page.begin()
    page.size(width - 40, height)
    time.sleep(0.7)
    page.size(width, height)
    time.sleep(0.7)
    page.at_bottom("the window made narrower and back, at the bottom", slide=False)

    # --- Reopening the chat.
    page.open(world["url"])
    page.wait("document.querySelectorAll('.media-card').length >= 3", "the media cards")
    time.sleep(2.0)
    again = page.frames()
    page.at_bottom("reopening the chat", again[len(again) // 2 :])

    # --- Scrolling up through it: what comes into view (replies not yet laid
    # out, a picture, the players) must not move what is being read.
    for step in range(6):
        page.wheel(-700)
        page.still(0.4)
        found = page.js(NEAR_TOP % 60)
        if not found:
            continue
        page.begin(find=found)
        time.sleep(0.8)
        page.held(f"standing still after scrolling up (stop {step + 1})")

    assert not _broken(page.rows), "\n".join(_broken(page.rows))


@pytest.mark.parametrize(("width", "height"), SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
def test_a_first_reply_in_a_new_chat_does_not_move_the_prompt(world, db, width, height):
    """A chat that just began: the prompt, and a reply that starts, grows past
    the screen and ends. Until the reply reaches the bottom of the screen the
    prompt does not move at all; it never moves down."""
    model = world["model"]
    model.title_after = 0.8
    owner = world["owner"]
    chat = Chat(user_id=owner, model_id="m", connection_id=world["connection"],
                title="New chat", title_generated=False)
    db.add(chat)
    db.flush()
    chat_service.create_message(db, chat, "user", "A first question in a new chat.")
    chat_service.create_message(db, chat, "assistant", "", complete_=False, model_id="m")
    db.commit()
    page = world["page"](width, height)
    page.open(f"{world['site'].base}/chat/{chat.id}")
    page.wait(_streaming(page), "the reply's bubble")
    time.sleep(0.8)

    # Short: the whole chat fits on the screen, before and after it ends.
    page.begin(find="A first question in a new chat.")
    model.say(paragraph(1))
    time.sleep(0.5)
    page.held("a short first reply being written")
    page.begin(find="A first question in a new chat.")
    model.end()
    page.wait(_idle(page), "the reply to end")
    time.sleep(2.0)
    page.held("a short first reply ending, and the chat being named")

    # Longer than the screen, from the bottom: followed, and the end of the
    # reply does not move the page.
    page.begin()
    page.send("And a long one.")
    page.wait(_streaming(page), "the second reply's bubble")
    model.say(long_reply(10, 8))
    time.sleep(0.5)
    page.at_bottom("a long reply in a new chat")
    page.shot("reply-live")
    page.begin()
    model.end()
    page.wait(_idle(page), "the long reply to end")
    time.sleep(1.5)
    page.at_bottom("a long reply ending in a new chat")
    page.shot("reply-finished")

    assert not _broken(page.rows), "\n".join(_broken(page.rows))


# --- Media: its size from the first layout ---------------------------------------------------
BOXES = r"""
JSON.stringify((function () {
  var out = {};
  [["picture", ".media-card__image"], ["video", ".media-card__video"],
   ["sound", ".media-card__audio"], ["attached", ".attachments__image img"]
  ].forEach(function (pair) {
    var e = document.querySelector(pair[1]);
    var r = e ? e.getBoundingClientRect() : null;
    out[pair[0]] = r ? [Math.round(r.width * 10) / 10, Math.round(r.height * 10) / 10] : null;
  });
  var p = document.querySelector(".media-card__image");
  var a = document.querySelector(".attachments__image img");
  out.loaded = !!(p && p.complete && p.naturalWidth && a && a.complete && a.naturalWidth);
  out.none = !(p && p.complete && p.naturalWidth) && !(a && a.complete && a.naturalWidth);
  out.height = document.getElementById("thread-scroll").scrollHeight;
  return out;
})())
"""


@pytest.mark.parametrize(("width", "height"), SIZES, ids=[f"{w}x{h}" for w, h in SIZES])
def test_a_picture_and_a_player_have_their_size_before_their_bytes(world, width, height):
    """A picture that takes its size when it has loaded moves everything under
    it by its height, and a picture is loaded when it comes near the screen:
    the page jumped while somebody scrolled up through it. So the bytes are
    held back here, the boxes measured, the bytes let through, and the boxes
    measured again: nothing may have changed, the page's height included."""
    page = world["page"](width, height)
    browser = page.browser
    browser.call("Fetch.enable", patterns=[{"urlPattern": "*files/raw*"},
                                           {"urlPattern": "*api/files/*"}])
    page.open(world["url"])
    page.wait("document.querySelectorAll('.media-card').length >= 3", "the media cards")
    # Bring them onto the screen: a picture off it is not asked for at all.
    page.wait("!!document.querySelector('.attachments__image img')", "the attached picture")
    page.js("document.querySelector('.attachments__image').scrollIntoView({block: 'start'}); 0")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if any(e.get("method") == "Fetch.requestPaused" for e in browser.events):
            break
        page.js("0")
        time.sleep(0.1)
    time.sleep(0.5)
    before = json.loads(page.js(BOXES))
    assert before["none"] is True, "a picture loaded although its bytes were held back"

    released = set()
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        for event in list(browser.events):
            request = (event.get("params") or {}).get("requestId")
            if event.get("method") == "Fetch.requestPaused" and request not in released:
                released.add(request)
                with contextlib.suppress(RuntimeError):
                    browser.call("Fetch.continueRequest", requestId=request)
        if json.loads(page.js(BOXES))["loaded"]:
            break
        time.sleep(0.1)
    time.sleep(0.8)
    after = json.loads(page.js(BOXES))
    assert after["loaded"] is True, "the pictures never loaded"
    page.shot("media")
    browser.call("Fetch.disable")

    target = os.environ.get("LEMBAS_SCROLL_REPORT")
    if target:
        with open(target + f".media-{width}", "w") as handle:
            json.dump({"before": before, "after": after}, handle)
    assert before["picture"] == after["picture"], (before, after)
    assert before["attached"] == after["attached"], (before, after)
    # Each picture in its own shape: the box is the picture's, not a wider one
    # the picture is centred in.
    for name, shape in (("attached", 480 / 360), ("picture", 640 / 400)):
        wide, tall = after[name]
        assert tall > 100 and abs(wide / tall - shape) < 0.02, (name, after)
    assert before["video"] == after["video"], (before, after)
    assert before["sound"] == after["sound"], (before, after)
    assert before["height"] == after["height"], (before, after)
    # The video's box is its own shape (832 x 480), not the 2:1 a player
    # without a size falls back to, and no taller than a page allows.
    wide, tall = after["video"]
    assert abs(tall - wide * 480 / 832) <= 1.5 or tall < wide * 480 / 832, after


# --- A turn the linked device started, in a page that is open and idle -----------------------
def test_a_turn_the_device_started_appears_in_an_open_page_with_its_reply(world, db):
    """An agent chat, open in a browser, nobody typing. The device's harness
    starts a turn (a finished job's note) and its model answers. The page has
    no stream open -- it learns of the turn from its own poll -- and shows the
    note and then the answer as it is written, and nothing is sent to the
    device for it. Twice: at the bottom, where the page follows it; and
    scrolled up, where it arrives below and moves nothing."""
    from lembas.services import device_link, settings_store
    from tests.test_device_harness_turn import JOB_NOTE, TEAM_REPORT, TURN_2, WITH_TURNS, Device

    device_link.reset()
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    device = Device(db, WITH_TURNS)
    chat = db.get(Chat, world["chat"])
    chat.kind = "agent"
    chat.device_token_id = device.token_id
    chat.device_session_id = "s1"
    chat.project_dir = "/home/dev/app"
    chat.agent_mode = "edit"
    db.commit()
    served = world["served"]
    page = world["page"](1280, 900)
    try:
        page.open(world["url"])
        page.wait("document.querySelectorAll('.media-card').length >= 3", "the media cards")
        time.sleep(1.5)
        assert page.js(_idle(page))

        # At the bottom.
        page.begin()
        served.run(device.emit(*device.harness_turn(JOB_NOTE)))
        page.wait("!!document.querySelector('.msg--machine')", "the note, by itself", 12)
        page.wait(_streaming(page), "the reply being written")
        first = device.answer_events()
        served.run(device.emit(*first[:2]))
        page.wait("document.querySelector('.msg[sse-connect]').textContent"
                  ".indexOf('The tests pass.') !== -1", "the answer's first words, live")
        served.run(device.emit(*first[2:]))
        page.wait(_idle(page), "the reply to end")
        time.sleep(1.0)
        page.at_bottom("a turn the device started, at the bottom")
        last = ("(function(){var a=document.querySelectorAll('#thread .msg--assistant');"
                "return a[a.length-1].textContent;})()")
        assert "The tests pass. Nothing else to do." in " ".join(page.js(last).split())

        # Scrolled up.
        page.wheel(-600)
        page.still()
        mark = page.mark_near_top()
        page.begin(find=mark)
        served.run(device.emit(*device.harness_turn(TEAM_REPORT, TURN_2)))
        page.wait("document.querySelectorAll('.msg--machine').length >= 2", "the report", 12)
        served.run(device.emit(*device.answer_events(TURN_2, text=("Second: ", "found."))))
        page.wait(_idle(page), "the second reply to end")
        time.sleep(1.0)
        page.held("a turn the device started, scrolled up")
        assert "Second: found." in " ".join(page.js(last).split())

        sent = [m for m, _ in device.asked if m in ("session/prompt", "_lembas/steer")]
        assert sent == [], sent
        assert not _broken(page.rows), "\n".join(_broken(page.rows))
    finally:
        device_link.reset()
