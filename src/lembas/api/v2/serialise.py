"""The app's JSON shapes: pure functions over the rows and the `Generation`.

They read exactly what the page's renderers read -- `steps.for_message`,
`steps.closed_from`, `steps.tail`, `tool_blocks.block_for`, the
`interaction.Interruption` on the generation, `plans.normalise`,
`metrics.from_generation` -- and turn it into JSON instead of HTML. One
producer, two renderings: the page and the app agree on what step N of a reply
is because both number it from the same marks.

**Markdown, never HTML.** A text step carries the model's text as written; the
app renders and sanitises it itself, by the rules the server applies before
HTML (no remote images, no script, links opened outside). Model output is
untrusted on a phone exactly as on a page.

`tests/fixtures/api_v2/` holds one example of every shape, written by
`tests/test_api_v2_contract.py`; the app pins those files and parses them in its
own tests. A change here that fails that test is a change to the contract.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from lembas.db.models import Chat, Message
from lembas.services import interaction, tool_blocks
from lembas.services import metrics as metrics_service
from lembas.services import steps as steps_service

# What a tool event may carry to the app. A list, not the dict as stored: an
# event is whatever the tool wrote, and a field nobody chose to publish should
# not become part of a contract by accident.
TOOL_FIELDS = (
    "id",
    "name",
    "kind",
    "status",
    "args",
    "query",
    "purpose",
    "why",
    "text",
    "error",
    "detail",
    "data",
    "results",
    "diff",
    "exit",
    "hits",
    "path",
    "edited",
    "truncated",
    "image",
    "job",
    "children",
    "show",
)

# What a `show` card carries to the app: the file's identity, never its text
# (the app fetches that from `GET /api/v2/chats/{chat}/show`). A device's card
# adds `source` and `shown_id`; the page's own `url` is the cookie door's and
# is left out.
SHOW_FIELDS = (
    "path", "name", "kind", "language", "line", "node_id", "version_id", "sha", "size", "mime",
    "source", "shown_id",
)

# A string longer than this is cut, and the event says so. A shell's output can
# run to a megabyte and the app shows a screenful behind a tap; the whole of it
# is one `GET` of the page away for anyone who needs it.
MAX_FIELD_CHARS = 32_000


def stamp(value: datetime | None) -> str | None:
    """ISO 8601 with an offset. SQLite hands back naive datetimes that are UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _plain(value: Any) -> Any:
    """Anything JSON can carry: a round trip, with `str` for the rest."""
    return json.loads(json.dumps(value, default=str))


def _cut(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS], True
    return value, False


# --- Chats -----------------------------------------------------------------------
def agents(row: Chat, cap: int | None = None) -> dict[str, int]:
    """The chat's team size (harness spec 3.2.0, team.md §2), as it is read:
    the spec's defaults on an older row, narrowed by the instance's cap."""
    from lembas.services import team as team_rules

    limit = cap if cap is not None else team_rules.defaults()[2]
    low, high = team_rules.read_setting(
        getattr(row, "agents_min", None), getattr(row, "agents_max", None), limit
    )
    return {"min": low, "max": high}


def chat(row: Chat, *, running: bool = False, cap: int | None = None) -> dict[str, Any]:
    params = row.params_json or {}
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "model": row.model_id,
        "folder_id": row.folder_id,
        "pinned": bool(row.pinned),
        "archived": bool(row.archived),
        "temporary": bool(row.temporary),
        "unread": bool(row.unread),
        "running": running,
        # A chat's mode is "" or "plan"; an agent chat's, one of the four.
        "mode": row.agent_mode or "",
        "effort": params.get("reasoning_effort") or None,
        "device": (
            {"token_id": row.device_token_id, "directory": row.project_dir or ""}
            if row.device_token_id
            else None
        ),
        "parent_chat_id": row.parent_chat_id,
        # Added within APP_API 2: the team's size setting.
        "agents": agents(row, cap),
        "created_at": stamp(row.created_at),
        "updated_at": stamp(row.updated_at),
    }


