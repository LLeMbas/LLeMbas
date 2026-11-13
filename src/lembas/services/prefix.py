"""The prefix: what the model has already read (harness spec 3.1.0, context.md §10).

A model's server keeps what it has read of a request and reuses it for the next
one only as far as the two are the same from the first byte. The system prompt
and the tool list come first, so a change to either is a full re-read of the
whole chat -- measured on a local model, a date that changed at midnight sent a
16,912-token session from 99 % cached to 0 %. So a chat's system prompt is made
**once per session** and resent byte for byte, and what changes during the
session reaches the model as a note on the next user message instead.

**The frozen prompt** is `Chat.system_frozen`, one record per speaker
(`key`): the text, what it was built for (the tool list, the chat's own
prompt), a revision id, and `base` -- the state the model was told when it was
made. It is made again only where everything is read again anyway: after a
compaction (which empties the column), when the speaker's model is another one
(another key), when the offered tools differ (the request differs from that
point regardless), or when the person edited the chat's own system prompt,
which is their deliberate change. A folder's, a model's or the instance's
prompt edited during a session is not seen until one of those happens.

**No clock in it.** The date is a note: on the first user message of a
session, and on the next one after the date changed in the person's zone.

**The note** (`loop.json` `context.note`) is a `<session-note>` block after the
person's words, its lines in the spec's order: the date, the mode, the plan,
the memory, an instruction file, the personality. What the model was told is
the record's `base`, then every note and reply-state in the history in order
(`Message.note_json`, per speaker); what is true now is compared with that. A
note, once made for a turn, is stored with it and sent identically from then
on -- never recomputed, or the bytes the server read would change under it. A
turn sent without one (a queued turn taken in mid-reply, every earlier turn
when a speaker joins) is stored as sent with none.

**Who changed it.** The person's own changes are notes. The model's own are
not: a memory it saved, or the mode its approved plan left, are recorded on its
reply as known. The plan is the one exception, because a chat here does not
replay an earlier reply's tool results -- the plan's ids the model needs for
`plan_update` would otherwise be gone after its reply -- so a plan its own tools
changed comes back on the next turn too (`context.note.plan_own`).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import ROLE_USER, Chat, Message
from lembas.services import harness_spec

# How the date reads in a note: the same words `core.today` used to put in the
# system prompt, in the person's zone.
DATE_FORMAT = "%A %-d %B %Y"

# The note's `{{mode}}` when a chat leaves Plan mode: a chat here has Plan or
# nothing (an agent chat's four modes are its device's), and the block's text
# is the spec's `context.note.plan_ended`.
NORMAL_MODE = "normal"

# The key of `Chat.system_frozen` that records changes the person made, beside
# the per-speaker records: a speaker key is "<model>|<connection>", and no
# model id starts with this.
PERSON = "~person"

_NOTE_BLOCK = re.compile(r"\n*<session-note>[\s\S]*?</session-note>\s*$")


def key(speaker) -> str:
    """A speaker's record in `system_frozen` and `note_json`."""
    return f"{speaker.model_id}|{speaker.connection_id or ''}"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def tools_hash(tools: list[dict[str, Any]] | None) -> str:
    return _hash(json.dumps(tools or [], sort_keys=True, ensure_ascii=False))


def today(user) -> str:
    """The date in the person's zone, as a note says it."""
    from lembas.services.schedule import clock

    return clock.now_for(user).strftime(DATE_FORMAT)


def strip(text: str) -> str:
    """A turn's words without the note a request carried after them."""
    return _NOTE_BLOCK.sub("", text or "")


