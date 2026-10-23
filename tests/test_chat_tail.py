"""Turns that arrive without the page having asked for them.

A reply can begin outside a request: `jobs.wake` writes a completion turn and
calls `generation.ensure` when a background job finishes on an idle chat. The
browser has no way to hear about it -- the only stream here is per-message and
it is opened by the `sse-connect` on an incomplete assistant bubble, which is a
bubble the page does not have, because the reply that created it started
somewhere else. So the page polls, and this is that poll.

Most of what can go wrong is a duplicate or an avalanche: a cursor the server
cannot place must never be answered with the whole transcript, because the page
still holds every one of those bubbles.
"""

from __future__ import annotations

from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from lembas.db.models import ROLE_ASSISTANT, ROLE_USER, Chat, Connection, Message, Model
from lembas.services import chat as chat_service
from lembas.services.crypto import encrypt


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


def _tail(client: TestClient, chat_id: str, after: str = ""):
    return client.get(f"/api/chats/{chat_id}/tail", params={"after": after} if after else {})


def _say(db, chat_id: str, role: str, content: str, **kwargs) -> Message:
    chat = db.get(Chat, chat_id)
    return chat_service.create_message(db, chat, role, content, **kwargs)


# --- The cursor ----------------------------------------------------------------
def test_nothing_new_is_a_204(client, db, registered, connection, make_chat):
    """A 204 and not an empty 200: htmx does not swap on a 204, where an empty
    body would still fire a swap and a settle on every open page every five
    seconds."""
    chat_id = make_chat()
    last = _say(db, chat_id, ROLE_USER, "hello")

    assert _tail(client, chat_id, last.id).status_code == 204


def test_a_turn_that_arrived_since_is_handed_back(client, db, registered, connection, make_chat):
    chat_id = make_chat()
    first = _say(db, chat_id, ROLE_USER, "hello")
    _say(db, chat_id, ROLE_ASSISTANT, "and a reply")

    response = _tail(client, chat_id, first.id)

    assert response.status_code == 200
    assert "and a reply" in response.text
    assert f"msg-{first.id}" not in response.text, "the cursor itself is not resent"


def test_no_after_returns_nothing(client, db, registered, connection, make_chat):
    """An empty `#thread` sends no cursor, and answering with the transcript
    would be the whole conversation appended to a page already showing it."""
    chat_id = make_chat()
    _say(db, chat_id, ROLE_USER, "hello")

    response = _tail(client, chat_id)

    assert response.status_code == 204
    assert "msg-" not in response.text


def test_an_unknown_after_does_not_dump_the_thread(
    client, db, registered, connection, make_chat
):
    """A rewind in another tab deletes the row the cursor names. A page whose
    history was rewritten underneath it is one only a reload can reconcile, and
    that is not this route's decision to make -- there may be a half-typed
    message in the box."""
    chat_id = make_chat()
    _say(db, chat_id, ROLE_USER, "hello")
    _say(db, chat_id, ROLE_ASSISTANT, "a reply")

    response = _tail(client, chat_id, "0" * 32)

    assert response.status_code == 204
    assert "msg-" not in response.text


def test_an_after_from_another_chat_returns_nothing(
    client, db, registered, connection, make_chat
):
    mine = make_chat()
    other = make_chat()
    elsewhere = _say(db, other, ROLE_USER, "in the other chat")
    _say(db, mine, ROLE_USER, "here")

    response = _tail(client, mine, elsewhere.id)

    assert response.status_code == 204
    assert "msg-" not in response.text


def test_another_readers_chat_is_not_found(client, db, registered, connection, make_chat):
    from lembas.db.models import User
    from lembas.security.passwords import hash_password

    stranger = User(
        email="stranger@example.com", name="Stranger", password_hash=hash_password("x" * 12)
    )
    db.add(stranger)
    db.commit()
    theirs = Chat(user_id=stranger.id)
    db.add(theirs)
    db.commit()
    last = _say(db, theirs.id, ROLE_USER, "private")

    assert _tail(client, theirs.id, last.id).status_code == 404


# --- What comes back -----------------------------------------------------------
def test_a_reply_that_started_outside_a_request_reaches_an_open_page(
    client, db, registered, connection, make_chat
):
    """The case the whole route exists for: `jobs.wake` wrote both rows and
    started a generation, and the page has neither. The assistant bubble has to
    arrive carrying its own `sse-connect`, because that shell is the only thing
    that opens a stream."""
    chat_id = make_chat()
    cursor = _say(db, chat_id, ROLE_ASSISTANT, "an earlier reply")
    _say(db, chat_id, ROLE_USER, "A background job you started has finished", machine=True)
    _say(db, chat_id, ROLE_ASSISTANT, "", complete_=False)

    response = _tail(client, chat_id, cursor.id)

    assert response.status_code == 200
    assert "A background job you started has finished" in response.text
    assert "sse-connect" in response.text
    assert "Background job" in response.text, "and not under the reader's name"


