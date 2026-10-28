"""Save in place: a settings form answered with its page, not a reload.

See web/in_place.py. The browser half (js/inplace.js) has no runner here; what
it relies on from the markup is pinned at the bottom.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import lembas

ROOT = Path(lembas.__file__).parent
IN_PLACE = {"HX-Request": "true", "LMB-In-Place": "1"}


def _post(client, path, data, current):
    return client.post(
        path,
        data=data,
        headers={**IN_PLACE, "HX-Current-URL": f"http://testserver{current}"},
        follow_redirects=False,
    )


def test_a_plain_post_is_still_a_redirect(client, registered):
    response = client.post(
        "/admin/general", data={"instance_name": "Rivendell"}, follow_redirects=False
    )
    assert response.status_code == 303


def test_a_redirect_back_is_answered_with_the_page(client, registered):
    response = _post(client, "/admin/general", {"instance_name": "Rivendell"}, "/admin/general")
    assert response.status_code == 200
    assert "data-in-place-root" in response.text
    # The saved state is the one rendered.
    assert 'value="Rivendell"' in response.text
    trigger = json.loads(response.headers["hx-trigger"])
    assert trigger["lembas:in-place"] == {"saved": "1"}
    # And the address bar loses the one-off word.
    assert response.headers["hx-replace-url"] == "/admin/general"


def test_a_redirect_elsewhere_becomes_a_navigation(client, registered):
    response = _post(client, "/admin/general", {"instance_name": "Bree"}, "/admin/audio")
    assert response.status_code == 200
    assert response.headers["hx-redirect"].startswith("/admin/general")
    assert response.text == ""


def test_without_the_header_htmx_requests_are_untouched(client, registered):
    response = client.post(
        "/admin/general",
        data={"instance_name": "Bree"},
        headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/admin/general"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_other_query_parameters_are_kept(client, registered):
    """A filtered, paginated list stays filtered and on its page."""
    from lembas.web.in_place import _local

    assert _local("/admin/models?filter=on&page=2&saved=1", "testserver") == (
        "/admin/models",
        "filter=on&page=2&saved=1",
    )
    assert _local("https://elsewhere.test/admin", "testserver") is None
    assert _local("javascript:alert(1)", "testserver") is None


def test_cookies_the_post_set_reach_the_page_it_renders():
    from lembas.web.in_place import _cookie_header

    merged = _cookie_header("a=1; b=2", ["b=3; Path=/", "c=4; Path=/", "a=; Max-Age=0"])
    assert dict(part.split("=") for part in merged.split("; ")) == {"b": "3", "c": "4"}


def test_a_settings_save_stays_on_settings(client, registered):
    response = _post(
        client,
        "/api/preferences/timezone",
        {"timezone": "Europe/Bratislava"},
        "/settings?tab=appearance",
    )
    assert response.status_code == 200, response.headers
    assert "data-in-place-root" in response.text
    assert "lembas:in-place" in response.headers["hx-trigger"]


# --- The markup the script relies on ---------------------------------------------


def test_every_layout_with_settings_forms_has_one_root():
    for name in ("admin/_layout.html", "settings.html", "schedules/_layout.html"):
        source = (ROOT / "web/templates" / name).read_text(encoding="utf-8")
        assert len(re.findall(r"<[a-z]+ [^>]*data-in-place-root", source)) == 1, name


def test_one_off_alerts_are_marked_to_become_toasts():
    """An alert rendered from `?saved=` that is not marked stays at the top of a
    page the person is not looking at -- the very thing this replaces."""
    theirs = {"models.html", "model_detail.html", "connections.html"}
    for path in (ROOT / "web/templates/admin").glob("*.html"):
        if path.name in theirs:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\{% if saved %\}\s*\n\s*(<div[^>]*>)", text):
            assert "data-flash" in match.group(1), path.name


def test_the_script_is_loaded_once_and_precached():
    base = (ROOT / "web/templates/base.html").read_text(encoding="utf-8")
    assert base.count("js/inplace.js") == 1
    worker = (ROOT / "web/static/js/sw.js").read_text(encoding="utf-8")
    assert "/static/js/inplace.js" in worker


def test_the_script_never_swaps_the_root_for_nothing():
    """An empty answer swapped with outerHTML deletes the page's content."""
    source = (ROOT / "web/static/js/inplace.js").read_text(encoding="utf-8")
    block = source[source.index("htmx:beforeSwap") :]
    assert "if (!hasRoot)" in block
    assert "shouldSwap = false" in block


def test_a_confirmed_form_goes_through_htmx():
    """`form.submit()` skips the `submit` event, so a confirmed in-place form
    would post the old way and reload the page."""
    source = (ROOT / "web/static/js/ui.js").read_text(encoding="utf-8")
    start = source.index("Plain forms opt in with data-confirm")
    block = source[start : start + 1500]
    assert "requestSubmit" in block
    assert "stopPropagation" in block
