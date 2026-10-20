"""The library's tools as the harness spec (1.2.0) has them.

`memory`, `note_view` / `note_manage` and `skills_list` / `skill_view` /
`skill_manage` replaced LLeMbas's own `memory_add`, `notes_*` and `skill_*` so
that a model is sent the same tools here as in the LLeMbas CLI. These hold what
the new shapes promise -- a list of changes that apply together or not at all --
and that a call under a former name is still the call it means.
"""

from __future__ import annotations

import json

import pytest

from lembas.db.models import Memory, Note, SkillRevision, User
from lembas.security.passwords import hash_password
from lembas.services import tool_labels
from lembas.services import tools as tools_service
from lembas.services.library import memories, notes, skills


@pytest.fixture
def owner(db):
    user = User(name="Frodo", email="frodo@shire.test", password_hash=hash_password("x"))
    db.add(user)
    db.commit()
    return user


async def _run(owner, tool, **args):
    context = tools_service.ToolContext(owner_id=owner.id)
    return await tools_service.run_tool(context, tool, json.dumps(args))


def _skill(db, owner, name):
    db.expire_all()
    return skills.owned_by_name(db, name, owner)


# --- skill_manage ---------------------------------------------------------------
async def test_a_skill_is_created_from_description_and_instructions(db, owner):
    op = {"action": "create", "name": "weekly-report", "description": "Use when asked for "
          "the weekly report.", "instructions": "1. Gather.\n2. Write."}
    outcome = await _run(owner, "skill_manage", operations=[op])
    assert outcome.event["status"] == "ok", outcome.content
    skill = _skill(db, owner, "weekly-report")
    assert skill.description == "Use when asked for the weekly report."
    assert skill.body == "1. Gather.\n2. Write."


async def test_a_skill_is_created_from_a_whole_skill_md(db, owner):
    content = tools_service.compose_skill("tea", 'Use when "tea" is asked: brew it.', "Boil.")
    outcome = await _run(
        owner, "skill_manage", operations=[{"action": "create", "name": "tea", "content": content}]
    )
    assert outcome.event["status"] == "ok", outcome.content
    assert _skill(db, owner, "tea").description == 'Use when "tea" is asked: brew it.'


async def test_a_patch_changes_part_of_a_skill_and_keeps_what_it_was(db, owner):
    skills.create(db, owner=owner, name="tea", description="Use when tea.", body="Boil water.")
    op = {"action": "patch", "name": "tea", "old_string": "Boil water.",
          "new_string": "Boil water, then wait a minute."}
    outcome = await _run(owner, "skill_manage", operations=[op])
    assert outcome.event["status"] == "ok", outcome.content
    skill = _skill(db, owner, "tea")
    assert skill.body == "Boil water, then wait a minute."
    assert db.query(SkillRevision).filter_by(skill_id=skill.id).count() == 1


async def test_a_patch_that_is_not_there_quotes_the_skill(db, owner):
    skills.create(db, owner=owner, name="tea", description="Use when tea.", body="Boil water.")
    op = {"action": "patch", "name": "tea", "old_string": "Steep.", "new_string": "x"}
    outcome = await _run(owner, "skill_manage", operations=[op])
    assert outcome.event["status"] == "error"
    assert "skill_view" in outcome.content and "Boil water." in outcome.content


async def test_a_patch_may_not_break_the_frontmatter(db, owner):
    skills.create(db, owner=owner, name="tea", description="Use when tea.", body="Boil.")
    op = {"action": "patch", "name": "tea", "old_string": "name: tea", "new_string": "name: x"}
    outcome = await _run(owner, "skill_manage", operations=[op])
    assert outcome.event["status"] == "error"
    assert "would break SKILL.md" in outcome.content
    assert _skill(db, owner, "tea").body == "Boil."


async def test_the_operations_apply_together_or_not_at_all(db, owner):
    """The second fails, so the first is not kept either -- the service commits
    as it goes, which is why every operation is checked before any is written."""
    ops = [
        {"action": "create", "name": "first", "description": "Use when one.",
         "instructions": "One."},
        {"action": "patch", "name": "missing", "old_string": "a", "new_string": "b"},
    ]
    outcome = await _run(owner, "skill_manage", operations=ops)
    assert outcome.event["status"] == "error"
    assert "operation 2" in outcome.content
    assert _skill(db, owner, "first") is None


