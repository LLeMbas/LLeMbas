"""Generating an image: what is offered, and what the loop does.

The gate tests matter more than they look. Image generation is the first tool
whose availability depends on an instance being *configured* as well as
permitted, and the branch it needs in `_family_allowed` is easy to leave out --
without it the family falls through to the last line and silently requires
`library.use`, which has nothing to do with drawing a picture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import lembas
from lembas.db.models import Chat, Connection, ImageWorkflow, Model, User
from lembas.services import settings_store
from lembas.services import tools as tools_service
from lembas.services.crypto import encrypt
from lembas.services.images import comfy
from lembas.services.images import tool as image_tool
from lembas.services.tools import ToolContext

BASE = json.loads(
    (Path(lembas.__file__).parent / "services/images/base_workflow.json").read_text(
        encoding="utf-8"
    )
)
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\xf6\x178U\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)


@pytest.fixture
def configured(db, user_id):
    """An instance that can draw: a ComfyUI, a checkpoint and a workflow."""
    connection = Connection(name="c", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt(""))
    db.add(connection)
    db.commit()
    model = Model(
        connection_id=connection.id,
        model_id="m",
        capabilities_json={"tools": True, "tool_image": True},
    )
    db.add(model)
    db.add(ImageWorkflow(slug="base", name="Base", description="general", workflow_json=BASE))
    db.commit()
    chat = Chat(user_id=user_id, model_id="m", connection_id=connection.id)
    db.add(chat)
    settings_store.update(
        db,
        {
            "enabled": True,
            "base_url": "http://comfy.test:8188",
            "checkpoints": ["sd.safetensors", "other.safetensors"],
            "review_enabled": False,
        },
        key=settings_store.IMAGES,
    )
    db.commit()
    return chat


def _context(db, user_id, chat) -> ToolContext:
    return tools_service.context_for(db, db.get(User, user_id), chat)


# --- Whether it is offered at all ----------------------------------------------
def test_it_is_offered_once_the_instance_can_draw(db, user_id, configured):
    offered = tools_service.resolve_tools(db, configured, db.get(User, user_id))
    assert "image_generate" in offered.by_name


def test_it_is_withheld_without_a_comfyui(db, user_id, configured):
    settings_store.update(db, {"base_url": ""}, key=settings_store.IMAGES)
    assert (
        "image_generate"
        not in tools_service.resolve_tools(db, configured, db.get(User, user_id)).by_name
    )


def test_it_is_withheld_with_no_checkpoints(db, user_id, configured):
    """A model naming a checkpoint that does not exist gets a refusal from
    ComfyUI and spends a round finding out, so an instance that has listed none
    should not offer the tool -- the same rule `skill_get` follows for an empty
    library."""
    settings_store.update(db, {"checkpoints": []}, key=settings_store.IMAGES)
    assert (
        "image_generate"
        not in tools_service.resolve_tools(db, configured, db.get(User, user_id)).by_name
    )


def test_it_is_withheld_when_the_feature_is_off(db, user_id, configured):
    settings_store.update(db, {"enabled": False}, key=settings_store.IMAGES)
    assert (
        "image_generate"
        not in tools_service.resolve_tools(db, configured, db.get(User, user_id)).by_name
    )


def test_it_is_withheld_without_the_permission(db, user_id, configured):
    user = db.get(User, user_id)
    user.role = "user"  # administrators are given every permission
    settings_store.update(db, {"default_permissions": {"tools.image": False}})
    db.commit()
    assert "image_generate" not in tools_service.resolve_tools(db, configured, user).by_name


def test_it_does_not_need_the_library_permission(db, user_id, configured):
    """The trap the explicit branch in `_family_allowed` exists to avoid: with
    no branch the family falls to the last line and requires `library.use`,
    which is a coincidence of naming rather than a rule."""
    user = db.get(User, user_id)
    user.role = "user"
    settings_store.update(db, {"default_permissions": {"library.use": False}})
    db.commit()
    assert "image_generate" in tools_service.resolve_tools(db, configured, user).by_name


def test_the_model_capability_can_withhold_it(db, user_id, configured):
    model = db.scalar(Model.__table__.select().where(Model.model_id == "m"))
    db.query(Model).filter(Model.model_id == "m").update(
        {"capabilities_json": {"tools": True, "tool_image": False}}
    )
    db.commit()
    assert model is not None
    assert (
        "image_generate"
        not in tools_service.resolve_tools(db, configured, db.get(User, user_id)).by_name
    )


def test_a_chat_can_switch_it_off(db, user_id, configured):
    configured.scope_json = {"families": {"image": False}}
    db.commit()
    assert (
        "image_generate"
        not in tools_service.resolve_tools(db, configured, db.get(User, user_id)).by_name
    )


def test_the_registry_knows_it_so_the_guidance_applies(db):
    """`harness._families` maps an offered name back to a family through
    `registry(db)`. A tool missing from there resolves to no family, and its
    fragment never appears -- the omission that cost custom tools their guidance
    once already."""
    assert tools_service.registry(db)["image_generate"].family == "image"
    assert "image" in tools_service.families(db)


# --- The schema ----------------------------------------------------------------
def test_the_schema_offers_this_instance_s_own_choices(db, user_id, configured):
    schema = image_tool.schema_for(db, settings_store.images(db))
    assert schema["properties"]["model"]["enum"] == ["sd.safetensors", "other.safetensors"]
    assert schema["properties"]["template"]["enum"] == ["base"]
    assert "general" in schema["properties"]["template"]["description"]


def test_only_the_prompt_is_required_and_it_is_first(db, user_id, configured):
    """`tools.parse_arguments` puts the raw string into the first *required*
    parameter when a model emits arguments that are not valid JSON -- common
    with small models. This way that degrades into a prompt rather than a seed.
    """
    schema = image_tool.schema_for(db, settings_store.images(db))
    assert schema["required"] == ["prompt"]
    assert next(iter(schema["properties"])) == "prompt"


def test_the_samplers_are_not_an_enum(db, user_id, configured):
    """Forty-four of them, on every request, forever, to prevent a mistake worth
    one sentence of correction."""
    schema = image_tool.schema_for(db, settings_store.images(db))
    assert "enum" not in schema["properties"]["sampler"]


# --- The loop ------------------------------------------------------------------
class FakeComfy:
    """Stands in for the far side, counting what it was asked to do."""

    def __init__(self):
        self.submits: list[dict] = []
        self.frees = 0

    async def submit(self, config, wf):
        self.submits.append(wf)
        return f"p{len(self.submits)}"

    async def await_images(self, config, prompt_id):
        return [comfy.Ref(f"{prompt_id}.png")]

    async def fetch_image(self, config, ref):
        return PNG

    async def free(self, config):
        self.frees += 1


@pytest.fixture
def fake(monkeypatch):
    stub = FakeComfy()
    for name in ("submit", "await_images", "fetch_image", "free"):
        monkeypatch.setattr(comfy, name, getattr(stub, name))
    return stub


async def test_one_call_produces_one_stored_image(db, user_id, configured, fake):
    from lembas.db.models import Attachment

    outcome = await image_tool.run(
        _context(db, user_id, configured), {"prompt": "a bicycle", "model": "sd.safetensors"}
    )

    assert outcome.event["status"] == "ok"
    attachment = db.get(Attachment, outcome.event["attachment_id"])
    assert attachment is not None
    assert attachment.media_type == "image/png", "kept as ComfyUI made it, not transcoded"
    assert attachment.message_id is None, "the loop binds it, not the runner"
    assert attachment.source_label == "Image generation"
    assert len(fake.submits) == 1


async def test_the_model_is_told_it_is_already_on_screen(db, user_id, configured, fake):
    """Without this the commonest next thing a model does is offer to show you
    the image, which it has no way of doing and which has already happened."""
    outcome = await image_tool.run(_context(db, user_id, configured), {"prompt": "a bicycle"})
    assert "already" in outcome.content or "is shown to them" in outcome.content


async def test_a_prompt_is_required(db, user_id, configured, fake):
    outcome = await image_tool.run(_context(db, user_id, configured), {"prompt": "   "})
    assert outcome.event["status"] == "error"
    assert not fake.submits


async def test_an_unknown_checkpoint_falls_back_rather_than_failing(db, user_id, configured, fake):
    """It would reach ComfyUI, be refused, and cost a round to discover -- and
    the model was shown the list it may choose from."""
    await image_tool.run(
        _context(db, user_id, configured), {"prompt": "x", "model": "invented.safetensors"}
    )
    assert fake.submits[0]["4"]["inputs"]["ckpt_name"] == "sd.safetensors"


async def test_the_chat_s_own_preference_is_used_when_the_model_names_none(
    db, user_id, configured, fake
):
    configured.image_checkpoint = "other.safetensors"
    db.commit()
    await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})
    assert fake.submits[0]["4"]["inputs"]["ckpt_name"] == "other.safetensors"


async def test_a_failure_out_there_is_reported_not_raised(
    db, user_id, configured, fake, monkeypatch
):
    async def refuse(config, wf):
        raise comfy.ComfyError("ComfyUI refused the workflow — node 4")

    monkeypatch.setattr(comfy, "submit", refuse)
    outcome = await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})
    assert outcome.event["status"] == "error"
    assert "node 4" in outcome.event["error"]


# --- Reviewing -----------------------------------------------------------------
def _with_review(db, chat, *, tries=3, vision=True):
    db.query(Model).filter(Model.model_id == "m").update(
        {"capabilities_json": {"tools": True, "tool_image": True, "vision": vision}}
    )
    settings_store.update(
        db, {"review_enabled": True, "max_tries": tries}, key=settings_store.IMAGES
    )
    db.commit()


async def test_a_kept_image_stops_the_loop(db, user_id, configured, fake, monkeypatch):
    _with_review(db, configured)
    monkeypatch.setattr(image_tool, "_review", _verdicts([(True, "")]))

    await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})
    assert len(fake.submits) == 1


async def test_a_rejected_image_is_tried_again(db, user_id, configured, fake, monkeypatch):
    _with_review(db, configured)
    monkeypatch.setattr(image_tool, "_review", _verdicts([(False, "no bicycle"), (True, "")]))

    outcome = await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})
    assert len(fake.submits) == 2
    assert "no bicycle" in outcome.event["text"]
    assert "2 attempts" in outcome.content


async def test_the_ceiling_is_enforced_and_the_last_one_is_kept(
    db, user_id, configured, fake, monkeypatch
):
    """A request always produces a picture. Returning nothing after four
    rejections would be spending a minute of somebody's GPU to say no."""
    _with_review(db, configured, tries=3)
    monkeypatch.setattr(image_tool, "_review", _verdicts([(False, "nope")] * 5))

    outcome = await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})
    assert len(fake.submits) == 3
    assert outcome.event["status"] == "ok"
    assert outcome.event["attachment_id"]


