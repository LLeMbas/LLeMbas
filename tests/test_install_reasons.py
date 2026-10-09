"""Why the Install button is not there, said out loud.

Four different things make it absent and all four look identical from the
settings page: the button is simply not rendered. The hint beside it used to read
"only offered over HTTPS or on localhost", which is true of one case and useless
for the other three — and the case it does not name is the commonest on a home
network, where a certificate signed by your own CA leaves the page outside a
secure context and the service worker is refused. A browser that cannot install
and a certificate a phone does not trust produced exactly the same silence.

There is no JavaScript runtime here (hard rule 1 keeps Node out of the project),
so what is pinned is the shape: the outcome is recorded rather than swallowed,
every state has its own sentence, and the sentence names the cause that is
actually likely.
"""

from __future__ import annotations

import re
from pathlib import Path

import lembas
from tests.conftest import js_code, js_function, js_says

ROOT = Path(lembas.__file__).parent
APP_JS = (ROOT / "web/static/js/app.js").read_text(encoding="utf-8")
BASE = (ROOT / "web/templates/base.html").read_text(encoding="utf-8")
SETTINGS = (ROOT / "web/templates/settings.html").read_text(encoding="utf-8")


def _inline_scripts(html: str) -> str:
    """The inline scripts with their comments stripped.

    `js_code` is what strips them, and it is needed: the first draft of the test
    below asserted that nothing throws and failed on the word "throw" inside a
    comment explaining why nothing does.
    """
    return js_code("\n".join(re.findall(r"<script>(.*?)</script>", html, re.S)))


def _words(text: str) -> str:
    """Whitespace collapsed, so an assertion survives a line wrap in a template."""
    return " ".join(text.split())


# --- The outcome is kept ------------------------------------------------------
def test_the_registration_outcome_is_recorded_rather_than_swallowed():
    """`\
.catch(function () {})` is what this replaced. It kept the page working, which
    was the point, and threw away the only evidence of why installing was
    impossible."""
    scripts = _inline_scripts(BASE)
    assert "navigator.serviceWorker.register(" in scripts
    assert "window.lembasWorker" in scripts
    assert js_says(scripts, "register(", 'state: "ready"')
    assert js_says(scripts, "register(", 'state: "failed"', "reason:")


def test_an_insecure_context_is_reported_separately_from_a_failure():
    """The fix differs: one is "serve it over TLS", the other is "trust this
    certificate on this device". A single "cannot install" covers neither."""
    scripts = _inline_scripts(BASE)
    assert js_says(scripts, "isSecureContext", 'state: "insecure"')


def test_success_and_failure_are_separate_callbacks():
    """`.then(ok).catch(fail)` would report a throw inside the success path as a
    registration failure, which is a sentence about the wrong thing."""
    scripts = _inline_scripts(BASE)
    assert ".catch(" not in scripts.split("register(", 1)[1]


def test_the_page_still_cannot_be_broken_by_a_failed_registration():
    """The property the swallowed catch was there for, kept: nothing rethrows."""
    scripts = _inline_scripts(BASE)
    assert "throw" not in scripts


# --- Every state has its own sentence -----------------------------------------
def test_each_reason_gets_its_own_explanation():
    body = js_function(APP_JS, "installExplanation")
    for state in ("insecure", "failed", "unsupported", "ready"):
        assert f'"{state}"' in body, f"no sentence for the {state} state"


def test_the_certificate_is_named_because_it_is_the_likely_cause():
    """The whole reason this exists. A private or self-signed certificate is the
    normal way a self-hosted instance on a LAN ends up un-installable, and it was
    the one cause the old hint did not mention."""
    body = js_function(APP_JS, "installExplanation")
    assert "certificate" in body
    assert "trust" in body


def test_the_browsers_own_words_are_included_and_escaped():
    """A browser is not a hostile source, but it is not ours either, and the
    message is arbitrary text going onto a page."""
    body = js_function(APP_JS, "installExplanation")
    assert "worker.reason" in body
    written = js_function(APP_JS, "describeInstall")
    assert "textContent" in written
    assert "innerHTML" not in written


def test_nothing_is_said_when_the_button_is_there():
    """An explanation beside a working button is noise, and a wrong one — "your
    browser has not offered an install" next to the offer — is worse."""
    body = js_function(APP_JS, "installExplanation")
    assert js_says(body, "if (installPrompt) return")


def test_an_installed_app_says_so_rather_than_explaining_itself():
    body = js_function(APP_JS, "installExplanation")
    assert js_says(body, "display-mode: standalone", "Already installed")


# --- It reaches the page ------------------------------------------------------
def test_the_settings_page_has_somewhere_to_put_it():
    assert "data-install-status" in SETTINGS


def test_the_explanation_is_refreshed_on_every_path_that_changes_it():
    """Four: the worker answering, the browser offering, the app being installed,
    and the page having loaded after the worker already answered. The last is the
    one that is easy to miss — the event has been and gone by then."""
    assert APP_JS.count("describeInstall()") >= 4
    assert 'document.addEventListener("lembas:worker", describeInstall)' in APP_JS


def test_the_static_hint_no_longer_claims_https_is_enough():
    """It said "only offered over HTTPS or on localhost". HTTPS with a
    certificate nothing trusts is HTTPS, and it does not install."""
    assert "Only offered over HTTPS" not in _words(SETTINGS)
    assert "certificate this device trusts" in _words(SETTINGS)
