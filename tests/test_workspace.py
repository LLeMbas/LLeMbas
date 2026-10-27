"""What a chat's model is told about its files (harness spec 3.0.0): the stacked
instruction files and the view (`services/workspace.py`, `blocks/workspace.md`).

The conformance area `instructions` runs twice: as the pure rule (`chain`, a
readable set from the case) and against a real tree, grants and resolver. The
cases are a copy of the CLI's `harness/conformance/instructions.json` until the
vendored spec carries it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lembas.db.models import (
    DEFAULT_ORG,
    KIND_FILE,
    ORG_OWNER,
    Chat,
    Connection,
    DataGroup,
    Model,
    SessionGrant,
    User,
)
from lembas.security import access
from lembas.security.passwords import hash_password
from lembas.services import chat as chat_service
from lembas.services import fs, harness, harness_spec, workspace
from lembas.services import tools as tools_service

_VENDORED = harness_spec.conformance_dir() / "instructions.json"
_COPY = Path(__file__).parent / "fixtures" / "harness" / "instructions.json"
SPEC = json.loads((_VENDORED if _VENDORED.exists() else _COPY).read_text(encoding="utf-8"))


def _visible(grants: dict[str, str] | None, path: str) -> bool:
    if grants is None:
        return True
    return access.nearest(grants, path) not in (None, "none")


@pytest.mark.parametrize("case", SPEC["cases"], ids=[str(n) for n in range(len(SPEC["cases"]))])
def test_the_rule(case):
    files = set(case["files"])
    grants = case.get("grants")
    got = workspace.chain(
        case["boundary"], case["start"], lambda p: p in files and _visible(grants, p)
    )
    assert got == case["expect"]


def _owner(db) -> User:
    user = User(name="frodo", email="frodo@shire.test", password_hash=hash_password("x" * 12),
                org_role=ORG_OWNER)
    db.add(user)
    db.commit()
    return user


def _file(db, path: str, text: str = "rules\n") -> None:
    node = fs._make_node(db, DEFAULT_ORG, path, KIND_FILE, owner_id=None, by=None)
    data = text.encode()
    fs._set_content(db, node, fs.blobs.put_bytes(db, data), len(data), by=None, chat_id=None,
                    message_id=None)


def _chat(db, user, *, boundary="/", start="", grants=None) -> Chat:
    group = DataGroup(name="g")
    db.add(group)
    db.flush()
    chat = Chat(user_id=user.id, data_group_id=group.id, boundary=boundary, start_dir=start,
                grants_customised=grants is not None)
    db.add(chat)
    db.flush()
    for path, level in (grants or {}).items():
        db.add(SessionGrant(chat_id=chat.id, path=path, level=level))
    db.commit()
    return chat


@pytest.mark.parametrize("case", SPEC["cases"], ids=[str(n) for n in range(len(SPEC["cases"]))])
def test_the_rule_on_a_real_tree(db, case):
    user = _owner(db)
    for path in case["files"]:
        _file(db, path)
    db.commit()
    start = case["start"]
    if fs.lookup(db, DEFAULT_ORG, start) is None and start != "/":
        fs._make_node(db, DEFAULT_ORG, start, "dir", owner_id=None, by=None)
        db.commit()
    chat = _chat(db, user, boundary=case["boundary"], start=start, grants=case.get("grants"))
    assert workspace.instruction_paths(fs.view(db, user, chat)) == case["expect"]


def _model_chat(db, user, **kwargs) -> Chat:
    connection = Connection(name="c", base_url="http://x.invalid/v1")
    db.add(connection)
    db.flush()
    db.add(Model(connection_id=connection.id, model_id="m", capabilities_json={"tools": True}))
    chat = _chat(db, user, **kwargs)
    chat.model_id = "m"
    chat.connection_id = connection.id
    db.commit()
    return chat


def test_the_system_prompt_carries_the_chain_and_the_view(db):
    user = _owner(db)
    _file(db, "/AGENTS.md", "Root rules.\n")
    _file(db, "/sec/CLAUDE.md", "Section rules.\n")
    _file(db, "/sec/repo/AGENTS.md", "Repository rules.\n")
    _file(db, "/sec/repo/src/AGENTS.md", "Below the start: not in the chain.\n")
    _file(db, "/Ref/r.md", "ref\n")
    db.commit()
    chat = _model_chat(db, user, start="/sec/repo", grants={"/": "stage", "/Ref": "ro"})
    body = chat_service.build_request(
        db, chat, tools=tools_service.resolve_tools(db, chat, user).schemas, user=user
    )
    system = body["messages"][0]["content"]
    root = system.index("Instructions from AGENTS.md:\n<instructions>\nRoot rules.")
    section = system.index("Instructions from sec/CLAUDE.md:")
    repo = system.index("Instructions from sec/repo/AGENTS.md:")
    assert root < section < repo
    assert "Below the start" not in system
    assert "Relative paths start at `/sec/repo`" in system
    assert "`/` (stage), `/Ref/` (read-only)" in system
    # The shared text's file bullet, and the context block with recall.
    assert harness.FILE_TOOLS in system
    assert "# Tool results\n- Every tool result starts with its id" in system


def test_a_read_hands_over_a_nearby_instruction_file_once(db):
    import asyncio

    user = _owner(db)
    _file(db, "/AGENTS.md", "Root.\n")
    _file(db, "/lib/AGENTS.md", "Library rules.\n")
    _file(db, "/lib/a.py", "a = 1\n")
    _file(db, "/lib/b.py", "b = 1\n")
    db.commit()
    chat = _chat(db, user)
    context = tools_service.ToolContext(owner_id=user.id, chat_id=chat.id)
    first = asyncio.run(tools_service.run_tool(context, "read", '{"path": "/lib/a.py"}'))
    assert first.content.endswith(
        '<instructions from="/lib/AGENTS.md">\nThis directory has its own instructions; they '
        "apply to work under it.\nLibrary rules.\n</instructions>"
    )
    second = asyncio.run(tools_service.run_tool(context, "read", '{"path": "/lib/b.py"}'))
    assert second.content == "1: b = 1"
    # The root's own is in the prompt already, never handed over again.
    assert "Root." not in first.content


def test_the_view_names_what_a_member_reaches(db):
    """A member of the organisation, in a chat that grants `/`: their home and
    the shared skills, each with its level -- not `/`, which they cannot read."""
    member = User(name="sam", email="sam@shire.test", password_hash=hash_password("x" * 12))
    db.add(member)
    db.commit()
    fs.seed_org(db, DEFAULT_ORG)
    home = fs.ensure_home(db, member).path
    db.commit()
    chat = _chat(db, member)
    sentence = workspace.view_sentence(fs.view(db, member, chat))
    assert sentence == f"`/skills/` (read-only), `{home}/` (commit)"
