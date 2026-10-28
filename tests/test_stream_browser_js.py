"""The browser half of a streaming reply: one bubble per message, a Stop that
says it is stopping, no page-wide work on every frame, and a microphone that
says why it did not open.

There is no JavaScript runner (hard rule 1), so these pin what the scripts say
about themselves -- and, where a fix leans on the vendored htmx behaving a
particular way, what *htmx* says about itself, so a library bump that changes
it fails here rather than in somebody's transcript.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

import lembas
from lembas.db.models import Connection, Model
from lembas.services.crypto import encrypt

from .conftest import js_code, js_function, js_says, script

WEB = Path(lembas.__file__).parent / "web"
APP = script("app.js")
APP_CODE = js_code(APP)
UI = script("ui.js")
UI_CODE = js_code(UI)
AUDIO = script("audio.js")
STEPS = js_code(script("steps.js"))
HTMX = (WEB / "static/vendor/htmx.min.js").read_text(encoding="utf-8")
CHAT_CSS = (WEB / "static/css/chat.css").read_text(encoding="utf-8")


def _listener(code: str, event: str, on: str = "document.body", about: str = "") -> str:
    """The body of the `on.addEventListener("event", function …)` that mentions
    `about` -- several scripts listen to the same event for different reasons."""
    for found in re.finditer(
        re.escape(f'{on}.addEventListener("{event}", function (event) {{')
        + r"([\s\S]*?)\n  \}\);",
        code,
    ):
        if about in found.group(1):
            return found.group(1)
    raise AssertionError(f"the {event} listener is gone")


# --- What the fixes rely on in the vendored htmx -------------------------------
def test_htmx_reads_the_response_back_after_before_swap():
    """The duplicate filter rewrites `detail.serverResponse`. That only works
    because htmx 2.0.10 reads the field back after dispatching the event; a
    version that kept its own copy would swap the unfiltered answer."""
    assert 'version:"2.0.10"' in HTMX
    assert js_says(HTMX, 'ae(r,"htmx:beforeSwap",m)', "g=m.serverResponse")


def test_htmx_skips_an_extension_when_a_listener_cancelled_the_event():
    """The second-stream belt cancels `htmx:afterProcessNode`; the SSE extension
    opens its stream from that event's `onEvent`, which htmx calls only while
    the event is not cancelled."""
    assert "e.onEvent(t,r)!==false&&!r.defaultPrevented" in HTMX
    sse = (WEB / "static/vendor/htmx-ext-sse.js").read_text(encoding="utf-8")
    assert js_says(sse, "case 'htmx:afterProcessNode':", "ensureEventSourceOnElement(parent)")


# --- One bubble per message -----------------------------------------------------
def test_every_swap_into_the_thread_drops_bubbles_the_page_already_has():
    """Not only the tail's answer: the composer's answer landing after a poll had
    appended the same bubbles was swapped in whole, and both copies carried
    `sse-connect` -- the reply streamed twice."""
    before = _listener(APP_CODE, "htmx:beforeSwap")
    assert "touchesThread(detail.target)" in before
    assert 'createElement("template")' in before
    assert "duplicates(parsed.content" in before
    assert "article.remove()" in before
    assert "detail.serverResponse = parsed.innerHTML" in before
    # The guard used to begin by returning for anything but the tail.
    assert 'elt.id !== "thread-tail"' not in before
    # The tail is still stricter: the whole answer goes.
    assert js_says(before, '"thread-tail"', "detail.shouldSwap = false")


def test_out_of_band_bubbles_are_filtered_too():
    """The `done` frame brings the next reply out of band (`beforeend:#thread`),
    and the SSE extension's swaps never fire `htmx:beforeSwap` at all."""
    oob = _listener(APP_CODE, "htmx:oobBeforeSwap")
    assert "touchesThread(target)" in oob
    assert "duplicates(fragment" in oob
    assert "article.remove()" in oob


def test_a_bubble_about_to_be_replaced_is_not_a_duplicate():
    """A rewind re-renders the thread with innerHTML and a queued bubble is
    re-rendered in place with outerHTML; treating either as a duplicate would
    drop the very bubbles being re-rendered."""
    replaced = js_function(APP, "replacedBy")
    assert js_says(replaced, '"outerHTML"', "target.contains(el)")
    assert js_says(replaced, '"innerHTML"', "el !== target", "target.contains(el)")
    assert "return false" in replaced