async def test_a_skill_made_in_the_call_can_be_patched_in_it(db, owner):
    ops = [
        {"action": "create", "name": "tea", "description": "Use when tea.",
         "instructions": "Boil."},
        {"action": "patch", "name": "tea", "old_string": "Boil.", "new_string": "Boil. Steep."},
    ]
    outcome = await _run(owner, "skill_manage", operations=ops)
    assert outcome.event["status"] == "ok", outcome.content
    assert _skill(db, owner, "tea").body == "Boil. Steep."


async def test_revert_puts_back_the_last_version_and_twice_undoes_it(db, owner):
    skills.create(db, owner=owner, name="tea", description="Use when tea.", body="Boil.")
    await _run(owner, "skill_manage", operations=[
        {"action": "patch", "name": "tea", "old_string": "Boil.", "new_string": "Steep."}
    ])
    await _run(owner, "skill_manage", operations=[{"action": "revert", "name": "tea"}])
    assert _skill(db, owner, "tea").body == "Boil."
    await _run(owner, "skill_manage", operations=[{"action": "revert", "name": "tea"}])
    assert _skill(db, owner, "tea").body == "Steep."


async def test_delete_and_revert_go_alone(db, owner):
    skills.create(db, owner=owner, name="tea", description="Use when tea.", body="Boil.")
    ops = [{"action": "delete", "name": "tea"},
           {"action": "create", "name": "coffee", "description": "Use when coffee.",
            "instructions": "Grind."}]
    outcome = await _run(owner, "skill_manage", operations=ops)
    assert "must be the only operation" in outcome.content
    assert _skill(db, owner, "tea") is not None
    outcome = await _run(owner, "skill_manage", operations=[{"action": "delete", "name": "tea"}])
    assert outcome.event["status"] == "ok"
    assert _skill(db, owner, "tea") is None


async def test_what_this_server_does_not_offer_is_refused(db, owner):
    skills.create(db, owner=owner, name="tea", description="Use when tea.", body="Boil.")
    op = {"action": "write_file", "name": "tea", "file_path": "references/a.md",
          "file_content": "x"}
    outcome = await _run(owner, "skill_manage", operations=[op])
    assert outcome.event["status"] == "error"
    assert "create, patch, delete or revert" in outcome.content


async def test_skills_list_and_skill_view(db, owner):
    skills.create(db, owner=owner, name="tea", description="Use when tea.", body="Boil.")
    listed = await _run(owner, "skills_list")
    assert "tea — Use when tea." in listed.content
    viewed = await _run(owner, "skill_view", name="tea")
    assert viewed.content == "# Skill: tea\nUse when tea.\n\nBoil."


def test_a_skill_md_round_trips():
    text = tools_service.compose_skill("tea", "Use when: tea, \"strong\".", "Boil.\n\nSteep.")
    meta, body = tools_service.parse_skill(text)
    assert meta == {"name": "tea", "description": 'Use when: tea, "strong".'}
    assert body == "Boil.\n\nSteep."


# --- note_manage / note_view ----------------------------------------------------
async def test_notes_are_written_read_changed_and_deleted(db, owner):
    created = await _run(owner, "note_manage", action="create", title="Map", body="Bree.")
    assert created.event["status"] == "ok", created.content
    note_id = created.event["results"][0]["id"]
    viewed = await _run(owner, "note_view", id=note_id)
    assert viewed.content.startswith("# Map\n") and "Bree." in viewed.content
    await _run(owner, "note_manage", action="edit", id=note_id, body="Rivendell.")
    db.expire_all()
    assert db.get(Note, note_id).body == "Rivendell."
    await _run(owner, "note_manage", action="delete", id=note_id)
    db.expire_all()
    assert db.get(Note, note_id) is None


async def test_a_note_needs_a_title_and_a_body(db, owner):
    outcome = await _run(owner, "note_manage", action="create", body="No title.")
    assert outcome.event["status"] == "error"
    assert "title and a body" in outcome.content


async def test_a_note_shared_with_someone_is_not_theirs_to_change(db, owner):
    other = User(name="Sam", email="sam@shire.test", password_hash=hash_password("x"))
    db.add(other)
    db.commit()
    note = notes.create(db, owner=other, title="Sam's", body="Mine.")
    outcome = await _run(owner, "note_manage", action="delete", id=note.id)
    assert outcome.event["status"] == "error"
    assert db.get(Note, note.id) is not None


