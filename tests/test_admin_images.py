"""The image generation admin page, and the workflows behind it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import lembas
from lembas.db.models import ImageWorkflow
from lembas.services import settings_store
from lembas.services.crypto import UNCHANGED_SENTINEL, decrypt
from tests.conftest import join

BASE_TEXT = (Path(lembas.__file__).parent / "services/images/base_workflow.json").read_text(
    encoding="utf-8"
)
BASE = json.loads(BASE_TEXT)


@pytest.fixture
def admin(client: TestClient, db, registered):
    from lembas.db.models import User

    db.query(User).update({"role": "admin"})
    db.commit()
    return client


def _settings(client, **overrides):
    data = {
        "enabled": "true",
        "base_url": "http://comfy.test:8188",
        "api_key": "",
        "timeout": "600",
        "checkpoints": "sd.safetensors\nother.safetensors",
        "default_workflow_id": "",
        "review_enabled": "true",
        "review_model_id": "",
        "max_tries": "4",
        "preserve_vram": "",
        "instructions": "",
    }
    data.update(overrides)
    return client.post("/admin/images", data=data, follow_redirects=False)


# --- The page ------------------------------------------------------------------
def test_the_page_renders_and_is_in_the_nav(admin):
    page = admin.get("/admin/images").text
    assert 'href="/admin/images"' in page
    assert "ComfyUI" in page


def test_settings_are_saved(admin, db):
    assert _settings(admin).status_code == 303

    values = settings_store.images(db)
    assert values["enabled"] is True
    assert values["base_url"] == "http://comfy.test:8188"
    assert values["checkpoints"] == ["sd.safetensors", "other.safetensors"]
    assert values["review_enabled"] is True


def test_a_trailing_slash_is_stripped_from_the_url(admin, db):
    _settings(admin, base_url="http://comfy.test:8188/")
    assert settings_store.images(db)["base_url"] == "http://comfy.test:8188"


def test_an_absent_checkbox_is_off(admin, db):
    """An unticked box is simply missing from a form post -- that absence *is*
    the off signal."""
    data = {"base_url": "http://x", "timeout": "600", "max_tries": "4"}
    admin.post("/admin/images", data=data, follow_redirects=False)
    values = settings_store.images(db)
    assert values["enabled"] is False
    assert values["review_enabled"] is False
    assert values["preserve_vram"] is False


def test_the_numbers_are_clamped(admin, db):
    _settings(admin, max_tries="500", timeout="1")
    values = settings_store.images(db)
    assert values["max_tries"] == 10
    assert values["timeout"] == 10.0


def test_max_tries_never_reaches_zero(admin, db):
    """Zero would mean the tool generates nothing and reports success. Unlike
    the zeroes elsewhere in the settings, there is no reading of it anybody
    wants."""
    _settings(admin, max_tries="0")
    assert settings_store.images(db)["max_tries"] == 1


def test_the_key_survives_a_save_that_did_not_touch_it(admin, db):
    _settings(admin, api_key="secret-key")
    assert decrypt(settings_store.images(db)["api_key_encrypted"]) == "secret-key"

    _settings(admin, api_key=UNCHANGED_SENTINEL, instructions="something else")
    assert decrypt(settings_store.images(db)["api_key_encrypted"]) == "secret-key"


def test_an_emptied_key_is_removed(admin, db):
    _settings(admin, api_key="secret-key")
    _settings(admin, api_key="")
    assert settings_store.images(db)["api_key_encrypted"] == ""


def test_the_key_is_never_rendered(admin, db):
    _settings(admin, api_key="secret-key")
    assert "secret-key" not in admin.get("/admin/images").text


def test_testing_without_a_url_says_so(admin, db):
    _settings(admin, base_url="")
    assert "Set a base URL first" in admin.post("/admin/images/test").text


# --- Workflows -----------------------------------------------------------------
def test_new_is_not_read_as_an_id(admin):
    """FastAPI matches in registration order, so `/workflows/new` has to be
    registered before `/workflows/{id}` or "new" is an id and 404s. This has
    been a bug twice in this codebase."""
    response = admin.get("/admin/images/workflows/new")
    assert response.status_code == 200
    assert "Export (API)" in response.text


def test_the_default_workflow_is_offered_to_start_from(admin):
    """Somebody setting this up for the first time should not have to find a
    working API-format document before they can try anything."""
    page = admin.get("/admin/images/workflows/new").text
    assert "CheckpointLoaderSimple" in page
    assert "{{prompt}}" in page


def test_a_workflow_is_created(admin, db):
    response = admin.post(
        "/admin/images/workflows",
        data={
            "slug": "sdxl",
            "name": "SDXL",
            "description": "photo",
            "workflow": BASE_TEXT,
            "enabled": "true",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    row = db.scalar(select(ImageWorkflow).where(ImageWorkflow.slug == "sdxl"))
    assert row is not None
    assert row.workflow_json["4"]["inputs"]["ckpt_name"] == "{{model}}"


def test_broken_json_is_refused_with_a_sentence(admin, db):
    """A sentence and not a 422: losing forty lines of pasted JSON to a
    validation error is not a thing to do to somebody, so the form comes back
    with what was typed still in it."""
    response = admin.post(
        "/admin/images/workflows",
        data={"slug": "x", "name": "X", "workflow": "{not json"},
    )
    assert response.status_code == 200
    assert "not valid JSON" in response.text
    assert "{not json" in response.text, "and what was typed is still there"
    assert db.scalar(select(ImageWorkflow)) is None


def test_a_workflow_with_no_prompt_placeholder_is_refused(admin, db):
    """It would draw the same picture whatever anybody typed, and would look
    like a broken model rather than an unparameterised template."""
    without = json.loads(BASE_TEXT)
    without["6"]["inputs"]["text"] = "a fixed prompt"
    response = admin.post(
        "/admin/images/workflows",
        data={"slug": "x", "name": "X", "workflow": json.dumps(without)},
    )
    assert "{{prompt}}" in response.text
    assert "every image would be the same" in response.text
    assert db.scalar(select(ImageWorkflow)) is None


def test_an_unknown_placeholder_is_refused(admin, db):
    response = admin.post(
        "/admin/images/workflows",
        data={
            "slug": "x",
            "name": "X",
            "workflow": json.dumps({"1": {"a": "{{prompt}} {{lora}}"}}),
        },
    )
    assert "lora" in response.text
    assert db.scalar(select(ImageWorkflow)) is None


def test_a_ui_format_export_is_refused_helpfully(admin, db):
    """The two ComfyUI formats look alike enough that this is the mistake
    everybody makes first, and "invalid" would not tell them which button to
    press instead."""
    response = admin.post(
        "/admin/images/workflows",
        data={"slug": "x", "name": "X", "workflow": "[]"},
    )
    assert "Export (API)" in response.text


def test_a_duplicate_slug_is_refused(admin, db):
    for _ in range(2):
        response = admin.post(
            "/admin/images/workflows",
            data={"slug": "dup", "name": "Dup", "workflow": BASE_TEXT},
        )
    assert "already a workflow" in response.text
    assert len(list(db.scalars(select(ImageWorkflow)))) == 1


def test_a_rejected_edit_leaves_the_stored_row_alone(admin, db):
    admin.post(
        "/admin/images/workflows",
        data={"slug": "keep", "name": "Keep", "workflow": BASE_TEXT},
    )
    row = db.scalar(select(ImageWorkflow))

    admin.post(
        f"/admin/images/workflows/{row.id}",
        data={"slug": "keep", "name": "Renamed", "workflow": "{broken"},
    )

    db.expire_all()
    row = db.scalar(select(ImageWorkflow))
    assert row.name == "Keep", "validated against a draft, so nothing was written"


def test_a_workflow_is_deleted(admin, db):
    admin.post(
        "/admin/images/workflows",
        data={"slug": "gone", "name": "Gone", "workflow": BASE_TEXT},
    )
    row = db.scalar(select(ImageWorkflow))
    admin.post(f"/admin/images/workflows/{row.id}/delete", follow_redirects=False)

    db.expire_all()
    assert db.scalar(select(ImageWorkflow)) is None


def test_only_an_administrator_can_reach_it(client: TestClient, db, registered):
    """The first account registered is an administrator, so this needs a second
    one -- the `test_admin_tools` fixture's reason for existing."""
    from lembas.db.models import User

    client.post("/auth/logout")
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "correct horse battery"},
        follow_redirects=False,
    )
    user = db.scalar(select(User).where(User.email == "sam@shire.test"))
    user.role = "user"
    user.active = True
    db.commit()
    client.post(
        "/auth/login",
        data={"email": "sam@shire.test", "password": "correct horse battery"},
        follow_redirects=False,
    )

    assert client.get("/admin/images").status_code in (302, 303, 403, 404)
    assert client.post("/admin/images/workflows", data={}).status_code in (302, 303, 403, 404)