# --- What is true now ------------------------------------------------------------------
@dataclass
class Facts:
    """The texts a note can report, as they are now. `memory` and
    `instructions` are None where the chat does not carry them at all."""

    mode: str = ""
    plan: str = ""
    memory: str | None = None
    instructions: dict[str, str] | None = None
    personal: str = ""
    personality: str = ""
    # The team's size block (spec 3.2.0, team.md §10) and the value it tells;
    # None where no team is offered. Not part of a prompt's `base`: the size
    # is never in the prompt, so the first user message of a session says it.
    team: str | None = None
    team_state: str = ""

    def state(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mode": self.mode,
            "plan": _hash(self.plan),
            "personal": _hash(self.personal),
            "personality": _hash(self.personality),
        }
        if self.memory is not None:
            out["memory"] = _hash(self.memory)
        if self.instructions is not None:
            out["instructions"] = {name: _hash(text) for name, text in self.instructions.items()}
        if self.team is not None:
            out["team"] = self.team_state
        return out


def facts(db: DBSession, chat: Chat, user, speaker, tools) -> Facts:
    from lembas.services import data_groups, harness, personalization, plans
    from lembas.services import memories as memories_service
    from lembas.services import tools as tools_service

    families = harness._families(db, tools or [])
    personal, personality = personalization.parts(db, user)
    out = Facts(
        mode="plan" if tools_service.plan_mode(chat) else "",
        plan=plans.render(chat.plan),
        personal=personal,
        personality=personality,
    )
    if "memory" in families:
        group = data_groups.for_speaker(db, user, chat, speaker)
        out.memory = memories_service.block(db, user, group)
    if "files" in families and user is not None:
        from lembas.services import fs, workspace

        out.instructions = dict(workspace.instruction_texts(fs.view(db, user, chat)))
    from lembas.services import team_run

    block, state = team_run.note_block(db, chat, tools)
    if block:
        out.team, out.team_state = block, state
    return out


# --- The frozen prompt -----------------------------------------------------------------
def compose(db: DBSession, chat: Chat, user, speaker, tools) -> str:
    """The system prompt as it is made now: the harness in front of whichever
    authored prompt won, the person's custom instructions and personality last."""
    from lembas.services import chat as chat_service
    from lembas.services import harness, personalization, prompts

    system = harness.join(
        harness.compose(db, user, tools, chat, speaker=speaker),
        chat_service.effective_system_prompt(db, chat, speaker),
        lead=prompts.render(db, "seam.authored_lead", {}),
    )
    if mine := personalization.block(db, user):
        system = f"{system}\n\n{mine}" if system else mine
    return system


def _record(
    db: DBSession, chat: Chat, user, speaker, tools, now: Facts, *, persist: bool
) -> dict[str, Any]:
    frozen = dict(chat.system_frozen or {})
    wanted = {
        "tools": tools_hash(tools),
        "authored": _hash((chat.system_prompt or "").strip()),
    }
    record = frozen.get(key(speaker))
    if (
        isinstance(record, dict)
        and isinstance(record.get("text"), str)
        and all(record.get(name) == value for name, value in wanted.items())
    ):
        return record
    record = {
        "text": compose(db, chat, user, speaker, tools),
        "rev": uuid.uuid4().hex[:12],
        **wanted,
        "built_at": datetime.now(UTC).isoformat(),
        # The team's size is told in a note, never in the prompt.
        "base": {k: v for k, v in now.state().items() if k != "team"},
        # Whether the team tools are in this prompt's list: they stay while
        # the prompt does, whatever the model's metadata does later.
        "team": now.team is not None,
    }
    if persist:
        frozen[key(speaker)] = record
        chat.system_frozen = frozen
    return record


def forget(chat: Chat) -> None:
    """Every speaker's frozen prompt goes: a compaction rereads everything, and
    the next request makes the prompt again."""
    chat.system_frozen = {}


def person_changed_plan(chat: Chat, plan: dict | None) -> None:
    """The person made this the chat's plan (an older proposal carried out), so
    the note that reports it is the spec's `plan` line, not the model's own."""
    from lembas.services import plans

    frozen = dict(chat.system_frozen or {})
    frozen[PERSON] = {"plan": _hash(plans.render(plan))}
    chat.system_frozen = frozen


