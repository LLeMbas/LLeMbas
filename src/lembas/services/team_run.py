"""A chat's team, on this server (harness spec 3.2.0, team.md).

`services/team.py` is the rules on plain data. This module is everything a
team is *made of* here, and it applies those rules and nothing of its own:

- **The rows.** `teams` (the lead's goal and what waits for it), `team_agents`
  (one per member: name, manager, role, state, the hidden chat it works in)
  and `team_messages` (the room, numbered from 1 per team). A team is keyed by
  its lead chat. Every operation loads the team from them into a
  `team.Team`, applies one rule, writes back what changed and carries out the
  `Effect`s the rule left: start an agent's turn, deliver a message into one
  that is running, stop one.

- **The members** run on the job machinery `services/subagent.py` already has
  (team.md §4): a hidden, temporary, unattended chat each, made by
  `subagent._create_child` under its manager's chat -- so it inherits the
  manager's narrowing, grants and effort, cannot ask anybody anything, and is
  bounded by the subagent settings like every helper. A turn is started with
  `wake.wake_chat`; a message to a member that is working is queued into its
  chat and taken in at its next step boundary by `generation._inject`, as a
  job's note is (§7). A member is not a `chat_jobs` row: the spec says members
  are not jobs to the model, and `job_list` must not show them.

- **The supervisor.** One task per team while anything in it works or waits,
  polling as the job watcher does (`subagent.POLL_SECONDS`): a member whose
  turn ended has its final reply posted as its report, which wakes its
  manager; a member past the subagent wall clock is stopped and reports as
  `budget`; a freed slot starts the next waiting agent, the lead first.

- **The refusals at the call.** The tool list never changes during a session
  (context.md §10 rule 4), so a manager's work calls (§6), `task` past the
  size (§3) and a team tool whose requirement is not met (§2) are answered at
  the call, from `refusal_for_call` in `tools/run.py` and from `task` itself.

Nothing here is said to a person in the spec's words; the UI's strings are
translated where they are shown (`api/chats.py`, the Team panel).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from lembas.db.models import (
    KIND_AGENT,
    KIND_TASK,
    ROLE_ASSISTANT,
    Chat,
    Message,
    TeamAgent,
    TeamMessage,
    User,
)
from lembas.db.models import Team as TeamRow
from lembas.db.session import session_scope
from lembas.services import settings_store
from lembas.services import team as rules
from lembas.web.i18n import N_, t

if TYPE_CHECKING:  # pragma: no cover - typing only
    from lembas.services.tools import ToolContext, ToolDef, ToolOutcome

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0

# An agent's state as the Team panel says it, marked for the catalogue here and
# translated where shown -- the model reads the spec's own words instead
# (`loop.json` `team.read.states`).
STATE_WORDS: dict[str, str] = {
    rules.WORKING: N_("working"),
    rules.WAITING: N_("waiting for a slot"),
    rules.FINISHED: N_("finished"),
    rules.IDLE: N_("idle"),
}


def state_word(state: str) -> str:
    return t(STATE_WORDS.get(str(state), str(state)))

_LOCKS: dict[str, asyncio.Lock] = {}
_SUPERVISORS: dict[str, asyncio.Task] = {}
# Member chats stopped at the wall clock, so their report says `budget`.
_OVERRUN: set[str] = set()


def clear() -> None:
    """For tests: the in-process state of every team."""
    for task in list(_SUPERVISORS.values()):
        task.cancel()
    _SUPERVISORS.clear()
    _LOCKS.clear()
    _OVERRUN.clear()


def _lock(lead_id: str) -> asyncio.Lock:
    lock = _LOCKS.get(lead_id)
    if lock is None:
        lock = _LOCKS[lead_id] = asyncio.Lock()
    return lock


def _now() -> datetime:
    return datetime.now(UTC)


def _epoch(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


# --- The setting and when a team is offered (team.md §2) ------------------------------
def model_parallel(model) -> int | None:
    """How many requests a model serves at once: its `parallel`, else 1 for a
    model that serves one at a time, else None (no limit: a hosted API).

    The one place a model's capacity is read for teams, so the model's own
    `parallel` field (spec 3.2.0 `capacity.parallel`) is a one-line change
    here when it lands."""
    if model is None:
        return None
    parallel = getattr(model, "parallel", None)
    if parallel:
        return int(parallel)
    return 1 if getattr(model, "single_session", False) else None


def cap(db) -> int:
    """The instance's ceiling on a team's size (`subagents.team_cap`)."""
    return int(settings_store.subagents(db).get("team_cap") or rules.defaults()[2])


def setting(db, chat: Chat | None) -> tuple[int, int]:
    """The chat's `agents {min, max}` as it is read: the spec's defaults on a
    row older than teams, narrowed by a cap lowered under it."""
    if chat is None:
        return rules.read_setting(None, None, cap(db))
    return rules.read_setting(chat.agents_min, chat.agents_max, cap(db))


def user_default(db, user) -> tuple[int, int]:
    """This person's default for new chats (Settings), seeded with the spec's."""
    stored = ((getattr(user, "settings_json", None) or {}).get("agents")) or {}
    return rules.read_setting(stored.get("min"), stored.get("max"), cap(db))


def may_have_team(chat: Chat | None) -> bool:
    """A chat a person leads: not a helper's or a member's hidden chat, not a
    schedule's, not an agent chat (whose team runs on its device, §11)."""
    if chat is None:
        return True
    return not (
        getattr(chat, "parent_chat_id", None)
        or getattr(chat, "unattended", False)
        or chat.kind in (KIND_TASK, KIND_AGENT)
    )


