"""Long generations as jobs: every state, a restart in the middle, a cancel,
where the result goes and how a chat is told -- against a ComfyUI and a
provider that are written here (`httpx.MockTransport`). Nothing real is
called."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from lembas.db.models import (
    ROLE_USER,
    Chat,
    Connection,
    ImageWorkflow,
    MediaJob,
    Message,
    Model,
    NodeVersion,
    User,
)
from lembas.services import fs, settings_store
from lembas.services import usage as usage_service
from lembas.services.crypto import encrypt
from lembas.services.images import starters
from lembas.services.media import jobs, library, store, tools
from lembas.services.media import sources as media_sources
from lembas.services.tools import ToolContext

MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x0a" + b"\xff\xfb\x90\x00" * 32
GLB = b"glTF\x02\x00\x00\x00" + b"\x00" * 64
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99"
    b"=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


class Far:
    """A ComfyUI and a DashScope in one handler: what was asked, and what
    each answers next."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.prompts: dict[str, dict] = {}
        self.history: dict[str, dict] = {}
        self.queue: dict[str, list] = {"queue_running": [], "queue_pending": []}
        self.files: dict[str, bytes] = {}
        self.tasks: dict[str, dict] = {}
        self.posted: list[tuple[str, dict]] = []
        self.refuse_submit = ""
        self.down = False
        self.cancel_ok = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        path = request.url.path
        if request.url.host == "files.example.org":
            return httpx.Response(200, content=self.files[path])
        if path == "/upload/image":
            return httpx.Response(200, json={"name": "in.png", "subfolder": "lembas",
                                             "type": "input"})
        if path == "/prompt":
            body = json.loads(request.content)
            if self.refuse_submit:
                return httpx.Response(400, json={"error": {"message": self.refuse_submit},
                                                 "node_errors": {}})
            self.prompts[body["prompt_id"]] = body["prompt"]
            self.queue["queue_pending"].append([1, body["prompt_id"], {}, {}, []])
            return httpx.Response(200, json={"prompt_id": body["prompt_id"], "number": 1,
                                             "node_errors": {}})
        if path.startswith("/history/"):
            ref = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=self.history.get(ref, {}))
        if path == "/queue" and request.method == "GET":
            return httpx.Response(200, json=self.queue)
        if path in ("/queue", "/interrupt"):
            self.posted.append((path, json.loads(request.content)))
            return httpx.Response(200)
        if path == "/view":
            return httpx.Response(200, content=self.files[request.url.params["filename"]])
        if path.endswith("/video-generation/video-synthesis"):
            assert request.headers["X-DashScope-Async"] == "enable"
            self.posted.append((path, json.loads(request.content)))
            if self.refuse_submit:
                return httpx.Response(400, json={"code": "InvalidParameter",
                                                 "message": self.refuse_submit})
            self.tasks["task-1"] = {"task_status": "PENDING", "task_id": "task-1"}
            return httpx.Response(200, json={"output": self.tasks["task-1"], "request_id": "r"})
        if path.endswith("/cancel"):
            self.posted.append((path, {}))
            if not self.cancel_ok:
                return httpx.Response(400, json={"code": "UnsupportedOperation",
                                                 "message": "not pending"})
            return httpx.Response(200, json={"request_id": "r"})
        if "/api/v1/tasks/" in path:
            ref = path.rsplit("/", 1)[-1]
            output = self.tasks.get(ref, {"task_status": "UNKNOWN", "task_id": ref})
            body = {"output": output, "request_id": "r"}
            if output.get("task_status") == "SUCCEEDED":
                body["usage"] = {"duration": 5, "output_video_duration": 5, "SR": 720,
                                 "video_count": 1}
            return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": "unknown"})

    def finish(self, ref: str, key: str, filename: str, payload: bytes, **extra) -> None:
        """ComfyUI's record of a finished run, in the shape `/history/{id}` has."""
        self.files[filename] = payload
        self.queue = {"queue_running": [], "queue_pending": []}
        self.history[ref] = {ref: {
            "status": {"status_str": "success", "completed": True, "messages": []},
            "outputs": {"9": {key: [{"filename": filename, "subfolder": "lembas",
                                     "type": "output"}], **extra}},
        }}

    def fail(self, ref: str, event: str, **payload) -> None:
        self.queue = {"queue_running": [], "queue_pending": []}
        self.history[ref] = {ref: {
            "status": {"status_str": "error", "completed": False,
                       "messages": [[event, payload]]},
            "outputs": {},
        }}


