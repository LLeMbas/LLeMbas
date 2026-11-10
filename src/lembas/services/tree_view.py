"""A chat's subagent tree as a person reads it (harness spec 3.4.0, subagents.md
§11-§12): one answer for the page's activity panel and the apps' `/api/v2`.

Two sources, one shape. A chat on this server has its tree in its team's rows
(`services/team_run.py`, the rules' `tree_info`); an agent chat's tree is its
device's, asked for over the link while it is linked (`_lembas/team`) and kept
with the chat (`Chat.team_tree_json`) for when it is not. A subagent's
conversation and findings are read the same way: from its hidden chat and row
here, from the device there -- read-only either way: nothing here writes into a
subagent's session. Stopping one is the person's (§10).

Everything in it -- names, positions, tasks, findings, room messages, what a
subagent wrote -- is a model's words: text, escaped wherever it is shown.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from lembas.db.models import KIND_AGENT, Chat, TeamAgent
from lembas.services import team as rules


class OnTheDevice(Exception):
    """What was asked is kept on the device, which cannot answer now."""


class NotFound(Exception):
    """No subagent with that id in this chat's tree."""


def is_agent(chat: Chat) -> bool:
    return chat.kind == KIND_AGENT


def _room_entry(post: rules.Post) -> dict[str, Any]:
    return {
        "n": post.n,
        "from": post.sender,
        "to": list(post.to),
        "text": post.text,
        "report": post.report,
        "kind": post.kind or ("report" if post.report else "message"),
        "time": int(post.created * 1000) if post.created else None,
    }


def server_tree(db, chat: Chat) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """A chat's own tree and room, or (None, []) while it has no subagents and
    no open slots."""
    from lembas.services import team_run

    if not team_run.may_have_team(chat):
        return None, []
    team = team_run.load(db, chat.id)
    if team is None:
        return None, []
    if not team.members() and not team.ended and not rules.open_slots(team):
        return None, []
    return rules.tree_info(team), [_room_entry(p) for p in team.room]


async def tree(db, chat: Chat, since: int = 0) -> dict[str, Any]:
    """`{tree, room, live}`: the tree (None for none), the room's messages
    above `since`, and whether this is the device's answer now (`live`) or
    what was kept of it."""
    from lembas.services import device_link

    if not is_agent(chat):
        found, room = server_tree(db, chat)
        return {"tree": found, "room": [r for r in room if r["n"] > since], "live": True}
    kept = chat.team_tree_json or None
    if device_link.offers(chat.device_token_id, "tree") and chat.device_session_id:
        try:
            answer = await device_link.team_view(
                chat.device_token_id, chat.device_session_id, since
            )
            return {"tree": answer["tree"] or kept, "room": answer["room"], "live": True}
        except (device_link.DeviceError, device_link.LinkClosed, TimeoutError):
            pass
    return {"tree": kept, "room": [], "live": False}


def _row(db, chat: Chat, agent_id: str) -> TeamAgent:
    row = db.scalars(
        select(TeamAgent).where(TeamAgent.lead_chat_id == chat.id, TeamAgent.agent_id == agent_id)
    ).first()
    if row is None:
        raise NotFound(agent_id)
    return row


async def transcript(db, chat: Chat, agent_id: str) -> dict[str, Any]:
    """`{id, state, turns}`: a subagent's conversation so far, live or ended."""
    from lembas.services import device_link, jobs

    if is_agent(chat):
        try:
            return await device_link.team_transcript(
                chat.device_token_id, chat.device_session_id, agent_id
            )
        except device_link.DeviceError as exc:
            if "not found" in str(exc).lower() or "-32602" in str(exc):
                raise NotFound(agent_id) from exc
            raise OnTheDevice(str(exc)) from exc
        except (device_link.LinkClosed, TimeoutError) as exc:
            raise OnTheDevice(device_link.ON_THE_DEVICE) from exc
    row = _row(db, chat, agent_id)
    turns = jobs.chat_transcript(db, row.chat_id) if row.chat_id else []
    return {"id": agent_id, "state": row.state, "turns": turns}


async def findings(db, chat: Chat, agent_id: str) -> dict[str, Any]:
    """`{id, findings: [{n, text, time}]}`: a subagent's findings."""
    from lembas.services import device_link

    if is_agent(chat):
        try:
            return await device_link.team_findings(
                chat.device_token_id, chat.device_session_id, agent_id
            )
        except device_link.DeviceError as exc:
            if "not found" in str(exc).lower() or "-32602" in str(exc):
                raise NotFound(agent_id) from exc
            raise OnTheDevice(str(exc)) from exc
        except (device_link.LinkClosed, TimeoutError) as exc:
            raise OnTheDevice(device_link.ON_THE_DEVICE) from exc
    if agent_id == rules.LEAD:
        from lembas.services import team_run

        team = team_run.load(db, chat.id)
        found = list(team.lead.findings) if team is not None else []
    else:
        found = list(_row(db, chat, agent_id).findings_json or [])
    return {
        "id": agent_id,
        "findings": [
            {"n": int(f.get("n") or 0), "text": str(f.get("text") or ""),
             "time": int(float(f["time"]) * 1000) if f.get("time") else None}
            for f in found
            if isinstance(f, dict)
        ],
    }


async def stop(db, chat: Chat, agent_id: str) -> list[str]:
    """The person stops a subagent and its subtree (§10): the ids stopped."""
    from lembas.services import device_link, team_run

    if is_agent(chat):
        try:
            return await device_link.team_stop(
                chat.device_token_id, chat.device_session_id, agent_id
            )
        except device_link.DeviceError as exc:
            if "not live" in str(exc).lower() or "-32602" in str(exc):
                raise NotFound(agent_id) from exc
            raise OnTheDevice(str(exc)) from exc
        except (device_link.LinkClosed, TimeoutError) as exc:
            raise OnTheDevice(device_link.ON_THE_DEVICE) from exc
    row = _row(db, chat, agent_id)
    if row.left is not None:
        raise NotFound(agent_id)
    team = team_run.load(db, chat.id)
    ids = {}
    if team is not None:
        ids = {name: (team.get(name).id if team.get(name) else "")
               for name in team.subtree(row.name)}
    names = await team_run.person_stop(chat.id, row.name)
    return [ids.get(name) or name for name in names]


__all__ = ["NotFound", "OnTheDevice", "findings", "server_tree", "stop", "transcript", "tree"]
