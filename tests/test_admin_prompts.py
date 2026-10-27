"""The prompt editor: what it saves, what it refuses to save, and the preview."""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import User
from lembas.services import harness, prompts, settings_store
from lembas.services.library import memories as memories_service
from tests.conftest import join


@pytest.fixture
def plain_user(client: TestClient, db, registered) -> User:
    """A second, non-admin account. Leaves the client signed in as them."""
    client.post("/auth/logout", follow_redirects=False)
    join(client,
        data={"name": "Sam", "email": "sam@shire.test", "password": "potatoes-po-ta-toes"},
        follow_redirects=False,
    )
    return db.scalar(select(User).where(User.email == "sam@shire.test"))


def _owner(db) -> User:
    return db.scalar(select(User).where(User.email == "frodo@shire.test"))


# --- Access ------------------------------------------------------------------
def test_the_page_lists_every_fragment(client: TestClient, registered):
    page = client.get("/admin/prompts").text
    for fragment in prompts.BUILTIN:
        assert f'name="prompt.{fragment.key}"' in page, fragment.key


def test_the_preview_controls_are_inside_what_the_preview_includes(
    client: TestClient, registered
):
    """`hx-include="#prompt-form, #preview-controls"` is the whole wiring, so a
    control placed outside that container is submitted by nothing and changes
    nothing -- with no error, which is this codebase's recurring failure. Assert
    the containment rather than the markup of any one field.
    """
    page = client.get("/admin/prompts").text
    controls = page.split('id="preview-controls"', 1)[1].split("</div>\n  </div>", 1)[0]
    for name in ("preview_model", "preview_bases", "preview_documents",
                 "preview_situation", "preview_mode", "preview_family"):
        assert f'name="{name}"' in controls, name


def test_the_editor_comes_before_the_reference(client: TestClient, registered):
    """The Variables legend and the Preview run to a screen each and used to sit
    above the tabs, so the thing this page exists for started two screens down.
    Every tab switch then had to move the viewport to be any use -- and on a
    short panel the scroller cannot reach the tab bar, so it clamped to the
    bottom and left the panel stranded above a screenful of nothing.

    Asserted as an ordering rather than by reading the CSS, because the position
    is the fix: with the tabs near the top there is nothing to scroll past.
    """
    page = client.get("/admin/prompts").text
    tabs = page.index('class="tabs__bar"')
    assert tabs < page.index("Variables</h2>")
    assert tabs < page.index("Preview</h2>")
    assert tabs < page.index("Tool descriptions</h2>")


def test_the_tab_bar_sticks_to_the_top_of_the_scroller():
    """The Tools panel is longer than a screen, so a bar that scrolls away means
    changing tab is a scroll back up to find it."""
    css = (
        pathlib.Path(__file__).resolve().parents[1]
        / "src/lembas/web/static/css/admin.css"
    ).read_text()
    bar = css.split(".tabs__bar {", 1)[1].split("}", 1)[0]
    assert "position: sticky" in bar
    assert "top: 0" in bar
    # Opaque, or the panel shows through it; stacked, or a panel's own cards
    # paint over it.
    assert "background:" in bar
    assert "z-index:" in bar


def test_the_page_is_refused_to_a_plain_user(client: TestClient, plain_user):
    assert client.get("/admin/prompts").status_code == 403
    assert client.post("/admin/prompts", data={}).status_code == 403


# --- Saving ------------------------------------------------------------------
def test_saving_changes_what_a_model_is_told(client: TestClient, db, registered):
    client.post(
        "/admin/prompts",
        data={"prompt.tool.web_search": "- Always search twice.", "max_harness_chars": "0"},
        follow_redirects=False,
    )
    text = harness.compose(db, _owner(db), _tools("web_search"))
    assert "- Always search twice." in text
    assert "Look things up" not in text


def test_a_cleared_box_turns_the_fragment_off(client: TestClient, db, registered):
    """The trap this page was written around: FastAPI cannot tell an empty form
    field from an absent one, so the handler reads the raw form."""
    client.post(
        "/admin/prompts",
        data={"prompt.core.style": "", "max_harness_chars": "0"},
        follow_redirects=False,
    )
    assert prompts.stored(db) == {"core.style": ""}
    assert "Answer in the language" not in harness.compose(db, _owner(db), [])


def test_a_fragment_not_submitted_at_all_is_left_alone(client: TestClient, db, registered):
    prompts.save(db, {"core.heading": "## Rules"})
    client.post("/admin/prompts", data={"max_harness_chars": "0"}, follow_redirects=False)
    assert prompts.resolve(db, "core.heading") == "## Rules"