def subagents_allowed(db, user, model) -> bool:
    """Subagents enabled for a chat on this model: the instance's switch, the
    person's permission, and the model's subagent tool."""
    from lembas.security import permissions

    if not settings_store.subagents(db).get("enabled"):
        return False
    if model is None:
        return False
    capabilities = model.capabilities_json or {}
    if not capabilities.get("tools"):
        return False
    if not capabilities.get("tool_subagent", True):
        return False
    return bool(permissions.resolve(db, user).get("tools.subagent"))


def offered(db, chat: Chat | None, user, model=None) -> bool:
    """Whether a team is offered in this chat (or, with no chat, a new one on
    `model`): subagents enabled, and the model serving at least two requests
    at once. Kept for as long as the chat's system prompt is: a chat whose
    prompt was made with the team tools keeps them when the model's metadata
    changes later (they then answer `one_request`)."""
    from lembas.services import chat as chat_service

    if not may_have_team(chat):
        return False
    if model is None and chat is not None:
        model = chat_service.model_for(db, chat)
    if not subagents_allowed(db, user, model):
        return False
    if rules.capacity_allows(model_parallel(model)):
        return True
    return chat is not None and _prompt_had_team(chat)


def _prompt_had_team(chat: Chat) -> bool:
    for record in (getattr(chat, "system_frozen", None) or {}).values():
        if isinstance(record, dict) and record.get("team"):
            return True
    return False


# --- Who is calling ----------------------------------------------------------------
def member_row(db, chat_id: str) -> TeamAgent | None:
    """The member a hidden chat is, while it is on its team."""
    if not chat_id:
        return None
    return db.scalars(
        select(TeamAgent).where(TeamAgent.chat_id == chat_id, TeamAgent.left.is_(None))
    ).first()


def whoami(db, chat: Chat | None) -> tuple[str, str] | None:
    """(the lead chat's id, the caller's name) for a chat in a team, or None."""
    if chat is None:
        return None
    if getattr(chat, "parent_chat_id", None):
        row = member_row(db, chat.id)
        return (row.lead_chat_id, row.name) if row is not None else None
    if may_have_team(chat):
        return chat.id, rules.LEAD
    return None


# --- Loading and saving a team -----------------------------------------------------
def _team_row(db, lead_id: str, *, create: bool = False) -> TeamRow | None:
    row = db.scalars(select(TeamRow).where(TeamRow.lead_chat_id == lead_id)).first()
    if row is None and create:
        row = TeamRow(lead_chat_id=lead_id, goal="", seq=0, data_json={})
        db.add(row)
        db.flush()
    return row


def _member_rows(db, lead_id: str) -> list[TeamAgent]:
    return list(
        db.scalars(
            select(TeamAgent)
            .where(TeamAgent.lead_chat_id == lead_id, TeamAgent.left.is_(None))
            .order_by(TeamAgent.position)
        )
    )


def _room_rows(db, lead_id: str) -> list[TeamMessage]:
    return list(
        db.scalars(
            select(TeamMessage)
            .where(TeamMessage.lead_chat_id == lead_id)
            .order_by(TeamMessage.n)
        )
    )


def _running(chat_id: str | None) -> bool:
    from lembas.services import generation as generation_service

    return bool(chat_id) and generation_service.running_for(chat_id) is not None


def _running_helpers(db, chat_ids: list[str]) -> int:
    """`task` helpers running in the team (they count toward the size, §3)."""
    from lembas.services import jobs as jobs_service

    return sum(len(jobs_service.running_for(db, chat_id)) for chat_id in chat_ids)


