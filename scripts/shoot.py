"""Render LLeMbas pages in a real browser, at a real size.

Run it:

    python scripts/shoot.py OUTDIR [/chat,/settings]        # measure + capture
    python scripts/shoot.py OUTDIR --manifest-screenshots   # the two the
                                                            # manifest wants

Needs a `chromium` on PATH and the development dependencies installed. It is a
development instrument, like the Node DOM stub the JavaScript is driven under
and like `fetch_vendor.py` -- it is not imported by the application and nothing
in `src/` knows it exists.

Not a test runner: an instrument. It renders a page through TestClient, rewrites
every asset URL to a file:// path, and refuses to continue if even one is left
pointing at `testserver` -- because the last harness that did this silently
measured an unstyled document and reported all five tab panels visible at once.
A dramatic finding that was entirely an artefact of a rewrite matching nothing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from functools import cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

# Resolved from the package that actually got imported, not from where this
# file happens to sit. A copy of this script run from somewhere else silently
# pointed STATIC at a directory that did not exist, every asset URL was
# rewritten to a file:// path with nothing behind it, and the run measured an
# unstyled document -- reporting that every page in the application overflowed
# by thirty thousand pixels. The guard below only asked whether the URLs had
# been rewritten, which they had.
import lembas  # noqa: E402

SRC = Path(lembas.__file__).resolve().parent.parent
STATIC = Path(lembas.__file__).resolve().parent / "web/static"
CHROMIUM = shutil.which("chromium") or shutil.which("chromium-browser")

# Routes that are served by the app rather than mounted, so the rewrite has to
# fetch them rather than point at a file that does not exist.
ROUTE_ASSETS = {"/branding.css": "branding.css", "/sw.js": "sw.js"}

MEASURE = """
<script>
window.__measure = function () {
  var de = document.scrollingElement || document.documentElement;
  var small = [];
  document.querySelectorAll(
    'button, a.btn, a.nav-item, .tabs__tab, input, select, [role=tab]'
  ).forEach(function (el) {
    var r = el.getBoundingClientRect();
    if (!r.width || !r.height) return;              /* hidden */
    if (el.closest('[hidden]')) return;
    /* A `.visually-hidden` radio is 1x1 on purpose -- the <label> beside it is
       the target, and that one is measured. Counting the input reports five
       failures on a settings page whose tabs are all 44px. */
    if (el.classList.contains('visually-hidden')) return;
    /* Inline text inside a sentence is not a tap target in the sense this is
       checking; it is a word you can also click. */
    if (getComputedStyle(el).display === 'inline') return;
    if (r.height < 40 || r.width < 40) {
      small.push({
        tag: el.tagName.toLowerCase(),
        cls: el.className && el.className.toString().slice(0, 60),
        label: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30),
        w: Math.round(r.width), h: Math.round(r.height)
      });
    }
  });
  var wide = [];
  document.querySelectorAll('body *').forEach(function (el) {
    var r = el.getBoundingClientRect();
    if (r.right > window.innerWidth + 1 || r.left < -1) {
      wide.push({
        tag: el.tagName.toLowerCase(),
        cls: el.className && el.className.toString().slice(0, 60),
        left: Math.round(r.left), right: Math.round(r.right)
      });
    }
  });
  /* Which element is actually making the document bigger than the window.
     "the page over-scrolls" is not actionable; "`.shell` is 1756px tall in an
     844px window" is. Reported for both axes, deepest first, because the
     outermost offender is usually just the ancestor of the real one. */
  /* Content taller than the window inside something built to scroll is not
     overflow, it is the point. So an element counts only when nothing between
     it and the root can scroll in that axis -- otherwise every long settings
     page reports its own cards as a bug and the signal is lost in them. */
  function contained(el, axis) {
    var prop = axis === 'y' ? 'overflowY' : 'overflowX';
    for (var n = el.parentElement; n && n !== document.documentElement; n = n.parentElement) {
      var o = getComputedStyle(n)[prop];
      if (o === 'auto' || o === 'scroll' || o === 'hidden') return true;
    }
    return false;
  }

  function culprits(axis) {
    var found = [];
    document.querySelectorAll('body, body *').forEach(function (el) {
      if (contained(el, axis)) return;
      var r = el.getBoundingClientRect();
      var over = axis === 'y'
        ? r.bottom - window.innerHeight
        : r.right - window.innerWidth;
      if (over > 1) {
        found.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.className && el.className.toString().slice(0, 50)) || '',
          over: Math.round(over),
          size: Math.round(axis === 'y' ? r.height : r.width),
          pos: getComputedStyle(el).position,
          id: el.id || '',
          parent: el.parentElement ? (el.parentElement.tagName.toLowerCase() + '.' +
                  (el.parentElement.className || '').toString().slice(0, 30)) : '',
          html: el.outerHTML.slice(0, 120)
        });
      }
    });
    return found.sort(function (a, b) { return b.over - a.over; }).slice(0, 8);
  }

  /* --- A box that scrolls sideways when nobody asked it to -----------------

     The blind spot that hid the suggestions bug through forty measurements.
     `.suggestions` rendered 455px wide inside a 390px `.thread-scroll`, and
     every check above looked straight past it: `culprits('x')` skips anything
     with a scrollable ancestor -- correct for a table inside its own scroller,
     wrong for the scroller itself -- and `scrollsSideways` stayed false because
     `.thread-scroll` absorbed the overflow instead of the document.

     "Authored" is the distinction that makes this reportable rather than noise.
     The tree's rule is that anything wide gets its OWN scroller, so a wrapper
     carrying `overflow-x: auto` in a stylesheet is right. A box given only
     `overflow-y: auto` scrolls sideways as well, because the other axis then
     computes to `auto` -- and that is always a bug. Computed style cannot tell
     those apart, both being `auto`, so the rules that say it are read off the
     stylesheets -- in Python, by `authored_sideways()` below, and not from the
     CSSOM here: a stylesheet loaded over `file://` is a foreign origin for
     `cssRules` even with `--allow-file-access-from-files`, and every sheet
     throws. That silently found *nothing authored*, which turns this check into
     "every vertical scroller is a bug" -- so the list arriving empty is a hard
     error rather than a clean run. */
  var sidewaysAuthors = __SIDEWAYS_AUTHORS__;

  function authoredSideways(el) {
    if (el.style.overflowX || el.style.overflow) return true;
    for (var i = 0; i < sidewaysAuthors.length; i++) {
      try { if (el.matches(sidewaysAuthors[i])) return true; } catch (e) { /* :has() etc */ }
    }
    return false;
  }

  var sideways = [];
  document.querySelectorAll('body, body *').forEach(function (el) {
    var ox = getComputedStyle(el).overflowX;
    if (ox !== 'auto' && ox !== 'scroll') return;
    if (el.scrollWidth <= el.clientWidth + 1) return;
    if (authoredSideways(el)) return;
    /* Which child is doing it. "`.thread-scroll` scrolls sideways" is not
       actionable; "`.suggestions` is 455px inside its 390px" is. */
    var worst = null;
    el.querySelectorAll('*').forEach(function (kid) {
      var over = kid.getBoundingClientRect().width - el.clientWidth;
      if (over > 1 && (!worst || over > worst.over)) {
        worst = {tag: kid.tagName.toLowerCase(),
                 cls: (kid.className && kid.className.toString().slice(0, 50)) || '',
                 w: Math.round(kid.getBoundingClientRect().width),
                 over: Math.round(over)};
      }
    });
    sideways.push({tag: el.tagName.toLowerCase(),
                   cls: (el.className && el.className.toString().slice(0, 50)) || '',
                   scrollW: el.scrollWidth, clientW: el.clientWidth,
                   widest: worst});
  });

  var shell = document.querySelector('.shell');
  return {
    sidewaysScrollers: sideways.slice(0, 8),
    sidewaysCount: sideways.length,
    docScrollH: de.scrollHeight,
    innerH: window.innerHeight,
    docScrollW: de.scrollWidth,
    innerW: window.innerWidth,
    bodyScrollH: document.body.scrollHeight,
    shellH: shell ? Math.round(shell.getBoundingClientRect().height) : null,
    shellW: shell ? Math.round(shell.getBoundingClientRect().width) : null,
    tallCulprits: culprits('y'),
    wideCulprits: culprits('x'),
    /* The invariant: the application shell fills the window and the DOCUMENT
       never scrolls *for the reader*. A document taller than the window is the
       /settings bug -- but only when the reader can actually move it. `overflow:
       hidden` blocks a wheel and a finger while still permitting an assignment
       to scrollTop, so a page whose shell clips a tall descendant reports a
       scrollHeight of thousands and scrolls for nobody. /admin/prompts does
       exactly that, and reading the raw height called it a bug four times. */
    documentScrolls:
      de.scrollHeight > window.innerHeight + 1 &&
      ["visible", "auto", "scroll"].indexOf(
        getComputedStyle(document.documentElement).overflowY
      ) !== -1,
    scrollsSideways: de.scrollWidth > window.innerWidth + 1,
    smallTargets: small.slice(0, 40),
    smallCount: small.length,
    overflowing: wide.slice(0, 20),
    overflowCount: wide.length
  };
};
/* Nothing is appended to the page itself. The first version of this harness
   did exactly that, and the div it added was 960px tall -- so the very first
   run reported that /chat over-scrolled by 960px on a phone, which was a
   finding entirely about the instrument. The frame outside reads __measure()
   across the boundary instead, and the page is left exactly as served. */
