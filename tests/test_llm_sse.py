"""The one SSE reader (services/llm/sse.py), and what its callers make of errors
inside a 200 stream (quirks.json `error-in-200-stream`, `model-unloaded`) and of
tool-call fragments without an index (`tool-call-without-index`)."""

from __future__ import annotations

import json

import httpx
import pytest

from lembas.services.llm import sse
from lembas.services.llm.openai_client import (
    Endpoint,
    LLMError,
    StreamError,
    ToolCallAccumulator,
    stream_chat,
)


def frames(text: str) -> list[tuple[str, str]]:
    return list(sse.parse_sse(text.splitlines()))


# --- The parser ---------------------------------------------------------------
def test_data_lines_are_joined_with_newlines_until_a_blank_line():
    text = 'data: {"a":\ndata:  1}\n\ndata: x\n\n'
    assert frames(text) == [("", '{"a":\n 1}'), ("", "x")]


def test_the_event_name_rides_with_its_data_and_resets():
    text = 'event: ping\ndata: {}\n\ndata: {"b":2}\n\n'
    assert frames(text) == [("ping", "{}"), ("", '{"b":2}')]


def test_an_unterminated_last_line_is_a_frame_like_any_other():
    assert frames('data: {"a":1}\n\ndata: {"b":2}') == [("", '{"a":1}'), ("", '{"b":2}')]


def test_crlf_line_endings_are_read():
    lines = ['data: {"a":1}\r', "\r", "data: [DONE]\r"]
    assert list(sse.parse_sse(lines)) == [("", '{"a":1}'), ("", "[DONE]")]


def test_comments_are_passed_on_as_comments():
    assert frames(": keepalive\n\ndata: x\n\n") == [(sse.COMMENT, " keepalive"), ("", "x")]


def test_a_bare_error_line_inside_the_stream_is_an_error_frame():
    """llama.cpp behind llama-swap: no `data:`, and the stream's last line, with
    no newline after it. By the letter of the spec it is a field called
    `{"error"`, and ignoring it ends the reply as an empty one."""
    text = 'data: {"choices":[]}\n\n{"error":{"code":500,"message":"boom"}}'
    assert frames(text) == [
        ("", '{"choices":[]}'),
        (sse.ERROR, '{"error":{"code":500,"message":"boom"}}'),
    ]


def test_a_bare_error_line_flushes_the_frame_before_it():
    text = 'data: {"a":1}\n{"error":"x"}\n'
    assert frames(text) == [("", '{"a":1}'), (sse.ERROR, '{"error":"x"}')]


def test_a_bare_json_line_without_an_error_member_is_not_a_frame():
    assert frames('{"choices":[]}\n\ndata: x\n\n') == [("", "x")]


def test_an_error_field_is_an_error_frame_too():
    assert frames("error: model unloaded\n\n") == [(sse.ERROR, "model unloaded")]


def test_a_servers_event_named_error_is_an_ordinary_named_frame():
    """Anthropic names its error frames `error`; the reader's own names begin
    with a colon, and an `event:` line can never produce one."""
    text = 'event: error\ndata: {"type":"error"}\n\nevent: :error\ndata: x\n\n'
    assert frames(text) == [("error", '{"type":"error"}'), ("", "x")]


def test_frames_without_a_blank_line_between_them_are_not_joined():
    assert frames('data: {"a":1}\ndata: {"b":2}\n\n') == [("", '{"a":1}'), ("", '{"b":2}')]


def test_other_fields_and_empty_frames_are_dropped():
    assert frames("id: 7\nretry: 100\n\nevent: x\n\n") == []


async def test_iter_sse_reads_a_response_to_its_unterminated_end():
    async def body():
        yield b'data: {"a":1}\n\n'
        yield b'{"error":{"message":"late"}}'

    response = httpx.Response(200, content=body())
    out = [frame async for frame in sse.iter_sse(response.aiter_lines())]
    assert out == [("", '{"a":1}'), (sse.ERROR, '{"error":{"message":"late"}}')]


def test_error_status_reads_every_shape_seen():
    assert sse.error_status({"code": 503}) == 503
    assert sse.error_status({"status": 429}) == 429
    assert sse.error_status({"type": "server_error", "code": "internal_error"}) == 500
    assert sse.error_status({"code": "INTERNAL"}) == 500
    assert sse.error_status({"type": "overloaded_error"}) == 500
    assert sse.error_status({"message": "x"}) is None
    assert sse.error_status("x") is None


def test_error_object_and_message():
    assert sse.error_object('{"error":{"message":"m"}}') == {"error": {"message": "m"}}
    assert sse.error_object("plain words") == {"error": "plain words"}
    assert sse.error_message({"message": " m "}) == "m"
    assert sse.error_message({"type": "t"}) == '{"type": "t"}'
    assert sse.error_message("s") == "s"