def team(view: dict[str, Any] | None) -> dict[str, Any]:
    """A chat's team for the apps (`GET /chats/{chat}/team`): whether one is
    offered, the setting, the tree in tree order and the room since a cursor.
    A room message's `text` is a model's: an app renders it as untrusted
    markdown, as it does a reply."""
    if view is None:
        return {"offered": False, "agents": [], "room": [], "cursor": 0}
    return {
        "offered": bool(view["offered"]),
        "min": int(view["min"]),
        "max": int(view["max"]),
        "size": int(view["size"]),
        "parallel": view["parallel"],
        "goal": view["goal"],
        "agents": [
            {
                "name": a["name"],
                "manager": a["manager"],
                "role": a["role"],
                "state": a["state"],
                "model": a["model"],
                "agent": a["agent"],
                "lead": bool(a["lead"]),
                "depth": int(a["depth"]),
            }
            for a in view["agents"]
        ],
        "room": [
            {
                "n": int(m["n"]),
                "from": m["from"],
                "to": list(m["to"]),
                "text": m["text"],
                "report": bool(m["report"]),
                "created": m["created"],
            }
            for m in view["room"]
        ],
        "cursor": int(view["cursor"]),
    }


def agent(entry: dict[str, Any]) -> dict[str, Any]:
    """One background agent of the activity panel (`services/activity.py`):
    a team's member (`kind` member, `id` its name) or a job (`kind` job, `id`
    the job's id; `type` subagent or command), with its own helpers under it.
    `brief`, `role` and `text` are a model's words: an app shows them as
    text."""
    step = entry.get("step")
    return {
        "kind": entry["kind"],
        "id": str(entry["id"]),
        "type": entry.get("type") or "",
        "name": entry["name"],
        "role": entry.get("role") or "",
        "brief": entry.get("brief") or "",
        "state": entry["state"],
        "seconds": int(entry.get("seconds") or 0),
        "started": entry.get("started") or "",
        "model": entry.get("model") or "",
        "agent": entry.get("agent") or "",
        "manager": entry.get("manager"),
        "depth": int(entry.get("depth") or 0),
        "lead": bool(entry.get("lead")),
        "step": (
            {
                "tool": step.get("tool") or "",
                "label": step.get("label") or "",
                "title": step.get("title") or "",
                "error": bool(step.get("error")),
                "running": bool(step.get("running")),
            }
            if isinstance(step, dict)
            else None
        ),
        "text": entry.get("text") or "",
        "steps": int(entry.get("steps") or 0),
        "tools": int(entry.get("tools") or 0),
        "stoppable": bool(entry.get("stoppable")),
        "transcript": bool(entry.get("transcript")),
        "children": [agent(child) for child in entry.get("children") or []],
    }


def activity(view: dict[str, Any]) -> dict[str, Any]:
    """`GET /chats/{chat}/activity`: what the web's activity panel shows --
    the agents (a forest), a summary of their states, the team's head and
    room since a cursor, the context by category, the files written, the
    plan -- and `version`, which changes whenever any of it (but the elapsed
    seconds) does."""
    team_view = view.get("team")
    ctx = view.get("context")
    return {
        "version": view["version"],
        "summary": {k: int(v) for k, v in view["summary"].items()},
        "agents": [agent(e) for e in view["agents"]],
        "team": (
            {
                "min": int(team_view["min"]),
                "max": int(team_view["max"]),
                "size": int(team_view["size"]),
                "goal": team_view["goal"],
                "room": [
                    {
                        "n": int(m["n"]),
                        "from": m["from"],
                        "to": list(m["to"]),
                        "text": m["text"],
                        "report": bool(m["report"]),
                        "created": m["created"],
                    }
                    for m in team_view["room"]
                ],
                "cursor": int(team_view["cursor"]),
            }
            if team_view
            else None
        ),
        "team_offered": bool(view.get("team_offered")),
        "context": (
            {
                "used": int(ctx["used"]),
                "limit": int(ctx["limit"]),
                "percent": ctx["percent"],
                "measured": bool(ctx["measured"]),
                "threshold": int(ctx["threshold"]),
                "tools_count": int(ctx["tools_count"]),
                "mcp_count": int(ctx["mcp_count"]),
                "categories": [
                    {"key": c["key"], "tokens": int(c["tokens"]), "percent": c["percent"]}
                    for c in ctx["categories"]
                ],
            }
            if ctx
            else None
        ),
        "outputs": [
            {
                "path": o["path"],
                "name": o["name"],
                "writes": int(o["writes"]),
                "created": bool(o["created"]),
                "added": o["added"],
                "removed": o["removed"],
                "at": o["at"],
            }
            for o in view.get("outputs") or []
        ],
        "plan": view.get("plan"),
    }