def test_the_server_deleting_a_bubble_is_not_a_duplicate():
    """`<article id="msg-…" hx-swap-oob="delete">` names a bubble the page has
    precisely because it is removing it."""
    dupes = js_function(APP, "duplicates")
    assert "[hx-swap-oob]" in dupes
    assert "getElementById(article.id)" in dupes
    assert "replacedBy(existing" in dupes


def test_nothing_sends_an_out_of_band_innerhtml_into_the_thread():
    """The out-of-band filter cannot see the swap style and takes anything into
    `#thread` that is not a replacement of its target as an append. An
    `innerHTML:#thread` would make it drop every bubble being re-rendered."""
    src = Path(lembas.__file__).parent
    for path in [*src.rglob("*.py"), *src.rglob("*.html")]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r'hx-swap-oob="innerHTML:\s*#thread"', text), path


def test_the_tail_stays_quiet_while_the_composer_sends():
    config = _listener(APP_CODE, "htmx:configRequest")
    assert js_says(
        config, 'getElementById("composer-form")', '"htmx-request"', "preventDefault()"
    )
    start = _listener(APP_CODE, "htmx:beforeRequest", about="composer-form")
    assert '"composer-form"' in start
    assert js_says(start, 'getElementById("thread-tail")', 'trigger(tail, "htmx:abort")')


def test_cancelling_the_tail_does_not_switch_the_progress_bar_off():
    """The abort the composer fires carries no request path, so the path test
    alone would count it -- and switch the bar off under the send."""
    assert 'el.id === "thread-tail"' in js_function(APP, "quiet")


def test_a_second_stream_for_one_message_is_never_opened():
    belt = re.search(
        r'document\.addEventListener\("htmx:afterProcessNode", function \(event\) \{'
        r"([\s\S]*?)\n  \}\);",
        APP_CODE,
    )
    assert belt, "the belt is gone"
    body = belt.group(1)
    assert '"sse-connect"' in body
    assert js_says(body, "streaming.has(", "event.preventDefault()", "el.remove()")
    assert "streaming.add(el)" in body
    assert "new WeakSet()" in APP_CODE


