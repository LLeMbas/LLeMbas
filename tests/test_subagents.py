"""Which model a subagent runs on, and when subagents are offered (harness spec
3.3.0, `subagents.md`): the chat's own model unless the person named another in
the chat; the logical capacity restrictions and no fallback; offered where any
model could serve.

The pure rules are driven through the spec's conformance cases in
`test_harness_spec.py`; here they are checked over real rows -- the person's
messages, the connections and the models -- and through `task` itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import ROLE_USER, Chat, Connection, Message, Model, User
from lembas.services import settings_store, team_run
from lembas.services import subagent as subagent_service
from lembas.services import subagents as rules
from lembas.services import tools as tools_service
from lembas.services.crypto import encrypt

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def delegation_allowed(db, registered):
    settings_store.update(db, {"enabled": True}, key=settings_store.SUBAGENTS)
    settings_store.update(db, {"default_permissions": {"tools.subagent": True}})
    subagent_service.clear()
    yield
    subagent_service.clear()


def _connection(db, name: str, *, one_model: bool = False) -> Connection:
    row = Connection(
        name=name,
        base_url="http://127.0.0.1:1",
        api_key_encrypted=encrypt(""),
        one_model_at_a_time=one_model,
    )
    db.add(row)
    db.commit()
    return row


def _model(db, connection, model_id: str, *, parallel=None, display: str = "") -> Model:
    row = Model(
        connection_id=connection.id,
        model_id=model_id,
        display_name=display or None,
        parallel=parallel,
        single_session=parallel == 1,
        capabilities_json={"tools": True},
    )
    db.add(row)
    db.commit()
    return row


def _user(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _chat(db, model: Model) -> Chat:
    chat = Chat(
        user_id=_user(db).id, title="t", model_id=model.model_id, connection_id=model.connection_id
    )
    db.add(chat)
    db.commit()
    return chat


def _say(db, chat: Chat, text: str, *, machine: bool = False) -> None:
    db.add(Message(chat_id=chat.id, role=ROLE_USER, content=text, machine=machine))
    db.commit()


# --- Named ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("use coder-30b for the search", True),
        ("Try CODER-30B.", True),
        ("coder-30b: go", True),
        ("coder-30b-instruct please", False),
        ("x/coder-30b", False),
        ("coder-30b.5 is different", False),
        ("the coder model", False),
        ("ask Big   Coder about it", True),
    ],
)
def test_a_name_counts_as_a_whole_word(text, named):
    assert rules.named_in([text], ["coder-30b", "Big Coder"]) is named


def test_only_the_persons_own_messages_count(db):
    main = _model(db, _connection(db, "A"), "lead-model", parallel=4)
    other = _model(db, _connection(db, "B"), "coder-30b")
    chat = _chat(db, main)
    _say(db, chat, "[Background job job1 finished:] coder-30b said hi", machine=True)
    db.add(Message(chat_id=chat.id, role="assistant", content="I could use coder-30b"))
    db.commit()
    owner = _user(db)
    assert rules.check(db, owner, chat, other) == rules.texts()["not_named"]
    _say(db, chat, "Send coder-30b to read the logs.")
    assert rules.check(db, owner, chat, other) == ""


# --- Capacity --------------------------------------------------------------------------
def test_the_logical_restrictions_and_no_fallback(db):
    words = rules.texts()
    swap = _connection(db, "Swap", one_model=True)
    lead = _model(db, swap, "lead-model", parallel=1)
    sibling = _model(db, swap, "sibling", display="Sibling")
    elsewhere = _model(db, _connection(db, "Far"), "far-model", parallel=1)
    chat = _chat(db, lead)
    owner = _user(db)
    _say(db, chat, "sibling and far-model may both help")
    # The chat's own model serves one request: refused, nothing picked instead.
    assert rules.check(db, owner, chat, None) == words["one_request"]
    # Another model on the same one-model server would unload the lead's.
    assert rules.check(db, owner, chat, sibling) == words["same_group"]
    # A named model on another server is fine, one request at a time or not.
    assert rules.check(db, owner, chat, elsewhere) == ""


def test_offered_where_any_model_could_serve(db):
    swap = _connection(db, "Swap", one_model=True)
    lead = _model(db, swap, "lead-model", parallel=1)
    _model(db, swap, "sibling")
    chat = _chat(db, lead)
    owner = _user(db)
    # Only its own one-model server: nothing could serve.
    assert not team_run.offered(db, chat, owner)
    assert rules.own_note(db, owner, chat) == ""
    # A model on another server: offered, and the setting says it needs naming.
    _model(db, _connection(db, "Far"), "far-model")
    assert team_run.offered(db, chat, owner)
    assert rules.own_note(db, owner, chat) == rules.setting_texts()["own"]
    names = {s["function"]["name"] for s in tools_service.resolve_tools(db, chat, owner).schemas}
    assert {"task", "team_add"} <= names


async def test_task_refuses_an_unnamed_model_with_the_specs_words(db):
    main = _model(db, _connection(db, "A"), "lead-model", parallel=4)
    _model(db, _connection(db, "B"), "coder-30b")
    chat = _chat(db, main)
    _say(db, chat, "Look into the logs.")
    context = tools_service.context_for(db, _user(db), chat)
    outcome = await subagent_service._run_subagent(
        context, {"prompt": "read the logs", "model": "B/coder-30b"}
    )
    assert outcome.content == rules.texts()["not_named"]
