"""Autocomplete: `POST /v1/lembas/complete`.

The text before the cursor and the text after it go in; the text to insert
comes out. Which model answers, how each kind of connection is asked, what a
chat model's answer is cleaned of, what is refused and in which words, what is
counted -- and that nothing of it is ever a chat.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from sqlalchemy import select

from lembas.api.v1 import lembas as complete_api
from lembas.db.models import TOKEN_DEVICE, Chat, Connection, Message, Model, Usage, User
from lembas.security import api_tokens
from lembas.services import editor, settings_store
from lembas.services.crypto import encrypt
from lembas.services.llm import fim
from tests.conftest import join

URL = "/v1/lembas/complete"
PREFIX = "def add(a, b):\n    return "
SUFFIX = "\n\nprint(add(1, 2))\n"


@pytest.fixture(autouse=True)
def _fresh():
    api_tokens.reset_rates()
    fim.reset()
    complete_api._IN_FLIGHT.clear()
    yield
    fim.reset()
    complete_api._IN_FLIGHT.clear()


def _me(db) -> User:
    return db.scalar(select(User))


def _connection(db, *, name="local", server="", provider_type=None, base_url=None) -> Connection:
    row = Connection(
        name=name,
        base_url=base_url or "http://upstream.test/v1",
        api_key_encrypted=encrypt("upstream-key"),
        health_json={"server": server} if server else {},
    )
    if provider_type:
        row.provider_type = provider_type
    db.add(row)
    db.commit()
    return row


def _model(db, connection, model_id="coder", **fields) -> Model:
    row = Model(connection_id=connection.id, model_id=model_id, **fields)
    db.add(row)
    db.commit()
    return row


def _auth(db, *, scopes=("models",), kind=None) -> dict[str, str]:
    extra = {"kind": kind, "device_host": "workstation"} if kind else {}
    _, raw = api_tokens.create(db, _me(db), name="editor", scopes=list(scopes), **extra)
    return {"Authorization": f"Bearer {raw}"}


def _sse(*frames, done=True) -> str:
    body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
    return body + ("data: [DONE]\n\n" if done else "")


def _chat_stream(*pieces, usage=(9, 4), finish="stop") -> str:
    frames = [{"choices": [{"delta": {"content": piece}}]} for piece in pieces]
    frames.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    if usage:
        frames.append({"choices": [], "usage": {"prompt_tokens": usage[0],
                                                "completion_tokens": usage[1]}})
    return _sse(*frames)


def _upstream(mock_http, body: str, seen: list | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append((str(request.url), json.loads(request.content), dict(request.headers)))
        return httpx.Response(status, text=body, headers={"content-type": "text/event-stream"})

    mock_http(handler)


def _ask(client, auth, **body):
    return client.post(URL, headers=auth, json={"prefix": PREFIX, "suffix": SUFFIX, **body})


# --- The door --------------------------------------------------------------------
def test_it_wants_a_token_with_the_models_scope(client, db, registered):
    _model(db, _connection(db))
    assert client.post(URL, json={"prefix": "x"}).status_code == 401
    refused = client.post(URL, headers=_auth(db, scopes=("memory",)), json={"prefix": "x"})
    assert refused.status_code == 403


def test_a_model_the_person_may_not_use_is_not_there(client, db, registered):
    """As on the rest of `/v1`: a 404, whoever else may use it."""
    from lembas.db.models import Group

    row = _model(db, _connection(db), public=False)
    row.groups = [Group(name="Others")]
    settings_store.update(db, {"default_permissions": {"api.use": True}})
    join(client, {"name": "Sam", "email": "sam@shire.test", "password": "second-breakfast-now"})
    sam = db.scalar(select(User).where(User.email == "sam@shire.test"))
    _, raw = api_tokens.create(db, sam, name="editor", scopes=["models"])
    response = _ask(client, {"Authorization": f"Bearer {raw}"}, model="local/coder")
    assert response.status_code == 404


# --- Which model -----------------------------------------------------------------
def test_with_no_model_chosen_it_says_where_to_choose_one(client, db, registered):
    _model(db, _connection(db))
    response = _ask(client, _auth(db))
    assert response.status_code == 409
    message = response.json()["error"]["message"]
    assert "Settings" in message and "administrator" in message


def test_the_requests_model_then_the_persons_then_the_instances(
    client, db, registered, mock_http
):
    connection = _connection(db)
    for name in ("one", "two", "three"):
        _model(db, connection, name)
    seen: list = []
    _upstream(mock_http, _chat_stream("a + b"), seen)
    auth = _auth(db)

    editor.set_instance_default(db, db.scalar(select(Model).where(Model.model_id == "one")))
    assert _ask(client, auth).json()["model"] == "local/one"

    editor.save(db, _me(db), autocomplete_model="local/two")
    assert _ask(client, auth).json()["model"] == "local/two"

    assert _ask(client, auth, model="local/three").json()["model"] == "local/three"
    assert [body["model"] for _, body, _ in seen] == ["one", "two", "three"]


def test_a_model_not_allowed_for_autocomplete_is_refused_in_words(client, db, registered):
    connection = _connection(db)
    _model(db, connection, "small", autocomplete=True)
    _model(db, connection, "large")
    response = _ask(client, _auth(db), model="local/large")
    assert response.status_code == 403
    assert "not allowed for autocomplete" in response.json()["error"]["message"]
    assert response.json()["error"]["type"] == "permission_error"


def test_a_stored_choice_that_is_no_longer_allowed_gives_way(client, db, registered, mock_http):
    connection = _connection(db)
    small, large = _model(db, connection, "small"), _model(db, connection, "large")
    editor.save(db, _me(db), autocomplete_model="local/large")
    editor.set_instance_default(db, small)
    small.autocomplete = True  # now only `small` is allowed
    db.commit()
    _upstream(mock_http, _chat_stream("a + b"))
    assert _ask(client, _auth(db)).json()["model"] == "local/small"
    assert large.autocomplete is False


def test_an_unknown_model_and_an_embedding_model(client, db, registered):
    connection = _connection(db)
    _model(db, connection, "vectors", kind="embedding")
    auth = _auth(db)
    assert _ask(client, auth, model="local/nothing").status_code == 404
    refused = _ask(client, auth, model="local/vectors")
    assert refused.status_code == 400 and "embedding" in refused.json()["error"]["message"]


# --- What is sent in ---------------------------------------------------------------
def test_a_side_over_64_kib_is_a_413_in_words(client, db, registered):
    _model(db, _connection(db))
    auth = _auth(db)
    for key, word in (("prefix", "before"), ("suffix", "after")):
        response = client.post(
            URL, headers=auth, json={"prefix": "x", key: "y" * (64 * 1024 + 1), "model": "coder"}
        )
        assert response.status_code == 413
        message = response.json()["error"]["message"]
        assert "64 KiB" in message and word in message
    # Bytes, not characters: 40,000 three-byte characters are over it.
    response = client.post(URL, headers=auth, json={"prefix": "€" * 40_000, "model": "coder"})
    assert response.status_code == 413


def test_a_body_past_the_whole_limit_is_refused_before_it_is_parsed(client, db, registered):
    _model(db, _connection(db))
    response = client.post(
        URL, headers={**_auth(db), "content-type": "application/json"},
        content=b'{"prefix": "' + b"a" * (complete_api.MAX_BODY_BYTES + 10) + b'"}',
    )
    assert response.status_code == 413 and "64 KiB" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("body", "word"),
    [
        ({"suffix": "x"}, "`prefix` is required"),
        ({"prefix": 3}, "`prefix` must be a string"),
        ({"prefix": "x", "suffix": ["y"]}, "`suffix` must be a string"),
        ({"prefix": "", "suffix": ""}, "nothing to complete"),
        ({"prefix": "x", "max_tokens": 0}, "`max_tokens`"),
        ({"prefix": "x", "max_tokens": "many"}, "`max_tokens`"),
        ({"prefix": "x", "max_tokens": True}, "`max_tokens`"),
    ],
)
def test_a_malformed_request_is_a_400_that_names_the_field(client, db, registered, body, word):
    _model(db, _connection(db))
    response = client.post(URL, headers=_auth(db), json=body)
    assert response.status_code == 400 and word in response.json()["error"]["message"]


def test_the_body_must_be_a_json_object(client, db, registered):
    auth = {**_auth(db), "content-type": "application/json"}
    assert client.post(URL, headers=auth, content=b"not json").status_code == 400
    assert client.post(URL, headers=auth, content=b"[1]").status_code == 400


def test_max_tokens_is_128_unless_asked_and_512_at_most(client, db, registered, mock_http):
    _model(db, _connection(db))
    seen: list = []
    _upstream(mock_http, _chat_stream("a + b"), seen)
    auth = _auth(db)
    _ask(client, auth, model="coder")
    _ask(client, auth, model="coder", max_tokens=40)
    _ask(client, auth, model="coder", max_tokens=100_000)
    assert [body["max_tokens"] for _, body, _ in seen] == [128, 40, 512]


# --- How each connection is asked ------------------------------------------------------
def test_a_llama_cpp_server_is_asked_to_infill(client, db, registered, mock_http):
    _model(db, _connection(db, server="llama.cpp"))
    seen: list = []
    _upstream(
        mock_http,
        _sse(
            {"content": "a ", "stop": False},
            {"content": "+ b", "stop": False},
            {"content": "", "stop": True, "stop_type": "eos", "tokens_evaluated": 21,
             "tokens_predicted": 3, "timings": {"cache_n": 12}},
            done=False,
        ),
        seen,
    )
    response = _ask(client, _auth(db), model="coder", max_tokens=32)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "text": "a + b",
        "model": "local/coder",
        "finish": "stop",
        "usage": {"prompt_tokens": 21, "completion_tokens": 3, "total_tokens": 24},
    }
    ((url, body, headers),) = seen
    # Beside `/v1`, not under it; the connection's own key.
    assert url == "http://upstream.test/infill"
    assert headers["authorization"] == "Bearer upstream-key"
    assert body == {"model": "coder", "input_prefix": PREFIX, "input_suffix": SUFFIX,
                    "n_predict": 32, "cache_prompt": True, "stream": True}


def test_infill_that_stopped_at_the_limit_says_length(client, db, registered, mock_http):
    _model(db, _connection(db, server="llama-swap"))
    _upstream(mock_http, _sse({"content": "a +", "stop": True, "stop_type": "limit",
                               "tokens_evaluated": 5, "tokens_predicted": 2}, done=False))
    assert _ask(client, _auth(db), model="coder").json()["finish"] == "length"


def test_a_model_that_cannot_infill_is_asked_as_a_chat_and_remembered(
    client, db, registered, mock_http
):
    _model(db, _connection(db, server="llama.cpp"))
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/infill":
            return httpx.Response(501, json={"error": {
                "code": 501, "message": "Infill is not supported by this model",
                "type": "not_supported_error"}})
        return httpx.Response(200, text=_chat_stream("a + b"))

    mock_http(handler)
    auth = _auth(db)
    assert _ask(client, auth, model="coder").json()["text"] == "a + b"
    assert _ask(client, auth, model="coder").json()["text"] == "a + b"
    # Asked once, refused, never asked that way again.
    assert asked == ["/infill", "/v1/chat/completions", "/v1/chat/completions"]


def test_an_ollama_is_asked_for_a_completion_with_a_suffix(client, db, registered, mock_http):
    _model(db, _connection(db, server="ollama"))
    seen: list = []
    _upstream(
        mock_http,
        _sse(
            {"choices": [{"text": "a + b"}]},
            {"choices": [{"text": "", "finish_reason": "length"}]},
            {"choices": [], "usage": {"prompt_tokens": 14, "completion_tokens": 3}},
        ),
        seen,
    )
    answer = _ask(client, _auth(db), model="coder").json()
    assert (answer["text"], answer["finish"]) == ("a + b", "length")
    assert answer["usage"] == {"prompt_tokens": 14, "completion_tokens": 3, "total_tokens": 17}
    ((url, body, _),) = seen
    assert url == "http://upstream.test/v1/completions"
    assert body == {"model": "coder", "prompt": PREFIX, "suffix": SUFFIX, "max_tokens": 128,
                    "stream": True, "stream_options": {"include_usage": True}}


def test_a_server_that_does_not_know_stream_options_is_asked_again_without(
    client, db, registered, mock_http
):
    _model(db, _connection(db, server="ollama"))
    bodies: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": {"message": "unknown field stream_options"}})
        return httpx.Response(200, text=_sse({"choices": [{"text": "a + b"}]}))

    mock_http(handler)
    answer = _ask(client, _auth(db), model="coder").json()
    assert answer["text"] == "a + b"
    assert len(bodies) == 2 and "stream_options" not in bodies[1]
    # The server gave no count: the lengths are counted, and it says so.
    assert answer["usage"]["estimated"] is True and answer["usage"]["completion_tokens"] >= 1


def test_deepseek_is_asked_at_its_fim_address(client, db, registered, mock_http):
    connection = _connection(
        db, name="DeepSeek", provider_type="deepseek", base_url="https://api.deepseek.com"
    )
    _model(db, connection, "deepseek-chat")
    seen: list = []
    _upstream(mock_http, _sse({"choices": [{"text": "a + b", "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 8, "completion_tokens": 3}}), seen)
    answer = _ask(client, _auth(db), model="deepseek-chat").json()
    assert answer["text"] == "a + b"
    ((url, body, _),) = seen
    assert url == "https://api.deepseek.com/beta/completions"
    assert body["prompt"] == PREFIX and body["suffix"] == SUFFIX


def test_any_other_server_gets_a_strict_chat_prompt_with_thinking_off(
    client, db, registered, mock_http
):
    _model(db, _connection(db), thinking_switch=True, reasoning_efforts=["low", "high"],
           params_json={"temperature": 0.2, "reasoning_effort": "high", "max_tokens": 9000})
    seen: list = []
    _upstream(mock_http, _chat_stream("a + b"), seen)
    answer = _ask(client, _auth(db), model="coder", path="src/sum.py", language="python").json()
    assert answer["text"] == "a + b"
    ((url, body, _),) = seen
    assert url == "http://upstream.test/v1/chat/completions"
    # Thinking off, where the model has the switch: no effort, and the switch.
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in body
    # The administrator's sampling, and this request's own length.
    assert body["temperature"] == 0.2 and body["max_tokens"] == 128
    system, user = body["messages"]
    assert system["role"] == "system" and "nothing else" in system["content"]
    assert "File: src/sum.py" in user["content"] and "Language: python" in user["content"]
    assert f"<before>\n{PREFIX}</before>" in user["content"]
    assert f"<after>\n{SUFFIX}</after>" in user["content"]
    assert "tools" not in body


def test_a_model_without_the_switch_is_asked_for_its_lowest_effort(
    client, db, registered, mock_http
):
    _model(db, _connection(db), reasoning_efforts=["medium", "low", "high"],
           capabilities_json={"reasoning": True})
    seen: list = []
    _upstream(mock_http, _chat_stream("a + b"), seen)
    _ask(client, _auth(db), model="coder", max_tokens=100)
    body = seen[0][1]
    assert body["reasoning_effort"] == "low"
    # It thinks whatever it is told: room for that on top of what was asked.
    assert body["max_tokens"] == 100 + fim.THINKING_ROOM


def test_an_anthropic_connection_is_asked_through_messages(client, db, registered, mock_http):
    connection = _connection(
        db, name="Anthropic", provider_type="anthropic", base_url="https://api.anthropic.com"
    )
    _model(db, connection, "claude-haiku-4-5")
    seen: list = []
    events = [
        {"type": "message_start", "message": {"id": "m", "usage": {"input_tokens": 30}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "a + b"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
         "usage": {"output_tokens": 4}},
        {"type": "message_stop"},
    ]
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    _upstream(mock_http, body, seen)
    answer = _ask(client, _auth(db), model="claude-haiku-4-5").json()
    assert answer["text"] == "a + b" and answer["model"] == "anthropic/claude-haiku-4-5"
    assert answer["usage"]["prompt_tokens"] == 30 and answer["usage"]["completion_tokens"] == 4
    url, sent, _ = seen[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert "thinking" not in sent and "nothing else" in json.dumps(sent["system"])


# --- Only the inserted text ------------------------------------------------------------
@pytest.mark.parametrize(
    ("said", "wanted"),
    [
        ("a + b", "a + b"),
        # A wrapping fence, with and without a language word.
        ("```python\na + b\n```", "a + b"),
        ("```\na + b\n```\n", "a + b"),
        # Thinking written into the answer.
        ("<think>The sum.</think>\n\na + b", "a + b"),
        # The line being completed, said again.
        ("    return a + b", "a + b"),
        ("return a + b", "a + b"),
        # What follows the cursor, said again.
        ("a + b\n\nprint(add(1, 2))\n", "a + b"),
        ("```python\n    return a + b\n```", "a + b"),
        # Indentation and inner blank lines are the completion's own.
        ("a + b\n\n\ndef sub(a, b):\n    return a - b",
         "a + b\n\n\ndef sub(a, b):\n    return a - b"),
        ("", ""),
    ],
)
def test_a_chat_models_answer_is_cleaned_to_the_inserted_text(said, wanted):
    assert fim.clean(said, PREFIX, SUFFIX) == wanted
    # However the answer is cut into pieces, the same text comes out.
    for size in (1, 3, 7):
        cleaner = fim.Inserted(PREFIX, SUFFIX)
        out = "".join(cleaner.feed(said[i : i + size]) for i in range(0, len(said), size))
        assert out + cleaner.finish() == wanted, size


def test_one_shared_character_is_not_a_repetition():
    """`)` ending the insertion and starting the suffix may both be wanted."""
    assert fim.clean("b)", "foo(bar(a, ", ")\n") == "b)"
    assert fim.clean("x", "a = ", "\nprint(a)") == "x"


def test_reasoning_in_its_own_field_never_reaches_the_answer(client, db, registered, mock_http):
    _model(db, _connection(db))
    frames = [
        {"choices": [{"delta": {"reasoning_content": "The reader adds two numbers."}}]},
        {"choices": [{"delta": {"content": "a + b"}}]},
    ]
    _upstream(mock_http, _sse(*frames))
    assert _ask(client, _auth(db), model="coder").json()["text"] == "a + b"


def test_only_the_text_near_the_cursor_is_sent(client, db, registered, mock_http):
    _model(db, _connection(db), context_length=4096)
    seen: list = []
    _upstream(mock_http, _chat_stream("x"), seen)
    before = "".join(f"line {n}\n" for n in range(6000))
    after = "".join(f"tail {n}\n" for n in range(3000))
    response = client.post(URL, headers=_auth(db), json={
        "prefix": before, "suffix": after, "model": "coder"})
    assert response.status_code == 200
    user = seen[0][1]["messages"][1]["content"]
    sent_before = user.split("<before>\n", 1)[1].split("</before>", 1)[0]
    sent_after = user.split("<after>\n", 1)[1].split("</after>", 1)[0]
    assert len(sent_before) <= 3072 and len(sent_after) <= 1024
    # The end of what is before, the start of what is after, cut at lines.
    assert before.endswith(sent_before) and sent_before.startswith("line ")
    assert after.startswith(sent_after) and sent_after.rsplit("\n", 1)[-1].startswith("tail ")


# --- Streamed ----------------------------------------------------------------------------
def _frames(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_a_streamed_completion_is_deltas_then_the_final_object(
    client, db, registered, mock_http
):
    _model(db, _connection(db, server="llama.cpp"))
    _upstream(mock_http, _sse(
        {"content": "a ", "stop": False},
        {"content": "+ b", "stop": False},
        {"content": "", "stop": True, "stop_type": "eos", "tokens_evaluated": 21,
         "tokens_predicted": 3},
        done=False,
    ))
    response = _ask(client, _auth(db), model="coder", stream=True)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = _frames(response.text)
    assert frames[:-1] == [{"delta": "a "}, {"delta": "+ b"}]
    assert frames[-1] == {
        "text": "a + b", "model": "local/coder", "finish": "stop",
        "usage": {"prompt_tokens": 21, "completion_tokens": 3, "total_tokens": 24},
    }
    # The stream ends with the final object: no sentinel after it.
    assert "[DONE]" not in response.text


def test_a_streamed_chat_answer_is_cleaned_and_its_deltas_add_up(
    client, db, registered, mock_http
):
    _model(db, _connection(db))
    long = "a + b  # " + "the sum of both numbers, " * 12
    said = f"```python\n    return {long}\n```"
    _upstream(mock_http, _chat_stream(*[said[i : i + 5] for i in range(0, len(said), 5)]))
    frames = _frames(_ask(client, _auth(db), model="coder", stream=True).text)
    assert "".join(f["delta"] for f in frames[:-1]) == frames[-1]["text"] == long
    assert len(frames) > 2  # more than one delta: it did stream


def test_an_upstream_refusal_before_any_text_is_an_error_with_its_status(
    client, db, registered, mock_http
):
    _model(db, _connection(db))
    mock_http(lambda request: httpx.Response(401, json={"error": {"message": "bad key"}}))
    for stream in (False, True):
        response = _ask(client, _auth(db), model="coder", stream=stream)
        assert response.status_code == 401
        assert response.json()["error"]["type"] == "upstream_error"
    # No completion: nothing counted, and nothing left in flight.
    assert db.scalar(select(Usage)) is None
    assert complete_api._IN_FLIGHT == {}


def test_an_error_in_the_middle_of_a_stream_goes_into_the_stream(
    client, db, registered, mock_http
):
    _model(db, _connection(db, server="llama.cpp"))
    body = 'data: {"content": "a ", "stop": false}\n\n{"error": {"message": "model unloaded"}}\n'
    _upstream(mock_http, body)
    frames = _frames(_ask(client, _auth(db), model="coder", stream=True).text)
    assert frames[0] == {"delta": "a "}
    assert frames[-1]["error"]["type"] == "upstream_error"
    assert "model unloaded" in frames[-1]["error"]["message"]


# --- Counted, priced, and never a chat ---------------------------------------------------
def test_a_completion_is_counted_and_priced_like_a_reply(client, db, registered, mock_http):
    _model(db, _connection(db), pricing_json={"input": 2.0, "output": 10.0})
    _upstream(mock_http, _chat_stream("a + b", usage=(1000, 100)))
    auth = _auth(db)
    assert _ask(client, auth, model="coder").status_code == 200
    usage = db.scalar(select(Usage))
    assert (usage.tokens_in, usage.tokens_out, usage.requests) == (1000, 100, 1)
    # 1000 at 2 a million and 100 at 10 a million.
    assert usage.cost_micro == 2000 + 1000
    # The token's own share, as for any call through it.
    from lembas.db.models import ApiToken

    token = db.scalar(select(ApiToken))
    assert (token.usage_prompt_tokens, token.usage_completion_tokens) == (1000, 100)


def test_a_subscriptions_connection_is_counted_and_never_priced(
    client, db, registered, mock_http
):
    connection = _connection(
        db, name="Plan", provider_type="alibaba_plan",
        base_url="https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    )
    _model(db, connection, "qwen-coder", pricing_json={"input": 2.0, "output": 10.0})
    _upstream(mock_http, _chat_stream("a + b", usage=(1000, 100)))
    # A linked device: a person at a keyboard, which a plan's terms allow.
    response = _ask(client, _auth(db, kind=TOKEN_DEVICE), model="qwen-coder")
    assert response.status_code == 200, response.text
    usage = db.scalar(select(Usage))
    assert (usage.tokens_in, usage.tokens_out, usage.cost_micro) == (1000, 100, 0)


def test_a_plan_serves_a_device_and_an_app_but_not_a_script(client, db, registered, mock_http):
    connection = _connection(
        db, name="Plan", provider_type="alibaba_plan",
        base_url="https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1",
    )
    _model(db, connection, "qwen-coder")
    _upstream(mock_http, _chat_stream("a + b"))
    script = _ask(client, _auth(db), model="qwen-coder")
    assert script.status_code == 403 and script.json()["error"]["type"] == "permission_error"
    assert _ask(client, _auth(db, scopes=("app", "models")), model="qwen-coder").status_code == 200
    assert _ask(client, _auth(db, kind=TOKEN_DEVICE), model="qwen-coder").status_code == 200


def test_nothing_of_a_completion_is_ever_a_chat(client, db, registered, mock_http):
    _model(db, _connection(db))
    _upstream(mock_http, _chat_stream("a + b"))
    auth = _auth(db)
    assert _ask(client, auth, model="coder").status_code == 200
    assert _ask(client, auth, model="coder", stream=True).status_code == 200
    assert db.scalar(select(Chat)) is None and db.scalar(select(Message)) is None


def test_a_completion_holds_a_slot_while_it_is_written_and_gives_it_back(
    client, db, registered, monkeypatch
):
    _model(db, _connection(db))
    me, held = _me(db).id, []

    async def upstream(ask):
        held.append(complete_api.in_flight(me))
        yield fim.DELTA, "a + b"
        yield fim.END, {"finish": "stop", "usage": None, "way": "chat"}

    monkeypatch.setattr(fim, "stream", upstream)
    auth = _auth(db)
    assert _ask(client, auth, model="coder").status_code == 200
    assert _ask(client, auth, model="coder", stream=True).status_code == 200
    assert held == [1, 1] and complete_api._IN_FLIGHT == {}


def test_more_than_a_few_at_once_is_refused(client, db, registered, mock_http):
    _model(db, _connection(db))
    _upstream(mock_http, _chat_stream("a + b"))
    auth = _auth(db)
    complete_api._IN_FLIGHT[_me(db).id] = complete_api.MAX_AT_ONCE
    refused = _ask(client, auth, model="coder")
    assert refused.status_code == 429
    assert "Suggestions" in refused.json()["error"]["message"]
    complete_api._IN_FLIGHT.clear()
    assert _ask(client, auth, model="coder").status_code == 200
    # Released when it ended.
    assert complete_api._IN_FLIGHT == {}


def test_completions_have_a_rate_of_their_own(client, db, registered, mock_http):
    """An editor asks after every pause in typing: that must never use up the
    rate the same token's chat and library calls are counted in."""
    _model(db, _connection(db))
    _upstream(mock_http, _chat_stream("a + b"))
    settings_store.update(db, {"api_requests_per_minute": 2, "api_completions_per_minute": 3})
    auth = _auth(db)
    for _ in range(3):
        assert _ask(client, auth, model="coder").status_code == 200
    # The token's other calls were not counted: both of its two are still there.
    assert client.get("/v1/models", headers=auth).status_code == 200
    assert client.get("/v1/models", headers=auth).status_code == 200
    assert client.get("/v1/models", headers=auth).status_code == 429
    # And the fourth completion is told, in words, that it is only too fast.
    slow = _ask(client, auth, model="coder")
    assert slow.status_code == 429 and int(slow.headers["retry-after"]) >= 1
    assert "Suggestions" in slow.json()["detail"] and "Nothing is wrong" in slow.json()["detail"]
    # Spent the other way round, completions still have theirs.
    api_tokens.reset_rates()
    for _ in range(2):
        client.get("/v1/models", headers=auth)
    assert client.get("/v1/models", headers=auth).status_code == 429
    assert _ask(client, auth, model="coder").status_code == 200


