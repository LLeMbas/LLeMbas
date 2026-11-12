"""Where a new agent chat runs: one slim bar above the composer's card.

Computer · folder · mode, three chips of one control family on one line,
folding to one summary chip where the bar is narrow (`.composer__target`,
`chat/_composer.html`; the wiki's Working notes describe the same bar for
LLeMbas Desktop).

Measured in a real Chromium at 1920, 1280, 1024, 800 and 390 px with a long
computer name and a long path: every chip that is drawn lies inside the bar
and is the thing a press at its centre lands on; unfolded they are on one
line and one height; folded there is one chip, which opens the three as a
column that lines up. A screenshot of each width is left in the test's
temporary directory.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lembas

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
LONG_HOST = "workstation-with-a-rather-long-name-in-the-attic"
LONG_DIR = "/home/developer/projects/clients/a-very-long-client-name/the-repository-of-the-loader"
CHAT_CSS = (Path(lembas.__file__).parent / "web/static/css/chat.css").read_text()

PROBE = """
<style>*, *::before, *::after { transition: none !important; animation: none !important; }</style>
<script>
window.addEventListener('load', function () {
  function shown(el) {
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  }
  function box(el) {
    var r = el.getBoundingClientRect();
    var hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return {name: (el.getAttribute('aria-label') || el.className.toString()).slice(0, 40),
            left: r.left, right: r.right, top: r.top, bottom: r.bottom,
            w: r.width, h: r.height, text: el.textContent.trim().replace(/\\s+/g, ' '),
            clipped: el.scrollWidth > el.clientWidth + 1,
            ellipsis: Array.prototype.some.call(el.querySelectorAll('*'), function (n) {
              return n.scrollWidth > n.clientWidth + 1 &&
                     getComputedStyle(n).textOverflow === 'ellipsis';
            }),
            hits: !!hit && (hit === el || el.contains(hit))};
  }
  function chips() {
    return Array.prototype.filter.call(
      document.querySelectorAll('.composer__target .btn'), shown).map(box);
  }
  setTimeout(function () {
    var bar = document.querySelector('.composer__target');
    var card = document.querySelector('.composer__card');
    var summary = document.querySelector('[data-target-summary]');
    var out = {width: window.innerWidth,
               bar: box(bar), card: box(card),
               page: document.documentElement.scrollWidth,
               folded: shown(summary), chips: chips(), open: null, names: null,
               toolbar: (function () {
                 var el = document.querySelector('.chatbar__row .btn, .topbar .btn');
                 return el ? el.getBoundingClientRect().height : 0; })()};
    if (!out.folded) { window.__probe = out; return; }
    summary.click();
    setTimeout(function () {
      out.open = chips();
      out.names = Array.prototype.filter.call(
        document.querySelectorAll('.target__name'), shown).map(box);
      out.fields = Array.prototype.filter.call(
        document.querySelectorAll('.target__field'), shown).map(box);
      out.expanded = summary.getAttribute('aria-expanded');
      window.__probe = out;
    }, 200);
  }, 500);
});
</script>
"""


def _run(client: TestClient, width: int, height: int, tmp: Path) -> dict:
    spec = importlib.util.spec_from_file_location(
        "shoot", Path(__file__).resolve().parent.parent / "scripts/shoot.py"
    )
    shoot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shoot)

    response = client.get("/chat?kind=agent")
    assert response.status_code == 200
    assert "data-agent-picker" in response.text, "no device was offered; this test went blind"
    assets = tmp / "assets"
    assets.mkdir(exist_ok=True)
    html = shoot.rewrite(response.text, client, assets).replace("</body>", PROBE + "</body>", 1)
    page = tmp / f"page-{width}.html"
    page.write_text(html, encoding="utf-8")
    frame = tmp / f"frame-{width}.html"
    frame.write_text(
        "<!doctype html><meta charset=utf-8><style>html,body{margin:0}"
        f"iframe{{width:{width}px;height:{height}px;border:0;display:block}}</style>"
        f'<iframe id="f" src="{page.name}"></iframe><pre id="out"></pre><script>'
        "window.addEventListener('load',function(){setTimeout(function(){"
        "document.getElementById('out').textContent="
        "JSON.stringify(document.getElementById('f').contentWindow.__probe||null);"
        "},2500);});</script>",
        encoding="utf-8",
    )
    common = [CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu",
              "--allow-file-access-from-files", "--hide-scrollbars",
              f"--window-size={max(width, 520)},{height + 40}", "--virtual-time-budget=8000"]
    subprocess.run(common + [f"--screenshot={tmp / f'target-bar-{width}.png'}",
                             f"file://{frame.resolve()}"], capture_output=True, timeout=120)
    dom = subprocess.run(common + ["--dump-dom", f"file://{frame.resolve()}"],
                         capture_output=True, text=True, timeout=120).stdout
    found = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    assert found and found.group(1).strip() not in ("", "null"), "the probe did not report"
    return json.loads(found.group(1).replace("&amp;", "&").replace("&quot;", '"')
                      .replace("&lt;", "<").replace("&gt;", ">"))


@pytest.fixture
def agent_screen(client: TestClient, db, registered):
    """A linked device with a long name, and a chat there in a long path, so
    the bar opens with both filled in."""
    from lembas.db.models import TOKEN_DEVICE, Chat, Connection, Model, User
    from lembas.security import api_tokens
    from lembas.services import device_link, settings_store

    connection = Connection(name="local", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.flush()
    db.add(Model(connection_id=connection.id, model_id="m", display_name="m",
                 capabilities_json={"tools": True}))
    settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
    user = db.query(User).first()
    token, _ = api_tokens.create(
        db, user, name=f"LLeMbas CLI on {LONG_HOST}", scopes=["models", "link"],
        kind=TOKEN_DEVICE, device_host=LONG_HOST,
    )
    db.add(Chat(user_id=user.id, kind="agent", title="x", model_id="m",
                connection_id=connection.id, device_token_id=token.id, project_dir=LONG_DIR))
    db.commit()

    async def nothing(*_a, **_k):
        return None

    device_link.LINKS[token.id] = device_link.Link(
        token_id=token.id, user_id=user.id, name=token.name, send_text=nothing, close=nothing,
        hello={"device": {"host": LONG_HOST}, "limits": {"roots": ["/home"], "max_mode": "edit"}},
    )
    yield client
    device_link.reset()


def test_the_bar_is_above_the_card_and_holds_the_three_in_order(agent_screen):
    html = agent_screen.get("/chat?kind=agent").text
    form = html[html.index('class="composer__form"'):]
    bar, card = form.index('class="composer__target"'), form.index('class="composer__card"')
    assert bar < card, "the bar is in the form, before the card -- not inside it"
    inside = form[bar:card]
    order = ['name="ssh_profile_id"', "data-dir-browse", 'name="agent_mode"']
    at = [inside.index(marker) for marker in order]
    assert at == sorted(at), "computer, folder, mode"
    # One control family: both selects become the upward button the folder is.
    assert inside.count("data-upward") == 2 and 'class="btn btn--sm composer__dir"' in inside
    # Nothing of the target is left in the card.
    card_html = form[card:form.index("</form>")]
    for gone in ('name="ssh_profile_id"', "data-dir-browse", 'name="agent_mode"'):
        assert gone not in card_html
    assert LONG_HOST in inside


def test_the_sizes_are_said_not_hoped_for():
    code = re.sub(r"/\*.*?\*/", "", CHAT_CSS, flags=re.S)
    assert "container: target / inline-size" in code
    assert "@container target not (min-width: 30rem)" in code
    # The mode never shrinks; the two names give, to a floor, and are capped.
    assert re.search(r"\.target__field--mode \{ flex: none;", code)
    assert re.search(r"\.target__field \{[^}]*min-width: 6rem", code)
    assert "max-width: var(--w-chip-sm)" in code and "max-width: var(--w-chip)" in code
    # One line: nothing in the bar wraps or scrolls.
    for rule in (".target {", ".composer__agent {"):
        body = code.split(rule, 1)[1].split("}", 1)[0]
        assert "flex-wrap" not in body and "overflow-x" not in body
    # A thumb's size where the pointer is coarse.
    assert re.search(
        r"@media \(pointer: coarse\) \{\s*\.composer__target \.btn \{ min-height: var\(--tap-min\)",
        code,
    )


@pytest.mark.slow
@pytest.mark.skipif(CHROMIUM is None, reason="no chromium on this machine")
@pytest.mark.parametrize(("width", "height"),
                         [(1920, 1080), (1280, 800), (1024, 768), (800, 900), (390, 844)])
def test_nothing_is_cut_off_at_any_width(agent_screen, tmp_path, width, height):
    seen = _run(agent_screen, width, height, tmp_path)
    assert seen["width"] == width
    assert seen["page"] <= width, "the page scrolls sideways"
    bar, card = seen["bar"], seen["card"]
    assert bar["bottom"] <= card["top"] + 0.5, "the bar is above the card"
    assert bar["left"] >= 0 and bar["right"] <= width + 0.5

    def inside(chip: dict) -> None:
        assert chip["left"] >= bar["left"] - 0.5 and chip["right"] <= bar["right"] + 0.5, chip
        assert chip["hits"], f"{chip['name']} is covered: a press would land elsewhere"

    chips = seen["chips"]
    for chip in chips:
        inside(chip)
    if not seen["folded"]:
        assert len(chips) == 3, [c["name"] for c in chips]
        # One line, one height: one control family.
        assert max(c["top"] for c in chips) - min(c["top"] for c in chips) < 1
        assert max(c["h"] for c in chips) - min(c["h"] for c in chips) < 1
        lefts = [c["left"] for c in chips]
        assert lefts == sorted(lefts), "computer, folder, mode -- left to right"
        # The long names are cut with an ellipsis, never by the edge of a box.
        device, folder, mode = chips
        assert device["ellipsis"] and LONG_HOST[:8] in device["text"]
        assert "the-repository-of-the-loader"[:8] in folder["text"]
        assert not mode["ellipsis"], "the mode is short and is never cut"
        if seen["toolbar"]:
            assert abs(chips[0]["h"] - seen["toolbar"]) <= 12, "the toolbar's family of chips"
    else:
        # One summary chip across the bar: computer · folder · mode.
        assert len(chips) == 1 and chips[0]["text"].count("·") == 2
        assert LONG_HOST[:6] in chips[0]["text"] and chips[0]["text"].endswith("Edit")
        assert chips[0]["w"] >= bar["w"] * 0.9
        # Opened: the three, each in a row of its own, lined up.
        assert seen["expanded"] == "true"
        opened = [c for c in seen["open"] if c["name"] != "Where this chat runs"]
        assert len(opened) == 3
        for chip in seen["open"]:
            inside(chip)
        tops = [c["top"] for c in opened]
        assert tops == sorted(tops) and len({round(t) for t in tops}) == 3
        fields = seen["fields"]
        assert len({round(f["left"]) for f in fields}) == 1, "one shared first column"
        assert [n["text"] for n in seen["names"]] == ["Computer", "Folder", "Mode"]
    assert (tmp_path / f"target-bar-{width}.png").exists()
