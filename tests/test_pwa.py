"""Installing as an app, and the composer's single send/stop button."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from lembas.services import settings_store
from lembas.web.templating import STATIC_DIR


# --- Manifest ----------------------------------------------------------------
def test_the_manifest_is_readable_when_signed_out(client: TestClient):
    """A browser fetches the manifest outside any page's session."""
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")


def test_the_manifest_carries_the_instance_name(client: TestClient, db, registered):
    """Set on Admin → General."""
    client.post(
        "/admin/general",
        data={"instance_name": "Rivendell", "language": "en"},
        follow_redirects=False,
    )
    assert client.get("/manifest.webmanifest").json()["name"] == "Rivendell"


def test_the_manifest_offers_a_maskable_icon(client: TestClient):
    """Without one, Android crops the corners off the wafer."""
    icons = client.get("/manifest.webmanifest").json()["icons"]
    assert any(icon["purpose"] == "maskable" for icon in icons)
    assert any(icon["sizes"] == "512x512" and icon["purpose"] == "any" for icon in icons)


def test_every_manifest_icon_exists(client: TestClient):
    for icon in client.get("/manifest.webmanifest").json()["icons"]:
        assert client.get(icon["src"]).status_code == 200, icon["src"]


def test_the_manifest_starts_at_the_chat(client: TestClient):
    payload = client.get("/manifest.webmanifest").json()
    assert payload["start_url"] == "/chat"
    assert payload["scope"] == "/"
    assert payload["display"] == "standalone"


# --- Service worker ----------------------------------------------------------
def test_the_worker_is_served_from_the_root(client: TestClient):
    """A worker under /static/js/ would have scope /static/js/ and control
    nothing."""
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")


def test_the_worker_is_never_cached(client: TestClient):
    """A stale worker keeps serving a stale cache."""
    assert "no-store" in client.get("/sw.js").headers["cache-control"]


def test_the_worker_leaves_the_api_alone():
    """The reply stream, the unread poll and attachment downloads all live
    under /api/. A cached response on any of them is at best stale."""
    source = (STATIC_DIR / "js" / "sw.js").read_text()
    assert '"/api/"' in source
    assert "text/event-stream" in source


def test_every_precached_asset_exists(client: TestClient):
    """addAll is all-or-nothing in most implementations, and a missing entry is
    invisible until someone opens the developer tools."""
    source = (STATIC_DIR / "js" / "sw.js").read_text()
    shell = source.split("var SHELL = [", 1)[1].split("];", 1)[0]
    paths = [line.strip().strip('",') for line in shell.splitlines() if '"' in line]

    assert paths
    for path in paths:
        assert client.get(path).status_code == 200, path


def test_the_offline_page_stands_on_its_own(client: TestClient):
    """Cached at install time, so it must render with no user and no chats."""
    response = client.get("/offline")
    assert response.status_code == 200
    assert 'class="sidebar' not in response.text
    assert 'id="thread' not in response.text


def test_the_page_links_the_manifest_and_the_apple_icon(client: TestClient, registered):
    page = client.get("/chat").text
    assert 'rel="manifest"' in page
    assert 'rel="apple-touch-icon"' in page
    assert 'name="theme-color"' in page


# --- Send and Stop -----------------------------------------------------------
def test_the_hidden_attribute_wins_over_component_styles():
    """`.btn` is display: inline-flex, which beats the browser's own
    `[hidden] { display: none }`. Without this rule a button hidden from
    JavaScript stays on screen -- which is how Stop came to sit permanently
    beside Send."""
    css = (STATIC_DIR / "css" / "app.css").read_text()
    assert "[hidden]" in css
    assert "display: none !important" in css


def test_the_composer_has_exactly_one_send_button(client: TestClient, db, registered):
    """One button that becomes Stop, not two that take turns being hidden."""
    _add_a_model(db)
    page = client.get("/chat").text
    assert page.count("data-composer-action") == 1


def test_the_send_button_carries_both_icons(client: TestClient, db, registered):
    """Rendered together and chosen in CSS, so the swap costs no layout and
    cannot flash an empty button."""
    _add_a_model(db)
    page = client.get("/chat").text
    assert "composer__icon--send" in page
    assert "composer__icon--stop" in page


def test_the_composer_starts_in_the_send_state(client: TestClient, db, registered):
    _add_a_model(db)
    assert 'data-composer-action="send"' in client.get("/chat").text


def _add_a_model(db):
    from lembas.db.models import Connection, Model

    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m"))
    db.commit()


# --- The icons themselves ----------------------------------------------------
def test_the_generated_icons_are_committed():
    """They come from scripts/build_artwork.py and are committed like the SVGs;
    the running application has no rasteriser."""
    for name in (
        "icon-192.png",
        "icon-512.png",
        "icon-maskable-512.png",
        "apple-touch-icon-180.png",
    ):
        path = Path(STATIC_DIR) / "img" / name
        assert path.exists(), name
        assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", name


