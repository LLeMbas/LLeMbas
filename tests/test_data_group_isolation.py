"""A model reads one data group's data, and only that one -- from both sides.

Every store is checked twice: in the harness, where memories and skills are
*handed* to a model, and in the tool runners, where a model goes
looking. A test that only covered the search would miss the fetch by id, which is
the path a model takes after learning an id from somewhere it should not have.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from lembas.db.models import (
    DEFAULT_GROUP,
    Chat,
    Connection,
    DataGroup,
    Model,
    User,
)
from lembas.services import harness, reports
from lembas.services import tools as tools_service
from lembas.services.crypto import encrypt
from lembas.services.library import documents, memories, notes, skills

HOSTED = "hosted"
TOOLS = {"tools": True}


@pytest.fixture
def owner(db, registered) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


@pytest.fixture
def chats(db, owner) -> tuple[Chat, Chat]:
    """One chat in the default group, one in the hosted group."""
    db.add(DataGroup(id=HOSTED, name="Hosted"))
    local = Connection(name="Local", base_url="http://127.0.0.1:1", api_key_encrypted=encrypt(""))
    cloud = Connection(
        name="Cloud",
        base_url="http://127.0.0.1:2",
        api_key_encrypted=encrypt(""),
        data_group_id=HOSTED,
    )
    db.add_all([local, cloud])
    db.flush()
    db.add_all(
        [
            Model(connection_id=local.id, model_id="local-model", capabilities_json=TOOLS),
            Model(connection_id=cloud.id, model_id="cloud-model", capabilities_json=TOOLS),
        ]
    )
    home = Chat(user_id=owner.id, model_id="local-model", connection_id=local.id,
                data_group_id=DEFAULT_GROUP)
    away = Chat(user_id=owner.id, model_id="cloud-model", connection_id=cloud.id,
                data_group_id=HOSTED)
    db.add_all([home, away])
    db.commit()
    return home, away


def _tools(*names):
    return [tools_service.REGISTRY[name].schema for name in names]


def _context(db, owner, chat) -> tools_service.ToolContext:
    return tools_service.context_for(db, owner, chat, tools=None)


async def _run(context, tool: str, **args):
    return await tools_service.run_tool(context, tool, json.dumps(args))


# --- The harness: what a model is handed ------------------------------------------
def test_a_model_is_handed_only_its_own_groups_memories(db, owner, chats):
    home, away = chats
    memories.add(db, owner=owner, content="Home fact.", group=DEFAULT_GROUP)
    memories.add(db, owner=owner, content="Hosted fact.", group=HOSTED)

    at_home = harness.compose(db, owner, _tools("memory"), chat=home)
    abroad = harness.compose(db, owner, _tools("memory"), chat=away)
    assert "Home fact." in at_home and "Hosted fact." not in at_home
    assert "Hosted fact." in abroad and "Home fact." not in abroad


def test_a_base_in_another_group_is_not_named(db, owner, chats):
    home, away = chats
    base = documents.create_base(db, owner=owner, name="Home contracts")
    away.knowledge_bases = [base]
    db.commit()
    abroad = harness.compose(db, owner, _tools("knowledge_search"), chat=away)
    assert "Home contracts" not in abroad


# --- The tools: what a model can go and get ----------------------------------------
def test_the_tool_context_carries_the_chats_group(db, owner, chats):
    home, away = chats
    assert _context(db, owner, home).data_group == DEFAULT_GROUP
    assert _context(db, owner, away).data_group == HOSTED


async def test_a_note_in_another_group_cannot_be_searched_or_fetched(db, owner, chats):
    home, away = chats
    secret = notes.create(db, owner=owner, title="Home only", body="mallorn", group=DEFAULT_GROUP)
    context = _context(db, owner, away)

    found = await _run(context, "notes_search", query="mallorn")
    assert found.event["results"] == []
    fetched = await _run(context, "note_view", id=secret.id)
    assert fetched.event["status"] == "error"
    edited = await _run(context, "note_manage", action="edit", id=secret.id, body="gone")
    assert edited.event["status"] == "error"


async def test_a_note_a_model_writes_lands_in_its_group(db, owner, chats):
    home, away = chats
    outcome = await _run(
        _context(db, owner, away), "note_manage", action="create", title="t", body="b"
    )
    assert outcome.event["status"] == "ok"
    note = notes.get(db, outcome.event["results"][0]["id"], owner)
    assert note.data_group_id == HOSTED


async def test_a_memory_is_recorded_in_the_group_and_forgotten_only_there(db, owner, chats):
    home, away = chats
    memories.add(db, owner=owner, content="Keep this at home.", group=DEFAULT_GROUP)
    await _run(
        _context(db, owner, away), "memory", target="user", action="add", content="Hosted fact."
    )
    assert [m.content for m in memories.all_for(db, owner, HOSTED)] == ["Hosted fact."]

    await _run(
        _context(db, owner, away),
        "memory",
        target="user",
        action="remove",
        old_text="Keep this at home.",
    )
    assert [m.content for m in memories.all_for(db, owner, DEFAULT_GROUP)] == [
        "Keep this at home."
    ]
    # The same call from the memory's own group does forget it, so the refusal
    # above is the group and not a mistyped argument.
    await _run(
        _context(db, owner, home),
        "memory",
        target="user",
        action="remove",
        old_text="Keep this at home.",
    )
    db.expire_all()
    assert memories.all_for(db, owner, DEFAULT_GROUP) == []


def test_the_same_fact_in_two_groups_is_two_memories(db, owner):
    first = memories.add(db, owner=owner, content="Same.", group=DEFAULT_GROUP)
    second = memories.add(db, owner=owner, content="Same.", group=HOSTED)
    assert first.id != second.id


async def test_a_document_in_another_group_cannot_be_fetched_by_id(db, owner, chats):
    home, away = chats
    base = documents.create_base(db, owner=owner, name="Home")
    document = documents.store_upload(
        db, owner=owner, payload=b"The mallorn is golden.", filename="a.txt", base=base
    )
    context = _context(db, owner, away)
    assert (await _run(context, "knowledge_search", query="mallorn")).event["results"] == []
    assert (await _run(context, "knowledge_get", id=document.id)).event["status"] == "error"


async def test_a_report_in_another_group_cannot_be_read(db, owner, chats):
    home, away = chats
    report = reports.create(db, owner=owner, title="Home report", body="mallorn", unread=False)
    context = _context(db, owner, away)
    assert (await _run(context, "report_get", id=report.id)).event["status"] == "error"


async def test_a_skill_in_another_group_cannot_be_fetched(db, owner, chats):
    home, away = chats
    skills.create(db, owner=owner, name="home-skill", description="Home.", body="SECRET")
    outcome = await _run(_context(db, owner, away), "skill_view", name="home-skill")
    assert "SECRET" not in outcome.content


def test_a_skill_name_taken_in_another_group_says_so(db, owner):
    skills.create(db, owner=owner, name="shared-name", description="d", body="b")
    with pytest.raises(skills.SkillError, match="another data group"):
        skills.create(
            db, owner=owner, name="shared-name", description="d", body="b", group=HOSTED
        )


def test_each_group_gets_its_own_default_base(db, owner, chats):
    home = documents.default_base(db, owner)
    away = documents.default_base(db, owner, HOSTED)
    assert home.id != away.id
    assert away.data_group_id == HOSTED
    assert home.name != away.name
