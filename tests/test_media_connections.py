"""Media from connections: speech and pictures a connection carries itself.

The instance's audio endpoints and its ComfyUI are the **defaults**; a
connection may carry speech and image models of its own, and two switches say
whether a chat on that connection uses them. What these tests hold is the
routing -- which source serves a request, and in which order -- because every
way it can go wrong is silent: a chat on a local model whose dictation went to
a cloud provider would return perfectly good text.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from sqlalchemy import select, text

from lembas.db.models import TOKEN_DEVICE, Chat, Connection, ImageWorkflow, Model, Usage, User
from lembas.security import api_tokens
from lembas.services import settings_store
from lembas.services import tools as tools_service
from lembas.services.crypto import encrypt
from lembas.services.images import tool as image_tool
from lembas.services.llm import providers
from lembas.services.llm.openai_client import LLMError
from lembas.services.media import dashscope, pictures, sources, speech, store

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)
PLAN_URL = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"
CLOUD_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


@pytest.fixture(autouse=True)
def _public_names(monkeypatch):
    """A result link is checked after resolution (`fetch.check_url`); no test
    looks a name up for real."""
    from lembas.services import fetch

    def resolve(host, *_args, **_kwargs):
        local = host in ("127.0.0.1", "localhost") or host.startswith("172.16.")
        return [(2, 1, 6, "", ("127.0.0.1" if local else "93.184.216.34", 0))]

    monkeypatch.setattr(fetch.socket, "getaddrinfo", resolve)
    api_tokens.reset_rates()


def _connection(db, name="Cloud", **fields) -> Connection:
    fields.setdefault("base_url", "https://api.example.org/v1")
    fields.setdefault("api_key_encrypted", encrypt("connection-key"))
    row = Connection(name=name, **fields)
    db.add(row)
    db.commit()
    db.add(Model(connection_id=row.id, model_id=f"{row.provider}-chat",
                 capabilities_json={"tools": True, "tool_image": True}))
    db.commit()
    return row


def _media(db, connection, *models, audio=True, image=True) -> None:
    connection.media_json = store.normalise(
        {"prefer_audio": audio, "prefer_image": image,
         "models": [{"enabled": True, **m} for m in models]}
    )
    db.commit()


def _defaults(db) -> None:
    """The instance's own endpoints, as an instance from before has them."""
    settings_store.update(
        db,
        {"stt_enabled": True, "stt_base_url": "http://stt.test/v1", "stt_model": "whisper-local",
         "stt_per_minute": 0.6,
         "tts_enabled": True, "tts_base_url": "http://tts.test/v1", "tts_model": "kokoro"},
        key=settings_store.AUDIO,
    )


def _on(db) -> None:
    """Dictation, read-aloud and pictures switched on, with no default source."""
    settings_store.update(db, {"stt_enabled": True, "tts_enabled": True},
                          key=settings_store.AUDIO)
    settings_store.update(db, {"enabled": True}, key=settings_store.IMAGES)


def _comfy(db) -> None:
    db.add(ImageWorkflow(slug="base", name="Base", description="general",
                         workflow_json=image_tool_base()))
    settings_store.update(
        db,
        {"enabled": True, "base_url": "http://comfy.test:8188",
         "checkpoints": ["sd.safetensors"], "review_enabled": False},
        key=settings_store.IMAGES,
    )
    db.commit()


def image_tool_base() -> dict:
    from pathlib import Path

    import lembas

    return json.loads(
        (Path(lembas.__file__).parent / "services/images/base_workflow.json").read_text("utf-8")
    )


# --- What is stored ---------------------------------------------------------------
def test_media_is_normalised_and_a_model_is_kept_once():
    media = store.normalise(
        {"prefer_audio": 1, "models": [
            {"capability": "tts", "model": " voice-one ", "enabled": True, "price": "2,5",
             "voice": "ada"},
            {"capability": "tts", "model": "voice-one", "enabled": False},
            {"capability": "video", "model": "nope"},
            {"capability": "stt", "model": ""},
            {"capability": "image", "model": "draw\x00-one", "price": -3, "voice": "x"},
        ]}
    )
    assert media["prefer_audio"] is True and media["prefer_image"] is False
    assert media["models"] == [
        {"capability": "tts", "model": "voice-one", "enabled": True, "price": 2.5, "voice": "ada"},
        {"capability": "image", "model": "draw-one", "enabled": False, "price": 0.0},
    ]


