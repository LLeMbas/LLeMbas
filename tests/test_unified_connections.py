"""Every endpoint is a connection: the ComfyUI and the speech servers too.

Three things are held here.

**The upgrade.** An instance from before the change keeps its ComfyUI and its
speech servers as addresses in the settings. `instance.carry` makes
connections of them, once. The tests start from a database *shaped like that
release* -- the newer columns dropped, the settings filled, connections with
ticked media models, workflows, a chat with its own workflow and checkpoint --
and assert that every source answers exactly as it did: the same address, key,
model, price, name on the usage page and in the header. And from an empty
database, where there is nothing to carry.

**The resolution.** `services/media/sources.py` is still the one place that
picks a source; the instance's own equipment answers as the instance did.

**The pages.** One list of connections, one list of models with a capability,
one page per item with link tabs, and Media for what is nobody's model.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select, text

from lembas.db.models import Connection, ImageWorkflow, Model, Usage, User
from lembas.db.session import get_engine, init_db
from lembas.services import settings_store
from lembas.services import tools as tools_service
from lembas.services.crypto import decrypt, encrypt
from lembas.services.images import tool as image_tool
from lembas.services.llm import providers
from lembas.services.media import catalog, instance, sources, store
from tests.test_media_connections import PNG, _chat_on, _comfy, _connection, _media, image_tool_base

STT_URL = "http://workstation.example.org:9000/v1"
TTS_URL = "http://workstation.example.org:8880/v1"
COMFY_URL = "http://workstation.example.org:8188"
CLOUD_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
PLAN_URL = "https://token-plan.maas.qwencloudapi.com/compatible-mode/v1"
# Columns that did not exist in the release these tests upgrade from.
NEWER_COLUMNS = (("connections", "options_json"), ("image_workflows", "connection_id"))


def _older_release(db) -> dict:
    """An instance as the release before left it: one ComfyUI address, two
    speech addresses, two provider connections with media models ticked and
    their switches on, a workflow, a chat that chose its own. Returns the ids."""
    settings_store.update(
        db,
        {
            "stt_enabled": True, "stt_base_url": STT_URL,
            "stt_api_key_encrypted": encrypt("stt-secret"), "stt_model": "whisper-large",
            "stt_language": "sk", "stt_per_minute": 0.01,
            "tts_enabled": True, "tts_base_url": TTS_URL,
            "tts_api_key_encrypted": encrypt("tts-secret"), "tts_model": "kokoro",
            "tts_voice": "af_heart", "tts_format": "wav", "tts_speed": 1.2,
            "tts_per_1m_chars": 3.0,
        },
        key=settings_store.AUDIO,
    )
    settings_store.update(
        db,
        {
            "enabled": True, "base_url": COMFY_URL,
            "api_key_encrypted": encrypt("comfy-secret"), "timeout": 300.0,
            "checkpoints": ["sdxl.safetensors", "flux.safetensors"],
            "samplers": ["euler", "dpmpp_2m"], "schedulers": ["normal", "karras"],
            "default_checkpoint": "flux.safetensors", "default_steps": 28,
            "preserve_vram": True, "per_image": 0.02, "review_enabled": False,
        },
        key=settings_store.IMAGES,
    )
    workflow = ImageWorkflow(slug="base", name="Base", description="general",
                             workflow_json=image_tool_base())
    db.add(workflow)
    db.commit()
    settings_store.update(db, {"default_workflow_id": workflow.id}, key=settings_store.IMAGES)
    cloud = _connection(db, "Cloud", base_url=CLOUD_URL, provider_type=providers.ALIBABA)
    plan = _connection(db, "Plan", base_url=PLAN_URL, provider_type=providers.ALIBABA_PLAN)
    _media(db, cloud, {"capability": "stt", "model": "qwen3-asr-flash"},
           {"capability": "image", "model": "qwen-image-3.0", "price": 0.03})
    _media(db, plan, {"capability": "tts", "model": "qwen-audio-3.0-tts-plus", "voice": "Cherry"})
    return {"cloud": cloud.id, "plan": plan.id, "workflow": workflow.id}


def _drop_newer_columns(db) -> None:
    db.commit()
    db.close()
    with get_engine().begin() as connection:
        for table, column in NEWER_COLUMNS:
            connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))


def _snapshot(db) -> dict:
    """What every default source is, in the fields a request can tell."""
    out = {}
    for capability in store.CAPABILITIES:
        source = sources.default(db, capability)
        assert source is not None, capability
        out[capability] = {
            "origin": source.origin, "label": source.label, "model": source.model,
            "dialect": source.dialect, "usage_id": source.usage_id, "price": source.price,
            "base_url": source.endpoint.base_url if source.endpoint else "",
            "key": source.endpoint.api_key if source.endpoint else "",
        }
    images = settings_store.images(db)
    out["comfy"] = {name: images[name] for name in (
        "base_url", "timeout", "checkpoints", "samplers", "schedulers", "default_checkpoint",
        "default_workflow_id", "preserve_vram", "default_steps", "per_image",
    )}
    out["comfy"]["key"] = decrypt(images["api_key_encrypted"])
    audio = settings_store.audio(db)
    out["audio"] = {name: audio[name] for name in (
        "stt_model", "stt_language", "tts_model", "tts_voice", "tts_format", "tts_speed",
    )}
    return out


# --- The upgrade ---------------------------------------------------------------------
def test_an_older_instance_is_carried_and_answers_as_it_did(db):
    ids = _older_release(db)
    before = _snapshot(db)
    raw_before = {
        group: dict(settings_store.get_group(db, group))
        for group in (settings_store.AUDIO, settings_store.IMAGES)
    }
    media_before = {
        key: dict(db.get(Connection, ids[key]).media_json) for key in ("cloud", "plan")
    }
    _drop_newer_columns(db)

    init_db()

    db.expire_all()
    made = {c.name: c for c in db.scalars(select(Connection)) if not providers.serves_chat(
        c.provider_type)}
    assert sorted(made) == ["ComfyUI", "Speech to text", "Text to speech"]
    comfy, stt, tts = made["ComfyUI"], made["Speech to text"], made["Text to speech"]
    assert (comfy.provider_type, comfy.base_url) == (providers.COMFYUI, COMFY_URL)
    assert (stt.provider_type, stt.base_url) == (providers.SPEECH, STT_URL)
    assert (tts.provider_type, tts.base_url) == (providers.SPEECH, TTS_URL)
    # The keys moved as they were: still encrypted at rest, and the same keys.
    for row, secret in ((comfy, "comfy-secret"), (stt, "stt-secret"), (tts, "tts-secret")):
        assert secret not in row.api_key_encrypted and decrypt(row.api_key_encrypted) == secret
    # A ComfyUI's models are its checkpoints; its options are its own.
    assert [e.model for e in store.entries(comfy, "image", enabled=True)] == [
        "sdxl.safetensors", "flux.safetensors"
    ]
    assert store.type_options(comfy) == {
        "timeout": 300.0, "preserve_vram": True, "default_workflow_id": ids["workflow"],
        "samplers": ["euler", "dpmpp_2m"], "schedulers": ["normal", "karras"],
    }
    assert db.get(ImageWorkflow, ids["workflow"]).connection_id == comfy.id
    spoken = store.get_entry(tts, "tts", "kokoro")
    assert (spoken.voice, spoken.options) == ("af_heart", {"speed": 1.2, "format": "wav"})
    assert store.get_entry(stt, "stt", "whisper-large").options == {"language": "sk"}

    # The defaults point at the same things, and answer as they did.
    assert sources.chosen(db, "stt") == f"{stt.id}/whisper-large"
    assert sources.chosen(db, "tts") == f"{tts.id}/kokoro"
    assert sources.chosen(db, "image") == f"{comfy.id}/flux.safetensors"
    assert _snapshot(db) == before

    # Nothing was retyped or taken away: what the older release wrote is
    # still where it wrote it, and the providers' ticked models are untouched.
    for group, values in raw_before.items():
        now = settings_store.get_group(db, group)
        new = ("connection", "comfyui", "carried")  # the defaults, and the marks
        assert {k: now[k] for k in values if not k.endswith(new)} == {
            k: v for k, v in values.items() if not k.endswith(new)
        }
    # ...and an older release reading the image default still reads "the ComfyUI".
    assert settings_store.get_group(db, settings_store.IMAGES)["connection"] == ""
    for key in ("cloud", "plan"):
        assert db.get(Connection, ids[key]).media_json == media_before[key]


def test_a_second_start_changes_nothing(db):
    _older_release(db)
    _drop_newer_columns(db)
    init_db()
    db.expire_all()
    rows = {c.id: (c.name, c.base_url, c.media_json, c.options_json)
            for c in db.scalars(select(Connection))}
    groups = {g: settings_store.get_group(db, g)
              for g in (settings_store.AUDIO, settings_store.IMAGES)}
    assert instance.carry(db) == []
    init_db()
    db.expire_all()
    assert {c.id: (c.name, c.base_url, c.media_json, c.options_json)
            for c in db.scalars(select(Connection))} == rows
    assert {g: settings_store.get_group(db, g) for g in groups} == groups


def test_what_an_administrator_changed_afterwards_is_not_carried_again(db):
    """The mark is the address that was carried: a connection renamed, moved
    or removed since stays as it was left."""
    _older_release(db)
    instance.carry(db)
    comfy = db.scalar(select(Connection).where(Connection.provider_type == providers.COMFYUI))
    comfy.name, comfy.base_url = "The studio", "http://studio.example.org:8188"
    speech = db.scalar(select(Connection).where(Connection.base_url == TTS_URL))
    db.delete(speech)
    db.commit()
    assert instance.carry(db) == []
    db.expire_all()
    assert db.scalar(select(Connection).where(Connection.base_url == TTS_URL)) is None
    assert settings_store.images(db)["base_url"] == "http://studio.example.org:8188"
    # The default it was is shown as gone; the old address is not used instead.
    assert sources.default(db, "tts") is None and sources.stale(db, "tts")


def test_one_server_for_both_directions_is_one_connection(db):
    settings_store.update(
        db,
        {"stt_enabled": True, "stt_base_url": STT_URL, "tts_enabled": True,
         "tts_base_url": STT_URL, "tts_model": "kokoro"},
        key=settings_store.AUDIO,
    )
    instance.carry(db)
    rows = list(db.scalars(select(Connection)))
    assert [(r.name, r.provider_type) for r in rows] == [("Speech", providers.SPEECH)]
    assert {(e.capability, e.model) for e in store.entries(rows[0])} == {
        ("stt", "whisper-1"), ("tts", "kokoro")
    }


def test_a_default_already_chosen_on_a_provider_is_left_where_it_is(db):
    ids = _older_release(db)
    choice = f"{ids['cloud']}/qwen-image-3.0"
    settings_store.update(db, {"connection": choice}, key=settings_store.IMAGES)
    settings_store.update(
        db, {"stt_connection": f"{ids['cloud']}/qwen3-asr-flash"}, key=settings_store.AUDIO
    )
    instance.carry(db)
    assert sources.chosen(db, "image") == choice
    assert sources.default(db, "image").label == "Cloud · qwen-image-3.0"
    assert sources.default(db, "stt").label == "Cloud · qwen3-asr-flash"
    # The equipment is a connection all the same, ready to be chosen.
    assert db.scalar(select(Connection).where(Connection.provider_type == providers.COMFYUI))


def test_an_empty_database_has_nothing_to_carry(db):
    init_db()
    assert instance.carry(db) == []
    assert list(db.scalars(select(Connection))) == []
    for capability in store.CAPABILITIES:
        assert sources.default(db, capability) is None and not sources.chosen(db, capability)


def test_the_carried_keys_are_in_the_backup_s_secret_columns():
    """They are a connection's key now, which a backup re-encrypts on a
    restore under another secret key."""
    from lembas.services import backup

    assert ("connections", "api_key_encrypted") in backup.SECRET_COLUMNS


# --- Resolution, on what was carried -----------------------------------------------------
def test_a_connection_s_own_still_comes_first_then_the_default(db, user_id):
    ids = _older_release(db)
    instance.carry(db)
    cloud = db.get(Connection, ids["cloud"])
    chain = sources.chain(db, "stt", cloud)
    assert [(s.own, s.label) for s in chain] == [
        (True, "Cloud · qwen3-asr-flash"), (False, "whisper-large")
    ]
    assert [(s.own, s.dialect) for s in sources.chain(db, "image", cloud)] == [
        (True, store.DASHSCOPE_IMAGE), (False, sources.COMFYUI)
    ]
    # A plan's own model is a person's own turn only; the instance's own
    # speech server is nobody's plan and stays.
    plan = db.get(Connection, ids["plan"])
    assert [s.label for s in sources.chain(db, "tts", plan, interactive=False)] == ["kokoro"]
    assert len(sources.chain(db, "tts", plan, interactive=True)) == 2


def test_equipment_is_never_a_chat_s_own_source(db):
    _older_release(db)
    instance.carry(db)
    speech = db.scalar(select(Connection).where(Connection.base_url == STT_URL))
    speech.media_json = {**speech.media_json, "prefer_audio": True}
    db.commit()
    assert [s.own for s in sources.chain(db, "stt", speech)] == [False]


async def test_dictation_goes_to_the_speech_connection_with_its_key(db, client, registered,
                                                                  mock_http):
    _older_release(db)
    instance.carry(db)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "speak friend", "duration": 6})

    mock_http(handler)
    response = client.post(
        "/api/audio/transcribe", files={"file": ("a.webm", b"audio-bytes", "audio/webm")}
    )
    assert response.status_code == 200 and response.text == "speak friend"
    assert seen["url"] == f"{STT_URL}/audio/transcriptions"
    assert seen["auth"] == "Bearer stt-secret"
    assert b"whisper-large" in seen["body"] and b"sk" in seen["body"]
    assert response.headers["x-lembas-source"] == "whisper-large"
    assert "x-lembas-fallback" not in response.headers
    db.expire_all()
    usage = db.scalar(select(Usage))
    assert usage.model_id == "stt/whisper-large" and usage.cost_micro == 1_000


async def test_a_person_still_chooses_among_the_speech_server_s_voices(db, mock_http):
    """The voice list of the settings page: asked of the speech connection,
    or the list typed on the default model's page -- and none for a
    provider's model, whose voice is set beside it."""
    from lembas.api.audio import available_voices
    from lembas.services import audio as audio_service

    ids = _older_release(db)
    instance.carry(db)
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json={"voices": ["af_heart", "bm_george"]})

    mock_http(handler)
    audio_service.forget_voices()
    assert await available_voices(settings_store.audio(db)) == (["af_heart", "bm_george"], "")
    assert asked and asked[0].startswith(TTS_URL)
    server = db.scalar(select(Connection).where(Connection.base_url == TTS_URL))
    entry = store.get_entry(server, "tts", "kokoro")
    server.media_json = store.with_entry(server, store._entry(
        {**entry.as_dict(), "options": {**entry.options, "voices": ["one", "two"]}}
    ))
    db.commit()
    assert await available_voices(settings_store.audio(db)) == (["one", "two"], "")
    settings_store.update(
        db, {"tts_connection": f"{ids['plan']}/qwen-audio-3.0-tts-plus"},
        key=settings_store.AUDIO,
    )
    assert await available_voices(settings_store.audio(db)) == ([], "")