@pytest.fixture
def far(monkeypatch) -> Far:
    handler = Far()
    real = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    # A result link is fetched like any address somebody else chose; the
    # made-up host does not resolve here.
    from lembas.services import fetch as fetch_service

    monkeypatch.setattr(fetch_service, "check_url", lambda url, **_: url)
    return handler


@pytest.fixture
def woken(monkeypatch) -> list[tuple[str, str]]:
    from lembas.services import wake

    seen: list[tuple[str, str]] = []

    async def wake_chat(chat_id: str, content: str, **_):
        seen.append((chat_id, content))
        return ""

    monkeypatch.setattr(wake, "wake_chat", wake_chat)
    return seen


@pytest.fixture
def world(db, registered) -> dict:
    """A person, a chat on a provider's model, a ComfyUI with a workflow of
    each long kind, and the provider's own video model."""
    user = db.scalars(select(User).order_by(User.created_at)).first()
    fs.ensure_home(db, user)
    comfyui = Connection(name="Studio", base_url="http://comfy.example.org:8188",
                         provider_type="comfyui", enabled=True, position=0)
    cloud = Connection(
        name="Cloud", base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        provider_type="alibaba", enabled=True, position=1, api_key_encrypted=encrypt("sk-secret"),
        media_json={"prefer_image": False, "models": [
            {"capability": "video", "model": "wan2.7-t2v", "enabled": True, "price": 0.1},
        ]},
    )
    db.add_all([comfyui, cloud])
    db.flush()
    db.add(Model(connection_id=cloud.id, model_id="qwen-plus", enabled=True,
                 capabilities_json={"tools": True}))
    for key in ("video_t2v", "music", "model3d"):
        starter = starters.get(key)
        db.add(ImageWorkflow(slug=key, name=starter.name, kind=starter.kind, enabled=True,
                             workflow_json=starter.template(), connection_id=comfyui.id))
    chat = Chat(user_id=user.id, title="Clips", model_id="qwen-plus", connection_id=cloud.id)
    db.add(chat)
    db.flush()
    message = Message(chat_id=chat.id, role=ROLE_USER, content="make it")
    db.add(message)
    settings_store.update(
        db,
        {"video_enabled": True, "video_default": f"{comfyui.id}/video_t2v",
         "music_enabled": True, "music_default": f"{comfyui.id}/music",
         "model3d_enabled": True, "model3d_default": f"{comfyui.id}/model3d",
         "video_price": 0.02, "music_price": 0.5},
        key=settings_store.IMAGES,
    )
    db.commit()
    context = ToolContext(owner_id=user.id, chat_id=chat.id, model_id="qwen-plus",
                          connection_id=cloud.id, message_id=message.id)
    return {"user": user, "chat": chat, "comfyui": comfyui, "cloud": cloud,
            "context": context, "message": message}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def the_job(db) -> MediaJob:
    db.expire_all()
    return db.scalars(select(MediaJob).order_by(MediaJob.started_at.desc())).first()


# --- Started, and answered at once ------------------------------------------------------
def test_a_video_is_started_as_a_job_and_the_tool_answers_at_once(db, world, far, woken):
    outcome = run(tools.run_video(world["context"], {"prompt": "a kite over dunes",
                                                    "seconds": 4, "aspect": "9:16"}))
    job = the_job(db)
    assert outcome.event["status"] == "ok"
    assert outcome.event["media_job"] == {"id": job.id, "kind": "video", "state": "running"}
    assert job.id in outcome.content and "Do not wait" in outcome.content
    assert job.state == "running" and job.dialect == "comfyui" and job.kind == "video"
    assert job.chat_id == world["chat"].id and job.message_id == world["message"].id
    # The id this server chose is the one ComfyUI was given and the one kept.
    assert job.provider_ref in far.prompts
    sent = far.prompts[job.provider_ref]
    assert sent["4"]["inputs"]["text"] == "a kite over dunes"
    assert sent["7"]["inputs"] == {"width": 480, "height": 832, "length": 65, "batch_size": 1}
    assert sent["10"]["inputs"]["fps"] == 16
    assert not woken and "media" not in outcome.event


