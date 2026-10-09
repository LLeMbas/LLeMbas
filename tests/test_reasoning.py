"""Splitting a reasoning model's thinking from its answer."""

from __future__ import annotations

import pytest

from lembas.services.llm.openai_client import delta_reasoning
from lembas.services.reasoning import (
    CONTENT,
    REASONING,
    ReasoningSplitter,
    format_duration,
    strip_reasoning,
)


def run(chunks: list[str]) -> tuple[str, str]:
    """Feed chunks through the splitter and return (answer, reasoning)."""
    splitter = ReasoningSplitter()
    answer, thinking = [], []
    for chunk in chunks:
        for kind, piece in splitter.feed(chunk):
            (thinking if kind == REASONING else answer).append(piece)
    for kind, piece in splitter.flush():
        (thinking if kind == REASONING else answer).append(piece)
    return "".join(answer), "".join(thinking)


# --- The dedicated field -----------------------------------------------------
def test_reasoning_content_field():
    chunk = {"choices": [{"delta": {"reasoning_content": "hmm"}}]}
    assert delta_reasoning(chunk) == "hmm"


def test_plain_reasoning_field_is_also_accepted():
    assert delta_reasoning({"choices": [{"delta": {"reasoning": "hmm"}}]}) == "hmm"


def test_no_reasoning_field():
    assert delta_reasoning({"choices": [{"delta": {"content": "hi"}}]}) == ""


@pytest.mark.parametrize("chunk", [{}, {"choices": []}, {"choices": [{"delta": {}}]}])
def test_delta_reasoning_tolerates_junk(chunk):
    assert delta_reasoning(chunk) == ""


# --- Inline <think> tags -----------------------------------------------------
def test_plain_content_passes_straight_through():
    assert run(["Hello ", "world"]) == ("Hello world", "")


def test_think_block_is_extracted():
    answer, thinking = run(["<think>weighing it up</think>The answer is 6."])
    assert answer == "The answer is 6."
    assert thinking == "weighing it up"


def test_tag_split_across_chunks():
    """The tag arrives in pieces, which is the whole reason this is a stream
    machine and not a regex."""
    answer, thinking = run(["<th", "ink>rea", "soning</thi", "nk>done"])
    assert answer == "done"
    assert thinking == "reasoning"


def test_single_character_chunks():
    source = "<think>abc</think>xyz"
    assert run(list(source)) == ("xyz", "abc")


def test_thinking_variant_is_not_mistaken_for_think():
    answer, thinking = run(["<thinking>deep</thinking>shallow"])
    assert answer == "shallow"
    assert thinking == "deep"


def test_content_before_and_after_a_think_block():
    answer, thinking = run(["before <think>mid</think> after"])
    assert answer == "before  after"
    assert thinking == "mid"


def test_unterminated_think_block_flushes_as_reasoning():
    """A truncated stream must not lose the partial thinking."""
    answer, thinking = run(["<think>never closed"])
    assert answer == ""
    assert thinking == "never closed"


def test_newlines_survive():
    answer, thinking = run(["<think>a\nb</think>c\nd"])
    assert thinking == "a\nb"
    assert answer == "c\nd"


def test_a_lone_angle_bracket_is_not_swallowed():
    assert run(["5 < 6 and 7 > 3"]) == ("5 < 6 and 7 > 3", "")


def test_no_output_is_withheld_at_the_end():
    """Whatever is buffered for a possible partial tag must be released on
    flush, or the last few characters of every reply would vanish."""
    answer, _ = run(["the end<thi"])
    assert answer == "the end<thi"


def test_emits_incrementally_rather_than_only_at_the_end():
    """Buffering the whole reply would defeat the point of streaming."""
    splitter = ReasoningSplitter()
    emitted = list(splitter.feed("a fairly long stretch of ordinary answer text"))
    assert emitted, "nothing emitted before flush"
    assert emitted[0][0] == CONTENT


# --- Whole strings -----------------------------------------------------------
def test_strip_reasoning_round_trip():
    answer, thinking = strip_reasoning("<think>because</think>Therefore 42.")
    assert (answer, thinking) == ("Therefore 42.", "because")


def test_strip_reasoning_leaves_plain_text_alone():
    assert strip_reasoning("just an answer") == ("just an answer", "")


# --- Duration phrasing -------------------------------------------------------
@pytest.mark.parametrize(
    ("milliseconds", "expected"),
    [
        (0, ""),
        (-5, ""),
        (400, "less than a second"),
        (1000, "1 second"),
        (8200, "8 seconds"),
        (60000, "1 minute"),
        (95000, "1m 35s"),
    ],
)
def test_format_duration(milliseconds, expected):
    assert format_duration(milliseconds) == expected
