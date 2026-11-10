"""The chat's toolbar and the composer: nothing cut off or unreachable at any width.

The chat's controls -- the model, Effort and Subagents, the mode, Terminal,
Activity, the chat's settings (tools and skills) and the inspector -- are one
toolbar at the top right of the chat (`.chatbar`, `chat/index.html`). When the
bar is narrow the fold's items go behind More, as a menu of the same elements.
The composer keeps the message: text, attach, Draw a picture, dictate, send.

Measured in a real Chromium at 1920, 1280, 1024, 800 and 390 px, with a long
model name, every chip and attachments: every control that is drawn lies inside
the window and is the thing a click at its centre lands on; what is folded is
reachable through More, and every item there passes the same test; the title
keeps its floor; the Effort and Subagents popover opens inside the window.
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

CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")
LONG = "Qwen3.6 Coder 480B A35B Instruct · Q4_K_M · 256k context · thinking"

PROBE = """
<style>*, *::before, *::after { transition: none !important; animation: none !important; }</style>
<script>
window.addEventListener('load', function () {
  var W = window.innerWidth, H = window.innerHeight;
  var out = {width: W, bar: [], fold: [], composer: [], folded: false};
  function shown(el) {
    var r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  }
  function check(el) {
    var r = el.getBoundingClientRect();
    var hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    return {name: (el.getAttribute('aria-label') || el.getAttribute('name') ||
                   el.className.toString()).slice(0, 40),
            left: Math.round(r.left), right: Math.round(r.right),
            top: Math.round(r.top), bottom: Math.round(r.bottom),
            w: Math.round(r.width), hits: !!hit && (hit === el || el.contains(hit)),
            hit: hit ? (hit.tagName + '.' + (hit.getAttribute('class') || '')).slice(0, 50) : null};
  }
  function controls(root, sel) {
    return Array.prototype.filter.call(root.querySelectorAll(sel), shown).map(check);
  }
  var CONTROL = 'button, a.btn, select, textarea';
  setTimeout(function () {
    out.bar = controls(document.querySelector('.topbar'), CONTROL);
    out.title = Math.round(document.querySelector('.topbar__title').getBoundingClientRect().width);
    out.composer = controls(document.querySelector('.composer__form'), CONTROL);
    var settings = document.querySelector('.chatbar__settings [data-picker-toggle]');
    if (settings) {
      settings.click();
      var menu = document.querySelector('.chatbar__settings [data-picker-menu]');
      var r = menu.getBoundingClientRect();
      out.settings = {open: !menu.hidden, left: r.left, right: r.right, bottom: r.bottom,
                      subagents: !!menu.querySelector('[data-team-setting]'),
                      effort: !!menu.querySelector('[data-effort]')};
      settings.click();
    }
    var more = document.querySelector('.chatbar__more');
    out.folded = !!more && shown(more);
    if (out.folded) {
      more.click();
      setTimeout(function () {
        out.fold = controls(document.querySelector('.chatbar__items'), CONTROL);
        window.__probe = out;
      }, 200);
    } else {
      window.__probe = out;
    }
  }, 400);
});
</script>
"""


def _run(client: TestClient, path: str, width: int, height: int, tmp: Path, chips: str) -> dict:
    spec = importlib.util.spec_from_file_location(
        "shoot", Path(__file__).resolve().parent.parent / "scripts/shoot.py"
    )
    shoot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shoot)

    response = client.get(path)
    assert response.status_code == 200
    html = response.text.replace(
        '<div class="composer__attachments" id="attachments"></div>',
        f'<div class="composer__attachments" id="attachments">{chips}</div>',
        1,
    )
    assert chips in html, "the attachments did not reach the composer; this test went blind"
    assets = tmp / "assets"
    assets.mkdir(exist_ok=True)
    html = shoot.rewrite(html, client, assets).replace("</body>", PROBE + "</body>", 1)
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
    dom = subprocess.run(
        [CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu",
         "--allow-file-access-from-files", f"--window-size={max(width, 520)},{height + 40}",
         "--virtual-time-budget=8000", "--dump-dom", f"file://{frame.resolve()}"],
        capture_output=True, text=True, timeout=120,
    ).stdout
    found = re.search(r'<pre id="out">(.*?)</pre>', dom, re.S)
    assert found and found.group(1).strip() not in ("", "null"), "the probe did not report"
    return json.loads(found.group(1).replace("&amp;", "&").replace("&quot;", '"'))


def _seed(db) -> str:
    """A chat on a reasoning model with a long name, a second model that could
    serve as its subagent, pictures and subagents on: every control drawn."""
    from lembas.db.models import Chat, Connection, Model, User
    from lembas.services import settings_store

    first = Connection(name="local", base_url="http://127.0.0.1:1", api_key_encrypted="")
    second = Connection(name="other", base_url="http://127.0.0.1:2", api_key_encrypted="")
    db.add_all([first, second])
    db.flush()
    db.add(Model(connection_id=first.id, model_id="long", display_name=LONG, pinned=True,
                 capabilities_json={"reasoning": True, "tools": True},
                 reasoning_efforts=["low", "medium", "high"], thinking_switch=True))
    db.add(Model(connection_id=second.id, model_id="helper", display_name="helper",
                 capabilities_json={"tools": True}))
    settings_store.update(db, {"enabled": True}, key=settings_store.SUBAGENTS)
    settings_store.update(
        db, {"enabled": True, "base_url": "http://127.0.0.1:3", "checkpoints": ["sdxl"]},
        key=settings_store.IMAGES,
    )
    settings_store.update(db, {"stt_enabled": True}, key=settings_store.AUDIO)
    owner = db.query(User).first()
    chat = Chat(user_id=owner.id, model_id="long", connection_id=first.id,
                data_group_id="default", title="Where the config loader is called from")
    db.add(chat)
    db.commit()
    return chat.id


def _chips(client: TestClient, chat_id: str) -> str:
    return "".join(
        client.post(f"/api/files?chat_id={chat_id}", files={"file": (name, body, kind)}).text
        for name, body, kind in (
            ("a-rather-long-report-name-for-the-quarter-and-the-year.md", b"# R\n",
             "text/markdown"),
            ("notes.md", b"# Notes\n", "text/markdown"),
            ("config.yaml", b"a: 1\n", "text/yaml"),
        )
    )


def test_the_chat_controls_are_the_toolbar_and_the_composer_keeps_the_message(
    client: TestClient, db, registered
):
    """The markup the layout is built on: the controls in `.chatbar`, in order;
    the composer with attach, the picture chip, dictate and send and nothing
    of the chat's own."""
    chat_id = _seed(db)
    html = client.get(f"/chat/{chat_id}").text

    start = html.index('class="topbar__actions chatbar"')
    bar = html[start:html.index("</header>", start)]
    order = ["picker__button", 'id="chatbar-settings"', 'name="agent_mode"',
             'data-toggle="#activity"', 'data-toggle="#chat-settings"']
    at = [bar.index(marker) for marker in order]
    assert at == sorted(at), "the toolbar's order changed"
    # Effort and Subagents in the popover, never folded.
    settings = bar[bar.index('id="chatbar-settings"'):bar.index("chatbar__fold")]
    assert "data-effort" in settings and "data-team-setting" in settings

    composer = html[html.index('class="composer__form"'):html.index('id="agent-mode-form"')]
    for wanted in ("data-composer-input", "data-attach", "data-picture",
                   'name="force_tool"', "data-mic", "data-composer-action"):
        assert wanted in composer, wanted
    for gone in ('name="agent_mode"', "data-effort", "chatbar", "data-toggle="):
        assert gone not in composer, gone