def test_a_finished_video_is_fetched_kept_counted_and_the_chat_is_told(db, world, far, woken):
    run(tools.run_video(world["context"], {"prompt": "a kite", "seconds": 4}))
    job = the_job(db)
    # Not finished: a pass changes nothing and tells nobody.
    run(jobs.tick(everything=True))
    assert the_job(db).state == "running" and not woken
    # SaveVideo reports its file under `images`, with `animated`.
    far.finish(job.provider_ref, "images", "video_00001_.mp4", MP4, animated=[True])
    run(jobs.tick(everything=True))
    job = the_job(db)
    assert job.state == "done" and job.ended_at is not None and job.notified_at is not None
    result = job.result_json
    home = fs.ensure_home(db, world["user"]).path
    assert result["kind"] == "video" and result["mime"] == "video/mp4"
    assert result["path"].startswith(f"{home}/Generated/video-") and result["path"].endswith(".mp4")
    assert result["seconds"] == 4 and (result["width"], result["height"]) == (832, 480)
    assert fs.read(fs.view(db, world["user"]), result["path"]) == MP4
    # The version says which chat and which reply made it.
    version = db.get(NodeVersion, result["version_id"])
    assert (version.chat_id, version.message_id) == (world["chat"].id, world["message"].id)
    # Priced per second, at the ComfyUI's price: 4 s at 0.02.
    assert job.cost_micro == 80_000
    rows = usage_service.by_model(db, world["user"].id)
    assert [(r.model_id, r.cost_micro) for r in rows] == [("comfyui/video_t2v", 80_000)]
    # Told once, in words that name themselves and forbid another generation.
    assert len(woken) == 1 and woken[0][0] == world["chat"].id
    assert job.id in woken[0][1] and result["path"] in woken[0][1]
    assert "Do not generate it again" in woken[0][1]
    run(jobs.tick(everything=True))
    run(jobs.reconcile())
    assert len(woken) == 1


@pytest.mark.parametrize(
    ("runner", "args", "key", "name", "payload", "mime"),
    [
        ("run_music", {"prompt": "calm piano", "lyrics": "la", "seconds": 30},
         "audio", "music_00001_.mp3", MP3, "audio/mpeg"),
        ("run_model", {}, "3d", "model_00001_.glb", GLB, "model/gltf-binary"),
    ],
)
def test_music_and_a_3d_model_are_jobs_too(
    db, world, far, woken, runner, args, key, name, payload, mime
):
    from lembas.services import files as files_service

    files_service.store(db, user_id=world["user"].id, chat_id=world["chat"].id, payload=PNG,
                        filename="chair.png", keep_original=True)
    outcome = run(getattr(tools, runner)(world["context"], args))
    assert outcome.event["status"] == "ok", outcome.content
    job = the_job(db)
    sent = far.prompts[job.provider_ref]
    if runner == "run_music":
        assert sent["2"]["inputs"]["tags"] == "calm piano"
        assert sent["2"]["inputs"]["lyrics"] == "la" and sent["4"]["inputs"]["seconds"] == 30
    else:
        # The most recent picture of the chat was uploaded and named.
        assert sent["2"]["inputs"]["image"] == "lembas/in.png"
        assert any(r.url.path == "/upload/image" for r in far.requests)
    far.finish(job.provider_ref, key, name, payload)
    run(jobs.tick(everything=True))
    job = the_job(db)
    assert job.state == "done", job.error
    assert job.result_json["mime"] == mime and job.result_json["kind"] == job.kind
    assert len(woken) == 1