def test_a_queued_turn_is_returned_and_carries_no_streaming_shell(
    client, db, registered, connection, make_chat
):
    """A completion waiting behind a running reply is exactly what the reader
    wants to watch arrive. It is safe to deliver early because the streaming
    shell requires the assistant role, so a queued user turn can never carry one
    -- and `_queue_frames` re-renders it in place when the reply ends."""
    chat_id = make_chat()
    cursor = _say(db, chat_id, ROLE_USER, "do the thing")
    _say(db, chat_id, ROLE_USER, "a job finished", queued=True, machine=True)

    response = _tail(client, chat_id, cursor.id)

    assert response.status_code == 200
    assert "a job finished" in response.text
    assert "sse-connect" not in response.text


def test_the_tail_renders_the_same_partial_the_thread_does(client, db, registered, connection):
    """Through `_render_bubble`, so a bubble that arrived late is the same bubble
    a reload would have drawn. Four handlers already render this template and a
    fifth that did its own thing is a fifth that forgets `template_flags`."""
    from pathlib import Path

    import lembas

    source = (Path(lembas.__file__).parent / "api/chats.py").read_text(encoding="utf-8")
    body = source[source.index("async def thread_tail") : source.index("async def post_message")]
    assert "_render_bubble" in body


# --- Being here counts as reading it -------------------------------------------
def test_polling_the_page_clears_the_unread_flag(client, db, registered, connection, make_chat):
    """`_persist` marks a reply unread whenever nobody is following it, which is
    true of a job-woken reply even with the reader watching -- so the toast
    announced the chat that was already on screen."""
    chat_id = make_chat()
    last = _say(db, chat_id, ROLE_USER, "hello")
    chat = db.get(Chat, chat_id)
    chat.unread = True
    chat.unread_notified = True
    db.commit()

    _tail(client, chat_id, last.id)

    db.expire_all()
    chat = db.get(Chat, chat_id)
    assert chat.unread is False
    assert chat.unread_notified is False


def test_the_flag_is_cleared_even_when_nothing_arrived(
    client, db, registered, connection, make_chat
):
    """The claim being made is that somebody is here, not that something came --
    and the empty answer is by far the common one."""
    chat_id = make_chat()
    last = _say(db, chat_id, ROLE_USER, "hello")
    chat = db.get(Chat, chat_id)
    chat.unread = True
    db.commit()

    assert _tail(client, chat_id, last.id).status_code == 204

    db.expire_all()
    assert db.get(Chat, chat_id).unread is False


# --- Where the poller sits -----------------------------------------------------
class _Ancestry(HTMLParser):
    """The open-tag stack above the element with a given id."""

    def __init__(self, wanted: str) -> None:
        super().__init__()
        self.wanted = wanted
        self.stack: list[tuple[str, dict[str, str]]] = []
        self.found: tuple[dict[str, str], list[tuple[str, dict[str, str]]]] | None = None

    def handle_starttag(self, tag, attrs):
        got = {key: (value or "") for key, value in attrs}
        if got.get("id") == self.wanted:
            self.found = (got, list(self.stack))
        if tag not in ("br", "img", "input", "hr", "meta", "link", "source", "use", "path"):
            self.stack.append((tag, got))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def test_the_poller_is_outside_the_thread_and_outside_the_composer_form(
    client, db, registered, connection, make_chat
):
    """Both failures are silent and both have happened here before.

    Inside `#thread` it would be swapped away by the first rewind or compaction,
    which replace that container's contents, and never fire again. Inside the
    composer's form it would inherit `hx-target="#thread"` from an ancestor --
    the jobs chip did exactly that and blanked the transcript on every tick.
    """
    chat_id = make_chat()
    _say(db, chat_id, ROLE_USER, "hello")

    page = client.get(f"/chat/{chat_id}").text
    parser = _Ancestry("thread-tail")
    parser.feed(page)

    assert parser.found is not None, "the chat page has no tail poller"
    attrs, ancestors = parser.found

    ids = {got.get("id") for _tag, got in ancestors}
    assert "thread" not in ids, "a rewind would swap the poller away"
    assert not any(tag == "form" for tag, _got in ancestors), "it would inherit hx-target"

    assert attrs.get("hx-target") == "#thread"
    assert attrs.get("hx-swap") == "beforeend"
    assert attrs.get("hx-sync"), "two overlapping polls are two copies of one bubble"