def load(db, lead_id: str) -> rules.Team | None:
    """The team of a lead chat as the rules see it, or None for no such chat."""
    from lembas.services import chat as chat_service
    from lembas.services import subagent as subagent_service

    chat = db.get(Chat, lead_id)
    if chat is None:
        return None
    owner = db.get(User, chat.user_id)
    model = chat_service.model_for(db, chat)
    low, high = setting(db, chat)
    row = _team_row(db, lead_id)
    data = (row.data_json or {}) if row is not None else {}
    inbox = list(data.get("lead_inbox") or [])
    lead_state = rules.WORKING if _running(lead_id) else (rules.WAITING if inbox else rules.IDLE)
    lead = rules.Agent(
        rules.LEAD,
        state=lead_state,
        model=subagent_service.model_ref(model),
        inbox=inbox,
        waiting_since=int(data.get("lead_waiting_since") or 0),
        last_ended=data.get("lead_last_ended"),
        extra={"lead_id": lead_id, "chat_id": lead_id},
    )
    team = rules.Team(
        min=low,
        max=high,
        offered=offered(db, chat, owner, model),
        parallel=model_parallel(model),
        single_session=False,
        agents=[lead],
        goal=row.goal if row is not None else "",
        seq=row.seq if row is not None else 0,
        stopped_unsaid=list(data.get("stopped_unsaid") or []),
    )
    members = _member_rows(db, lead_id)
    for member in members:
        team.agents.append(
            rules.Agent(
                name=member.name,
                manager=member.manager,
                role=member.role,
                may_lead=member.may_lead,
                agent=member.agent,
                model=member.model,
                state=member.state,
                goal=member.goal,
                order=member.position,
                waiting_since=member.waiting_since,
                inbox=list(member.inbox_json or []),
                last_ended=_epoch(member.last_ended),
                extra={"row_id": member.id, "chat_id": member.chat_id},
            )
        )
    chat_ids = [lead_id] + [m.chat_id for m in members if m.chat_id]
    team.helpers = [rules.LEAD] * _running_helpers(db, chat_ids)
    for entry in _room_rows(db, lead_id):
        team.room.append(
            rules.Post(
                n=entry.n,
                sender=entry.sender,
                to=list(entry.to_json or []),
                recipients=list(entry.recipients_json or []),
                text=entry.text,
                report=entry.report,
                created=_epoch(entry.created_at) or 0.0,
            )
        )
    return team


def save(db, lead_id: str, team: rules.Team) -> None:
    """Write back what an operation changed: the members' states and inboxes,
    those that left, new room messages, the team's own fields. New members
    are written by `_add_member` before this, with their rows' ids."""
    row = _team_row(db, lead_id, create=True)
    row.goal = team.goal
    row.seq = team.seq
    lead = team.lead
    row.data_json = {
        **(row.data_json or {}),
        "lead_inbox": list(lead.inbox),
        "lead_waiting_since": lead.waiting_since,
        "lead_last_ended": lead.last_ended,
        "stopped_unsaid": list(team.stopped_unsaid),
    }
    present = {a.name: a for a in team.members()}
    for member in _member_rows(db, lead_id):
        agent = present.get(member.name)
        if agent is None:
            member.left = _now()
            member.state = "stopped"
            member.inbox_json = []
            continue
        if member.state != agent.state and agent.state == rules.WORKING:
            member.run_started = _now()
        member.state = agent.state
        member.inbox_json = list(agent.inbox)
        member.waiting_since = agent.waiting_since
        member.goal = agent.goal
        if agent.last_ended is not None:
            member.last_ended = datetime.fromtimestamp(agent.last_ended, UTC)
    stored = {m.n for m in _room_rows(db, lead_id)}
    for entry in team.room:
        if entry.n in stored:
            continue
        db.add(
            TeamMessage(
                lead_chat_id=lead_id,
                n=entry.n,
                sender=entry.sender,
                to_json=list(entry.to),
                recipients_json=list(entry.recipients),
                text=entry.text,
                report=entry.report,
            )
        )
    db.commit()


def _chat_of(db, lead_id: str, name: str) -> str:
    if name == rules.LEAD:
        return lead_id
    row = db.scalars(
        select(TeamAgent).where(
            TeamAgent.lead_chat_id == lead_id, TeamAgent.name == name, TeamAgent.left.is_(None)
        )
    ).first()
    return (row.chat_id or "") if row is not None else ""


# --- Carrying out the effects ---------------------------------------------------------
MARKS = ("[Team message from ", "[Team report from ")


