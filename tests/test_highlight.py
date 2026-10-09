"""Marking `@mentions` in a message somebody wrote.

This is the one render path where a person controls the bytes exactly, and
until now there was no render path at all -- the template printed the column
and let `white-space: pre-wrap` carry the newlines. So the first half of every
test here is that escaping still happens, and happens *before* anything is
injected.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lembas.services.markdown import highlight_tokens


def test_a_mention_is_marked():
    assert highlight_tokens("look at @src/main.py") == (
        'look at <span class="tok-mention">@src/main.py</span>'
    )


def test_a_mention_at_the_start_is_marked():
    assert highlight_tokens("@README.md is wrong") == (
        '<span class="tok-mention">@README.md</span> is wrong'
    )


def test_an_email_address_is_not_a_mention():
    """The whole reason the pattern is anchored on whitespace. Without it every
    address in a message becomes a highlighted file reference."""
    assert "tok-mention" not in highlight_tokens("write to frodo@shire.test")


def test_a_bare_at_is_left_alone():
    assert "tok-mention" not in highlight_tokens("dinner @ 8")


def test_several_mentions_are_all_marked():
    marked = highlight_tokens("@a.py and @b.py")

    assert marked.count("tok-mention") == 2


@pytest.mark.parametrize(
    "hostile",
    [
        "<script>alert(1)</script>",
        "@<script>alert(1)</script>",
        "</span><img src=x onerror=alert(1)>",
        "@a\"><script>alert(1)</script>",
    ],
)
def test_markup_is_escaped_before_anything_is_injected(hostile):
    """The order is the security property. Injecting first and escaping after
    would escape our own span; escaping first means the span is the only markup
    that can exist in the output."""
    out = highlight_tokens(hostile)

    assert "<script" not in out
    assert "<img" not in out
    # The only tags in the result are ours.
    assert out.replace('<span class="tok-mention">', "").replace("</span>", "").count("<") == 0


def test_an_ampersand_survives_as_an_entity():
    assert highlight_tokens("a & b") == "a &amp; b"


def test_newlines_are_untouched():
    """They are carried by `white-space: pre-wrap`, not by markup. Turning them
    into <br> here would double up with the CSS."""
    assert highlight_tokens("one\ntwo") == "one\ntwo"


def test_empty_is_empty():
    assert highlight_tokens("") == ""


# --- Through the page ---------------------------------------------------------
def _model(db):
    """A model, without which index.html renders the "no models available"
    screen *instead of* the thread -- so an assertion about message markup
    would be made against a page that has no messages on it."""
    from lembas.db.models import Connection, Model

    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m"))
    db.commit()


def test_a_sent_mention_is_marked_in_the_transcript(
    client: TestClient, db, registered, make_chat
):
    from lembas.db.models import ROLE_USER, Chat, Message

    _model(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    db.add(Message(chat_id=chat.id, role=ROLE_USER, content="check @src/main.py please"))
    db.commit()

    body = client.get(f"/chat/{chat_id}").text

    assert '<span class="tok-mention">@src/main.py</span>' in body


def test_copying_a_message_still_yields_what_was_typed(
    client: TestClient, db, registered, make_chat
):
    """The hidden copy source stays raw. Copy must give back the text, not the
    markup wrapped round it."""
    from lembas.db.models import ROLE_USER, Chat, Message

    _model(db)
    chat_id = make_chat()
    chat = db.get(Chat, chat_id)
    message = Message(chat_id=chat.id, role=ROLE_USER, content="check @src/main.py")
    db.add(message)
    db.commit()

    body = client.get(f"/chat/{chat_id}").text
    source = body[body.index(f'id="msg-body-{message.id}"') :][:200]

    assert "tok-mention" not in source
    assert "@src/main.py" in source