async def test_the_image_tool_draws_on_the_comfyui_connection(db, user_id, monkeypatch):
    from lembas.services.images import comfy

    _comfy(db)
    instance.carry(db)
    row = db.scalar(select(Connection).where(Connection.provider_type == providers.COMFYUI))
    # The checkpoint's own options, and its own price.
    entry = store.get_entry(row, "image", "sd.safetensors")
    row.media_json = store.with_entry(row, store._entry(
        {**entry.as_dict(), "price": 0.05, "options": {"steps": 9, "sizes": "768x512"}}
    ))
    db.commit()
    local = _connection(db, "Local", base_url="http://127.0.0.1:8081/v1")
    chat = _chat_on(db, user_id, local)
    sent = {}

    async def submit(config, workflow):
        sent["config"], sent["workflow"] = config, workflow
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
    assert outcome.event["status"] == "ok" and outcome.event["source"] == "ComfyUI"
    assert sent["config"].base_url == "http://comfy.test:8188"
    flat = str(sent["workflow"])
    assert "sd.safetensors" in flat and "'steps': 9" in flat
    assert "'width': 768" in flat and "'height': 512" in flat
    db.expire_all()
    usage = db.scalar(select(Usage))
    assert usage.model_id == "comfyui/sd.safetensors" and usage.cost_micro == 50_000