# --- What the model was told ---------------------------------------------------------
def _told(record: dict[str, Any], rows: list[Message], who: str) -> dict[str, Any]:
    told = dict(record.get("base") or {})
    for row in rows:
        entry = (row.note_json or {}).get(who)
        if not isinstance(entry, dict):
            continue
        state = entry.get("state") or {}
        if entry.get("rev") == record.get("rev"):
            told.update(state)
        elif "date" in state:
            # A note made under an earlier prompt is still in the request; the
            # date it said is still said. Everything else is the newer base's.
            told["date"] = state["date"]
    return told


def _fill(template: str, **values: str) -> str:
    return harness_spec.fill(template, values)


def _mode_words(db: DBSession, mode: str) -> tuple[str, str]:
    if mode == "plan":
        from lembas.services import prompts

        return "plan", prompts.render(db, "mode.plan", {"plan_dir": prompts.PLAN_DIR})
    return NORMAL_MODE, harness_spec.note()["plan_ended"]


def _note(
    db: DBSession, chat: Chat, user, told: dict[str, Any], now: Facts, *, persist: bool = False
) -> tuple[str, dict[str, Any]]:
    """The note for the next user message, and the state it tells."""
    texts = harness_spec.note()
    nothing = texts["empty"]
    current = now.state()
    lines: list[str] = []
    changed: dict[str, Any] = {}

    date = today(user)
    if told.get("date") != date:
        lines.append(_fill(texts["date"], date=date))
        changed["date"] = date

    if told.get("mode", "") != current["mode"]:
        name, words = _mode_words(db, current["mode"])
        lines.append(_fill(texts["mode"], mode=name, mode_text=words))
        changed["mode"] = current["mode"]

    if told.get("plan") != current["plan"]:
        by_person = (chat.system_frozen or {}).get(PERSON, {}).get("plan") == current["plan"]
        template = texts["plan"] if by_person else texts["plan_own"]
        lines.append(_fill(template, plan=now.plan or nothing))
        changed["plan"] = current["plan"]

    if "memory" in current and told.get("memory") != current["memory"]:
        lines.append(_fill(texts["memory"], memory=now.memory or nothing))
        changed["memory"] = current["memory"]

    if "instructions" in current and told.get("instructions") != current["instructions"]:
        before = told.get("instructions") or {}
        after = current["instructions"]
        for name in sorted(set(before) | set(after)):
            if before.get(name) != after.get(name):
                text = (now.instructions or {}).get(name, "")
                lines.append(_fill(texts["instructions"], path=name, text=text or nothing))
        changed["instructions"] = after

    if told.get("personal") != current["personal"]:
        lines.append(
            _fill(texts["instructions"], path=texts["personal"], text=now.personal or nothing)
        )
        changed["personal"] = current["personal"]

    if told.get("personality") != current["personality"]:
        lines.append(_fill(texts["personality"], personality=now.personality or nothing))
        changed["personality"] = current["personality"]

    # The team (spec 3.2.0, team.md §10): the size on the first user message
    # and after it changed, then the members the person stopped.
    if "team" in current and told.get("team") != current["team"]:
        lines.append(now.team or "")
        changed["team"] = current["team"]
    if now.team is not None:
        from lembas.services import team, team_run

        stopped = team_run.take_stopped(db, chat, persist=persist)
        if stopped:
            lines.append(team.stopped_block(stopped))

    if not lines:
        return "", changed
    # One block per change, a blank line between blocks (a block may span
    # lines), in `context.note`'s order.
    body = "\n\n".join(lines)
    return f"{texts['open']}\n{body}\n{texts['close']}", changed


# --- One request ---------------------------------------------------------------------
@dataclass
class Prepared:
    """What one request sends ahead of and inside the conversation."""

    system: str
    # message id -> the note that turn carries
    notes: dict[str, str] = field(default_factory=dict)


