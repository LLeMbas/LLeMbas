"""The viewers by kind, in a real Chromium: what only a browser can show.

* **An SVG cannot run.** One carrying a script and an `onload` is shown by
  the file page as a picture; neither runs, and nothing it would fetch is
  asked. Opened as a document, framed, embedded or fetched from this
  application's own origin, its address gives a download and no document.
  A control shows the listener would have heard it: the same bytes opened as
  a document of this origin do run.
* **A PDF and a 3D model are drawn** -- inside the sealed frame, by pages and
  by triangles counted there, with ink on the canvas.
* **A table is laid out**, with what looks like markup in a cell shown as the
  text it is, and goes back to text.
* **Sound loads** over the raw route; a notebook's scripts do not run.
* **Everything fits** at 1280x900 and at 390x844: the page never scrolls
  sideways, and what is wide scrolls inside its own box.

Slow (a server and a browser): run it through `heavy-guard`, by itself.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import math
import os
import shutil
import struct
import time
import zipfile
from urllib.parse import quote

import pytest
from sqlalchemy import select

from lembas.db.models import User
from lembas.services import fs
from tests.test_show_in_chat_browser import Site, _browser_class

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
REQUIRED = bool(os.environ.get("CI") or os.environ.get("LEMBAS_REQUIRE_BROWSER"))
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to look in"),
]
TAP = 40
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
# Whatever runs this asks the application for an address nothing else asks for.
SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80" '
    'onload="fetch(\'/static/svg-ran-onload\')">'
    "<script>fetch('/static/svg-ran-script'); window.svgRan = 1;</script>"
    '<rect width="120" height="80" fill="green"/></svg>'
)
LONG = "a-very-long-name-for-a-file-that-nobody-wanted-to-shorten-" * 3


def _pdf() -> bytes:
    """One page with a filled rectangle and a word, offsets and all."""
    content = b"0 0 1 rg 20 20 120 60 re f BT /F1 24 Tf 20 120 Td (Hello) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref
    return bytes(out)


def _stl() -> bytes:
    """A tetrahedron: four triangles, binary."""
    points = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)]
    faces = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
    out = bytes(80) + struct.pack("<I", len(faces))
    for face in faces:
        out += struct.pack("<3f", 0, 0, 0)
        for corner in face:
            out += struct.pack("<3f", *points[corner])
        out += b"\x00\x00"
    return out


def _glb() -> bytes:
    """One triangle with an index buffer and a material colour."""
    positions = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    indices = struct.pack("<3H", 0, 1, 2) + b"\x00\x00"
    binary = positions + indices
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "translation": [0, 0, 0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [1, 0.2, 0.2, 1]}}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 36},
            {"buffer": 0, "byteOffset": 36, "byteLength": 6},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3",
             "min": [0, 0, 0], "max": [1, 1, 0]},
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
    }
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary)
    return (
        b"glTF" + struct.pack("<II", 2, total)
        + struct.pack("<I", len(text)) + b"JSON" + text
        + struct.pack("<I", len(binary)) + b"BIN\x00" + binary
    )


def _wav(seconds: float = 0.5, rate: int = 8000) -> bytes:
    samples = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / rate)))
        for n in range(int(seconds * rate))
    )
    return (
        b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(samples)) + samples
    )


def _zip() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(f"deep/{LONG}.txt", b"x")
        for number in range(40):
            archive.writestr(f"file-{number}.txt", b"y" * number)
    return out.getvalue()


NOTEBOOK = json.dumps({
    "metadata": {"language_info": {"name": "python"}},
    "cells": [
        {"cell_type": "markdown", "source": "# A title\n<script>window.nbRan = 1</script>"},
        {"cell_type": "code", "execution_count": 1, "source": "x = '" + "long " * 80 + "'",
         "outputs": [
             {"output_type": "display_data",
              "data": {"text/html": "<img src=x onerror='window.nbRan = 2'>"}},
             {"output_type": "display_data", "data": {"image/png": base64.b64encode(PNG).decode()}},
             {"output_type": "stream", "text": "<img src=x onerror='window.nbRan = 3'>\n"},
         ]},
    ],
}).encode()
TABLE = (
    ",".join(f"column {n}" for n in range(30)) + "\n"
    + "\n".join(",".join(["<b>bold</b>", '"a, b"', *[f"v{r}"] * 28]) for r in range(60))
    + "\n"
).encode()


@pytest.fixture
def rig(client, db, registered):
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    home = fs.ensure_home(db, owner).path
    view = fs.view(db, owner)
    files = {
        "pic.svg": SVG.encode(), "doc.pdf": _pdf(), "shape.stl": _stl(), "tri.glb": _glb(),
        "tone.wav": _wav(), "pack.zip": _zip(), "book.ipynb": NOTEBOOK, "data.csv": TABLE,
        f"{LONG}.bin": bytes(range(256)) * 4,
    }
    for name, data in files.items():
        fs.write(view, f"{home}/{name}", data)
    db.commit()

    from lembas.main import app

    site = Site(app)
    browser = None
    try:
        # One process for the page and its sealed frames, so the test can read
        # what a frame drew. What a frame may do is unchanged by it.
        browser = _browser_class()(
            width=1280, height=900,
            args=("--disable-features=IsolateSandboxedIframes", "--enable-unsafe-swiftshader"),
        )
        browser.call("Network.setCookie", name="lembas_session",
                     value=client.cookies.get("lembas_session"), url=site.base)
        yield {"site": site, "browser": browser, "home": home}
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.close()
        site.close()


def _go(rig, name: str, width: int = 1280, height: int = 900, preview: bool = False) -> None:
    browser = rig["browser"]
    browser.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                 deviceScaleFactor=1, mobile=width < 600)
    browser.events.clear()
    browser.call("Page.navigate",
                 url=f"{rig['site'].base}/files/view?path={quote(rig['home'] + '/' + name)}")
    browser.wait_for("document.readyState === 'complete'", timeout=30)
    browser.js(
        "(function(){var b=[].slice.call(document.querySelectorAll('dialog[open] button'))"
        ".find(function(e){return /Not now/.test(e.textContent)}); if(b) b.click();})()"
    )
    if preview:
        browser.js("document.querySelector('[data-testid=\"file-preview\"]').click()")
        browser.wait_for("!document.querySelector('[data-file-preview]').hidden", timeout=10)
    time.sleep(0.5)


def _in_frames(browser, expression: str) -> list:
    """`expression` in every frame's own world; what each one that has an
    answer says."""
    said = []
    for event in list(browser.events):
        if event.get("method") != "Runtime.executionContextCreated":
            continue
        context = event["params"]["context"]
        try:
            result = browser.call("Runtime.evaluate", expression=expression,
                                  contextId=context["id"], returnByValue=True)
        except RuntimeError:
            continue
        value = result.get("result", {}).get("value")
        if value is not None:
            said.append(value)
    return said


def _wait_frames(browser, expression: str, timeout: float = 60.0) -> list:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        said = _in_frames(browser, expression)
        if said:
            return said
        time.sleep(0.3)
    raise AssertionError(f"no frame ever answered: {expression[:80]}")


FITS = """JSON.stringify((function () {
  function box(e) { var r = e.getBoundingClientRect();
    return {l: Math.round(r.left), r: Math.round(r.right), w: Math.round(r.width),
            h: Math.round(r.height)}; }
  var body = [].filter.call(document.querySelectorAll('.viewer__body'),
    function (e) { return e.getBoundingClientRect().width > 0; })[0];
  return {
    width: window.innerWidth, page: document.documentElement.scrollWidth,
    body: body ? box(body) : null,
    inside: body ? [].map.call(
      body.querySelectorAll('img,audio,video,iframe,table,pre,ul,dl,a.btn,button'),
      function (e) { var b = box(e); b.tag = e.tagName; b.cls = e.className; return b; })
      .filter(function (b) { return b.w > 0; }) : [],
    bar: [].map.call(document.querySelectorAll('.fx-file__bar .btn'), function (e) {
      var b = box(e); b.id = e.getAttribute('data-testid') || ''; return b; })
      .filter(function (b) { return b.w > 0; })
  };
})())"""


def _fits(rig) -> dict:
    seen = json.loads(rig["browser"].js(FITS))
    assert seen["page"] <= seen["width"], seen
    assert seen["body"] is not None and seen["body"]["r"] <= seen["width"], seen
    for control in seen["bar"]:
        assert control["l"] >= 0 and control["r"] <= seen["width"], seen
    return seen


# --- An SVG cannot run ---------------------------------------------------------------------
def test_an_svg_is_a_picture_and_nothing_in_it_runs(rig):
    browser, site = rig["browser"], rig["site"]
    _go(rig, "pic.svg", preview=True)
    browser.wait_for(
        "(function(){var i=document.querySelector('[data-testid=\"viewer-image\"]');"
        "return i && i.complete && i.naturalWidth > 0;})()", timeout=20, what="the picture")
    time.sleep(1.0)
    assert browser.js("typeof window.svgRan") == "undefined"
    assert not [a for a in site.asked if "svg-ran" in a], site.asked
    assert any("/files/raw" in a and "inline=1" in a for a in site.asked)
    _fits(rig)


def test_an_svg_cannot_be_opened_as_a_document_from_this_origin(rig):
    browser, site = rig["browser"], rig["site"]
    _go(rig, "pic.svg")
    raw = f"/files/raw?path={quote(rig['home'] + '/pic.svg')}&inline=1"

    # Fetched by a script of this origin: bytes for a download, not a picture.
    kind = browser.js(f"fetch({json.dumps(raw)}).then(function(r){{"
                      "return r.headers.get('content-type') + '|' + "
                      "r.headers.get('content-disposition').slice(0, 10)})")
    assert kind == "application/octet-stream|attachment"

    # Framed, embedded and as an object, in a page of this origin.
    browser.js(
        "(function(u){['iframe','embed','object'].forEach(function(tag){"
        "var e=document.createElement(tag); e[tag==='object'?'data':'src']=u;"
        "e.width=200;e.height=100;document.body.appendChild(e);});})(" + json.dumps(raw) + ")"
    )
    time.sleep(2.0)
    assert not [a for a in site.asked if "svg-ran" in a], site.asked

    # Opened in the tab itself: a download, and the page that was there stays.
    browser.call("Page.navigate", url=site.base + raw)
    time.sleep(2.0)
    assert not [a for a in site.asked if "svg-ran" in a], site.asked
    assert browser.js("typeof window.svgRan") == "undefined"


def test_the_same_svg_as_a_document_of_this_origin_would_run(rig):
    """The control: what the two tests above keep from happening is real,
    and the listener hears it."""
    browser, site = rig["browser"], rig["site"]
    _go(rig, "pic.svg")
    # A blob's address has no path to be relative to: the same script, asking
    # the same thing by its whole address.
    absolute = SVG.replace("'/static/", "'" + site.base + "/static/")
    browser.js("location.href = URL.createObjectURL(new Blob([" + json.dumps(absolute)
               + "], {type: 'image/svg+xml'}))")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not [a for a in site.asked if "svg-ran" in a]:
        time.sleep(0.2)
    assert [a for a in site.asked if "svg-ran-script" in a], site.asked


# --- A PDF and a model, drawn in the sealed frame ------------------------------------------
INK = """(function () {
  var c = document.querySelector('canvas.page');
  if (document.documentElement.getAttribute('data-failed')) return 'failed';
  if (!c || !document.documentElement.getAttribute('data-drawn')) return null;
  var d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data, blue = 0;
  for (var i = 0; i < d.length; i += 4) if (d[i + 2] > 200 && d[i] < 60 && d[i + 1] < 60) blue++;
  return JSON.stringify({pages: document.documentElement.getAttribute('data-pages'),
    drawn: document.documentElement.getAttribute('data-drawn'), blue: blue, w: c.width,
    wide: document.documentElement.scrollWidth <= window.innerWidth,
    app: (function () {
      try { return String(parent.document.cookie); } catch (e) { return 'refused'; } })(),
    cookie: (function () { try { return document.cookie; } catch (e) { return 'refused'; } })()});
})()"""


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_a_pdf_is_drawn_in_the_sealed_frame(rig, width, height):
    browser = rig["browser"]
    _go(rig, "doc.pdf", width, height)
    said = _wait_frames(browser, INK)
    assert said[0] != "failed"
    seen = json.loads(said[0])
    assert seen["pages"] == "1" and seen["drawn"] == "1"
    assert seen["blue"] > 500, seen           # the rectangle is on the canvas
    assert seen["wide"], seen                  # the page fits the frame
    # Where it was drawn can reach nothing of the application.
    assert seen["app"] == "refused" and seen["cookie"] in ("refused", ""), seen
    fits = _fits(rig)
    frame = next(b for b in fits["inside"] if b["tag"] == "IFRAME")
    assert frame["r"] <= fits["width"] and frame["h"] > 200


SHAPE = """(function () {
  var failed = document.documentElement.getAttribute('data-failed');
  if (failed) return 'failed:' + failed;
  var c = document.querySelector('canvas.model');
  var n = document.documentElement.getAttribute('data-triangles');
  var note = document.querySelector('.note');
  if (!c || !n) return note ? 'note:' + note.textContent : null;
  var p = document.createElement('canvas'); p.width = c.width; p.height = c.height;
  var x = p.getContext('2d'); x.drawImage(c, 0, 0);
  var d = x.getImageData(0, 0, p.width, p.height).data, lit = 0, red = 0;
  for (var i = 0; i < d.length; i += 4) {
    if (d[i + 3] > 0) lit++;
    if (d[i] > d[i + 1] * 2 && d[i] > 40) red++;
  }
  return JSON.stringify({triangles: n, lit: lit, red: red, w: c.width, h: c.height});
})()"""


@pytest.mark.parametrize("name,triangles", [("shape.stl", "4"), ("tri.glb", "1")])
def test_a_model_is_drawn_in_the_sealed_frame(rig, name, triangles):
    browser = rig["browser"]
    _go(rig, name)
    said = _wait_frames(browser, SHAPE)
    if said[0].startswith("note:"):
        pytest.skip(f"this Chromium draws no WebGL here: {said[0]}")
    assert not said[0].startswith("failed"), said
    seen = json.loads(said[0])
    assert seen["triangles"] == triangles
    assert seen["lit"] > 500, seen
    if name == "tri.glb":
        assert seen["red"] > 200, seen   # the material's own colour
    _fits(rig)


def test_a_model_fits_a_phone(rig):
    _go(rig, "shape.stl", 390, 844)
    _wait_frames(rig["browser"], SHAPE)
    _fits(rig)


# --- A table -------------------------------------------------------------------------------
@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_a_table_is_laid_out_scrolls_in_its_own_box_and_goes_back_to_text(rig, width, height):
    browser = rig["browser"]
    _go(rig, "data.csv", width, height, preview=True)
    seen = json.loads(browser.js("""JSON.stringify((function () {
      var holder = document.querySelector('[data-table]');
      var out = holder.querySelector('[data-table-out]');
      var first = out.querySelector('tr:nth-child(2) td');
      var second = out.querySelector('tr:nth-child(2) td:nth-child(2)');
      var toggle = holder.querySelector('[data-table-toggle]').getBoundingClientRect();
      return {rows: out.querySelectorAll('tr').length, heads: out.querySelectorAll('th').length,
        cell: first.textContent, bold: out.querySelectorAll('b').length, quoted: second.textContent,
        scrolls: out.scrollWidth > out.clientWidth, shown: !out.hidden,
        text: holder.querySelector('[data-table-text]').hidden,
        toggle: {h: Math.round(toggle.height), w: Math.round(toggle.width)}};
    })())"""))
    assert seen["rows"] == 61 and seen["heads"] == 30
    assert seen["cell"] == "<b>bold</b>" and seen["bold"] == 0      # text, never markup
    assert seen["quoted"] == "a, b"
    assert seen["shown"] and seen["text"] and seen["scrolls"]
    if width < 600:
        assert seen["toggle"]["h"] >= TAP, seen
    _fits(rig)

    browser.js("document.querySelector('[data-table-toggle]').click()")
    after = json.loads(browser.js("""JSON.stringify((function () {
      var holder = document.querySelector('[data-table]');
      return {table: holder.querySelector('[data-table-out]').hidden,
              text: holder.querySelector('[data-table-text]').hidden,
              says: holder.querySelector('[data-table-toggle]').textContent};
    })())"""))
    assert after == {"table": True, "text": False, "says": "Show as a table"}
    _fits(rig)


# --- Sound, a notebook, an archive, a binary -----------------------------------------------
def test_sound_loads_over_the_raw_route(rig):
    browser = rig["browser"]
    _go(rig, "tone.wav")
    browser.wait_for(
        "(function(){var a=document.querySelector('[data-testid=\"viewer-audio\"]');"
        "return a && a.readyState >= 1 && a.duration > 0.4;})()", timeout=20, what="the sound")
    _fits(rig)
    _go(rig, "tone.wav", 390, 844)
    _fits(rig)


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_a_notebook_shows_its_cells_and_runs_nothing(rig, width, height):
    browser = rig["browser"]
    _go(rig, "book.ipynb", width, height, preview=True)
    time.sleep(0.8)
    seen = json.loads(browser.js("""JSON.stringify((function () {
      var book = document.querySelector('[data-testid="viewer-notebook"]');
      var img = book.querySelector('img');
      return {cells: book.querySelectorAll('.nb-cell').length,
        images: book.querySelectorAll('img').length,
        decoded: img.complete && img.naturalWidth === 1, ran: typeof window.nbRan,
        scripts: book.querySelectorAll('script').length};
    })())"""))
    assert seen == {"cells": 2, "images": 1, "decoded": True, "ran": "undefined", "scripts": 0}
    _fits(rig)


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
@pytest.mark.parametrize("name", ["pack.zip", f"{LONG}.bin"])
def test_a_listing_and_a_binary_fit(rig, name, width, height):
    _go(rig, name, width, height)
    seen = _fits(rig)
    for inner in seen["inside"]:
        assert inner["r"] <= seen["width"] + 1, inner
    if name == "pack.zip":
        count = rig["browser"].js("document.querySelectorAll('.viewer__entry').length")
        assert count == 41


# --- The MCP sign-in pages, which share this rig ---------------------------------------------
@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844)])
def test_the_mcp_sign_in_pages_fit(rig, db, width, height):
    from lembas.db.models import AUTH_OAUTH, McpServer

    server = McpServer(slug="mail", name="Mail " + "with a long name " * 4,
                       url="https://mcp.example.org/" + "a-long-path/" * 6,
                       auth_kind=AUTH_OAUTH, tools_json=[])
    db.add(server)
    db.commit()
    browser = rig["browser"]
    browser.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                 deviceScaleFactor=1, mobile=width < 600)
    look = """JSON.stringify((function () {
      function box(e) { var r = e.getBoundingClientRect();
        return {l: Math.round(r.left), r: Math.round(r.right), h: Math.round(r.height)}; }
      return {width: window.innerWidth, page: document.documentElement.scrollWidth,
        buttons: [].map.call(document.querySelectorAll(
          '.auth__card .btn, #mcp-signins .btn, .model-row__actions .btn, ' +
          '[data-testid="mcp-oauth-fields"] input'),
          box).filter(function (b) { return b.h > 0; })};
    })())"""
    for address in (f"/settings/mcp/{server.id}/sign-in", "/settings?tab=security",
                    "/admin/mcp", f"/admin/mcp/{server.id}/edit"):
        browser.call("Page.navigate", url=rig["site"].base + address)
        browser.wait_for("document.readyState === 'complete'", timeout=30)
        time.sleep(0.6)
        seen = json.loads(browser.js(look))
        assert seen["page"] <= seen["width"], (address, seen)
        assert seen["buttons"], address
        for button in seen["buttons"]:
            assert button["l"] >= 0 and button["r"] <= seen["width"], (address, seen)
            if width < 600 and "sign-in" in address:
                assert button["h"] >= TAP, (address, seen)
