"""How full the context is, what a reply cost, and how fast it arrived."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import Connection, Model
from lembas.services import metrics, tokens
from lembas.services.crypto import encrypt
from lembas.services.llm.openai_client import chunk_usage, context_from


def _model(db, **kwargs) -> Model:
    connection = Connection(
        name="Test", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt("")
    )
    db.add(connection)
    db.commit()
    model = Model(connection_id=connection.id, model_id="test-model", **kwargs)
    db.add(model)
    db.commit()
    return model


# --- Reading a context length off /v1/models ---------------------------------
def test_context_length_is_read_from_any_of_the_spellings():
    assert context_from({"id": "m", "context_length": 8192}) == 8192
    assert context_from({"id": "m", "max_model_len": 32768}) == 32768
    assert context_from({"id": "m", "context_window": 4096}) == 4096
    assert context_from({"id": "m", "meta": {"n_ctx": 2048}}) == 2048


def test_a_quoted_number_is_accepted_but_a_label_is_not():
    """Some servers quote it. "8192 tokens" is a label, not a measurement."""
    assert context_from({"id": "m", "context_length": "8192"}) == 8192
    assert context_from({"id": "m", "context_length": "8192 tokens"}) == 0


def test_an_absent_or_implausible_context_length_is_zero():
    assert context_from({"id": "m"}) == 0
    assert context_from({"id": "m", "context_length": 0}) == 0
    assert context_from({"id": "m", "context_length": 64}) == 0
    assert context_from({"id": "m", "context_length": 10**9}) == 0
    # True is an int in Python, and it is not a context length.
    assert context_from({"id": "m", "context_length": True}) == 0


# --- Discovery ---------------------------------------------------------------
async def test_discovery_fills_in_a_context_length(client: TestClient, db, registered, mock_http):
    import httpx

    mock_http(
        lambda _r: httpx.Response(
            200, json={"data": [{"id": "big-model", "context_length": 16384}]}
        )
    )
    client.post(
        "/admin/connections",
        data={"name": "Local", "base_url": "http://x.test", "api_key": ""},
        follow_redirects=False,
    )
    model = db.scalar(select(Model).where(Model.model_id == "big-model"))
    assert model.context_length == 16384


async def test_discovery_never_overwrites_a_number_an_admin_typed(
    client: TestClient, db, registered, mock_http
):
    """A refresh must not undo a correction. Administrators set this precisely
    because the endpoint was wrong or silent."""
    import httpx

    mock_http(
        lambda _r: httpx.Response(200, json={"data": [{"id": "m", "context_length": 4096}]})
    )
    client.post(
        "/admin/connections",
        data={"name": "Local", "base_url": "http://x.test", "api_key": ""},
        follow_redirects=False,
    )
    model = db.scalar(select(Model).where(Model.model_id == "m"))
    model.context_length = 131072
    db.commit()

    connection = db.scalar(select(Connection))
    client.post(f"/admin/connections/{connection.id}/refresh", follow_redirects=False)

    db.refresh(model)
    assert model.context_length == 131072


# --- The admin field ---------------------------------------------------------
def test_an_admin_can_set_and_clear_the_context_length(client: TestClient, db, registered):
    model = _model(db)
    client.post(
        f"/admin/models/{model.id}",
        data={"context_length": "8192", "position": ""},
        follow_redirects=False,
    )
    db.refresh(model)
    assert model.context_length == 8192

    client.post(
        f"/admin/models/{model.id}", data={"context_length": "", "position": ""},
        follow_redirects=False,
    )
    db.refresh(model)
    assert model.context_length == 0


def test_junk_in_the_context_length_field_is_ignored_not_a_500(
    client: TestClient, db, registered
):
    model = _model(db, context_length=4096)
    response = client.post(
        f"/admin/models/{model.id}",
        data={"context_length": "eight thousand", "position": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.refresh(model)
    assert model.context_length == 4096


# --- Usage off the wire -------------------------------------------------------
def test_usage_is_read_from_a_usage_chunk():
    chunk = {
        "choices": [],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }
    assert chunk_usage(chunk) == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
    }


def test_a_missing_total_is_worked_out():
    chunk = {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    assert chunk_usage(chunk)["total_tokens"] == 120


def test_an_ordinary_chunk_carries_no_usage():
    assert chunk_usage({"choices": [{"delta": {"content": "hi"}}]}) is None
    assert chunk_usage({}) is None
    assert chunk_usage({"usage": "lots"}) is None


def test_an_all_zero_usage_object_is_not_an_answer():
    """Some servers attach zeros to every chunk and the real numbers only at the
    end. Believing the zeros freezes the count at nothing."""
    chunk = {"choices": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    assert chunk_usage(chunk) is None


def test_the_other_accessors_still_ignore_a_usage_chunk():
    """They return early on `choices: []`, which is exactly the shape of one.
    That is what lets a usage chunk through the loop untouched."""
    from lembas.services.llm.openai_client import (
        delta_reasoning,
        delta_text,
        delta_tool_calls,
        finish_reason,
    )

    chunk = {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    assert delta_text(chunk) == ""
    assert delta_reasoning(chunk) == ""
    assert delta_tool_calls(chunk) == []
    assert finish_reason(chunk) == ""


async def test_stream_options_is_asked_for(mock_http):
    import json as json_module

    import httpx

    from lembas.services.llm.openai_client import Endpoint, stream_chat

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json_module.loads(request.content))
        return httpx.Response(200, text="data: [DONE]\n\n")

    mock_http(handler)
    async for _ in stream_chat(Endpoint("http://ask.test", "", {}), {"model": "m"}):
        pass

    assert seen[0]["stream_options"] == {"include_usage": True}


async def test_an_endpoint_that_rejects_stream_options_is_asked_once(mock_http):
    """A 400 for an unknown key is the same hazard as sending `tools` to an
    endpoint without support. Retry without it, then stop asking."""
    import json as json_module

    import httpx

    from lembas.services.llm.openai_client import (
        _NO_STREAM_OPTIONS,
        Endpoint,
        stream_chat,
    )

    _NO_STREAM_OPTIONS.discard("http://fussy.test")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json_module.loads(request.content)
        seen.append(body)
        if "stream_options" in body:
            return httpx.Response(400, json={"error": {"message": "unknown field"}})
        reply = 'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=reply)

    mock_http(handler)
    endpoint = Endpoint("http://fussy.test", "", {})

    text = [c async for c in stream_chat(endpoint, {"model": "m"})]
    assert text, "the retry should have produced the reply"
    assert len(seen) == 2

    # Second reply: it already knows, so one request and no stream_options.
    async for _ in stream_chat(endpoint, {"model": "m"}):
        pass
    assert len(seen) == 3
    assert "stream_options" not in seen[2]
    _NO_STREAM_OPTIONS.discard("http://fussy.test")


# --- The estimate -------------------------------------------------------------
def test_the_estimate_is_about_four_characters_a_token():
    assert tokens.estimate("") == 0
    assert tokens.estimate("x" * 400) == 100


def test_typed_content_parts_are_counted_and_an_image_by_its_size():
    """An image counts by its dimensions, never by its base64 (2026-10-23: it
    counted nothing, so a chat of pictures read nearly empty). One whose size
    cannot be read has a flat cost."""
    content = [
        {"type": "text", "text": "x" * 40},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA" * 500}},
    ]
    assert tokens.estimate_content(content) == 10 + tokens.IMAGE_FALLBACK_TOKENS


def test_a_request_estimate_includes_the_tools_array():
    """Thirteen schemas is a meaningful slice of a short window; leaving them
    out would read low exactly when it matters."""
    payload = {
        "messages": [{"role": "user", "content": "x" * 40}],
        "tools": [
            {"function": {"name": "web_search", "description": "y" * 400, "parameters": {}}}
        ],
    }
    assert tokens.estimate_request(payload) > tokens.estimate_request(
        {"messages": payload["messages"]}
    )


# --- The Metrics object -------------------------------------------------------
def test_the_percentage_is_zero_when_nobody_said_how_big_the_window_is():
    """Unknown must stay tellable from small. A percentage of an unknown total
    is a made-up number in a place people trust numbers."""
    assert metrics.Metrics(context_tokens=5000, context_limit=0).percent == 0
    assert metrics.Metrics(context_tokens=5000, context_limit=10000).percent == 50


def test_pressure_marks_the_bar_only_when_it_is_worth_noticing():
    assert metrics.Metrics(context_tokens=50, context_limit=100).pressure == ""
    assert metrics.Metrics(context_tokens=85, context_limit=100).pressure == "warning"
    assert metrics.Metrics(context_tokens=96, context_limit=100).pressure == "danger"
    assert metrics.Metrics(context_tokens=0, context_limit=0).pressure == ""


def test_speed_needs_both_a_count_and_a_clock():
    assert metrics.Metrics(completion_tokens=100, elapsed_ms=2000).tokens_per_second == 50.0
    assert metrics.Metrics(completion_tokens=100, elapsed_ms=0).tokens_per_second == 0.0
    assert metrics.Metrics(completion_tokens=0, elapsed_ms=2000).tokens_per_second == 0.0


def test_metrics_survive_a_round_trip_through_the_row():
    original = metrics.Metrics(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        context_tokens=120,
        context_limit=8192,
        estimated=True,
        elapsed_ms=1500,
        rounds=2,
    )
    assert metrics.from_message(metrics.to_json(original)) == original


def test_a_row_with_no_usage_reads_as_nothing_rather_than_failing():
    assert metrics.from_message(None).has_anything is False
    assert metrics.from_message({}).has_anything is False
    assert metrics.from_message({"prompt_tokens": "lots"}).prompt_tokens == 0


# --- Through a generation -----------------------------------------------------
def _chunks(*frames: str) -> str:
    return "".join(f"data: {frame}\n\n" for frame in frames) + "data: [DONE]\n\n"


async def test_usage_is_summed_across_tool_rounds(db, registered, make_chat, monkeypatch):
    """Prompt and completion are what the reply cost; context_tokens is what the
    window holds. A three-round reply pays for its prompt three times and only
    ever occupies the window once."""
    from lembas.services import generation as generation_service

    generation = generation_service.Generation(chat_id="c", message_id="m")
    for prompt_tokens, completion_tokens in ((100, 10), (250, 20)):
        counts = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
        generation.prompt_tokens += counts["prompt_tokens"]
        generation.completion_tokens += counts["completion_tokens"]
        generation.context_tokens = prompt_tokens + completion_tokens

    assert generation.prompt_tokens == 350
    assert generation.completion_tokens == 30
    assert generation.context_tokens == 270


async def test_a_reply_records_what_it_cost(client: TestClient, db, registered, mock_http):
    import httpx

    from lembas.db.models import Message

    def handler(_request: httpx.Request) -> httpx.Response:
        if _request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "m", "context_length": 1000}]})
        return httpx.Response(
            200,
            text=_chunks(
                '{"choices":[{"delta":{"content":"Waybread."}}]}',
                '{"choices":[],"usage":{"prompt_tokens":120,"completion_tokens":8}}',
            ),
        )

    mock_http(handler)
    client.post(
        "/admin/connections",
        data={"name": "Local", "base_url": "http://x.test", "api_key": ""},
        follow_redirects=False,
    )
    client.post("/api/chats/start", data={"content": "what is lembas?"})
    reply = db.scalar(select(Message).where(Message.role == "assistant"))

    # Following the stream is what runs the generation to completion.
    with client.stream(
        "GET", f"/api/chats/{reply.chat_id}/messages/{reply.id}/stream"
    ) as response:
        list(response.iter_lines())

    db.refresh(reply)
    assert reply.usage_json["prompt_tokens"] == 120
    assert reply.usage_json["completion_tokens"] == 8
    assert reply.usage_json["context_limit"] == 1000
    assert reply.usage_json["estimated"] is False
    assert reply.usage_json["elapsed_ms"] >= 0


def test_the_chips_render_from_a_stored_row(client: TestClient, db, registered, make_chat):
    from lembas.db.models import Message

    _model(db)
    chat_id = make_chat()
    db.add(
        Message(
            chat_id=chat_id,
            role="assistant",
            content="Waybread.",
            usage_json={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "context_tokens": 120,
                "context_limit": 1000,
                "estimated": False,
                "elapsed_ms": 2000,
                "rounds": 1,
            },
        )
    )
    db.commit()

    page = client.get(f"/chat/{chat_id}").text
    # Context first, both numbers; the cost says it is what was processed.
    assert "120 processed" in page
    assert "120 / 1k · 12%" in page
    assert "10.0 tok/s" in page
    assert "~" not in page.split("msg__metrics")[1][:400]


def test_an_estimated_row_says_so(client: TestClient, db, registered, make_chat):
    from lembas.db.models import Message

    _model(db)
    chat_id = make_chat()
    db.add(
        Message(
            chat_id=chat_id,
            role="assistant",
            content="Waybread.",
            usage_json={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "context_tokens": 120,
                "context_limit": 1000,
                "estimated": True,
                "elapsed_ms": 2000,
                "rounds": 1,
            },
        )
    )
    db.commit()

    page = client.get(f"/chat/{chat_id}").text
    assert "~120 processed" in page
    assert "reports no token counts" in page


def test_no_context_length_means_no_percentage(client: TestClient, db, registered, make_chat):
    from lembas.db.models import Message

    _model(db)
    chat_id = make_chat()
    db.add(
        Message(
            chat_id=chat_id,
            role="assistant",
            content="Waybread.",
            usage_json={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "context_tokens": 120,
                "context_limit": 0,
                "estimated": False,
                "elapsed_ms": 2000,
                "rounds": 1,
            },
        )
    )
    db.commit()

    page = client.get(f"/chat/{chat_id}").text
    assert "120 processed" in page
    # Without a window there is no percentage, but what it holds is still said.
    assert "% " not in page.split("msg__metrics")[1][:600]
    assert "in context" in page


# --- Counted, estimated, and the gap between them -----------------------------
def _live(**fields):
    """A Generation with just the fields the metrics read."""
    from lembas.services import generation as generation_service

    generation = generation_service.Generation(chat_id="c", message_id="m")
    for key, value in fields.items():
        setattr(generation, key, value)
    return generation


def test_a_reported_count_is_shown_verbatim():
    """Ours is four characters to a token. Overriding a number the endpoint
    actually counted with that would be a downgrade dressed as a fix."""
    generation = _live(
        reported_usage=True,
        prompt_tokens=120,
        completion_tokens=8,
        content=["Waybread."],
        counted_chars=len("Waybread."),
    )

    got = metrics.from_generation(generation)

    assert got.prompt_tokens == 120
    assert got.completion_tokens == 8
    assert got.estimated is False


def test_the_counts_keep_moving_between_usage_chunks():
    """Usage arrives once per round, so on a long agent reply the reported
    figures used to stand still for minutes while text streamed underneath
    them -- `reported or estimate` never reaches its fallback again once the
    first chunk has landed. Only what has been written since the last count is
    estimated."""
    generation = _live(
        reported_usage=True,
        prompt_tokens=100,
        completion_tokens=10,
        context_tokens=110,
        content=["x" * 40],
        counted_chars=0,
    )

    got = metrics.from_generation(generation)

    assert got.completion_tokens == 20, "10 counted plus 40 characters of new text"
    assert got.context_tokens == 120


def test_the_interpolation_is_zero_the_moment_a_count_lands():
    """Which is what makes the figure land exactly on the reported total at the
    end of a round rather than drifting a little past it every time."""
    generation = _live(
        reported_usage=True,
        completion_tokens=10,
        content=["x" * 40],
        counted_chars=40,
    )

    assert metrics.from_generation(generation).completion_tokens == 10


def test_a_reply_nobody_counted_still_says_so_once_it_is_stored():
    """`estimated` was inferred from "are both counts non-zero?", and the
    end-of-reply fallback made that true of a reply nobody had counted. So the
    tilde showed all the way through and then vanished at the moment the numbers
    were written down, which is where it mattered most."""
    generation = _live(content=["Waybread."], prompt_estimate_total=30)

    got = metrics.from_generation(generation)

    assert got.estimated is True
    assert got.prompt_tokens == 30
    assert got.completion_tokens > 0


def test_the_prompt_does_not_jump_when_the_reply_ends():
    """It read the latest round's estimate live and the sum of every round's
    when stored, so a reply that called a tool visibly changed number at the
    `done` frame. Both are the sum now."""
    generation = _live(prompt_estimate=400, prompt_estimate_total=1000)

    assert metrics.from_generation(generation).prompt_tokens == 1000


def test_estimate_chars_does_not_invent_a_token_out_of_nothing():
    """`estimate` floors at one token for any non-empty string, which is right
    for a piece of text and wrong for a difference between two lengths."""
    assert tokens.estimate_chars(0) == 0
    assert tokens.estimate_chars(-5) == 0
    assert tokens.estimate_chars(4) == 1