def test_a_chat_s_own_workflow_and_checkpoint_still_hold(db, user_id):
    ids = _older_release(db)
    instance.carry(db)
    local = _connection(db, "Local", base_url="http://127.0.0.1:8081/v1")
    chat = _chat_on(db, user_id, local)
    chat.image_workflow_id, chat.image_checkpoint = ids["workflow"], "sdxl.safetensors"
    db.commit()
    values = settings_store.images(db)
    rows, checkpoints = image_tool._choices(db, values)
    assert image_tool._pick(rows, "", chat.image_workflow_id, values).id == ids["workflow"]
    assert image_tool._checkpoint(
        "", chat.image_checkpoint, checkpoints, instance_default=values["default_checkpoint"]
    ) == "sdxl.safetensors"
    # With no choice of the chat's: the default it always was.
    assert image_tool._checkpoint(
        "", "", checkpoints, instance_default=values["default_checkpoint"]
    ) == "flux.safetensors"


def test_a_workflow_is_offered_only_where_its_comfyui_draws(db):
    _comfy(db)
    instance.carry(db)
    first = db.scalar(select(Connection).where(Connection.provider_type == providers.COMFYUI))
    other = Connection(name="Second", base_url="http://second.example.org:8188",
                       provider_type=providers.COMFYUI)
    db.add(other)
    db.commit()
    db.add(ImageWorkflow(slug="theirs", name="Theirs", workflow_json={}, connection_id=other.id))
    db.add(ImageWorkflow(slug="shared", name="Shared", workflow_json={}))
    db.commit()
    assert db.get(Connection, first.id) is not None
    slugs = [w.slug for w in instance.workflows(db, settings_store.images(db))]
    assert slugs == ["base", "shared"]