class _Form(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attrs: dict[str, str] | None = None

    def handle_starttag(self, tag, attrs):
        got = {key: (value or "") for key, value in attrs}
        if tag == "form" and got.get("id") == "composer-form":
            self.attrs = got


@pytest.fixture
def connection(db):
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()
    return connection


def test_a_double_send_is_dropped_and_the_controls_inside_are_not_held(
    client, db, registered, connection, make_chat
):
    """Without `hx-sync` htmx queues a second submit behind the first, which is
    the same message sent twice. `hx-sync` is inherited, so without the
    disinherit a scope switch flipped just before Enter swallows the send."""
    chat_id = make_chat()
    parser = _Form()
    parser.feed(client.get(f"/chat/{chat_id}").text)
    assert parser.attrs is not None, "the chat page has no composer form"
    assert parser.attrs.get("hx-sync") == "this:drop"
    assert "hx-sync" in parser.attrs.get("hx-disinherit", "").split()


# --- Stop ------------------------------------------------------------------------
def test_sync_does_not_re_enable_stop_while_it_is_stopping():
    """Every stream frame runs `sync()`, and it used to end in
    `button.disabled = false` -- so a pressed Stop came back a twelfth of a
    second later with nothing to say it had been pressed."""
    sync = js_function(UI, "sync")
    assert "button.disabled = false" not in sync
    assert js_says(sync, "stopping()", "button.disabled = halting")
    assert '".msg[data-stopping][sse-connect]"' in js_function(UI, "stopping")


def test_pressing_stop_marks_the_bubble_and_a_failure_undoes_it():
    click = UI_CODE[UI_CODE.index("[data-composer-action=\"stop\"]") :]
    click = click[: click.index("\n  });\n")]
    assert js_says(click, "markStopping(article, true)", "sync()", "fetch(")
    # `fetch` resolves on a 404; only a network error rejects.
    assert js_says(click, "response.ok", "failed()")
    assert ".catch(failed)" in click
    failed = js_function(UI, "failed")
    assert js_says(failed, "markStopping(article, false)", "sync()", "notify(")
    assert 't("The reply could not be stopped. Try again.")' in failed


def test_the_stopping_words_survive_the_next_status_frame():
    """`.msg__status` is an `sse-swap` target, so words written into it are gone
    at the next status frame. The note is a sibling in the row, and the live
    status is hidden while the mark is on."""
    mark = js_function(UI, "markStopping")
    assert '"data-stopping"' in mark
    assert 't("Stopping…")' in mark
    assert "row.appendChild(note)" in mark
    hide = ".msg[data-stopping] .msg__status:not(.msg__status--stopping) { display: none; }"
    assert hide in CHAT_CSS
    message = (WEB / "templates/chat/_message.html").read_text(encoding="utf-8")
    assert re.search(
        r'<div class="msg__waiting">[\s\S]*?class="msg__status" sse-swap="status"', message
    )


# --- No page-wide work on a stream frame -----------------------------------------
def test_a_stream_frame_is_recognised_by_where_it_landed():
    """Inside an article still carrying `sse-connect`. The `done` frame swaps in
    the finished article, which has none, so it is not a frame and everything
    that runs after a reply finished still runs."""
    frame = js_function(APP, "isStreamFrame")
    assert 'closest(".msg[sse-connect]")' in frame
    assert "isStreamFrame: isStreamFrame" in APP_CODE


@pytest.mark.parametrize(
    ("name", "code", "event", "on"),
    [
        ("app.js reapply", APP_CODE, "htmx:afterSettle", "document"),
        ("app.js oob reapply", APP_CODE, "htmx:oobAfterSwap", "document"),
        ("app.js afterSwap", APP_CODE, "htmx:afterSwap", "document.body"),
        ("steps.js restore", STEPS, "htmx:afterSwap", "document.body"),
        ("audio.js arrivals", js_code(AUDIO), "htmx:afterSettle", "document.body"),
        ("ui.js notify scan", UI_CODE, "htmx:afterSettle", "document"),
        ("ui.js stop sync", UI_CODE, "htmx:afterSettle", "document.body"),
    ],
)
def test_per_swap_handlers_skip_stream_frames(name, code, event, on):
    """The first lines of the handler, because the guard is the first thing it
    does: a check further down is work already done."""
    header = f'{on}.addEventListener("{event}", function (event) {{'
    openings = [
        code[match.end() : match.end() + 240]
        for match in re.finditer(re.escape(header), code)
    ]
    assert openings, f"{name}: the listener is gone"
    assert any("isStreamFrame" in opening for opening in openings), name


def test_the_thread_observer_ignores_mutations_inside_a_streaming_bubble():
    relevant = js_function(UI, "relevant")
    assert 'closest(".msg[sse-connect]")' in relevant
    watch = js_function(UI, "watch")
    assert js_says(watch, "if (relevant(records)) sync()")


def test_the_observers_look_only_at_what_was_added():
    """The body-wide observer: each added subtree once, nodes a later frame
    already replaced skipped, and never the whole document."""
    roots = js_function(APP, "addedRoots")
    assert "isConnected" in roots
    assert "contains(node)" in roots
    assert "addedRoots(records).forEach(addCodeCopy)" in APP_CODE


# --- The microphone ------------------------------------------------------------
def test_a_microphone_failure_says_which_failure_it_was():
    start = js_function(AUDIO, "startRecording")
    assert js_says(start, ".catch(function (error)", "microphoneError(error)")
    why = js_function(AUDIO, "microphoneError")
    for name in (
        "NotAllowedError",
        "SecurityError",
        "NotFoundError",
        "OverconstrainedError",
        "NotReadableError",
    ):
        assert f'"{name}"' in why, name
    # Anything else is named, not guessed at.
    assert js_says(why, 't("The microphone could not be opened (%(error)s).", { error: name')
    assert "Permission may be blocked" not in js_code(AUDIO)
