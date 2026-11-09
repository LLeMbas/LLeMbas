"""What a chat's side panel shows: its context, its outputs, its background
agents and their room, and its plan.

One answer for the web page (`chat/_activity_*.html`) and the apps
(`/api/v2/chats/{chat}/activity`), so Desktop and Android draw the same panel
from the same facts. Read only: nothing here changes a chat, a job or a team.

- **Agents** are every background worker a chat has -- a team's members
  (harness spec 3.2.0, `services/team_run.py`), the `task` helpers and
  background commands it started (`services/jobs.py`), and a member's own
  helpers nested under it. Each says what it was asked to do, its state, how
  long it has run, its model, and its live progress: the latest tool call and
  the tail of what it is writing, read off the running generation.
- **Context** is the request the chat would send now, measured by category
  (the system prompt, its memory and instruction files, the tools, the
  messages), against the model's window, the compaction threshold and the last
  reply's real count.
- **Outputs** are the files this chat -- or any of its agents -- wrote, from
  the file versions that carry the chat's id.
- **Plan** is the chat's own plan, normalised.

Model output is untrusted here as everywhere: everything in these dicts is
text; the templates escape it, or put it through the markdown service.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from lembas.db.models import (
    JOB_DONE,
    JOB_ERROR,
    JOB_RUNNING,
    JOB_STOPPED,
    ROLE_ASSISTANT,
    ROLE_USER,
    Chat,
    Message,
    TeamAgent,
)
from lembas.services import jobs as jobs_service
from lembas.services import team as team_rules
from lembas.web.i18n import N_, t

# An agent's state as the panel says it, the same few words for a team's
# member, a helper and a command (the owner's list: queued, working, waiting,
# done, stopped, failed).
QUEUED = "queued"
WORKING = "working"
WAITING = "waiting"
DONE = "done"
STOPPED = "stopped"
ERROR = "error"
STATES = (QUEUED, WORKING, WAITING, DONE, STOPPED, ERROR)
ACTIVE = (QUEUED, WORKING, WAITING)

STATE_WORDS: dict[str, str] = {
    QUEUED: N_("queued"),
    WORKING: N_("working"),
    WAITING: N_("waiting"),
    DONE: N_("done"),
    STOPPED: N_("stopped"),
    ERROR: N_("failed"),
}

KIND_MEMBER = "member"
KIND_JOB = "job"

# How much of a live reply the panel shows, and how many outputs and stopped
# members it lists.
LIVE_TAIL = 400
BRIEF_CHARS = 600
OUTPUTS_MAX = 50
STOPPED_KEPT = 20
DIFF_BYTES_MAX = 512 * 1024


def state_word(state: str) -> str:
    return t(STATE_WORDS.get(str(state), str(state)))


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str:
    value = _utc(value)
    return value.isoformat() if value else ""


def _seconds(start: datetime | None, end: datetime | None) -> int:
    start, end = _utc(start), _utc(end) or datetime.now(UTC)
    if start is None:
        return 0
    return max(0, int((end - start).total_seconds()))


# --- Progress, read off a hidden chat ---------------------------------------------------
def _brief(db, chat_id: str) -> str:
    row = db.scalars(
        select(Message)
        .where(Message.chat_id == chat_id, Message.role == ROLE_USER)
        .order_by(Message.created_at, Message.id)
        .limit(1)
    ).first()
    text = (row.content or "").strip() if row is not None else ""
    return text[:BRIEF_CHARS] + ("…" if len(text) > BRIEF_CHARS else "")


def _step(event: dict[str, Any]) -> dict[str, Any]:
    from lembas.services import tool_blocks, tool_labels

    return {
        "tool": str(event.get("name") or ""),
        "label": tool_labels.label_for(event),
        "title": (tool_blocks.block_for(event).title or str(event.get("query") or ""))[:200],
        "error": str(event.get("status") or "") == "error",
        "running": str(event.get("status") or "") in ("running", "pending"),
    }


def progress(db, chat_id: str | None) -> dict[str, Any]:
    """An agent's latest step and the tail of what it is writing: from the
    running generation while its turn runs, else from its last reply."""
    from lembas.services import generation as generation_service

    out: dict[str, Any] = {"step": None, "text": "", "steps": 0, "tools": 0}
    if not chat_id:
        return out
    out["steps"], out["tools"] = jobs_service.counts(db, chat_id)
    live = generation_service.running_for(chat_id)
    if live is not None:
        events = [e for e in live.tool_events if isinstance(e, dict)]
        text = "".join(live.content)
    else:
        row = db.scalars(
            select(Message)
            .where(Message.chat_id == chat_id, Message.role == ROLE_ASSISTANT)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        ).first()
        events = [e for e in (row.tool_calls_json or []) if isinstance(e, dict)] if row else []
        text = (row.content or "") if row is not None else ""
    if events:
        out["step"] = _step(events[-1])
    text = text.strip()
    out["text"] = ("…" + text[-LIVE_TAIL:]) if len(text) > LIVE_TAIL else text
    return out


def chat_transcript(db, chat_id: str) -> list[dict[str, Any]]:
    """A hidden chat's conversation in the helper panel's shape
    (`jobs.transcript`): what a member or a helper was told and did."""
    return jobs_service.chat_transcript(db, chat_id)


# --- The agents --------------------------------------------------------------------------
def _job_state(state: str) -> str:
    return {
        JOB_RUNNING: WORKING,
        JOB_DONE: DONE,
        JOB_STOPPED: STOPPED,
        JOB_ERROR: ERROR,
    }.get(str(state), ERROR)


def _job_entry(db, job, depth: int, manager: str) -> dict[str, Any]:
    info = jobs_service.info(db, job)
    running = job.state == JOB_RUNNING
    entry = {
        "kind": KIND_JOB,
        # A member's own helper is `<member>:<job id>`: job ids are numbered
        # per chat, so a member's first helper and the lead's share theirs.
        "id": job.job_id if manager == team_rules.LEAD else f"{manager}:{job.job_id}",
        "type": job.kind,
        "name": job.job_id,
        "role": job.description or "",
        "brief": "",
        "state": _job_state(job.state),
        "seconds": info["seconds"],
        "started": info["started"],
        "model": job.model or "",
        "agent": job.agent or "",
        "manager": manager,
        "depth": depth,
        "lead": False,
        "step": None,
        "text": "",
        "steps": info["steps"],
        "tools": info["tools"],
        "stoppable": running,
        "transcript": bool(job.child_chat_id),
        "children": [],
    }
    if job.child_chat_id:
        entry["brief"] = _brief(db, job.child_chat_id)
        live = progress(db, job.child_chat_id)
        entry.update(step=live["step"], text=live["text"])
    elif job.kind == jobs_service.KIND_COMMAND:
        data = job.data_json or {}
        entry["brief"] = str(data.get("command") or job.description or "")[:BRIEF_CHARS]
    return entry


def _member_state(row: TeamAgent, ended_badly: bool) -> str:
    if row.left is not None:
        return STOPPED
    if row.state == team_rules.WORKING:
        return WORKING
    if row.state == team_rules.WAITING:
        return QUEUED
    return ERROR if ended_badly else DONE


def _ended_badly(db, chat_id: str | None) -> bool:
    if not chat_id:
        return False
    row = db.scalars(
        select(Message)
        .where(Message.chat_id == chat_id, Message.role == ROLE_ASSISTANT)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).first()
    return bool(row is not None and row.error)


def _member_entry(db, row: TeamAgent, depth: int, now: datetime) -> dict[str, Any]:
    working = row.left is None and row.state == team_rules.WORKING
    end = None if working else (row.last_ended or row.left)
    state = _member_state(row, _ended_badly(db, row.chat_id))
    live = progress(db, row.chat_id)
    return {
        "kind": KIND_MEMBER,
        "id": row.name,
        "type": "member",
        "name": row.name,
        "role": row.role,
        "brief": _brief(db, row.chat_id) if row.chat_id else "",
        "state": state,
        "seconds": _seconds(row.run_started or row.created_at, end or now),
        "started": _iso(row.run_started or row.created_at),
        "model": row.model,
        "agent": row.agent,
        "manager": row.manager,
        "depth": depth,
        "lead": bool(row.may_lead),
        "step": live["step"],
        "text": live["text"],
        "steps": live["steps"],
        "tools": live["tools"],
        "stoppable": row.left is None,
        "transcript": bool(row.chat_id),
        "children": [],
    }


def agents(db, chat: Chat) -> list[dict[str, Any]]:
    """Every background agent of a chat on this server, as a forest: the
    team's members in tree order (each with its own helpers under it), then
    the chat's own helpers and commands, then members that were stopped."""
    from lembas.services import team_run

    now = datetime.now(UTC)
    out: list[dict[str, Any]] = []
    rows = list(
        db.scalars(
            select(TeamAgent)
            .where(TeamAgent.lead_chat_id == chat.id)
            .order_by(TeamAgent.position)
        )
    )
    present = {r.name: r for r in rows if r.left is None}
    view = team_run.snapshot(db, chat.id) if present else None
    if view is not None:
        stack: list[dict[str, Any]] = []
        for agent in view["agents"]:
            row = present.get(agent["name"])
            if row is None:
                continue
            entry = _member_entry(db, row, agent["depth"] - 1, now)
            if row.chat_id:
                entry["children"] = [
                    _job_entry(db, job, entry["depth"] + 1, row.name)
                    for job in jobs_service.for_chat(db, row.chat_id)
                ]
            while stack and stack[-1]["depth"] >= entry["depth"]:
                stack.pop()
            (stack[-1]["children"] if stack else out).append(entry)
            stack.append(entry)
    own = jobs_service.for_chat(db, chat.id)
    out.extend(_job_entry(db, job, 0, team_rules.LEAD) for job in own)
    gone = [r for r in rows if r.left is not None][-STOPPED_KEPT:]
    out.extend(_member_entry(db, r, 0, now) for r in reversed(gone))
    return out


