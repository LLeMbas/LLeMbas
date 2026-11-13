"""Measure the chat page and the end of a reply, in a real browser.

Run it:

    python scripts/perf_measure.py                 # prints a table and JSON
    python scripts/perf_measure.py --json OUT.json # ... and writes the JSON
    python scripts/perf_measure.py --slow 0.02     # also: what held the event
                                                   # loop for 20 ms or more

Needs a `chromium` on PATH and the development dependencies. A development
instrument like `scripts/shoot.py`: nothing in `src/` knows it exists.

What it does, all in this one process and gone when it ends:

* builds an instance in a temporary directory, with a chat of 300 messages
  (every fifth reply carrying a code block);
* answers every outgoing model request itself (`httpx.AsyncClient` patched, as
  the suite's `mock_http` does): a streamed reply at a fixed pace, and a title
  completion that takes `--title-seconds` (3 by default);
* serves the application on a loopback port for the length of the run (the
  page needs a real event stream, which a `file://` render cannot have);
* drives a headless Chromium over the DevTools protocol -- no Playwright, no
  Node -- and measures:

  (with the browser's CPU slowed fourfold by default, `--cpu`: a phone, and
  less at the mercy of what else this machine is doing)

  - **typing**: 200 characters into the composer of the 300-message chat. Per
    key, the time from `keydown` to the task after the next animation frame
    (the frame that paints the key), and the browser's own layout, style and
    script time for the whole phase;
  - **a streamed reply** in that chat: long tasks (over 50 ms), layout, style
    and script time, and the time from the model's last token to the finished
    bubble on the page;
  - **the end of a reply** on both streams, first turn of a new chat so the
    title is in play: from the model's last token to the terminal `done`
    frame, on the page's stream and on `/api/v2`, and when the title arrived.

  And on the server, for each phase: how long the event loop was held (the
  overshoot of a 5 ms sleep), which is what every other request waits behind.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

_TMP = Path(tempfile.mkdtemp(prefix="lembas-perf-"))
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ.setdefault("LEMBAS_SECRET_KEY", "perf-secret-key-not-for-real-use")
os.environ["LEMBAS_DATA_DIR"] = str(_TMP / "data")
os.environ.setdefault("LEMBAS_LOG_LEVEL", "warning")

import httpx  # noqa: E402

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
MESSAGES = 300
TYPED = (
    "Explain **how** the `loader` reads its settings, and why the second pass "
    "is slower than the first -- with a short example in Python, please. "
)

CODE = "\n".join(
    f"def step_{i}(value: int, scale: float = 1.5) -> float:\n"
    f'    """Step {i} of the pipeline."""\n'
    f"    return (value + {i}) * scale  # {i}\n"
    for i in range(40)
)
_SECTION = (
    "The loader reads the file once and keeps what it parsed.\n\n"
    "## Why the second pass is slower\n\n"
    "It is **not** the parsing: the cache is checked *after* the file is read, so "
    "every pass pays for the read.\n\n"
    f"```python\n{CODE}```\n\n"
    "- move the check before the read\n- keep the parsed value, not the text\n\n"
    + "And a closing paragraph, long enough to be streamed for a while. " * 12
    + "\n\n"
)
# A long answer: four sections, each with prose, a list and a 120-line block.
REPLY = _SECTION * 4


# --------------------------------------------------------------------------
# The model, answered here.


class Upstream:
    """Every outgoing request: a paced stream, a slow title, a plain 200."""

    def __init__(self, title_seconds: float, chunk: int = 12, pace: float = 0.005) -> None:
        self.title_seconds = title_seconds
        self.chunk = chunk
        self.pace = pace
        self.last_token_wall = 0.0
        self.last_token = 0.0
        self.title_calls = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        body = {}
        with contextlib.suppress(Exception):
            body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/chat/completions") and body.get("stream"):
            return httpx.Response(
                200, stream=_Paced(self), headers={"content-type": "text/event-stream"}
            )
        if request.url.path.endswith("/chat/completions"):
            self.title_calls += 1
            await asyncio.sleep(self.title_seconds)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "How the loader reads"}}], "usage": {}},
            )
        return httpx.Response(200, json={})


class _Paced(httpx.AsyncByteStream):
    def __init__(self, upstream: Upstream) -> None:
        self.upstream = upstream

    async def __aiter__(self):
        up = self.upstream
        pieces = [REPLY[i : i + up.chunk] for i in range(0, len(REPLY), up.chunk)]
        for piece in pieces:
            frame = {"choices": [{"delta": {"content": piece}}]}
            yield f"data: {json.dumps(frame)}\n\n".encode()
            up.last_token = time.monotonic()
            up.last_token_wall = time.time()
            await asyncio.sleep(up.pace)
        usage = {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": len(pieces)}}
        yield f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode()


def patch_httpx(upstream: Upstream) -> None:
    original = httpx.AsyncClient

    class Patched(original):
        def __init__(self, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=httpx.MockTransport(upstream.handle), **kwargs)

    httpx.AsyncClient = Patched
    atexit.register(setattr, httpx, "AsyncClient", original)


# --------------------------------------------------------------------------
# The instance.


def build() -> dict:
    """An account, a model, a token and the long chat. Returns what the run
    needs to find them."""
    from fastapi.testclient import TestClient

    from lembas.db.models import Chat, Connection, Message, Model, User
    from lembas.db.session import init_db, session_scope
    from lembas.main import create_app
    from lembas.security import api_tokens

    init_db()
    app = create_app()
    with TestClient(app) as client:
        client.post(
            "/auth/register",
            data={"name": "Frodo", "email": "f@example.com", "password": "mellonmellon"},
            follow_redirects=False,
        )
        cookie = client.cookies.get("lembas_session")
    if not cookie:
        raise SystemExit("could not register the account")

    with session_scope() as db:
        connection = Connection(
            name="local", base_url="http://upstream.test/v1", api_key_encrypted=""
        )
        db.add(connection)
        db.flush()
        db.add(
            Model(
                connection_id=connection.id,
                model_id="perf-model",
                display_name="Perf model",
                capabilities_json={"tools": False},
            )
        )
        owner = db.query(User).first()
        _, token = api_tokens.create(db, owner, name="perf", scopes=["app"])
        chat = Chat(
            user_id=owner.id,
            model_id="perf-model",
            connection_id=connection.id,
            title="A long chat",
            title_generated=True,
        )
        db.add(chat)
        db.flush()
        chat_id = chat.id
        base = datetime.now(UTC) - timedelta(hours=2)
        for i in range(MESSAGES):
            user_turn = i % 2 == 0
            text = f"Question {i // 2}: what does step {i} of the pipeline do, and why?"
            if not user_turn:
                text = (
                    f"Step {i} scales the value. It is **cheap**, and it runs once per item.\n\n"
                    "1. read the value\n2. add the offset\n3. scale it\n\n"
                    + ("Some more prose to give the bubble a realistic height. " * 6)
                )
                if i % 5 == 1:
                    text += f"\n\n```python\n{CODE[:900]}\n```\n"
            db.add(
                Message(
                    chat_id=chat_id,
                    role="user" if user_turn else "assistant",
                    content=text,
                    model_id="" if user_turn else "perf-model",
                    connection_id=None if user_turn else connection.id,
                    created_at=base + timedelta(seconds=10 * i),
                    usage_json={} if user_turn else {
                        "prompt_tokens": 800, "completion_tokens": 120, "elapsed_ms": 4000,
                    },
                )
            )
    return {"app": app, "cookie": cookie, "token": token, "chat_id": chat_id}


class LoopLag:
    """How long the server's event loop was held: the overshoot of a short
    sleep, sampled for as long as the server runs."""

    INTERVAL = 0.005

    def __init__(self) -> None:
        self.samples: list[float] = []
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        while True:
            before = time.perf_counter()
            await asyncio.sleep(self.INTERVAL)
            self.samples.append(max(0.0, time.perf_counter() - before - self.INTERVAL))

    def take(self) -> dict:
        samples, self.samples = self.samples, []
        if not samples:
            return {}
        ordered = sorted(samples)
        return {
            "samples": len(ordered),
            "p50_ms": round(ordered[len(ordered) // 2] * 1000, 2),
            "p99_ms": round(ordered[int(len(ordered) * 0.99)] * 1000, 2),
            "max_ms": round(ordered[-1] * 1000, 2),
            "over_20ms": sum(1 for s in ordered if s > 0.020),
            "over_50ms": sum(1 for s in ordered if s > 0.050),
            "held_ms": round(sum(s for s in ordered if s > 0.005) * 1000, 1),
        }


class SlowCallbacks(logging.Handler):
    """asyncio's own "Executing <Task …> took N seconds", by what ran."""

    PATTERN = re.compile(r"Executing <(?:Task|Handle|TimerHandle).*?> took ([\d.]+) seconds")
    CORO = re.compile(r"coro=<([^ >]+)")
    WHERE = re.compile(r"running at ([^>]+?)>")

    def __init__(self) -> None:
        super().__init__()
        self.found: dict[str, list[float]] = {}

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        took = self.PATTERN.search(text)
        if not took:
            return
        coro = self.CORO.search(text)
        where = self.WHERE.search(text)
        name = coro.group(1) if coro else text[:80]
        if where:
            name += " @ " + where.group(1).replace(str(REPO) + "/", "")
        self.found.setdefault(name, []).append(float(took.group(1)))

    def report(self) -> list[dict]:
        rows = [
            {"what": name, "count": len(times), "total_ms": round(sum(times) * 1000),
             "max_ms": round(max(times) * 1000)}
            for name, times in self.found.items()
        ]
        return sorted(rows, key=lambda r: -r["total_ms"])


