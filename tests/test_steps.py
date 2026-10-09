"""A reply as the sequence of steps it was.

The interesting cases are all about what a bubble does when the marks and the
three stores disagree, because that is what an old row, a half-written persist
and a hand-edited column all look like. None of them may throw: a transcript
that renders in the wrong order is a nuisance, one that will not render is a
page nobody can open.
"""

from __future__ import annotations

from types import SimpleNamespace

from lembas.services import steps
from lembas.services.markdown import open_fence


def _message(*, content="", reasoning="", events=None, marks=None, error="", ms=0):
    return SimpleNamespace(
        id="m1",
        content=content,
        reasoning=reasoning,
        reasoning_ms=ms,
        error=error,
        tool_calls_json=list(events or []),
        steps_json=list(marks or []),
    )


def _kinds(built):
    return [(step.index, step.kind) for step in built]


# --- The compatibility layout --------------------------------------------------
def test_a_reply_with_no_marks_reads_exactly_as_it_always_did():
    """Every row written before the marks existed. Thinking, then every tool
    block, then the whole answer -- which is what those bubbles have shown since
    the beginning, and there is no version flag anywhere to say so."""
    built = steps.for_message(
        _message(content="the answer", reasoning="hmm", events=[{"name": "a"}, {"name": "b"}])
    )

    assert _kinds(built) == [(0, "thinking"), (0, "tools"), (0, "text")]
    assert built[1].events == ({"name": "a"}, {"name": "b"})


def test_a_new_reply_that_called_nothing_is_the_same_list():
    """The one ambiguity in "no marks means the old layout", and it is harmless:
    with no tool blocks to sit between the prose, the old order and the new one
    are the same sequence."""
    built = steps.for_message(_message(content="hello", reasoning="hmm"))

    assert _kinds(built) == [(0, "thinking"), (0, "text")]


def test_a_failed_reply_does_not_show_its_thinking():
    built = steps.for_message(_message(content="", reasoning="hmm", error="boom"))

    assert built == []


# --- Interleaving --------------------------------------------------------------
def test_prose_either_side_of_a_tool_call_renders_either_side_of_it():
    """The whole point. This used to be one thinking block, then every tool
    block, then all the prose at the bottom -- fine on a two-round answer and
    unusable on a forty-round one."""
    built = steps.for_message(
        _message(
            content="Looking now. All fourteen pass.",
            reasoning="first thoughtsecond thought",
            events=[{"name": "shell_run"}],
            marks=[{"round": 0, "thinking_to": 13, "text_to": 12, "tools_to": 1}],
        )
    )

    assert _kinds(built) == [
        (0, "thinking"),
        (0, "text"),
        (0, "tools"),
        (1, "thinking"),
        (1, "text"),
    ]
    assert built[0].text == "first thought"
    assert "Looking now." in built[1].html
    assert built[3].text == "second thought"
    assert "fourteen pass" in built[4].html


def test_thinking_is_sliced_per_round_and_the_column_stays_whole():
    """A dozen thinking blocks in one bubble, each beside the command it led to,
    and `Message.reasoning` still the single string everything else reads."""
    message = _message(
        reasoning="round oneround two",
        content="",
        events=[{"name": "a"}],
        marks=[{"round": 0, "thinking_to": 9, "text_to": 0, "tools_to": 1}],
    )
    built = steps.for_message(message)

    assert [s.text for s in built if s.kind == "thinking"] == ["round one", "round two"]
    assert message.reasoning == "round oneround two", "the column is untouched"


def test_only_the_trailing_prose_is_marked_live():
    """`--live` draws the caret, and a caret after every paragraph that happened
    to precede a tool call is not where the reply is being written.

    Driven through the *live* path deliberately. This used to go through
    `for_message` and pass, which is precisely how a stored reply came to blink
    a cursor for ever: the test asserted the caret was in the right place
    without ever asking whether it should be there at all.
    """
    generation = _generation(
        content=["before after"],
        tool_events=[{"name": "a"}],
        steps=[{"round": 0, "thinking_to": 0, "text_to": 6, "tools_to": 1}],
    )

    assert [s.open for s in _live_steps(generation) if s.kind == "text"] == [False, True]


def test_a_step_with_nothing_in_it_produces_nothing():
    """A round that only called a tool leaves no empty prose block behind it."""
    built = steps.for_message(
        _message(
            content="",
            events=[{"name": "a"}],
            marks=[{"round": 0, "thinking_to": 0, "text_to": 0, "tools_to": 1}],
        )
    )

    assert _kinds(built) == [(0, "tools")]


# --- Offsets that disagree with the stores -------------------------------------
def test_offsets_past_the_end_are_clamped_rather_than_raising():
    built = steps.for_message(
        _message(
            content="short",
            reasoning="tiny",
            events=[{"name": "a"}],
            marks=[{"round": 0, "thinking_to": 9999, "text_to": 9999, "tools_to": 9999}],
        )
    )

    assert "short" in built[1].html
    assert built[0].text == "tiny"