def test_the_completion_rate_is_600_a_minute_unless_set_and_0_is_off(db, registered):
    assert settings_store.get(db, "api_completions_per_minute") == 600
    token, _ = api_tokens.create(db, _me(db), name="editor", scopes=["models"])
    settings_store.update(db, {"api_completions_per_minute": 0})
    assert all(
        api_tokens.over_rate(db, token, bucket=api_tokens.BUCKET_COMPLETE) == 0
        for _ in range(700)
    )


# --- A client that goes away ---------------------------------------------------------------
class _Leaving:
    """A request whose client is gone after `after` looks."""

    def __init__(self, after: int) -> None:
        self.left = after

    async def is_disconnected(self) -> bool:
        self.left -= 1
        return self.left < 0


def test_a_client_that_disconnects_cancels_the_upstream_request(monkeypatch):
    """Also before the first word -- which is when the next keystroke comes."""
    monkeypatch.setattr(complete_api, "WATCH_EVERY", 0.01)
    closed = []

    async def upstream():
        try:
            yield "a "
            await asyncio.sleep(30)  # the model's server, still thinking
            yield "+ b"
        finally:
            closed.append(True)

    async def run() -> list[str]:
        got = []
        with pytest.raises(complete_api._Gone):
            async for piece in complete_api._watched(_Leaving(2), upstream()):
                got.append(piece)
        return got

    assert asyncio.run(asyncio.wait_for(run(), timeout=5)) == ["a "]
    assert closed == [True]


