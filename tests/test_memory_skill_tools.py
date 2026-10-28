"""Memory and skills as the harness spec has them.

`memory` and `skills_list` / `skill_view` / `skill_manage` replaced LLeMbas's
own `memory_add` and `skill_*` so
that a model is sent the same tools here as in the LLeMbas CLI. These hold what
the new shapes promise -- a list of changes that apply together or not at all --
and that a call under a former name is still the call it means.
"""

from __future__ import annotations

import json

import pytest

from lembas.db.models import Memory, User
from lembas.security.passwords import hash_password
from lembas.services import fs, memories, tool_labels
from lembas.services import skill_format as fmt
from lembas.services import tools as tools_service


@pytest.fixture
def owner(db):
    user = User(name="Frodo", email="frodo@shire.test", password_hash=hash_password("x"))
    db.add(user)
    db.commit()
    return user


async def _run(owner, tool, **args):
    context = tools_service.ToolContext(owner_id=owner.id)
    return await tools_service.run_tool(context, tool, json.dumps(args))


class _Skill:
    def __init__(self, description: str, body: str, path: str) -> None:
        self.description, self.body, self.path = description, body, path


def _path(db, owner, name: str) -> str:
    return f"{fs.ensure_home(db, owner).path}/skills/{name}/SKILL.md"


def _skill(db, owner, name):
    """The skill file as it stands (skills are files since spec 3.0.0), or None."""
    db.expire_all()
    path = _path(db, owner, name)
    if fs.lookup(db, owner.org_id, path) is None:
        return None
    meta, body = fmt.parse(fs.read(fs.view(db, owner), path).decode())
    return _Skill(meta["description"], body, path)


def _make(db, owner, *, name, description, body):
    text = fmt.compose(name, description, body)
    fs.write(fs.view(db, owner), _path(db, owner, name), text.encode())
    db.commit()


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
    _make(db, owner, name="tea", description="Use when tea.", body="Boil water.")
    op = {"action": "patch", "name": "tea", "old_string": "Boil water.",
          "new_string": "Boil water, then wait a minute."}
    outcome = await _run(owner, "skill_manage", operations=[op])
    assert outcome.event["status"] == "ok", outcome.content
    skill = _skill(db, owner, "tea")
    assert skill.body == "Boil water, then wait a minute."
    # What it was is the file's previous version.
    assert len(fs.versions(fs.view(db, owner), skill.path)) == 2


async def test_a_patch_that_is_not_there_quotes_the_skill(db, owner):
    _make(db, owner, name="tea", description="Use when tea.", body="Boil water.")
    op = {"action": "patch", "name": "tea", "old_string": "Steep.", "new_string": "x"}
    outcome = await _run(owner, "skill_manage", operations=[op])
    assert outcome.event["status"] == "error"
    assert "skill_view" in outcome.content and "Boil water." in outcome.content


async def test_a_patch_may_not_break_the_frontmatter(db, owner):
    _make(db, owner, name="tea", description="Use when tea.", body="Boil.")
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
    _make(db, owner, name="tea", description="Use when tea.", body="Boil.")
    await _run(owner, "skill_manage", operations=[
        {"action": "patch", "name": "tea", "old_string": "Boil.", "new_string": "Steep."}
    ])
    await _run(owner, "skill_manage", operations=[{"action": "revert", "name": "tea"}])
    assert _skill(db, owner, "tea").body == "Boil."
    await _run(owner, "skill_manage", operations=[{"action": "revert", "name": "tea"}])
    assert _skill(db, owner, "tea").body == "Steep."


async def test_delete_and_revert_go_alone(db, owner):
    _make(db, owner, name="tea", description="Use when tea.", body="Boil.")
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
    _make(db, owner, name="tea", description="Use when tea.", body="Boil.")
    op = {"action": "write_file", "name": "tea", "file_path": "references/a.md",
          "file_content": "x"}
    outcome = await _run(owner, "skill_manage", operations=[op])
    assert outcome.event["status"] == "error"
    assert "create, patch, delete or revert" in outcome.content


async def test_skills_list_and_skill_view(db, owner):
    _make(db, owner, name="tea", description="Use when tea.", body="Boil.")
    listed = await _run(owner, "skills_list")
    assert "tea — Use when tea." in listed.content
    viewed = await _run(owner, "skill_view", name="tea")
    assert viewed.content == (
        f"# Skill: tea\nUse when tea.\nFile: {_path(db, owner, 'tea')}\n\nBoil."
    )


def test_a_skill_md_round_trips():
    text = tools_service.compose_skill("tea", "Use when: tea, \"strong\".", "Boil.\n\nSteep.")
    meta, body = tools_service.parse_skill(text)
    assert meta == {"name": "tea", "description": 'Use when: tea, "strong".'}
    assert body == "Boil.\n\nSteep."


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
        ("skill_get", {"name": "tea"}, "skill_view", {"name": "tea"}),
        ("skill_create", {"name": "tea", "description": "d", "body": "b"}, "skill_manage",
         {"operations": [{"action": "create", "name": "tea", "description": "d",
                          "instructions": "b"}]}),
    ],
)
def test_a_former_name_is_converted(old, args, new, expected):
    assert tools_service.resolve_alias(old, args) == (new, expected)


async def test_a_former_skill_edit_sets_what_it_sent(db, owner):
    _make(db, owner, name="tea", description="Use when tea.", body="Boil.")
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
    assert tool_labels.describe("skill_manage", {"operations": [
        {"action": "patch", "name": "tea"}
    ]})[1] == "patch tea"


def test_old_transcripts_keep_their_labels():
    for old in ("memory_add", "memory_forget", "notes_get", "notes_create", "skill_get"):
        assert old in tool_labels.LABELS and old in tool_labels.ICONS