# --- The other endings ------------------------------------------------------------------
def test_a_run_that_fails_ends_failed_and_the_chat_is_told_not_to_retry(db, world, far, woken):
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    far.fail(job.provider_ref, "execution_error", node_type="KSampler",
             exception_type="torch.OutOfMemoryError", exception_message="CUDA out of memory.")
    run(jobs.tick(everything=True))
    job = the_job(db)
    assert job.state == "failed" and "video memory" in job.error and not job.result_json
    assert job.cost_micro == 0 and usage_service.by_model(db, world["user"].id) == []
    assert len(woken) == 1 and "Do not start it again" in woken[0][1]


def test_what_is_not_the_kind_asked_for_is_refused_by_its_bytes(db, world, far, woken):
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    far.finish(job.provider_ref, "images", "video_00001_.mp4", b"<html>not a film</html>")
    run(jobs.tick(everything=True))
    job = the_job(db)
    assert job.state == "failed" and "not a video" in job.error
    home = fs.ensure_home(db, world["user"]).path
    assert fs.lookup(db, world["user"].org_id, f"{home}/Generated") is None


def test_a_file_over_the_cap_is_not_kept(db, world, far, woken):
    settings_store.update(db, {"video_max_mb": 1}, key=settings_store.IMAGES)
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    far.finish(job.provider_ref, "images", "video_00001_.mp4", MP4 + b"\x00" * (1024 * 1024 + 8))
    run(jobs.tick(everything=True))
    job = the_job(db)
    assert job.state == "failed" and "1MB" in job.error


def test_a_job_past_its_deadline_is_given_up_on_and_stopped_there(db, world, far, woken):
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    far.queue = {"queue_running": [[1, job.provider_ref, {}, {}, []]], "queue_pending": []}
    job.deadline_at = jobs.now() - timedelta(seconds=1)
    db.commit()
    run(jobs.tick(everything=True))
    job = the_job(db)
    assert job.state == "expired" and "interrupted" in job.error
    assert ("/interrupt", {"prompt_id": job.provider_ref}) in far.posted
    assert len(woken) == 1


def test_a_far_side_that_is_down_is_asked_again_not_failed(db, world, far, woken):
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    far.down = True
    run(jobs.tick(everything=True))
    assert the_job(db).state == "running" and not woken
    far.down = False
    far.finish(the_job(db).provider_ref, "gifs", "clip.webm", b"\x1aE\xdf\xa3" + b"\x00" * 64)
    run(jobs.tick(everything=True))
    job = the_job(db)
    # A custom node's key (`gifs`) is read like any other.
    assert job.state == "done" and job.result_json["path"].endswith(".webm")


# --- A restart in the middle --------------------------------------------------------------
def test_a_restart_mid_job_re_attaches_by_the_far_side_id_and_starts_nothing(
    db, world, far, woken
):
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    submitted = len(far.prompts)
    # The process ends: nothing in memory survives.
    jobs._STATE.next_at = None
    far.finish(job.provider_ref, "images", "video_00001_.mp4", MP4)
    run(jobs.reconcile())
    job = the_job(db)
    assert job.state == "done" and len(far.prompts) == submitted
    assert len(woken) == 1


def test_a_job_that_ended_while_nobody_could_be_told_is_told_at_the_next_start(
    db, world, far, woken, monkeypatch
):
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    far.finish(job.provider_ref, "images", "video_00001_.mp4", MP4)

    async def nothing(_job_id):  # the process dies between the store and the wake
        return None

    monkeypatch.setattr(jobs, "_tell", nothing)
    run(jobs.tick(everything=True))
    assert the_job(db).state == "done" and the_job(db).notified_at is None and not woken
    monkeypatch.undo()
    monkeypatch.setattr(httpx, "AsyncClient", httpx.AsyncClient)
    from lembas.services import wake

    seen: list = []

    async def wake_chat(chat_id, content, **_):
        seen.append(chat_id)
        return ""

    monkeypatch.setattr(wake, "wake_chat", wake_chat)
    run(jobs.reconcile())
    assert seen == [world["chat"].id] and the_job(db).notified_at is not None
    # The result was never lost: it is in Files and in the list.
    assert jobs.view(the_job(db))["result"]["path"]