# --- The defaults card ----------------------------------------------------------
def test_the_defaults_are_saved_and_clamped(client, db, registered):
    """Clamped at the save as well as on the way out. A number stored by an
    earlier version, or written straight into the settings row, still has to be
    safe when a generation reads it."""
    from lembas.services import settings_store

    client.post(
        "/admin/images",
        data={
            "base_url": "http://comfy.test",
            "api_key": "",
            "timeout": "600",
            "checkpoints": "sd.safetensors",
            "default_steps": "9000",
            "default_width": "1024",
            "default_cfg": "5.5",
            "default_sampler": "dpmpp_2m",
            "default_batch": "4",
        },
        follow_redirects=False,
    )

    values = settings_store.images(db)
    assert values["default_steps"] == 150  # the ceiling, not 9000
    assert values["default_width"] == 1024
    assert values["default_cfg"] == 5.5
    assert values["default_sampler"] == "dpmpp_2m"
    assert values["default_batch"] == 4


def test_an_empty_default_stays_empty_rather_than_becoming_zero(client, db, registered):
    """The empty string is how an administrator says "no opinion", which
    `workflow.resolve` reads as "fall through to the built-in". Read as a number
    it would set the instance to zero steps."""
    from lembas.services import settings_store

    client.post(
        "/admin/images",
        data={"base_url": "http://comfy.test", "api_key": "", "timeout": "600"},
        follow_redirects=False,
    )

    values = settings_store.images(db)
    assert values["default_steps"] == ""
    assert values["default_cfg"] == ""