async def test_each_attempt_gets_a_new_seed(db, user_id, configured, fake, monkeypatch):
    """Retrying with the same seed regenerates the same rejected image."""
    _with_review(db, configured, tries=3)
    monkeypatch.setattr(
        image_tool, "_review", _verdicts([(False, "no"), (False, "no"), (True, "")])
    )

    await image_tool.run(_context(db, user_id, configured), {"prompt": "x", "seed": 99})
    seeds = [wf["3"]["inputs"]["seed"] for wf in fake.submits]
    assert seeds[0] == 99, "the seed that was asked for is honoured"
    assert len(set(seeds)) == 3, "and the retries are not the same picture again"


async def test_nothing_is_reviewed_without_a_vision_model(db, user_id, configured, fake):
    """ "You asked for a picture and got an error about vision" is a worse answer
    than a picture."""
    _with_review(db, configured, vision=False)
    outcome = await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})
    assert len(fake.submits) == 1
    assert outcome.event["status"] == "ok"


def _verdicts(sequence):
    calls = {"n": 0}

    async def review(context, endpoint, model_id, prompt, payload):
        index = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        return sequence[index]

    return review


# --- Preserve VRAM -------------------------------------------------------------
async def test_nothing_is_unloaded_by_default(db, user_id, configured, fake, monkeypatch):
    calls = {"n": 0}

    async def unload(context):
        calls["n"] += 1
        return True

    monkeypatch.setattr(image_tool, "_unload_llm", unload)
    await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})
    assert calls["n"] == 0
    assert fake.frees == 0