def test_none_is_none_whatever_an_older_address_says(db):
    _older_release(db)
    settings_store.update(db, {"stt_connection": instance.NONE}, key=settings_store.AUDIO)
    assert sources.default(db, "stt") is None and not sources.stale(db, "stt")


def test_a_model_of_a_capability_nothing_serves_yet_is_kept(db):
    """The capability is a value: one added tomorrow needs no schema change."""
    row = _connection(db)
    row.media_json = store.normalise({"models": [
        {"capability": "video", "model": "wan3.0-video", "enabled": True},
        {"capability": "hologram", "model": "nope", "enabled": True},
    ]})
    db.commit()
    assert [(e.capability, e.model) for e in store.entries(row)] == [("video", "wan3.0-video")]
    listed = catalog.items(db, capability="video")
    assert [(i.label, i.connection.name) for i in listed] == [("wan3.0-video", "Cloud")]
    assert "video" not in store.CAPABILITIES


# --- The one list ------------------------------------------------------------------------
def test_one_list_holds_every_model_with_its_capability(db):
    _older_release(db)
    instance.carry(db)
    found = catalog.items(db)
    by_capability = catalog.counts(found)
    assert by_capability == {"chat": 2, "stt": 2, "tts": 2, "image": 3}
    assert [i.label for i in catalog.items(db, capability="image", state="on")] == [
        "qwen-image-3.0", "sdxl.safetensors", "flux.safetensors",
    ]
    defaults = {(i.capability, i.model_id) for i in found if i.default}
    assert defaults == {("stt", "whisper-large"), ("tts", "kokoro"), ("image", "flux.safetensors")}
    assert [i.label for i in catalog.items(db, q="kok")] == ["kokoro"]
    # A chat model's line leads to its page, a media model's to its own.
    assert all(i.href.startswith("/admin/models/") for i in found)
    assert all(("/media/" in i.href) == i.media for i in found)