def test_the_mic_appears_only_when_dictation_is_configured(
    client: TestClient, db, registered
):
    _add_a_model(db)
    assert "data-mic" not in client.get("/chat").text

    settings_store.update(
        db,
        {"stt_enabled": True, "stt_base_url": "http://stt"},
        key=settings_store.AUDIO,
    )
    assert "data-mic" in client.get("/chat").text


# --- The manifest, beyond the installability minimum -------------------------
def test_the_manifest_offers_launcher_shortcuts(client: TestClient):
    """A long-press on the launcher icon should reach the three places worth
    going to directly. Absent, it offers nothing. (Reports, where scheduled
    results land, is in the Library, and Scheduled is in Settings.)"""
    payload = client.get("/manifest.webmanifest").json()
    urls = {s["url"] for s in payload["shortcuts"]}
    assert urls == {"/chat", "/settings?tab=scheduled"}


def test_the_manifest_identity_matches_where_it_starts(client: TestClient):
    """`id` was "/", which serves nothing but a redirect, while the app started
    at /chat. Legal, and it reads as a mistake to anyone comparing the two."""
    payload = client.get("/manifest.webmanifest").json()
    assert payload["id"] == payload["start_url"]


def test_the_manifest_declares_the_rest_of_the_quality_set(client: TestClient):
    payload = client.get("/manifest.webmanifest").json()
    for key in ("orientation", "categories", "lang", "dir",
                "display_override", "launch_handler"):
        assert key in payload, key


def test_the_splash_follows_the_instance_theme(client: TestClient, monkeypatch):
    """It was Moria's near-black whatever the instance was set up in, so a
    parchment instance installed to a phone flashed dark and opened light --
    and `THEME_COLOUR["shire"]` sat beside it, defined and read by nothing."""
    from lembas.config import settings

    monkeypatch.setattr(settings, "default_theme", "shire")
    payload = client.get("/manifest.webmanifest").json()
    assert payload["theme_color"] == "#F1EAD6"
    assert payload["background_color"] == payload["theme_color"]


def test_the_page_paints_the_right_chrome_before_any_script_runs(client, registered):
    """One unscoped `theme-color` meant a light-theme reader got dark browser
    chrome on every load until the deferred script corrected it."""
    page = client.get("/chat").text
    assert 'media="(prefers-color-scheme: dark)"' in page
    assert 'media="(prefers-color-scheme: light)"' in page


# --- The worker --------------------------------------------------------------
def test_the_worker_does_not_take_over_a_page_being_read():
    """It called skipWaiting() unconditionally, so a release replaced the
    assets under an open tab mid-session. It waits to be asked now."""
    import re

    source = (STATIC_DIR / "js" / "sw.js").read_text()
    # Comments stripped first: the install handler explains at length that it
    # deliberately does not call this, and a test that reads prose would fail
    # on the explanation for the fix.
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    install = code.split('addEventListener("install"', 1)[1].split("addEventListener(", 1)[0]
    assert "skipWaiting" not in install
    assert 'event.data.type === "SKIP_WAITING"' in code


def test_the_worker_survives_a_rotated_subscription():
    """A browser replacing a subscription on its own is the normal way push
    stops working, and nothing anywhere said so."""
    source = (STATIC_DIR / "js" / "sw.js").read_text()
    assert "pushsubscriptionchange" in source


def test_the_badge_is_not_the_full_colour_icon():
    """A badge is drawn as a mask -- the device keeps the alpha and throws the
    colour away -- so an icon opaque to its edges renders as a grey square."""
    source = (STATIC_DIR / "js" / "sw.js").read_text()
    assert 'badge: "/static/img/badge-72.png"' in source
    badge = Path(STATIC_DIR) / "img" / "badge-72.png"
    assert badge.exists() and badge.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_two_icons_a_device_crops_are_cached():
    source = (STATIC_DIR / "js" / "sw.js").read_text()
    shell = source.split("var SHELL = [", 1)[1].split("];", 1)[0]
    assert "icon-maskable-512.png" in shell
    assert "apple-touch-icon-180.png" in shell


def test_every_script_the_base_template_loads_is_in_the_shell():
    """steps.js and canvas.js were loaded by every page and precached by
    nobody, so the installed app drew a chat with its blocks forgetting their
    state the first time the network was away."""
    import re

    base = (Path(STATIC_DIR).parent / "templates" / "base.html").read_text()
    loaded = set(re.findall(r"asset\('js/([a-z_]+\.js)'", base))
    assert loaded, "base.html stopped loading any script; this test is now blind"
    source = (STATIC_DIR / "js" / "sw.js").read_text()
    shell = source.split("var SHELL = [", 1)[1].split("];", 1)[0]
    missing = sorted(name for name in loaded if f"/static/js/{name}" not in shell)
    assert not missing, f"loaded by base.html but not precached: {missing}"