class Server:
    """The application on a loopback port, in a thread, until `stop()`."""

    def __init__(self, app, lag: LoopLag, slow: float = 0.0) -> None:
        import uvicorn

        self.lag = lag
        self.slow = slow
        self.slow_log = SlowCallbacks()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        inner = app

        # How long each request took to answer, by route (ids folded away).
        # Wall time, not time on the loop -- but a handler that is `async def`
        # and does its database and rendering work in line holds the loop for
        # nearly all of it, so the slow ones here are where to look.
        self.requests: dict[str, list[float]] = {}
        took = self.requests

        async def wrapped(scope, receive, send):
            lag.start()
            if slow:
                loop = asyncio.get_running_loop()
                if not loop.get_debug():
                    loop.set_debug(True)
                    loop.slow_callback_duration = slow
            if scope.get("type") != "http":
                await inner(scope, receive, send)
                return
            path = re.sub(r"/[0-9a-f]{32}", "/{id}", scope.get("path", ""))
            path = re.sub(r"/static/.*", "/static/…", path)
            began = time.perf_counter()
            first: list[float] = []

            async def timed_send(message):
                if message.get("type") == "http.response.start" and not first:
                    first.append(time.perf_counter() - began)
                await send(message)

            try:
                await inner(scope, receive, timed_send)
            finally:
                # To the first byte of the answer: a stream's length is not its cost.
                spent = first[0] if first else time.perf_counter() - began
                took.setdefault(f"{scope.get('method', '')} {path}", []).append(spent)

        config = uvicorn.Config(
            wrapped, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on"
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        if slow:
            logging.getLogger("asyncio").addHandler(self.slow_log)
            logging.getLogger("asyncio").setLevel(logging.WARNING)
            logging.getLogger("asyncio").propagate = False

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.thread.start()
        atexit.register(self.stop)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with contextlib.suppress(httpx.TransportError):
                if httpx.get(f"{self.base}/healthz", timeout=1.0).status_code == 200:
                    return
            time.sleep(0.1)
        raise SystemExit("the server never answered")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


# --------------------------------------------------------------------------
# The browser, over the DevTools protocol.


class Browser:
    def __init__(self, width: int = 1280, height: int = 900, args: tuple[str, ...] = ()) -> None:
        if not CHROMIUM:
            raise SystemExit("no chromium on PATH")
        self.profile = tempfile.mkdtemp(prefix="lembas-perf-chromium-", dir=_TMP)
        self.process = subprocess.Popen(
            [
                CHROMIUM, "--headless=new", "--no-sandbox", "--disable-gpu",
                "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
                "--no-first-run", "--force-device-scale-factor=1",
                f"--window-size={width},{height}", *args, "about:blank",
            ],
            stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True,
            start_new_session=True,
        )
        atexit.register(self.close)
        self.ws = None
        self._id = 0
        self.events: list[dict] = []
        endpoint = ""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            line = self.process.stderr.readline()
            if not line and self.process.poll() is not None:
                break
            if found := re.search(r"DevTools listening on (ws://\S+)", line):
                endpoint = found.group(1)
                break
        if not endpoint:
            raise SystemExit("chromium did not open its DevTools port")
        port = re.search(r":(\d+)/", endpoint).group(1)
        # Drain stderr so a chatty browser cannot fill the pipe and stall.
        threading.Thread(target=self._drain, daemon=True).start()
        pages = httpx.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
        page = next(p for p in pages if p.get("type") == "page")
        from websockets.sync.client import connect

        warnings.simplefilter("ignore", DeprecationWarning)
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=None, origin=None)
        for domain in ("Page", "Runtime", "Network", "Performance"):
            self.call(f"{domain}.enable")

    def _drain(self) -> None:
        with contextlib.suppress(Exception):
            for _ in self.process.stderr:
                pass

    def call(self, method: str, **params):
        self._id += 1
        mine = self._id
        self.ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        while True:
            message = json.loads(self.ws.recv(timeout=60))
            if message.get("id") == mine:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self.events.append(message)

    def js(self, expression: str):
        result = self.call(
            "Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True
        )
        if "exceptionDetails" in result:
            raise RuntimeError(str(result["exceptionDetails"])[:500])
        return result.get("result", {}).get("value")

    def wait_for(self, expression: str, timeout: float = 60.0, what: str = "") -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.js(expression):
                return
            time.sleep(0.05)
        raise SystemExit(f"timed out waiting for {what or expression}")

    def metrics(self) -> dict:
        return {m["name"]: m["value"] for m in self.call("Performance.getMetrics")["metrics"]}

    def key(self, char: str) -> None:
        self.call("Input.dispatchKeyEvent", type="keyDown", text=char, key=char,
                  unmodifiedText=char)
        self.call("Input.dispatchKeyEvent", type="keyUp", key=char)

    def enter(self) -> None:
        common = {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13,
                  "nativeVirtualKeyCode": 13}
        self.call("Input.dispatchKeyEvent", type="keyDown", text="\r", **common)
        self.call("Input.dispatchKeyEvent", type="keyUp", **common)

    def close(self) -> None:
        if self.ws is not None:
            with contextlib.suppress(Exception):
                self.ws.close()
            self.ws = None
        if self.process.poll() is None:
            # Its own session: the helpers go with it.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)