def test_offsets_that_go_backwards_lose_nothing():
    """A second mark earlier than the first would slice backwards and silently
    drop text. It comes out empty instead, and the tail still arrives."""
    built = steps.for_message(
        _message(
            content="one two three",
            marks=[
                {"round": 0, "thinking_to": 0, "text_to": 8, "tools_to": 0},
                {"round": 1, "thinking_to": 0, "text_to": 2, "tools_to": 0},
            ],
        )
    )

    assert "one two" in built[0].html
    assert "three" in built[-1].html


def test_junk_in_the_column_does_not_stop_the_bubble_rendering():
    built = steps.for_message(
        _message(content="hello", marks=[{}, {"text_to": None}, {"text_to": "lots"}])
    )

    assert any("hello" in step.html for step in built)


def test_a_row_written_before_the_column_existed_reads_as_no_marks():
    message = _message(content="hello")
    message.steps_json = None

    assert _kinds(steps.for_message(message)) == [(0, "text")]


# --- Code fences across a tool call --------------------------------------------
def test_a_fence_left_open_is_closed_and_reopened_around_the_tool_call():
    """Splitting the markdown at a round boundary can leave a fence open, and
    markdown-it then runs it to the end of that segment and mispairs every later
    fence in the reply. Each piece closes its own and the next reopens it."""
    opened = "Here:\n```python\nx = 1\n"
    built = steps.for_message(
        _message(
            content=opened + "and the rest\n",
            events=[{"name": "a"}],
            marks=[{"round": 0, "thinking_to": 0, "text_to": len(opened), "tools_to": 1}],
        )
    )

    first, last = (s for s in built if s.kind == "text")
    # `code-block`, not the literal source: the fence renderer highlights, so
    # `x = 1` comes back as a run of spans.
    assert "code-block" in first.html
    assert "rest" in last.html
    assert "code-block" in last.html, "the fence carries on rather than the prose becoming code"


def test_the_carry_never_touches_the_stored_text():
    """It is a rendering device. `build_messages`, titling and the copy button
    all read `message.content`, and it has to be what the model wrote."""
    text = "```python\nx = 1\nmore"
    message = _message(
        content=text,
        marks=[{"round": 0, "thinking_to": 0, "text_to": 16, "tools_to": 0}],
    )
    steps.for_message(message)

    assert message.content == text


def test_a_fence_closed_before_the_boundary_carries_nothing():
    text = "```py\nx\n```\ndone. more"
    built = steps.for_message(
        _message(
            content=text,
            marks=[{"round": 0, "thinking_to": 0, "text_to": 18, "tools_to": 0}],
        )
    )

    assert "<pre" not in built[-1].html


def test_open_fence_reads_the_common_shapes():
    assert open_fence("nothing here") == ("", "")
    assert open_fence("a\n```python\nx = 1") == ("```", "python")
    assert open_fence("a\n```python\nx = 1\n```\nb") == ("", "")
    assert open_fence("~~~js\nx") == ("~~~", "js")
    # A fence marker inside an open fence is text, not a closer: it carries an
    # info string, and a closer never does.
    assert open_fence("```\n```python inside\n") == ("```", "")


# --- The live path -------------------------------------------------------------
def _generation(**fields):
    from lembas.services import generation as generation_service

    generation = generation_service.Generation(chat_id="c", message_id="m")
    for key, value in fields.items():
        setattr(generation, key, value)
    return generation


def test_closed_from_returns_only_what_a_follower_has_not_seen():
    """`_follow` keeps what it has rendered. A closed step never changes again,
    which is what stops a forty-round reply re-rendering its whole transcript
    twelve times a second -- the cost the old `tools` frame actually paid."""
    generation = _generation(
        content=["one ", "two "],
        tool_events=[{"name": "a"}, {"name": "b"}],
        steps=[
            {"round": 0, "thinking_to": 0, "text_to": 4, "tools_to": 1},
            {"round": 1, "thinking_to": 0, "text_to": 8, "tools_to": 2},
        ],
    )

    assert _kinds(steps.closed_from(generation, since=0)) == [
        (0, "text"),
        (0, "tools"),
        (1, "text"),
        (1, "tools"),
    ]
    assert _kinds(steps.closed_from(generation, since=1)) == [(1, "text"), (1, "tools")]


def test_closed_from_never_includes_the_step_still_being_written():
    generation = _generation(
        content=["done ", "still going"],
        steps=[{"round": 0, "thinking_to": 0, "text_to": 5, "tools_to": 0}],
    )

    assert all("still going" not in step.html for step in steps.closed_from(generation, since=0))


def test_the_tail_is_what_is_past_the_last_mark():
    generation = _generation(
        content=["closed ", "open"],
        reasoning=["thought ", "thinking"],
        steps=[{"round": 0, "thinking_to": 8, "text_to": 7, "tools_to": 0}],
    )

    assert steps.tail(generation) == ("thinking", "open")