# --- The pages ---------------------------------------------------------------------------
@pytest.fixture
def carried(db, client, registered) -> dict:
    ids = _older_release(db)
    instance.carry(db)
    for row in db.scalars(select(Connection)):
        ids[row.name] = row.id
    return ids


def test_connections_is_a_list_and_each_line_opens_a_page(client, carried):
    page = client.get("/admin/connections")
    assert page.status_code == 200
    assert page.text.count('data-testid="connection-line"') == 5
    assert 'href="/admin/connections/new"' in page.text
    # A list: no address or key field on it, and no key in it.
    assert 'name="base_url"' not in page.text and 'name="api_key"' not in page.text
    for secret in ("comfy-secret", "stt-secret", "connection-key"):
        assert secret not in page.text
    for name in ("ComfyUI", "Cloud"):
        assert f'href="/admin/connections/{carried[name]}"' in page.text


@pytest.mark.parametrize(
    ("name", "tabs"),
    [
        ("Cloud", ["overview", "models", "media", "remove"]),
        ("ComfyUI", ["overview", "models", "workflows", "remove"]),
        ("Speech to text", ["overview", "models", "remove"]),
    ],
)
def test_a_connection_s_page_has_the_tabs_that_apply(client, db, carried, name, tabs):
    from lembas.api.admin_connections import tabs_for

    row = db.get(Connection, carried[name])
    listed = tabs_for(row)
    assert [key for key, _, _ in listed] == tabs
    for _key, href, _label in listed:
        page = client.get(href)
        assert page.status_code == 200, href
        # Link tabs, each with its address; never radio tabs.
        assert page.text.count('class="tabs__tab') == len(tabs)
        assert 'role="radiogroup"' not in page.text and f'href="{href}"' in page.text
        assert "comfy-secret" not in page.text and "connection-key" not in page.text
    missing = {"media", "workflows"} - set(tabs)
    for tab in missing:
        assert client.get(f"/admin/connections/{row.id}/{tab}").status_code == 404


def test_adding_a_connection_starts_with_what_it_is(client, db, registered, monkeypatch):
    from lembas.services.images import comfy

    choice = client.get("/admin/connections/new")
    assert choice.status_code == 200 and 'name="base_url"' not in choice.text
    for type_id in providers.TYPE_IDS:
        assert f'href="/admin/connections/new?type={type_id}"' in choice.text
    form = client.get("/admin/connections/new?type=comfyui")
    assert 'name="base_url"' in form.text and 'name="provider"' not in form.text
    assert 'data-testid="connection-detect"' not in form.text

    async def discover(_config):
        return ["sdxl.safetensors", "flux.safetensors"], ["euler"], ["normal", "karras"]

    monkeypatch.setattr(comfy, "discover", discover)
    made = client.post(
        "/admin/connections",
        data={"name": "Studio", "base_url": COMFY_URL, "provider_type": "comfyui",
              "api_key": "studio-key"},
        follow_redirects=False,
    )
    row = db.scalar(select(Connection))
    assert made.headers["location"] == f"/admin/connections/{row.id}"
    assert (row.provider_type, row.kind) == ("comfyui", "openai")
    # A new ComfyUI draws at once: what it has is switched on.
    assert [e.model for e in store.entries(row, "image", enabled=True)] == [
        "sdxl.safetensors", "flux.safetensors"
    ]
    assert store.type_options(row)["schedulers"] == ["normal", "karras"]
    assert list(db.scalars(select(Model))) == []


def test_a_later_refresh_adds_a_new_checkpoint_switched_off(client, db, carried, monkeypatch):
    from lembas.services.images import comfy

    async def discover(_config):
        return ["sdxl.safetensors", "new.safetensors"], ["euler"], ["normal"]

    monkeypatch.setattr(comfy, "discover", discover)
    back = client.post(
        f"/admin/connections/{carried['ComfyUI']}/models/refresh", follow_redirects=False
    )
    assert back.headers["location"].startswith(f"/admin/connections/{carried['ComfyUI']}/models")
    db.expire_all()
    row = db.get(Connection, carried["ComfyUI"])
    assert {(e.model, e.enabled) for e in store.entries(row, "image")} == {
        ("sdxl.safetensors", True), ("flux.safetensors", True), ("new.safetensors", False)
    }
    # The answer of Test is the status card, swapped in place.
    card = client.post(f"/admin/connections/{carried['ComfyUI']}/test")
    assert f'id="connection-{row.id}"' in card.text and 'data-status="ok"' in card.text