# What the page records about itself. Installed before any of its scripts run.
PROBE = """
(function () {
  var perf = window.__perf = { keys: [], long: [], doneAt: 0, sawStream: false };
  try {
    new PerformanceObserver(function (list) {
      list.getEntries().forEach(function (e) {
        perf.long.push({ start: e.startTime, duration: e.duration });
      });
    }).observe({ type: 'longtask', buffered: true });
  } catch (e) {}
  document.addEventListener('keydown', function (event) {
    if (!event.target || !event.target.matches ||
        !event.target.matches('[data-composer-input]')) return;
    if (event.key === 'Enter') return;
    var t0 = performance.now();
    requestAnimationFrame(function () {
      var channel = new MessageChannel();
      channel.port1.onmessage = function () { perf.keys.push(performance.now() - t0); };
      channel.port2.postMessage(0);
    });
  }, true);
  /* The thread's own children only: a bubble arriving, and the `done` frame
     replacing it. Watching the whole page's subtree made this probe one of
     the most expensive things on the page it was measuring. */
  document.addEventListener('DOMContentLoaded', function () {
    var thread = document.getElementById('thread');
    if (!thread) return;
    new MutationObserver(function () {
      var live = thread.querySelector(':scope > [sse-connect]');
      if (live) { perf.sawStream = true; perf.doneAt = 0; }
      else if (perf.sawStream && !perf.doneAt) {
        perf.doneAt = performance.timeOrigin + performance.now();
      }
    }).observe(thread, { childList: true });
  });
})();
"""