def test_the_first_paint_colours_come_from_the_tokens(client, registered):
    """The two `theme-color` metas used to repeat the hex from tokens.css by
    hand, which is a value that drifts. They read it out of the file now."""
    import re

    from lembas.web.templating import theme_colour

    tokens = (STATIC_DIR / "css" / "tokens.css").read_text()
    moria = re.search(r'\[data-theme="moria"\][^{]*\{[^}]*?--bg:\s*(#[0-9A-Fa-f]+);', tokens)
    shire = re.search(r'\[data-theme="shire"\][^{]*\{[^}]*?--bg:\s*(#[0-9A-Fa-f]+);', tokens)
    assert moria and shire
    assert theme_colour("moria") == moria.group(1)
    assert theme_colour("shire") == shire.group(1)
    page = client.get("/chat").text
    assert f'content="{moria.group(1)}" media="(prefers-color-scheme: dark)"' in page
    assert f'content="{shire.group(1)}" media="(prefers-color-scheme: light)"' in page


# --- The window's own edges --------------------------------------------------
def test_the_page_asks_for_the_whole_screen_and_then_pays_for_it(client, registered):
    """`viewport-fit=cover` is what makes `env(safe-area-inset-*)` resolve to
    anything but zero, and `black-translucent` below it is what puts the page
    under the status bar in the first place. One without the other is a topbar
    beneath the clock."""
    page = client.get("/chat").text
    assert "viewport-fit=cover" in page

    css = (STATIC_DIR / "css" / "tokens.css").read_text()
    assert "safe-area-inset-top" in css
    app = (STATIC_DIR / "css" / "app.css").read_text()
    assert "var(--safe-top)" in app
    assert "var(--safe-bottom)" in app


# --- A release cannot be drawn with the previous release's stylesheet --------
def test_every_static_asset_carries_the_release(client: TestClient, registered):
    """The bug this is here to stop.

    The worker caches `/static/...` under a cache named for the release, and a
    page is fetched network-first while its assets come from that cache -- so
    since the worker does not claim open tabs the instant it installs (it must
    not, or it swaps stylesheets under somebody mid-reply), new HTML and old CSS
    can be served together. What that looks like is a close button meant for a
    phone drawer appearing, unstyled, on every desktop.

    A version in the URL settles it: the new HTML asks for something the old
    cache has never heard of.
    """
    import re

    for path in ("/chat", "/settings"):
        page = client.get(path).text
        bare = re.findall(r'(?:href|src)="(/static/[^"?]+)"', page)
        assert not bare, f"{path} loads unversioned assets: {bare[:5]}"


def test_no_template_reaches_past_the_helper(client: TestClient):
    """`url_for('static', ...)` produces a URL with no version in it, so one
    left behind is one asset that can still come from the wrong release."""
    from pathlib import Path

    import lembas

    root = Path(lembas.__file__).parent / "web/templates"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.html")
        if "url_for('static'" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"still using url_for for static assets: {offenders}"


def test_the_worker_precaches_what_a_page_will_ask_for():
    """`caches.match` compares the whole URL. Precaching the bare path fills the
    cache with entries nothing requests, and every asset then goes to the
    network on every load while looking perfectly cached."""
    source = (STATIC_DIR / "js" / "sw.js").read_text()
    assert 'path + "?v=" + VERSION' in source
    assert "versioned(path)" in source


def test_the_update_offer_asks_whether_this_page_is_older(client: TestClient, registered):
    """After a release the toast offered a reload on every page, including one
    just fetched with Ctrl+Shift+R, and reloading never made it go away. It
    fired whenever a worker was waiting. But a page loaded after the update
    already IS the update: it comes from the network, and every asset it names
    carries `?v=`. The worker that waits is nearly always the previous one,
    still holding the tab, because a reload never lets it run out of pages.

    So the offer, and the automatic reload when another tab accepts it, compare
    the worker's release with the page's own. Driven under Node against stubs
    before committing: the older check offered a reload to a current page and to a page
    that another tab's Reload had just made current. This does neither, and
    still offers it to a page from an older release.
    """
    import re

    import lembas

    page = client.get("/chat").text
    assert f"window.lembasRelease = \"{lembas.__version__}\";" in page

    source = (STATIC_DIR / "js" / "app.js").read_text()
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    start = code.index("function watchForUpdate")
    block = code[start : code.index("navigator.serviceWorker.ready.then(watchForUpdate)")]
    # Both ways a waiting worker is found, and the controllerchange reload.
    assert block.count("isNewerThanThisPage(") == 3
    assert 'searchParams.get("v")' in code