def test_a_known_type_is_prefilled_and_a_generic_one_is_typed():
    assert {e.capability for e in store.offers(providers.OPENAI)} == set(store.CAPABILITIES)
    assert store.offers(providers.OPENAI_COMPATIBLE) == ()
    assert all(not e.enabled for e in store.offers(providers.ALIBABA_PLAN, PLAN_URL))
    # A plan that serves chat models only offers nothing.
    assert store.offers(providers.ALIBABA_PLAN, "https://coding-intl.dashscope.aliyuncs.com/v1") \
        == ()
    assert not store.supported(providers.ANTHROPIC, "https://api.anthropic.com")


def test_the_dialect_is_the_providers_not_a_guess():
    local = "http://172.16.0.5:9000/v1"
    assert store.dialect("openai_compatible", local, "tts", "x") == store.OPENAI
    assert store.dialect("alibaba", CLOUD_URL, "stt", "qwen3-asr-flash") == store.ALIBABA_CHAT_ASR
    assert store.dialect("alibaba_plan", PLAN_URL, "stt", "qwen-audio-3.0-asr-flash") \
        == store.DASHSCOPE_ASR
    assert store.dialect("alibaba", CLOUD_URL, "tts", "qwen3-tts-flash") \
        == store.DASHSCOPE_TTS_CHAT
    assert store.dialect("alibaba_plan", PLAN_URL, "tts", "qwen-audio-3.0-tts-plus") \
        == store.DASHSCOPE_TTS
    assert store.dialect("alibaba", CLOUD_URL, "image", "anything") == store.DASHSCOPE_IMAGE


def test_the_new_hosts_are_offered_as_regions():
    plan = [r.base_url for r in providers.ALIBABA_PLAN_REGIONS]
    assert PLAN_URL in plan
    assert "https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1" in [
        r.base_url for r in providers.ALIBABA_REGIONS
    ]
    for region in providers.ALIBABA_REGIONS + providers.ALIBABA_PLAN_REGIONS:
        found = providers.by_host(region.base_url.replace("{WorkspaceId}", "ws"))
        assert found is not None and found.id in (providers.ALIBABA, providers.ALIBABA_PLAN)
    assert providers.by_host(PLAN_URL).id == providers.ALIBABA_PLAN


def test_an_older_connection_row_gains_empty_media(db, user_id):
    from lembas.db.migrations import sync_schema
    from lembas.db.session import get_engine

    row = _connection(db)
    row_id = row.id
    db.close()
    engine = get_engine()
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE connections DROP COLUMN media_json"))
    sync_schema(engine)
    back = db.get(Connection, row_id)
    assert back.media_json == {}
    assert store.own(back, "stt") is None


# --- The one decision -------------------------------------------------------------
def test_a_connection_without_media_stays_on_the_default(db, user_id):
    _defaults(db)
    local = _connection(db, "Local", base_url="http://172.16.0.5:8080/v1")
    chain = sources.chain(db, "stt", local)
    assert [s.origin for s in chain] == [sources.ORIGIN_INSTANCE]
    assert chain[0].endpoint.base_url == "http://stt.test/v1"


def test_its_own_comes_first_and_the_default_stays_behind_it(db, user_id):
    _defaults(db)
    cloud = _connection(db)
    _media(db, cloud, {"capability": "stt", "model": "hear-one", "price": 1.2})
    chain = sources.chain(db, "stt", cloud)
    assert [(s.origin, s.own, s.model) for s in chain] == [
        (sources.ORIGIN_CONNECTION, True, "hear-one"),
        (sources.ORIGIN_INSTANCE, False, "whisper-local"),
    ]
    # No own text-to-speech model: reading stays on the default.
    assert [s.origin for s in sources.chain(db, "tts", cloud)] == [sources.ORIGIN_INSTANCE]


def test_the_switch_off_or_the_model_unticked_is_the_default(db, user_id):
    _defaults(db)
    cloud = _connection(db)
    _media(db, cloud, {"capability": "stt", "model": "hear-one"}, audio=False)
    assert [s.origin for s in sources.chain(db, "stt", cloud)] == [sources.ORIGIN_INSTANCE]
    cloud.media_json = store.normalise(
        {"prefer_audio": True,
         "models": [{"capability": "stt", "model": "hear-one", "enabled": False}]}
    )
    db.commit()
    assert [s.origin for s in sources.chain(db, "stt", cloud)] == [sources.ORIGIN_INSTANCE]


