"""A model reads one data group's data, and only that one -- from both sides.

Every store is checked twice: in the harness, where memories are
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
from lembas.services import harness, memories
from lembas.services import tools as tools_service
from lembas.services.crypto import encrypt

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


# --- The tools: what a model can go and get ----------------------------------------
def test_the_tool_context_carries_the_chats_group(db, owner, chats):
    home, away = chats
    assert _context(db, owner, home).data_group == DEFAULT_GROUP
    assert _context(db, owner, away).data_group == HOSTED


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