@pytest.mark.slow
@pytest.mark.skipif(not CHROMIUM, reason="no chromium on this machine")
@pytest.mark.parametrize(
    "width, height", [(1920, 1080), (1280, 800), (1024, 768), (800, 900), (390, 844)]
)
def test_in_a_browser_nothing_is_cut_off_or_out_of_reach(
    client: TestClient, db, registered, tmp_path, width, height
):
    chat_id = _seed(db)
    result = _run(client, f"/chat/{chat_id}", width, height, tmp_path, _chips(client, chat_id))

    def reachable(rows: list[dict], where: str) -> None:
        assert rows, f"nothing measured in the {where}; this test went blind"
        for row in rows:
            assert row["left"] >= 0 and row["right"] <= width, (where, row)
            assert row["hits"], f"{where}: covered or clipped: {row}"

    reachable(result["bar"], "toolbar")
    reachable(result["composer"], "composer")
    assert result["title"] >= 64, "the chat's title was squeezed below its floor"

    names = {row["name"] for row in result["bar"] + result["fold"]}
    for control in ("Activity", "Chat settings", "Inspect this chat", "Effort and subagents"):
        assert control in names, (control, "neither in the bar nor behind More")
    assert "Draw a picture" not in names  # the composer's, not the bar's
    composer = {row["name"] for row in result["composer"]}
    assert {"Attach", "Dictate a message", "Send"} <= composer
    assert any("composer__chip" in name for name in composer), composer

    if result["folded"]:
        reachable(result["fold"], "More menu")
    settings = result["settings"]
    assert settings["open"] and settings["effort"] and settings["subagents"]
    assert settings["left"] >= 0 and settings["right"] <= width
    assert settings["bottom"] <= height

    # Wide, nothing is folded; on a phone, the fold is.
    if width >= 1280:
        assert not result["folded"]
    if width <= 800:
        assert result["folded"]