def test_a_local_chat_is_never_sent_to_another_connections_media(db, user_id):
    """The privacy direction: the fallback goes from a connection's own to the
    default, and a connection's media serves that connection's chats only."""
    _defaults(db)
    cloud = _connection(db)
    _media(db, cloud, {"capability": "stt", "model": "hear-one"},
           {"capability": "tts", "model": "say-one"}, {"capability": "image", "model": "draw"})
    local = _connection(db, "Local", base_url="http://172.16.0.5:8080/v1")
    for capability in ("stt", "tts"):
        chain = sources.chain(db, capability, local)
        assert [s.origin for s in chain] == [sources.ORIGIN_INSTANCE]
        assert all(s.connection_id != cloud.id for s in chain)
    # No ComfyUI and no default: nothing draws for the local chat.
    settings_store.update(db, {"enabled": True}, key=settings_store.IMAGES)
    assert sources.chain(db, "image", local) == []
    assert [s.connection_id for s in sources.chain(db, "image", cloud)] == [cloud.id]


def test_the_administrator_may_make_a_connections_model_the_default(db, user_id):
    cloud = _connection(db)
    _media(db, cloud, {"capability": "tts", "model": "say-one", "voice": "ada"})
    settings_store.update(
        db, {"tts_enabled": True, "tts_connection": f"{cloud.id}/say-one"},
        key=settings_store.AUDIO,
    )
    local = _connection(db, "Local", base_url="http://172.16.0.5:8080/v1")
    chain = sources.chain(db, "tts", local)
    assert [(s.origin, s.own, s.model, s.voice) for s in chain] == [
        (sources.ORIGIN_CONNECTION, False, "say-one", "ada")
    ]
    # The connection's own chat: the same model once, not twice.
    assert len(sources.chain(db, "tts", cloud)) == 1
    # A default whose model has gone is nothing -- never another one.
    cloud.media_json = {}
    db.commit()
    assert sources.chain(db, "tts", local) == []


def test_a_plans_media_is_for_a_persons_own_turn(db, user_id):
    _defaults(db)
    plan = _connection(db, "Plan", base_url=PLAN_URL, provider_type=providers.ALIBABA_PLAN)
    _media(db, plan, {"capability": "stt", "model": "qwen-audio-3.0-asr-flash", "price": 9})
    own = sources.chain(db, "stt", plan)
    assert own[0].own and own[0].price == 0.0  # a subscription is never priced
    unattended = sources.chain(db, "stt", plan, interactive=False)
    assert [s.origin for s in unattended] == [sources.ORIGIN_INSTANCE]
    # Nor as the default the administrator chose.
    settings_store.update(
        db, {"stt_base_url": "", "stt_connection": f"{plan.id}/qwen-audio-3.0-asr-flash"},
        key=settings_store.AUDIO,
    )
    assert sources.chain(db, "stt", None, interactive=False) == []
    assert len(sources.chain(db, "stt", None, interactive=True)) == 1


def test_which_chat_is_the_chats_model_or_the_model_chosen(db, user_id):
    cloud = _connection(db)
    user = db.get(User, user_id)
    chat = Chat(user_id=user_id, model_id="cloud-chat", connection_id=cloud.id)
    db.add(chat)
    db.commit()
    assert sources.connection_of(db, user, chat_id=chat.id).id == cloud.id
    assert sources.connection_of(db, user, model="cloud/cloud-chat").id == cloud.id
    assert sources.connection_of(db, user, model="cloud-chat").id == cloud.id
    assert sources.connection_of(db, user) is None
    assert sources.connection_of(db, user, model="no-such-model") is None
    # Somebody else's chat is no context at all.
    other = User(name="Sam", email="sam@example.org", password_hash="x")
    db.add(other)
    db.commit()
    assert sources.connection_of(db, other, chat_id=chat.id) is None


# --- The clients ------------------------------------------------------------------
async def test_qwen_asr_goes_through_the_chat_route_with_an_audio_part(db, user_id, mock_http):
    _on(db)
    cloud = _connection(db, base_url=CLOUD_URL, provider_type=providers.ALIBABA)
    _media(db, cloud, {"capability": "stt", "model": "qwen3-asr-flash"})
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": " Hello there. "}}],
            "usage": {"seconds": 7},
        })

    mock_http(handler)
    source = sources.chain(db, "stt", cloud)[0]
    heard = await speech.transcribe(
        source, data=b"abc", filename="a.webm", content_type="audio/webm;codecs=opus",
        language="sk-SK",
    )
    assert heard == ("Hello there.", 7.0)
    assert seen["url"] == f"{CLOUD_URL}/chat/completions"
    assert seen["auth"] == "Bearer connection-key"
    part = seen["body"]["messages"][0]["content"][0]
    assert part["type"] == "input_audio"
    assert part["input_audio"]["data"] == "data:audio/webm;base64," + base64.b64encode(
        b"abc").decode()
    # Slovak is not a language this recogniser takes a hint for: detection.
    assert "asr_options" not in seen["body"]


