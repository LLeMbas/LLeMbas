"""Every AI service over the API: what `/v1/models` says each entry is,
which endpoint takes which kind, speech counted and charged, and pictures
through `POST /v1/images/generations`."""

from __future__ import annotations

import base64
import io
import json
import wave
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select, text

import lembas
from lembas.db.models import (
    Attachment,
    Chat,
    Connection,
    DataGroup,
    ImageWorkflow,
    Message,
    Model,
    Usage,
    User,
)
from lembas.db.session import get_engine
from lembas.security import api_tokens
from lembas.services import audio as audio_service
from lembas.services import settings_store
from lembas.services.crypto import encrypt
from lembas.services.images import comfy

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)
BASE = json.loads(
    (Path(lembas.__file__).parent / "services/images/base_workflow.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture(autouse=True)
def _rates():
    api_tokens.reset_rates()
    yield


def _me(db) -> User:
    return db.scalar(select(User).order_by(User.created_at))


def _plain(db) -> User:
    user = _me(db)
    user.role = "user"
    db.commit()
    settings_store.update(
        db, {"default_permissions": {"api.use": True, "tools.image": True}}
    )
    return user


def _bearer(db, user: User | None = None) -> dict[str, str]:
    _, raw = api_tokens.create(db, user or _me(db), name="t", scopes=["models"])
    return {"Authorization": f"Bearer {raw}"}


def _usage(db, model_id: str) -> Usage:
    db.expire_all()
    return db.scalar(select(Usage).where(Usage.model_id == model_id))


def _models(db) -> dict[str, Model]:
    connection = Connection(
        name="Local", provider="local", base_url="http://up.test/v1",
        api_key_encrypted=encrypt(""),
    )
    db.add(connection)
    db.commit()
    out = {
        "chat": Model(connection_id=connection.id, model_id="qwen", kind="chat"),
        "embed": Model(connection_id=connection.id, model_id="nomic", kind="embedding"),
        "old": Model(connection_id=connection.id, model_id="legacy"),
    }
    db.add_all(out.values())
    db.commit()
    # An older row: nobody has said what it is.
    with get_engine().begin() as raw:
        raw.execute(text("UPDATE models SET kind = NULL WHERE model_id = 'legacy'"))
    db.expire_all()
    return out


def _voice(db, **prices) -> None:
    settings_store.update(
        db,
        {"stt_enabled": True, "stt_base_url": "http://stt.test/v1", "stt_model": "whisper-1",
         "tts_enabled": True, "tts_base_url": "http://tts.test/v1", "tts_model": "kokoro",
         **prices},
        key=settings_store.AUDIO,
    )


def _drawing(db, **extra) -> None:
    db.add(ImageWorkflow(slug="base", name="Base", description="general", workflow_json=BASE))
    db.commit()
    settings_store.update(
        db,
        {"enabled": True, "base_url": "http://comfy.test:8188",
         "checkpoints": ["sd.safetensors", "xl.safetensors"], **extra},
        key=settings_store.IMAGES,
    )


def _wav(seconds: float, rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(1)
        out.setframerate(rate)
        out.writeframes(b"\x80" * int(seconds * rate))
    return buffer.getvalue()


# --- What each entry is ------------------------------------------------------------
def test_models_say_their_kind_and_the_api_behind_them(client, db, registered):
    _models(db)
    data = {e["id"]: e for e in client.get("/v1/models", headers=_bearer(db)).json()["data"]}
    assert data["local/qwen"]["kind"] == "chat"
    assert data["local/nomic"]["kind"] == "embedding"
    assert data["local/legacy"]["kind"] == "chat"  # unset reads as chat
    assert {e["api"] for e in data.values()} == {"openai"}


def test_the_services_are_listed_where_they_are_on_and_allowed(client, db, registered):
    _models(db)
    _voice(db)
    _drawing(db)
    data = {e["id"]: e for e in client.get("/v1/models", headers=_bearer(db)).json()["data"]}
    assert data["stt/whisper-1"]["kind"] == "stt"
    assert data["tts/kokoro"]["kind"] == "tts"
    assert data["comfyui/sd.safetensors"]["kind"] == "image"
    assert data["comfyui/xl.safetensors"]["provider"] == "comfyui"

    # Without the permissions, none of them.
    user = _plain(db)
    settings_store.update(
        db,
        {"default_permissions": {"api.use": True, "audio.transcribe": False,
                                 "audio.listen": False, "tools.image": False}},
    )
    kinds = {e["kind"] for e in client.get("/v1/models", headers=_bearer(db, user)).json()["data"]}
    assert kinds <= {"chat", "embedding"}


def test_a_service_never_shadows_a_model_s_own_id(client, db, registered):
    connection = Connection(name="stt", provider="stt", base_url="http://x.test/v1",
                            api_key_encrypted=encrypt(""))
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="whisper-1"))
    db.commit()
    _voice(db)
    data = client.get("/v1/models", headers=_bearer(db)).json()["data"]
    assert [e["kind"] for e in data if e["id"] == "stt/whisper-1"] == ["chat"]


def test_chat_completions_refuse_an_embedding_model(client, db, registered):
    _models(db)
    response = client.post(
        "/v1/chat/completions", headers=_bearer(db),
        json={"model": "local/nomic", "messages": [{"role": "user", "content": "x"}]},
    )
    assert response.status_code == 400
    assert "/v1/embeddings" in response.json()["error"]["message"]


def test_embeddings_take_embedding_and_unkinded_models_only(client, db, registered, mock_http):
    _models(db)
    mock_http(lambda _r: httpx.Response(
        200, json={"data": [{"embedding": [0.1]}], "usage": {"prompt_tokens": 3}}
    ))
    headers = _bearer(db)
    for model, status in (("local/nomic", 200), ("local/legacy", 200), ("local/qwen", 400)):
        response = client.post(
            "/v1/embeddings", headers=headers, json={"model": model, "input": "x"}
        )
        assert response.status_code == status, (model, response.text)
    assert "chat model" in response.json()["error"]["message"]


def test_the_model_page_sets_the_kind_and_discovery_guesses_it(client, db, registered):
    from lembas.db.models import guess_kind

    models = _models(db)
    client.post(f"/admin/models/{models['old'].id}", data={"kind": "embedding", "enabled": "true"})
    db.expire_all()
    assert db.get(Model, models["old"].id).kind == "embedding"
    # Junk is ignored, not stored.
    client.post(f"/admin/models/{models['old'].id}", data={"kind": "tts", "enabled": "true"})
    db.expire_all()
    assert db.get(Model, models["old"].id).kind == "embedding"
    assert guess_kind("text-embedding-3-small") == "embedding"
    assert guess_kind("nomic-embed-text") == "embedding"
    assert guess_kind("qwen3-coder") == "chat"


def test_the_migration_marks_what_the_instance_embeds_with(db, registered):
    from lembas.db.migrations import infer_model_kinds

    plain = Connection(name="Local", provider="local", base_url="http://a.test/v1",
                       api_key_encrypted="")
    embedder = Connection(name="Embedding box", provider="vectors", base_url="http://b.test/v1",
                          api_key_encrypted="")
    db.add_all([plain, embedder])
    db.commit()
    rows = {
        name: Model(connection_id=connection.id, model_id=name)
        for name, connection in (
            ("bge-m3", plain), ("grouped", plain), ("on-the-box", embedder),
            ("e5-embed", plain), ("qwen", plain), ("chosen-chat", plain),
        )
    }
    db.add_all(rows.values())
    db.commit()
    db.add(DataGroup(id="g", name="G", embedding_model_id="grouped",
                     embedding_connection_id=plain.id))
    db.commit()
    settings_store.update(db, {"embedding_model_id": "bge-m3"}, key=settings_store.EXTRACTION)
    with get_engine().begin() as raw:
        raw.execute(text("UPDATE models SET kind = NULL WHERE model_id != 'chosen-chat'"))
        raw.execute(text("UPDATE models SET kind = 'chat' WHERE model_id = 'chosen-chat'"))

    assert infer_model_kinds(get_engine())
    db.expire_all()
    kinds = {m.model_id: m.kind for m in db.scalars(select(Model))}
    assert kinds == {
        "bge-m3": "embedding", "grouped": "embedding", "on-the-box": "embedding",
        "e5-embed": "embedding", "qwen": None, "chosen-chat": "chat",
    }
    assert infer_model_kinds(get_engine()) == []


# --- Speech, counted and charged -----------------------------------------------------
def test_a_wav_is_measured_by_its_header_and_anything_else_by_its_size():
    assert audio_service.measure_seconds(_wav(6)) == pytest.approx(6)
    assert audio_service.measure_seconds(b"x" * 16_000) == pytest.approx(2)
    assert audio_service.measure_seconds(b"x" * 16_000, reported=42.5) == 42.5
    assert audio_service.measure_seconds(b"RIFF....WAVE") > 0


def test_a_transcription_is_counted_and_priced_by_the_minute(client, db, registered, mock_http):
    _voice(db, stt_per_minute=0.06)
    user = _plain(db)
    settings_store.update(db, {"default_permissions": {"api.use": True,
                                                       "audio.transcribe": True}})
    mock_http(lambda _r: httpx.Response(200, json={"text": "hello"}))
    response = client.post(
        "/v1/audio/transcriptions", headers=_bearer(db, user),
        files={"file": ("a.wav", _wav(30), "audio/wav")},
    )
    assert response.status_code == 200, response.text
    row = _usage(db, "stt/whisper-1")
    assert row.audio_seconds == 30 and row.requests == 1
    assert row.cost_micro == 30_000  # half a minute at 0.06


def test_the_server_s_own_duration_wins(client, db, registered, mock_http):
    _voice(db, stt_per_minute=0.01)
    mock_http(lambda _r: httpx.Response(200, json={"text": "hi", "duration": 120.0}))
    client.post(
        "/v1/audio/transcriptions", headers=_bearer(db),
        files={"file": ("a.webm", b"x" * 100, "audio/webm")},
    )
    row = _usage(db, "stt/whisper-1")
    assert row.audio_seconds == 120 and row.cost_micro == 20_000


def test_speech_is_priced_by_the_character_and_never_refused(client, db, registered, mock_http):
    _voice(db, tts_per_1m_chars=10.0)
    user = _plain(db)
    settings_store.update(db, {"default_permissions": {"api.use": True, "audio.listen": True}})
    mock_http(
        lambda _r: httpx.Response(200, content=b"RIFF", headers={"content-type": "audio/wav"})
    )
    spoken = client.post("/v1/audio/speech", headers=_bearer(db, user), json={"input": "Mellon."})
    assert spoken.status_code == 200
    row = _usage(db, "tts/kokoro")
    assert row.speech_chars == 7 and row.cost_micro == 70  # 7 characters at 10 a million


def test_free_speech_is_counted_and_not_priced(client, db, registered, mock_http):
    _voice(db)
    mock_http(lambda _r: httpx.Response(200, json={"text": "hi"}))
    client.post(
        "/v1/audio/transcriptions", headers=_bearer(db),
        files={"file": ("a.wav", _wav(3), "audio/wav")},
    )
    row = db.scalar(select(Usage))
    assert row.audio_seconds == 3 and row.cost_micro == 0


def test_the_web_s_own_dictation_and_read_aloud_are_counted_the_same_way(
    client, db, registered, mock_http
):
    _voice(db, stt_per_minute=1.0, tts_per_1m_chars=1.0)
    user = _plain(db)
    settings_store.update(
        db, {"default_permissions": {"audio.transcribe": True, "audio.listen": True}}
    )
    chat = Chat(user_id=user.id, model_id="")
    db.add(chat)
    db.commit()
    reply = Message(chat_id=chat.id, role="assistant", content="Speak, friend.")
    db.add(reply)
    db.commit()
    mock_http(lambda r: httpx.Response(200, json={"text": "hi"})
              if r.url.path.endswith("transcriptions")
              else httpx.Response(200, content=b"RIFF", headers={"content-type": "audio/wav"}))
    assert client.post(
        "/api/audio/transcribe", files={"file": ("a.wav", _wav(60), "audio/wav")}
    ).status_code == 200
    assert client.get(f"/api/audio/speech/{chat.id}/{reply.id}").status_code == 200
    stt = _usage(db, "stt/whisper-1")
    assert stt.audio_seconds == 60 and stt.cost_micro == 1_000_000
    assert _usage(db, "tts/kokoro").speech_chars == 14


# --- Pictures ---------------------------------------------------------------------
class FakeComfy:
    def __init__(self):
        self.submits: list[dict] = []

    async def submit(self, config, wf):
        self.submits.append(wf)
        return f"p{len(self.submits)}"

    async def await_images(self, config, prompt_id):
        return [comfy.Ref(f"{prompt_id}.png")]

    async def fetch_image(self, config, ref):
        return PNG

    async def free(self, config):
        return None


@pytest.fixture
def fake(monkeypatch):
    stub = FakeComfy()
    for name in ("submit", "await_images", "fetch_image", "free"):
        monkeypatch.setattr(comfy, name, getattr(stub, name))
    return stub


def _sizes(workflow: dict) -> tuple[int, int]:
    for node in workflow.values():
        inputs = node.get("inputs") or {}
        if "width" in inputs and "height" in inputs:
            return inputs["width"], inputs["height"]
    raise AssertionError("no size in the workflow")


def test_a_picture_comes_back_as_a_url_the_same_token_can_fetch(client, db, registered, fake):
    _drawing(db)
    headers = _bearer(db)
    response = client.post(
        "/v1/images/generations", headers=headers,
        json={"prompt": "a mallorn tree", "size": "1000x700"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    url = body["data"][0]["url"]
    assert url.startswith("http://testserver/v1/files/") and url.endswith("/content")
    assert body["data"][0]["revised_prompt"] == "a mallorn tree"
    # The nearest size a workflow takes: multiples of 64.
    assert _sizes(fake.submits[0]) == (1024, 704)
    fetched = client.get(url.replace("http://testserver", ""), headers=headers)
    assert fetched.status_code == 200 and fetched.content.startswith(b"\x89PNG")
    assert client.get(url.replace("http://testserver", "")).status_code == 401
    # Stored as the caller's own file, in no chat: it counts against the
    # daily budget and the orphan sweep takes it in a day.
    stored = db.scalar(select(Attachment))
    assert stored.chat_id is None and stored.kind == "image"
    assert db.scalar(select(Usage)).images == 1


def test_somebody_else_s_picture_is_a_404(client, db, registered, fake):
    _drawing(db)
    url = client.post(
        "/v1/images/generations", headers=_bearer(db), json={"prompt": "x"}
    ).json()["data"][0]["url"]
    other = User(email="sam@shire.test", name="Sam", password_hash="x", role="admin")
    db.add(other)
    db.commit()
    assert client.get(
        url.replace("http://testserver", ""), headers=_bearer(db, other)
    ).status_code == 404


def test_b64_n_and_the_checkpoint(client, db, registered, fake):
    _drawing(db, api_max_images=3)
    headers = _bearer(db)
    response = client.post(
        "/v1/images/generations", headers=headers,
        json={"prompt": "x", "n": 2, "response_format": "b64_json",
              "model": "comfyui/xl.safetensors"},
    ).json()
    assert [base64.b64decode(item["b64_json"])[:4] for item in response["data"]] == [
        b"\x89PNG", b"\x89PNG"
    ]
    assert len(fake.submits) == 2
    assert "xl.safetensors" in json.dumps(fake.submits[0])
    too_many = client.post("/v1/images/generations", headers=headers, json={"prompt": "x", "n": 4})
    assert too_many.status_code == 400 and "1 to 3" in too_many.text
    unknown = client.post(
        "/v1/images/generations", headers=headers,
        json={"prompt": "x", "model": "comfyui/nope"},
    )
    assert unknown.status_code == 404
    # A model name from a client written for OpenAI is the default checkpoint.
    assert client.post(
        "/v1/images/generations", headers=headers, json={"prompt": "x", "model": "dall-e-3"}
    ).status_code == 200
    assert client.post(
        "/v1/images/generations", headers=headers,
        json={"prompt": "x", "response_format": "png"},
    ).status_code == 400


def test_pictures_need_the_permission_and_a_ready_instance(client, db, registered, fake):
    headers = _bearer(db)
    assert client.post(
        "/v1/images/generations", headers=headers, json={"prompt": "x"}
    ).status_code == 404
    _drawing(db)
    user = _plain(db)
    settings_store.update(db, {"default_permissions": {"api.use": True, "tools.image": False}})
    assert client.post(
        "/v1/images/generations", headers=_bearer(db, user), json={"prompt": "x"}
    ).status_code == 404
    assert fake.submits == []


def test_pictures_are_priced_and_gated_only_by_the_daily_limit(client, db, registered, fake):
    from lembas.db.models import Group

    _drawing(db, per_image=0.05)
    user = _plain(db)
    headers = _bearer(db, user)
    made = client.post("/v1/images/generations", headers=headers, json={"prompt": "x", "n": 2})
    assert made.status_code == 200
    row = _usage(db, "comfyui/sd.safetensors")
    assert row.images == 2 and row.cost_micro == 100_000

    group = Group(name="Two a day", limits_json={"images_per_day": 3})
    db.add(group)
    db.get(User, user.id).groups.append(group)
    db.commit()
    over = client.post("/v1/images/generations", headers=headers, json={"prompt": "x", "n": 2})
    assert over.status_code == 429 and "for today" in over.text


async def test_the_chat_tool_prices_its_picture(db, registered, fake):
    from lembas.services import tools as tools_service
    from lembas.services.images import tool as image_tool

    _drawing(db, per_image=0.02)
    user = _plain(db)
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted="")
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m",
                 capabilities_json={"tools": True, "tool_image": True}))
    chat = Chat(user_id=user.id, model_id="m", connection_id=connection.id)
    db.add(chat)
    db.commit()
    context = tools_service.context_for(db, user, chat)
    outcome = await image_tool.run(context, {"prompt": "a bicycle"})
    assert outcome.event["status"] == "ok"
    row = _usage(db, "comfyui/sd.safetensors")
    assert row.images == 1 and row.cost_micro == 20_000


# --- Where the prices are set ---------------------------------------------------------
def test_the_admin_pages_save_the_service_prices(client, db, registered):
    client.post(
        "/admin/audio",
        data={"stt_per_minute": "0.006", "tts_per_1m_chars": "-4", "tts_speed": "1"},
    )
    audio = settings_store.audio(db)
    assert audio["stt_per_minute"] == 0.006 and audio["tts_per_1m_chars"] == 0.0
    page = client.get("/admin/audio").text
    assert 'name="stt_per_minute"' in page and "($)" in page

    client.post("/admin/images", data={"per_image": "0.04", "api_max_images": "99"})
    images = settings_store.images(db)
    assert images["per_image"] == 0.04 and images["api_max_images"] == 10
    assert 'name="per_image"' in client.get("/admin/images").text