def test_saving_the_page_does_not_clear_the_discovered_lists(client, db, registered):
    """They belong to whatever ComfyUI was tested, and a save that only changed
    the instructions box has no opinion about them. The sampler picker is built
    from them, so clearing them would empty it."""
    from lembas.services import settings_store

    settings_store.update(
        db, {"samplers": ["euler", "dpmpp_2m"], "schedulers": ["normal"]},
        key=settings_store.IMAGES,
    )
    db.commit()

    client.post(
        "/admin/images",
        data={"base_url": "http://comfy.test", "api_key": "", "timeout": "600"},
        follow_redirects=False,
    )

    values = settings_store.images(db)
    assert values["samplers"] == ["euler", "dpmpp_2m"]


def test_the_sampler_picker_offers_what_comfyui_advertised(client, db, registered):
    """Discovered, stored, and — until now — read by nothing at all. There are
    forty-odd samplers and spelling one wrong is a refused workflow, so it is
    picked rather than typed."""
    from lembas.services import settings_store

    settings_store.update(
        db, {"samplers": ["euler", "dpmpp_2m_sde"], "schedulers": ["karras"]},
        key=settings_store.IMAGES,
    )
    db.commit()

    body = client.get("/admin/images").text

    assert 'name="default_sampler"' in body
    assert "dpmpp_2m_sde" in body
    assert "karras" in body


def test_a_stored_sampler_survives_a_list_that_does_not_have_it(client, db, registered):
    """Otherwise opening the page and saving it silently clears a working
    setting, because the select had no option matching the stored value."""
    from lembas.services import settings_store

    settings_store.update(
        db, {"samplers": ["euler"], "default_sampler": "some_custom_sampler"},
        key=settings_store.IMAGES,
    )
    db.commit()

    body = client.get("/admin/images").text

    assert "some_custom_sampler" in body


def test_the_workflow_editor_says_what_each_placeholder_resolves_to(client, db, registered):
    """A legend listing names answers "what may I write"; the question somebody
    has in front of a workflow that came out wrong is "what happens if I leave
    this out"."""
    from lembas.services import settings_store

    settings_store.update(db, {"default_width": 1024}, key=settings_store.IMAGES)
    db.commit()

    body = client.get("/admin/images/workflows/new").text

    assert "ckpt_name" in body  # the name that is not the placeholder's
    assert "sampler_name" in body
    assert "1024" in body  # the resolved default, beside {{width}}