def spread(values: list[float]) -> dict:
    if not values:
        return {}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median_ms": round(statistics.median(ordered), 1),
        "p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 1),
        "max_ms": round(ordered[-1], 1),
    }


def delta(before: dict, after: dict) -> dict:
    def ms(name: str) -> float:
        return round((after.get(name, 0) - before.get(name, 0)) * 1000, 1)

    return {
        "layouts": int(after.get("LayoutCount", 0) - before.get("LayoutCount", 0)),
        "layout_ms": ms("LayoutDuration"),
        "style_recalcs": int(after.get("RecalcStyleCount", 0) - before.get("RecalcStyleCount", 0)),
        "style_ms": ms("RecalcStyleDuration"),
        "script_ms": ms("ScriptDuration"),
        "task_ms": ms("TaskDuration"),
    }


def hot_functions(profile: dict, top: int = 12) -> list[dict]:
    """A CPU profile as its most expensive functions, by their own time."""
    nodes = {node["id"]: node for node in profile.get("nodes", [])}
    own: dict[int, float] = {}
    deltas = profile.get("timeDeltas", [])
    for node_id, micros in zip(profile.get("samples", []), deltas, strict=False):
        own[node_id] = own.get(node_id, 0.0) + micros / 1000
    parent = {child: node["id"] for node in nodes.values() for child in node.get("children", [])}

    def label(node_id: int) -> str:
        frame = nodes[node_id]["callFrame"]
        where = frame.get("url", "").rsplit("/", 1)[-1].split("?")[0]
        name = frame.get("functionName") or "(anonymous)"
        return f"{name} {where}:{frame.get('lineNumber', 0) + 1}" if where else name

    by_name: dict[str, float] = {}
    for node_id, spent in own.items():
        key = label(node_id)
        # A browser built-in says nothing by itself: name who called it.
        if not nodes[node_id]["callFrame"].get("url") and node_id in parent:
            caller = label(parent[node_id])
            if not caller.startswith("("):
                key = f"{key} < {caller}"
        by_name[key] = by_name.get(key, 0.0) + spent
    rows = sorted(by_name.items(), key=lambda item: -item[1])
    return [{"function": name, "self_ms": round(spent)} for name, spent in rows[:top]]