</script>
"""


@cache
def authored_sideways() -> tuple[str, ...]:
    """Selectors whose rules really do ask for horizontal scrolling.

    The tree's rule is that anything wide gets its own scroller, so these are
    the correct ones: a table wrapper, a code block, the tab bar. Everything
    else that scrolls sideways is `overflow-y: auto` dragging the other axis
    along with it, which is always a bug and is what `.suggestions` did.
    """
    selectors: list[str] = []
    for path in sorted((STATIC / "css").glob("*.css")):
        text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
        # Innermost blocks only: `[^{}]*` cannot cross a brace, so an `@media`
        # prelude never matches and the rules inside it do.
        for prelude, body in re.findall(r"([^{}]*)\{([^{}]*)\}", text):
            wants = False
            for declaration in body.split(";"):
                name, _, value = declaration.partition(":")
                name, value = name.strip().lower(), value.strip().lower()
                if name not in ("overflow", "overflow-x") or not value:
                    continue
                # `overflow: hidden auto` is x then y, so the first word is ours;
                # `overflow: auto` is both.
                wants = wants or value.split()[0] in ("auto", "scroll")
            if not wants:
                continue
            selectors += [
                part.strip()
                for part in prelude.split(",")
                if part.strip() and not part.strip().startswith("@")
            ]
    if not selectors:
        raise SystemExit("read no horizontal-overflow rules -- the sideways check would cry wolf")
    return tuple(selectors)


# The chat `build_client` seeds, so a run can name `/chat/<SHOOT_CHAT>`.
SHOOT_CHAT = "5" * 32


def build_client():
    import lembas.config as config_mod

    tmp = Path(tempfile.mkdtemp(prefix="lembas-shoot-"))
    config_mod.settings.data_dir = tmp
    config_mod.settings.secret_key = "x" * 43

    from fastapi.testclient import TestClient

    from lembas.db.session import init_db, session_scope
    from lembas.main import create_app

    init_db()
    app = create_app()
    client = TestClient(app)
    client.post(
        "/auth/register",
        data={"name": "Frodo", "email": "f@example.com", "password": "mellonmellon"},
        follow_redirects=False,
    )

    from lembas.db.models import Connection, Model

    with session_scope() as db:
        connection = Connection(
            name="local", base_url="http://127.0.0.1:1", api_key_encrypted=""
        )
        db.add(connection)
        db.flush()
        # One reasoning model (the composer's effort control only exists on one)
        # and both pinned, so the sidebar's pinned list is drawn too.
        for name in ("gemma4-moe", "qwen3-coder"):
            db.add(
                Model(
                    connection_id=connection.id,
                    model_id=name,
                    display_name=name,
                    pinned=True,
                    capabilities_json={"reasoning": name == "gemma4-moe", "tools": True},
                )
            )

    # A second data group with a hosted model in it, a note in each group and a
    # chat with a fixed id (`SHOOT_CHAT`). Without them every grouped screen --
    # the chips, the Data tab, the models named under the picker as being in
    # another group -- is rendered with nothing in it, which is the "a screen the
    # test never renders is unchecked" lesson again.
    from lembas.db.models import Chat, DataGroup, Note, User

    with session_scope() as db:
        db.add(DataGroup(id="hosted", name="Hosted providers"))
        hosted = Connection(
            name="hosted",
            base_url="http://127.0.0.1:2",
            api_key_encrypted="",
            data_group_id="hosted",
        )
        db.add(hosted)
        db.flush()
        db.add(
            Model(
                connection_id=hosted.id,
                model_id="deepseek-flash",
                display_name="DeepSeek Flash",
                capabilities_json={"reasoning": True, "tools": True},
            )
        )
        owner = db.query(User).first()
        db.add(Note(owner_id=owner.id, title="A note at home", body="x", data_group_id="default"))
        db.add(Note(owner_id=owner.id, title="A hosted note", body="x", data_group_id="hosted"))
        local = db.query(Connection).filter_by(name="local").first()
        db.add(
            Chat(
                id=SHOOT_CHAT,
                user_id=owner.id,
                model_id="gemma4-moe",
                connection_id=local.id,
                data_group_id="default",
                title="A chat to measure",
            )
        )

    # A conversation in the measured chat: a prompt, and a reply with a
    # command, an edit, a read and a message taken in mid-reply -- the blocks a
    # transcript is made of, so their widths are measured on a phone too.
    from lembas.db.models import Message

    diff = "\n".join(
        [
            "--- a/src/app.py",
            "+++ b/src/app.py",
            "@@ -10,4 +10,6 @@",
            " def main():",
            "-    run(config)",
            "+    config = load(Path.home() / '.config' / 'app' / 'settings.yaml')",
            "+    run(config, verbose=True)",
            "+    report()",
            " ",
        ]
    )
    command = "npm test -- --reporter=dot --coverage --runInBand src/very/long/path"
    with session_scope() as db:
        db.add(
            Message(
                chat_id=SHOOT_CHAT,
                role="user",
                content="Fix the config loading and run the tests.",
            )
        )
        db.flush()
        read = {
            "name": "read",
            "kind": "agent",
            "query": "src/app.py · 1–80 of 214",
            "path": "src/app.py · 1–80 of 214",
            "text": "...",
            "status": "ok",
        }
        edit = {
            "name": "edit",
            "kind": "agent",
            "query": "src/app.py",
            "args": {"path": "src/app.py"},
            "diff": diff,
            "status": "ok",
            "text": "",
        }
        bash = {
            "name": "bash",
            "kind": "agent",
            "query": "npm test",
            "args": {"command": command},
            "text": "42 passing",
            "exit": 0,
            "status": "ok",
        }
        reply = Message(
            chat_id=SHOOT_CHAT,
            role="assistant",
            model_id="gemma4-moe",
            content="Found it. Running the tests now.All 42 pass.",
            tool_calls_json=[read, edit, bash],
            steps_json=[
                {"round": 0, "thinking_to": 0, "text_to": 9, "tools_to": 2, "thinking_ms": 0},
                {
                    "round": 1,
                    "thinking_to": 0,
                    "text_to": 32,
                    "tools_to": 3,
                    "thinking_ms": 0,
                    "steer": [{"id": "", "text": "and check the README too"}],
                },
            ],
            usage_json={
                "prompt_tokens": 1200,
                "completion_tokens": 300,
                "total_tokens": 1500,
                "elapsed_ms": 12_000,
            },
        )
        db.add(reply)

    # 🚨 The suggestion cards are seeded by the startup hook, and `TestClient(app)`
    # runs a lifespan only inside a `with` block -- so every shot of the new-chat
    # screen ever taken by this script was of a page with its cards missing. That
    # is how a grid 65px wider than a phone survived forty measurements. Seeded
    # here rather than by entering the lifespan, which would also start the
    # schedule ticker and rehydrate background jobs inside a screenshot run.
    from lembas.services.suggestions import seed_defaults as seed_suggestions

    with session_scope() as db:
        seed_suggestions(db)
        # Agent chats on, so the sidebar's Chats/Agents switch and the agent
        # composer's controls are rendered at all -- a switch that is never
        # drawn is a switch nobody has measured.
        from lembas.services import settings_store

        settings_store.update(db, {"enabled": True}, key=settings_store.AGENTS)
        # Dictation and reading aloud on, with no server behind them: the Audio
        # settings tab exists only then, and an empty voice list is answered
        # without a request.
        settings_store.update(
            db, {"stt_enabled": True, "tts_enabled": True}, key=settings_store.AUDIO
        )
        # `SHOOT_LANG=sk` shoots the pages in that language: the one person's own
        # choice, exactly as the settings screen stores it.
        language = os.environ.get("SHOOT_LANG", "")
        if language:
            owner = db.query(User).first()
            owner.settings_json = {**(owner.settings_json or {}), "language": language}
        # `SHOOT_KIND=agent` shoots the sidebar on its Agents side.
        kind = os.environ.get("SHOOT_KIND", "")
        if kind:
            owner = db.query(User).first()
            owner.settings_json = {**(owner.settings_json or {}), "sidebar_kind": kind}

    _seed_devices_reports_schedules()
    return client


def _seed_devices_reports_schedules() -> None:
    """Two linked devices and one that is not, each with an agent chat; a report;
    a schedule. Without them the Agents side of the sidebar, Library → Reports
    and Settings → Scheduled are shot empty -- the screens nobody measured."""
    from datetime import UTC, datetime, timedelta

    from lembas.db.models import TOKEN_DEVICE, ApiToken, Chat, Connection, Report, Schedule, User
    from lembas.db.session import session_scope
    from lembas.services import device_link

    with session_scope() as db:
        owner = db.query(User).first()
        local = db.query(Connection).filter_by(name="local").first()
        for index, (host, linked) in enumerate(
            (("device-1", True), ("device-2", True), ("old-laptop", False))
        ):
            token = ApiToken(
                user_id=owner.id, name=f"LLeMbas CLI on {host}", kind=TOKEN_DEVICE,
                prefix=f"lmb_{index}", token_hash=f"{index:064d}", device_host=host,
                client="lembas 1.0.0",
            )
            db.add(token)
            db.flush()
            for title in (f"Refactor the loader on {host}", f"Release notes ({host})")[
                : 1 if host == "device-2" else 2
            ]:
                db.add(Chat(
                    user_id=owner.id, kind="agent", model_id="qwen3-coder",
                    connection_id=local.id, data_group_id="default", title=title,
                    device_token_id=token.id, project_dir=f"/home/me/{host}/project",
                ))
            if linked:
                device_link.LINKS[token.id] = device_link.Link(
                    token_id=token.id, user_id=owner.id, name=token.name,
                    send_text=None, close=None,
                    hello={"device": {"host": host},
                           "limits": {"roots": ["~/projects"], "max_mode": "edit"}},
                )
        db.add(Report(
            owner_id=owner.id, title="Weekly digest", data_group_id="default",
            summary="Three pull requests merged, one release cut, nothing failing.",
            body="# Weekly digest\n\nThree pull requests merged.",
        ))
        db.add(Schedule(
            user_id=owner.id, title="Weekly digest", data_group_id="default",
            request="Every Monday at nine, summarise last week's work.",
            instruction="Summarise last week's work.", model_id="gemma4-moe",
            rule_json={"at": {"times": ["09:00"]}},
            next_fire_at=datetime.now(UTC) + timedelta(days=2),
        ))


def rewrite(html: str, client, assets: Path) -> str:
    """Point every asset at a file on disk, and prove none was missed."""
    for route, name in ROUTE_ASSETS.items():
        response = client.get(route)
        if response.status_code == 200:
            (assets / name).write_text(response.text)

    html = re.sub(
        r'(?:http://testserver)?/static/([^"\'?\s>]+)(\?[^"\'\s>]*)?',
        lambda m: f"file://{STATIC}/{m.group(1)}",
        html,
    )
    html = re.sub(
        r'(?:http://testserver)?/branding\.css(\?[^"\'\s>]*)?',
        f"file://{assets}/branding.css",
        html,
    )

    # Anything else the *application* serves rather than mounts. Model avatars live
    # under `/uploads/models/…`, which is a route behind auth -- so they cannot be
    # pointed at a file on disk and have to be fetched through the client like
    # `/branding.css` above. A real instance has them and a fixture does not, which
    # is exactly the difference that makes a page measured here unlike the page
    # somebody is looking at.
    for url in sorted({*re.findall(r'\bsrc="(/(?:uploads|branding)/[^"?]+)"', html)}):
        response = client.get(url)
        if response.status_code != 200:
            continue
        name = "fetched-" + url.strip("/").replace("/", "-")
        (assets / name).write_bytes(response.content)
        html = html.replace(f'src="{url}"', f'src="file://{assets}/{name}"')

    # Fail loudly, and only about things that decide how the page LOOKS: every
    # `src`, and `href` on a <link>. An `href` on an anchor is a destination,
    # not an asset -- flagging those makes the guard cry wolf on every page and
    # a guard nobody believes is worse than none.
    leftovers = re.findall(r'<link\b[^>]*\bhref="([^"]+)"', html)
    leftovers += re.findall(r'\bsrc="([^"]+)"', html)
    blocking = [
        url
        for url in leftovers
        if url.startswith(("/", "http://testserver"))
        and not url.startswith(("/branding/", "/manifest", "/sw.js"))
    ]
    if blocking:
        raise SystemExit(
            "UNREWRITTEN ASSET URLS -- this would measure an unstyled document: "
            f"{sorted(set(blocking))[:8]}"
        )

    # And that what they were rewritten *to* is really there. A rewrite that
    # matches and produces a dead path is indistinguishable, from inside the
    # browser, from no stylesheet at all -- and it is the failure that actually
    # happened, twice.
    missing = [
        url
        for url in re.findall(r'(?:href|src)="file://([^"?]+)"', html)
        if not Path(url).exists()
    ]
    if missing:
        raise SystemExit(f"REWRITTEN TO NOTHING -- still an unstyled document: {missing[:5]}")

    # The one-time notifications offer is a modal over the very page we came
    # to measure, and it is gated on a localStorage key. Set it in the head, so
    # it runs before the deferred script that reads it.
    quiet = (
        "<script>try{localStorage.setItem('lembas-notifications-asked','1');}"
        "catch(e){}</script>"
    )
    measure = MEASURE.replace("__SIDEWAYS_AUTHORS__", json.dumps(list(authored_sideways())))
    # `SHOOT_OPEN=selector[,selector]` clicks each, in order, once the page has
    # loaded -- a popover or a side panel is otherwise never in a shot.
    opener = ""
    if os.environ.get("SHOOT_OPEN"):
        selectors = json.dumps(os.environ["SHOOT_OPEN"].split(","))
        opener = (
            "<script>window.addEventListener('load',function(){setTimeout(function(){"
            f"{selectors}.forEach(function(s){{var e=document.querySelector(s);"
            "if(e)e.click();});},250);});</script>"
        )
    return html.replace("</head>", quiet + measure + opener + "</head>", 1)


def shoot(client, path: str, width: int, height: int, theme: str, outdir: Path) -> dict:
    """One page, at one size, in one theme.

    The page is rendered inside an <iframe> of exactly the target size rather
    than into a window of it, because headless Chromium refuses to make a window
    narrower than about 500px -- ask for 390 and you get 500, and every
    measurement is then of a layout no phone will ever produce. A media query
    inside an iframe evaluates against the iframe's own viewport, so this is the
    real thing: `width: 390px` on the frame is a 390px viewport inside it.
    """
    response = client.get(path)
    if response.status_code != 200:
        raise SystemExit(f"{path} -> HTTP {response.status_code}")

    assets = outdir / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    # Both attributes, as app.js:applyTheme sets them: tokens.css and the
    # artwork key a light theme on `data-base` as much as on `data-theme`, so
    # swapping only the first shot custom light themes on Moria's surfaces.
    from lembas.services import branding

    base = next((t.base for t in branding.snapshot().themes if t.id == theme), "moria")
    html = response.text
    html = re.sub(r'data-theme="[^"]*"', f'data-theme="{theme}"', html, count=1)
    html = re.sub(r'data-base="[^"]*"', f'data-base="{base}"', html, count=1)
    html = rewrite(html, client, assets)

    # A query string is part of what is shot (`/settings?tab=security`) but not of
    # a file name: a `?` in a file:// URL starts its query, and the frame loaded
    # nothing and reported nothing.
    slug = f"{re.sub(r'[^A-Za-z0-9_-]+', '-', path.strip('/')) or 'root'}-{theme}-{width}x{height}"
    # `SHOOT_TAG` tells apart two runs of one page (a side, an open popover).
    if os.environ.get("SHOOT_TAG"):
        slug = f"{os.environ['SHOOT_TAG']}-{slug}"
    page = outdir / f"{slug}.html"
    page.write_text(html)

    frame = outdir / f"{slug}-frame.html"
    frame.write_text(
        "<!doctype html><meta charset=utf-8>"
        "<style>html,body{margin:0;background:#888}"
        f"iframe{{width:{width}px;height:{height}px;border:0;display:block}}</style>"
        f'<iframe id="f" src="{page.name}"></iframe>'
        "<div id=\"__measurements\"></div>"
        "<script>"
        "window.addEventListener('load',function(){setTimeout(function(){"
        "var w=document.getElementById('f').contentWindow;"
        "document.getElementById('__measurements').textContent="
        "JSON.stringify(w.__measure?w.__measure():{error:'no __measure -- the page did not load'});"
        "},600);});"
        "</script>"
    )

    shot = outdir / f"{slug}.png"
    common = [
        CHROMIUM, "--headless", "--no-sandbox", "--disable-gpu",
        "--allow-file-access-from-files", "--hide-scrollbars",
        "--force-device-scale-factor=1",
        f"--window-size={max(width, 520)},{height + 40}",
        "--virtual-time-budget=4000",
    ]
    subprocess.run(common + [f"--screenshot={shot}", f"file://{frame}"],
                   capture_output=True, timeout=120)
    dom = subprocess.run(common + ["--dump-dom", f"file://{frame}"],
                         capture_output=True, text=True, timeout=120).stdout

    match = re.search(r'id="__measurements">(.*?)</div>', dom, re.S)
    if not match or not match.group(1).strip():
        raise SystemExit(f"no measurements for {slug} -- the frame did not report")
    data = json.loads(match.group(1))
    if "error" in data:
        raise SystemExit(f"{slug}: {data['error']}")
    data["page"] = slug
    if data["innerW"] != width:
        raise SystemExit(
            f"{slug}: measured a {data['innerW']}px viewport, asked for {width}px"
        )
    return data


# The two the manifest asks for. Without them Chrome on Android falls back to
# the one-line mini-infobar instead of the install dialog with a name, an icon
# and a picture in it -- which is the difference between an install somebody
# chooses and one they dismiss without reading.
MANIFEST_SHOTS = (
    ("screenshot-narrow.png", 390, 844, "narrow"),
    ("screenshot-wide.png", 1280, 800, "wide"),
)


def manifest_screenshots(client, outdir: Path) -> None:
    """Capture the two, straight into static/img/ where the manifest names them.

    A browser capture rather than something `build_artwork.py` draws: the point
    of a screenshot is that it is what the application actually looks like, and
    an illustration of what it looks like is the one thing it must not be.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - design-time tool
        raise SystemExit("pillow is needed to crop the frame off a screenshot") from None

    for name, width, height, _form in MANIFEST_SHOTS:
        shoot(client, "/chat", width, height, "moria", outdir)
        slug = f"chat-moria-{width}x{height}.png"
        target = STATIC / "img" / name
        # Cropped to the iframe, which sits at the origin of a zero-margin
        # wrapper. The capture is of the *outer* document, so without this the
        # screenshot carries the harness's own readout along its bottom edge
        # and a strip of grey beside it -- and a manifest screenshot is the one
        # picture of this application most people will ever see.
        with Image.open(outdir / slug) as shot:
            shot.crop((0, 0, width, height)).save(target)
        print(f"wrote {target.relative_to(REPO)}")


