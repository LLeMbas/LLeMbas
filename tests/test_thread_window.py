"""The chat page draws the newest messages; the rest arrive a page at a time."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import lembas
from lembas.db.models import Chat, Message
from lembas.services import thread as thread_service
from tests.conftest import script

STATIC = Path(lembas.__file__).parent / "web/static"
CHAT_CSS = (STATIC / "css/chat.css").read_text()


def _rows(count: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(id=f"m{i}") for i in range(count)]


# --- The window -----------------------------------------------------------------
def test_the_newest_page_and_the_page_above_it():
    rows = _rows(120)
    newest = thread_service.window(rows)
    assert [m.id for m in newest.messages] == [f"m{i}" for i in range(70, 120)]
    assert newest.earlier == "m70" and newest.above == 70

    above = thread_service.window(rows, before=newest.earlier)
    assert [m.id for m in above.messages] == [f"m{i}" for i in range(20, 70)]
    top = thread_service.window(rows, before=above.earlier)
    assert [m.id for m in top.messages] == [f"m{i}" for i in range(0, 20)]
    assert top.earlier == "" and top.above == 0


def test_a_short_chat_is_drawn_whole():
    part = thread_service.window(_rows(7))
    assert len(part.messages) == 7 and part.earlier == ""
    assert thread_service.window([]).messages == []


def test_a_link_to_a_message_draws_from_it_to_the_end():
    rows = _rows(300)
    part = thread_service.window(rows, at="m40")
    # A little before it, and everything after: nothing between the message
    # and the newest is missing from the page.
    assert part.messages[0].id == f"m{40 - thread_service.LEAD}"
    assert part.messages[-1].id == "m299" and part.earlier == part.messages[0].id
    # One already inside the newest page changes nothing.
    assert thread_service.window(rows, at="m290").messages[0].id == "m250"
    # A stale id: the newest page, not an error.
    assert thread_service.window(rows, at="gone").messages[0].id == "m250"


# --- The page ---------------------------------------------------------------------
def _model(db) -> None:
    """A model to talk to: without one the page is the "connect a model"
    screen and draws no thread at all."""
    from lembas.db.models import Connection, Model
    from lembas.services.crypto import encrypt

    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="test-model"))
    db.commit()


def _long_chat(db, chat_id: str, count: int = 120) -> list[str]:
    base = datetime.now(UTC) - timedelta(hours=1)
    ids = []
    for i in range(count):
        row = Message(
            chat_id=chat_id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"turn number {i} here",
            created_at=base + timedelta(seconds=i),
        )
        db.add(row)
        db.flush()
        ids.append(row.id)
    db.commit()
    return ids


def _bubbles(html: str) -> list[str]:
    return re.findall(r'<article[^>]*?\sid="msg-([0-9a-f]+)"', html)


def test_the_chat_page_draws_the_newest_fifty(client, db, registered, make_chat):
    _model(db)
    chat_id = make_chat()
    ids = _long_chat(db, chat_id)

    page = client.get(f"/chat/{chat_id}").text
    assert _bubbles(page) == ids[-50:]
    assert f'hx-get="/api/chats/{chat_id}/earlier?before={ids[-50]}"' in page
    assert "Earlier messages" in page


def test_earlier_messages_come_a_page_at_a_time_down_to_the_first(
    client, db, registered, make_chat
):
    chat_id = make_chat()
    ids = _long_chat(db, chat_id)

    above = client.get(f"/api/chats/{chat_id}/earlier?before={ids[-50]}").text
    assert _bubbles(above) == ids[20:70]
    # It begins with the row that fetches the page above it.
    assert above.index('id="thread-earlier"') < above.index(f'id="msg-{ids[20]}"')
    top = client.get(f"/api/chats/{chat_id}/earlier?before={ids[20]}").text
    assert _bubbles(top) == ids[:20]
    assert "thread-earlier" not in top

    # A cursor from another chat, or one a rewind deleted: nothing, not the lot.
    other = make_chat()
    assert client.get(f"/api/chats/{other}/earlier?before={ids[20]}").text == ""
    assert client.get(f"/api/chats/{chat_id}/earlier?before=nope").text == ""
    assert client.get("/api/chats/" + "0" * 32 + "/earlier").status_code == 404


def test_the_summarised_turns_are_at_the_very_top_only(client, db, registered, make_chat):
    chat_id = make_chat()
    ids = _long_chat(db, chat_id)
    chat = db.get(Chat, chat_id)
    chat.compact_summary = "What was said."
    chat.compacted_through_id = ids[9]
    chat.compacted_at = datetime.now(UTC)
    db.commit()

    page = client.get(f"/chat/{chat_id}").text
    assert 'class="compacted"' not in page
    top = client.get(f"/api/chats/{chat_id}/earlier?before={ids[60]}").text
    assert top.index('class="compacted"') < top.index(f'id="msg-{ids[10]}"')
    assert "thread-earlier" not in top


def test_a_search_hit_opens_the_chat_on_the_message_that_matched(
    client, db, registered, make_chat
):
    """The page holds the newest messages, and a hit from long ago is not one
    of them: the link names the message, and the page draws from it on."""
    _model(db)
    chat_id = make_chat()
    ids = _long_chat(db, chat_id, count=200)

    from lembas.api.search import _search_chats
    from lembas.db.models import User

    hits = _search_chats(db, db.query(User).first(), "turn number 30 here", 5)
    assert hits and hits[0]["url"] == f"/chat/{chat_id}?at={ids[30]}"

    page = client.get(hits[0]["url"]).text
    shown = _bubbles(page)
    assert shown[0] == ids[30 - thread_service.LEAD] and shown[-1] == ids[-1]
    assert f'data-jump="msg-{ids[30]}"' in page
    # Without the link the page says nothing of the kind.
    assert "data-jump" not in client.get(f"/chat/{chat_id}").text


def test_a_rewind_answers_with_the_newest_page_too(client, db, registered, make_chat):
    from lembas.api.chats import _thread_context
    from lembas.db.models import User

    chat_id = make_chat()
    ids = _long_chat(db, chat_id)
    context = _thread_context(db, db.get(Chat, chat_id), db.query(User).first())
    assert [m.id for m in context["messages"]] == ids[-50:]
    assert context["earlier"] == ids[-50]


# --- The browser's side -------------------------------------------------------------
def test_the_page_presses_earlier_when_the_reader_nears_it_and_keeps_their_place():
    app = script("app.js")
    assert 'window.htmx.trigger(button, "lembas:earlier")' in app
    # The distance from the bottom, taken before the page lands and put back
    # after: the same place whatever was added above.
    assert "keepFromBottom = thread.scrollHeight - thread.scrollTop" in app
    assert "thread.scrollHeight - from" in app
    assert 'addEventListener("htmx:beforeSwap", noteEarlier)' in app
    assert 'addEventListener("htmx:afterSettle", placeEarlier)' in app
    thread = (Path(lembas.__file__).parent / "web/templates/chat/_thread.html").read_text()
    assert 'hx-trigger="click, lembas:earlier"' in thread
    assert 'hx-target="#thread-earlier" hx-swap="outerHTML"' in thread


def test_finished_replies_off_screen_are_not_laid_out():
    code = re.sub(r"/\*.*?\*/", "", CHAT_CSS, flags=re.S)
    rule = code[code.index(".msg:not([sse-connect]) .msg__steps") :]
    rule = rule[: rule.index("}")]
    assert "content-visibility: auto" in rule
    assert "contain-intrinsic-size: auto" in rule
    # Only where the browser anchors the scroll, and anchoring only while the
    # reader is not following a reply.
    assert "@supports (overflow-anchor: auto)" in code
    assert re.search(r"\.thread-scroll\.is-reading\s*\{\s*overflow-anchor: auto", code)
    assert 'thread.classList.toggle("is-reading", !stick)' in script("app.js")


def test_typing_measures_and_repaints_once_a_frame():
    app = script("app.js")
    composer = script("composer.js")
    assert 'if (event.target.matches("[data-autosize]")) autosizeSoon(event.target);' in app
    soon = app[app.index("function autosizeSoon") :]
    assert "requestAnimationFrame" in soon[: soon.index("/* --- Copy")]
    paint_soon = composer[composer.index("function paintSoon") :]
    assert "requestAnimationFrame" in paint_soon[:400]
    listener = composer[composer.index('document.addEventListener("input"') :][:200]
    assert "paintSoon();" in listener and "paint();" not in listener


# --- A cursor the conversation moved under ------------------------------------------
def test_earlier_above_a_turn_summarised_since_is_the_summary_never_the_newest_page(
    client, db, registered, make_chat
):
    """The page holds an "Earlier messages" row naming its oldest bubble. A
    compaction then summarises that bubble. The row's fetch used to find its
    cursor "not in the list" and get the newest page again: every bubble on
    the page twice, and a second stream on a reply being written."""
    from lembas.services import compaction

    _model(db)
    chat_id = make_chat()
    ids = _long_chat(db, chat_id, count=70)
    page = client.get(f"/chat/{chat_id}").text
    assert f"earlier?before={ids[20]}" in page

    chat = db.get(Chat, chat_id)
    # Summarised up to the last complete reply: the cursor with them.
    compaction.apply(chat, summary="What was said.", upto=db.get(Message, ids[-1]))
    db.commit()

    answer = client.get(f"/api/chats/{chat_id}/earlier?before={ids[20]}").text
    live = answer.split("</details>")[-1]
    assert 'id="msg-' not in live, "bubbles the page already holds were sent again"
    assert "sse-connect" not in answer and "thread-earlier" not in answer
    # What is above it now is the summarised turns, behind their divider.
    assert 'class="compacted"' in answer

    # The service says the same of any cursor that is not in the list.
    rows = _rows(10)
    assert thread_service.window(rows, before="gone").messages == []


def test_earlier_above_a_message_taken_into_a_reply_is_what_is_above_it(
    client, db, registered, make_chat
):
    _model(db)
    chat_id = make_chat()
    ids = _long_chat(db, chat_id, count=70)
    # The page's oldest bubble becomes an inline steer of the next reply.
    reply = db.get(Message, ids[21])
    reply.steps_json = [{"round": 0, "thinking_to": 0, "text_to": 0, "tools_to": 0,
                         "thinking_ms": 0, "steer": [{"id": ids[20], "text": "and this"}]}]
    db.commit()

    answer = client.get(f"/api/chats/{chat_id}/earlier?before={ids[20]}").text
    assert _bubbles(answer) == ids[:20]


def test_a_search_hit_in_a_summarised_turn_opens_on_it(client, db, registered, make_chat):
    """`?at=` named a message that is behind the divider: the page used to
    open at the bottom with nothing marked."""
    from lembas.services import compaction

    _model(db)
    chat_id = make_chat()
    ids = _long_chat(db, chat_id, count=120)
    chat = db.get(Chat, chat_id)
    compaction.apply(chat, summary="What was said.", upto=db.get(Message, ids[59]))
    db.commit()

    page = client.get(f"/chat/{chat_id}?at={ids[30]}").text
    assert f'data-jump="msg-{ids[30]}"' in page
    block = page[page.index('<details class="compacted"'):]
    assert block.startswith('<details class="compacted" open>')
    assert f'id="msg-{ids[30]}"' in block[: block.index("</details>")]
    # Without the link the block is folded and the page is the newest one.
    plain = client.get(f"/chat/{chat_id}").text
    assert '<details class="compacted" open>' not in plain and "data-jump" not in plain