async def test_native_speech_is_collected_and_the_key_stays_home(db, user_id, mock_http):
    """The provider answers with a link that expires. The bytes are fetched
    here, without the connection's key, and the link is never handed on."""
    _on(db)
    plan = _connection(db, "Plan", base_url=PLAN_URL, provider_type=providers.ALIBABA_PLAN)
    _media(db, plan, {"capability": "tts", "model": "qwen-audio-3.0-tts-plus", "voice": "ada"})
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), request.headers.get("authorization")))
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["input"]["voice"] == "ada" and body["input"]["format"] == "mp3"
            assert len(body["input"]["text"]) <= speech.ALIBABA_TTS_CHARS
            return httpx.Response(200, json={
                "output": {"audio": {"url": "https://results.example.org/a.mp3?Expires=1"}},
                "usage": {"characters": len(body["input"]["text"])},
            })
        return httpx.Response(200, content=b"MP3")

    mock_http(handler)
    source = sources.chain(db, "tts", plan)[0]
    media_type, stream = await speech.speak(source, "One sentence. " * 80)
    audio = b"".join([chunk async for chunk in stream])
    assert media_type == "audio/mpeg"
    posts = [c for c in calls if c[0] == "POST"]
    gets = [c for c in calls if c[0] == "GET"]
    assert len(posts) == len(gets) >= 2 and audio == b"MP3" * len(gets)
    assert all(
        url == "https://token-plan.maas.qwencloudapi.com/api/v1/services/audio/tts/"
        "SpeechSynthesizer" and auth == "Bearer connection-key"
        for _, url, auth in posts
    )
    assert all(auth is None for _, _, auth in gets)


async def test_a_result_link_to_this_network_is_refused(mock_http):
    mock_http(lambda _r: httpx.Response(200, content=b"secret"))
    with pytest.raises(LLMError):
        await dashscope.collect("http://172.16.0.1/admin", limit=1000)
    with pytest.raises(LLMError):
        await dashscope.collect("file:///etc/passwd", limit=1000)
    with pytest.raises(LLMError):
        await dashscope.collect("https://results.example.org/big", limit=3)


def test_several_wav_files_become_one_stream():
    import io
    import wave

    def wav(samples: bytes) -> bytes:
        out = io.BytesIO()
        with wave.open(out, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24000)
            handle.writeframes(samples)
        return out.getvalue()

    header, samples = speech.wav_parts(wav(b"\x01\x00" * 10))
    assert samples == b"\x01\x00" * 10 and header.endswith(b"data" + (20).to_bytes(4, "little"))
    assert speech.wav_parts(b"not a wav") is None
    assert all(len(p) <= 600 for p in speech.pieces("word " * 1000))
    assert "".join(speech.pieces("A b. C d! E f?")) == "A b. C d! E f?"


