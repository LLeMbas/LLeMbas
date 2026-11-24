"""The Skills and Notes pages and their settings, measured in a real Chromium
at 1280x900 and at 390x844 with long names and long text: nothing lies outside
the window, the page never scrolls sideways, rows line up by construction (a
form's labels, controls and hints on shared tracks; a fact's label and value
on two), anything wide scrolls in its own box, and what a thumb presses is at
least 40 px.

Slow (a browser per page): run it through `heavy-guard`, by itself."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import User
from lembas.services.skillstore import catalog, policy, service
from tests.test_reports_layout import CHROMIUM, REQUIRED, TAP, _measure

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CHROMIUM and not REQUIRED, reason="needs a Chromium to measure in"),
]

LONG = "a-skill-with-a-name-that-goes-on-for-quite-a-while-and-does-not-wrap"[:64]
WORDS = "Use when the eastern grove is three weeks late and nobody at all knows why it is. " * 3
WIDE = "x" * 300


@pytest.fixture(autouse=True)
def _a_browser_is_there():
    if not CHROMIUM:
        pytest.fail("no Chromium on PATH, and this run requires the browser tests")


@pytest.fixture
def seeded(client, db, registered) -> dict:
    admin = db.scalar(select(User))
    hand = policy.person(db, admin)
    body = (f"# Deploy\n\n{WORDS}\n\n## Before\n\n```\n{WIDE}\n```\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n")
    service.skill_add(db, hand, name=LONG, description=WORDS[:390], body=body,
                      category="a-category-with-a-long-name", subcategory="a-long-group")
    service.skill_add(db, hand, name="short", description="Use when short.", body="Do it.")
    service.skill_pin(db, hand, name=LONG, pinned=True)
    service.note_add(db, hand, title="The eastern grove of mallorn trees is three weeks late",
                     body=body, subjects=["a subject of several words", "grove", "harvest"],
                     kind="incident", summary=WORDS[:500])
    note = catalog.notes(db, hand)[0].ident
    agent = policy.agent(db, admin, harness="a-harness-with-a-long-name-of-its-own",
                         model="model-q8-131072",
                         model_said=True, session="a-session-id-that-is-long-0123456789abcdef")
    service.skill_edit(db, agent, name=LONG, section="before", text=f"{WORDS}\n\n{WIDE}",
                       reason=WORDS[:300])
    service.note_change(db, policy.agent(db, admin, harness="another-agent"), ident=note,
                        summary="Another summary", reason="because")
    service.remote_set(db, address="https://git.example.org/a-team/a-repository-of-skills.git",
                       username="ann", token="t")
    policy.save(db, {"sync_state": "diverged", "sync_at": "2026-01-02T03:04:05Z",
                     "sync_message": WORDS})
    return {"note": note}


def _inside(probe: dict, width: int) -> list[dict]:
    assert probe["page"] <= probe["width"], f"the page scrolls sideways at {width}"
    boxes = probe["named"]
    assert boxes, "nothing was measured"
    assert all(b["left"] >= 0 and b["right"] <= probe["width"] + 1 for b in boxes), [
        b for b in boxes if b["left"] < 0 or b["right"] > probe["width"] + 1
    ]
    assert not [b for b in boxes if b["cut"]], [b for b in boxes if b["cut"]]
    return boxes


def _taps(probe: dict, width: int) -> None:
    if width < 500:
        small = [tap for tap in probe["taps"] if tap["h"] < TAP]
        assert not small, small


def _fields_line_up(boxes: list[dict]) -> None:
    """In a row of fields, the controls start on one line and so do the hints."""
    controls = [b for b in boxes if b["what"].startswith(("input", "select"))]
    rows: dict[int, list[dict]] = {}
    for control in controls:
        rows.setdefault(control["top"], []).append(control)
    assert all(len({c["h"] for c in row}) == 1 for row in rows.values()), rows


@pytest.mark.parametrize("width", [1280, 390])
def test_the_skills_list_and_its_form(client, seeded, tmp_path, width):
    probe = _measure(
        client, "/skills", width, tmp_path,
        ".reports-list__link, .reports-list__title, .reports-list__summary, .card__title, "
        ".field-grid .field__label, .field-grid .input, .field-grid .field__hint, "
        ".reports-filter .input, .reports-filter .select, .store-actions .btn, .store-editor",
        ".reports-list__link, .store-actions .btn, .card .btn, .reports-filter .select",
    )
    boxes = _inside(probe, width)
    _taps(probe, width)
    labels = [b for b in boxes if b["what"] == "field__label"]
    inputs = [b for b in boxes if b["what"] == "input" and b["top"] > labels[0]["top"]]
    assert len(labels) == 3 and len(inputs) >= 3, boxes  # name, category, group
    if width >= 1000:
        # Name and category share a row: their labels end on one line and
        # their controls start on one, whatever the labels' words are.
        assert labels[0]["bottom"] == labels[1]["bottom"], labels
        assert inputs[0]["top"] == inputs[1]["top"] and inputs[0]["h"] == inputs[1]["h"], inputs
        assert inputs[0]["right"] < inputs[1]["left"], inputs
    else:
        # One column: a label, its control, then the next field.
        assert all(a["bottom"] <= b["top"] + 1 for a, b in zip(labels, labels[1:], strict=False))
        assert len({(b["left"], b["right"]) for b in inputs[:3]}) == 1, inputs
    _fields_line_up(boxes)


@pytest.mark.parametrize("width", [1280, 390])
def test_a_skills_page_and_its_editor(client, seeded, tmp_path, width):
    probe = _measure(
        client, f"/skills/s/{LONG}", width, tmp_path,
        ".topbar__title, .report__summary, .report__meta, .report__body, .report__body pre, "
        ".report__body table, .reports-list__link, .store-actions .btn, .card .btn",
        ".store-actions .btn, .card .btn, .reports-list__link",
    )
    boxes = _inside(probe, width)
    _taps(probe, width)
    assert any(b["what"] == "skill-body" for b in boxes), boxes
    probe = _measure(
        client, f"/skills/s/{LONG}?edit=1", width, tmp_path,
        ".field__label, .input, .store-editor, .field__hint, .card .btn",
        ".card .btn",
    )
    boxes = _inside(probe, width)
    _taps(probe, width)
    editor = next(b for b in boxes if "store-editor" in b["what"])
    inputs = [b for b in boxes if b["what"] == "input"]
    # The editor and the one-line fields above it end on the same line: the card's.
    assert abs(editor["right"] - inputs[0]["right"]) <= 1, (editor, inputs[0])
    _fields_line_up(boxes)


@pytest.mark.parametrize("width", [1280, 390])
def test_the_queue_shows_the_reason_and_the_provenance_beside_the_diff(
    client, seeded, tmp_path, width
):
    probe = _measure(
        client, "/skills/queue", width, tmp_path,
        "[data-testid=proposal] .card__title, .store-facts dt, .store-facts dd, .store-diff, "
        ".store-decide .input, .store-decide .btn, .report__meta",
        ".store-decide .btn, .store-actions .btn",
    )
    boxes = _inside(probe, width)
    _taps(probe, width)
    labels = [b for b in boxes if b["what"] == "DT"]
    values = [b for b in boxes if b["what"] == "DD"]
    assert len(labels) == len(values) and len(labels) >= 10, boxes  # two proposals
    first = labels[:5], values[:5]
    # Two shared tracks: every label starts on one x, every value on another,
    # and each pair shares a row whatever wraps.
    assert len({b["left"] for b in first[0]}) == 1 and len({b["left"] for b in first[1]}) == 1
    assert all(label["top"] == value["top"] for label, value in zip(*first, strict=True))
    assert all(label["right"] <= value["left"] for label, value in zip(*first, strict=True))
    text = " ".join(b["text"] for b in values)
    assert "a-harness-with-a-long-name" in text and "said by the caller" in text
    # The diff holds a 300-character line: it scrolls in its own box.
    diffs = [b for b in boxes if "store-diff" in b["what"]]
    assert diffs and all(b["right"] <= probe["width"] for b in diffs)
    buttons = [b for b in boxes if b["what"].startswith("proposal-")]
    assert len({b["h"] for b in buttons}) == 1, buttons


@pytest.mark.parametrize("width", [1280, 390])
def test_the_notes_list_and_a_notes_page(client, seeded, tmp_path, width):
    probe = _measure(
        client, "/notes", width, tmp_path,
        ".reports-list__link, .reports-list__head, .reports-list__summary, .reports-filter .input, "
        ".reports-filter .select, .field-grid .field__label, .field-grid .input, "
        ".field-grid .select, .field-grid .field__hint, .store-editor",
        ".reports-list__link, .card .btn, .reports-filter .select, .reports-filter .btn",
    )
    boxes = _inside(probe, width)
    _taps(probe, width)
    _fields_line_up(boxes)
    probe = _measure(
        client, f"/notes/n/{seeded['note']}", width, tmp_path,
        ".topbar__title, .store-facts dt, .store-facts dd, .report__body, .report__body pre, "
        ".card .btn, .store-actions .btn, .reports-list__link, .store-editor",
        ".card .btn, .store-actions .btn",
    )
    boxes = _inside(probe, width)
    _taps(probe, width)
    labels = [b for b in boxes if b["what"] == "DT"]
    values = [b for b in boxes if b["what"] == "DD"]
    assert len(labels) == len(values) == 7, boxes
    assert len({b["left"] for b in labels}) == 1 and len({b["left"] for b in values}) == 1
    assert all(label["top"] == value["top"] for label, value in zip(labels, values, strict=True))


@pytest.mark.parametrize("width", [1280, 390])
def test_the_settings_of_the_policy_and_the_remote(client, seeded, tmp_path, width):
    probe = _measure(
        client, "/admin/skills", width, tmp_path,
        ".card .field__label, .card .select, .card .input, .card .textarea, .card .field__hint, "
        ".store-kinds__name, .store-kinds__shared, .card .btn, .card .alert, "
        "[data-testid=sync-state], [data-testid=sync-message]",
        ".card .btn, .store-kinds .select",
    )
    boxes = _inside(probe, width)
    _taps(probe, width)
    names = [b for b in boxes if b["what"] == "store-kinds__name"]
    shared = [b for b in boxes if "store-kinds__shared" in b["what"]]
    kinds = [b for b in boxes if b["what"] == "select" and b["top"] >= names[0]["top"] - 60
             and b["top"] <= shared[-1]["bottom"]]
    assert len(names) == len(shared) == len(policy.KINDS), boxes
    # One grid for every kind: the names share a column, and so do the switches.
    assert len({b["left"] for b in names}) == 1 and len({b["left"] for b in shared}) == 1
    assert len({(b["left"], b["right"]) for b in kinds[-len(names):]}) == 1, kinds
    if width >= 1000:
        # A kind is one row: its name, its level and its switch share a centre line.
        for name, switch in zip(names, shared, strict=True):
            assert abs((name["top"] + name["bottom"]) - (switch["top"] + switch["bottom"])) <= 2
    else:
        assert all(n["bottom"] <= s["top"] + 1 for n, s in zip(names, shared, strict=True))
    _fields_line_up([b for b in boxes if b["top"] < names[0]["top"] - 80])
