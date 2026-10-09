"""Prototypes: HTML a model writes, run in the reader's browser only.

Four things have to hold, and each would fail quietly:

* the reply's own HTML never carries a frame or a script -- the placeholder is
  sanitised like everything else, and the frame is built in the browser;
* the route serves only a block that is really in that message, to its owner,
  under a policy that makes it an opaque origin with no network;
* the renderer and the route agree on which block is which, including while
  the reply is still being written;
* the model is told about prototypes exactly when the reader can see them.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from lembas.services import markdown, prototypes, settings_store

ROOT = Path(__file__).resolve().parent.parent / "src/lembas"
JS = (ROOT / "web/static/js/prototypes.js").read_text()

PAGE = "<!doctype html><title>Bakery</title><h1>Bread</h1><script>document.title='x'</script>\n"
CHART = "<svg width='100' height='50'><rect width='10' height='40'/></svg>\n"


def reply(*blocks: tuple[str, str]) -> str:
    parts = ["Here it is."]
    for info, code in blocks:
        parts.append(f"```{info}\n{code}```")
    parts.append("That is all.")
    return "\n\n".join(parts)


# --- Rendering -------------------------------------------------------------------
def test_a_prototype_fence_becomes_a_placeholder_and_nothing_that_runs():
    html = markdown.render_markdown(reply(("prototype-page Bakery landing", PAGE)))
    assert 'class="proto proto--page"' in html
    assert f'data-proto-hash="{prototypes.digest(PAGE)}"' in html
    assert 'data-proto-title="Bakery landing"' in html
    # The model's markup appears once, escaped, as source to read.
    assert "<script" not in html
    assert "<iframe" not in html
    assert "&lt;script&gt;" in html or "&lt;</span><span class=\"pg-nt\">script" in html
    assert '<details class="proto__source">' in html


def test_the_title_falls_back_to_the_pages_own():
    html = markdown.render_markdown(reply(("prototype-page", PAGE)))
    assert 'data-proto-title="Bakery"' in html


def test_an_inline_one_is_the_chat_kind_and_so_is_a_bare_prototype():
    for info in ("prototype-chat Sales", "prototype"):
        html = markdown.render_markdown(reply((info, CHART)))
        assert 'data-proto="chat"' in html, info


def test_an_ordinary_html_fence_is_still_a_code_block():
    html = markdown.render_markdown(reply(("html", PAGE)))
    assert "data-proto" not in html
    assert 'class="code-block"' in html


def test_a_block_still_being_written_has_no_digest():
    """Its contents change with every token; a frame mounted on it would reload
    a dozen times a second, and the route would serve half a page."""
    text = "Working on it.\n\n```prototype-page Draft\n<h1>Half\n<p>a page\n"
    html = markdown.render_markdown(text)
    assert "proto--writing" in html
    assert "data-proto-hash" not in html
    assert 'data-proto-lines="2"' in html
    assert prototypes.find(text, prototypes.digest("<h1>Half\n<p>a page\n")) is None


def test_a_closed_block_inside_a_list_is_found_too():
    text = "1. First\n\n   ```prototype-chat\n   <b>x</b>\n   ```\n"
    tokens = [token for token, closed in markdown.fences(text) if closed]
    assert len(tokens) == 1
    assert prototypes.find(text, prototypes.digest(tokens[0].content)) is not None


def test_a_hand_written_placeholder_keeps_only_harmless_attributes():
    html = markdown.render_markdown(
        '<div data-proto="page" data-proto-hash="../../admin" onclick="x()">a</div>'
    )
    assert "onclick" not in html
    # Kept, and refused in the browser instead: the digest is checked against
    # a 16-hex pattern before it becomes part of an address.
    assert 'data-proto-hash="../../admin"' in html
    assert "var DIGEST = /^[0-9a-f]{16}$/;" in JS


# --- Finding ----------------------------------------------------------------------
def test_the_digest_finds_its_own_block_and_no_other():
    text = reply(("prototype-chat One", CHART), ("prototype-page Two", PAGE))
    first = prototypes.find(text, prototypes.digest(CHART))
    second = prototypes.find(text, prototypes.digest(PAGE))
    assert (first.kind, first.title, first.code) == ("chat", "One", CHART)
    assert (second.kind, second.title) == ("page", "Two")
    assert prototypes.find(text, "0" * 16) is None
    assert prototypes.find(text, "not-a-digest") is None


def test_the_renderer_and_the_finder_agree_on_every_digest():
    text = reply(("prototype-chat", CHART), ("prototype-page", PAGE))
    rendered = re.findall(r'data-proto-hash="([0-9a-f]{16})"', markdown.render_markdown(text))
    assert len(rendered) == 2
    for digest in rendered:
        assert prototypes.find(text, digest) is not None


# --- The document and its policy ----------------------------------------------------
def test_the_policy_seals_the_page_off():
    policy = prototypes.policy(allow_network=False)
    directives = dict(part.split(" ", 1) for part in policy.split("; "))
    assert directives["sandbox"] == "allow-scripts allow-forms"
    assert "allow-same-origin" not in policy
    assert directives["connect-src"] == "'none'"
    assert directives["default-src"] == "'none'"
    assert directives["form-action"] == "'none'"
    assert directives["frame-ancestors"] == "'self'"
    assert "https:" not in policy


def test_the_network_setting_opens_https_for_loading_and_nothing_else():
    policy = prototypes.policy(allow_network=True)
    directives = dict(part.split(" ", 1) for part in policy.split("; "))
    for name in ("script-src", "style-src", "img-src", "font-src", "connect-src"):
        assert "https:" in directives[name], name
    for name in ("frame-src", "form-action", "base-uri", "object-src"):
        assert "https:" not in directives[name], name
    assert "allow-same-origin" not in directives["sandbox"]


def test_the_boot_script_runs_before_the_models_page():
    page = prototypes.document(PAGE, scheme="dark")
    assert page.index("lembasPrototype") < page.index("<title>Bakery")
    assert '<meta name="color-scheme" content="dark">' in page
    assert page.endswith(PAGE)
    # An unknown scheme is simply not stated.
    assert "color-scheme" not in prototypes.document(PAGE, scheme="purple")


def test_a_refusal_escapes_its_message():
    assert "<b>" not in prototypes.refusal("<b>no</b>")


def test_a_download_is_named_after_its_title():
    assert prototypes.filename_for("Bakery landing page!") == "bakery-landing-page.html"
    assert prototypes.filename_for("") == "prototype.html"


# --- The route ----------------------------------------------------------------------
@pytest.fixture
def stored(db, registered, make_chat):
    """A chat whose reply holds one prototype of each kind -- on a real model,
    because a chat page with no model to answer renders no transcript."""
    from lembas.db.models import Connection, Message, Model

    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    db.commit()
    chat_id = make_chat(model_id="m")
    message = Message(
        chat_id=chat_id,
        role="assistant",
        content=reply(("prototype-chat Sales", CHART), ("prototype-page Bakery", PAGE)),
    )
    db.add(message)
    db.commit()
    return SimpleNamespace(chat=chat_id, message=message.id)


def _address(stored, code: str) -> str:
    return (
        f"/api/chats/{stored.chat}/messages/{stored.message}/prototypes/"
        f"{prototypes.digest(code)}"
    )


def test_the_route_serves_the_page_sealed(client, stored):
    response = client.get(_address(stored, PAGE) + "?scheme=light")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    policy = response.headers["content-security-policy"]
    assert policy.startswith("sandbox allow-scripts allow-forms;")
    assert "allow-same-origin" not in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.text.endswith(PAGE)
    assert '<meta name="color-scheme" content="light">' in response.text


def test_a_digest_that_is_not_in_the_message_is_not_served(client, stored):
    response = client.get(
        f"/api/chats/{stored.chat}/messages/{stored.message}/prototypes/{'a' * 16}"
    )
    assert response.status_code == 404
    # Refusals are sealed too: there is no answer from here a browser runs as us.
    assert "sandbox" in response.headers["content-security-policy"]


def test_somebody_elses_chat_is_not_served(client, stored):
    client.post("/auth/logout")
    client.post(
        "/auth/register",
        data={"name": "Sam", "email": "sam@shire.test", "password": "po-tay-toes-boil"},
    )
    assert client.get(_address(stored, PAGE)).status_code == 404


def test_switching_prototypes_off_stops_serving_them(client, db, stored):
    settings_store.update(db, {"prototypes_enabled": False})
    response = client.get(_address(stored, PAGE))
    assert response.status_code == 403
    assert "turned off" in response.text


def test_the_network_setting_reaches_the_header(client, db, stored):
    settings_store.update(db, {"prototypes_network": True})
    assert "https:" in client.get(_address(stored, CHART)).headers["content-security-policy"]


def test_a_block_that_closed_mid_reply_is_served_from_the_running_reply(client, db, stored):
    """While a reply streams, the row is empty and the reply's buffer holds the
    text -- and an inline chart closed three paragraphs ago should show now."""
    from lembas.db.models import Message
    from lembas.services import generation

    message = db.get(Message, stored.message)
    message.content = ""
    message.complete = False
    db.commit()
    generation._RUNNING[stored.message] = SimpleNamespace(
        text=reply(("prototype-chat Live", CHART)) + "\n\nStill writing"
    )
    response = client.get(_address(stored, CHART))
    assert response.status_code == 200
    assert response.text.endswith(CHART)


def test_the_download_is_the_raw_page(client, stored):
    response = client.get(_address(stored, PAGE) + "/download")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="bakery.html"'
    assert response.text == PAGE
    assert "lembasPrototype" not in response.text


# --- The page ----------------------------------------------------------------------------
def test_a_message_carries_what_the_frame_address_is_built_from(client, stored):
    page = client.get(f"/chat/{stored.chat}").text
    assert f'data-chat="{stored.chat}" data-message="{stored.message}"' in page
    assert 'data-proto="chat"' in page


def test_the_chat_page_has_the_panel_and_the_window_switch(client, stored):
    page = client.get(f"/chat/{stored.chat}").text
    assert 'id="prototype"' in page
    assert 'data-resize="--prototype-width" data-resize-min="384"' in page
    assert 'class="winswitch"' in page
    assert 'data-window="#prototype"' in page
    assert '<template id="proto-ui">' in page


def test_no_panel_when_prototypes_are_off(client, db, stored):
    settings_store.update(db, {"prototypes_enabled": False})
    page = client.get(f"/chat/{stored.chat}").text
    assert 'id="prototype"' not in page
    assert 'data-window="#prototype"' not in page


def test_the_toggle_menu_offers_the_switch(client, stored):
    page = client.get(f"/chat/{stored.chat}").text
    assert '"name": "prototypes"' in page


def test_the_administrator_can_switch_both_settings(client, db, registered):
    response = client.post(
        "/admin/general",
        data={"compact_threshold": "95", "max_chat_rounds": "0", "prototypes_network": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.expire_all()
    assert settings_store.get(db, "prototypes_enabled") is False
    assert settings_store.get(db, "prototypes_network") is True


# --- What the model is told ---------------------------------------------------------------
def _harness(db, chat=None):
    from sqlalchemy import select

    from lembas.db.models import User
    from lembas.services import harness

    user = db.scalars(select(User)).first()
    return harness.compose(db, user, [], chat)


def test_the_model_is_told_when_the_reader_can_see_them(db, registered):
    text = _harness(db)
    assert "prototype-chat" in text and "prototype-page" in text
    assert "Nothing can be loaded from the internet" in text


def test_the_guidance_follows_the_network_setting(db, registered):
    settings_store.update(db, {"prototypes_network": True})
    assert "over https" in _harness(db)


def test_nothing_is_said_when_they_are_off(db, registered):
    settings_store.update(db, {"prototypes_enabled": False})
    assert "prototype-chat" not in _harness(db)


def test_a_chat_that_switched_them_off_is_told_nothing(db, registered, make_chat):
    from lembas.db.models import Chat

    chat = db.get(Chat, make_chat())
    chat.scope_json = {"families": {"prototypes": False}}
    db.commit()
    assert "prototype-chat" not in _harness(db, chat)


def test_a_helper_is_never_offered_them(db, registered, make_chat):
    from lembas.db.models import Chat

    parent = make_chat()
    child = db.get(Chat, make_chat())
    child.parent_chat_id = parent
    db.commit()
    assert "prototype-chat" not in _harness(db, child)


# --- The browser half ---------------------------------------------------------------------
def test_the_frame_is_never_given_the_same_origin():
    assert 'setAttribute("sandbox", "allow-scripts allow-forms")' in JS
    assert "allow-same-origin" not in JS.replace("No allow-same-origin", "")


def test_the_script_never_writes_markup():
    assert "innerHTML" not in JS.replace("Never innerHTML", "")
    assert "outerHTML" not in JS


def test_only_the_frames_own_window_is_listened_to():
    assert "frames[i].contentWindow === source" in JS
    assert "if (!source) return;" in JS


def test_the_permission_exists_and_is_granted_by_default():
    from lembas.security.permissions import PERMISSION_DEFS

    found = [p for p in PERMISSION_DEFS if p.key == prototypes.PERMISSION]
    assert found and found[0].default is True