def prepare(
    db: DBSession,
    chat: Chat,
    user,
    speaker,
    tools,
    *,
    window: list[Message],
    sent: list[Message],
    persist: bool,
    new_note: bool = True,
) -> Prepared:
    """The frozen system prompt and every turn's note, for one request.

    `window` is the history in front of the model (after any compaction, up to
    the request's end); `sent` the rows of it that become turns. With
    `persist`, a prompt made here and a note made for the newest user turn are
    stored, and every other user turn sent without one is stored as having none
    -- what a reply does. Without it nothing is written: the inspector's
    preview. `new_note=False` makes no new note at all: the compaction
    request, which adds a turn of its own after the stored ones.
    """
    who = key(speaker)
    now = facts(db, chat, user, speaker, tools)
    record = _record(db, chat, user, speaker, tools, now, persist=persist)
    told = _told(record, window, who)

    newest = next((row for row in reversed(sent) if row.role == ROLE_USER), None)
    notes: dict[str, str] = {}
    for row in sent:
        entry = (row.note_json or {}).get(who)
        if entry is None and row is newest and new_note:
            text, changed = _note(db, chat, user, told, now, persist=persist)
            entry = {"text": text, "state": changed, "rev": record["rev"]}
            if persist:
                _store(row, who, entry)
        elif entry is None and row.role == ROLE_USER and persist:
            entry = {"text": "", "state": {}, "rev": record["rev"]}
            _store(row, who, entry)
        if isinstance(entry, dict) and entry.get("text"):
            notes[row.id] = str(entry["text"])
    return Prepared(system=record["text"], notes=notes)


def _store(row: Message, who: str, entry: dict[str, Any]) -> None:
    stored = dict(row.note_json or {})
    stored[who] = entry
    row.note_json = stored


def sent_without_note(db: DBSession, chat: Chat, message: Message, speaker) -> None:
    """A user turn handed to the model with no note (taken in between two rounds
    of a reply): stored as sent, so it is never given one afterwards."""
    who = key(speaker)
    if (message.note_json or {}).get(who) is not None:
        return
    record = (chat.system_frozen or {}).get(who) or {}
    _store(message, who, {"text": "", "state": {}, "rev": record.get("rev", "")})


def known(
    db: DBSession,
    chat: Chat,
    user,
    speaker,
    reply: Message,
    *,
    memory: bool = False,
    mode: bool = False,
) -> None:
    """Record on a reply what its own tools changed -- the memory it saved, the
    mode its approved plan left: the model knows it, so no note reports it
    later. As it is now, in `Facts.state`'s terms."""
    from lembas.services import data_groups
    from lembas.services import memories as memories_service
    from lembas.services import tools as tools_service

    who = key(speaker)
    record = (chat.system_frozen or {}).get(who)
    if not isinstance(record, dict) or not (memory or mode):
        return
    state: dict[str, Any] = {}
    if memory:
        group = data_groups.for_speaker(db, user, chat, speaker)
        state["memory"] = _hash(memories_service.block(db, user, group))
    if mode:
        state["mode"] = "plan" if tools_service.plan_mode(chat) else ""
    entry = dict((reply.note_json or {}).get(who) or {})
    entry["state"] = {**(entry.get("state") or {}), **state}
    entry["rev"] = record.get("rev", "")
    entry.setdefault("text", "")
    _store(reply, who, entry)


__all__ = [
    "DATE_FORMAT",
    "NORMAL_MODE",
    "Facts",
    "Prepared",
    "compose",
    "facts",
    "forget",
    "key",
    "known",
    "person_changed_plan",
    "prepare",
    "sent_without_note",
    "strip",
    "today",
    "tools_hash",
]


def pages_line(change: str, pages_text: str = "") -> str:
    """The session note's line when HTML pages are switched on or off for a
    chat after its prompt was made (harness spec 3.8.0, pages.md §5): the
    block itself when on, one sentence when off. The prompt is not made again."""
    shape = harness_spec.loop()["context"]["note"]
    if change == "on":
        return shape["pages_on"].replace("{{pages_text}}", pages_text)
    return shape["pages_off"]