def test_the_health_round_leaves_a_comfyui_and_a_speech_server_alone(db, carried, mock_http):
    import asyncio

    from lembas.services.llm import health

    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.host)
        return httpx.Response(200, json={"data": []})

    mock_http(handler)
    asyncio.run(health.tick(force=True))
    assert "workstation.example.org" not in asked


def test_a_model_is_added_set_up_and_made_the_default_on_its_own_page(client, db, carried):
    base = f"/admin/connections/{carried['Cloud']}"
    added = client.post(
        f"{base}/models/add", data={"capability": "tts", "model": "qwen3-tts-flash"},
        follow_redirects=False,
    )
    href = added.headers["location"]
    assert href == catalog.media_href(carried["Cloud"], "tts", "qwen3-tts-flash")
    page = client.get(href)
    assert page.status_code == 200 and 'data-testid="media-model-general"' in page.text
    # What the provider documents for it came with it.
    row = db.get(Connection, carried["Cloud"])
    assert store.get_entry(row, "tts", "qwen3-tts-flash").voice == "Cherry"

    client.post(f"{href}/options", data={"voice": "Ethan", "voices": "Cherry\nEthan",
                                         "speed": "1.1", "format": "wav", "language": "en"})
    client.post(href, data={"name": "Flash voice", "enabled": "true", "price": "10"})
    db.expire_all()
    entry = store.get_entry(db.get(Connection, carried["Cloud"]), "tts", "qwen3-tts-flash")
    assert (entry.name, entry.voice, entry.price) == ("Flash voice", "Ethan", 10.0)
    assert entry.options == {
        "voices": ["Cherry", "Ethan"], "speed": 1.1, "format": "wav", "language": "en"
    }
    options = client.get(f"{href}/options").text
    assert 'name="voices"' in options and 'name="steps"' not in options

    client.post(f"{href}/default")
    assert sources.default(db, "tts").label == "Cloud · qwen3-tts-flash"
    assert sources.chosen(db, "tts") == f"{carried['Cloud']}/qwen3-tts-flash"

    # Removed, the default is shown as gone and nothing is served in its place.
    gone = client.post(f"{href}/delete", follow_redirects=False)
    assert gone.headers["location"].startswith(f"{base}/models")
    db.expire_all()
    assert sources.default(db, "tts") is None and sources.stale(db, "tts")
    assert client.get(href).status_code == 404


def test_a_checkpoint_s_page_has_the_sampling_options_and_a_provider_s_has_not(
    client, db, carried
):
    checkpoint = catalog.media_href(carried["ComfyUI"], "image", "flux.safetensors")
    page = client.get(f"{checkpoint}/options").text
    for name in ("steps", "cfg", "sampler", "scheduler", "sizes", "negative"):
        assert f'name="{name}"' in page, name
    # The samplers it was found to have are the choice.
    assert '<option value="dpmpp_2m"' in page
    hosted = catalog.media_href(carried["Cloud"], "image", "qwen-image-3.0")
    page = client.get(f"{hosted}/options").text
    assert 'name="sizes"' in page and 'name="steps"' not in page
    # No Test for a picture somebody pays for; one for a checkpoint.
    assert 'data-testid="media-model-test"' not in client.get(hosted).text
    assert 'data-testid="media-model-test"' in client.get(checkpoint).text


def test_the_media_tab_keeps_the_switches_and_the_ticks(client, db, carried):
    base = f"/admin/connections/{carried['Cloud']}/media"
    page = client.get(base).text
    assert 'name="prefer_audio"' in page and page.count('data-testid="media-row"') == 2
    assert 'name="base_url"' not in page
    keep = catalog.media_key("image", "qwen-image-3.0")
    client.post(base, data={"prefer_image": "true", "enabled": keep})
    db.expire_all()
    row = db.get(Connection, carried["Cloud"])
    assert store.prefers(row, "image") and not store.prefers(row, "stt")
    assert [(e.model, e.enabled) for e in store.entries(row)] == [
        ("qwen3-asr-flash", False), ("qwen-image-3.0", True)
    ]
    # Its price, set on the model's own page, was not the tab's to change.
    assert store.get_entry(row, "image", "qwen-image-3.0").price == 0.03


def test_removing_a_connection_is_its_own_tab_and_stays_guarded(client, db, carried):
    page = client.get(f"/admin/connections/{carried['ComfyUI']}/remove").text
    assert "data-confirm-button=" in page and "default under Media" in page
    assert "data-confirm-button=" not in client.get(
        f"/admin/connections/{carried['ComfyUI']}"
    ).text
    gone = client.post(f"/admin/connections/{carried['ComfyUI']}/delete", follow_redirects=False)
    assert "message=" in gone.headers["location"]
    assert sources.default(db, "image") is None and sources.stale(db, "image")
    # Its workflows are somebody's work: they stay, for every ComfyUI.
    db.expire_all()
    assert db.get(ImageWorkflow, carried["workflow"]).connection_id is None


