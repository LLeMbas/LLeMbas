"""What may appear in the sidebar's chat tree, and what may not.

The sidebar narrows on `Chat.kind`, and passes `""` whenever the Chat/Agent
switch is absent -- which is every instance with agent chats turned off. For as
long as there were exactly two kinds, "" meaning "no filter" and "" meaning
"both sides of the switch" were the same thing. They stopped being the same
thing the moment a third kind existed, and the difference is invisible until
somebody has a conversation that belongs to a section instead of to the tree.

That is the shape this file exists for: correct code whose meaning changed
underneath it. It is pinned in both places that do the narrowing, because they
are two implementations of one rule and only one of them is SQL.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from lembas.db.models import ALL_KINDS, KIND_TASK, KINDS, Chat, Folder, User


def _user(db) -> User:
    return db.scalars(select(User).order_by(User.created_at)).first()


def _chat(db, *, kind: str, folder: Folder | None = None) -> Chat:
    chat = Chat(user_id=_user(db).id, kind=kind, folder_id=folder.id if folder else None)
    db.add(chat)
    db.commit()
    return chat


# --- The vocabulary -----------------------------------------------------------
def test_kinds_stays_the_two_sided_switch():
    """`api/preferences.py:set_sidebar_kind` validates against KINDS, so a third
    entry makes the tree filterable to a side with no button to leave it -- the
    "one side of a fork nobody can move" failure `sidebar_split` already guards.
    New kinds go in ALL_KINDS.
    """
    assert KINDS == ("chat", "agent")
    assert set(ALL_KINDS) > set(KINDS)


def test_the_sidebar_switch_refuses_a_kind_that_is_not_a_side(
    client: TestClient, db, registered
):
    client.post("/api/preferences/sidebar-kind", data={"kind": KIND_TASK})
    stored = (_user(db).settings_json or {}).get("sidebar_kind")
    assert stored != KIND_TASK


# --- The two narrowings -------------------------------------------------------
def test_unfiled_sections_chats_stay_out_of_the_tree(client: TestClient, db, registered):
    """With no switch on screen the sidebar asks for "" -- and "" must not mean
    "everything". This is the SQL half, in `sidebar_context`."""
    from lembas.api.pages import sidebar_context

    ordinary = _chat(db, kind="chat")
    task = _chat(db, kind=KIND_TASK)

    listed = {c.id for c in sidebar_context(db, _user(db))["unfiled_chats"]}
    assert ordinary.id in listed
    assert task.id not in listed


def test_foldered_sections_chats_stay_out_of_the_tree(client: TestClient, db, registered):
    """And this is the Python half, in `Folder.visible_chats`. Two
    implementations of one rule, so both are pinned: fixing only the query would
    leave a task chat filed in a folder showing up anyway.
    """
    folder = Folder(user_id=_user(db).id, name="Work")
    db.add(folder)
    db.commit()

    ordinary = _chat(db, kind="chat", folder=folder)
    _chat(db, kind=KIND_TASK, folder=folder)

    listed = {c.id for c in folder.visible_chats()}
    assert listed == {ordinary.id}
    # And with the switch present it is still only the ordinary one.
    assert {c.id for c in folder.visible_chats("chat")} == {ordinary.id}


def test_a_folder_holding_only_a_task_chat_reads_as_empty(client: TestClient, db, registered):
    """`shown_in` keeps a folder that is empty of everything, because hiding a
    container somebody just made means it can never be filed into. A folder
    holding only a task chat has to count as that empty one -- otherwise it
    shows on both sides claiming contents nobody can see.
    """
    folder = Folder(user_id=_user(db).id, name="Scheduled work")
    db.add(folder)
    db.commit()
    _chat(db, kind=KIND_TASK, folder=folder)

    assert folder.holds() is False
    assert folder.shown_in("chat") is True


def test_the_composer_cannot_manufacture_a_section_chat(client: TestClient, db, registered):
    """`_new_chat` collapses kind to agent-or-chat, so this is already true by
    construction. Pinned so it stays true: the ordinary composer is a form
    anybody can post to."""
    from lembas.db.models import Connection, Model

    connection = Connection(name="local", base_url="http://x.test/v1", enabled=True)
    db.add(connection)
    db.commit()
    db.add(Model(connection_id=connection.id, model_id="m", display_name="M", enabled=True))
    db.commit()

    client.post("/api/chats/start", data={"content": "hello", "kind": KIND_TASK})
    kinds = {c.kind for c in db.scalars(select(Chat))}
    assert KIND_TASK not in kinds