async def test_preserve_vram_unloads_and_frees(db, user_id, configured, fake, monkeypatch):
    settings_store.update(db, {"preserve_vram": True}, key=settings_store.IMAGES)
    db.commit()
    calls = {"n": 0}

    async def unload(context):
        calls["n"] += 1
        return True

    monkeypatch.setattr(image_tool, "_unload_llm", unload)
    await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})
    assert calls["n"] == 1, "once, before generating"
    assert fake.frees >= 1, "and ComfyUI is asked to let go afterwards"


async def test_a_connection_with_no_unload_url_is_never_called(db, user_id, configured):
    """The whole of the per-connection design: a chat talking to a box on the
    network must not have that box unloaded, because its memory is not the
    memory ComfyUI wants."""
    context = _context(db, user_id, configured)
    assert await image_tool._unload_llm(context) is False


async def test_an_unload_that_fails_does_not_stop_the_generation(
    db, user_id, configured, fake, monkeypatch
):
    """It is a hint before a slow operation. A machine that will not answer it is
    one where the picture should still be drawn."""
    connection = db.scalar(Connection.__table__.select())
    db.query(Connection).update({"unload_url": "http://127.0.0.1:9/unload"})
    settings_store.update(db, {"preserve_vram": True}, key=settings_store.IMAGES)
    db.commit()
    assert connection is not None

    outcome = await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})
    assert outcome.event["status"] == "ok"