def measure_page(
    server: Server, upstream: Upstream, seeded: dict, lag: LoopLag, cpu: float = 1.0,
    profile: bool = False,
) -> dict:
    out: dict = {}
    browser = Browser()
    try:
        if cpu > 1:
            # A slower machine than this one: the browser's own throttle.
            browser.call("Emulation.setCPUThrottlingRate", rate=cpu)
        browser.call("Network.setCookie", name="lembas_session", value=seeded["cookie"],
                     url=server.base, httpOnly=True)
        browser.call("Page.addScriptToEvaluateOnNewDocument", source=PROBE)
        lag.take()
        started = time.monotonic()
        browser.call("Page.navigate", url=f"{server.base}/chat/{seeded['chat_id']}")
        browser.wait_for("document.readyState === 'complete' && !!window.htmx", what="the page")
        browser.wait_for("!!document.querySelector('[data-composer-input]')", what="the composer")
        time.sleep(1.5)
        out["open"] = {
            "ready_ms": round((time.monotonic() - started - 1.5) * 1000),
            "elements": browser.js("document.querySelectorAll('*').length"),
            "messages_in_dom": browser.js("document.querySelectorAll('#thread .msg').length"),
            "server_loop": lag.take(),
        }

        # Typing. First the dialog a new account is shown about notifications:
        # it is modal, and nothing behind a modal dialog takes focus or keys.
        browser.js(
            "document.querySelectorAll('dialog').forEach(function (d) {"
            " if (d.matches(':modal')) d.querySelector('button').click(); })"
        )
        browser.call("Emulation.setFocusEmulationEnabled", enabled=True)
        browser.js("document.querySelector('[data-composer-input]').focus()")
        if not browser.js("document.activeElement.matches('[data-composer-input]')"):
            raise SystemExit("the composer did not take focus")
        text = (TYPED * 3)[:200]
        before = browser.metrics()
        long_before = browser.js("window.__perf.long.length")
        if profile:
            browser.call("Profiler.enable")
            browser.call("Profiler.start")
        for char in text:
            browser.key(char)
            time.sleep(0.03)
        time.sleep(0.3)
        after = browser.metrics()
        if profile:
            out["typing_hot"] = hot_functions(browser.call("Profiler.stop")["profile"])
        typed = browser.js("document.querySelector('[data-composer-input]').value.length")
        out["typing"] = {
            "typed": typed,
            "key_to_paint": spread(browser.js("window.__perf.keys")),
            "long_tasks": browser.js("window.__perf.long.length") - long_before,
            **delta(before, after),
        }
        per_key = out["typing"]
        for name in ("layout_ms", "style_ms", "script_ms"):
            per_key[name.replace("_ms", "_ms_per_key")] = round(per_key[name] / max(1, typed), 2)

        # A streamed reply in the long chat.
        lag.take()
        browser.js("window.__perf.long.length = 0")
        before = browser.metrics()
        sent_at = time.monotonic()
        if profile:
            browser.call("Profiler.start")
        browser.enter()
        browser.wait_for("window.__perf.sawStream", timeout=30, what="the reply to start")
        browser.wait_for("window.__perf.doneAt > 0", timeout=120, what="the reply to finish")
        done_wall = browser.js("window.__perf.doneAt") / 1000
        after = browser.metrics()
        if profile:
            out["stream_hot"] = hot_functions(browser.call("Profiler.stop")["profile"])
        long = browser.js("window.__perf.long")
        out["stream"] = {
            "reply_chars": len(REPLY),
            "seconds": round(time.monotonic() - sent_at, 1),
            "long_tasks": len(long),
            "long_total_ms": round(sum(e["duration"] for e in long)),
            "long_max_ms": round(max((e["duration"] for e in long), default=0)),
            "last_token_to_finished_bubble_ms": round(
                (done_wall - upstream.last_token_wall) * 1000
            ),
            **delta(before, after),
            "server_loop": lag.take(),
        }
    finally:
        browser.close()
    return out