def test_a_request_never_confirmed_is_given_up_on_and_never_sent_again(db, world, far, woken):
    """The row was written; the process died before the provider answered."""
    source = media_sources.chain(db, "video", world["cloud"])
    with_own = dict(world["cloud"].media_json, prefer_image=True)
    world["cloud"].media_json = with_own
    db.commit()
    source = media_sources.chain(db, "video", world["cloud"])[0]
    job = jobs.open_job(db, user_id=world["user"].id, chat_id=world["chat"].id,
                        message_id=None, kind="video", source=source, params={}, minutes=30)
    run(jobs.tick(everything=True))
    assert the_job(db).state == "queued"  # young: its request may still be in flight
    job.started_at = jobs.now() - timedelta(seconds=jobs.UNCONFIRMED_SECONDS + 5)
    db.commit()
    run(jobs.tick(everything=True))
    job = the_job(db)
    assert job.state == "failed" and "may or may not" in job.error
    assert not any("video-synthesis" in r.url.path for r in far.requests)


# --- Cancel -------------------------------------------------------------------------------
def test_cancel_takes_a_waiting_run_out_of_the_queue(db, world, far, woken):
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    view = run(jobs.cancel(job.id, world["user"].id))
    assert view["state"] == "cancelled" and "queue" in view["error"]
    assert ("/queue", {"delete": [job.provider_ref]}) in far.posted
    # Nobody is woken for what the person did themselves, and a later pass
    # that finds the run finished does not resurrect it.
    far.finish(job.provider_ref, "images", "video_00001_.mp4", MP4)
    run(jobs.tick(everything=True))
    assert the_job(db).state == "cancelled" and not woken and not the_job(db).result_json


def test_cancel_interrupts_the_run_when_it_is_the_one_running(db, world, far, woken):
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    far.queue = {"queue_running": [[1, job.provider_ref, {}, {}, []]], "queue_pending": []}
    view = run(jobs.cancel(job.id, world["user"].id))
    assert view["state"] == "cancelled"
    assert far.posted == [("/interrupt", {"prompt_id": job.provider_ref})]


def test_somebody_elses_job_cannot_be_cancelled_or_seen(db, world, far, woken):
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    assert run(jobs.cancel(job.id, "somebody-else")) is None
    assert jobs.owned(db, job.id, "somebody-else") is None
    assert the_job(db).state == "running"


# --- Limits -------------------------------------------------------------------------------
def test_a_person_has_only_so_many_jobs_at_once(db, world, far, woken):
    settings_store.update(db, {"jobs_per_person": 1}, key=settings_store.IMAGES)
    assert run(tools.run_video(world["context"], {"prompt": "one"})).event["status"] == "ok"
    second = run(tools.run_music(world["context"], {"prompt": "two"}))
    assert second.event["status"] == "error" and "still being made" in second.content
    assert db.scalar(select(MediaJob).where(MediaJob.kind == "music")) is None


def test_a_refused_start_fails_the_job_and_tells_the_model_not_to_retry(db, world, far, woken):
    far.refuse_submit = "Value not in list: unet_name"
    outcome = run(tools.run_video(world["context"], {"prompt": "a kite"}))
    assert outcome.event["status"] == "error"
    assert "unet_name" in outcome.content and "Do not try again" in outcome.content
    job = the_job(db)
    assert job.state == "failed" and job.notified_at is not None and not woken
    assert jobs.active_count(db, world["user"].id) == 0


# --- Video from a provider (DashScope) ------------------------------------------------------
def _own_video(db, world, **entry) -> None:
    world["cloud"].media_json = {"prefer_image": True, "models": [
        {"capability": "video", "model": "wan2.7-t2v", "enabled": True, "price": 0.1, **entry},
    ]}
    db.commit()