def test_models_is_one_filterable_list(client, db, carried):
    page = client.get("/admin/models")
    assert page.status_code == 200 and page.text.count('data-testid="model-line"') == 9
    assert 'name="display_name"' not in page.text
    for query, count in (("capability=image", 3), ("capability=chat", 2), ("state=off", 0),
                         (f"connection={carried['ComfyUI']}", 2), ("q=whisper", 1),
                         ("capability=tts&q=kok", 1)):
        text_ = client.get(f"/admin/models?{query}").text
        assert text_.count('data-testid="model-line"') == count, query
    # On or off from the list, at once -- and back to the list as it was.
    href = catalog.media_href(carried["ComfyUI"], "image", "sdxl.safetensors")
    back = client.post(f"{href}/toggle?back=/admin/models%3Fcapability%3Dimage",
                       follow_redirects=False)
    assert back.headers["location"] == "/admin/models?capability=image"
    db.expire_all()
    row = db.get(Connection, carried["ComfyUI"])
    assert store.get_entry(row, "image", "sdxl.safetensors").enabled is False
    chat = db.scalar(select(Model))
    elsewhere = client.post(f"/admin/models/{chat.id}/toggle?back=https://example.org/",
                            follow_redirects=False)
    assert elsewhere.headers["location"] == "/admin/models"
    db.expire_all()
    assert db.get(Model, chat.id).enabled is False


def test_a_chat_model_s_page_is_tabs_and_a_save_writes_its_tab_only(client, db, carried):
    model = db.scalar(select(Model).where(Model.connection_id == carried["Plan"]))
    model.description, model.pricing_json = "kept", {"input": 2.0}
    model.capabilities_json = {"tools": True, "tool_image": True, "vision": True}
    model.context_length, model.reasoning_efforts = 4096, ["low", "high"]
    db.commit()
    base = f"/admin/models/{model.id}"
    for tab, field in (("", "display_name"), ("/limits", "context_length"),
                       ("/prices", "data-testid=\"price-subscription\""),
                       ("/capabilities", "cap_vision"), ("/edit", "display_name")):
        page = client.get(base + tab)
        assert page.status_code == 200 and field in page.text, tab
        assert page.text.count('class="tabs__tab') == 4
    # A plan's rule is its connection's: said on the model, set nowhere on it.
    assert "a provider's plan" in client.get(f"{base}/capabilities").text
    assert 'name="interactive_only"' not in client.get(f"{base}/capabilities").text

    saved = client.post(base, data={"tab": "general", "display_name": "Named",
                                    "enabled": "true", "public": "true"},
                        follow_redirects=False)
    assert saved.headers["location"].startswith(f"{base}?saved=")
    db.expire_all()
    model = db.get(Model, model.id)
    assert model.display_name == "Named" and model.description == ""
    # The other tabs' values are what they were.
    assert model.pricing_json == {"input": 2.0} and model.context_length == 4096
    assert model.reasoning_efforts == ["low", "high"]
    assert model.capabilities_json == {"tools": True, "tool_image": True, "vision": True}

    limits = client.post(base, data={"tab": "limits", "context_length": "8192",
                                     "reasoning_efforts": ["low"], "parallel": "2"},
                         follow_redirects=False)
    assert limits.headers["location"].startswith(f"{base}/limits?saved=")
    db.expire_all()
    model = db.get(Model, model.id)
    assert model.context_length == 8192 and model.parallel == 2
    assert (model.display_name, model.enabled, model.public) == ("Named", True, True)
    assert model.capabilities_json["vision"] is True


def test_media_holds_the_defaults_and_no_address(client, db, carried):
    assert client.get("/admin/audio", follow_redirects=False).headers["location"] == (
        "/admin/media/stt"
    )
    assert client.get("/admin/images", follow_redirects=False).headers["location"] == (
        "/admin/media/image"
    )
    for capability in store.CAPABILITIES:
        page = client.get(f"/admin/media/{capability}")
        assert page.status_code == 200
        # One tab per capability: the first three, and the four generations
        # beyond a picture.
        assert page.text.count('class="tabs__tab') == len(store.ALL)
        for gone in ('name="base_url"', 'name="api_key"', 'name="stt_base_url"', "/test"):
            assert gone not in page.text, (capability, gone)
        assert "comfy-secret" not in page.text and "stt-secret" not in page.text
    # The generations beyond a picture have a tab each; nothing else has one.
    assert client.get("/admin/media/video").status_code == 200
    assert client.get("/admin/media/chat").status_code == 404

    # The default is chosen from what is added; anything else changes nothing.
    hosted = f"{carried['Cloud']}/qwen-image-3.0"
    client.post("/admin/media/image", data={"enabled": "true", "default": hosted,
                                            "max_tries": "3", "per_image": "0.5"})
    assert sources.chosen(db, "image") == hosted
    values = settings_store.get_group(db, settings_store.IMAGES)
    assert (values["connection"], values["comfyui"], values["max_tries"]) == (hosted, "", 3)
    client.post("/admin/media/image", data={"enabled": "true", "default": "made/up"})
    assert sources.chosen(db, "image") == hosted
    back = f"{carried['ComfyUI']}/sdxl.safetensors"
    client.post("/admin/media/image", data={"enabled": "true", "default": back})
    assert sources.default(db, "image").dialect == sources.COMFYUI
    assert settings_store.images(db)["default_checkpoint"] == "sdxl.safetensors"
    client.post("/admin/media/stt", data={"default": instance.NONE})
    assert sources.default(db, "stt") is None
    assert settings_store.audio(db)["stt_enabled"] is False


