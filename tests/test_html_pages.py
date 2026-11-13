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
def test_three_switches_and_the_places_that_never_have_pages(db, user_id):
    chat = _chat(db, user_id)
    model = db.scalars(select(Model)).one()
    # Off by default, at every level.
    assert not html_pages.available(db, chat, model) and not html_pages.on(db, chat, model)
    settings_store.update(db, {"html_pages_enabled": True})
    assert not html_pages.available(db, chat, model)  # the model is not flagged
    model.html_pages = True
    db.commit()
    assert html_pages.available(db, chat, model)
    assert not html_pages.on(db, chat, model)  # the chat's own switch is off
    chat.scope_json = {html_pages.SCOPE_KEY: True}
    db.commit()
    assert html_pages.on(db, chat, model)

    # A schedule's run has no chat to switch: the instance and the model decide.
    run = Chat(user_id=user_id, model_id="m", kind=KIND_TASK)
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


def test_the_prompt_has_the_block_only_when_offered(db, user_id):
    session = Session(db, _chat(db, user_id))
    assert "html-view" not in _system(session.turn("Hello."))

    chat = _chat(db, user_id, scope_json={html_pages.SCOPE_KEY: True})
    _on(db)
    asked = _system(Session(db, chat).turn("Hello."))
    assert "html-view" in asked and "--page-accent" in asked
    # This server shows a page without running its scripts, and says so.
    assert html_pages.STATIC_NOTE in asked


def test_switching_it_during_a_chat_is_a_note_not_a_new_prompt(db, user_id):
    session = Session(db, _chat(db, user_id))
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

    session.chat.scope_json = {}
    db.commit()
    fourth = session.turn("No more pages.")
    assert _system(fourth) == _system(first)
    off = _last_user(fourth)
    assert "<session-note>" in off and "html-view" not in off.split("<session-note>")[0]
    # Everything already sent goes out exactly as it did.
    assert fourth["messages"][: len(third["messages"])] == third["messages"]


# --- What a reader is shown ---------------------------------------------------------------
def test_a_finished_page_is_a_mount_and_never_its_markup(db):
    html = render_markdown(REPLY)
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
    for info in ("html", "html-views", "HTML-VIEW", "html-view2"):
        text = f"```{info} T\n<p>x</p>\n```\n"
        assert "data-html-page" not in render_markdown(text), info
        assert pages_in(text) == []
    # A reply that ended inside the fence has no page there.
    open_ended = "```html-view Cut off\n<p>x</p>\n"
    assert "data-html-page" not in render_markdown(open_ended) and pages_in(open_ended) == []
    assert "code-block" in render_markdown(open_ended)
    # Four backticks hold a page with a line of three in it.
    nested = "````html-view Code\n<pre>\n```\nx\n```\n</pre>\n````\n"
    (page,) = pages_in(nested)
    assert "```\nx\n```" in page.html and "data-html-page" in render_markdown(nested)


def test_a_page_being_written_is_a_placeholder_with_its_title():
    live = render_markdown("Wait.\n\n```html-view Monthly <b>sales</b>\n<p>half", live=True)
    assert "html-page--writing" in live and "Monthly" in live
    assert "<b>" not in live and "&lt;p&gt;half" not in live and "<iframe" not in live
    # A long open page is sent in parts: the later ones draw nothing more.
    assert render_markdown("```html-view T\n<p>more", live=True, cont=True) == ""


def test_a_model_cannot_write_a_mount_of_its_own():
    forged = (
        '<div class="html-page-slot">0:0</div>\n\n'
        '<iframe src="https://example.org/"></iframe>\n\n'
        '<figure class="html-page" data-html-page data-digest="' + "a" * 32 + '"></figure>\n\n'
        "```html-view Real\n<p>x</p>\n```\n"
    )
    html = render_markdown(forged)
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
    # Nothing that runs, loads, hints or navigates does.
    for gone in ("<script", "<link", "<meta http-equiv", "<base", "<iframe", "<object",
                 "onload", "formaction", "action=", "@import", "<!--"):
        assert gone not in out, gone
    # The one address left is inside a style sheet, where the policy refuses
    # the request; no element or attribute names the host.
    without_css = re.sub(r"<style>.*?</style>", "", out, flags=re.S)
    assert "leak.example.org" not in without_css
    # Every `<` that is written opens a tag this code chose to write.
    assert "<link" not in out and "\\3c link" in out


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
    message = Message(chat_id=chat.id, role="assistant", content=REPLY, complete=True)
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
def test_the_chats_switch_is_shown_only_where_pages_can_be(client: TestClient, db, registered):
    owner = db.scalars(select(User).order_by(User.created_at)).first()
    chat = _chat(db, owner.id)
    switch = f"/api/chats/{chat.id}/html-pages"
    assert switch not in client.get(f"/chat/{chat.id}").text

    assert client.post(
        "/admin/general/html-pages", data={"html_pages_enabled": "true"},
        follow_redirects=False,
    ).status_code in (302, 303)
    assert html_pages.instance_on(db)
    assert switch not in client.get(f"/chat/{chat.id}").text  # the model is not flagged
    for model in db.scalars(select(Model)):
        model.html_pages = True
    db.commit()
    assert switch in client.get(f"/chat/{chat.id}").text

    assert client.post(f"/api/chats/{chat.id}/html-pages", data={"on": "1"}).status_code == 204
    db.refresh(chat)
    assert html_pages.chat_on(chat)
    assert client.post(f"/api/chats/{chat.id}/html-pages", data={}).status_code == 204
    db.refresh(chat)
    assert not html_pages.chat_on(chat) and html_pages.SCOPE_KEY not in (chat.scope_json or {})


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
                                                  markdown=REPLY))
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
    message = Message(chat_id=chat.id, role="assistant", content=hostile, complete=True)
    db.add(message)
    report = reports_service.file(db, owner_id=owner.id, report={"title": "R", "body": hostile})
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
    assert client.get(url, headers=auth).json() == {"offered": True, "on": False}
    assert client.post(url, json={"on": True}, headers=auth).json() == {
        "offered": True, "on": True
    }
    db.refresh(chat)
    assert html_pages.chat_on(chat)
    assert client.post(url, json={"on": False}, headers=auth).json()["on"] is False
    # An agent chat never has them, whatever is asked.
    agent = Chat(user_id=owner.id, model_id="m", kind=KIND_AGENT)
    db.add(agent)
    db.commit()
    agent_url = f"/api/v2/chats/{agent.id}/html-pages"
    assert client.get(agent_url, headers=auth).json() == {"offered": False, "on": False}
    assert client.post(agent_url, json={"on": True}, headers=auth).status_code == 409
