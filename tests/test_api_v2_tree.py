"""`/api/v2`'s subagent tree (harness spec 3.4.0, subagents.md §11-§12): the
tree and the room, a subagent's conversation (read-only) and findings, and
stopping one -- for a chat on this server from its rows, and for an agent
chat from its device, or as it was kept while the device is away.
"""

from __future__ import annotations

from sqlalchemy import select

from lembas.db.models import Chat, Message, TeamAgent, TeamMessage, User
from lembas.db.models import Team as TeamRow
from lembas.security import api_tokens
from lembas.services import team_run
from tests.test_team import _chat, team_ready  # noqa: F401


def _bearer(db) -> dict[str, str]:
    user = db.scalar(select(User))
    _, raw = api_tokens.create(db, user, name="app", scopes=["app"])
    return {"Authorization": f"Bearer {raw}"}


def _seed(db, chat: Chat) -> None:
    """A tree as the runtime writes one: a sub-manager and its subagent with a
    finding, one that finished, and a room."""
    from datetime import UTC, datetime

    db.add(TeamRow(lead_chat_id=chat.id, goal="An API", seq=3,
                   data_json={"id_seq": 3}))
    for i, (name, manager, lead, state, left) in enumerate(
        [("area", "lead", True, "finished", None), ("routes", "area", False, "finished", None),
         ("scout", "lead", False, "done", datetime.now(UTC))], start=1,
    ):
        child = Chat(user_id=chat.user_id, title=name, model_id=chat.model_id,
                     connection_id=chat.connection_id, parent_chat_id=chat.id,
                     unattended=True, temporary=True)
        db.add(child)
        db.flush()
        db.add(TeamAgent(lead_chat_id=chat.id, name=name, manager=manager, role=f"owns {name}",
                         may_lead=lead, agent="general", model="Test/test-model",
                         chat_id=child.id, state=state, position=i, agent_id=f"s{i}",
                         task=f"Do {name}.", mode="edit", left=left,
                         findings_json=[{"n": 1, "text": f"{name} <b>found</b>", "time": 1.0}]))
        db.add(Message(chat_id=child.id, role="user", content=f"Do {name}.", complete=True,
                       machine=True))
        db.add(Message(chat_id=child.id, role="assistant", content=f"{name} done.",
                       complete=True))
    db.add(TeamMessage(lead_chat_id=chat.id, n=1, sender="routes", to_json=["area"],
                       recipients_json=["area"], text="Routes in.", report=False,
                       kind="message"))
    db.commit()


def test_a_chats_tree_room_transcript_findings_and_stop(client, db, team_ready):  # noqa: F811
    chat = _chat(db, team_ready)
    _seed(db, chat)
    headers = _bearer(db)
    got = client.get(f"/api/v2/chats/{chat.id}/tree", headers=headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["live"] is True
    assert [a["id"] for a in body["tree"]["agents"]] == ["lead", "s1", "s2"]
    assert body["tree"]["agents"][2]["parent"] == "area"
    assert [a["id"] for a in body["tree"]["ended"]] == ["s3"]
    assert body["room"][0]["kind"] == "message"
    assert client.get(f"/api/v2/chats/{chat.id}/tree?since=1", headers=headers).json()[
        "room"] == []

    said = client.get(f"/api/v2/chats/{chat.id}/subagents/s3/transcript", headers=headers)
    assert said.status_code == 200
    assert [t["role"] for t in said.json()["turns"]] == ["user", "assistant"]
    assert said.json()["turns"][1]["text"] == "scout done."
    found = client.get(f"/api/v2/chats/{chat.id}/subagents/s2/findings", headers=headers)
    assert found.json()["findings"][0]["text"] == "routes <b>found</b>"
    assert client.get(f"/api/v2/chats/{chat.id}/subagents/s9/findings",
                      headers=headers).status_code == 404

    stopped = client.post(f"/api/v2/chats/{chat.id}/subagents/s1/stop", headers=headers)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["stopped"] == ["s1", "s2"]
    # Stopped is ended: it cannot be stopped again.
    assert client.post(f"/api/v2/chats/{chat.id}/subagents/s1/stop",
                       headers=headers).status_code == 404
    team_run.clear()


def test_an_agent_chats_tree_is_the_one_kept_while_its_device_is_away(
    client, db, team_ready  # noqa: F811
):
    chat = _chat(db, team_ready)
    chat.kind = "agent"
    chat.team_tree_json = {"min": 1, "max": 2, "size": 2, "open_slots": 0,
                           "agents": [{"id": "lead", "name": "lead"}], "ended": []}
    db.commit()
    headers = _bearer(db)
    body = client.get(f"/api/v2/chats/{chat.id}/tree", headers=headers).json()
    assert body == {"tree": chat.team_tree_json, "room": [], "live": False}
    away = client.get(f"/api/v2/chats/{chat.id}/subagents/s1/transcript", headers=headers)
    assert away.status_code == 409
    assert "not linked" in away.json()["detail"]