# --- The OpenAI client -------------------------------------------------------
ENDPOINT = Endpoint("http://h", "", {})
PAYLOAD = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}


def _chunk(text: str) -> str:
    return f"data: {json.dumps({'choices': [{'delta': {'content': text}}]})}\n\n"


async def _collect(payload=PAYLOAD) -> list[dict]:
    return [chunk async for chunk in stream_chat(ENDPOINT, payload)]


def _server(bodies: list[str]):
    """Answers each request with the next body; records how many were made."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        body = bodies[min(len(calls), len(bodies)) - 1]
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    return handler, calls


async def test_an_error_framed_as_data_is_the_error_it_is(mock_http):
    body = 'data: {"error":{"code":400,"message":"context too long"}}\n\ndata: [DONE]\n\n'
    handler, calls = _server([body])
    mock_http(handler)
    with pytest.raises(StreamError) as caught:
        await _collect()
    assert caught.value.status_code == 400
    assert "context too long" in caught.value.message
    # A 400 inside the stream is about the reply, not the request: the
    # stream_options retry does not fire for it.
    assert len(calls) == 1


async def test_a_bare_error_as_the_unterminated_last_line_is_raised_after_the_text(mock_http):
    body = _chunk("Hel") + '{"error":{"code":500,"message":"boom"}}'
    handler, calls = _server([body])
    mock_http(handler)
    got: list[dict] = []
    with pytest.raises(LLMError) as caught:
        async for chunk in stream_chat(ENDPOINT, PAYLOAD):
            got.append(chunk)
    assert got and got[0]["choices"][0]["delta"]["content"] == "Hel"
    assert caught.value.status_code == 500
    assert "boom" in caught.value.message
    assert len(calls) == 1  # output had been streamed: no retry


async def test_a_model_unloaded_frame_before_any_output_is_retried_once(mock_http):
    """llama-swap, when another client swapped the model out: `type:
    server_error` with a string code is read as the 500 it is."""
    unloaded = (
        'data: {"error":{"message":"group: model unloaded","type":"server_error",'
        '"code":"internal_error"}}\n\n'
    )
    handler, calls = _server([unloaded, _chunk("ok") + "data: [DONE]\n\n"])
    mock_http(handler)
    chunks = await _collect()
    assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["ok"]
    assert len(calls) == 2


async def test_a_server_error_is_retried_once_and_then_raised(mock_http):
    unloaded = 'data: {"error":{"message":"model unloaded","type":"server_error"}}\n\n'
    handler, calls = _server([unloaded, unloaded])
    mock_http(handler)
    with pytest.raises(LLMError) as caught:
        await _collect()
    assert caught.value.status_code == 500
    assert len(calls) == 2


async def test_a_plain_stream_still_reads_as_before(mock_http):
    body = ": keepalive\n\n" + _chunk("a") + _chunk("b") + "data: [DONE]\n\n" + _chunk("never")
    handler, _calls = _server([body])
    mock_http(handler)
    chunks = await _collect()
    assert [c["choices"][0]["delta"]["content"] for c in chunks] == ["a", "b"]


# --- Tool-call reassembly ------------------------------------------------------
def test_a_fragment_without_an_index_continues_the_last_call():
    acc = ToolCallAccumulator()
    acc.feed([{"index": 0, "id": "a", "function": {"name": "read", "arguments": "{}"}}])
    acc.feed([{"index": 1, "id": "b", "function": {"name": "grep", "arguments": '{"q":'}}])
    acc.feed([{"function": {"arguments": '"x"}'}}])
    assert acc.calls == [
        {"id": "a", "name": "read", "arguments": "{}"},
        {"id": "b", "name": "grep", "arguments": '{"q":"x"}'},
    ]


def test_the_first_fragment_without_an_index_starts_call_zero():
    acc = ToolCallAccumulator()
    acc.feed([{"id": "a", "function": {"name": "read", "arguments": "{"}}])
    acc.feed([{"function": {"arguments": "}"}}])
    assert acc.calls == [{"id": "a", "name": "read", "arguments": "{}"}]


def test_calls_are_ordered_by_index_and_given_an_id_when_the_server_sent_none():
    acc = ToolCallAccumulator()
    acc.feed([{"index": 1, "function": {"name": "b"}}, {"index": 0, "function": {"name": "a"}}])
    acc.feed([{"index": 2, "function": {"name": "", "arguments": "x"}}])  # nameless: not a call
    assert [c["id"] for c in acc.calls] == ["call_0", "call_1"]
    assert bool(acc)
    assert not ToolCallAccumulator()
