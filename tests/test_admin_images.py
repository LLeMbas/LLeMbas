"""The image generation admin page, and the workflows behind it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import lembas
from lembas.db.models import Connection, ImageWorkflow
from lembas.services import settings_store
from lembas.services.crypto import UNCHANGED_SENTINEL, decrypt
from lembas.services.media import store as media_store
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


COMFY_URL = "http://comfy.test:8188"


def _comfyui(db, **fields) -> Connection:
    """The instance's ComfyUI: a connection, with its checkpoints as models."""
    fields.setdefault("base_url", COMFY_URL)
    row = Connection(name="ComfyUI", provider_type="comfyui", **fields)
    row.media_json = media_store.normalise({"models": [
        {"capability": "image", "model": name, "enabled": True}
        for name in ("sd.safetensors", "other.safetensors")
    ]})
    db.add(row)
    db.commit()
    return row


def _overview(client, connection, **overrides):
    """The connection's Overview form, as its page posts it."""
    data = {
        "name": connection.name,
        "provider_type": "comfyui",
        "base_url": connection.base_url,
        "api_key": UNCHANGED_SENTINEL if connection.api_key_encrypted else "",
        "enabled": "true",
        "options_form": "1",
        "option_timeout": "600",
    }
    data.update(overrides)
    return client.post(f"/admin/connections/{connection.id}", data=data, follow_redirects=False)


def _settings(client, **overrides):
    """Media › Image generation, as its page posts it."""
    data = {
        "enabled": "true",
        "review_enabled": "true",
        "review_model_id": "",
        "max_tries": "4",
        "instructions": "",
    }
    data.update(overrides)
    return client.post("/admin/media/image", data=data, follow_redirects=False)


# --- The page ------------------------------------------------------------------
def test_the_page_renders_and_is_in_the_nav(admin):
    # The old address leads to the page that took its place.
    moved = admin.get("/admin/images", follow_redirects=False)
    assert moved.status_code == 303 and moved.headers["location"] == "/admin/media/image"
    page = admin.get("/admin/media/image").text
    assert 'href="/admin/media"' in page
    assert "ComfyUI" in page


def test_settings_are_saved(admin, db):
    comfy = _comfyui(db)
    assert _settings(admin, default=f"{comfy.id}/").status_code == 303

    values = settings_store.images(db)
    assert values["enabled"] is True
    assert values["base_url"] == "http://comfy.test:8188"
    assert values["checkpoints"] == ["sd.safetensors", "other.safetensors"]
    assert values["review_enabled"] is True


def test_a_trailing_slash_is_stripped_from_the_url(admin, db):
    comfy = _comfyui(db)
    assert _overview(admin, comfy, base_url="http://comfy.test:8188/").status_code == 303
    db.expire_all()
    assert db.get(Connection, comfy.id).base_url == "http://comfy.test:8188"
    _settings(admin, default=f"{comfy.id}/")
    assert settings_store.images(db)["base_url"] == "http://comfy.test:8188"


def test_an_absent_checkbox_is_off(admin, db):
    """An unticked box is simply missing from a form post -- that absence *is*
    the off signal."""
    comfy = _comfyui(db, options_json={"preserve_vram": True})
    settings_store.update(db, {"enabled": True, "review_enabled": True},
                          key=settings_store.IMAGES)
    assert media_store.type_options(comfy)["preserve_vram"] is True
    admin.post("/admin/media/image", data={"max_tries": "4"}, follow_redirects=False)
    values = settings_store.images(db)
    assert values["enabled"] is False
    assert values["review_enabled"] is False
    _overview(admin, comfy)
    db.expire_all()
    assert media_store.type_options(db.get(Connection, comfy.id))["preserve_vram"] is False


def test_the_numbers_are_clamped(admin, db):
    comfy = _comfyui(db)
    _settings(admin, max_tries="500", default=f"{comfy.id}/")
    _overview(admin, comfy, option_timeout="1")
    db.expire_all()
    values = settings_store.images(db)
    assert values["max_tries"] == 10
    assert values["timeout"] == 10.0


def test_max_tries_never_reaches_zero(admin, db):
    """Zero would mean the tool generates nothing and reports success. Unlike
    the zeroes elsewhere in the settings, there is no reading of it anybody
    wants."""
    _settings(admin, max_tries="0")
    assert settings_store.images(db)["max_tries"] == 1


def _key(db, connection) -> str:
    db.expire_all()
    return decrypt(db.get(Connection, connection.id).api_key_encrypted)


def test_the_key_survives_a_save_that_did_not_touch_it(admin, db):
    comfy = _comfyui(db)
    _overview(admin, comfy, api_key="secret-key")
    assert _key(db, comfy) == "secret-key"

    _overview(admin, comfy, api_key=UNCHANGED_SENTINEL, name="Something else")
    assert _key(db, comfy) == "secret-key"
    assert db.get(Connection, comfy.id).name == "Something else"


def test_an_emptied_key_is_removed(admin, db):
    comfy = _comfyui(db)
    _overview(admin, comfy, api_key="secret-key")
    assert _key(db, comfy) == "secret-key"
    _overview(admin, comfy, api_key="")
    db.expire_all()
    assert db.get(Connection, comfy.id).api_key_encrypted == ""


def test_the_key_is_never_rendered(admin, db):
    comfy = _comfyui(db)
    _overview(admin, comfy, api_key="secret-key")
    _settings(admin, default=f"{comfy.id}/")
    assert _key(db, comfy) == "secret-key"
    for path in ("/admin/media/image", "/admin/connections", f"/admin/connections/{comfy.id}",
                 f"/admin/connections/{comfy.id}/models"):
        page = admin.get(path)
        assert page.status_code == 200 and "secret-key" not in page.text, path


def test_a_comfyui_cannot_be_left_without_a_url(admin, db):
    """There is no testing "without a URL" any more: a ComfyUI is a
    connection, and a connection is never saved without its address."""
    comfy = _comfyui(db)
    assert _overview(admin, comfy, base_url="").status_code in (400, 422)
    db.expire_all()
    assert db.get(Connection, comfy.id).base_url == COMFY_URL


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

    assert client.get("/admin/media/image").status_code in (302, 303, 403, 404)
    assert client.post("/admin/images/workflows", data={}).status_code in (302, 303, 403, 404)


# --- The defaults card ----------------------------------------------------------
def test_the_defaults_are_saved_and_clamped(client, db, registered):
    """Clamped at the save as well as on the way out. A number stored by an
    earlier version, or written straight into the settings row, still has to be
    safe when a generation reads it."""
    from lembas.services import settings_store

    client.post(
        "/admin/media/image",
        data={
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

    saved = client.post("/admin/media/image", data={"instructions": "x"},
                        follow_redirects=False)
    assert saved.status_code == 303

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

    saved = client.post("/admin/media/image", data={"instructions": "x"},
                        follow_redirects=False)
    assert saved.status_code == 303

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

    body = client.get("/admin/media/image").text

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

    body = client.get("/admin/media/image").text

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