def main() -> None:
    if not CHROMIUM:
        raise SystemExit("no chromium")

    if "--manifest-screenshots" in sys.argv:
        outdir = Path(sys.argv[1]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp())
        outdir.mkdir(parents=True, exist_ok=True)
        manifest_screenshots(build_client(), outdir)
        return
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/lembas-shoot/out")
    outdir.mkdir(parents=True, exist_ok=True)

    paths = sys.argv[2].split(",") if len(sys.argv) > 2 else ["/chat", "/settings"]
    sizes = [(390, 844), (360, 640), (1280, 800)]
    # `SHOOT_SIZES=1440x900,390x844` and `SHOOT_THEMES=moria` narrow a run to
    # what is being compared, rather than every size in every theme.
    if os.environ.get("SHOOT_SIZES"):
        sizes = [
            tuple(int(n) for n in size.split("x"))
            for size in os.environ["SHOOT_SIZES"].split(",")
        ]
    client = build_client()
    # Every theme the instance offers, custom ones included -- a custom light
    # theme is exactly the case the two-attribute swap above exists for.
    from lembas.services import branding

    themes = [t.id for t in branding.snapshot().themes]
    if os.environ.get("SHOOT_THEMES"):
        themes = [t for t in os.environ["SHOOT_THEMES"].split(",") if t in themes]
    results = []
    for path in paths:
        for width, height in sizes:
            for theme in themes:
                results.append(shoot(client, path, width, height, theme, outdir))

    (outdir / "results.json").write_text(json.dumps(results, indent=2))
    for r in results:
        flags = []
        if r["documentScrolls"]:
            flags.append(f"DOC-SCROLLS({r['docScrollH']}>{r['innerH']})")
        if r["scrollsSideways"]:
            flags.append(f"SIDEWAYS({r['docScrollW']}>{r['innerW']})")
        for s in r.get("sidewaysScrollers", []):
            widest = s["widest"]
            blame = f"<{widest['tag']}.{widest['cls']} {widest['w']}px" if widest else ""
            flags.append(
                f"SCROLLER-SIDEWAYS({s['tag']}.{s['cls']} "
                f"{s['scrollW']}>{s['clientW']}{blame})"
            )
        if r["overflowCount"]:
            flags.append(f"overflow:{r['overflowCount']}")
        if r["smallCount"]:
            flags.append(f"small-targets:{r['smallCount']}")
        print(f"{r['page']:44} {' '.join(flags) or 'clean'}")


if __name__ == "__main__":
    main()