def _frames(lines):
    """(event, data) for each frame of an event stream read line by line."""
    name, data = "", []
    for line in lines:
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif not line and name:
            yield name, "\n".join(data)
            name, data = "", []


def measure_end(server: Server, upstream: Upstream, seeded: dict, lag: LoopLag) -> dict:
    """Last token to `done`, first turn of a new chat, on both streams."""
    out: dict = {}
    cookies = {"lembas_session": seeded["cookie"]}
    bearer = {"Authorization": f"Bearer {seeded['token']}"}

    def title_of(chat_id: str) -> str:
        # From the database, not over HTTP: asked ten times a second, the
        # token's rate limit would answer instead.
        from lembas.db.models import Chat
        from lembas.db.session import session_scope

        with session_scope() as db:
            chat = db.get(Chat, chat_id)
            return chat.title if chat is not None else ""

    def follow(url: str, chat_id: str, **auth) -> dict:
        seen: dict = {"frames": 0}
        with httpx.stream("GET", url, timeout=120, **auth) as response:
            for name, data in _frames(response.iter_lines()):
                seen["frames"] += 1
                now = time.monotonic()
                if name == "done":
                    seen["done"] = now
                    seen["title_in_done"] = (
                        json.loads(data)["chat"]["title"] if data.startswith("{")
                        else (re.search(r'id="chat-title"[^>]*>([^<]*)<', data) or [None, ""])[1]
                    )
                elif name == "title":
                    seen["title_frame"] = now
                elif name == "close":
                    seen["close"] = now
                    break
        last = upstream.last_token
        result = {
            "frames": seen["frames"],
            "last_token_to_done_ms": round((seen["done"] - last) * 1000),
            "last_token_to_close_ms": round((seen.get("close", seen["done"]) - last) * 1000),
            "title_in_done": seen.get("title_in_done", ""),
        }
        if "title_frame" in seen:
            result["last_token_to_title_frame_ms"] = round((seen["title_frame"] - last) * 1000)
        # And when a fetch shows the generated title.
        deadline = time.monotonic() + upstream.title_seconds + 30
        while time.monotonic() < deadline:
            if title_of(chat_id) == "How the loader reads":
                result["last_token_to_title_on_fetch_ms"] = round((time.monotonic() - last) * 1000)
                break
            time.sleep(0.05)
        return result

    # The page's stream.
    lag.take()
    started = httpx.post(
        f"{server.base}/api/chats/start", data={"content": "How does the loader read?",
                                                "model_id": "perf-model"},
        cookies=cookies, headers={"HX-Request": "true"}, timeout=30, follow_redirects=False,
    )
    where = started.headers.get("HX-Redirect") or started.headers.get("location") or ""
    chat_id = where.rsplit("/", 1)[-1]
    page = httpx.get(f"{server.base}/chat/{chat_id}", cookies=cookies, timeout=30).text
    stream_url = re.search(r'sse-connect="([^"]+)"', page)
    if not stream_url:
        raise SystemExit(f"the new chat has no streaming reply ({started.status_code} {where!r})")
    out["web"] = follow(server.base + stream_url.group(1), chat_id, cookies=cookies)
    out["web"]["server_loop"] = lag.take()

    # /api/v2.
    made = httpx.post(
        f"{server.base}/api/v2/chats", headers=bearer,
        json={"text": "How does the loader read?", "model": "perf-model"}, timeout=30,
    )
    body = made.json()
    if made.status_code != 201:
        raise SystemExit(f"/api/v2/chats -> {made.status_code} {made.text[:200]}")
    chat_id, reply_id = body["chat"]["id"], body["reply"]["id"]
    out["v2"] = follow(
        f"{server.base}/api/v2/chats/{chat_id}/messages/{reply_id}/stream", chat_id,
        headers=bearer,
    )
    out["v2"]["server_loop"] = lag.take()
    out["title_calls"] = upstream.title_calls
    return out