def chat_detail(row: Chat, *, running: bool = False, cap: int | None = None) -> dict[str, Any]:
    """A chat with what its settings sheet shows."""
    from lembas.services import plans

    return {
        **chat(row, running=running, cap=cap),
        "system_prompt": row.system_prompt or "",
        "plan": plans.normalise(row.plan) if row.plan else None,
    }


# --- Messages and their steps ------------------------------------------------------
def tool_call(event: dict) -> dict[str, Any]:
    block = tool_blocks.block_for(event)
    out: dict[str, Any] = {}
    cut = False
    for key in TOOL_FIELDS:
        if key == "show":
            card = event.get("show")
            if isinstance(card, dict) and card:
                out["show"] = {k: card[k] for k in SHOW_FIELDS if k in card}
            continue
        if key in event and event[key] not in (None, ""):
            value, was_cut = _cut(event[key])
            cut = cut or was_cut
            out[key] = value
    if cut:
        out["truncated"] = True
    out["block"] = {
        "kind": block.kind,
        "title": block.title,
        "chips": list(block.chips),
        "added": block.added,
        "removed": block.removed,
    }
    return _plain(out)


def step(item: steps_service.Step) -> dict[str, Any]:
    out: dict[str, Any] = {"index": item.index, "kind": item.kind, "open": item.open}
    if item.kind == steps_service.KIND_THINKING:
        out["text"] = item.text
        out["label"] = item.label
    elif item.kind == steps_service.KIND_TEXT:
        out["markdown"] = item.markdown
    elif item.kind == steps_service.KIND_TOOLS:
        out["calls"] = [tool_call(e) for e in item.events]
    elif item.kind == steps_service.KIND_STEER:
        out["messages"] = [{"id": e.get("id", ""), "text": e.get("text", "")} for e in item.events]
    return out


def attachment(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.filename,
        "mime": row.media_type or "",
        "size": int(row.size_bytes or 0),
        "kind": row.kind,
        "image": bool(getattr(row, "is_image", False)),
        "width": int(row.width or 0),
        "height": int(row.height or 0),
    }


def message(row: Message) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "role": row.role,
        "created_at": stamp(row.created_at),
        "complete": bool(row.complete),
        "stopped": bool(row.stopped),
        "error": row.error or None,
        "queued": bool(row.queued),
        "machine": bool(row.machine),
        "origin": row.origin or "",
        "model": row.model_id or None,
        "attachments": [attachment(a) for a in row.attachments],
    }
    if row.role == "assistant":
        out["steps"] = [step(s) for s in steps_service.for_message(row)] if row.complete else []
        out["plan"] = row.plan if row.plan_json else None
        out["metrics"] = metrics(metrics_service.from_message(row.usage_json))
    else:
        # The person's own words are not model output, and were never
        # rendered as markdown; they travel as they were typed.
        out["text"] = row.content or ""
    return out


def metrics(value: metrics_service.Metrics) -> dict[str, Any]:
    return {
        **metrics_service.to_json(value),
        "percent": value.percent,
        "tok_s": round(value.tokens_per_second, 1),
    }


# --- The live reply ------------------------------------------------------------------
def live_steps(generation: Any) -> list[dict[str, Any]]:
    """Every closed step of a running reply -- the whole prefix, every time."""
    return [step(s) for s in steps_service.closed_from(generation, since=0)]