async def test_a_picture_from_the_native_route_is_bytes_not_a_link(db, user_id, mock_http):
    cloud = _connection(db, base_url=CLOUD_URL, provider_type=providers.ALIBABA)
    _media(db, cloud, {"capability": "image", "model": "qwen-image-3.0"})
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["url"], seen["body"] = str(request.url), json.loads(request.content)
            return httpx.Response(200, json={"output": {"choices": [{"message": {"content": [
                {"image": "https://results.example.org/x.png?Expires=1"}]}}]}})
        assert "authorization" not in request.headers
        return httpx.Response(200, content=PNG)

    mock_http(handler)
    settings_store.update(db, {"enabled": True}, key=settings_store.IMAGES)
    source = sources.chain(db, "image", cloud)[0]
    payload = await pictures.generate(source, "a lamp", negative="text", width=1024, height=768)
    assert payload == PNG
    assert seen["url"] == (
        "https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    )
    assert seen["body"]["parameters"] == {
        "n": 1, "watermark": False, "size": "1024*768", "negative_prompt": "text"
    }
    assert seen["body"]["input"]["messages"][0]["content"] == [{"text": "a lamp"}]


async def test_an_openai_shaped_connection_draws_by_the_openai_route(db, user_id, mock_http):
    local = _connection(db, "Local", base_url="http://172.16.0.5:9000/v1", api_key_encrypted="")
    _media(db, local, {"capability": "image", "model": "local-draw"})
    settings_store.update(db, {"enabled": True}, key=settings_store.IMAGES)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://172.16.0.5:9000/v1/images/generations"
        body = json.loads(request.content)
        assert body == {"model": "local-draw", "prompt": "a lamp", "n": 1, "size": "512x512"}
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    mock_http(handler)
    source = sources.chain(db, "image", local)[0]
    assert await pictures.generate(source, "a lamp", width=512, height=512) == PNG


async def test_a_provider_refusal_is_short_and_a_used_up_plan_says_so(db, user_id, mock_http):
    plan = _connection(db, "Plan", base_url=PLAN_URL, provider_type=providers.ALIBABA_PLAN)
    _media(db, plan, {"capability": "image", "model": "qwen-image-3.0-pro"})
    settings_store.update(db, {"enabled": True}, key=settings_store.IMAGES)
    source = sources.chain(db, "image", plan)[0]
    mock_http(lambda _r: httpx.Response(
        429, json={"code": "Throttling.AllocationQuota", "message": "insufficient_quota"}))
    with pytest.raises(LLMError) as quota:
        await pictures.generate(source, "a lamp")
    assert "quota" in quota.value.message.lower()
    mock_http(lambda _r: httpx.Response(
        400, json={"code": "InvalidParameter", "message": "x" * 5000, "request_id": "r"}))
    with pytest.raises(LLMError) as refusal:
        await pictures.generate(source, "a lamp")
    assert "InvalidParameter" in refusal.value.message and len(refusal.value.message) < 500


# --- Fallback, said, and counted against what answered ------------------------------
def _signed_in_with_cloud_speech(client, db, registered):
    _defaults(db)
    cloud = _connection(db)
    _media(db, cloud, {"capability": "stt", "model": "hear-one", "price": 3.0},
           {"capability": "tts", "model": "say-one", "price": 20.0, "voice": "ada"})
    cloud.currency = "EUR"
    db.commit()
    user = db.scalar(select(User))
    chat = Chat(user_id=user.id, model_id="cloud-chat", connection_id=cloud.id)
    db.add(chat)
    db.commit()
    return cloud, chat


def test_dictation_uses_the_chats_connection_and_says_who_answered(
    client, db, registered, mock_http
):
    cloud, chat = _signed_in_with_cloud_speech(client, db, registered)
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json={"text": "from " + request.url.host, "duration": 60})

    mock_http(handler)
    wav = {"file": ("a.webm", b"abc", "audio/webm")}
    own = client.post("/api/audio/transcribe", files=wav, data={"chat_id": chat.id})
    assert own.text == "from api.example.org"
    assert own.headers["x-lembas-source"].startswith("Cloud") and \
        "x-lembas-fallback" not in own.headers
    # A new chat: the model chosen in the composer decides.
    chosen = client.post("/api/audio/transcribe", files=wav, data={"model_id": "cloud-chat"})
    assert chosen.text == "from api.example.org"
    # No context at all: the instance's default, as it always was.
    plain = client.post("/api/audio/transcribe", files=wav)
    assert plain.text == "from stt.test"
    assert hosts == ["api.example.org", "api.example.org", "stt.test"]
    db.expire_all()
    rows = {row.model_id: row for row in db.scalars(select(Usage))}
    assert rows["cloud/hear-one"].requests == 2 and rows["cloud/hear-one"].currency == "EUR"
    assert rows["cloud/hear-one"].cost_micro == 6_000_000  # two minutes at 3.0
    assert rows["stt/whisper-local"].cost_micro == 600_000


def test_when_its_own_fails_the_default_answers_once_and_it_is_said(
    client, db, registered, mock_http
):
    cloud, chat = _signed_in_with_cloud_speech(client, db, registered)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.example.org":
            return httpx.Response(429, json={"error": {"message": "quota <b>gone</b>"}})
        return httpx.Response(200, json={"text": "from the default", "duration": 30})

    mock_http(handler)
    response = client.post(
        "/api/audio/transcribe", files={"file": ("a.webm", b"abc", "audio/webm")},
        data={"chat_id": chat.id},
    )
    assert response.status_code == 200 and response.text == "from the default"
    assert response.headers["x-lembas-fallback"] == "1"
    assert response.headers["x-lembas-source"] == "whisper-local"
    db.expire_all()
    assert [row.model_id for row in db.scalars(select(Usage))] == ["stt/whisper-local"]

    # And when the default fails too, that is the answer -- no third place.
    mock_http(lambda _r: httpx.Response(500, text="down"))
    assert client.post(
        "/api/audio/transcribe", files={"file": ("a.webm", b"abc", "audio/webm")},
        data={"chat_id": chat.id},
    ).status_code == 502