def test_a_providers_video_is_a_task_that_is_polled_and_its_link_fetched_at_once(
    db, world, far, woken
):
    _own_video(db, world)
    outcome = run(tools.run_video(world["context"], {"prompt": "a kite", "seconds": 5,
                                                    "aspect": "9:16"}))
    assert outcome.event["status"] == "ok", outcome.content
    job = the_job(db)
    assert (job.dialect, job.provider_ref, job.state) == ("dashscope_video", "task-1", "running")
    path, body = far.posted[0]
    assert path == "/api/v1/services/aigc/video-generation/video-synthesis"
    assert body == {"model": "wan2.7-t2v", "input": {"prompt": "a kite"},
                    "parameters": {"duration": 5, "watermark": False, "resolution": "720P",
                                   "ratio": "9:16"}}
    run(jobs.tick(everything=True))
    assert the_job(db).state == "running"
    far.tasks["task-1"] = {"task_status": "SUCCEEDED", "task_id": "task-1",
                           "video_url": "https://files.example.org/out/clip.mp4?sig=1"}
    far.files["/out/clip.mp4"] = MP4
    run(jobs.tick(everything=True))
    job = the_job(db)
    assert job.state == "done", job.error
    assert job.result_json["seconds"] == 5 and job.result_json["mime"] == "video/mp4"
    # Per second, at the model's own price, in the connection's currency.
    assert job.cost_micro == 500_000
    # The link was fetched without the connection's key, and is not kept.
    fetched = next(r for r in far.requests if r.url.host == "files.example.org")
    assert "authorization" not in fetched.headers
    assert "files.example.org" not in json.dumps(jobs.view(job)) and len(woken) == 1


def test_a_first_picture_goes_as_a_data_address_and_an_older_model_gets_its_own_shape(
    db, world, far, woken
):
    from lembas.services import files as files_service
    from lembas.services.media import video

    files_service.store(db, user_id=world["user"].id, chat_id=world["chat"].id, payload=PNG,
                        filename="dune.png", keep_original=True)
    _own_video(db, world)
    run(tools.run_video(world["context"], {"prompt": "it moves", "image": "dune.png"}))
    body = far.posted[0][1]
    media = body["input"]["media"]
    assert media[0]["type"] == "first_frame"
    assert media[0]["url"].startswith("data:image/png;base64,")
    assert "ratio" not in body["parameters"]
    source = media_sources.chain(db, "video", world["cloud"])[0]
    older = video.body_for(
        media_sources.Source(**{**source.__dict__, "model": "wan2.2-t2v-plus"}), "x",
        picture=None, seconds=5, resolution="720P", ratio="16:9",
    )
    assert older["parameters"] == {"duration": 5, "watermark": False, "size": "1280*720"}
    older = video.body_for(
        media_sources.Source(**{**source.__dict__, "model": "wan2.6-i2v"}), "x",
        picture=PNG, seconds=5, resolution="720P", ratio="16:9",
    )
    assert older["input"]["img_url"].startswith("data:image/png") and "media" not in older["input"]


@pytest.mark.parametrize(
    ("status", "state", "word"),
    [("FAILED", "failed", "DataInspectionFailed"), ("UNKNOWN", "expired", "no longer knows"),
     ("CANCELED", "failed", "cancelled")],
)
def test_a_providers_other_answers(db, world, far, woken, status, state, word):
    _own_video(db, world)
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    far.tasks["task-1"] = {"task_status": status, "task_id": "task-1",
                           "code": "DataInspectionFailed", "message": "sk-secret refused"}
    run(jobs.tick(everything=True))
    job = the_job(db)
    assert job.state == state and word in job.error
    assert "sk-secret" not in job.error and job.cost_micro == 0


@pytest.mark.parametrize("ok", [True, False])
def test_cancelling_a_providers_task_says_what_the_provider_did(db, world, far, woken, ok):
    _own_video(db, world)
    far.cancel_ok = ok
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    view = run(jobs.cancel(the_job(db).id, world["user"].id))
    assert view["state"] == "cancelled"
    assert ("/api/v1/tasks/task-1/cancel", {}) in far.posted
    assert ("before it began" in view["error"]) is ok
    assert ("may still be finished and charged" in view["error"]) is not ok


