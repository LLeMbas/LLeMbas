"""Anthropic connections and provider names.

A connection has a type -- OpenAI's dialect, or Anthropic's Messages API -- and a
provider name, the first half of the `provider/model` id `/v1` serves its models
under. The Anthropic side is a translation both ways, so every test here talks to
a fake Anthropic server (an httpx MockTransport): the request it receives is the
assertion, and the chunks the translation yields are read back with the same
readers the generation loop uses. No test calls the real API.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from lembas.db.models import Chat, Connection, Model, Usage, User
from lembas.security import api_tokens
from lembas.services import device_link
from lembas.services import tools as tools_service
from lembas.services.crypto import encrypt
from lembas.services.llm import anthropic_client
from lembas.services.llm.openai_client import (
    Endpoint,
    LLMError,
    assistant_blocks,
    chunk_usage,
    complete,
    context_from,
    delta_reasoning,
    delta_text,
    delta_tool_calls,
    fetch_chat_template,
    finish_reason,
    list_models,
    stream_chat,
)

ANTHROPIC = Endpoint(
    "https://api.anthropic.com", "sk-ant-test", {}, name="Anthropic", kind="anthropic"
)
OPUS = "claude-opus-6"
SONNET_45 = "claude-sonnet-4-5"


def _build(payload, endpoint=ANTHROPIC, stream=True):
    return anthropic_client.build_request(endpoint, payload, stream=stream)


def _sse(*events) -> bytes:
    lines = []
    for event in events:
        lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def _collect(endpoint, payload):
    async def run():
        return [chunk async for chunk in stream_chat(endpoint, payload)]

    return asyncio.run(run())


# --- 1. The request ------------------------------------------------------------------
def test_system_messages_go_to_the_top_and_turns_of_one_role_merge():
    body = _build(
        {
            "model": OPUS,
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "One."},
                {"role": "user", "content": [{"type": "text", "text": "Two."}]},
                {"role": "assistant", "content": "Yes."},
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Three."},
            ],
        }
    )
    assert [b["text"] for b in body["system"]] == ["You are helpful.", "Be brief."]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert [b["text"] for b in body["messages"][0]["content"]] == ["One.", "Two."]


def test_images_become_base64_blocks_and_empty_text_is_dropped():
    body = _build(
        {
            "model": OPUS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": ""},
                        {"type": "text", "text": "What is this?"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                        {"type": "image_url", "image_url": {"url": "https://x.test/a.jpg"}},
                    ],
                }
            ],
        }
    )
    blocks = body["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "What is this?"}
    assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}
    assert blocks[2]["source"] == {"type": "url", "url": "https://x.test/a.jpg"}


def test_tools_tool_calls_and_tool_results_translate():
    body = _build(
        {
            "model": OPUS,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "description": "Read a file.",
                        "parameters": {
                            "type": "object", "properties": {"path": {"type": "string"}}
                        },
                    },
                }
            ],
            "messages": [
                {"role": "user", "content": "Read both."},
                {
                    "role": "assistant",
                    "content": "Reading.",
                    "tool_calls": [
                        {"id": "call.1", "type": "function",
                         "function": {"name": "read", "arguments": '{"path": "a"}'}},
                        {"id": "call_2", "type": "function",
                         "function": {"name": "read", "arguments": '{"path": "b"}'}},
                    ],
                },
                {"role": "tool", "tool_call_id": "call.1", "name": "read", "content": "A"},
                {"role": "tool", "tool_call_id": "call_2", "name": "read", "content": "B"},
                {"role": "user", "content": "And now?"},
            ],
        }
    )
    assert body["tools"] == [
        {
            "name": "read",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            "description": "Read a file.",
        }
    ]
    assistant = body["messages"][1]
    assert assistant["content"][0] == {"type": "text", "text": "Reading."}
    assert assistant["content"][1] == {
        "type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "a"}
    }
    # Both results and the message typed meanwhile are one user turn, results first.
    results = body["messages"][2]
    assert results["role"] == "user"
    assert [b["type"] for b in results["content"]] == ["tool_result", "tool_result", "text"]
    assert results["content"][0]["tool_use_id"] == "call_1"
    assert results["content"][0]["content"] == "A"


HI = [{"role": "user", "content": "Hi"}]


def test_max_tokens_is_always_set_and_never_above_the_models_ceiling():
    assert _build({"model": OPUS, "messages": HI})["max_tokens"] == 32000
    limited = Endpoint("https://api.anthropic.com", "", {}, kind="anthropic",
                       output_limits=((OPUS, 128000), ("claude-haiku-4-5", 8000)))
    # The ceiling caps; it is not what a request asks for by default.
    assert _build({"model": OPUS, "messages": HI}, limited)["max_tokens"] == 32000
    assert _build({"model": OPUS, "max_tokens": 900, "messages": HI}, limited)["max_tokens"] == 900
    assert _build({"model": OPUS, "max_tokens": 100000, "messages": HI}, limited)[
        "max_tokens"] == 100000
    asked_too_much = {"model": "claude-haiku-4-5", "max_tokens": 64000, "messages": HI}
    assert _build(asked_too_much, limited)["max_tokens"] == 8000
    assert _build({"model": "claude-haiku-4-5", "messages": HI}, limited)["max_tokens"] == 8000


def test_max_tokens_leaves_room_for_the_prompt_in_the_context_window():
    small = Endpoint("https://api.anthropic.com", "", {}, kind="anthropic",
                     context_limits=((OPUS, 20000),))
    long = [{"role": "user", "content": "word " * 4000}]  # 20k characters, ~5k tokens
    room = _build({"model": OPUS, "messages": long}, small)["max_tokens"]
    assert 10000 < room < 20000


@pytest.mark.parametrize(("effort", "budget"), [("low", 4000), ("medium", 12000), ("high", 30976)])
def test_an_effort_is_a_thinking_budget_on_a_model_that_takes_one(effort, budget):
    body = _build(
        {"model": SONNET_45, "reasoning_effort": effort, "temperature": 0.3, "top_p": 0.9,
         "messages": [{"role": "user", "content": "Hi"}]}
    )
    # Below max_tokens (32000), with room left for the answer.
    assert body["thinking"] == {"type": "enabled", "budget_tokens": budget}
    assert "temperature" not in body and "top_p" not in body
    assert "output_config" not in body


def test_a_budget_that_cannot_fit_means_no_thinking_and_sampling_survives():
    body = _build({"model": SONNET_45, "reasoning_effort": "high", "max_tokens": 1500,
                   "temperature": 0.3, "messages": [{"role": "user", "content": "Hi"}]})
    assert "thinking" not in body and body["temperature"] == 0.3
    plain = _build({"model": SONNET_45, "temperature": 1.7,
                    "messages": [{"role": "user", "content": "Hi"}]})
    assert plain["temperature"] == 1.0


def test_the_newest_models_think_adaptively_and_take_no_sampling_or_forced_tool():
    tools = [{"type": "function", "function": {"name": "draw", "parameters": {"type": "object"}}}]
    body = _build(
        {"model": OPUS, "reasoning_effort": "high", "temperature": 0.2, "tools": tools,
         "tool_choice": {"type": "function", "function": {"name": "draw"}},
         "chat_template_kwargs": {"reasoning_effort": "high"},
         "messages": [{"role": "user", "content": "Draw."}]}
    )
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "high"}
    assert "temperature" not in body and "chat_template_kwargs" not in body
    assert body["tool_choice"] == {"type": "auto"}
    # Without an effort, a 5.x model still thinks; it is shown rather than hidden.
    quiet = _build({"model": OPUS, "messages": [{"role": "user", "content": "Hi"}]})
    assert quiet["thinking"]["display"] == "summarized" and "output_config" not in quiet


def test_a_forced_tool_reaches_a_model_that_accepts_one():
    tools = [{"type": "function", "function": {"name": "draw", "parameters": {"type": "object"}}}]
    body = _build({"model": "claude-opus-4-6", "temperature": 0.4, "tools": tools,
                   "tool_choice": {"type": "function", "function": {"name": "draw"}},
                   "messages": [{"role": "user", "content": "Draw."}]})
    assert body["tool_choice"] == {"type": "tool", "name": "draw"}
    assert body["temperature"] == 0.4 and "thinking" not in body


def test_caching_marks_the_system_prompt_and_the_newest_user_turn():
    body = _build(
        {"model": OPUS, "messages": [
            {"role": "system", "content": "Rules."},
            {"role": "user", "content": "First."},
            {"role": "assistant", "content": "Ok."},
            {"role": "user", "content": [{"type": "text", "text": "Second."},
                                         {"type": "text", "text": "Really."}]},
        ]}
    )
    assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}
    last = body["messages"][-1]["content"]
    assert last[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in last[0]
    assert "cache_control" not in body["messages"][0]["content"][0]


def test_an_assistant_turn_with_its_own_blocks_goes_back_unchanged():
    native = [
        {"type": "thinking", "thinking": "Plan.", "signature": "sig-1"},
        {"type": "text", "text": "Reading."},
        {"type": "tool_use", "id": "toolu_1", "name": "run", "input": {"cmd": "rm -rf /"}},
        {"type": "thinking", "thinking": "", "signature": "sig-2"},
        {"type": "tool_use", "id": "toolu_2", "name": "run", "input": {"cmd": "ls"}},
    ]
    calls = [
        # Corrected on the approval card: the model is told what actually ran.
        {"id": "toolu_1", "name": "run", "arguments": '{"cmd": "ls -la"}'},
        {"id": "toolu_2", "name": "run", "arguments": '{"cmd": "ls"}'},
    ]
    turn = tools_service.assistant_turn(calls, "Reading.", native)
    body = _build({"model": OPUS, "tools": [{"type": "function", "function": {"name": "run"}}],
                   "messages": [{"role": "user", "content": "Go."}, turn,
                                tools_service.tool_turn(calls[0], "x"),
                                tools_service.tool_turn(calls[1], "y")]})
    content = body["messages"][1]["content"]
    assert [b["type"] for b in content] == ["thinking", "text", "tool_use", "thinking", "tool_use"]
    assert content[0] == {"type": "thinking", "thinking": "Plan.", "signature": "sig-1"}
    assert content[3]["signature"] == "sig-2"
    assert content[2]["input"] == {"cmd": "ls -la"}
    # An OpenAI endpoint's assistant turn is exactly what it always was.
    assert "assistant_blocks" not in tools_service.assistant_turn(calls, "x", [])


def test_tools_withdrawn_mid_loop_are_declared_and_thinking_goes():
    """A loop asked to wrap up withdraws its tools while the transcript still
    holds calls; the API refuses tool_use with no tool defined."""
    native = [{"type": "thinking", "thinking": "", "signature": "s"},
              {"type": "tool_use", "id": "t1", "name": "search", "input": {}}]
    calls = [{"id": "t1", "name": "search", "arguments": "{}"}]
    body = _build({"model": OPUS, "messages": [
        {"role": "user", "content": "Find it."},
        tools_service.assistant_turn(calls, "", native),
        tools_service.tool_turn(calls[0], "found"),
    ]})
    stub = {"name": "search", "input_schema": {"type": "object", "properties": {}}}
    assert body["tools"] == [stub]
    assert body["tool_choice"] == {"type": "none"}
    assert [b["type"] for b in body["messages"][1]["content"]] == ["tool_use"]


# --- 2. The stream -------------------------------------------------------------------
def _stream_events(stop="tool_use"):
    return _sse(
        {"type": "message_start", "message": {"id": "msg_1", "usage": {
            "input_tokens": 20, "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 300, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "Let me "}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "thinking_delta", "thinking": "look."}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "signature_delta", "signature": "SIG"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "ping"},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "On "}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "it."}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2, "content_block": {
            "type": "tool_use", "id": "toolu_9", "name": "read", "input": {}}},
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "input_json_delta", "partial_json": '{"pa'}},
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "input_json_delta", "partial_json": 'th": "a.txt"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 42}},
        {"type": "message_stop"},
    )


def test_a_stream_translates_into_the_chunks_the_loop_reads(mock_http):
    seen = {}

    def server(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=_stream_events(),
                              headers={"content-type": "text/event-stream"})

    mock_http(server)
    chunks = _collect(ANTHROPIC, {"model": OPUS, "messages": [{"role": "user", "content": "Go"}]})

    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-ant-test"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert "authorization" not in seen["headers"]
    assert seen["body"]["stream"] is True

    assert "".join(delta_text(c) for c in chunks) == "On it."
    assert "".join(delta_reasoning(c) for c in chunks) == "Let me look."
    fragments = [f for c in chunks for f in delta_tool_calls(c)]
    assert fragments[0] == {"index": 0, "id": "toolu_9", "type": "function",
                            "function": {"name": "read", "arguments": ""}}
    assert "".join(f["function"].get("arguments", "") for f in fragments) == '{"path": "a.txt"}'
    assert [finish_reason(c) for c in chunks if finish_reason(c)] == ["tool_calls"]

    usage = [u for c in chunks if (u := chunk_usage(c))]
    assert usage == [{"prompt_tokens": 1320, "completion_tokens": 42,
                      "total_tokens": 1362, "cached_tokens": 1000,
                      "cache_write_tokens": 300}]

    (native,) = [b for c in chunks if (b := assistant_blocks(c))]
    assert native == [
        {"type": "thinking", "thinking": "Let me look.", "signature": "SIG"},
        {"type": "text", "text": "On it."},
        {"type": "tool_use", "id": "toolu_9", "name": "read", "input": {"path": "a.txt"}},
    ]


@pytest.mark.parametrize(("stop", "finish"), [("end_turn", "stop"), ("max_tokens", "length"),
                                              ("tool_use", "tool_calls"),
                                              ("stop_sequence", "stop")])
def test_stop_reasons_become_finish_reasons(mock_http, stop, finish):
    mock_http(lambda r: httpx.Response(200, content=_stream_events(stop)))
    chunks = _collect(ANTHROPIC, {"model": OPUS, "messages": [{"role": "user", "content": "x"}]})
    assert [finish_reason(c) for c in chunks if finish_reason(c)] == [finish]


def test_complete_goes_through_the_same_translation(mock_http):
    mock_http(lambda r: httpx.Response(200, content=_stream_events("end_turn")))
    assert asyncio.run(complete(ANTHROPIC, {"model": OPUS, "messages": HI})) == "On it."


# --- 3. Errors -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "kind", "words"),
    [(401, "authentication_error", "API key was rejected"),
     (429, "rate_limit_error", "Rate limited"),
     (529, "overloaded_error", "overloaded"),
     (402, "billing_error", "no credit left")],
)
def test_an_error_status_is_an_llm_error_worth_reading(mock_http, status, kind, words):
    mock_http(lambda r: httpx.Response(
        status, json={"type": "error", "error": {"type": kind, "message": "Upstream says no."}}))
    with pytest.raises(LLMError) as caught:
        _collect(ANTHROPIC, {"model": OPUS, "messages": HI})
    assert caught.value.status_code == status
    assert words in caught.value.message and "Upstream says no." in caught.value.message


def test_an_error_event_mid_stream_stops_the_reply(mock_http):
    body = _sse(
        {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 1}}},
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )
    mock_http(lambda r: httpx.Response(200, content=body))
    with pytest.raises(LLMError) as caught:
        _collect(ANTHROPIC, {"model": OPUS, "messages": HI})
    assert caught.value.status_code == 529


# --- 4. The model list ---------------------------------------------------------------
def test_the_model_list_follows_every_page(mock_http):
    asked = []

    def server(request: httpx.Request) -> httpx.Response:
        asked.append(dict(request.url.params))
        if "after_id" not in request.url.params:
            return httpx.Response(200, json={
                "data": [{"id": OPUS, "display_name": "Claude Opus 5.5", "type": "model",
                          "max_input_tokens": 1000000, "max_tokens": 128000}],
                "has_more": True, "first_id": OPUS, "last_id": OPUS})
        return httpx.Response(200, json={
            "data": [{"id": "claude-haiku-4-5", "type": "model"}],
            "has_more": False, "last_id": "claude-haiku-4-5"})

    mock_http(server)
    models = asyncio.run(list_models(ANTHROPIC))
    assert [m["id"] for m in models] == [OPUS, "claude-haiku-4-5"]
    assert asked[1]["after_id"] == OPUS
    assert context_from(models[0]) == 1000000 and models[0]["max_output"] == 128000
    assert models[0]["display_name"] == "Claude Opus 5.5"
    assert context_from(models[1]) == 0


def test_a_rejected_key_on_the_model_list_is_readable(mock_http):
    mock_http(lambda r: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}))
    with pytest.raises(LLMError) as caught:
        asyncio.run(list_models(ANTHROPIC))
    assert "Anthropic API key was rejected" in caught.value.message


# --- 5. The dispatcher ---------------------------------------------------------------
def test_an_openai_endpoint_is_untouched_by_the_dispatcher(mock_http):
    seen = {}

    def server(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]})

    mock_http(server)
    assert asyncio.run(complete(Endpoint("http://llama.test", "k", {}), {"model": "m"})) == "hi"
    assert seen == {"url": "http://llama.test/v1/chat/completions", "auth": "Bearer k"}


def test_an_endpoint_from_an_anthropic_connection_carries_its_kind_and_ceilings(db):
    connection = Connection(name="Anthropic", base_url="https://api.anthropic.com/",
                            api_key_encrypted=encrypt("sk-ant"), kind="anthropic")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id=OPUS, output_limit=128000,
                 context_length=1000000))
    db.add(Model(connection_id=connection.id, model_id="claude-haiku-4-5"))
    db.commit()
    db.refresh(connection)
    endpoint = Endpoint.from_connection(connection)
    assert endpoint.is_anthropic and endpoint.api_key == "sk-ant"
    assert endpoint.output_limit(OPUS) == 128000 and endpoint.output_limit("claude-haiku-4-5") == 0
    assert endpoint.context_limit(OPUS) == 1000000
    assert endpoint.url("messages") == "https://api.anthropic.com/v1/messages"


def test_embeddings_and_chat_templates_are_not_asked_of_anthropic(mock_http):
    from lembas.services.llm.embeddings import embed

    calls = []
    mock_http(lambda r: calls.append(r) or httpx.Response(500))
    with pytest.raises(LLMError) as caught:
        asyncio.run(embed(ANTHROPIC, "m", ["text"]))
    assert "no embeddings" in caught.value.message
    assert asyncio.run(fetch_chat_template(ANTHROPIC)) == ""
    assert calls == []


def test_an_anthropic_model_is_not_an_embedding_choice(client, db, registered):
    connection = Connection(name="A", base_url="https://api.anthropic.com", kind="anthropic")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id=OPUS,
                 capabilities_json={"embeddings": True}))
    db.commit()
    from lembas.api.admin_extraction import _embedding_models

    assert _embedding_models(db) == []


# --- 6. Served ids -------------------------------------------------------------------
def _token(db, scopes=("models",)) -> str:
    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="test", scopes=list(scopes))
    return raw


def _bearer(raw):
    return {"Authorization": f"Bearer {raw}"}


def _two_providers(db):
    api_tokens.reset_rates()
    llama = Connection(name="Local", provider="llama", base_url="http://llama.test/v1")
    cloud = Connection(name="DeepSeek", provider="deepseek", base_url="http://ds.test/v1")
    claude = Connection(name="Anthropic", provider="anthropic", kind="anthropic",
                        base_url="https://api.anthropic.com", api_key_encrypted=encrypt("sk"))
    db.add_all([llama, cloud, claude])
    db.commit()
    db.add_all([
        Model(connection_id=llama.id, model_id="bonsai"),
        Model(connection_id=llama.id, model_id="qwen"),
        Model(connection_id=cloud.id, model_id="deepseek-flash"),
        Model(connection_id=cloud.id, model_id="qwen"),
        Model(connection_id=claude.id, model_id=OPUS),
    ])
    db.commit()


def test_every_model_is_served_as_provider_slash_model(client, db, registered):
    _two_providers(db)
    data = client.get("/v1/models", headers=_bearer(_token(db))).json()["data"]
    by_id = {m["id"]: m for m in data}
    assert set(by_id) == {"llama/bonsai", "llama/qwen", "deepseek/deepseek-flash",
                          "deepseek/qwen", f"anthropic/{OPUS}"}
    assert by_id[f"anthropic/{OPUS}"]["provider"] == "anthropic"
    # The API behind it is `api`; `kind` means what the model is for.
    assert by_id[f"anthropic/{OPUS}"]["api"] == "anthropic"
    assert by_id["llama/bonsai"]["api"] == "openai"
    assert by_id["llama/bonsai"]["kind"] == "chat"


def test_the_ids_from_before_still_resolve(client, db, registered):
    from lembas.api.v1.openai import find

    _two_providers(db)
    user = db.scalar(select(User))
    assert find(db, user, "deepseek/deepseek-flash").connection.provider == "deepseek"
    # The bare id, where it was unique...
    assert find(db, user, "deepseek-flash").connection.provider == "deepseek"
    assert find(db, user, "bonsai").connection.provider == "llama"
    # ...and the old `<connection name>/<id>` form where it was not.
    assert find(db, user, "DeepSeek/qwen").connection.provider == "deepseek"
    assert find(db, user, "Local/qwen").connection.provider == "llama"
    # A bare id two connections serve names neither, as it did before.
    assert find(db, user, "qwen") is None
    assert find(db, user, "nobody/qwen") is None


def test_a_v1_completion_on_an_anthropic_model_is_translated(client, db, registered, mock_http):
    _two_providers(db)
    seen = {}

    def server(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=_stream_events("end_turn"))

    mock_http(server)
    response = client.post(
        "/v1/chat/completions", headers=_bearer(_token(db)),
        json={"model": f"anthropic/{OPUS}", "messages": [
            {"role": "system", "content": "Be kind."}, {"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["body"]["model"] == OPUS and seen["body"]["system"][0]["text"] == "Be kind."
    assert data["model"] == f"anthropic/{OPUS}"
    assert data["choices"][0]["message"]["content"] == "On it."
    assert data["choices"][0]["finish_reason"] == "stop"
    usage = db.scalar(select(Usage))
    assert (usage.prompt_tokens, usage.completion_tokens) == (1320, 42)


def test_a_streamed_v1_completion_on_an_anthropic_model(client, db, registered, mock_http):
    _two_providers(db)
    mock_http(lambda r: httpx.Response(200, content=_stream_events("tool_use")))
    with client.stream(
        "POST", "/v1/chat/completions", headers=_bearer(_token(db)),
        json={"model": f"anthropic/{OPUS}", "stream": True,
              "messages": [{"role": "user", "content": "Hi"}]},
    ) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line.startswith("data:")]
    assert lines[-1] == "data: [DONE]"
    frames = [json.loads(line[5:]) for line in lines[:-1]]
    assert all(f["model"] == f"anthropic/{OPUS}" for f in frames)
    assert "".join(delta_text(f) for f in frames) == "On it."
    assert [finish_reason(f) for f in frames if finish_reason(f)] == ["tool_calls"]


def test_a_refused_v1_request_keeps_anthropics_status(client, db, registered, mock_http):
    _two_providers(db)
    mock_http(lambda r: httpx.Response(529, json={"error": {"type": "overloaded_error",
                                                            "message": "Overloaded"}}))
    response = client.post("/v1/chat/completions", headers=_bearer(_token(db)),
                           json={"model": f"anthropic/{OPUS}", "stream": True,
                                 "messages": [{"role": "user", "content": "Hi"}]})
    assert response.status_code == 529
    assert "overloaded" in response.json()["error"]["message"]


def test_v1_embeddings_refuse_an_anthropic_model(client, db, registered, mock_http):
    _two_providers(db)
    mock_http(lambda r: httpx.Response(500))
    response = client.post("/v1/embeddings", headers=_bearer(_token(db)),
                           json={"model": f"anthropic/{OPUS}", "input": "x"})
    assert response.status_code == 400 and "no embeddings" in response.text


# --- 7. A provider by default --------------------------------------------------------
def test_a_new_connection_without_a_provider_is_given_one(db):
    db.add_all([Connection(name="Local LM", base_url="http://a.test"),
                Connection(name="Local LM", base_url="http://b.test")])
    db.commit()
    assert sorted(c.provider for c in db.scalars(select(Connection))) == [
        "local-lm", "local-lm-2"]


# --- 8. The admin form ---------------------------------------------------------------
def test_an_anthropic_connection_is_added_with_its_type_and_provider(
    client, db, registered, mock_http
):
    asked = {}

    def server(request):
        asked["headers"] = dict(request.headers)
        return httpx.Response(200, json={"data": [
            {"id": OPUS, "display_name": "Claude Opus 5.5", "max_input_tokens": 1000000,
             "max_tokens": 128000}], "has_more": False})

    mock_http(server)
    response = client.post("/admin/connections", data={
        "name": "Anthropic", "kind": "anthropic", "provider": "Anthropic",
        "base_url": "https://api.anthropic.com", "api_key": "sk-ant-secret"},
        follow_redirects=False)
    assert response.status_code == 303
    connection = db.scalar(select(Connection))
    assert (connection.kind, connection.provider) == ("anthropic", "anthropic")
    assert asked["headers"]["x-api-key"] == "sk-ant-secret"
    model = db.scalar(select(Model))
    # The ceiling is a cap, kept apart: no request asks for all of it by default.
    assert model.context_length == 1000000 and model.output_limit == 128000
    assert "max_tokens" not in (model.params_json or {})
    assert model.display_name == "Claude Opus 5.5"

    page = client.get("/admin/connections").text
    assert "sk-ant-secret" not in page
    assert "<code>anthropic</code>" in page and "Anthropic API key" in page
    assert "https://api.anthropic.com" in page


@pytest.mark.parametrize("bad", ["-llama", "llama!", "a" * 33, "ll ama", "ľama"])
def test_a_provider_name_of_the_wrong_shape_is_refused(client, db, registered, bad):
    response = client.post("/admin/connections", data={
        "name": "X", "provider": bad, "base_url": "http://127.0.0.1:1"}, follow_redirects=False)
    assert response.status_code == 400
    assert db.scalar(select(Connection)) is None


def test_a_provider_name_is_unique(client, db, registered):
    db.add_all([Connection(name="One", provider="llama", base_url="http://127.0.0.1:1"),
                Connection(name="Two", provider="deepseek", base_url="http://127.0.0.1:1")])
    db.commit()
    response = client.post("/admin/connections", data={
        "name": "Three", "provider": "llama", "base_url": "http://127.0.0.1:1"},
        follow_redirects=False)
    assert response.status_code == 400 and "One" in response.text

    two = db.scalar(select(Connection).where(Connection.name == "Two"))
    form = {"name": "Two", "base_url": "http://127.0.0.1:1", "enabled": "true"}
    clash = client.post(f"/admin/connections/{two.id}", data={**form, "provider": "llama"},
                        follow_redirects=False)
    assert clash.status_code == 400
    # Its own name is not a clash; a new one is taken, and so is a new type.
    same = client.post(f"/admin/connections/{two.id}",
                       data={**form, "provider": "deepseek", "kind": "anthropic"},
                       follow_redirects=False)
    assert same.status_code == 303
    db.expire_all()
    assert (db.get(Connection, two.id).provider, db.get(Connection, two.id).kind) == (
        "deepseek", "anthropic")
    # An older form sends neither, and changes neither.
    client.post(f"/admin/connections/{two.id}", data=form, follow_redirects=False)
    db.expire_all()
    assert db.get(Connection, two.id).provider == "deepseek"


# --- 9. Device model matching --------------------------------------------------------
class _Link:
    def __init__(self, connection=""):
        self.initialized = {"_meta": {"lembas": {"connection": connection}}}

    connection = device_link.Link.connection


@pytest.mark.parametrize(
    ("connection", "ref", "instance", "strict", "wanted"),
    [
        ("example", "deepseek/deepseek-flash", True, True, "deepseek/deepseek-flash"),
        ("example", "deepseek/deepseek-flash", False, False, ""),
        ("example", "example/deepseek/deepseek-flash", None, True, "deepseek/deepseek-flash"),
        ("example", "ollama/qwen3", None, False, ""),
        ("example", "deepseek-flash", None, False, "deepseek-flash"),
        ("example", "deepseek-flash", None, True, ""),
        ("", "example/deepseek-flash", None, False, "deepseek-flash"),
        ("", "example/deepseek-flash", None, True, ""),
    ],
)
def test_which_device_references_are_this_instances(connection, ref, instance, strict, wanted):
    assert device_link._instance_ref(_Link(connection), ref, instance, strict=strict) == wanted


def _device_setup(db, make_chat):
    from tests.test_device_protocol2 import _turn_link
    from tests.test_device_sync import _device_chat

    _two_providers(db)
    link, _ = _turn_link(db, lambda m, p: {}, capabilities=("turns", "model"))
    link.initialized["_meta"]["lembas"]["connection"] = "example"
    chat_id = _device_chat(db, make_chat, link)
    return link, chat_id


async def _model_event(link, **event):
    await link._notification("_lembas/event",
                             {"sessionId": "s1", "event": {"type": "model", **event}})


def test_a_model_event_marked_instance_is_matched_by_served_id(db, registered, make_chat):
    link, chat_id = _device_setup(db, make_chat)
    asyncio.run(_model_event(link, ref="deepseek/deepseek-flash", instance=True, effort="low"))
    db.expire_all()
    chat = db.get(Chat, chat_id)
    assert chat.model_id == "deepseek-flash" and not chat.device_model
    assert db.get(Connection, chat.connection_id).provider == "deepseek"
    assert link.configs[chat_id] == ("deepseek/deepseek-flash", "low")


def test_a_model_event_from_an_older_cli_keeps_the_prefix_rule(db, registered, make_chat):
    link, chat_id = _device_setup(db, make_chat)
    asyncio.run(_model_event(link, ref="example/llama/bonsai", effort="off"))
    db.expire_all()
    chat = db.get(Chat, chat_id)
    assert chat.model_id == "bonsai" and not chat.device_model
    # The id an older CLI cached -- the bare upstream id -- still finds it.
    asyncio.run(_model_event(link, ref="example/deepseek-flash", effort="off"))
    db.expire_all()
    assert db.get(Chat, chat_id).model_id == "deepseek-flash"


def test_a_model_event_marked_not_instance_is_the_devices_own(db, registered, make_chat):
    link, chat_id = _device_setup(db, make_chat)
    before = db.get(Chat, chat_id).model_id
    asyncio.run(_model_event(link, ref="deepseek/deepseek-flash", instance=False, effort="off"))
    db.expire_all()
    chat = db.get(Chat, chat_id)
    assert chat.device_model == "deepseek/deepseek-flash" and chat.model_id == before
    assert device_link.chat_config(db, chat) == ("", "off")


def test_an_announce_marked_instance_is_matched_by_served_id(db, registered, make_chat):
    link, chat_id = _device_setup(db, make_chat)
    chat = db.get(Chat, chat_id)
    device_link._apply_announced(db, link, chat, {"model": f"anthropic/{OPUS}",
                                                  "instance": True})
    assert chat.model_id == OPUS
    assert link.configs[chat.id][0] == f"anthropic/{OPUS}"
    device_link._apply_announced(db, link, chat, {"model": "llama/bonsai", "instance": False})
    assert chat.model_id == OPUS
    # Without `instance`, `llama/` is not the login's connection, so not ours.
    device_link._apply_announced(db, link, chat, {"model": "llama/bonsai"})
    assert chat.model_id == OPUS


# --- 10. Audit fixes -----------------------------------------------------------------
def _loop(with_blocks=True):
    turn = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "toolu_1", "type": "function",
         "function": {"name": "search", "arguments": '{"q":"x"}'}}]}
    if with_blocks:
        turn["assistant_blocks"] = [
            {"type": "thinking", "thinking": "hm", "signature": "SIG"},
            {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {"q": "x"}}]
    return [{"role": "user", "content": "go"}, turn,
            {"role": "tool", "tool_call_id": "toolu_1", "content": "r"}]


SEARCH = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}]


def test_claude_4_gets_temperature_or_top_p_never_both():
    both = _build({"model": SONNET_45, "temperature": 0.7, "top_p": 0.9, "messages": HI})
    assert both["temperature"] == 0.7 and "top_p" not in both
    only = _build({"model": SONNET_45, "top_p": 0.9, "messages": HI})
    assert only["top_p"] == 0.9 and "temperature" not in only


def test_a_wrap_up_round_on_a_budget_model_does_not_think():
    body = _build({"model": SONNET_45, "reasoning_effort": "high", "messages": _loop()})
    assert "thinking" not in body and "output_config" not in body
    assert body["tool_choice"] == {"type": "none"}
    # The same round with its blocks and tools thinks, blocks first.
    kept = _build({"model": SONNET_45, "reasoning_effort": "high", "tools": SEARCH,
                   "messages": _loop()})
    assert kept["thinking"]["type"] == "enabled"
    assert [m for m in kept["messages"] if m["role"] == "assistant"][-1]["content"][0][
        "type"] == "thinking"


def test_a_v1_client_without_blocks_turns_budget_thinking_off_for_that_request():
    body = _build({"model": "claude-haiku-4-5", "reasoning_effort": "low", "tools": SEARCH,
                   "messages": _loop(with_blocks=False)})
    assert "thinking" not in body
    # An adaptive model is unaffected.
    adaptive = _build({"model": OPUS, "reasoning_effort": "low", "tools": SEARCH,
                       "messages": _loop(with_blocks=False)})
    assert adaptive["thinking"]["type"] == "adaptive"


def test_cache_writes_reach_usage_and_are_billed_above_input(db):
    from lembas.services import credits

    translator = anthropic_client._Translator(OPUS)
    translator.feed({"type": "message_start", "message": {"id": "m", "usage": {
        "input_tokens": 10, "output_tokens": 1}}})
    out = translator.feed({"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                           "usage": {"cache_creation_input_tokens": 1000, "output_tokens": 5}})
    counts = chunk_usage(out[-1])
    assert counts["cache_write_tokens"] == 1000 and counts["prompt_tokens"] == 1010
    model = Model(model_id=OPUS, pricing_json={"input": 1.0, "cached_input": 0.1, "output": 0})
    plain = credits.cost(model, prompt_tokens=1010)
    written = credits.cost(model, prompt_tokens=1010, cache_write_tokens=1000)
    assert plain == 1010 and written == 10 + 1250
    # Reads keep the cached rate.
    assert credits.cost(model, prompt_tokens=1010, cached_tokens=1000) == 10 + 100


def test_an_empty_tool_input_is_json_in_a_whole_response(mock_http):
    events = _sse(
        {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 3}}},
        {"type": "content_block_start", "index": 0, "content_block": {
            "type": "tool_use", "id": "toolu_9", "name": "now", "input": {}}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 4}},
        {"type": "message_stop"})
    mock_http(lambda r: httpx.Response(200, content=events))
    response = asyncio.run(anthropic_client.complete_response(
        ANTHROPIC, {"model": OPUS, "messages": HI}))
    call = response["choices"][0]["message"]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {}


@pytest.mark.parametrize("status", [301, 307, 308])
def test_a_redirect_is_an_error_naming_the_host_only(mock_http, status):
    mock_http(lambda r: httpx.Response(
        status, headers={"location": "https://evil.example/v1/messages?key=sk-ant-test"}))
    with pytest.raises(LLMError) as caught:
        _collect(ANTHROPIC, {"model": OPUS, "messages": HI})
    message = caught.value.message
    assert "evil.example" in message and "sk-ant" not in message and "/v1" not in message
    with pytest.raises(LLMError):
        asyncio.run(list_models(ANTHROPIC))


def test_whitespace_only_text_is_dropped_and_nothing_left_is_an_error():
    body = _build({"model": OPUS, "messages": [
        {"role": "user", "content": [{"type": "text", "text": "  \n "},
                                     {"type": "text", "text": "Hi"}]},
        {"role": "assistant", "content": "   "},
        {"role": "user", "content": "Again"}]})
    assert [b["text"] for m in body["messages"] for b in m["content"]] == ["Hi", "Again"]
    with pytest.raises(LLMError) as caught:
        _build({"model": OPUS, "messages": [{"role": "user", "content": " \t "}]})
    assert "nothing to send" in caught.value.message


@pytest.mark.parametrize("name", ["", "🦙🦙", "-" * 50, "Čaj ☕", "x" * 200, "a-" * 40, "123"])
def test_a_provider_made_from_any_name_is_valid(name):
    from lembas.db.models.connection import PROVIDER_PATTERN, provider_slug, unique_provider

    slug = provider_slug(name)
    assert PROVIDER_PATTERN.match(slug), slug
    assert PROVIDER_PATTERN.match(unique_provider(slug, {slug}))