async def _deliver(chat_id: str, text: str) -> None:
    """A message into an agent's running turn: queued into its chat and taken
    in at its next step boundary. Several waiting at one boundary go in one
    user message, in room order, a blank line between (§7) -- so a message
    joins a team message already waiting there rather than queueing behind
    it, where `_inject` would take one per round."""
    from lembas.services import wake as wake_service

    async with wake_service.lock_for(chat_id):
        if _running(chat_id):
            with session_scope() as db:
                waiting = db.scalars(
                    select(Message)
                    .where(
                        Message.chat_id == chat_id,
                        Message.queued.is_(True),
                        Message.machine.is_(True),
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                ).first()
                if waiting is not None and (waiting.content or "").startswith(MARKS):
                    waiting.content = f"{waiting.content}\n\n{text}"
                    db.commit()
                    return
    await wake_service.wake_chat(chat_id, text)


async def _start(chat_id: str, texts: list[str]) -> None:
    from lembas.services import wake as wake_service

    if texts:
        await wake_service.wake_chat(chat_id, "\n\n".join(texts))


async def _stop_chat(chat_id: str) -> None:
    """A member leaves: its turn stops where it is, and what waited for it is
    dropped (§9)."""
    from lembas.services import generation as generation_service

    running = generation_service.running_for(chat_id)
    if running is not None:
        generation_service.request_stop(running.message_id)
    with session_scope() as db:
        for row in db.scalars(
            select(Message).where(Message.chat_id == chat_id, Message.queued.is_(True))
        ):
            db.delete(row)
        db.commit()


async def _apply(lead_id: str, effects: list[rules.Effect], chats: dict[str, str]) -> None:
    for effect in effects:
        chat_id = chats.get(effect.name) or ""
        if not chat_id:
            continue
        try:
            if effect.kind == "deliver":
                await _deliver(chat_id, effect.texts[0])
            elif effect.kind == "start":
                await _start(chat_id, effect.texts)
            elif effect.kind == "stop":
                await _stop_chat(chat_id)
        except Exception:  # one agent that cannot be reached must not stop the rest
            log.exception("team %s: could not %s %s", lead_id, effect.kind, effect.name)
    _bump(lead_id)


def _chats_for(db, lead_id: str, team: rules.Team, effects: list[rules.Effect]) -> dict[str, str]:
    """The chat each effect's agent works in, read before its row may leave."""
    out: dict[str, str] = {}
    for effect in effects:
        agent = team.get(effect.name)
        chat_id = (agent.extra.get("chat_id") if agent is not None else "") or ""
        out[effect.name] = chat_id or _chat_of(db, lead_id, effect.name)
    return out


# A counter per team, so the Team panel and the stream know something moved.
_VERSIONS: dict[str, int] = {}


def _bump(lead_id: str) -> None:
    _VERSIONS[lead_id] = _VERSIONS.get(lead_id, 0) + 1


def version(lead_id: str) -> int:
    return _VERSIONS.get(lead_id, 0)


async def operate(lead_id: str, change) -> Any:
    """Load the team, sync it with what is running, apply `change(db, team)`
    under the team's lock, save, and carry out the effects. Returns what
    `change` returned."""
    async with _lock(lead_id):
        with session_scope() as db:
            team = load(db, lead_id)
            if team is None:
                return None
            _sync(db, team)
            result = change(db, team)
            effects = list(team.effects)
            team.effects.clear()
            chats = _chats_for(db, lead_id, team, effects)
            save(db, lead_id, team)
        await _apply(lead_id, effects, chats)
    if _needs_watching(lead_id):
        ensure_supervisor(lead_id)
    return result


# --- Keeping the rules in step with what runs ------------------------------------------
def _end_reason(message: Message | None) -> str:
    """How a member's turn ended, in the spec's words (`team.texts.ended`)."""
    if message is None:
        return "error — the reply could not be found"
    if message.error:
        return f"error — {message.error}"
    for event in message.tool_calls_json or []:
        if not isinstance(event, dict) or event.get("status") != "error":
            continue
        said = str(event.get("error") or "")
        if event.get("kind") == "agent" and "rounds of tool calls" in said:
            return "steps"
        if event.get("name") == "budget" and said.startswith("Stopped at the"):
            return "budget"
    return "stop"


def _harvest(db, chat_id: str) -> tuple[str, str]:
    message = db.scalars(
        select(Message)
        .where(Message.chat_id == chat_id, Message.role == ROLE_ASSISTANT)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    ).first()
    reply = (message.content or "").strip() if message is not None else ""
    reason = _end_reason(message)
    if chat_id in _OVERRUN:
        _OVERRUN.discard(chat_id)
        reason = "budget"
    return reply, reason


def _sync(db, team: rules.Team) -> None:
    """Bring the rules' view up to what is running: a member whose turn has
    ended finishes (its reply is its report), a member past the wall clock is
    stopped, a member a queued message started again is working, and a freed
    slot goes to whoever waits."""
    from lembas.services import generation as generation_service
    from lembas.services import wake as wake_service

    wall = float(settings_store.subagents(db)["wall_seconds"])
    now = time.time()
    for agent in list(team.members()):
        chat_id = agent.extra.get("chat_id") or ""
        if not chat_id or db.get(Chat, chat_id) is None:
            if agent.state == rules.WORKING:
                rules.finish(team, agent.name, "", "error — its conversation is gone", now=now)
            continue
        running = generation_service.running_for(chat_id)
        if agent.state == rules.WORKING:
            if running is not None:
                row = db.get(TeamAgent, agent.extra.get("row_id"))
                started = _epoch(row.run_started) if row is not None else None
                if started is not None and now - started > wall and chat_id not in _OVERRUN:
                    _OVERRUN.add(chat_id)
                    generation_service.request_stop(running.message_id)
                continue
            if wake_service.queued_count(chat_id):
                # A message waits that the turn did not take in: the queue's
                # own drain is starting it again, as the same turn's work.
                continue
            reply, reason = _harvest(db, chat_id)
            rules.finish(team, agent.name, reply, reason, now=now)
        elif running is not None:
            # Started again by its queue's own drain: working, and what waited
            # for its next turn goes into this one.
            agent.state = rules.WORKING
            for text in agent.inbox:
                team.effects.append(rules.Effect("deliver", agent.name, [text]))
            agent.inbox = []
    rules.schedule(team)


def _needs_watching(lead_id: str) -> bool:
    with session_scope() as db:
        rows = _member_rows(db, lead_id)
        if any(r.state in (rules.WORKING, rules.WAITING) for r in rows):
            return True
        row = _team_row(db, lead_id)
        return bool(row is not None and (row.data_json or {}).get("lead_inbox"))


def ensure_supervisor(lead_id: str) -> None:
    task = _SUPERVISORS.get(lead_id)
    if task is not None and not task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _SUPERVISORS[lead_id] = loop.create_task(_supervise(lead_id))


async def _supervise(lead_id: str) -> None:
    try:
        while True:
            await asyncio.sleep(POLL_SECONDS)
            try:
                await operate(lead_id, lambda db, team: None)
            except Exception:  # a supervisor that dies silently is worse
                log.exception("team %s: supervision failed", lead_id)
            if not _needs_watching(lead_id):
                return
    finally:
        _SUPERVISORS.pop(lead_id, None)


# --- The tools ----------------------------------------------------------------------
def _outcome(text: str, event: dict[str, Any]) -> ToolOutcome:
    from lembas.services.tools import ToolOutcome

    return ToolOutcome(text, event)


def _event(name: str, text: str, query: str, *, ok: bool = True) -> dict[str, Any]:
    event = {"name": name, "status": "ok" if ok else "error", "query": query[:120], "text": text}
    if not ok:
        event["error"] = text[:300]
    return event


def _caller(context: ToolContext) -> tuple[str, str] | None:
    with session_scope() as db:
        chat = db.get(Chat, context.chat_id) if context.chat_id else None
        found = whoami(db, chat)
    return found


def _new_member(db, lead_id: str, by: str, agent: rules.Agent, owner: User, model) -> None:
    """The member's hidden chat and row, made under its manager's chat."""
    from lembas.services import subagent as subagent_service

    manager_chat = db.get(Chat, _chat_of(db, lead_id, by) or lead_id)
    child = subagent_service._create_child(
        db,
        manager_chat,
        title=f"{agent.name}: {agent.role}"[:200],
        write=agent.agent == subagent_service.AGENT_GENERAL,
        agent=agent.agent,
        model=model,
    )
    scope = dict(child.scope_json or {})
    scope["team"] = {"lead": lead_id, "name": agent.name}
    child.scope_json = scope
    row = TeamAgent(
        lead_chat_id=lead_id,
        name=agent.name,
        manager=by,
        role=agent.role,
        may_lead=agent.may_lead,
        agent=agent.agent,
        model=agent.model,
        model_id=child.model_id or "",
        connection_id=child.connection_id,
        chat_id=child.id,
        state=agent.state,
        goal=agent.goal,
        position=agent.order,
        waiting_since=agent.waiting_since,
        inbox_json=list(agent.inbox),
        run_started=_now() if agent.state == rules.WORKING else None,
    )
    db.add(row)
    db.flush()
    agent.extra = {"row_id": row.id, "chat_id": child.id}


async def _run_team_add(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    from lembas.services import chat as chat_service
    from lembas.services import subagent as subagent_service
    from lembas.services import tools as tools_service

    who = _caller(context)
    if who is None:
        text = rules.spec()["unavailable"]["one_agent"]
        return _outcome(text, _event("team_add", text, "", ok=False))
    lead_id, by = who
    wanted_agent = str(args.get("agent") or "").strip() or subagent_service.AGENT_GENERAL
    name = str(args.get("name") or "")
    if wanted_agent not in subagent_service.AGENTS:
        text = (
            f"There is no subagent called {wanted_agent!r} here. The agents are: "
            + "; ".join(f"{n} — {d}" for n, d in tools_service.HELPER_AGENTS)
            + "."
        )
        return _outcome(text, _event("team_add", text, name, ok=False))
    with session_scope() as db:
        caller_chat = db.get(Chat, context.chat_id)
        owner = db.get(User, caller_chat.user_id) if caller_chat is not None else None
        if caller_chat is None or owner is None:
            text = "That conversation no longer exists."
            return _outcome(text, _event("team_add", text, name, ok=False))
        model = None
        wanted_model = str(args.get("model") or "").strip()
        if wanted_model:
            model = subagent_service._pick_model(db, owner, caller_chat, wanted_model)
            if model is None:
                text = (
                    f"There is no model called {wanted_model!r} that this chat may use. "
                    "Leave `model` out to run the member on this chat's own model."
                )
                return _outcome(text, _event("team_add", text, name, ok=False))
        if wanted_agent == subagent_service.AGENT_GENERAL:
            refusal = subagent_service.write_refusal(caller_chat)
            if refusal:
                return _outcome(refusal, _event("team_add", refusal, name, ok=False))
        ref = subagent_service.model_ref(
            model or chat_service.model_row(db, chat_service.speaker_for(db, caller_chat))
        )
        owner_id = owner.id
        model_id = model.id if model is not None else None

    def change(db, team: rules.Team):
        added = rules.add(team, by, {**args, "agent": wanted_agent}, model=ref)
        if added.agent is not None:
            owner_row = db.get(User, owner_id)
            from lembas.db.models import Model

            model_row = db.get(Model, model_id) if model_id else None
            _new_member(db, lead_id, by, added.agent, owner_row, model_row)
        return added

    added = await operate(lead_id, change)
    if added is None:
        text = "That conversation no longer exists."
        return _outcome(text, _event("team_add", text, name, ok=False))
    ok = added.agent is not None
    event = _event("team_add", added.text, name, ok=ok)
    if ok:
        event["detail"] = f"{name} · {wanted_agent} · {added.agent.role}"[:200]
    return _outcome(added.text, event)


async def _run_team_send(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    who = _caller(context)
    message = str(args.get("message") or "")
    if who is None:
        text = rules.spec()["unavailable"]["one_agent"]
        return _outcome(text, _event("team_send", text, "", ok=False))
    lead_id, by = who

    def change(db, team: rules.Team):
        text, delivered = rules.send(team, by, args.get("to"), message, now=time.time())
        return text, bool(delivered)

    result = await operate(lead_id, change)
    text, ok = result if result is not None else ("That conversation no longer exists.", False)
    to = ", ".join(rules.normalise_to(args.get("to")))
    return _outcome(text, {**_event("team_send", text, to, ok=ok), "text": message})


async def _run_team_read(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    who = _caller(context)
    if who is None:
        text = rules.spec()["unavailable"]["one_agent"]
        return _outcome(text, _event("team_read", text, "", ok=False))
    lead_id, by = who
    try:
        since = max(int(args.get("since") or 0), 0)
    except (TypeError, ValueError):
        since = 0

    def change(db, team: rules.Team):
        if refused := rules.unmet(team, by):
            return refused, False
        text, _shown = rules.read_text(team, by, since, now=time.time())
        return text, True

    result = await operate(lead_id, change)
    text, ok = result if result is not None else ("That conversation no longer exists.", False)
    return _outcome(text, _event("team_read", text, f"since {since}", ok=ok))


async def _run_team_stop(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
    who = _caller(context)
    name = str(args.get("name") or "")
    if who is None:
        text = rules.spec()["unavailable"]["one_agent"]
        return _outcome(text, _event("team_stop", text, name, ok=False))
    lead_id, by = who

    def change(db, team: rules.Team):
        text, names = rules.stop(team, by, name)
        return text, bool(names)

    result = await operate(lead_id, change)
    text, ok = result if result is not None else ("That conversation no longer exists.", False)
    return _outcome(text, _event("team_stop", text, name, ok=ok))


def tool_defs(*, lead: bool = True) -> list[ToolDef]:
    """The four team tools as the spec defines them; a member that may not
    lead has `team_send` and `team_read` alone."""
    from lembas.services.tools import (
        FAMILY_SUBAGENT,
        RISK_INTERACT,
        RISK_READ,
        ToolDef,
        spec_description,
        spec_parameters,
    )

    runners = {
        "team_add": (_run_team_add, RISK_INTERACT),
        "team_send": (_run_team_send, RISK_INTERACT),
        "team_read": (_run_team_read, RISK_READ),
        "team_stop": (_run_team_stop, RISK_INTERACT),
    }
    names = rules.TOOLS if lead else rules.MEMBER_TOOLS
    return [
        ToolDef(
            name=name,
            family=FAMILY_SUBAGENT,
            description=spec_description(name),
            parameters=spec_parameters(name),
            run=runners[name][0],
            risk=runners[name][1],
        )
        for name in names
    ]


def defs_for(db, chat: Chat, user) -> list[ToolDef]:
    """The team tools this chat is offered: all four to a lead chat with a
    team offered, and to a member that may lead; two to any other member."""
    if getattr(chat, "parent_chat_id", None):
        row = member_row(db, chat.id)
        if row is None:
            return []
        return tool_defs(lead=row.may_lead)
    if offered(db, chat, user):
        return tool_defs(lead=True)
    return []


# --- Refusals at the call ------------------------------------------------------------
def refusal_for_call(context: ToolContext, tool, parsed: dict[str, Any] | None) -> str:
    """What a call answers instead of running, in a team: a manager's work
    (§6). "" for every call that runs. The team tools check their own
    requirement; `task` checks the size in `task_refusal`."""
    if tool.name in rules.TOOLS or not context.chat_id:
        return ""
    with session_scope() as db:
        chat = db.get(Chat, context.chat_id)
        who = whoami(db, chat)
        if who is None:
            return ""
        lead_id, by = who
        if by == rules.LEAD and not _member_rows(db, lead_id) and setting(db, chat)[0] < 2:
            # The common case, read cheaply: a chat with no team is a manager
            # only when its person asked for one from the start.
            return ""
        team = load(db, lead_id)
        if team is None:
            return ""
        risk = _mcp_risk(tool) or tool.risk
        return rules.manager_refusal(team, by, tool.name, risk, parsed)


def _mcp_risk(tool) -> str:
    """An MCP tool's class for the rules: `read` when it says `readOnlyHint`,
    else `execute` (team.md §6)."""
    from lembas.services.tools import FAMILY_MCP, gate_of

    if gate_of(tool.family) != FAMILY_MCP:
        return ""
    return "read" if tool.risk == "read" else "execute"


def task_refusal(db, chat: Chat) -> str:
    """What `task` (or a `job_send` that would start a finished helper again)
    answers while a team is offered and one more helper would pass `max`."""
    who = whoami(db, chat)
    if who is None:
        return ""
    team = load(db, who[0])
    if team is None:
        return ""
    return rules.task_refusal(team)


async def report_from_member(chat_id: str, text: str) -> str | None:
    """`report` from a team's member: a message to its manager, posted in the
    room and delivered with the message mark. None when the chat is no
    member (the job's own `report` then applies)."""
    with session_scope() as db:
        row = member_row(db, chat_id)
        if row is None:
            return None
        lead_id, name, manager = row.lead_chat_id, row.name, row.manager

    def change(db, team: rules.Team):
        text_, _delivered = rules.send(team, name, [manager], text, now=time.time())
        return text_

    result = await operate(lead_id, change)
    return result or ""


# --- The person -------------------------------------------------------------------------
async def person_stop(lead_id: str, name: str) -> list[str]:
    """The person stopped a member in the Team panel: it and those under it
    leave, and the lead is told in a note."""
    result = await operate(lead_id, lambda db, team: rules.person_stop(team, name))
    return result or []


def note_block(db, chat: Chat, tools: list[dict[str, Any]] | None) -> tuple[str, str]:
    """The size block for the lead's next note, and the state it tells -- or
    ("", state) when the team is not offered. `services/prefix.py` compares
    the state with what the lead was told."""
    names = {(t.get("function") or {}).get("name") for t in tools or []}
    if "team_add" not in names or getattr(chat, "parent_chat_id", None):
        return "", ""
    low, high = setting(db, chat)
    return rules.size_block(low, high), rules.size_state(low, high)


def take_stopped(db, chat: Chat, *, persist: bool) -> list[str]:
    """The names the person stopped that the lead has not been told, taken."""
    row = _team_row(db, chat.id)
    if row is None:
        return []
    names = list((row.data_json or {}).get("stopped_unsaid") or [])
    if names and persist:
        row.data_json = {**(row.data_json or {}), "stopped_unsaid": []}
    return names


# --- What the UI and the API read -----------------------------------------------------
def snapshot(db, lead_id: str, since: int = 0) -> dict[str, Any] | None:
    """The team as the Team panel and `/api/v2` show it: the tree and the room
    since `since` -- everything, for the person sees the whole room."""
    team = load(db, lead_id)
    if team is None:
        return None
    rows = {r.name: r for r in _member_rows(db, lead_id)}
    tree = []
    for agent, depth in team.tree():
        row = rows.get(agent.name)
        tree.append(
            {
                "name": agent.name,
                "manager": agent.manager,
                "role": agent.role,
                "state": agent.state,
                "model": agent.model,
                "agent": agent.agent if agent.name != rules.LEAD else "",
                "lead": agent.may_lead or agent.name == rules.LEAD,
                "depth": depth,
                "chat_id": row.chat_id if row is not None else lead_id,
            }
        )
    room = [
        {
            "n": p.n,
            "from": p.sender,
            "to": list(p.to),
            "text": p.text,
            "report": p.report,
            "created": datetime.fromtimestamp(p.created, UTC).isoformat() if p.created else "",
        }
        for p in team.room
        if p.n > since
    ]
    low, high = setting(db, db.get(Chat, lead_id))
    return {
        "offered": team.offered,
        "min": low,
        "max": high,
        "size": team.size(),
        "parallel": team.slots(),
        "goal": team.goal,
        "agents": tree,
        "room": room,
        "cursor": team.room[-1].n if team.room else 0,
        "version": version(lead_id),
    }


def strip_rows(db, lead_id: str) -> list[dict[str, Any]]:
    """Members working or waiting, as the job strip shows them (§11's
    `description` being `<name>: <role>`)."""
    rows = []
    for member in _member_rows(db, lead_id):
        if member.state not in (rules.WORKING, rules.WAITING):
            continue
        started = member.run_started or member.created_at
        seconds = 0
        if started is not None:
            seconds = max(0, int(time.time() - (_epoch(started) or time.time())))
        rows.append(
            {
                "id": member.name,
                "kind": "team",
                "state": "running",
                "team_state": member.state,
                "description": f"{member.name}: {member.role}",
                "agent": member.agent,
                "model": member.model,
                "seconds": seconds,
                "command": "",
            }
        )
    return rows


def has_team(db, lead_id: str) -> bool:
    return _team_row(db, lead_id) is not None


# --- Deleting -------------------------------------------------------------------------
def forget_for_chats(db, chat_ids: list[str]) -> list[Chat]:
    """Deleting a lead chat stops its whole team (§4): every member's turn is
    stopped, the rows go, and the members' hidden chats are returned for the
    caller to delete with it. Does not commit."""
    from lembas.services import generation as generation_service

    if not chat_ids:
        return []
    members = list(db.scalars(select(TeamAgent).where(TeamAgent.lead_chat_id.in_(chat_ids))))
    chats: list[Chat] = []
    for member in members:
        if member.chat_id:
            running = generation_service.running_for(member.chat_id)
            if running is not None:
                generation_service.request_stop(running.message_id)
            child = db.get(Chat, member.chat_id)
            if child is not None and child.id not in chat_ids:
                chats.append(child)
        db.delete(member)
    for row in db.scalars(select(TeamMessage).where(TeamMessage.lead_chat_id.in_(chat_ids))):
        db.delete(row)
    for row in db.scalars(select(TeamRow).where(TeamRow.lead_chat_id.in_(chat_ids))):
        db.delete(row)
    for lead_id in chat_ids:
        task = _SUPERVISORS.pop(lead_id, None)
        if task is not None:
            with contextlib.suppress(Exception):
                task.cancel()
    return chats


__all__ = [
    "cap",
    "clear",
    "defs_for",
    "ensure_supervisor",
    "forget_for_chats",
    "has_team",
    "load",
    "member_row",
    "model_parallel",
    "note_block",
    "offered",
    "operate",
    "person_stop",
    "refusal_for_call",
    "setting",
    "snapshot",
    "strip_rows",
    "take_stopped",
    "task_refusal",
    "tool_defs",
    "user_default",
    "version",
    "whoami",
]