def test_read_aloud_follows_the_chat_and_the_page_can_ask_who_read(
    client, db, registered, mock_http
):
    from lembas.db.models import Message

    cloud, chat = _signed_in_with_cloud_speech(client, db, registered)
    message = Message(chat_id=chat.id, role="assistant", content="Hello there.")
    db.add(message)
    db.commit()
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.example.org":
            bodies.append(json.loads(request.content))
            return httpx.Response(503, text="busy")
        bodies.append(json.loads(request.content))
        return httpx.Response(200, content=b"AUDIO", headers={"content-type": "audio/mpeg"})

    mock_http(handler)
    url = f"/api/audio/speech/{chat.id}/{message.id}"
    response = client.get(url)
    assert response.content == b"AUDIO" and response.headers["x-lembas-fallback"] == "1"
    assert bodies[0]["model"] == "say-one" and bodies[0]["voice"] == "ada"
    assert bodies[1]["model"] == "kokoro"
    assert client.get(url + "/source").json() == {"source": "kokoro", "fallback": True}
    db.expire_all()
    rows = {row.model_id: row for row in db.scalars(select(Usage))}
    assert list(rows) == ["tts/kokoro"] and rows["tts/kokoro"].speech_chars == len("Hello there.")


def test_a_personal_key_gets_no_plan_media_and_a_device_does(client, db, registered, mock_http):
    _defaults(db)
    plan = _connection(db, "Plan", base_url=PLAN_URL, provider_type=providers.ALIBABA_PLAN)
    _media(db, plan, {"capability": "stt", "model": "qwen-audio-3.0-asr-flash"})
    user = db.scalar(select(User))
    _, personal = api_tokens.create(db, user, name="script", scopes=["models"])
    _, device = api_tokens.create(db, user, name="laptop", scopes=["models"], kind=TOKEN_DEVICE)
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "stt.test":
            return httpx.Response(200, json={"text": "default"})
        return httpx.Response(200, json={"output": {"text": "plan"}, "usage": {"duration": 2}})

    mock_http(handler)

    def ask(token: str, **fields) -> httpx.Response:
        return client.post(
            "/v1/audio/transcriptions", headers={"Authorization": f"Bearer {token}"},
            files={"file": ("a.wav", b"abc", "audio/wav")}, data=fields,
        )

    assert ask(device, chat_model="plan/plan-chat").json() == {"text": "plan"}
    assert ask(personal, chat_model="plan/plan-chat").json() == {"text": "default"}
    assert ask(device).json() == {"text": "default"}  # no chat named: the default
    assert hosts == ["token-plan.maas.qwencloudapi.com", "stt.test", "stt.test"]
    db.expire_all()
    rows = {row.model_id: row for row in db.scalars(select(Usage))}
    assert rows["plan/qwen-audio-3.0-asr-flash"].cost_micro == 0  # counted, never priced


def test_v1_models_lists_the_defaults_and_not_a_connections_own(client, db, registered):
    _defaults(db)
    cloud = _connection(db)
    _media(db, cloud, {"capability": "stt", "model": "hear-one"},
           {"capability": "tts", "model": "say-one"})
    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="t", scopes=["models"])
    listed = {
        m["id"]: m["kind"]
        for m in client.get("/v1/models", headers={"Authorization": f"Bearer {raw}"}).json()["data"]
    }
    assert listed["stt/whisper-local"] == "stt" and listed["tts/kokoro"] == "tts"
    assert not any("hear-one" in key or "say-one" in key for key in listed)
    # Made the default on purpose, it is the default's entry.
    settings_store.update(
        db, {"tts_connection": f"{cloud.id}/say-one"}, key=settings_store.AUDIO
    )
    listed = {
        m["id"]: m["kind"]
        for m in client.get("/v1/models", headers={"Authorization": f"Bearer {raw}"}).json()["data"]
    }
    assert listed.get("tts/say-one") == "tts" and "tts/kokoro" not in listed


# --- The image tool ---------------------------------------------------------------
def _chat_on(db, user_id, connection) -> Chat:
    chat = Chat(user_id=user_id, model_id=f"{connection.provider}-chat",
                connection_id=connection.id)
    db.add(chat)
    db.commit()
    return chat