def test_a_plans_video_is_for_a_persons_turn_and_never_for_a_scheduled_run(db, world, far):
    from lembas.services import interactive

    world["cloud"].provider_type = "alibaba_plan"
    world["cloud"].base_url = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    world["cloud"].media_json = {"prefer_image": True, "models": [
        {"capability": "video", "model": "wan3.0-video", "enabled": True}]}
    settings_store.update(db, {"video_default": ""}, key=settings_store.IMAGES)
    db.commit()
    assert interactive.connection_only(world["cloud"])
    mine = media_sources.chain(db, "video", world["cloud"], interactive=True)
    assert [s.model for s in mine] == ["wan3.0-video"] and mine[0].price == 0
    assert media_sources.chain(db, "video", world["cloud"], interactive=False) == []
    assert any(e.model == "wan3.0-video" and e.capability == "video"
               for e in store.offers("alibaba_plan", world["cloud"].base_url))


# --- The doors ----------------------------------------------------------------------------
def test_the_v2_door_lists_shows_and_cancels(client, db, world, far, woken):
    from lembas.security import api_tokens

    _, raw = api_tokens.create(db, world["user"], name="LLeMbas for Android", scopes=["app"])
    headers = {"Authorization": f"Bearer {raw}"}
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    listed = client.get("/api/v2/media/jobs", headers=headers,
                        params={"chat_id": world["chat"].id, "active": True}).json()
    assert [j["id"] for j in listed["jobs"]] == [job.id]
    one = client.get(f"/api/v2/media/jobs/{job.id}", headers=headers).json()
    assert one["state"] == "running" and one["active"] and one["result"] is None
    assert one["prompt"] == "a kite"
    assert one["source"] == "Studio · " + starters.get("video_t2v").name
    assert client.get("/api/v2/media/jobs/nope", headers=headers).status_code == 404
    gone = client.delete(f"/api/v2/media/jobs/{job.id}", headers=headers)
    assert gone.status_code == 200 and gone.json()["state"] == "cancelled"
    assert client.delete(f"/api/v2/media/jobs/{job.id}", headers=headers).json()["state"] == (
        "cancelled"
    )


def test_a_generated_file_is_served_by_its_id_wherever_it_is_now(client, db, world):
    result = library.keep(db, world["user"], kind="video", payload=MP4, chat_id=world["chat"].id)
    db.commit()
    again = library.keep(db, world["user"], kind="video", payload=MP4, when=None)
    db.commit()
    assert again["path"] != result["path"]  # made, never written over
    fs.mv(fs.view(db, world["user"]), result["path"],
          result["path"].replace("/Generated/", "/Generated/kept-"))
    db.commit()
    part = client.get("/files/raw", params={"node": result["node_id"], "inline": 1},
                      headers={"Range": "bytes=0-9"})
    assert part.status_code == 206 and part.content == MP4[:10]
    assert part.headers["content-type"].startswith("video/mp4")
    opened = client.get("/media/open", params={"node": result["node_id"]}, follow_redirects=False)
    assert opened.status_code == 303 and "kept-" in opened.headers["location"]
    assert client.get("/files/raw", params={"node": "nope"}).status_code == 404


def test_the_card_asks_for_itself_while_active_and_holds_the_player_when_done(
    client, db, world, far, woken
):
    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = the_job(db)
    card = client.get(f"/media/jobs/{job.id}/card").text
    assert 'hx-trigger="every 3s"' in card and "media-job-cancel" in card
    far.finish(job.provider_ref, "images", "video_00001_.mp4", MP4)
    run(jobs.tick(everything=True))
    card = client.get(f"/media/jobs/{job.id}/card").text
    node = the_job(db).result_json["node_id"]
    assert "hx-trigger" not in card and "media-job-cancel" not in card
    assert f'src="/files/raw?node={node}&amp;inline=1"' in card and "<video" in card
    assert f'href="/media/open?node={node}"' in card and "download=" in card


def test_a_picture_given_to_a_workflow_that_takes_none_is_said_not_ignored(db, world, far):
    from lembas.services import files as files_service

    files_service.store(db, user_id=world["user"].id, chat_id=world["chat"].id, payload=PNG,
                        filename="dune.png", keep_original=True)
    outcome = run(tools.run_video(world["context"], {"prompt": "it moves", "image": "dune.png"}))
    assert outcome.event["status"] == "error" and "cannot start from a picture" in outcome.content
    assert not far.prompts and db.scalar(select(MediaJob)) is None