def test_a_cancelled_completion_is_counted_for_what_was_written(
    client, db, registered, mock_http, monkeypatch
):
    """The model's server wrote it, whether or not anybody still wanted it."""
    _model(db, _connection(db, server="llama.cpp"))

    async def half(ask):
        yield fim.DELTA, "a + "
        raise complete_api._Gone

    monkeypatch.setattr(fim, "stream", half)
    response = _ask(client, _auth(db), model="coder")
    assert response.status_code == 499
    usage = db.scalar(select(Usage))
    assert usage.requests == 1 and usage.tokens_out >= 1
    assert complete_api._IN_FLIGHT == {}


# --- The instance's setting, as stored ------------------------------------------------------
def test_the_instance_default_is_a_ref_in_the_general_settings(db, registered):
    row = _model(db, _connection(db))
    assert settings_store.get(db, "autocomplete_model") == ""
    editor.set_instance_default(db, row)
    assert settings_store.get(db, "autocomplete_model") == "local/coder"
    editor.set_instance_default(db, None)
    assert settings_store.get(db, "autocomplete_model") == ""


# --- After the review ------------------------------------------------------------------------
class _BadClose:
    """A stream whose closing fails, as a cancellation inside the close does."""

    def __init__(self, pieces):
        self._pieces = iter(pieces)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._pieces)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self):
        raise RuntimeError("asynchronous generator is already running")