# --- What the model is told when it fails --------------------------------------
async def test_running_out_of_memory_tells_the_model_what_to_do(
    db, user_id, configured, fake, monkeypatch
):
    """A bare "out of memory" gets the same request sent again, which fails the
    same way. The numbers are concrete because "use a lower resolution" against
    a request that was already 512x512 is advice nobody can follow."""

    async def oom(config, wf):
        raise comfy.OutOfMemory("ComfyUI ran out of video memory in KSampler.")

    monkeypatch.setattr(comfy, "submit", oom)
    outcome = await image_tool.run(
        _context(db, user_id, configured), {"prompt": "x", "width": 1024, "height": 1024}
    )

    assert outcome.event["status"] == "error"
    assert "512x512" in outcome.content, "a size it can actually try"
    assert "1024x1024" in outcome.content, "and what it just asked for"
    assert "lighter checkpoint" in outcome.content
    assert "unchanged" in outcome.content


async def test_a_cancelled_generation_is_not_retried_blindly(
    db, user_id, configured, fake, monkeypatch
):
    """Somebody pressed stop. Starting it again is arguing with them."""

    async def stopped(config, wf):
        raise comfy.Interrupted("The image was cancelled on the ComfyUI side.")

    monkeypatch.setattr(comfy, "submit", stopped)
    outcome = await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})

    assert "do not simply start it again" in outcome.content


async def test_an_ordinary_failure_gets_no_invented_advice(
    db, user_id, configured, fake, monkeypatch
):
    """A model told to "try again" after a broken workflow tries the identical
    thing, and a suggestion invented for a failure nobody understands is a guess
    wearing the application's authority."""

    async def broken(config, wf):
        raise comfy.ComfyError("ComfyUI could not finish the workflow in VAEDecode.")

    monkeypatch.setattr(comfy, "submit", broken)
    outcome = await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})

    assert "VAEDecode" in outcome.content
    assert "smaller size" not in outcome.content
    assert "Try once more" not in outcome.content


async def test_preserve_vram_frees_comfyui_even_when_it_failed(
    db, user_id, configured, fake, monkeypatch
):
    """It failed *inside* the far side, so its models are still resident and the
    language model is still unloaded. Without this the reply cannot even get far
    enough to say what happened."""
    settings_store.update(db, {"preserve_vram": True}, key=settings_store.IMAGES)
    db.commit()

    async def oom(config, wf):
        raise comfy.OutOfMemory("out of video memory")

    monkeypatch.setattr(comfy, "submit", oom)
    monkeypatch.setattr(image_tool, "_unload_llm", _noop)

    await image_tool.run(_context(db, user_id, configured), {"prompt": "x"})
    assert fake.frees >= 1


async def _noop(context):
    return True


# --- Telling a model how to use the thing --------------------------------------
def test_every_parameter_says_when_to_move_it(db, user_id, configured):
    """ "cfg: prompt adherence, default 8" tells a model nothing it can act on,
    and the observable result is a model that sends the prompt alone and leaves
    ten parameters at their defaults for ever."""
    schema = image_tool.schema_for(db, settings_store.images(db))
    for name in ("steps", "cfg", "width", "height", "sampler", "scheduler", "denoise", "negative"):
        description = schema["properties"][name]["description"]
        assert len(description) > 80, f"{name} is described too thinly to act on"

    assert "never" in schema["properties"]["negative"]["description"].lower(), (
        "the negative prompt's one real trap: phrasing it as an instruction"
    )
    assert "portrait" in schema["properties"]["width"]["description"]


def test_the_schema_states_this_instance_s_defaults(db, registered):
    """Every "Default 20." in those descriptions was written when there was one
    set of defaults in the world. A schema still saying "Default 512" beside an
    instance that draws at 1024 is worse than saying nothing: the model reasons
    from it and omits the parameter, arriving at the right behaviour for the
    wrong reason or the wrong one silently."""
    from lembas.services.images import tool as image_tool

    schema = image_tool.schema_for(
        db, {"default_width": 1024, "default_steps": 30, "default_sampler": "dpmpp_2m"}
    )
    properties = schema["properties"]

    assert "Default 1024." in properties["width"]["description"]
    assert "Default 30." in properties["steps"]["description"]
    assert "Default dpmpp_2m." in properties["sampler"]["description"]
    # And the rest of the sentence survives -- these say what to do *instead* of
    # the default, which is most of their value.
    assert "SDXL" in properties["width"]["description"]


def test_a_whole_number_default_is_not_written_as_a_decimal(db, registered):
    """"Default 8.0" is text a model reasons about, and reads as a precision
    somebody chose."""
    from lembas.services.images import tool as image_tool

    schema = image_tool.schema_for(db, {"default_cfg": 6})

    assert "Default 6." in schema["properties"]["cfg"]["description"]
    assert "Default 6.0" not in schema["properties"]["cfg"]["description"]


def test_the_punctuation_after_a_default_is_kept(db, registered):
    """`denoise` says "Default 1, which is what you want…" while the rest use a
    full stop. A rewrite that assumed one would produce a sentence that does not
    read."""
    from lembas.services.images import tool as image_tool

    schema = image_tool.schema_for(db, {})

    assert "Default 1, which is" in schema["properties"]["denoise"]["description"]
