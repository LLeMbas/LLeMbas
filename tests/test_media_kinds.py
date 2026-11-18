# ruff: noqa: F811 -- the fixtures of test_media_jobs.py are used here by name
"""The generations beyond a picture: which source serves each, the tools a
chat is offered, an edited picture in the call, ComfyUI's workflow kinds
(placeholders, outputs, import, the starters), and where results go.

ComfyUI's shapes here were read off its source (0.46.0, commit a277b5bf):
`server.py` for `/prompt`, `/queue`, `/interrupt` and `/upload/image`;
`comfy_api/latest/_ui.py` for what a save node reports -- `SavedImages` under
`images`, `PreviewVideo` (what `SaveVideo` returns) under `images` with
`animated`, `SavedAudios` under `audio`, `Saved3DModels` under `3d` -- and
`tests/fixtures/comfyui/node_inputs.json` for the inputs of the nodes the
starters use. No ComfyUI and no provider was called."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from lembas.db.models import Chat, Connection, ImageWorkflow, MediaJob, Message
from lembas.services import fs, settings_store
from lembas.services import tools as tools_service
from lembas.services.images import comfy, importer, starters, workflow
from lembas.services.images import tool as image_tool
from lembas.services.llm import openai_client
from lembas.services.media import library, store, tools
from lembas.services.media import sources as media_sources
from tests.test_media_jobs import GLB, MP3, MP4, PNG, far, run, woken, world  # noqa: F401

NODES = json.loads(
    (Path(__file__).parent / "fixtures/comfyui/node_inputs.json").read_text(encoding="utf-8")
)

# What `GET /history/{id}` holds for one finished run of each kind, as the
# save nodes of ComfyUI 0.46.0 report it.
OUTPUTS = {
    "image": {"9": {"images": [{"filename": "a_00001_.png", "subfolder": "", "type": "output"}]}},
    "video": {"11": {"images": [{"filename": "v_00001_.mp4", "subfolder": "video",
                                 "type": "output"}], "animated": [True]}},
    "music": {"8": {"audio": [{"filename": "m_00001_.mp3", "subfolder": "audio",
                               "type": "output"}]}},
    "model3d": {"10": {"3d": [{"filename": "o_00001_.glb", "subfolder": "3d", "type": "output"}],
                       "camera_info": [None], "model_3d_info": []}},
    # A preview beside a save, a custom pack's own key, and a text output.
    "mixed": {
        "5": {"images": [{"filename": "tmp_00001_.png", "subfolder": "", "type": "temp"}]},
        "7": {"gifs": [{"filename": "clip.webm", "subfolder": "", "type": "output"}]},
        "8": {"text": ["done"]},
        "9": {"result": ["3d/preview.glb", None, None]},
    },
}


# --- What a run saved ---------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "name", "key"),
    [("image", "a_00001_.png", "images"), ("video", "v_00001_.mp4", "images"),
     ("music", "m_00001_.mp3", "audio"), ("model3d", "o_00001_.glb", "3d")],
)
def test_the_file_of_a_kind_is_found_under_whatever_key_its_node_used(kind, name, key):
    found = comfy.outputs_in(OUTPUTS[kind])
    chosen = comfy.pick(found, kind)
    assert (chosen.filename, chosen.key, chosen.kind) == (name, key, "output")
    assert comfy.pick(found, "music" if kind != "music" else "video") is None


def test_every_key_is_read_and_what_is_not_a_file_is_passed_over():
    found = comfy.outputs_in(OUTPUTS["mixed"])
    assert [(o.key, o.filename) for o in found] == [("images", "tmp_00001_.png"),
                                                    ("gifs", "clip.webm")]
    assert comfy.pick(found, "video").filename == "clip.webm"
    # A preview is taken only when nothing was saved.
    assert comfy.pick(found, "image").kind == "temp"
    assert comfy.outputs_in(None) == [] and comfy.outputs_in({"1": None, "2": {"x": 3}}) == []


async def test_an_input_picture_is_uploaded_and_named_as_comfyui_named_it(mock_http):
    seen = {}

    def upload(request):
        seen["type"] = request.headers["content-type"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"name": "lembas-1 (2).png", "subfolder": "lembas",
                                         "type": "input"})

    mock_http(lambda request: upload(request) if request.url.path == "/upload/image"
              else httpx.Response(404))
    config = comfy.Config(base_url="http://comfy.example.org:8188")
    assert await comfy.upload_image(config, PNG, "lembas-1.png") == "lembas/lembas-1 (2).png"
    assert seen["type"].startswith("multipart/form-data") and PNG in seen["body"]
    assert b'name="image"; filename="lembas-1.png"' in seen["body"]


async def test_a_file_is_never_read_past_its_cap(mock_http):
    mock_http(lambda request: httpx.Response(200, content=b"x" * 5000))
    config = comfy.Config(base_url="http://comfy.example.org:8188")
    with pytest.raises(comfy.ComfyError, match="over the"):
        await comfy.fetch_file(config, comfy.Out("big.mp4"), limit=1000)


# --- Placeholders by kind -------------------------------------------------------------------
def test_the_kinds_values_are_typed_and_clamped_like_a_pictures():
    values = workflow.resolve_kind(
        "video", {"prompt": "x", "seconds": "900", "fps": 500, "mesh_quality": 9999,
                  "image": "in.png"}, max_seconds=15,
    )
    assert values["seconds"] == 15 and values["fps"] == 120 and values["mesh_quality"] == 512
    assert values["frames"] == 1801 and values["image"] == "in.png" and values["lyrics"] == ""
    assert workflow.frames_for(5, 16) == 81 and workflow.frames_for(0.1, 1) == 1
    music = workflow.resolve_kind("music", {"prompt": "slow jazz", "seconds": None})
    assert music["tags"] == "slow jazz" and music["seconds"] == 5
    filled = workflow.fill({"a": "{{seconds}}", "b": "{{tags}}, warm"}, music)
    assert filled == {"a": 5, "b": "slow jazz, warm"}
    assert workflow.resolve({"prompt": "x"})["steps"] == 20  # a picture's are untouched


def test_a_workflows_page_can_say_what_its_kind_needs():
    assert workflow.missing_for("image_edit", {"1": {"inputs": {"t": "{{prompt}}"}}}) == ["image"]
    assert workflow.missing_for("model3d", {"1": {"inputs": {"i": "{{image}}"}}}) == []
    assert set(workflow.REQUIRED) == set(workflow.KINDS) == set(workflow.USABLE)
    assert all(name in workflow.DESCRIPTIONS for name in workflow.ALL_PLACEHOLDERS)


# --- The starters ---------------------------------------------------------------------------
@pytest.mark.parametrize("starter", starters.STARTERS, ids=lambda s: s.key)
def test_a_starter_is_built_on_nodes_and_inputs_comfyui_has(starter):
    template = starter.template()
    assert importer.parse(json.dumps(template)) == template
    assert importer.kind_of(template) == starter.kind
    assert workflow.missing_for(starter.kind, template) == []
    assert workflow.placeholders_in(template) <= set(workflow.USABLE[starter.kind])
    assert starter.files == [name for name, _ in starter.folders] and starter.files
    for node_id, node in template.items():
        required, optional = NODES[node["class_type"]]
        given = set(node["inputs"])
        assert set(required) <= given, f"{starter.key} node {node_id} lacks {set(required) - given}"
        assert given <= set(required) | set(optional), f"{starter.key} node {node_id}"
        for value in node["inputs"].values():
            if isinstance(value, list):
                assert value[0] in template, f"{starter.key} node {node_id} links nowhere"


def test_there_is_a_starter_for_every_kind_beyond_a_picture():
    assert {s.kind for s in starters.STARTERS} == set(store.NAMED)
    assert len(starters.of_kind("video")) == 2  # from text, and from a picture


# --- Import -----------------------------------------------------------------------------------
BASE = json.loads(
    (Path(importer.__file__).parent / "base_workflow.json").read_text(encoding="utf-8")
)
PLAIN = json.loads(
    json.dumps(BASE)
    .replace('"{{seed}}"', "7").replace('"{{steps}}"', "20").replace('"{{cfg}}"', "8.0")
    .replace("{{sampler}}", "euler").replace("{{scheduler}}", "normal")
    .replace('"{{denoise}}"', "1.0").replace("{{model}}", "sd.safetensors")
    .replace('"{{width}}"', "512").replace('"{{height}}"', "512").replace('"{{batch}}"', "1")
    .replace("{{prompt}}", "a castle").replace("{{negative}}", "blurry")
)


def test_an_import_proposes_the_placeholders_and_changes_nothing_itself():
    before = json.dumps(PLAIN)
    proposal = importer.propose(importer.parse(before))
    assert json.dumps(PLAIN) == before
    assert proposal.kind == "image" and not proposal.notes
    assert {c.placeholder for c in proposal.changes} == set(workflow.PLACEHOLDERS)
    # Applying everything gives the shipped template back; applying nothing, the paste.
    everything = {c.key for c in proposal.changes}
    assert importer.apply(PLAIN, proposal.changes, everything) == BASE
    assert importer.apply(PLAIN, proposal.changes, set()) == PLAIN
    only = importer.apply(PLAIN, proposal.changes, {"3:seed"})
    assert only["3"]["inputs"]["seed"] == "{{seed}}" and only["3"]["inputs"]["steps"] == 20


def test_the_prompt_is_the_encoder_the_sampler_s_positive_reaches():
    swapped = json.loads(json.dumps(PLAIN))
    # The encoders in the other order, and a node between one and the sampler.
    swapped["3"]["inputs"]["positive"], swapped["3"]["inputs"]["negative"] = ["20", 0], ["6", 0]
    swapped["20"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["7", 0]}}
    by = {c.key: c.placeholder for c in importer.propose(swapped).changes}
    assert by["7:text"] == "prompt" and by["6:text"] == "negative"


@pytest.mark.parametrize("starter", starters.STARTERS, ids=lambda s: s.key)
def test_an_import_finds_a_kinds_own_holes(starter):
    """A starter with its holes filled in again is what somebody would export."""
    filled = workflow.fill(
        starter.template(),
        {**workflow.resolve_kind(starter.kind, {"prompt": "p", "negative": "n", "tags": "t",
                                                "lyrics": "l", "image": "in.png"}),
         "model": "m"},
    )
    proposal = importer.propose(filled)
    assert proposal.kind == starter.kind
    found = {c.placeholder for c in proposal.changes}
    assert set(workflow.REQUIRED[starter.kind]) <= found, proposal.changes
    assert workflow.placeholders_in(starter.template()) - {"steps", "cfg"} <= found | {
        "sampler", "scheduler", "denoise"}
    assert proposal.files == starter.files


def test_the_interface_format_and_what_is_no_workflow_are_refused_in_a_sentence():
    ui = {"last_node_id": 9, "nodes": [{"id": 1, "type": "KSampler"}], "links": [], "version": 0.4}
    with pytest.raises(importer.ImportError_) as refused:
        importer.parse(json.dumps(ui))
    assert "Export (API)" in refused.value.message
    for bad in ("not json", "[]", "{}", '{"1": {"inputs": {}}}', '{"1": "x"}'):
        with pytest.raises(importer.ImportError_):
            importer.parse(bad)
    assert importer.parse(json.dumps({"prompt": PLAIN, "client_id": "x"})) == PLAIN


def test_the_page_imports_in_two_steps_and_saves_nothing_until_asked(client, db, registered):
    page = client.post("/admin/images/workflows/import", data={"workflow": json.dumps(PLAIN)})
    assert page.status_code == 200 and 'data-testid="import-proposal"' in page.text
    assert page.text.count('name="accept"') == len(workflow.PLACEHOLDERS)
    assert db.scalar(select(ImageWorkflow)) is None
    refused = client.post("/admin/images/workflows/import",
                          data={"workflow": json.dumps({"nodes": [], "links": []})})
    assert 'data-testid="import-error"' in refused.text and "Export (API)" in refused.text
    form = client.post(
        "/admin/images/workflows/import/apply",
        data={"workflow": json.dumps(PLAIN), "kind": "image",
              "accept": ["6:text", "3:seed", "4:ckpt_name"]},
    )
    assert "{{prompt}}" in form.text and 'data-testid="workflow-unverified"' in form.text
    assert "a castle" not in form.text and "blurry" in form.text
    assert db.scalar(select(ImageWorkflow)) is None
    upload = client.post("/admin/images/workflows/import",
                         files={"file": ("wf.json", json.dumps(PLAIN), "application/json")})
    assert 'data-testid="import-proposal"' in upload.text


def test_a_starter_is_added_off_with_its_files_named_and_test_keeps_what_happened(
    client, db, registered, mock_http
):
    connection = Connection(name="Studio", base_url="http://comfy.example.org:8188",
                            provider_type="comfyui", enabled=True)
    db.add(connection)
    db.commit()
    tab = client.get(f"/admin/connections/{connection.id}/workflows")
    assert 'data-testid="workflow-starters"' in tab.text and "starter=music" in tab.text
    draft = client.get("/admin/images/workflows/new", params={"starter": "music"})
    assert "ace_step_v1_3.5b.safetensors" in draft.text and "models/checkpoints/" in draft.text
    assert 'data-testid="workflow-unverified"' in draft.text
    starter = starters.get("music")
    made = client.post("/admin/images/workflows", data={
        "name": starter.name, "slug": "music", "kind": "music", "description": "d",
        "connection_id": connection.id, "workflow": json.dumps(starter.template()),
    }, follow_redirects=False)
    assert made.status_code == 303
    row = db.scalar(select(ImageWorkflow))
    assert row.kind == "music" and not row.enabled and row.last_checked_at is None
    # A kind's required hole is asked for at save.
    lacking = client.post(f"/admin/images/workflows/{row.id}", data={
        "name": "x", "slug": "music", "kind": "model3d",
        "workflow": json.dumps(starter.template())})
    assert "{{image}}" in lacking.text and db.get(ImageWorkflow, row.id).kind == "music"
    page = client.get(f"/admin/images/workflows/{row.id}/edit")
    assert "Not tested against a ComfyUI yet." in page.text

    state = {"fail": True}

    def handle(request):
        path = request.url.path
        if path == "/prompt":
            body = json.loads(request.content)
            assert body["prompt"]["4"]["inputs"]["seconds"] == 10
            return httpx.Response(200, json={"prompt_id": "p1", "node_errors": {}})
        if path.startswith("/history/"):
            if state["fail"]:
                return httpx.Response(200, json={"p1": {"status": {
                    "status_str": "error", "messages": [["execution_error", {
                        "node_type": "CheckpointLoaderSimple",
                        "exception_type": "FileNotFoundError",
                        "exception_message": "ace_step_v1_3.5b.safetensors not found"}]]}}})
            return httpx.Response(200, json={"p1": {
                "status": {"status_str": "success", "messages": []},
                "outputs": OUTPUTS["music"]}})
        if path == "/view":
            return httpx.Response(200, content=MP3)
        return httpx.Response(404)

    mock_http(handle)
    failed = client.post(f"/admin/images/workflows/{row.id}/test")
    assert "It did not pass" in failed.text and "not found" in failed.text
    db.expire_all()
    assert "not found" in db.get(ImageWorkflow, row.id).last_error
    state["fail"] = False
    passed = client.post(f"/admin/images/workflows/{row.id}/test")
    assert "audio/mpeg" in passed.text and "Last test passed." in passed.text
    db.expire_all()
    assert db.get(ImageWorkflow, row.id).last_error == ""


# --- Which source, and which tools -----------------------------------------------------------
def test_a_capability_is_served_by_the_chats_own_then_the_default_and_off_is_off(db, world):
    cloud, comfyui = world["cloud"], world["comfyui"]
    chain = media_sources.chain(db, "video", cloud)
    assert [(s.dialect, s.model, s.own) for s in chain] == [("comfyui", "video_t2v", False)]
    assert chain[0].price == 0.02 and chain[0].usage_id == "comfyui/video_t2v"
    cloud.media_json = dict(cloud.media_json, prefer_image=True)
    db.commit()
    chain = media_sources.chain(db, "video", cloud)
    assert [(s.dialect, s.own) for s in chain] == [("dashscope_video", True), ("comfyui", False)]
    assert media_sources.chain(db, "music", cloud)[0].model == "music"
    settings_store.update(db, {"video_enabled": False}, key=settings_store.IMAGES)
    assert media_sources.chain(db, "video", cloud) == []
    # A default that names a workflow of another kind, or one switched off, serves nothing.
    settings_store.update(db, {"video_enabled": True, "video_default": f"{comfyui.id}/music"},
                          key=settings_store.IMAGES)
    cloud.media_json = dict(cloud.media_json, prefer_image=False)
    db.commit()
    assert media_sources.chain(db, "video", cloud) == []
    assert media_sources.stale(db, "video") == f"{comfyui.id}/music"
    # What a default can be chosen from: the ComfyUI's workflows of the kind, then models.
    values = [c["value"] for c in media_sources.choices(db, "video")]
    assert values == [f"{comfyui.id}/video_t2v", f"{cloud.id}/wan2.7-t2v"]
    assert media_sources.choices(db, "music") == [
        {"value": f"{comfyui.id}/music", "label": "Studio · " + starters.get("music").name,
         "connection_id": comfyui.id, "model": "music"}]


def test_only_what_this_server_can_ask_for_is_a_capability_of_a_connection():
    assert store.capabilities_of("comfyui") == ("image",)
    assert store.capabilities_of("speech") == ("stt", "tts")
    assert store.capabilities_of("openai") == ("stt", "tts", "image", "image_edit")
    assert store.capabilities_of("alibaba") == ("stt", "tts", "image", "image_edit", "video")
    assert store.capabilities_of("anthropic") == ()
    assert store.dialect("openai", "", "video", "x") == ""
    assert store.dialect("alibaba", "", "music", "x") == ""


def _named(db, world) -> set[str]:
    world["chat"] = db.get(Chat, world["chat"].id)
    return set(tools_service.resolve_tools(db, world["chat"], world["user"]).by_name) & {
        "image_generate", "image_edit", "video_generate", "music_generate", "model_generate"}


def test_each_tool_is_offered_only_when_its_capability_has_a_source(db, world):
    assert _named(db, world) == {"video_generate", "music_generate", "model_generate"}
    settings_store.update(db, {"music_enabled": False, "model3d_default": ""},
                          key=settings_store.IMAGES)
    assert _named(db, world) == {"video_generate"}
    settings_store.update(db, {"video_enabled": False}, key=settings_store.IMAGES)
    assert _named(db, world) == set()
    book = tools_service.registry(db)
    assert {book[n].family for n in tools.NAMES.values()} == {"image:media"}
    assert tools_service.gate_of(book["video_generate"].family) == "image"


def test_a_tool_says_that_it_answers_later_and_what_it_costs(db, world):
    world["cloud"].media_json = dict(world["cloud"].media_json, prefer_image=True)
    world["cloud"].currency = "USD"
    db.commit()
    offered = tools_service.resolve_tools(db, world["chat"], world["user"]).by_name
    described = offered["video_generate"].description
    assert "answers at once with a job id" in described
    assert "0.1 USD per second of video" in described
    assert "answers at once" not in tools.tool_def("image_edit").description


def test_the_guidance_reaches_the_model_and_forbids_a_second_paid_try(db, world):
    from lembas.services import harness

    offered = tools_service.resolve_tools(db, world["chat"], world["user"])
    schemas = [tool.schema for tool in offered.by_name.values()]
    assert "image:media" in harness._families(db, schemas)
    from lembas.services import prompts

    fragment = next(f for f in prompts.BUILTIN if f.key == "tool.media")
    assert "never start it again" in fragment.default and "end your reply" in fragment.default
    picture = next(f for f in prompts.BUILTIN if f.key == "tool.image")
    assert "paid for per attempt" in picture.default


def test_a_scheduled_run_on_a_plan_is_offered_no_video(db, world, monkeypatch):
    from lembas.services import interactive

    world["cloud"].provider_type = "alibaba_plan"
    world["cloud"].base_url = "https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1"
    world["cloud"].media_json = {"prefer_image": True, "models": [
        {"capability": "video", "model": "wan3.0-video", "enabled": True}]}
    settings_store.update(db, {"video_default": "", "music_enabled": False,
                               "model3d_enabled": False}, key=settings_store.IMAGES)
    db.commit()
    assert [s.model for s in media_sources.for_chat(db, "video", world["chat"])] == [
        "wan3.0-video"]
    monkeypatch.setattr(interactive, "scheduled_work", lambda db, chat: True)
    assert media_sources.for_chat(db, "video", world["chat"]) == []


# --- An edited picture, in the call -------------------------------------------------------
def _edit_on(db, world, **entry):
    world["cloud"].media_json = {"prefer_image": True, "models": [
        {"capability": "image_edit", "model": "qwen-image-2.0", "enabled": True, "price": 0.035,
         **entry}]}
    settings_store.update(db, {"image_edit_enabled": True}, key=settings_store.IMAGES)
    db.commit()


def _attach(db, world, name="photo.png"):
    from lembas.services import files as files_service

    return files_service.store(db, user_id=world["user"].id, chat_id=world["chat"].id,
                               payload=PNG, filename=name, keep_original=True)


def test_a_picture_is_edited_by_the_providers_model_and_lands_in_both_places(
    db, world, monkeypatch
):
    _edit_on(db, world)
    _attach(db, world)
    seen = {}
    real = httpx.AsyncClient

    def handle(request):
        if request.url.host == "files.example.org":
            return httpx.Response(200, content=PNG)
        seen["path"], seen["body"] = request.url.path, json.loads(request.content)
        return httpx.Response(200, json={"output": {"choices": [{"message": {"content": [
            {"image": "https://files.example.org/edited.png"}]}}]}})

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    from lembas.services import fetch as fetch_service

    monkeypatch.setattr(fetch_service, "check_url", lambda url, **_: url)
    outcome = run(tools.run_edit(world["context"], {"prompt": "make it dusk"}))
    assert outcome.event["status"] == "ok", outcome.content
    assert seen["path"] == "/api/v1/services/aigc/multimodal-generation/generation"
    content = seen["body"]["input"]["messages"][0]["content"]
    assert content[0]["image"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert content[1] == {"text": "make it dusk"}
    media = outcome.event["media"]
    home = fs.ensure_home(db, world["user"]).path
    assert media["kind"] == "image_edit" and media["path"].startswith(f"{home}/Generated/edit-")
    assert fs.read(fs.view(db, world["user"]), media["path"]) == PNG
    # And as the bubble's picture, as a drawn one is.
    assert outcome.event["image"]["id"] == outcome.event["attachment_id"]
    assert "photo.png" in outcome.content and media["path"] in outcome.content


def test_openais_edit_is_the_multipart_form(db, world, monkeypatch):
    world["cloud"].provider_type = "openai"
    world["cloud"].base_url = "https://api.example.org/v1"
    _edit_on(db, world)
    _attach(db, world, "a.png")
    _attach(db, world, "b.png")
    seen = {}
    real = httpx.AsyncClient

    def handle(request):
        seen["path"], seen["type"] = request.url.path, request.headers["content-type"]
        seen["body"] = request.read()
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG).decode()}]})

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    outcome = run(tools.run_edit(world["context"],
                                 {"prompt": "merge them", "image": "a.png", "image2": "b.png"}))
    assert outcome.event["status"] == "ok", outcome.content
    assert seen["path"] == "/v1/images/edits" and seen["type"].startswith("multipart/form-data")
    assert seen["body"].count(b'name="image[]"') == 2 and b'name="prompt"' in seen["body"]
    assert b"qwen-image-2.0" in seen["body"]


def test_a_failed_edit_tells_the_model_to_stop_and_nothing_is_kept(db, world, monkeypatch):
    _edit_on(db, world)
    _attach(db, world)
    real = httpx.AsyncClient
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(400, json={"code": "DataInspectionFailed", "message": "refused"})

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    outcome = run(tools.run_edit(world["context"], {"prompt": "make it dusk"}))
    assert outcome.event["status"] == "error" and len(calls) == 1
    assert "refused" in outcome.content and "Do not try again" in outcome.content
    home = fs.ensure_home(db, world["user"]).path
    assert fs.lookup(db, world["user"].org_id, f"{home}/Generated") is None


def test_a_picture_is_named_by_attachment_by_path_or_is_the_last_one(db, world):
    _attach(db, world, "first.png")
    _attach(db, world, "second.png")
    home = fs.ensure_home(db, world["user"]).path
    fs.write(fs.view(db, world["user"]), f"{home}/pics/tree.png", PNG)
    fs.write(fs.view(db, world["user"]), f"{home}/notes.md", b"# words")
    db.commit()
    context = world["context"]
    assert tools.picture_named(context, "")[1] == "second.png"
    assert tools.picture_named(context, "FIRST.png")[1] == "first.png"
    assert tools.picture_named(context, f"{home}/pics/tree.png") == (PNG, "tree.png")
    for bad in (f"{home}/notes.md", f"{home}/nothing.png", "/etc/passwd"):
        with pytest.raises(tools.NoPicture):
            tools.picture_named(context, bad)


# --- image_generate: the same guard, and Files -------------------------------------------------
def test_a_drawn_picture_is_also_in_files_and_a_providers_failure_forbids_a_retry(
    db, world, monkeypatch
):
    world["cloud"].media_json = {"prefer_image": True, "models": [
        {"capability": "image", "model": "qwen-image-3.0", "enabled": True}]}
    settings_store.update(db, {"enabled": True}, key=settings_store.IMAGES)
    db.commit()
    real = httpx.AsyncClient
    state = {"fail": False}

    def handle(request):
        if state["fail"]:
            return httpx.Response(500, json={"code": "InternalError", "message": "busy"})
        return httpx.Response(200, json={"data": [{"b64_json": "x"}], "output": {"choices": [
            {"message": {"content": [{"image": "https://files.example.org/p.png"}]}}]}}) if (
            request.url.host != "files.example.org") else httpx.Response(200, content=PNG)

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handle), **kw))
    from lembas.services import fetch as fetch_service

    monkeypatch.setattr(fetch_service, "check_url", lambda url, **_: url)
    outcome = run(image_tool.run(world["context"], {"prompt": "a kite"}))
    assert outcome.event["status"] == "ok", outcome.content
    media = outcome.event["media"]
    assert media["kind"] == "image" and "/Generated/image-" in media["path"]
    assert outcome.event["image"]["id"]  # the attachment, as before
    state["fail"] = True
    failed = run(image_tool.run(world["context"], {"prompt": "a kite"}))
    assert failed.event["status"] == "error"
    assert "Do not call image_generate again" in failed.content


def test_an_answer_that_could_not_be_read_is_not_called_unreachable():
    endpoint = openai_client.Endpoint(
        base_url="https://api.example.org/v1", api_key="k", extra_headers={}
    )
    unread = openai_client.wrap_transport_error(
        httpx.DecodingError("Error -3 while decompressing data: incorrect header check"), endpoint
    )
    assert "Could not reach" not in unread.message and "could not be read" in unread.message
    assert not getattr(unread, "unreachable", False)
    refused = openai_client.wrap_transport_error(httpx.ConnectError("refused"), endpoint)
    assert "Could not reach" in refused.message and refused.unreachable
    config = comfy.Config(base_url="http://comfy.example.org:8188")
    assert "could not be read" in comfy._transport_error(httpx.DecodingError("bad"), config).message


# --- Where results go ------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("kind", "payload", "hint", "ext", "mime"),
    [("video", MP4, "", "mp4", "video/mp4"), ("music", MP3, "mp3", "mp3", "audio/mpeg"),
     ("model3d", GLB, "glb", "glb", "model/gltf-binary"), ("image", PNG, "jpg", "png", "image/png"),
     ("model3d", b"solid cube\nfacet normal 0 0 0\n", "stl", "stl", "model/stl")],
)
def test_a_generated_file_is_named_by_what_its_bytes_are(kind, payload, hint, ext, mime):
    assert library.sniff(kind, payload, hint) == (ext, mime)


@pytest.mark.parametrize(
    ("kind", "payload", "hint"),
    [("video", b"<html></html>", "mp4"), ("video", PNG, "mp4"), ("music", MP4, "mp3"),
     ("model3d", b"MZ\x90\x00" + b"\x00" * 60, "glb"), ("image", b"#!/bin/sh\n", "png")],
)
def test_what_is_not_the_kind_is_refused_whatever_it_is_called(kind, payload, hint):
    with pytest.raises(openai_client.LLMError):
        library.sniff(kind, payload, hint)


def test_admin_media_has_a_tab_per_capability_and_saves_its_limits(client, db, world):
    for capability in store.NAMED:
        page = client.get(f"/admin/media/{capability}")
        assert page.status_code == 200 and 'data-testid="media-limits"' in page.text
        assert page.text.count('aria-current="page"') == 1
    page = client.get("/admin/media/video").text
    assert f'value="{world["comfyui"].id}/video_t2v" selected' in page
    saved = client.post("/admin/media/video", data={
        "enabled": "true", "default": f"{world['cloud'].id}/wan2.7-t2v", "price": "0.05",
        "video_max_seconds": "999", "video_max_mb": "64", "jobs_per_person": "3",
        "job_minutes": "1"}, follow_redirects=False)
    assert saved.status_code == 303
    values = settings_store.images(db)
    assert values["video_default"] == f"{world['cloud'].id}/wan2.7-t2v"
    assert (values["video_max_seconds"], values["video_max_mb"]) == (60, 64)
    assert (values["jobs_per_person"], values["job_minutes"], values["video_price"]) == (3, 2, 0.05)
    assert media_sources.default(db, "video").dialect == "dashscope_video"
    client.post("/admin/media/music", data={"default": "none"})
    assert settings_store.images(db)["music_enabled"] is False
    assert media_sources.default(db, "music") is None


def test_a_job_and_a_result_reach_the_page_in_the_tool_block(client, db, world, far, woken):
    from lembas.services.media import jobs

    run(tools.run_video(world["context"], {"prompt": "a kite"}))
    job = db.scalars(select(MediaJob)).first()
    far.finish(job.provider_ref, "images", "v.mp4", MP4)
    asyncio.new_event_loop().run_until_complete(jobs.tick(everything=True))
    db.expire_all()
    result = db.get(MediaJob, job.id).result_json
    from lembas.web.templating import templates

    macro = templates.env.get_template("chat/_tool_activity.html")
    assert macro is not None
    answer = Message(chat_id=world["chat"].id, role="assistant", content="Started.",
                     tool_calls_json=[
                         {"name": "video_generate", "kind": "image", "status": "ok",
                          "media_job": {"id": job.id, "kind": "video", "state": "running"}},
                         {"name": "model_generate", "kind": "image", "status": "ok",
                          "media": {**result, "kind": "model3d", "name": "chair.glb"}},
                     ])
    db.add(answer)
    db.commit()
    page = client.get(f"/chat/{world['chat'].id}").text
    # The job's card as it stands, on the bubble: done, so the player and no trigger.
    assert f'id="media-job-{job.id}"' in page and 'data-state="done"' in page
    assert f'src="/files/raw?node={result["node_id"]}&amp;inline=1"' in page
    assert f'hx-get="/media/jobs/{job.id}/card"' not in page
    assert 'data-sealed-view="model3d"' in page and 'sandbox="allow-scripts"' in page
    assert f'data-raw="/files/raw?node={result["node_id"]}"' in page
    assert 'data-format="glb"' in page



# --- Sound and video as attachments -----------------------------------------------------------
def test_a_chat_can_carry_sound_and_video_which_are_played_and_never_sent_to_a_model(
    client, db, world
):
    from lembas.services import chat as chat_service
    from lembas.services import files as files_service

    song = files_service.store(db, user_id=world["user"].id, chat_id=world["chat"].id,
                               payload=MP3, filename="song.txt")
    clip = files_service.store(db, user_id=world["user"].id, chat_id=world["chat"].id,
                               payload=MP4, filename="clip.mp4")
    # By their bytes, whatever they were called.
    assert (song.kind, song.media_type) == ("audio", "audio/mpeg")
    assert (clip.kind, clip.media_type) == ("video", "video/mp4")
    assert not song.extracted_text and not clip.extracted_text and not clip.is_image
    played = client.get(f"/api/files/{clip.id}/content", headers={"Range": "bytes=0-7"})
    assert played.status_code == 206 and played.headers["content-type"] == "video/mp4"
    assert played.headers["content-disposition"].startswith("inline")
    assert played.headers["x-content-type-options"] == "nosniff"
    message = db.get(Message, world["message"].id)
    song.message_id = clip.message_id = message.id
    db.commit()
    db.refresh(message)
    assert chat_service.document_context(message) == ""
    page = client.get(f"/chat/{world['chat'].id}").text
    assert '<video class="attachments__video" controls' in page
    assert f'src="/api/files/{song.id}/content"' in page and "<audio" in page
    with pytest.raises(files_service.FileError):
        files_service.prepare(b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200, "tool.exe")


def test_a_file_asked_for_by_id_is_refused_without_naming_its_path(client, db, world):
    """Another person's generated file: not served, and the refusal says the
    id that was asked for, never where the file is."""
    from lembas.db.models import User
    from lembas.security import api_tokens

    other = User(email="sam@shire.test", name="Sam", password_hash="x", role="admin",
                 org_id=world["user"].org_id)
    db.add(other)
    db.commit()
    theirs = library.keep(db, world["user"], kind="video", payload=MP4)
    db.commit()
    _, raw = api_tokens.create(db, other, name="LLeMbas for Android", scopes=["app"])
    refused = client.get("/api/v2/files/raw", params={"node": theirs["node_id"]},
                         headers={"Authorization": f"Bearer {raw}"})
    assert refused.status_code == 404, refused.text
    assert theirs["node_id"] in refused.json()["detail"]
    assert "Generated" not in refused.text and "frodo" not in refused.text.lower()