def device_agents(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A device chat's jobs (`_lembas/job/list`) in the same shape: a device
    reports no live progress, so `step` and `text` stay empty."""
    out = []
    for job in jobs:
        running = job.get("state") == JOB_RUNNING
        out.append(
            {
                "kind": KIND_JOB,
                "id": job.get("id", ""),
                "type": job.get("kind", ""),
                "name": job.get("id", ""),
                "role": job.get("description", ""),
                "brief": job.get("command", ""),
                "state": _job_state(str(job.get("state") or "")),
                "seconds": int(job.get("seconds") or 0),
                "started": job.get("started", ""),
                "model": job.get("model", ""),
                "agent": job.get("agent", ""),
                "manager": team_rules.LEAD,
                "depth": 0,
                "lead": False,
                "step": None,
                "text": "",
                "steps": int(job.get("steps") or 0),
                "tools": int(job.get("tools") or 0),
                "stoppable": running,
                "transcript": job.get("kind") == jobs_service.KIND_SUBAGENT,
                "children": [],
            }
        )
    return out


def flatten(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in entries:
        out.append(entry)
        out.extend(flatten(entry.get("children") or []))
    return out


def summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    """How many agents are in each state, across the whole forest."""
    counts = dict.fromkeys(STATES, 0)
    for entry in flatten(entries):
        counts[entry["state"]] = counts.get(entry["state"], 0) + 1
    counts["active"] = sum(counts[s] for s in ACTIVE)
    counts["finished"] = counts[DONE] + counts[STOPPED] + counts[ERROR]
    counts["total"] = counts["active"] + counts["finished"]
    return counts


def summary_text(counts: dict[str, int]) -> str:
    """'3 agents working · 1 queued · 2 done', in the reader's language."""
    parts = []
    if counts.get(WORKING) == 1:
        parts.append(t("1 agent working"))
    elif counts.get(WORKING):
        parts.append(t("%(n)s agents working", n=counts[WORKING]))
    if counts.get(QUEUED):
        parts.append(t("%(n)s queued", n=counts[QUEUED]))
    if counts.get(WAITING):
        parts.append(t("%(n)s waiting", n=counts[WAITING]))
    if counts.get("finished"):
        parts.append(t("%(n)s finished", n=counts["finished"]))
    return " · ".join(parts)


def find(entries: list[dict[str, Any]], kind: str, ident: str) -> dict[str, Any] | None:
    return next(
        (e for e in flatten(entries) if e["kind"] == kind and str(e["id"]) == str(ident)), None
    )


def fingerprint(payload: Any) -> str:
    """A short hash of what the panel would show, so a stream sends a frame
    only when something moved -- elapsed seconds left out, or every second
    would be a change."""

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k != "seconds"}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    raw = json.dumps(strip(payload), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# --- Context -----------------------------------------------------------------------------
CONTEXT_KEYS = (
    "messages",
    "system",
    "memory",
    "instructions",
    "tools",
    "mcp_tools",
    "since",
    "buffer",
    "free",
)
CONTEXT_WORDS: dict[str, str] = {
    "messages": N_("Messages"),
    "system": N_("System prompt"),
    "memory": N_("Memory and personal"),
    "instructions": N_("Instruction files"),
    "tools": N_("Tools"),
    "mcp_tools": N_("MCP tools"),
    "since": N_("Since the last reply"),
    "buffer": N_("Compaction buffer"),
    "free": N_("Free space"),
}


def context_word(key: str) -> str:
    return t(CONTEXT_WORDS.get(key, key))


def context(db, chat: Chat, user) -> dict[str, Any]:
    """The request this chat would send now, by category, against the
    model's window. Estimates (`services/tokens.py`) where the last reply's
    real count is not there; `measured` says which the total is."""
    from lembas.services import chat as chat_service
    from lembas.services import compaction, metrics, prefix, tokens
    from lembas.services import tools as tools_service
    from lembas.services.tools import FAMILY_MCP, gate_of

    toolset = tools_service.resolve_tools(db, chat, user)
    schemas = toolset.schemas
    payload = chat_service.build_request(db, chat, tools=schemas, user=user)
    messages = payload.get("messages") or []
    system_text = ""
    if messages and messages[0].get("role") == "system":
        system_text = str(messages[0].get("content") or "")
        messages = messages[1:]
    speaker = chat_service.speaker_for(db, chat)
    facts = prefix.facts(db, chat, user, speaker, schemas)
    memory = tokens.estimate((facts.memory or "") + (facts.personal or ""))
    instructions = sum(tokens.estimate(text) for text in (facts.instructions or {}).values())
    system_all = tokens.estimate(system_text)
    mcp_defs = [d for d in toolset.defs if gate_of(d.family) == FAMILY_MCP]
    mcp = sum(tokens.estimate(json.dumps(d.schema)) for d in mcp_defs)
    tools_all = sum(tokens.estimate(json.dumps(d.schema)) for d in toolset.defs)
    parts = {
        "messages": tokens.estimate_messages(messages),
        "system": max(0, system_all - memory - instructions),
        "memory": min(memory, system_all),
        "instructions": min(instructions, system_all),
        "tools": max(0, tools_all - mcp),
        "mcp_tools": mcp,
    }
    estimated = sum(parts.values())

    last = compaction.last_complete(db, chat)
    usage = metrics.from_message(last.usage_json if last is not None else None)
    model = chat_service.model_row(db, speaker)
    limit = int(getattr(model, "context_length", 0) or 0) or usage.context_limit
    measured = usage.context_tokens > 0 and not usage.estimated
    since = 0
    if measured and last is not None:
        newer = db.scalars(
            select(Message).where(
                Message.chat_id == chat.id,
                Message.created_at > last.created_at,
            )
        ).all()
        since = sum(tokens.estimate(m.content or "") for m in newer)
    used = (usage.context_tokens + since) if measured else estimated
    if measured:
        # The categories are estimates; scaled so they add up to the real
        # count, which is the one the model saw.
        scale = usage.context_tokens / estimated if estimated else 0
        parts = {k: int(round(v * scale)) for k, v in parts.items()}
    parts["since"] = since
    threshold = compaction.threshold(db)
    buffer = int(limit * (100 - threshold) / 100) if limit and 0 < threshold < 100 else 0
    parts["buffer"] = buffer
    parts["free"] = max(0, limit - used - buffer) if limit else 0
    rows = []
    for key in CONTEXT_KEYS:
        value = int(parts.get(key) or 0)
        if key in ("buffer", "free") and not limit:
            continue
        rows.append(
            {
                "key": key,
                "tokens": value,
                "percent": round(value * 100 / limit, 1) if limit else None,
            }
        )
    return {
        "used": int(used),
        "limit": int(limit),
        "percent": round(used * 100 / limit, 1) if limit else None,
        "measured": bool(measured),
        "threshold": threshold,
        "tools_count": len(toolset.defs),
        "mcp_count": len(mcp_defs),
        "categories": rows,
    }


# --- Outputs -----------------------------------------------------------------------------
def _chat_ids(db, chat: Chat) -> list[str]:
    """The chat and every hidden chat its agents work in."""
    from lembas.db.models import ChatJob

    ids = [chat.id]
    ids += [
        r.chat_id
        for r in db.scalars(select(TeamAgent).where(TeamAgent.lead_chat_id == chat.id))
        if r.chat_id
    ]
    frontier = list(ids)
    while frontier:
        found = [
            j.child_chat_id
            for j in db.scalars(select(ChatJob).where(ChatJob.chat_id.in_(frontier)))
            if j.child_chat_id and j.child_chat_id not in ids
        ]
        ids += found
        frontier = found
    return ids


def job_owner(db, chat: Chat, job_id: str):
    """(the chat a job belongs to, the job) for a job of this chat, or of one
    of its members (`<member>:<job id>`, as `agents` names it), or None."""
    from lembas.services import team_run

    owner_id = chat.id
    if ":" in job_id:
        name, job_id = job_id.split(":", 1)
        row = team_run.member_any(db, chat.id, name)
        if row is None or not row.chat_id:
            return None
        owner_id = row.chat_id
    job = jobs_service.by_id(db, owner_id, job_id)
    return (owner_id, job) if job is not None else None


def _lines(db, digest: str | None, size: int) -> list[str] | None:
    from lembas.services import blobs

    if not digest:
        return []
    if size > DIFF_BYTES_MAX:
        return None
    try:
        return blobs.read_bytes(db, digest).decode("utf-8").splitlines()
    except Exception:  # not text, or a blob that cannot be read
        return None


def outputs(db, chat: Chat) -> list[dict[str, Any]]:
    """The files this chat and its agents wrote, newest first: the path, how
    many writes, and the lines added and removed across them where the
    file is text (None where it is not, or too big to compare)."""
    from urllib.parse import quote

    from lembas.db.models import Node, NodeVersion

    ids = _chat_ids(db, chat)
    versions = list(
        db.scalars(
            select(NodeVersion).where(NodeVersion.chat_id.in_(ids)).order_by(NodeVersion.at)
        )
    )
    by_node: dict[str, list] = {}
    for version in versions:
        by_node.setdefault(version.node_id, []).append(version)
    out = []
    for node_id, mine in by_node.items():
        node = db.get(Node, node_id)
        if node is None or node.trashed_at is not None:
            continue
        first, last = mine[0], mine[-1]
        before = db.scalars(
            select(NodeVersion)
            .where(NodeVersion.node_id == node_id, NodeVersion.at < first.at)
            .order_by(NodeVersion.at.desc())
            .limit(1)
        ).first()
        old = _lines(db, before.blob_hash, before.size) if before is not None else []
        new = _lines(db, last.blob_hash, last.size)
        added = removed = None
        if old is not None and new is not None:
            added = removed = 0
            for line in difflib.ndiff(old, new):
                if line.startswith("+ "):
                    added += 1
                elif line.startswith("- "):
                    removed += 1
        out.append(
            {
                "path": node.path,
                "name": node.path.rsplit("/", 1)[-1] or node.path,
                "writes": len(mine),
                "created": before is None,
                "added": added,
                "removed": removed,
                "at": _iso(last.at),
                "url": f"/files/view?path={quote(node.path)}",
            }
        )
    out.sort(key=lambda o: o["at"], reverse=True)
    return out[:OUTPUTS_MAX]


# --- Everything ---------------------------------------------------------------------------
async def chat_agents(db, chat: Chat) -> list[dict[str, Any]]:
    """The agents of any chat: from the device for an agent chat, else here."""
    if chat.device_token_id:
        from lembas.services import device_link

        try:
            raw = await device_link.list_jobs(chat.device_token_id, chat.device_session_id)
        except (device_link.DeviceError, device_link.LinkClosed, TimeoutError):
            return []
        return device_agents(raw)
    return agents(db, chat)


def room(db, chat: Chat, since: int = 0) -> dict[str, Any] | None:
    """The team's head and its room since `since`, or None without a team."""
    from lembas.services import team_run

    if not team_run.may_have_team(chat) or not team_run.has_team(db, chat.id):
        return None
    view = team_run.snapshot(db, chat.id, since)
    if view is None:
        return None
    return {
        "min": view["min"],
        "max": view["max"],
        "size": view["size"],
        "goal": view["goal"],
        "room": view["room"],
        "cursor": view["cursor"],
    }


def plan(chat: Chat) -> dict[str, Any] | None:
    from lembas.services import plans

    return plans.normalise(chat.plan) if chat.plan else None


async def snapshot(
    db, chat: Chat, user, *, since: int = 0, with_context: bool = True
) -> dict[str, Any]:
    """The whole panel, as data."""
    from lembas.services import team_run

    entries = await chat_agents(db, chat)
    counts = summary(entries)
    team = room(db, chat, since)
    out: dict[str, Any] = {
        "summary": counts,
        "agents": entries,
        "team": team,
        "team_offered": team_run.offered(db, chat, user) if team_run.may_have_team(chat) else False,
        "outputs": outputs(db, chat) if not chat.device_token_id else [],
        "plan": plan(chat),
        "context": context(db, chat, user) if with_context else None,
    }
    out["version"] = fingerprint(
        {"agents": entries, "team": team, "plan": out["plan"], "outputs": out["outputs"]}
    )
    out["at"] = time.time()
    return out


__all__ = [
    "ACTIVE",
    "KIND_JOB",
    "KIND_MEMBER",
    "STATES",
    "agents",
    "chat_agents",
    "chat_transcript",
    "context",
    "context_word",
    "device_agents",
    "find",
    "fingerprint",
    "flatten",
    "job_owner",
    "outputs",
    "plan",
    "progress",
    "room",
    "snapshot",
    "state_word",
    "summary",
    "summary_text",
]