def test_a_workflow_is_saved_for_its_comfyui_and_listed_on_its_tab(client, db, carried):
    import json

    comfy = carried["ComfyUI"]
    new = client.get(f"/admin/images/workflows/new?connection={comfy}")
    assert new.status_code == 200 and f'value="{comfy}" selected' in new.text
    made = client.post(
        "/admin/images/workflows",
        data={"name": "Photo", "slug": "photo", "description": "people", "enabled": "true",
              "connection_id": comfy, "workflow": json.dumps(image_tool_base())},
        follow_redirects=False,
    )
    assert made.headers["location"].startswith(f"/admin/connections/{comfy}/workflows")
    row = db.scalar(select(ImageWorkflow).where(ImageWorkflow.slug == "photo"))
    assert row.connection_id == comfy
    tab = client.get(f"/admin/connections/{comfy}/workflows").text
    assert f"/admin/images/workflows/{row.id}/edit" in tab
    client.post(f"/admin/connections/{comfy}/workflows", data={"default_workflow_id": row.id})
    assert settings_store.images(db)["default_workflow_id"] == row.id


# --- The APIs ----------------------------------------------------------------------------
def test_model_objects_say_their_capability(client, db, carried):
    from lembas.security import api_tokens

    user = db.scalars(select(User)).first()
    _, token = api_tokens.create(db, user, name="t", scopes=["app", "chat", "models"])
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    data = client.get("/v1/models", headers=headers).json()["data"]
    assert data and all(entry["capability"] == entry["kind"] for entry in data)
    assert {entry["capability"] for entry in data} >= {"chat", "stt", "tts", "image"}
    listed = client.get("/api/v2/models", headers=headers)
    assert listed.status_code == 200, listed.text
    models = listed.json()["models"]
    assert models and all(entry["capability"] == "chat" for entry in models)


# --- Review fixes: the carry ------------------------------------------------------------
def test_one_address_that_cannot_be_carried_does_not_stop_the_others(db, monkeypatch):
    _older_release(db)
    real = instance._find

    def find(db_, type_id, address, key):
        if type_id == providers.SPEECH:
            raise RuntimeError("a value in a shape nobody expected")
        return real(db_, type_id, address, key)

    monkeypatch.setattr(instance, "_find", find)
    done = instance.carry(db)
    db.expire_all()
    # The ComfyUI is a connection and the default points at it...
    assert any("ComfyUI" in line for line in done)
    assert db.scalar(select(Connection).where(Connection.provider_type == providers.COMFYUI))
    assert settings_store.get_group(db, settings_store.IMAGES).get("carried")
    # ...the speech servers are not marked, so the next start carries them.
    audio = settings_store.get_group(db, settings_store.AUDIO)
    assert not audio.get("stt_carried") and not audio.get("tts_carried")
    monkeypatch.undo()
    assert any("speech server" in line for line in instance.carry(db))
    assert settings_store.get_group(db, settings_store.AUDIO).get("tts_carried")


def test_a_key_that_cannot_be_opened_is_carried_as_it_is_and_matches_only_itself(db):
    """A database restored under another secret: the stored key opens to
    nothing. It is not "the same key" as a connection with no key at all."""
    from lembas.services import crypto

    other = crypto.fernet_for("another-secret-entirely").encrypt(b"sk-old").decode("ascii")
    assert crypto.decrypt(other) == ""
    _older_release(db)
    settings_store.update(db, {"tts_api_key_encrypted": other}, key=settings_store.AUDIO)
    keyless = Connection(name="Somebody's", base_url=TTS_URL, provider_type=providers.SPEECH,
                         enabled=True, position=9)
    db.add(keyless)
    db.commit()
    instance.carry(db)
    db.expire_all()
    rows = list(db.scalars(select(Connection).where(Connection.base_url == TTS_URL)))
    assert len(rows) == 2
    made = next(r for r in rows if r.id != keyless.id)
    assert made.api_key_encrypted == other
    assert not instance._same_key(other, "") and instance._same_key(other, other)
    assert not instance._same_key(
        other, crypto.fernet_for("a-third-secret").encrypt(b"sk-x").decode("ascii")
    )