def table(results: dict) -> str:
    lines: list[str] = []

    def row(label: str, value) -> None:
        lines.append(f"  {label:<46} {value}")

    opened, typing, stream = results["open"], results["typing"], results["stream"]
    lines.append(
        f"browser CPU slowed {results['cpu_throttle']}x; "
        f"load average at start {results['load_average'][0]}"
    )
    lines.append("chat page, 300 messages")
    row("messages in the DOM / elements", f"{opened['messages_in_dom']} / {opened['elements']}")
    row("page ready (ms)", opened["ready_ms"])
    lines.append("typing 200 characters")
    k = typing["key_to_paint"]
    row("keydown -> painted, median / p95 / max (ms)",
        f"{k.get('median_ms')} / {k.get('p95_ms')} / {k.get('max_ms')}")
    row("layout / style / script per key (ms)",
        f"{typing['layout_ms_per_key']} / {typing['style_ms_per_key']} / "
        f"{typing['script_ms_per_key']}")
    row("layouts / style recalcs", f"{typing['layouts']} / {typing['style_recalcs']}")
    row("long tasks", typing["long_tasks"])
    lines.append(f"streamed reply ({stream['reply_chars']} characters, {stream['seconds']} s)")
    row("long tasks: count / total / max (ms)",
        f"{stream['long_tasks']} / {stream['long_total_ms']} / {stream['long_max_ms']}")
    row("layout / style / script (ms)",
        f"{stream['layout_ms']} / {stream['style_ms']} / {stream['script_ms']}")
    row("last token -> finished bubble (ms)", stream["last_token_to_finished_bubble_ms"])
    loop = stream["server_loop"]
    row("server loop held: p99 / max (ms), >20 ms, >50 ms",
        f"{loop.get('p99_ms')} / {loop.get('max_ms')}, {loop.get('over_20ms')}, "
        f"{loop.get('over_50ms')}")
    lines.append(f"end of a first reply (title takes {results['title_seconds']} s)")
    for name in ("web", "v2"):
        end = results["end"][name]
        row(f"{name}: last token -> done (ms)", end["last_token_to_done_ms"])
        row(f"{name}: last token -> close (ms)", end["last_token_to_close_ms"])
        if "last_token_to_title_frame_ms" in end:
            row(f"{name}: last token -> title frame (ms)", end["last_token_to_title_frame_ms"])
        row(f"{name}: generated title on a fetch after (ms)",
            end.get("last_token_to_title_on_fetch_ms", "never"))
    for phase in ("typing_hot", "stream_hot"):
        if results.get(phase):
            lines.append(f"{phase.split('_')[0]}: the page's own time, by function (ms)")
            for item in results[phase]:
                lines.append(f"  {item['self_ms']:>6}  {item['function']}")
    if results.get("requests"):
        lines.append("slowest answers, to the first byte: max / median (ms), count")
        for item in results["requests"][:8]:
            lines.append(
                f"  {item['max_ms']:>6} / {item['median_ms']:<5} {item['count']:>3}x"
                f"  {item['route']}"
            )
    if results.get("slow"):
        lines.append("held the event loop (asyncio debug)")
        for item in results["slow"][:15]:
            row(item["what"][:46], f"{item['count']}x, {item['total_ms']} ms, max {item['max_ms']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", help="write the numbers here as JSON")
    parser.add_argument("--title-seconds", type=float, default=3.0)
    parser.add_argument("--slow", type=float, default=0.0,
                        help="report what held the event loop this many seconds or more")
    parser.add_argument("--skip-page", action="store_true", help="the streams only, no browser")
    parser.add_argument("--profile", action="store_true",
                        help="also: the page's most expensive functions in each phase")
    parser.add_argument("--cpu", type=float, default=4.0,
                        help="slow the browser's CPU by this factor (4: a mid-range phone)")
    args = parser.parse_args()

    upstream = Upstream(args.title_seconds)
    patch_httpx(upstream)
    seeded = build()
    lag = LoopLag()
    server = Server(seeded["app"], lag, slow=args.slow)
    server.start()
    # The load this machine was under: the numbers mean little without it.
    results: dict = {
        "title_seconds": args.title_seconds,
        "cpu_throttle": args.cpu,
        "load_average": [round(x, 2) for x in os.getloadavg()],
    }
    try:
        if not args.skip_page:
            results.update(
                measure_page(server, upstream, seeded, lag, cpu=args.cpu, profile=args.profile)
            )
        results["end"] = measure_end(server, upstream, seeded, lag)
        if args.slow:
            results["slow"] = server.slow_log.report()
        results["requests"] = sorted(
            (
                {"route": route, "count": len(times), "max_ms": round(max(times) * 1000),
                 "median_ms": round(statistics.median(times) * 1000)}
                for route, times in server.requests.items()
            ),
            key=lambda row: -row["max_ms"],
        )[:12]
    finally:
        server.stop()
    if not args.skip_page:
        print(table(results))
    print(json.dumps(results, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