def tail(generation: Any, label: str) -> dict[str, Any]:
    thinking, text = steps_service.tail(generation)
    return {"thinking": thinking, "text": text, "label": label}


def plan(value: dict | list | None) -> dict[str, Any] | None:
    from lembas.services import plans

    if value is None:
        return None
    if isinstance(value, list):
        value = plans.from_todos(value)
    return plans.normalise(value)


def item(entry: interaction.Item) -> dict[str, Any]:
    return _plain(
        {
            "index": entry.index,
            "key": entry.key,
            "kind": entry.kind,
            "tool": entry.tool_name,
            "title": entry.title,
            "header": entry.header,
            "detail": entry.detail,
            "reason": entry.reason,
            "purpose": entry.purpose,
            "options": [
                {
                    "label": o.label,
                    "description": o.description,
                    "recommended": o.recommended,
                }
                for o in entry.options
            ],
            "multiple": entry.multiple,
            "free_text": entry.allow_free_text,
            "editable": entry.editable,
            "plan": plan(entry.plan) if entry.plan else None,
            "mode_choice": entry.mode_choice,
            "job": entry.job,
        }
    )


def interruption(card: interaction.Interruption, *, now: float | None = None) -> dict[str, Any]:
    """One card. `expires_in` in seconds rather than a time: the deadline is on
    this process's monotonic clock, which means nothing on a phone."""
    moment = now if now is not None else time.monotonic()
    kinds = {i.kind for i in card.items}
    kind = (
        interaction.KIND_PLAN
        if interaction.KIND_PLAN in kinds
        else interaction.KIND_APPROVAL
        if interaction.KIND_APPROVAL in kinds
        else interaction.KIND_QUESTION
    )
    return {
        "id": card.id,
        "kind": kind,
        "why": card.why,
        "dismissable": card.dismissable,
        "expires_in": max(0, round(card.expires_at - moment)) if card.expires_at else None,
        "items": [item(i) for i in card.items],
    }


def asks(waiting: list | None) -> list[dict[str, Any]]:
    return [interruption(card) for card in (waiting or [])]


def jobs(value: dict | None) -> list[dict[str, Any]]:
    return _plain(list((value or {}).values()))


# --- Files --------------------------------------------------------------------------
# A directory entry and an opened file, as `GET /files` and `GET /files/content`
# return them (`api/v2/files.py`). What the explorer shows, from the same rows.
WITHHELD_TOO_LARGE = "too_large"
WITHHELD_NOT_TEXT = "not_text"


def file_entry(row: Any) -> dict[str, Any]:
    """One `file_ui.Row`: `kind` is `file`, `dir` or `mount`."""
    return {
        "name": row.name,
        "path": row.path,
        "kind": row.kind,
        "size": int(row.size or 0),
        "mime": row.mime or "",
        "updated_at": stamp(row.updated_at),
    }


def file_listing(path: str, rows: list[Any]) -> dict[str, Any]:
    parent = None if path == "/" else (path.rsplit("/", 1)[0] or "/")
    return {"path": path, "parent": parent, "entries": [file_entry(row) for row in rows]}


def file_content(opened: Any, withheld: dict[str, str] | None = None) -> dict[str, Any]:
    """One `file_ui.Opened`. `text` is the file as written -- an HTML file's
    source, never rendered -- or empty, with `withheld` saying why."""
    out: dict[str, Any] = {
        "path": opened.path,
        "name": opened.name,
        "size": int(opened.size or 0),
        "mime": opened.mime or "",
        "kind": opened.kind,
        "language": opened.language or "",
        "sha": opened.sha or "",
        "text": "" if withheld else opened.text,
    }
    if withheld:
        out["withheld"] = withheld
    return out


__all__ = [
    "WITHHELD_NOT_TEXT",
    "WITHHELD_TOO_LARGE",
    "asks",
    "attachment",
    "chat",
    "chat_detail",
    "file_content",
    "file_entry",
    "file_listing",
    "interruption",
    "item",
    "jobs",
    "live_steps",
    "message",
    "metrics",
    "plan",
    "stamp",
    "step",
    "tail",
    "tool_call",
]
