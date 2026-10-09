"""The wire format every reply travels over, which had no test of its own.

`services/sse.py` is twenty lines and carries all of it. That is exactly the
kind of module that never gets one: too small to look risky, and load-bearing
enough that a subtle mistake is a stream which works until a model emits a
newline -- which is to say, until the first code block.

The events are parsed back the way the browser's `EventSource` does rather than
compared against an expected string. A test asserting the literal bytes passes
on a format that is wrong in the same way it was written.
"""

from __future__ import annotations

import pytest

from lembas.services.sse import KEEPALIVE, event


def parse(frame: str) -> tuple[str, str]:
    """Read one frame the way an `EventSource` does.

    Field lines are `name: value`, the space after the colon is stripped, and
    several `data:` lines in one event are joined with a newline. That last rule
    is the whole reason `event()` exists.
    """
    name = ""
    data: list[str] = []
    for line in frame.split("\n"):
        if line.startswith("event:"):
            name = line[len("event:") :].lstrip(" ")
        elif line.startswith("data:"):
            data.append(line[len("data:") :].lstrip(" "))
    return name, "\n".join(data)


def test_a_plain_payload_survives_the_round_trip():
    assert parse(event("render", "hello")) == ("render", "hello")


def test_a_frame_ends_with_a_blank_line():
    """The blank line is what tells the browser the event is over. Without it
    nothing is dispatched at all and the stream simply appears to hang."""
    assert event("render", "hello").endswith("\n\n")


@pytest.mark.parametrize(
    "payload",
    [
        "line one\nline two",
        "```python\nprint('hi')\n```",
        "\nleading",
        "trailing\n",
        "two\n\nblank",
        "<p>markup</p>\n<p>and more</p>",
    ],
)
def test_newlines_survive_because_they_are_split_across_data_lines(payload):
    """The failure this module exists to prevent. A raw newline inside one
    `data:` line ends that line, so everything after it is dropped -- the event
    arrives truncated, with no error anywhere, the first time a model writes a
    code block."""
    name, back = parse(event("render", payload))

    assert name == "render"
    assert back == payload


def test_every_line_of_a_multiline_payload_is_its_own_field():
    frame = event("steps", "a\nb\nc")

    assert frame.count("data: ") == 3
    assert "data: a\ndata: b\ndata: c\n" in frame


def test_an_empty_payload_is_still_a_frame():
    """Several frames must be able to blank themselves -- `metrics`, `status`,
    `ask`, `reasoning`, `think` and `render` are sent on every version bump
    *including* empty, because each has to be able to clear. An approval card
    that survived being answered would be a button you could press twice."""
    name, back = parse(event("ask", ""))

    assert name == "ask"
    assert back == ""
    assert event("ask", "").endswith("\n\n")


def test_a_payload_that_looks_like_a_field_is_not_read_as_one():
    """Model output is untrusted, and `event: done` inside a reply must stay
    text. It does because every line is prefixed -- but the reason is worth
    pinning, since the day it is not, a model can end its own stream."""
    hostile = "event: done\ndata: {}"
    name, back = parse(event("render", hostile))

    assert name == "render", "the payload took over the event name"
    assert back == hostile


def test_the_keepalive_is_a_comment_and_not_an_event():
    """It exists to stop a proxy killing an idle stream while a model thinks.
    A comment line does that without dispatching anything; an event would reach
    the page and be swapped into it."""
    assert KEEPALIVE.startswith(":")
    assert KEEPALIVE.endswith("\n\n")
    assert parse(KEEPALIVE) == ("", "")
