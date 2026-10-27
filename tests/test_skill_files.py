"""Skills as files (harness spec 3.0.0, `filesystem.md` *Skills*).

`<skills dir>/<name>/SKILL.md` along the chat's instruction chain, then the
person's `/home/<user>/skills/`, then the organisation's `/skills/`; a name's
first claim wins. `skills_list`, `skill_view` and `skill_manage` are their
meaning, through the chat's view of the tree: a skill it cannot read does not
exist for it, one it can only read cannot be changed, and revert puts back the
file's previous version.
"""

from __future__ import annotations

import asyncio
import json

from lembas.db.models import DEFAULT_ORG, KIND_FILE, Chat, DataGroup, SessionGrant, User
from lembas.security.passwords import hash_password
from lembas.services import fs, skill_files
from lembas.services import skill_format as fmt
from lembas.services import tools as tools_service


def _member(db, email="sam@shire.test") -> User:
    user = User(name=email.split("@")[0], email=email, password_hash=hash_password("x" * 12))
    db.add(user)
    db.commit()
    fs.seed_org(db, DEFAULT_ORG)
    fs.ensure_home(db, user)
    db.commit()
    return user


def _org_file(db, path: str, text: str) -> None:
    node = fs._make_node(db, DEFAULT_ORG, path, KIND_FILE, owner_id=None, by=None)
    data = text.encode()
    fs._set_content(db, node, fs.blobs.put_bytes(db, data), len(data), by=None, chat_id=None,
                    message_id=None)
    db.commit()


def _chat(db, user, *, start="", grants=None) -> Chat:
    group = DataGroup(name="g")
    db.add(group)
    db.flush()
    chat = Chat(user_id=user.id, data_group_id=group.id, start_dir=start,
                grants_customised=grants is not None)
    db.add(chat)
    db.flush()
    for path, level in (grants or {}).items():
        db.add(SessionGrant(chat_id=chat.id, path=path, level=level))
    db.commit()
    return chat


def _call(user, chat, tool, **args):
    context = tools_service.ToolContext(owner_id=user.id, chat_id=chat.id)
    return asyncio.run(tools_service.run_tool(context, tool, json.dumps(args)))


def test_create_view_patch_revert_delete(db):
    user = _member(db)
    chat = _chat(db, user)
    home = fs.home_of(db, user).path
    made = _call(user, chat, "skill_manage", operations=[{
        "action": "create", "name": "weekly-report",
        "description": "Use when the weekly report is due.", "instructions": "Write it.",
    }])
    assert made.event["status"] == "ok", made.content
    path = f"{home}/skills/weekly-report/SKILL.md"
    assert fs.read(fs.view(db, user), path).decode() == fmt.compose(
        "weekly-report", "Use when the weekly report is due.", "Write it."
    )
    listed = _call(user, chat, "skills_list")
    assert listed.content.startswith("weekly-report — Use when the weekly report is due.")
    viewed = _call(user, chat, "skill_view", name="weekly-report")
    assert viewed.content == (
        f"# Skill: weekly-report\nUse when the weekly report is due.\nFile: {path}\n\nWrite it."
    )
    patched = _call(user, chat, "skill_manage", operations=[{
        "action": "patch", "name": "weekly-report", "old_string": "Write it.",
        "new_string": "Write it on Fridays.",
    }])
    assert patched.event["status"] == "ok", patched.content
    assert "Fridays" in fs.read(fs.view(db, user), path).decode()
    reverted = _call(user, chat, "skill_manage",
                     operations=[{"action": "revert", "name": "weekly-report"}])
    assert "revert again to undo this" in reverted.content
    assert "Fridays" not in fs.read(fs.view(db, user), path).decode()
    gone = _call(user, chat, "skill_manage",
                 operations=[{"action": "delete", "name": "weekly-report"}])
    assert gone.event["status"] == "ok"
    assert skill_files.all_skills(fs.view(db, user, chat)) == []
    # To the trash, restorable.
    assert any(n.path == f"{home}/skills/weekly-report" for n in fs.trash(fs.view(db, user)))


def test_one_failing_operation_keeps_none(db):
    user = _member(db)
    chat = _chat(db, user)
    out = _call(user, chat, "skill_manage", operations=[
        {"action": "create", "name": "first-one", "description": "Use when x.",
         "instructions": "Do x."},
        {"action": "create", "name": "second-one", "content": "no frontmatter"},
    ])
    assert out.event["status"] == "error"
    assert out.content.startswith("operation 2 (create second-one): SKILL.md must start")
    assert skill_files.all_skills(fs.view(db, user, chat)) == []


def test_the_chain_the_home_and_the_organisation_in_that_order(db):
    user = _member(db)
    home = fs.home_of(db, user).path
    skill = "---\nname: {n}\ndescription: {d}\n---\n\nBody.\n"
    _org_file(db, "/skills/shared/SKILL.md", skill.format(n="shared", d="From the org."))
    _org_file(db, "/skills/clash/SKILL.md", skill.format(n="clash", d="The org's."))
    fs.write(fs.view(db, user), f"{home}/skills/clash/SKILL.md",
             skill.format(n="clash", d="Mine.").encode())
    fs.write(fs.view(db, user), f"{home}/proj/skills/clash/SKILL.md",
             skill.format(n="clash", d="The project's.").encode())
    db.commit()
    chat = _chat(db, user, start=f"{home}/proj")
    found = {s.name: s.description for s in skill_files.all_skills(fs.view(db, user, chat))}
    assert found == {"clash": "The project's.", "shared": "From the org."}
    elsewhere = _chat(db, user)
    assert {s.name: s.description
            for s in skill_files.all_skills(fs.view(db, user, elsewhere))}["clash"] == "Mine."
    # The org's are readable but not the member's to change.
    out = _call(user, elsewhere, "skill_manage", operations=[{
        "action": "patch", "name": "shared", "old_string": "Body.", "new_string": "Mine now.",
    }])
    assert out.content == (
        "Read-only: /skills/shared/SKILL.md can be read in this session but not changed."
    )


def test_a_skill_the_chat_cannot_read_does_not_exist_for_it(db):
    user = _member(db)
    home = fs.home_of(db, user).path
    fs.write(fs.view(db, user), f"{home}/skills/private/SKILL.md",
             fmt.compose("private", "Use when private.", "Secret.").encode())
    db.commit()
    chat = _chat(db, user, grants={"/skills": "ro"})
    assert skill_files.all_skills(fs.view(db, user, chat)) == []
    out = _call(user, chat, "skill_view", name="private")
    assert out.event["status"] == "error" and "Secret" not in out.content


def test_a_stray_skill_md_is_noted_by_write(db):
    assert skill_files.stray_note("/home/sam/notes/SKILL.md").startswith("\nNote: a SKILL.md")
    assert skill_files.stray_note("/home/sam/skills/x/SKILL.md") == ""
    assert skill_files.stray_note("/home/sam/x/README.md") == ""


def test_the_frontmatter_checks_are_the_old_ones():
    import pytest

    with pytest.raises(ValueError, match="frontmatter name must be"):
        fmt.validate(fmt.compose("other", "Use when.", "Body."), "mine")
    with pytest.raises(ValueError, match="description is 401 characters"):
        fmt.validate(fmt.compose("mine", "x" * 401, "Body."), "mine")
    with pytest.raises(ValueError, match="not a valid skill name"):
        fmt.check_name("x")
    assert fmt.validate(fmt.compose("mine", "Use when.", "Body."), "mine") == ("Use when.", "Body.")