def test_the_tool_is_offered_where_the_connection_draws_and_takes_its_parameters(db, user_id):
    settings_store.update(db, {"enabled": True}, key=settings_store.IMAGES)
    cloud = _connection(db)
    _media(db, cloud, {"capability": "image", "model": "draw-one"})
    local = _connection(db, "Local", base_url="http://172.16.0.5:8080/v1")
    user = db.get(User, user_id)
    offered = tools_service.resolve_tools(db, _chat_on(db, user_id, cloud), user)
    assert set(offered.by_name["image_generate"].parameters["properties"]) == {
        "prompt", "negative", "width", "height", "seed"
    }
    # No ComfyUI, no model of its own: not offered at all.
    assert "image_generate" not in tools_service.resolve_tools(
        db, _chat_on(db, user_id, local), user
    ).by_name


async def test_a_picture_from_the_connection_is_this_chats_file_and_counted(
    db, user_id, mock_http
):
    settings_store.update(db, {"enabled": True}, key=settings_store.IMAGES)
    cloud = _connection(db)
    _media(db, cloud, {"capability": "image", "model": "draw-one", "price": 0.04})
    chat = _chat_on(db, user_id, cloud)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"data": [
                {"url": "https://results.example.org/../../evil name.png?x=<script>"}]})
        return httpx.Response(200, content=PNG)

    mock_http(handler)
    context = tools_service.context_for(db, db.get(User, user_id), chat)
    outcome = await image_tool.run(context, {"prompt": "a lamp", "steps": 99})
    assert outcome.event["status"] == "ok" and outcome.event["source"] == "Cloud · draw-one"
    assert outcome.event["fallback"] is False
    db.expire_all()
    from lembas.db.models import Attachment

    attachment = db.get(Attachment, outcome.event["attachment_id"])
    # Stored here, named here: nothing of the provider's link is kept.
    assert attachment.chat_id == chat.id and attachment.filename.startswith("image-")
    assert "evil" not in attachment.filename and "example.org" not in outcome.content
    row = db.scalar(select(Usage).where(Usage.model_id == "cloud/draw-one"))
    assert row.images == 1 and row.cost_micro == 40_000


async def test_when_the_connection_does_not_draw_the_comfyui_does_and_says_so(
    db, user_id, mock_http, monkeypatch
):
    from lembas.services.images import comfy

    _comfy(db)
    cloud = _connection(db)
    _media(db, cloud, {"capability": "image", "model": "draw-one"})
    chat = _chat_on(db, user_id, cloud)
    mock_http(lambda _r: httpx.Response(401, json={"error": {"message": "bad key"}}))

    async def submit(_config, _workflow):
        return "job"

    async def await_images(_config, _job):
        return ["ref"]

    async def fetch_image(_config, _ref):
        return PNG

    monkeypatch.setattr(comfy, "submit", submit)
    monkeypatch.setattr(comfy, "await_images", await_images)
    monkeypatch.setattr(comfy, "fetch_image", fetch_image)
    context = tools_service.context_for(db, db.get(User, user_id), chat)
    outcome = await image_tool.run(context, {"prompt": "a lamp"})
    assert outcome.event["status"] == "ok" and outcome.event["fallback"] is True
    assert outcome.event["source"] == "ComfyUI"
    assert "Cloud · draw-one did not answer" in outcome.content
    assert "instance's default drew it instead" in outcome.event["text"]
    db.expire_all()
    assert [r.model_id for r in db.scalars(select(Usage))] == ["comfyui/sd.safetensors"]


async def test_with_nothing_behind_it_the_refusal_is_the_result(db, user_id, mock_http):
    settings_store.update(db, {"enabled": True}, key=settings_store.IMAGES)
    cloud = _connection(db)
    _media(db, cloud, {"capability": "image", "model": "draw-one"})
    chat = _chat_on(db, user_id, cloud)
    mock_http(lambda _r: httpx.Response(500, text="down"))
    context = tools_service.context_for(db, db.get(User, user_id), chat)
    outcome = await image_tool.run(context, {"prompt": "a lamp"})
    assert outcome.event["status"] == "error"
    assert db.scalar(select(Usage)) is None


def test_a_scheduled_run_does_not_draw_on_a_plan(db, user_id):
    from lembas.db.models import KIND_TASK

    settings_store.update(db, {"enabled": True}, key=settings_store.IMAGES)
    plan = _connection(db, "Plan", base_url=PLAN_URL, provider_type=providers.ALIBABA_PLAN)
    _media(db, plan, {"capability": "image", "model": "qwen-image-3.0-pro"})
    user = db.get(User, user_id)
    person = _chat_on(db, user_id, plan)
    assert "image_generate" in tools_service.resolve_tools(db, person, user).by_name
    run = Chat(user_id=user_id, model_id="plan-chat", connection_id=plan.id, kind=KIND_TASK)
    db.add(run)
    db.commit()
    assert "image_generate" not in tools_service.resolve_tools(db, run, user).by_name
    context = tools_service.context_for(db, user, run)
    assert image_tool._sources(context) == []