def test_the_new_chat_screen_has_no_poller(client, db, registered, connection):
    """There is no row to poll: a chat is written together with its first
    message."""
    assert 'id="thread-tail"' not in client.get("/chat").text


def test_the_cursor_is_not_read_with_last_of_type():
    """`:last-of-type` is per-parent and `querySelector` returns the first match
    in document order, so on a compacted chat it answers with the last article
    inside `<details class="compacted">` rather than the newest message -- and
    the poll then asks about a message from the middle of the conversation and
    re-appends everything after it.

    Matched on the selector *as written in a selector string*, not on the words:
    the comment beside the code names `:last-of-type` in order to say why it is
    not being used, exactly as `data-prompt`'s comment names `hx-prompt`.
    """
    from pathlib import Path

    import lembas

    source = (Path(lembas.__file__).parent / "web/static/js/app.js").read_text(encoding="utf-8")
    assert ':last-of-type"' not in source
    assert ":last-of-type'" not in source
    assert 'querySelectorAll("article.msg")' in source


def test_the_poller_is_quiet_while_a_reply_is_streaming():
    """That reply delivers its own bubbles through the `done` frame, which is the
    only channel that gets the order right -- and it is the one window in which
    the transcript's order moves underneath us, since `_inject` restamps the
    placeholder."""
    from pathlib import Path

    import lembas

    source = (Path(lembas.__file__).parent / "web/static/js/app.js").read_text(encoding="utf-8")
    start = source.index("htmx:configRequest")
    block = source[start : source.index("htmx:beforeSwap", start)]
    assert "thread-tail" in block
    assert "sse-connect" in block
    assert "preventDefault" in block
    assert "parameters.after" in block


def test_a_bubble_the_page_already_has_is_never_swapped_in():
    """The race `hx-sync` cannot reach: the composer's POST committing between a
    tail request going out and its answer coming back. A duplicate here is not
    cosmetic -- it would carry a second `sse-connect` for one message."""
    from pathlib import Path

    import lembas

    source = (Path(lembas.__file__).parent / "web/static/js/app.js").read_text(encoding="utf-8")
    block = source[source.index('addEventListener("htmx:beforeSwap"') :]
    assert "shouldSwap = false" in block
    # Checked against the page by `duplicates`, which every swap into the
    # thread goes through (test_stream_browser_js.py has the rest).
    assert "duplicates(" in block
    dupes = source[source.index("function duplicates(") :]
    assert "getElementById" in dupes[: dupes.index("\n  }\n")]


def test_no_template_hands_htmx_a_string_to_evaluate():
    """Every `hx-vals` in this project is static JSON rendered server-side. A
    `js:` one would be the only string htmx ever evaluated here, and it would die
    silently under any CSP a deployment added later -- the same family as the
    `hx-prompt` rule."""
    from pathlib import Path

    import lembas

    templates = Path(lembas.__file__).parent / "web/templates"
    offenders = [
        path.relative_to(templates)
        for path in templates.rglob("*.html")
        if 'hx-vals="js:' in path.read_text(encoding="utf-8")
        or "hx-vals='js:" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, f"hx-vals with js: cannot cancel a request: {offenders}"


# --- Ordering ------------------------------------------------------------------
def test_rows_sharing_a_timestamp_do_not_stall_the_poll(
    client, db, registered, connection, make_chat
):
    """Under a bare `created_at >` a row sharing the cursor's microsecond is
    skipped forever -- returned never, passed never. The id clause is what makes
    the poll make progress instead of stopping on a row it can neither hand back
    nor step over."""
    chat_id = make_chat()
    cursor = _say(db, chat_id, ROLE_USER, "first")
    twin = _say(db, chat_id, ROLE_ASSISTANT, "same instant")
    twin.created_at = cursor.created_at
    db.commit()

    ordered = sorted([cursor.id, twin.id])
    response = _tail(client, chat_id, ordered[0])

    assert response.status_code == 200
    assert f"msg-{ordered[1]}" in response.text


def test_the_turns_come_back_in_the_order_they_happened(
    client, db, registered, connection, make_chat
):
    chat_id = make_chat()
    cursor = _say(db, chat_id, ROLE_USER, "first")
    _say(db, chat_id, ROLE_ASSISTANT, "second")
    _say(db, chat_id, ROLE_USER, "third")

    body = _tail(client, chat_id, cursor.id).text

    assert body.index("second") < body.index("third")