def test_the_tail_reopens_a_fence_from_the_closed_part():
    """Otherwise the code being written mid-reply stops looking like code the
    moment a round closes underneath it."""
    generation = _generation(
        content=["```python\n", "x = 1"],
        steps=[{"round": 0, "thinking_to": 0, "text_to": 10, "tools_to": 0}],
    )

    _, text = steps.tail(generation)
    assert text.startswith("```python")


def test_a_reply_with_no_marks_has_everything_in_its_tail():
    generation = _generation(content=["all of it"], reasoning=["thinking"])

    assert steps.tail(generation) == ("thinking", "all of it")


# --- Finished means finished ---------------------------------------------------
def test_a_stored_reply_marks_nothing_as_still_being_written():
    """`msg__body--live` draws the blinking caret. `include_open` used to do two
    jobs -- emit the trailing step, and mark it live -- so every finished reply
    ending in prose blinked a cursor at the reader for ever."""
    built = steps.for_message(
        _message(
            content="before after",
            events=[{"name": "a"}],
            marks=[{"round": 0, "thinking_to": 0, "text_to": 6, "tools_to": 1}],
        )
    )

    assert [s.open for s in built] == [False] * len(built)


def test_a_stored_reply_with_no_marks_marks_nothing_either():
    """The compatibility branch had its own copy of the same flag, so every row
    written before the marks existed blinked too -- which is most of them."""
    built = steps.for_message(_message(content="An older answer."))

    assert [s.open for s in built] == [False]


def test_a_running_reply_still_marks_its_tail():
    """The other half: the caret has to be somewhere while the reply is being
    written, or there is no sign it is still going."""
    generation = _generation(content=["still going"])

    assert [s.open for s in _live_steps(generation)] == [True]


def _live_steps(generation):
    from lembas.services.steps import _build

    return _build(
        text=generation.text,
        thinking=generation.thinking,
        events=list(generation.tool_events),
        marks=list(generation.steps),
    )


# --- What a thinking block says about itself ----------------------------------
def test_a_token_count_stays_exact_until_the_digits_stop_meaning_anything():
    assert steps.format_tokens(0) == ""
    assert steps.format_tokens(86) == "86"
    assert steps.format_tokens(200) == "200"
    assert steps.format_tokens(201) == "0.2k"
    assert steps.format_tokens(1500) == "1.5k"


def test_the_live_label_is_terser_than_the_finished_one():
    """It sits beside an animating word and changes every second. "less than a
    second" flickering into "1 second" reads as a glitch, not a measurement."""
    assert steps.thinking_label(ms=6000, tokens=400, live=True) == "6s · 0.4k tokens"
    assert steps.thinking_label(ms=6000, tokens=400, live=False) == "for 6 seconds · 0.4k tokens"
    # Never "Thought for 86 tokens": with no time measured, the count is not a duration.
    assert steps.thinking_label(ms=0, tokens=86, live=False) == "· 86 tokens"
    assert steps.thinking_label(ms=64000, tokens=0, live=True) == "1m 04s"


def test_a_block_with_nothing_to_report_says_nothing():
    """Rather than "Thought for 0 seconds", which is worse than the bare word."""
    assert steps.thinking_label(ms=0, tokens=0, live=False) == ""
    assert steps.thinking_label(ms=0, tokens=0, live=True) == ""


def test_each_block_reports_its_own_round_not_the_reply():
    """The whole point of the per-round timing. It used to read the reply's
    total, so only the first block could honestly claim it and every other one
    said "Thought" and nothing else."""
    built = steps.for_message(
        _message(
            reasoning="a" * 400 + "b" * 800,
            events=[{"name": "x"}],
            marks=[
                {"round": 1, "thinking_to": 400, "text_to": 0, "tools_to": 1, "thinking_ms": 4000}
            ],
            ms=9000,
        )
    )

    labels = [s.label for s in built if s.kind == "thinking"]
    assert labels[0].startswith("for 4 seconds")
    # The last round closes no mark -- it is the one that stopped calling
    # tools -- so its duration is whatever the reply spent beyond the last mark.
    assert labels[1].startswith("for 5 seconds")


def test_a_row_with_no_per_round_timing_falls_back_to_the_reply(): 
    """Every reply written before the marks carried a duration. One block, and
    the reply's own figure is exactly right for it."""
    built = steps.for_message(_message(reasoning="a" * 400, ms=3000))

    assert built[0].label.startswith("for 3 seconds")


def test_a_running_block_carries_no_label_of_its_own():
    """The live one is fed by the `think` frame, which knows the clock. Baking a
    stale number into the markup would be a number that never moved."""
    generation = _generation(reasoning=["still thinking"])

    assert [s.label for s in _live_steps(generation)] == [""]