# --- memory ---------------------------------------------------------------------
async def test_memory_applies_a_list_together(db, owner):
    memories.add(db, owner=owner, content="Prefers tea.")
    outcome = await _run(owner, "memory", target="user", operations=[
        {"action": "remove", "old_text": "tea"},
        {"action": "add", "content": "Prefers coffee."},
    ])
    assert outcome.event["status"] == "ok", outcome.content
    assert "Applied 2 operations." in outcome.content and "do not repeat it" in outcome.content
    db.expire_all()
    assert [m.content for m in memories.all_for(db, owner)] == ["Prefers coffee."]


async def test_a_refused_change_shows_the_entries(db, owner):
    memories.add(db, owner=owner, content="Drinks coffee black.")
    memories.add(db, owner=owner, content="Allergic to coffee.")
    outcome = await _run(owner, "memory", target="user", action="remove", old_text="coffee")
    assert outcome.event["status"] == "error"
    assert "Current entries" in outcome.content and "1. Drinks coffee black." in outcome.content


async def test_a_replaced_memory_keeps_its_place(db, owner):
    memories.add(db, owner=owner, content="First.")
    memories.add(db, owner=owner, content="Second.")
    memories.add(db, owner=owner, content="Third.")
    await _run(owner, "memory", target="user", action="replace", old_text="Second.",
               content="Second, changed.")
    db.expire_all()
    assert [m.content for m in memories.all_for(db, owner)] == [
        "First.", "Second, changed.", "Third."
    ]


async def test_there_is_one_target_here(db, owner):
    outcome = await _run(owner, "memory", target="project", action="add", content="x")
    assert outcome.event["status"] == "error"
    assert db.query(Memory).count() == 0


# --- Former names -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("old", "args", "new", "expected"),
    [
        ("memory_add", {"content": "x"}, "memory",
         {"target": "user", "action": "add", "content": "x"}),
        ("memory_forget", {"content": "x"}, "memory",
         {"target": "user", "action": "remove", "old_text": "x"}),
        ("notes_get", {"id": "a"}, "note_view", {"id": "a"}),
        ("notes_create", {"title": "t", "body": "b"}, "note_manage",
         {"action": "create", "title": "t", "body": "b"}),
        ("notes_edit", {"id": "a", "body": "b"}, "note_manage",
         {"action": "edit", "id": "a", "body": "b"}),
        ("notes_delete", {"id": "a"}, "note_manage", {"action": "delete", "id": "a"}),
        ("skill_get", {"name": "tea"}, "skill_view", {"name": "tea"}),
        ("skill_create", {"name": "tea", "description": "d", "body": "b"}, "skill_manage",
         {"operations": [{"action": "create", "name": "tea", "description": "d",
                          "instructions": "b"}]}),
    ],
)
def test_a_former_name_is_converted(old, args, new, expected):
    assert tools_service.resolve_alias(old, args) == (new, expected)


async def test_a_former_skill_edit_sets_what_it_sent(db, owner):
    skills.create(db, owner=owner, name="tea", description="Use when tea.", body="Boil.")
    name, args = tools_service.resolve_alias("skill_edit", {"name": "tea", "body": "Steep."})
    outcome = await _run(owner, name, **args)
    assert outcome.event["status"] == "ok", outcome.content
    skill = _skill(db, owner, "tea")
    assert (skill.description, skill.body) == ("Use when tea.", "Steep.")


# --- What an approval card says ---------------------------------------------------
def test_an_approval_card_says_what_changes():
    assert tool_labels.describe("memory", {"operations": [
        {"action": "add", "content": "Prefers tea."}, {"action": "remove", "old_text": "x"}
    ]}) == ("Change what is remembered", "+ Prefers tea.\n− x")
    assert tool_labels.describe("note_manage", {"action": "create", "title": "Map"}) == (
        "Change your notes", "create Map"
    )
    assert tool_labels.describe("skill_manage", {"operations": [
        {"action": "patch", "name": "tea"}
    ]})[1] == "patch tea"


def test_old_transcripts_keep_their_labels():
    for old in ("memory_add", "memory_forget", "notes_get", "notes_create", "skill_get"):
        assert old in tool_labels.LABELS and old in tool_labels.ICONS
