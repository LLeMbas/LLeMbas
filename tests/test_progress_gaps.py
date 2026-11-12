"""The progress phases a reply on the server could not say before: how far
the prompt has been read (llama.cpp's `return_progress`, quirks.json
`prompt-progress`), a model being swapped in (`loading_model`, only where the
server says so: llama-swap's `/running` and its loading banner, quirks.json
`swap-banner`), and a prompt waiting behind a running reply (`queued`)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from lembas.api.v2 import serialise
from lembas.db.models import Message
from lembas.services import progress
from lembas.services.llm import openai_client, swap
from lembas.services.llm.openai_client import Endpoint, LLMError, stream_chat
from tests.test_api_v2 import _bearer, _models

LOCAL = Endpoint("http://local.test/v1", "", {})
HOSTED = Endpoint("https://api.openai.com/v1", "k", {}, provider_type="openai")
PAYLOAD = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
OK = 'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n'


@pytest.fixture(autouse=True)
def _forget():
    openai_client._NO_PROGRESS.clear()
    openai_client._NO_STREAM_OPTIONS.clear()
    yield
    openai_client._NO_PROGRESS.clear()
    openai_client._NO_STREAM_OPTIONS.clear()


def _server(answers: list[tuple[int, str]]):
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        code, body = answers[min(len(calls), len(answers)) - 1]
        kind = "text/event-stream" if code == 200 else "application/json"
        return httpx.Response(code, content=body.encode(), headers={"content-type": kind})

    return handler, calls


async def _collect(endpoint, progress_wanted=True):
    return [c async for c in stream_chat(endpoint, PAYLOAD, progress=progress_wanted)]


# --- return_progress ---------------------------------------------------------------
async def test_a_watched_reply_on_a_local_server_asks_for_prompt_progress(mock_http):
    handler, calls = _server([(200, OK)])
    mock_http(handler)
    await _collect(LOCAL)
    assert calls[0]["return_progress"] is True
    await _collect(LOCAL, progress_wanted=False)
    assert "return_progress" not in calls[1]


async def test_a_hosted_api_is_never_asked(mock_http):
    handler, calls = _server([(200, OK)])
    mock_http(handler)
    await _collect(HOSTED)
    assert "return_progress" not in calls[0]


async def test_a_refusal_naming_the_field_is_learned_at_once(mock_http):
    refused = '{"error":{"message":"Unrecognized request argument: return_progress"}}'
    handler, calls = _server([(400, refused), (200, OK)])
    mock_http(handler)
    chunks = await _collect(LOCAL)
    assert chunks and "return_progress" not in calls[1]
    # stream_options was not the culprit and is still asked for.
    assert calls[1]["stream_options"] == {"include_usage": True}
    assert LOCAL.base_url in openai_client._NO_PROGRESS
    await _collect(LOCAL)
    assert "return_progress" not in calls[2]


async def test_an_unnamed_refusal_is_learned_only_if_the_retry_works(mock_http):
    vague = '{"error":{"message":"bad request"}}'
    handler, calls = _server([(400, vague), (200, OK)])
    mock_http(handler)
    await _collect(LOCAL)
    assert "return_progress" not in calls[1]
    assert LOCAL.base_url in openai_client._NO_PROGRESS


async def test_a_refusal_about_something_else_switches_nothing_off_for_good(mock_http):
    too_long = '{"error":{"message":"the prompt is too long"}}'
    handler, calls = _server([(400, too_long)])
    mock_http(handler)
    with pytest.raises(LLMError):
        await _collect(LOCAL)
    # Without progress, then without stream_options: still refused.
    assert "return_progress" in calls[0] and "return_progress" not in calls[1]
    assert LOCAL.base_url not in openai_client._NO_PROGRESS


async def test_a_refused_effort_is_left_to_the_callers_retry(mock_http):
    effort = '{"error":{"message":"reasoning_effort high is not supported"}}'
    handler, calls = _server([(400, effort), (200, OK)])
    mock_http(handler)
    await _collect(LOCAL)
    assert calls[1].get("return_progress") is True
    assert LOCAL.base_url not in openai_client._NO_PROGRESS


# --- The loading banner ------------------------------------------------------------
BANNER = "━━━━━\nllama-swap loading model: qwen\n...\nDone! (12.3s)\n━━━━━\n\n"


def test_the_banner_is_taken_out_of_the_reasoning_in_any_pieces():
    banner = swap.SwapBanner()
    out, states = [], []
    pieces = [BANNER[i : i + 7] for i in range(0, len(BANNER), 7)] + ["\n", "  ", "Hmm."]
    for piece in pieces:
        fed = banner.feed(piece)
        out.append(fed.text)
        states.append("loading" if fed.loading else "loaded" if fed.loaded else "")
    assert "".join(out) == "Hmm."
    assert "loading" in states and states.count("loaded") == 1
    assert states.index("loading") < states.index("loaded")


def test_reasoning_that_is_not_a_banner_passes_untouched():
    banner = swap.SwapBanner()
    assert banner.feed("The reader").text == "The reader"
    assert banner.feed(" wants").text == " wants"
    unclosed = swap.SwapBanner()
    assert unclosed.feed("━━━━━ opening").loading
    assert unclosed.feed("x" * (swap.MAX_BANNER + 10)).text.startswith("━━━━━ opening")


def _swap_server(states: list[str], *, chat=None):
    """llama-swap's `/running` answering each look with the next state."""
    looks: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/running"):
            state = states[min(len(looks), len(states) - 1)]
            looks.append(state)
            return httpx.Response(200, json={"running": [{"model": "qwen", "state": state}]})
        if chat is not None:
            return chat(request)
        return httpx.Response(404, json={})

    return handler, looks