@pytest.mark.parametrize("stream", [False, True])
def test_the_slot_is_given_back_even_when_closing_the_stream_fails(
    client, db, registered, monkeypatch, stream
):
    """Four slots that are never given back are a 429 on every suggestion
    until a restart."""
    _model(db, _connection(db))
    monkeypatch.setattr(complete_api, "_watched", lambda request, run: _BadClose(["a + b"]))
    auth = _auth(db)
    for _ in range(complete_api.MAX_AT_ONCE + 2):
        answer = _ask(client, auth, model="coder", stream=stream)
        assert answer.status_code == 200, answer.text
    assert complete_api._IN_FLIGHT == {}


def test_a_400_about_this_request_demotes_nobody(client, db, registered, mock_http):
    """Too long for the loaded context is this request's error. The model
    still fills in for the next person."""
    _model(db, _connection(db, server="llama.cpp"))
    asked: list[str] = []
    small = {"error": {"code": 400, "message": "the request exceeds the available context size",
                       "type": "exceed_context_size_error"}}

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if len(asked) == 1:
            return httpx.Response(400, json=small)
        return httpx.Response(200, text=_sse({"content": "a + b", "stop": True}, done=False))

    mock_http(handler)
    auth = _auth(db)
    refused = _ask(client, auth, model="coder")
    assert refused.status_code == 400 and refused.json()["error"]["type"] == "upstream_error"
    assert "context size" in refused.json()["error"]["message"]
    assert _ask(client, auth, model="coder").json()["text"] == "a + b"
    # Never asked as a chat: both went to the server's own fill-in.
    assert asked == ["/infill", "/infill"]


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, {"error": "not found"}),
        (405, {"error": "method not allowed"}),
        (501, {"error": {"message": "Infill is not supported by this model"}}),
        (400, {"error": {"message": "model does not support insert"}}),
        (422, {"detail": "suffix is not supported for this model"}),
    ],
)
def test_no_such_call_in_any_of_its_spellings_is_asked_as_a_chat(
    client, db, registered, mock_http, status, body
):
    _model(db, _connection(db, server="ollama"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, text=_chat_stream("a + b"))
        return httpx.Response(status, json=body)

    mock_http(handler)
    assert _ask(client, _auth(db), model="coder").json()["text"] == "a + b"


def test_a_demotion_is_forgotten_after_a_while(client, db, registered, mock_http, monkeypatch):
    """A model server restarted with a model that fills in is asked that way
    again, without this server being restarted."""
    _model(db, _connection(db, server="llama.cpp"))
    asked: list[str] = []
    can = {"infill": False}

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/infill":
            if not can["infill"]:
                return httpx.Response(501, json={"error": {"message": "not supported"}})
            return httpx.Response(200, text=_sse({"content": "a + b", "stop": True}, done=False))
        return httpx.Response(200, text=_chat_stream("a + b"))

    mock_http(handler)
    auth = _auth(db)
    now = {"t": 1000.0}
    monkeypatch.setattr(fim.time, "monotonic", lambda: now["t"])
    _ask(client, auth, model="coder")
    can["infill"] = True
    now["t"] += fim.FORGET_AFTER - 1
    _ask(client, auth, model="coder")
    now["t"] += 2
    assert _ask(client, auth, model="coder").json()["text"] == "a + b"
    assert asked == ["/infill", "/v1/chat/completions", "/v1/chat/completions", "/infill"]


@pytest.mark.parametrize(
    "usage",
    [{"prompt_tokens": "many", "completion_tokens": None}, {"prompt_tokens": [1], "x": {}},
     {"prompt_tokens": float("nan"), "completion_tokens": -4}, {"completion_tokens": True}],
)
def test_usage_that_is_not_numbers_does_not_fail_a_finished_completion(
    client, db, registered, monkeypatch, usage
):
    _model(db, _connection(db))

    async def upstream(ask):
        yield fim.DELTA, "a + b"
        yield fim.END, {"finish": "stop", "usage": usage, "way": "chat"}

    monkeypatch.setattr(fim, "stream", upstream)
    for stream in (False, True):
        answer = _ask(client, _auth(db), model="coder", stream=stream)
        assert answer.status_code == 200 and "a + b" in answer.text
    whole = _ask(client, _auth(db), model="coder").json()
    assert whole["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert complete_api._IN_FLIGHT == {}


def test_a_request_whose_client_is_gone_stops_at_the_models_server(
    client, db, registered, monkeypatch
):
    """Through the route: the connection is looked at while the model's
    server is awaited, the stream to it is closed, and the slot given back."""
    from starlette.requests import Request

    _model(db, _connection(db))
    monkeypatch.setattr(complete_api, "WATCH_EVERY", 0.01)
    closed = []

    async def upstream(ask):
        try:
            await asyncio.sleep(30)  # the model's server, before its first word
            yield fim.DELTA, "never"
        finally:
            closed.append(True)

    async def gone(self) -> bool:
        return True

    monkeypatch.setattr(fim, "stream", upstream)
    monkeypatch.setattr(Request, "is_disconnected", gone)
    for stream in (False, True):
        assert _ask(client, _auth(db), model="coder", stream=stream).status_code == 499
    assert closed == [True, True] and complete_api._IN_FLIGHT == {}
