"""HTML pages in answers (harness spec 3.8.0, `pages.md`): the three switches,
what the model is told, what a reader is shown, and the document a frame is
given.

The fence and the projection are the spec's cases (`conformance/pages.json`,
`tests/test_harness_spec.py`). That a page reaches nothing and sends nothing
is proved in a browser (`tests/test_html_pages_sealed.py`). Here is everything
between: when pages are offered, the mount the renderer writes, the route, and
the rebuilding of a page into something static.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import KIND_AGENT, KIND_TASK, Chat, Message, Model, User
from lembas.security import sealed
from lembas.services import html_pages, settings_store
from lembas.services.markdown import pages_in, render_markdown, speakable_text
from tests.test_prefix import Session, _chat, _last_user, _system
from tests.test_schedules_ui import _as_stranger

PAGE = "<!doctype html>\n<title>Inner</title>\n<p>10 wrapped, 2 not</p>"
REPLY = f"Here is the count.\n\n```html-view Cakes\n{PAGE}\n```\n\nTwo are unwrapped."


def _on(db) -> None:
    settings_store.update(db, {"html_pages_enabled": True})
    for model in db.scalars(select(Model)):
        model.html_pages = True
    db.commit()


# --- When pages are offered ------------------------------------------------------------
def test_one_switch_is_enough_and_the_other_two_are_ways_out(db, user_id):
    """The instance's switch alone gives a chat pages: a model nobody ticked,
    a chat nobody touched. The model's flag and the chat's switch narrow."""
    chat = _chat(db, user_id)
    model = db.scalars(select(Model)).one()
    assert not model.html_pages and html_pages.SCOPE_KEY not in (chat.scope_json or {})
    # Off by default.
    assert not html_pages.available(db, chat, model) and not html_pages.on(db, chat, model)
    assert html_pages.why_not(db, chat, model) == "off"
    settings_store.update(db, {"html_pages_enabled": True})
    assert html_pages.models_scope(db) == html_pages.MODELS_ALL
    assert html_pages.available(db, chat, model) and html_pages.on(db, chat, model)
    assert html_pages.why_not(db, chat, model) == ""
    assert html_pages.model_counts(db) == (1, 1)

    # The chat's way out: its person switched pages off there.
    chat.scope_json = {html_pages.SCOPE_KEY: False}
    db.commit()
    assert html_pages.available(db, chat, model) and not html_pages.on(db, chat, model)
    chat.scope_json = {html_pages.SCOPE_KEY: True}
    db.commit()
    assert html_pages.on(db, chat, model)

    # The instance's way out: only the models an administrator chose.
    settings_store.update(db, {html_pages.MODELS_KEY: html_pages.MODELS_CHOSEN})
    assert not html_pages.available(db, chat, model) and not html_pages.on(db, chat, model)
    assert html_pages.why_not(db, chat, model) == "model"
    assert html_pages.model_counts(db) == (0, 1)
    model.html_pages = True
    db.commit()
    assert html_pages.on(db, chat, model) and html_pages.model_counts(db) == (1, 1)
    # Anything else stored reads as "all".
    settings_store.update(db, {html_pages.MODELS_KEY: "nonsense"})
    assert html_pages.models_scope(db) == html_pages.MODELS_ALL


def test_an_instance_that_had_the_switch_on_and_no_model_ticked_gets_pages(db, user_id):
    """What an instance looked like before this: the switch on, stored
    without the models choice, no model flagged, chats with no switch of
    their own. It has pages now with nothing more done -- and a chat in
    progress is told by a note, not a new prompt."""
    from lembas.db.models import Setting

    session = Session(db, _chat(db, user_id))
    first = session.turn("Hello.")
    assert "html-view" not in _system(first)

    row = db.get(Setting, settings_store.GENERAL) or Setting(key=settings_store.GENERAL, value={})
    row.value = {**(row.value or {}), "html_pages_enabled": True}
    row.value.pop(html_pages.MODELS_KEY, None)
    db.add(row)
    db.commit()
    model = db.scalars(select(Model)).one()
    assert not model.html_pages
    assert html_pages.on(db, session.chat, model)
    second = session.turn("Draw it.")
    assert _system(second) == _system(first)
    assert "html-view" in _last_user(second) and "<session-note>" in _last_user(second)
    # A chat started now has the block in its prompt from the first word.
    assert "html-view" in _system(Session(db, _chat(db, user_id)).turn("Hello."))


def test_a_run_has_no_chat_switch_and_three_places_never_have_pages(db, user_id):
    chat = _chat(db, user_id, scope_json={html_pages.SCOPE_KEY: True})
    model = db.scalars(select(Model)).one()
    _on(db)
    # A schedule's run has no chat to switch: the instance and the model decide.
    run = Chat(user_id=user_id, model_id="m", kind=KIND_TASK,
               scope_json={html_pages.SCOPE_KEY: False})
    assert html_pages.on(db, run, model)
    # Never, whatever the switches say.
    never = [
        Chat(user_id=user_id, model_id="m", kind=KIND_AGENT, scope_json=chat.scope_json),
        Chat(user_id=user_id, model_id="m", parent_chat_id=chat.id, scope_json=chat.scope_json),
        Chat(user_id=user_id, model_id="m", phone_message_id="0" * 32,
             scope_json=chat.scope_json),
    ]
    for other in never:
        assert not html_pages.on(db, other, model)
        assert not html_pages.available(db, other, model)
        assert html_pages.why_not(db, other, model) in html_pages.spec()["never"]


def test_the_prompt_has_the_block_only_when_offered(db, user_id):
    session = Session(db, _chat(db, user_id))
    assert "html-view" not in _system(session.turn("Hello."))

    chat = _chat(db, user_id, scope_json={html_pages.SCOPE_KEY: True})
    _on(db)
    asked = _system(Session(db, chat).turn("Hello."))
    assert "html-view" in asked and "--page-accent" in asked
    # A page is static, and the model is told so: nothing in it runs.
    assert "A page is static" in asked and "inline SVG" in asked
    # Spec 3.8.1: what is to be seen is a page, not a file opened with `show`
    # -- with nothing said, a model wrote the file and showed that -- and what
    # has to run is not a page: it says so, and shows a file.
    assert "Do not write it to an HTML file and open that with `show`" in asked
    assert "a page in an answer cannot run anything" in asked
    assert "<!--" not in asked  # the block's own comment is not the model's to read


def test_switching_it_during_a_chat_is_a_note_not_a_new_prompt(db, user_id):
    session = Session(db, _chat(db, user_id, scope_json={html_pages.SCOPE_KEY: False}))
    _on(db)
    first = session.turn("Hello.")
    assert "html-view" not in _system(first)

    session.chat.scope_json = {html_pages.SCOPE_KEY: True}
    db.commit()
    second = session.turn("Draw it.")
    assert _system(second) == _system(first)  # the prefix stays still
    assert "html-view" in _last_user(second) and "<session-note>" in _last_user(second)
    third = session.turn("Again.")
    assert "html-view" not in _last_user(third)  # told once

    session.chat.scope_json = {html_pages.SCOPE_KEY: False}
    db.commit()
    fourth = session.turn("No more pages.")
    assert _system(fourth) == _system(first)
    off = _last_user(fourth)
    assert "<session-note>" in off and "html-view" not in off.split("<session-note>")[0]
    # Everything already sent goes out exactly as it did.
    assert fourth["messages"][: len(third["messages"])] == third["messages"]


# --- What a reader is shown ---------------------------------------------------------------
def test_a_finished_page_is_a_mount_and_never_its_markup(db):
    html = render_markdown(REPLY, pages=True)
    (page,) = pages_in(REPLY)
    assert (page.title, page.html) == ("Cakes", PAGE)
    assert f'data-html-page data-digest="{page.digest}"' in html
    # The frame: sealed, empty until the page's script gives it an address.
    frame = re.search(r"<iframe[^>]*>", html).group(0)
    assert 'sandbox=""' in frame and "src=" not in frame and "allow-" not in frame
    # The source is there to read, escaped; nothing of it is markup of ours.
    assert "&lt;p&gt;10&#32;wrapped" in html and "<p>10 wrapped" not in html
    assert "<p>Here is the count.</p>" in html and "<p>Two are unwrapped.</p>" in html
    # The renderer and the text side agree on what the page is.
    assert [p.digest for p in html_pages.pages_of(REPLY)] == [page.digest]


def test_only_a_closed_fence_with_the_exact_word_is_a_page():
    # The word is exact and stands straight after the backticks: a space
    # before it makes an ordinary code block, as the spec and the apps read it.
    for info in ("html", "html-views", "HTML-VIEW", "html-view2", " html-view", "\thtml-view"):
        text = f"```{info} T\n<p>x</p>\n```\n"
        assert "data-html-page" not in render_markdown(text, pages=True), info
        assert pages_in(text) == []
    # A reply that ended inside the fence has no page there.
    open_ended = "```html-view Cut off\n<p>x</p>\n"
    assert "data-html-page" not in render_markdown(open_ended, pages=True)
    assert pages_in(open_ended) == []
    assert "code-block" in render_markdown(open_ended, pages=True)
    # Four backticks hold a page with a line of three in it.
    nested = "````html-view Code\n<pre>\n```\nx\n```\n</pre>\n````\n"
    (page,) = pages_in(nested)
    assert "```\nx\n```" in page.html and "data-html-page" in render_markdown(nested, pages=True)


def test_a_page_being_written_is_a_placeholder_with_its_title():
    live = render_markdown(
        "Wait.\n\n```html-view Monthly <b>sales</b>\n<p>half", live=True, pages=True
    )
    assert "html-page--writing" in live and "Monthly" in live
    assert "<b>" not in live and "&lt;p&gt;half" not in live and "<iframe" not in live
    # A long open page is sent in parts: the later ones draw nothing more.
    assert render_markdown("```html-view T\n<p>more", live=True, cont=True, pages=True) == ""


def test_a_model_cannot_write_a_mount_of_its_own():
    forged = (
        '<div class="html-page-slot">0:0</div>\n\n'
        '<iframe src="https://example.org/"></iframe>\n\n'
        '<figure class="html-page" data-html-page data-digest="' + "a" * 32 + '"></figure>\n\n'
        "```html-view Real\n<p>x</p>\n```\n"
    )
    html = render_markdown(forged, pages=True)
    assert html.count("<iframe") == 1 and html.count("data-html-page data-digest") == 1
    assert "<iframe" not in html.split("<figure")[0]
    assert "a" * 32 not in html


def test_read_aloud_says_the_projection():
    said = speakable_text(REPLY)
    assert "[page: Cakes]" in said and "wrapped" in said and "doctype" not in said


# --- The document a frame is given ---------------------------------------------------------
HOSTILE = """<!doctype html><html><head><title>T</title>
<link rel="dns-prefetch" href="//leak.example.org"><link rel=preconnect href=//leak.example.org>
<meta http-equiv="refresh" content="0;url=https://leak.example.org/"><base href="https://leak.example.org/">
<style>a>b{color:var(--page-ink,#222)} @import url(https://leak.example.org/a.css);</style></head>
<body onload="go()"><h1 style="color:red">Sales &amp; <b>costs</b></h1>
<script>fetch('https://leak.example.org/')</script>
<a href="https://leak.example.org/">out</a> <a href="#part">in</a>
<img src="data:image/png;base64,AAAA" alt="ok"><img src="https://leak.example.org/a.png">
<svg viewBox="0 0 10 10"><style><p><link rel=dns-prefetch href=//leak.example.org></style>
<rect width="5" height="5" fill="url(#g)"/><image href="https://leak.example.org/i.png"/>
<script>bad()</script></svg>
<iframe src="https://leak.example.org/"></iframe><object data="https://leak.example.org/"></object>
<form action="https://leak.example.org/"><input name="q"><button formaction="https://leak.example.org/">go</button></form>
<!-- <link rel="dns-prefetch" href="//leak.example.org"> --></body></html>"""


def test_the_document_is_rebuilt_static_and_names_no_other_host():
    out = html_pages.document(html_pages.Page("T", HOSTILE), {"bg": "#101418", "ink": "rgb(1 2 3)"})
    # What a page is for survives.
    assert '<h1 style="color:red">Sales &amp; <b>costs</b></h1>' in out
    assert '<a href="#part">in</a>' in out and 'src="data:image/png;base64,AAAA"' in out
    assert '<rect width="5" height="5" fill="url(#g)">' in out
    assert "--page-bg:#101418" in out and "--page-ink:rgb(1 2 3)" in out
    # Nothing that runs, loads, hints or navigates does -- and a style sheet
    # that would fetch goes whole.
    for gone in ("<script", "<link", "<meta http", "<base", "<iframe", "<object", "<form",
                 "<input", "<button", "onload", "formaction", "action=", "@import", "<!--"):
        assert gone not in out, gone
    # The rebuilt document names no other host anywhere: it is safe in a
    # frame whose policy this server does not write (an app's).
    assert "leak.example.org" not in out and "example.org" not in out
    # A style sheet inside SVG is not kept at all (a browser reads it as
    # markup there), and the page's own, which would fetch, went whole.
    assert out.count("<style>") == 1  # ours: the frame's variables
    # The spec's own account of what was wrong with it, from the same parse.
    why = html_pages.static_faults(HOSTILE)
    assert why[:4] == ["element:link", "element:meta", "element:base", "css:import"]
    assert {"element:script", "attribute:onload", "address:href", "address:src",
            "element:iframe", "element:form"} <= set(why)


def test_the_allow_list_is_the_specs_and_only_the_specs():
    rules = html_pages.static_rules()
    assert "script" not in rules["elements"] and "link" not in rules["elements"]
    assert rules["image_types"] == ["png", "jpeg", "gif", "webp"]  # no svg+xml
    fine = (
        '<h1 class="t" data-n="1" aria-label="x">Sales</h1>'
        '<svg viewBox="0 0 10 10"><use href="#a"/><rect fill="url(#g)" width="5" height="5"/></svg>'
        '<img alt="" src="data:image/png;base64,AAAA"><style>.a{fill:url(#g)}</style>'
    )
    assert html_pages.static_faults(fine) == []
    built = html_pages.document(html_pages.Page("T", fine))
    assert 'data-n="1"' in built and '<use href="#a">' in built and "url(#g)" in built
    # An inline SVG picture is not an allowed image, and `xlink:href` is not listed.
    odd = '<img alt="" src="data:image/svg+xml;base64,AAAA"><svg><use xlink:href="#a"/></svg>'
    assert html_pages.static_faults(odd) == ["address:src", "attribute:xlink:href"]
    built = html_pages.document(html_pages.Page("T", odd))
    assert "svg+xml" not in built and "xlink" not in built


def test_the_frames_colours_are_colours_or_nothing():
    kept = html_pages.theme({
        "bg": "#fff", "ink": "oklch(0.2 0.01 250)", "muted": "red; } html { background: url(//x)",
        "accent": "url(https://example.org/)", "line": "rgba(0, 0, 0, 0.2)",
    })
    assert kept == {"--page-bg": "#fff", "--page-ink": "oklch(0.2 0.01 250)",
                    "--page-line": "rgba(0, 0, 0, 0.2)"}


def test_the_policy_runs_no_script_and_reaches_no_network():
    policy = sealed.page_policy()
    directives = dict(part.split(" ", 1) if " " in part else (part, "")
                      for part in policy.split("; "))
    assert directives["sandbox"] == ""  # nothing allowed: no scripts, no same-origin
    assert directives["script-src"] == "'none'" and directives["default-src"] == "'none'"
    assert directives["connect-src"] == "'none'" and directives["form-action"] == "'none'"
    assert "allow-same-origin" not in policy and "allow-scripts" not in policy
    assert "http" not in policy


# --- The route ------------------------------------------------------------------------------
def _reply(db, user: User) -> Message:
    chat = Chat(user_id=user.id, title="Cakes")
    db.add(chat)
    db.commit()
    message = Message(
        chat_id=chat.id, role="assistant", content=REPLY, complete=True, html_pages=True
    )
    db.add(message)
    db.commit()
    return message


def test_a_page_is_served_sealed_to_its_owner_in_a_frame(client: TestClient, db, registered):
    _on(db)
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    message = _reply(db, owner)
    (page,) = pages_in(REPLY)
    url = f"/pages/m/{message.id}/{page.digest}"

    framed = client.get(url + "?bg=%23101418&ink=nonsense", headers={"Sec-Fetch-Dest": "iframe"})
    assert framed.status_code == 200
    assert framed.headers["content-security-policy"] == sealed.page_policy()
    assert framed.headers["cache-control"] == "no-store"
    assert "10 wrapped, 2 not" in framed.text and "--page-bg:#101418" in framed.text
    assert "nonsense" not in framed.text
    # No cookie is set on it, and the application's own policy is not laid over it.
    assert "set-cookie" not in framed.headers

    # As a document of its own -- a tab -- it is refused.
    assert client.get(url, headers={"Sec-Fetch-Dest": "document"}).status_code == 403
    # A digest that is not a page of this reply; a reply that is not there.
    assert client.get(f"/pages/m/{message.id}/{'0' * 32}").status_code == 404
    assert client.get(f"/pages/m/{'0' * 32}/{page.digest}").status_code == 404
    # Somebody else's reply is as absent as one that is not there.
    _as_stranger(client, db)
    assert client.get(url, headers={"Sec-Fetch-Dest": "iframe"}).status_code == 404


def test_switched_off_the_frame_says_so_and_shows_nothing_of_the_page(
    client: TestClient, db, registered
):
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    message = _reply(db, owner)
    (page,) = pages_in(REPLY)
    off = client.get(f"/pages/m/{message.id}/{page.digest}")
    assert off.status_code == 200 and "10 wrapped" not in off.text
    assert off.headers["content-security-policy"] == sealed.page_policy()


def test_a_report_shows_its_pages_the_same_way(client: TestClient, db, registered):
    from lembas.services import reports as reports_service

    _on(db)
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    report = reports_service.file(
        db, owner_id=owner.id, report={"title": "Cakes", "body": REPLY}
    )
    report.html_pages = True
    db.commit()
    (page,) = pages_in(REPLY)
    shown = client.get(f"/reports/{report.id}").text
    assert f'data-pages-base="/pages/r/{report.id}"' in shown
    assert f'data-digest="{page.digest}"' in shown
    served = client.get(f"/pages/r/{report.id}/{page.digest}")
    assert served.status_code == 200 and "10 wrapped" in served.text
    _as_stranger(client, db)
    assert client.get(f"/pages/r/{report.id}/{page.digest}").status_code == 404


# --- The switches, as they are set ------------------------------------------------------------
def _pages_setting(html: str) -> str:
    """The chat settings' pages row, or "" when the menu has none."""
    start = html.find('class="pages-setting"')
    return html[start:html.find("</div>", start)] if start != -1 else ""


def test_the_chats_switch_is_on_from_the_admins_one_switch(client: TestClient, db, registered):
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    chat = _chat(db, owner.id)
    switch = f"/api/chats/{chat.id}/html-pages"
    assert _pages_setting(client.get(f"/chat/{chat.id}").text) == ""  # off: nothing to say

    assert client.post(
        "/admin/general/html-pages", data={"html_pages_enabled": "true"},
        follow_redirects=False,
    ).status_code in (302, 303)
    assert html_pages.instance_on(db) and html_pages.models_scope(db) == html_pages.MODELS_ALL
    # No model ticked, nothing set on the chat: the switch is there, and on.
    row = _pages_setting(client.get(f"/chat/{chat.id}").text)
    assert switch in row and "checked" in row and "disabled" not in row

    assert client.post(f"/api/chats/{chat.id}/html-pages", data={}).status_code == 204
    db.refresh(chat)
    assert not html_pages.chat_on(chat) and chat.scope_json[html_pages.SCOPE_KEY] is False
    assert "checked" not in _pages_setting(client.get(f"/chat/{chat.id}").text)
    assert client.post(f"/api/chats/{chat.id}/html-pages", data={"on": "1"}).status_code == 204
    db.refresh(chat)
    assert html_pages.chat_on(chat)


def test_the_admin_page_says_how_many_models_and_where_to_change_it(
    client: TestClient, db, registered
):
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    _chat(db, owner.id)  # makes the one model
    page = client.get("/admin/general").text
    assert "Pages are off: no model is offered them." in page
    assert 'name="html_pages_models" value="all" checked' in page

    def save(**data):
        assert client.post(
            "/admin/general/html-pages", data=data, follow_redirects=False
        ).status_code in (302, 303)
        return client.get("/admin/general").text

    page = save(html_pages_enabled="true", html_pages_models="chosen")
    assert html_pages.models_scope(db) == html_pages.MODELS_CHOSEN
    assert 'name="html_pages_models" value="chosen" checked' in page
    assert "0 of 1 chat models are offered pages now." in page
    assert 'href="/admin/models"' in page[page.find('data-testid="html-pages-count"'):][:600]
    # The switch saved without the choice keeps the choice.
    save(html_pages_enabled="true")
    assert html_pages.models_scope(db) == html_pages.MODELS_CHOSEN
    page = save(html_pages_enabled="true", html_pages_models="all")
    assert "1 of 1 chat models are offered pages now." in page


def test_the_chats_settings_say_why_pages_cannot_be_had(client: TestClient, db, registered):
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    chat = _chat(db, owner.id)
    settings_store.update(
        db, {"html_pages_enabled": True, html_pages.MODELS_KEY: html_pages.MODELS_CHOSEN}
    )
    row = _pages_setting(client.get(f"/chat/{chat.id}").text)
    assert "disabled" in row and "checked" not in row and "hx-post" not in row
    assert 'data-testid="pages-why-not"' in row and "Not available with this model" in row
    for model in db.scalars(select(Model)):
        model.html_pages = True
    db.commit()
    row = _pages_setting(client.get(f"/chat/{chat.id}").text)
    assert "disabled" not in row and "checked" in row and "pages-why-not" not in row


def test_a_new_chat_follows_the_composers_switch(client: TestClient, db, registered):
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    _chat(db, owner.id)
    _on(db)
    from lembas.api import chats as chats_api

    def start(**said):
        return chats_api._new_chat(db, owner, model_id="m", **said)

    assert html_pages.chat_on(start())  # nobody said: on
    assert html_pages.chat_on(start(html_pages=True))
    off = start(html_pages=False)
    assert not html_pages.chat_on(off) and off.scope_json[html_pages.SCOPE_KEY] is False
    # The new-chat screen shows the box ticked, and says it was shown.
    page = client.get("/chat").text
    row = _pages_setting(page)
    assert 'name="html_pages"' in row and "checked" in row
    assert 'name="html_pages_shown"' in page


def test_the_model_flag_is_in_both_model_lists(db, user_id):
    from lembas.api.v2 import serialise

    _chat(db, user_id)
    model = db.scalars(select(Model)).one()
    assert serialise.model(model)["html_pages"] is False
    model.html_pages = True
    assert serialise.model(model)["html_pages"] is True


def test_a_text_step_says_which_pages_it_carries():
    from lembas.api.v2 import serialise
    from lembas.services import steps as steps_service

    (page,) = pages_in(REPLY)
    with_page = serialise.step(steps_service.Step(index=0, kind=steps_service.KIND_TEXT,
                                                  markdown=REPLY, pages=True))
    assert with_page["markdown"] == REPLY
    assert with_page["pages"] == [{"title": "Cakes", "digest": page.digest}]
    assert with_page["text"] == "Here is the count.\n\n[page: Cakes]\n\nTwo are unwrapped."
    plain = serialise.step(steps_service.Step(index=0, kind=steps_service.KIND_TEXT,
                                              markdown="Just words."))
    assert "pages" not in plain and "text" not in plain


@pytest.mark.parametrize("where", ["title", "report"])
def test_whoever_reads_a_reply_as_text_reads_the_projection(where):
    from lembas.services import reports as reports_service

    answer = f"```html-view Cakes\n{PAGE}\n```\n\nTwo are unwrapped."
    if where == "report":
        report, note = reports_service.outcome(
            notify="always", title="Cakes", calls=[], answer=answer
        )
        assert note == "[page: Cakes]" and report["summary"] == "[page: Cakes]"
        assert report["body"] == answer  # the report keeps the page itself
    else:
        assert html_pages.project(answer).startswith("[page: Cakes]")


# --- For an app: the same document, and the chat's switch --------------------------------------
def _app(db, user: User) -> dict[str, str]:
    from lembas.security import api_tokens

    _, raw = api_tokens.create(db, user, name="LLeMbas Desktop", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def test_an_app_is_given_the_same_rebuilt_document(client: TestClient, db, registered):
    from lembas.services import reports as reports_service

    _on(db)
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    auth = _app(db, owner)
    hostile = (
        "```html-view Cakes\n<p>ok</p><script>go()</script>"
        "<link rel=dns-prefetch href=//x>\n```\n"
    )
    chat = Chat(user_id=owner.id, title="Cakes")
    db.add(chat)
    db.commit()
    message = Message(
        chat_id=chat.id, role="assistant", content=hostile, complete=True, html_pages=True
    )
    db.add(message)
    report = reports_service.file(db, owner_id=owner.id, report={"title": "R", "body": hostile})
    report.html_pages = True
    db.commit()
    (page,) = pages_in(hostile)

    got = client.get(
        f"/api/v2/pages/m/{message.id}/{page.digest}?bg=%23101418&ink=nonsense", headers=auth
    )
    assert got.status_code == 200 and got.headers["content-type"].startswith("application/json")
    body = got.json()
    assert set(body) == {"title", "digest", "html", "policy"}
    assert (body["title"], body["digest"]) == ("Cakes", page.digest)
    assert body["policy"] == sealed.page_policy()
    # Rebuilt here: the app never sees the model's markup to clean for itself.
    assert "<p>ok</p>" in body["html"] and "--page-bg:#101418" in body["html"]
    assert "<script" not in body["html"] and "<link" not in body["html"]
    assert "nonsense" not in body["html"]
    # Byte for byte what the web's own frame is served.
    framed = client.get(f"/pages/m/{message.id}/{page.digest}?bg=%23101418")
    assert framed.text == body["html"]

    by_report = client.get(f"/api/v2/pages/r/{report.id}/{page.digest}", headers=auth)
    assert by_report.status_code == 200 and "<p>ok</p>" in by_report.json()["html"]
    assert client.get(f"/api/v2/pages/m/{message.id}/{'0' * 32}", headers=auth).status_code == 404
    assert client.get(f"/api/v2/pages/m/{message.id}/{page.digest}").status_code == 401

    # Somebody else's reply and report are as absent as ones that are not there.
    sam = _as_stranger(client, db)
    settings_store.update(db, {"default_permissions": {"api.use": True}})
    other = _app(db, sam)
    for url in (f"/api/v2/pages/m/{message.id}/{page.digest}",
                f"/api/v2/pages/r/{report.id}/{page.digest}"):
        assert client.get(url, headers=other).status_code == 404


def test_an_app_reads_and_sets_the_chats_switch(client: TestClient, db, registered):
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    auth = _app(db, owner)
    chat = _chat(db, owner.id)
    url = f"/api/v2/chats/{chat.id}/html-pages"
    assert client.get(url, headers=auth).json() == {"offered": False, "on": False}
    # Not offered: it cannot be switched on (switching it off always can).
    assert client.post(url, json={"on": True}, headers=auth).status_code == 409
    assert client.post(url, json={"on": False}, headers=auth).json() == {
        "offered": False, "on": False
    }
    _on(db)
    # What was said stays said: this chat was switched off above.
    assert client.get(url, headers=auth).json() == {"offered": True, "on": False}
    assert client.post(url, json={"on": True}, headers=auth).json()["on"] is True
    # A chat nobody touched is offered pages, and has them on.
    fresh = _chat(db, owner.id)
    assert client.get(
        f"/api/v2/chats/{fresh.id}/html-pages", headers=auth
    ).json() == {"offered": True, "on": True}
    assert client.post(url, json={"on": False}, headers=auth).json() == {
        "offered": True, "on": False
    }
    db.refresh(chat)
    assert not html_pages.chat_on(chat)
    assert client.post(url, json={"on": True}, headers=auth).json()["on"] is True
    db.refresh(chat)
    assert html_pages.chat_on(chat)
    # An agent chat never has them, whatever is asked.
    agent = Chat(user_id=owner.id, model_id="m", kind=KIND_AGENT)
    db.add(agent)
    db.commit()
    agent_url = f"/api/v2/chats/{agent.id}/html-pages"
    assert client.get(agent_url, headers=auth).json() == {"offered": False, "on": False}
    assert client.post(agent_url, json={"on": True}, headers=auth).status_code == 409


# --- Probes: every way found of hiding an address, and the caps ---------------------------------
HOST = "evil.example.org"


@pytest.mark.parametrize(
    "css",
    [
        f"a{{background:url(\"http://{HOST}/a'b\")}}",      # a quote inside the string
        f"a{{background:u\\72l(http://{HOST}/x)}}",          # an escape in the function's name
        f"a{{background:image-set(\"http://{HOST}/x\" 1x)}}",
        f"a{{background:-webkit-image-set(\"//{HOST}/x\" 1x)}}",
        f"@\\69mport \"http://{HOST}/a.css\";",
        f"@import url(http://{HOST}/a.css);",
        f"@im/**/port \"http://{HOST}/a.css\";",
        f"@font-face{{font-family:x;src:url(http://{HOST}/f.woff)}}",
        f"@namespace svg url(http://{HOST}/);",
        f"a{{background:ur/**/l(http://{HOST}/x)}}",
        f"a{{background:URL( 'http://{HOST}/x' )}}",
        f"a{{background:url(//{HOST}/x)}}",
        "a{background:url(data:image/svg+xml;base64,AAAA)}",
        f"a{{background:url(data:image/png;base64,AAAA) , url(http://{HOST}/)}}",
        f"a{{background:cross-fade(\"http://{HOST}/x\", red)}}",
        "a{content:attr(data-u url)}",
        f"a{{background:image(\"http://{HOST}/x\")}}",
        f"a::after{{content:\"\\75 rl(http://{HOST})\"}}",
        f"a{{background:&#117;rl(http://{HOST}/x)}}",        # a character reference
    ],
)
def test_css_that_could_fetch_is_dropped_whole(css):
    assert html_pages.css_faults(css), css
    for page in (f"<style>{css}</style><p>kept</p>", f'<p style="{css}">kept</p>'):
        out = html_pages.document(html_pages.Page("T", page))
        assert HOST not in out and "kept" in out, page
        assert html_pages.static_faults(page)


def test_css_that_fetches_nothing_is_kept():
    fine = (
        "body{color:var(--page-ink,#222)} a>b{margin:calc(1px + 2%)} "
        "@media (max-width:40em){td{padding:0}} @keyframes k{from{opacity:0}} "
        ".a{fill:url(#grad)} .b{background:url(data:image/png;base64,iVBORw0KGgo=)} "
        ".curly{color:rgb(1 2 3)} /* a comment */"
    )
    assert html_pages.css_faults(fine) == []
    assert fine in html_pages.document(html_pages.Page("T", f"<style>{fine}</style>"))


def test_a_style_sheet_inside_svg_is_never_kept():
    """There a browser decodes `&#117;rl(` to `url(`, and this parser read text."""
    page = f"<svg><style>a{{background:&#117;rl(http://{HOST}/x)}}</style><rect/></svg>"
    out = html_pages.document(html_pages.Page("T", page))
    assert HOST not in out and "&#117;" not in out and out.count("<style>") == 1
    harmless = "<svg><style>rect{fill:red}</style><rect/></svg>"
    assert html_pages.document(html_pages.Page("T", harmless)).count("<style>") == 1
    assert html_pages.static_faults(harmless) == ["element:style"]


@pytest.mark.parametrize(
    "attribute",
    ["fill", "stroke", "clip-path", "marker-start", "marker-mid", "marker-end", "style",
     "transform", "class"],
)
def test_every_attribute_value_is_checked_for_an_address(attribute):
    page = f'<svg><rect {attribute}="url(http://{HOST}/p.svg#g)" width="5"/></svg>'
    out = html_pages.document(html_pages.Page("T", page))
    assert HOST not in out and 'width="5"' in out
    assert "css:url" in html_pages.static_faults(page)
    kept = html_pages.document(html_pages.Page("T", f'<svg><rect {attribute}="url(#g)"/></svg>'))
    assert f'{attribute}="url(#g)"' in kept


def test_an_address_is_written_exactly_as_it_was_checked():
    # A no-break space is not white space to a browser: `\u00a0#x` is a
    # relative address, on this server's own origin. It is not kept.
    nbsp = html_pages.document(html_pages.Page("T", '<a href="\u00a0#x">a</a>'))
    assert "href" not in nbsp
    # ASCII white space is trimmed, and what is written is the trimmed value.
    spaced = html_pages.document(html_pages.Page("T", '<a href=" \t#x\n">a</a>'))
    assert '<a href="#x">' in spaced
    assert html_pages.address("#a b") is None and html_pages.address("#") == "#"
    assert html_pages.address("data:image/png;base64,AAAA\" onx=\"1") is None


def test_a_page_past_its_limits_is_not_shown():
    huge = "<p>" + "x" * (html_pages.MAX_SOURCE + 1) + "</p>"
    assert "too large" in html_pages.document(html_pages.Page("T", huge))
    assert html_pages.static_faults(huge) == ["limit:size"]
    many = "<p>a</p>" * (html_pages.MAX_ELEMENTS + 1)
    assert "too large" in html_pages.document(html_pages.Page("T", many))
    deep = "<div>" * (html_pages.MAX_DEPTH + 5) + "x"
    assert "too large" in html_pages.document(html_pages.Page("T", deep))
    # A long list written without end tags is long, not deep.
    items = "<ul>" + "<li>item" * 2000 + "</ul>"
    out = html_pages.document(html_pages.Page("T", items))
    assert "too large" not in out and out.count("<li>") == 2000 == out.count("</li>")


def test_a_use_bomb_draws_nothing():
    """Ten groups, each using the one before ten times: ten thousand million
    shapes from a few hundred bytes. No `<use>` of such a page is kept."""
    layers = '<g id="a0"><rect width="1" height="1"/></g>'
    for n in range(1, 11):
        layers += f'<g id="a{n}">' + f'<use href="#a{n - 1}"/>' * 10 + "</g>"
    bomb = f'<svg><defs>{layers}</defs><use href="#a10"/></svg>'
    out = html_pages.document(html_pages.Page("T", bomb))
    assert "<use" not in out and "<rect" in out
    assert "limit:use" in html_pages.static_faults(bomb)
    # A reference to itself is the same thing.
    loop = '<svg><g id="a"><use href="#a"/></g></svg>'
    assert "<use" not in html_pages.document(html_pages.Page("T", loop))
    # An honest legend is untouched.
    legend = '<svg><defs><rect id="k" width="4" height="4"/></defs>' + '<use href="#k"/>' * 20
    assert html_pages.document(html_pages.Page("T", legend + "</svg>")).count("<use") == 20


def test_the_rebuilt_document_is_remembered():
    page = html_pages.Page("T", "<p>once</p>")
    html_pages._document.cache_clear()
    first = html_pages.document(page, {"bg": "#fff"})
    assert html_pages.document(page, {"bg": "#fff"}) is first
    assert html_pages._document.cache_info().hits == 1
    assert html_pages.document(page, {"bg": "#000"}) != first


# --- A page only where pages were offered (pages.md §4) ------------------------------------------
def test_a_fence_is_a_page_only_in_a_reply_written_with_pages_offered(
    client: TestClient, db, registered
):
    from lembas.api.v2 import serialise
    from lembas.services import steps as steps_service

    # The renderer by itself draws no page: a preview, a quoted text, a
    # subagent's turn in the job panel, a reply from before the switch.
    plain = render_markdown(REPLY)
    assert "data-html-page" not in plain and "<iframe" not in plain
    assert "code-block" in plain and "&lt;p&gt;10 wrapped" in plain

    _on(db)
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    chat = Chat(user_id=owner.id, title="Cakes")
    db.add(chat)
    db.commit()
    without = Message(chat_id=chat.id, role="assistant", content=REPLY, complete=True)
    with_pages = Message(chat_id=chat.id, role="assistant", content=REPLY, complete=True,
                         html_pages=True)
    db.add_all([without, with_pages])
    db.commit()
    (page,) = pages_in(REPLY)

    # The reply written without pages offered: a code block, no page to fetch,
    # none in the app's data -- whatever the switches say today.
    (step,) = steps_service.for_message(without)
    assert "data-html-page" not in step.html and "pages" not in serialise.step(step)
    assert client.get(f"/pages/m/{without.id}/{page.digest}").status_code == 404
    auth = _app(db, owner)
    assert client.get(
        f"/api/v2/pages/m/{without.id}/{page.digest}", headers=auth
    ).status_code == 404

    (step,) = steps_service.for_message(with_pages)
    assert f'data-digest="{page.digest}"' in step.html
    assert serialise.step(step)["pages"] == [{"title": "Cakes", "digest": page.digest}]
    assert client.get(f"/pages/m/{with_pages.id}/{page.digest}").status_code == 200


def test_the_reply_being_written_is_marked_by_what_its_model_is_offered(db, user_id):
    session = Session(db, _chat(db, user_id, scope_json={html_pages.SCOPE_KEY: True}))
    session.turn("Before.")
    _on(db)
    session.turn("After.")
    replies = list(db.scalars(
        select(Message).where(Message.chat_id == session.chat.id, Message.role == "assistant")
        .order_by(Message.created_at)
    ))
    assert [m.html_pages for m in replies] == [False, True]


def test_a_page_cut_in_two_by_a_tool_call_is_not_a_page():
    """The model opened the block, called a tool, and went on: the two pieces
    are closed and reopened by hand to render as code -- a page of half its
    source would be a digest nothing serves."""
    from lembas.services import steps as steps_service

    first = "Look:\n\n```html-view Cut\n<p>one</p>\n"
    second = "<p>two</p>\n```\n\nDone.\n\n```html-view Whole\n<p>w</p>\n```\n"
    text = first + second
    built = steps_service._build(
        text=text, thinking="", events=[{"name": "read"}],
        marks=[{"text_to": len(first), "tools_to": 1, "thinking_to": 0}],
        live=False, pages=True,
    )
    texts = [s for s in built if s.kind == steps_service.KIND_TEXT]
    assert len(texts) == 2
    assert all("data-html-page" not in s.html for s in texts)
    assert not texts[0].pages and not texts[1].pages
    # Uncut, the same reply has both.
    whole = steps_service._build(
        text=text, thinking="", events=[], marks=[], live=False, pages=True
    )
    assert whole[-1].html.count("data-html-page data-digest") == 2


def test_the_web_switch_is_refused_where_pages_are_not_offered(client: TestClient, db, registered):
    """As the app's route: never "on" stored for a chat that cannot have pages."""
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    chat = _chat(db, owner.id)
    url = f"/api/chats/{chat.id}/html-pages"
    assert client.post(url, data={"on": "1"}).status_code == 409  # the instance is off
    db.refresh(chat)
    assert html_pages.SCOPE_KEY not in (chat.scope_json or {})
    _on(db)
    assert client.post(url, data={"on": "1"}).status_code == 204
    for never in (
        Chat(user_id=owner.id, model_id="m", kind=KIND_AGENT),
        Chat(user_id=owner.id, model_id="m", parent_chat_id=chat.id),
        Chat(user_id=owner.id, model_id="m", phone_message_id="0" * 32),
    ):
        db.add(never)
        db.commit()
        assert client.post(f"/api/chats/{never.id}/html-pages", data={"on": "1"}).status_code == 409
        db.refresh(never)
        assert (never.scope_json or {}).get(html_pages.SCOPE_KEY) is not True
        # Off is always possible.
        assert client.post(f"/api/chats/{never.id}/html-pages", data={}).status_code == 204
