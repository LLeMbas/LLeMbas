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
def chat(row: Chat, *, running: bool = False) -> dict[str, Any]:
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
        "created_at": stamp(row.created_at),
        "updated_at": stamp(row.updated_at),
    }


def chat_detail(row: Chat, *, running: bool = False) -> dict[str, Any]:
    """A chat with what its settings sheet shows."""
    from lembas.services import plans

    return {
        **chat(row, running=running),
        "system_prompt": row.system_prompt or "",
        "plan": plans.normalise(row.plan) if row.plan else None,
    }


# --- Messages and their steps ------------------------------------------------------
def tool_call(event: dict) -> dict[str, Any]:
    block = tool_blocks.block_for(event)
    out: dict[str, Any] = {}
    cut = False
    for key in TOOL_FIELDS:
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


__all__ = [
    "asks",
    "attachment",
    "chat",
    "chat_detail",
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