def test_saving_the_built_in_wording_stores_nothing(client: TestClient, db, registered):
    """Opening the page and pressing Save must not freeze today's defaults, or a
    later release could never improve them."""
    page_fields = {f"prompt.{f.key}": f.default for f in prompts.BUILTIN}
    client.post(
        "/admin/prompts", data={**page_fields, "max_harness_chars": "0"}, follow_redirects=False
    )
    assert prompts.stored(db) == {}


def test_the_character_cap_is_clamped_and_kept(client: TestClient, db, registered):
    client.post("/admin/prompts", data={"max_harness_chars": "-5"}, follow_redirects=False)
    assert settings_store.get(db, "max_harness_chars", key=settings_store.PROMPTS) == 0

    client.post("/admin/prompts", data={"max_harness_chars": "600"}, follow_redirects=False)
    assert harness.limit_for(db) == 600


# --- Resetting ---------------------------------------------------------------
def test_use_default_fills_the_box_without_saving(client: TestClient, db, registered):
    prompts.save(db, {"core.heading": "## Rules"})
    response = client.post("/admin/prompts/default", data={"key": "core.heading"})

    assert "## How to work" in response.text
    assert "edited" not in response.text
    # Nothing was written: it takes a Save to make it stick.
    assert prompts.resolve(db, "core.heading") == "## Rules"


def test_use_default_on_an_unknown_key_is_a_404(client: TestClient, registered):
    assert client.post("/admin/prompts/default", data={"key": "nope.nope"}).status_code == 404


def test_restore_all_defaults_empties_the_overrides(client: TestClient, db, registered):
    prompts.save(db, {"core.heading": "## Rules", "core.style": ""})
    client.post("/admin/prompts/reset", follow_redirects=False)
    assert prompts.stored(db) == {}


# --- Preview -----------------------------------------------------------------
def test_the_preview_shows_text_that_has_not_been_saved(client: TestClient, db, registered):
    body = client.post(
        "/admin/prompts/preview",
        data={"prompt.core.heading": "## Draft heading", "preview_family": ["web_search"]},
    ).text

    assert "## Draft heading" in body
    assert prompts.stored(db) == {}


def test_the_preview_only_shows_guidance_for_the_families_ticked(client: TestClient, registered):
    body = client.post("/admin/prompts/preview", data={"preview_family": ["notes"]}).text
    assert "You keep notes" in body
    assert "Look things up" not in body


def test_the_preview_escapes_what_a_model_wrote(client: TestClient, db, registered):
    """A memory is model-written text on an admin page. Hard rule 6 applies to
    the preview exactly as it does to a chat bubble."""
    memories_service.add(db, owner=_owner(db), content="<img src=x onerror=alert(1)>")
    body = client.post("/admin/prompts/preview", data={"preview_family": ["memory"]}).text

    assert "<img src=x" not in body
    assert "&lt;img src=x" in body


def test_the_preview_can_reach_the_fragments_that_need_a_chat(client: TestClient, registered):
    """Fragments gated on variables `context_variables` fills only when it is
    handed a real `Chat` -- the scheduling fragments and the helper warning --
    were absent from every preview whatever was ticked, because the preview
    hands it `None`. The samples are what close that.

    (There is no agent family and no agent samples: an agent chat's prompt is
    the linked CLI's, not this harness's.)
    """
    task = client.post(
        "/admin/prompts/preview", data={"preview_situation": "task", "preview_family": []}
    ).text
    assert "every weekday at 08:00" in task

    helper = client.post(
        "/admin/prompts/preview", data={"preview_situation": "helper", "preview_family": []}
    ).text
    assert "buildbox" not in helper
    assert helper != task


def test_the_preview_gates_its_samples_exactly_as_a_real_request_would(
    client: TestClient, registered
):
    """A preview that admitted a fragment the real request would not is worse
    than one that omitted it, so the samples follow the same gates: the agent
    block on the family, the other two on the situation and on no family at all.
    """
    plain = client.post("/admin/prompts/preview", data={"preview_family": ["notes"]}).text
    assert "Fix the parser" not in plain
    assert "every weekday at 08:00" not in plain
    assert "Permission mode: plan" not in plain


def test_the_preview_warns_when_the_cap_would_cut_it_off(client: TestClient, db, registered):
    settings_store.update(db, {"max_harness_chars": 60}, key=settings_store.PROMPTS)
    body = client.post("/admin/prompts/preview", data={"preview_family": ["web_search"]}).text
    assert "past the cap is cut off" in body


def _tools(*names):
    from lembas.services import tools as tools_service

    return [tools_service.REGISTRY[name].schema for name in names]