async def test_the_watch_says_loading_while_the_server_does_and_loaded_after(
    mock_http, monkeypatch
):
    monkeypatch.setattr(swap, "FIRST", 0.0)
    monkeypatch.setattr(swap, "EVERY", 0.0)
    handler, looks = _swap_server(["starting", "starting", "ready"])
    mock_http(handler)
    said: list[str] = []
    watch = swap.LoadWatch(LOCAL, "qwen", lambda: said.append("loading"),
                           lambda: said.append("loaded")).start()
    for _ in range(100):
        if "loaded" in said:
            break
        await asyncio.sleep(0.01)
    await watch.stop()
    assert said == ["loading", "loaded"]


async def test_the_watch_says_nothing_where_the_server_is_not_llama_swap(
    mock_http, monkeypatch
):
    monkeypatch.setattr(swap, "FIRST", 0.0)
    looked: list[str] = []

    def handler(request):
        looked.append(request.url.path)
        return httpx.Response(404, json={})

    mock_http(handler)
    said: list[str] = []
    watch = swap.LoadWatch(LOCAL, "qwen", lambda: said.append("x"), lambda: None).start()
    await asyncio.sleep(0.05)
    await watch.stop()
    assert said == [] and looked == ["/running"]
    # Nor on a hosted API: it is never asked at all.
    assert not swap.LoadWatch.applies(HOSTED)


async def test_the_watch_stops_once_the_reply_is_heard(mock_http, monkeypatch):
    monkeypatch.setattr(swap, "FIRST", 0.05)
    handler, looks = _swap_server(["starting"])
    mock_http(handler)
    watch = swap.LoadWatch(LOCAL, "qwen", lambda: None, lambda: None).start()
    watch.heard.set()
    await asyncio.sleep(0.1)
    await watch.stop()
    assert looks == []


def _phases(monkeypatch) -> list[str]:
    seen: list[str] = []
    original = progress.Tracker.set

    def record(self, phase, **data):
        original(self, phase, **data)
        if phase and (not seen or seen[-1] != phase):
            seen.append(phase)

    monkeypatch.setattr(progress.Tracker, "set", record)
    return seen


def test_a_reply_behind_a_loading_banner_says_loading_and_keeps_it_out(
    client, db, registered, mock_http, monkeypatch
):
    frames = [
        {"choices": [{"delta": {"reasoning_content": BANNER[:20]}}]},
        {"choices": [{"delta": {"reasoning_content": BANNER[20:]}}]},
        {"prompt_progress": {"total": 100, "cache": 0, "processed": 50, "time_ms": 3}},
        {"choices": [{"delta": {"reasoning_content": "Bread."}}]},
        {"choices": [{"delta": {"content": "Lembas."}}]},
    ]
    body = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
    sent: list[dict] = []

    def chat(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload.get("stream"):
            sent.append(payload)
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "T"}}]})

    handler, _ = _swap_server(["ready"], chat=chat)
    _models(db)
    mock_http(handler)
    seen = _phases(monkeypatch)
    headers = _bearer(db)
    made = client.post("/api/v2/chats", headers=headers, json={"text": "Hi"}).json()
    url = f"/api/v2/chats/{made['chat']['id']}/messages/{made['reply']['id']}/stream"
    client.get(url, headers=headers)
    reply = db.get(Message, made["reply"]["id"])
    db.refresh(reply)
    assert sent[0]["return_progress"] is True
    assert seen[:4] == ["reading_prompt", "loading_model", "reading_prompt", "thinking"]
    assert "━" not in json.dumps(reply.usage_json or {}) + (reply.content or "")
    assert reply.content.strip() == "Lembas."


# --- queued ----------------------------------------------------------------------
def test_a_prompt_queued_behind_a_reply_says_so_with_the_turns_ahead(
    client, db, registered, mock_http
):
    from lembas.services import generation as generation_service

    _models(db)
    headers = _bearer(db)
    def chat(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "T"}}]})

    mock_http(chat)
    made = client.post("/api/v2/chats", headers=headers, json={"text": "Hi"}).json()
    chat_id = made["chat"]["id"]
    # The real first reply to its end, so that what is counted below is the
    # queue behind the stand-in and nothing a finishing reply drained from it.
    client.get(
        f"/api/v2/chats/{chat_id}/messages/{made['reply']['id']}/stream", headers=headers
    )
    # As if the first reply were still being written.
    running = generation_service.Generation(chat_id=chat_id, message_id="busy")
    generation_service._RUNNING["busy"] = running
    try:
        first = client.post(f"/api/v2/chats/{chat_id}/messages", headers=headers,
                            json={"text": "And?"}).json()
        second = client.post(f"/api/v2/chats/{chat_id}/messages", headers=headers,
                             json={"text": "And more?"}).json()
        assert first["reply"] is None and second["reply"] is None
        assert first["message"]["progress"]["phase"] == "queued"
        assert first["message"]["progress"]["ahead"] == 1
        assert second["message"]["progress"]["ahead"] == 2
        assert set(second["message"]["progress"]) == {"phase", "since", "ahead"}
        listed = client.get(f"/api/v2/chats/{chat_id}/messages", headers=headers).json()
        queued = [m for m in listed["messages"] if m["queued"]]
        assert [m["progress"]["ahead"] for m in queued] == [1, 2]
        assert progress.text(queued[1]["progress"]) == "Queued: 2 ahead"
    finally:
        generation_service._RUNNING.pop("busy", None)
    delivered = [m for m in listed["messages"] if not m["queued"] and m["role"] == "user"]
    assert all(m["progress"] is None for m in delivered)


def test_only_a_queued_prompt_has_progress():
    row = Message(id="m", chat_id="c", role="assistant", queued=True)
    assert serialise.queued_progress(row) is None