# --- The administrator's pages ----------------------------------------------------
def test_the_connection_form_keeps_media_and_shows_no_key(client, db, registered):
    cloud = _connection(db, base_url="https://api.openai.com/v1", provider_type=providers.OPENAI)
    page = client.get("/admin/connections").text
    assert 'data-testid="connection-media"' in page
    assert "whisper-1" in page and "connection-key" not in page
    form = {
        "name": "Cloud", "base_url": "https://api.openai.com/v1", "provider": "cloud",
        "provider_type": "openai", "api_key": "•" * 8, "enabled": "true", "currency": "USD",
        "media_form": "1", "media_prefer_audio": "true",
        "media_capability": ["stt", "tts", "image", "stt"],
        "media_model": ["whisper-1", "tts-1", "", "typed-by-hand"],
        "media_voice": ["", "alloy", "", ""],
        "media_price": ["0.006", "15", "", ""],
        "media_enabled": ["0", "3"],
    }
    from lembas.services.crypto import UNCHANGED_SENTINEL

    form["api_key"] = UNCHANGED_SENTINEL
    assert client.post(f"/admin/connections/{cloud.id}", data=form,
                       follow_redirects=False).status_code == 303
    db.expire_all()
    media = store.of(db.get(Connection, cloud.id))
    assert media["prefer_audio"] is True and media["prefer_image"] is False
    assert [(m["capability"], m["model"], m["enabled"]) for m in media["models"]] == [
        ("stt", "whisper-1", True), ("tts", "tts-1", False), ("stt", "typed-by-hand", True),
    ]
    assert media["models"][0]["price"] == 0.006 and media["models"][1]["voice"] == "alloy"
    # A form from before the media part leaves it alone.
    del form["media_form"]
    client.post(f"/admin/connections/{cloud.id}", data=form, follow_redirects=False)
    db.expire_all()
    assert len(store.of(db.get(Connection, cloud.id))["models"]) == 3


def test_the_settings_say_they_are_defaults_and_take_a_connections_model(client, db, registered):
    cloud = _connection(db)
    _media(db, cloud, {"capability": "tts", "model": "say-one"},
           {"capability": "image", "model": "draw-one"})
    audio = client.get("/admin/audio").text
    assert 'data-testid="audio-defaults-note"' in audio
    assert f'value="{cloud.id}/say-one"' in audio and "connection-key" not in audio
    client.post("/admin/audio", data={
        "tts_enabled": "true", "tts_connection": f"{cloud.id}/say-one",
        "stt_connection": f"{cloud.id}/say-one",  # not a speech-to-text model: refused
    })
    values = settings_store.audio(db)
    assert values["tts_connection"] == f"{cloud.id}/say-one" and values["stt_connection"] == ""
    images = client.get("/admin/images").text
    assert 'data-testid="image-defaults"' in images and f'value="{cloud.id}/draw-one"' in images
    client.post("/admin/images", data={"enabled": "true", "connection": f"{cloud.id}/draw-one"})
    assert settings_store.images(db)["connection"] == f"{cloud.id}/draw-one"
    assert sources.default(db, "image").model == "draw-one"


# --- The page ---------------------------------------------------------------------
def test_dictation_says_which_chat_and_a_fallback_is_a_note_not_an_error():
    """There is no JavaScript runner; what is pinned is what would fail in
    silence: a recording sent without its chat goes to the default with no
    sign of it, and a note raised as an error would alarm at a reply that is
    being read perfectly well."""
    from tests.conftest import js_code, js_function, js_says, script

    source = script("audio.js")
    upload = js_code(js_function(source, "upload"))
    assert js_says(upload, 'body.append("chat_id"', 'body.append("model_id"')
    assert js_says(upload, "X-Lembas-Fallback", "X-Lembas-Source")
    say = js_code(js_function(source, "saySource"))
    assert '"info"' in say and '"error"' not in say and "if (!fellBack" in say
    # Asked when the reply is being read, never from the play() a browser may refuse.
    assert "whoReads" not in js_function(source, "speak")
    assert js_says(js_code(source), 'addEventListener("playing"', "whoReads(speaking)")
