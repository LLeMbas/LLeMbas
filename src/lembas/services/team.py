"""Teams: subagents that work together (harness spec 3.2.0, `team.md`).

A chat's agent -- the **lead** -- may add **members**, background agents that
each own a part of the work, and lead them as a manager: it plans, splits the
work, coordinates and reviews, and the members do it. Members talk to each
other and to their managers in the team's **room**. A member added with
`lead: true` may add members of its own, so a team grows into a tree.

This module is **the rules**, and nothing else: a team as plain data
(`Team`, `Agent`, `Post`) and the operations the spec defines on it -- what
is offered, the size, who is a manager and what a manager may not do, adding,
sending, reading, stopping, a turn ending, the slots a model has, and the
session note. Pure: no database, no generation, no clock but the one passed
in. `tests/test_harness_spec.py` drives it through the spec's
`conformance/team.json` step by step, so the CLI and this server apply the
same rules in the same words.

What a team *does* on this server -- the rows it is kept in, the hidden chats
its members run in, waking them, stopping them -- is `services/team_run.py`,
which loads a `Team` from its rows, applies one of these operations and
carries out the `Effect`s it leaves behind. Two implementations of "who may
send to whom" would be two answers; there is one.

Every text an agent reads comes from the spec (`loop.json` `team`, the size
notes in `context.note`), word for word, and is never translated: it is said
to a model, not shown to a person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from lembas.services import harness_spec

# The four tools, in the order the spec lists them.
TOOLS = ("team_add", "team_send", "team_read", "team_stop")
# What a member that does not lead has of them.
MEMBER_TOOLS = ("team_send", "team_read")

LEAD = "lead"
ALL = "all"

WORKING = "working"
WAITING = "waiting"
FINISHED = "finished"
IDLE = "idle"

AGENT_GENERAL = "general"
AGENT_EXPLORE = "explore"


def spec() -> dict[str, Any]:
    """`loop.json` `team`: the texts and numbers."""
    return harness_spec.loop()["team"]


def texts() -> dict[str, str]:
    return dict(spec()["texts"])


def _say(key: str, **values: Any) -> str:
    return harness_spec.fill(texts()[key], {k: str(v) for k, v in values.items()})


def defaults() -> tuple[int, int, int]:
    """(min, max, cap) for a new chat, as the spec seeds them."""
    agents = spec()["agents"]
    return int(agents["min"]), int(agents["max"]), int(agents["cap"])


NAME = re.compile(harness_spec.loop()["team"]["names"]["pattern"])


# --- When a team is offered (team.md §2) -------------------------------------------
def capacity_allows(parallel: int | None, single_session: bool = False) -> bool:
    """Whether a model serves at least two requests at once: `parallel ≥ 2`, or
    neither `parallel` nor `single_session` said (a hosted API). `parallel`
    wins where the two disagree."""
    if parallel is not None:
        return int(parallel) >= 2
    return not single_session


def slots(parallel: int | None, single_session: bool = False) -> int | None:
    """How many requests the model serves at once: None for no limit."""
    if parallel is not None:
        return max(int(parallel), 1)
    return 1 if single_session else None


def bounds_refusal(low: Any, high: Any, cap: int) -> str:
    """Why a size setting cannot be stored, or "" when it can: `1 ≤ min ≤ max ≤
    cap`, refused with the bounds rather than clamped (team.md §2). Said to a
    person, so the caller translates it; the numbers go in as values."""
    try:
        low_n, high_n = int(low), int(high)
    except (TypeError, ValueError):
        return "bounds"
    if not (1 <= low_n <= high_n <= cap):
        return "bounds"
    return ""


def read_setting(low: Any, high: Any, cap: int) -> tuple[int, int]:
    """A stored setting as it is read: `max = min(max, cap)`, `min = min(min,
    max)` -- a cap lowered under it narrows it, and nothing else changes it."""
    d_min, d_max, _ = defaults()
    try:
        high_n = int(high) if high is not None else d_max
    except (TypeError, ValueError):
        high_n = d_max
    try:
        low_n = int(low) if low is not None else d_min
    except (TypeError, ValueError):
        low_n = d_min
    high_n = max(1, min(high_n, cap))
    low_n = max(1, min(low_n, high_n))
    return low_n, high_n


# --- The team as data --------------------------------------------------------------
@dataclass
class Agent:
    name: str
    manager: str | None = None
    role: str = ""
    may_lead: bool = False
    agent: str = AGENT_GENERAL
    model: str = ""
    state: str = WORKING
    goal: str = ""
    # When it was added, for tree order; when it began waiting, for queue order.
    order: int = 0
    waiting_since: int = 0
    # What waits for its next turn while it is not working: its brief before
    # its first, the messages and reports that will wake it.
    inbox: list[str] = field(default_factory=list)
    # Seconds since the epoch its last turn ended; None while it never has.
    last_ended: float | None = None
    # Anything the runtime keeps with it (its chat, its row); never read here.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Post:
    """One message of the room."""

    n: int
    sender: str
    to: list[str]
    recipients: list[str]
    text: str
    report: bool = False
    created: float = 0.0

    def addressed(self) -> str:
        return ", ".join(self.to)


@dataclass
class Effect:
    """What the runtime has to do after an operation: `deliver` a text into an
    agent's running turn, `start` an agent's turn with its inbox, `stop` one."""

    kind: str
    name: str
    texts: list[str] = field(default_factory=list)


@dataclass
class Team:
    min: int = 1
    max: int = 4
    # Fixed with the system prompt: the team tools are listed.
    offered: bool = True
    # The model's capacity now (it may have changed since the prompt was made).
    parallel: int | None = None
    single_session: bool = False
    # Spec 3.3.0 (subagents.md §4): whether any model could serve as a
    # subagent -- the lead's, serving two at once, or another usable one off
    # its one-model server. None: judged by the lead's model alone, as 3.2.0.
    serves: bool | None = None
    # The lead and every member on the team (stopped ones are gone from it).
    agents: list[Agent] = field(default_factory=lambda: [Agent(LEAD, state=WORKING)])
    room: list[Post] = field(default_factory=list)
    # `task` helpers running in the team, by the agent that started each.
    helpers: list[str] = field(default_factory=list)
    # The lead's own goal: the last `goal` it gave.
    goal: str = ""
    seq: int = 0
    # Names the person stopped, not yet said to the lead (`team_stopped`).
    stopped_unsaid: list[str] = field(default_factory=list)
    # The size setting as the lead was last told it (`team_note_state`).
    told: str | None = None
    effects: list[Effect] = field(default_factory=list)

    # -- lookups --
    def get(self, name: str) -> Agent | None:
        return next((a for a in self.agents if a.name == name), None)

    @property
    def lead(self) -> Agent:
        agent = self.get(LEAD)
        assert agent is not None
        return agent

    def members(self) -> list[Agent]:
        return [a for a in self.agents if a.name != LEAD]

    def children(self, name: str) -> list[Agent]:
        return sorted((a for a in self.agents if a.manager == name), key=lambda a: a.order)

    def tree(self) -> list[tuple[Agent, int]]:
        """Every agent in tree order -- an agent, then its members in the order
        they were added, depth first -- with its depth below the lead."""
        out: list[tuple[Agent, int]] = []

        def walk(agent: Agent, depth: int) -> None:
            out.append((agent, depth))
            for child in self.children(agent.name):
                walk(child, depth + 1)

        walk(self.lead, 0)
        return out

    def names(self) -> list[str]:
        return [a.name for a, _ in self.tree()]

    def subtree(self, name: str) -> list[str]:
        """The agent and everyone under it, in tree order."""
        out: list[str] = []

        def walk(agent_name: str) -> None:
            out.append(agent_name)
            for child in self.children(agent_name):
                walk(child.name)

        walk(name)
        return out

    def under(self, name: str) -> list[str]:
        return self.subtree(name)[1:]

    def size(self) -> int:
        """The lead, every member whatever its state, every `task` helper
        running in the team (team.md §3)."""
        return len(self.agents) + len(self.helpers)

    def capacity_ok(self) -> bool:
        if self.serves is not None:
            return self.serves
        return capacity_allows(self.parallel, self.single_session)

    def slots(self) -> int | None:
        return slots(self.parallel, self.single_session)

    def busy(self) -> int:
        return len([a for a in self.agents if a.state == WORKING]) + len(self.helpers)


# --- §2: offered, and the requirement -----------------------------------------------
def offered_tools(team: Team, by: str = LEAD) -> list[str]:
    """The team tools listed for an agent, sorted."""
    if not team.offered:
        return []
    if by == LEAD:
        return sorted(TOOLS)
    agent = team.get(by)
    if agent is None:
        return []
    return sorted(TOOLS if agent.may_lead else MEMBER_TOOLS)


def unmet(team: Team, by: str = LEAD) -> str:
    """What a team tool answers while its requirement `team` is not met, or ""
    when it is: met when `max ≥ 2` and the model serves two at once -- or
    while the team still has a member (so a lead whose person lowered `max`
    can still talk to, read and stop the members it has)."""
    if team.members():
        return ""
    if team.max >= 2 and team.capacity_ok():
        return ""
    unavailable = spec()["unavailable"]
    return unavailable["one_agent"] if team.capacity_ok() else unavailable["one_request"]


# --- §6: a manager does not do the work ---------------------------------------------
def is_manager(team: Team, name: str) -> bool:
    """An agent with at least one member (a finished one included), and the
    lead from the start when `min ≥ 2` and the team is offered."""
    if team.children(name):
        return True
    return name == LEAD and team.offered and team.min >= 2


def _spec_risk(tool: str) -> str:
    spec_tool = harness_spec.tools().get(tool) or {}
    return str(spec_tool.get("risk") or "")


def _bash_allowed(command: str) -> bool:
    """Whether the default rules alone allow this command line in manual mode
    (`permission/defaults.json`, no rules of the person's): every command in
    it allowed by the `bash` map, the last matching pattern winning."""
    from lembas.services.agent import shell

    rules = harness_spec._json("permission/defaults.json")["rules"].get("bash") or {}
    split = shell.split_command(str(command or ""))
    if split.unsafe or not split.commands:
        return False
    for one in split.commands:
        action = rules.get("*", "ask") if isinstance(rules, dict) else "ask"
        for pattern, verdict in rules.items():
            if _wildcard(pattern, one.strip()):
                action = verdict
        if action != "allow":
            return False
    return True


def _wildcard(pattern: str, text: str) -> bool:
    """The rules' wildcard: `*` any run, and a trailing ` *` optional, so
    `git status *` matches `git status` too."""
    body = re.escape(pattern).replace(r"\*", ".*")
    if pattern.endswith(" *"):
        body = re.escape(pattern[:-2]).replace(r"\*", ".*") + r"( .*)?"
    return re.fullmatch(body, text, re.DOTALL) is not None


def is_work(tool: str, risk: str = "", args: dict[str, Any] | None = None) -> bool:
    """Whether a call is **work** (team.md §6): its risk class `write` or
    `execute`, but `job_stop`, and a `bash` call the default rules allow."""
    work = spec()["work"]
    if tool in (work.get("kept") or []):
        return False
    if tool == "bash":
        return not _bash_allowed(str((args or {}).get("command") or ""))
    if tool in (work.get("write") or []) or tool in (work.get("execute") or []):
        return True
    # The spec's class for a spec tool (this server files `memory` as a
    # write, the spec as `interact`); a tool of this server's own -- an image,
    # a schedule, an MCP tool -- is placed by its own.
    risk = _spec_risk(tool) or risk
    return risk in ("write", "execute")


def manager_refusal(team: Team, by: str, tool: str, risk: str = "", args=None) -> str:
    """The fixed refusal a manager's work call answers, or ""."""
    if tool in TOOLS:
        return ""
    if is_manager(team, by) and is_work(tool, risk, args):
        return texts()["manager"]
    return ""


# --- §8: slots ----------------------------------------------------------------------
def _has_slot(team: Team) -> bool:
    limit = team.slots()
    return limit is None or team.busy() < limit


def _start(team: Team, agent: Agent) -> None:
    agent.state = WORKING
    team.effects.append(Effect("start", agent.name, list(agent.inbox)))
    agent.inbox = []


def _wants(team: Team, agent: Agent) -> None:
    """An agent has work: it starts at once with a free slot, else waits."""
    if agent.state == WORKING:
        return
    if agent.state != WAITING:
        team.seq += 1
        agent.waiting_since = team.seq
        agent.state = WAITING
    schedule(team)


def schedule(team: Team) -> None:
    """Start waiting agents while slots are free: the lead first (the person
    waits on it), then the others in the order they began waiting."""
    while _has_slot(team):
        waiting = [a for a in team.agents if a.state == WAITING]
        if not waiting:
            return
        waiting.sort(key=lambda a: (a.name != LEAD, a.waiting_since))
        _start(team, waiting[0])


# --- §5, §7: the room and delivery --------------------------------------------------
def mark(sender: str, to: str, *, report: bool = False) -> str:
    marks = spec()["marks"]
    return harness_spec.fill(marks["report" if report else "message"], {"from": sender, "to": to})


def _deliver(team: Team, name: str, text: str, delivered: dict[str, list[str]]) -> None:
    agent = team.get(name)
    if agent is None:
        return
    delivered.setdefault(name, []).append(text)
    if agent.state == WORKING:
        team.effects.append(Effect("deliver", name, [text]))
        return
    agent.inbox.append(text)
    _wants(team, agent)


def post(
    team: Team,
    sender: str,
    to: list[str],
    recipients: list[str],
    text: str,
    *,
    report: bool = False,
    now: float = 0.0,
) -> tuple[Post, dict[str, list[str]]]:
    """Put a message in the room and deliver it to each recipient, marked."""
    entry = Post(
        n=len(team.room) + 1 if not team.room else team.room[-1].n + 1,
        sender=sender,
        to=list(to),
        recipients=list(recipients),
        text=text,
        report=report,
        created=now,
    )
    team.room.append(entry)
    body = f"{mark(sender, entry.addressed(), report=report)}\n{text}"
    delivered: dict[str, list[str]] = {}
    for name in recipients:
        _deliver(team, name, body, delivered)
    return entry, delivered


def normalise_to(raw: Any) -> list[str]:
    """`to` as the spec reads it: a lone string is a one-name list; names
    deduplicated in the order given; `all` anywhere means all."""
    if isinstance(raw, str):
        names = [raw]
    elif isinstance(raw, list):
        names = [str(item) for item in raw]
    else:
        names = []
    names = [n.strip() for n in names if str(n).strip()]
    if ALL in names:
        return [ALL]
    out: list[str] = []
    for name in names:
        if name not in out:
            out.append(name)
    return out


def send(team: Team, by: str, raw_to: Any, message: str, *, now: float = 0.0):
    """`team_send`: (the answer, what was delivered)."""
    if refused := unmet(team, by):
        return refused, {}
    to = normalise_to(raw_to)
    text = str(message or "").strip()
    if not to or not text:
        return "team_send needs `to` (names, or [\"all\"]) and a `message`.", {}
    if to == [ALL]:
        recipients = [n for n in team.names() if n != by]
        if not recipients:
            return texts()["nobody"], {}
    else:
        unknown = next((n for n in to if team.get(n) is None), None)
        if unknown is not None:
            return _say("unknown", name=unknown, names=", ".join(team.names())), {}
        if by in to:
            return texts()["self"], {}
        recipients = list(to)
    _entry, delivered = post(team, by, to, recipients, text, now=now)
    return _say("sent", to=", ".join(to)), delivered


def may_see(team: Team, by: str, entry: Post) -> bool:
    """The lead sees every message; any other agent those whose sender or one
    of whose recipients is in its subtree."""
    if by == LEAD:
        return True
    mine = set(team.subtree(by)) if team.get(by) is not None else {by}
    return entry.sender in mine or any(name in mine for name in entry.recipients)


def visible(team: Team, by: str, since: int = 0) -> list[Post]:
    return [p for p in team.room if p.n > since and may_see(team, by, p)]


def _ago(seconds: float) -> str:
    from lembas.services import jobs

    return f"{jobs.duration(seconds)} ago"


def read_text(team: Team, by: str, since: int = 0, *, now: float = 0.0) -> tuple[str, list[Post]]:
    """`team_read`'s answer: the tree, then the room since `since` that the
    caller may see, at most `read_max` (`loop.json` `team.read`)."""
    shape = spec()["read"]
    states = shape["states"]
    lines = [harness_spec.fill(shape["tree"], {"size": str(team.size()), "max": str(team.max)})]
    for agent, depth in team.tree():
        if agent.state == WORKING:
            last = shape["active"]
        elif agent.last_ended is not None:
            last = _ago(max(0.0, now - agent.last_ended))
        else:
            last = "—"
        role = shape["lead_role"] if agent.name == LEAD else agent.role
        lines.append(
            harness_spec.fill(
                shape["agent"],
                {
                    "indent": "  " * depth,
                    "name": agent.name,
                    "role": role,
                    "state": states.get(agent.state, agent.state),
                    "model": agent.model or "—",
                    "last": last,
                },
            )
        )
    lines.append("")
    seen = visible(team, by, since)
    limit = int(spec().get("read_max") or 50)
    shown = seen[:limit]
    if not shown:
        lines.append(harness_spec.fill(shape["none"], {"since": str(since)}))
    else:
        lines.append(harness_spec.fill(shape["room"], {"since": str(since)}))
        for entry in shown:
            first, *rest = (entry.text or "").split("\n")
            text = first + "".join(f"\n  {line}" for line in rest)
            template = shape["report"] if entry.report else shape["message"]
            lines.append(
                harness_spec.fill(
                    template,
                    {"n": str(entry.n), "from": entry.sender, "to": entry.addressed(),
                     "text": text},
                )
            )
        if len(seen) > len(shown):
            lines.append(harness_spec.fill(shape["more"], {"last": str(shown[-1].n)}))
    return "\n".join(lines), shown


# --- §4: adding ---------------------------------------------------------------------
@dataclass
class Added:
    text: str
    agent: Agent | None = None


def add(
    team: Team, by: str, args: dict[str, Any], *, model: str = "", model_refusal: str = ""
) -> Added:
    """`team_add`: refusals in the spec's order, then the member, working or
    waiting for a slot. `model` is what the runtime resolved `args.model` to
    (shown in the tree); an unknown agent or model is the runtime's to refuse,
    as `task` does, before this is called. `model_refusal` is the model's
    (spec 3.3.0, subagents.md §2-3: `not_named`, `same_group`,
    `one_request`), said after the team's own refusals and before anything
    is added (team.md §4)."""
    if refused := unmet(team, by):
        return Added(refused)
    name = str(args.get("name") or "").strip()
    if name == ALL:
        return Added(texts()["all_name"])
    if not NAME.fullmatch(name):
        return Added(_say("bad_name", name=name))
    if team.get(name) is not None:
        return Added(_say("taken", name=name))
    goal = str(args.get("goal") or "").strip()
    if by == LEAD and not goal and not team.goal:
        return Added(texts()["no_goal"])
    if team.size() + 1 > team.max:
        return Added(_say("full", max=team.max))
    if model_refusal:
        return Added(model_refusal)
    role = " ".join(str(args.get("role") or "").split())
    brief = str(args.get("task") or "").strip()
    if not role or not brief:
        return Added("team_add needs a `role` (one line: what it owns) and a `task` (its brief).")

    caller = team.get(by)
    if by == LEAD and goal:
        team.goal = goal
    told_goal = goal or (team.goal if by == LEAD else (caller.goal if caller else team.goal))
    agent_kind = str(args.get("agent") or "").strip() or AGENT_GENERAL
    team.seq += 1
    member = Agent(
        name=name,
        manager=by,
        role=role,
        may_lead=bool(args.get("lead")),
        agent=agent_kind,
        model=model,
        state=FINISHED,
        goal=told_goal,
        order=team.seq,
        inbox=[brief],
    )
    team.agents.append(member)
    _wants(team, member)
    values = {"name": name, "role": role, "size": team.size(), "max": team.max}
    if member.state == WORKING:
        return Added(_say("added", **values), member)
    return Added(_say("added_waiting", parallel=team.slots() or 0, **values), member)


# --- §9: stopping -------------------------------------------------------------------
def _remove(team: Team, names: list[str]) -> None:
    for name in names:
        agent = team.get(name)
        if agent is None:
            continue
        team.agents.remove(agent)
        team.effects.append(Effect("stop", name))
    schedule(team)


def stop(team: Team, by: str, name: str) -> tuple[str, list[str]]:
    """`team_stop`: (the answer, the stopped in tree order)."""
    if refused := unmet(team, by):
        return refused, []
    target = str(name or "").strip()
    if target == ALL:
        names = team.under(by)
        if not names:
            return texts()["nobody_to_stop"], []
    else:
        if team.get(target) is None:
            return _say("unknown", name=target, names=", ".join(team.names())), []
        if target == by or target not in team.under(by):
            return _say("not_yours", name=target), []
        names = team.subtree(target)
    _remove(team, names)
    return _say("stopped", names=", ".join(names), size=team.size(), max=team.max), names


def person_stop(team: Team, name: str) -> list[str]:
    """The person stopped a member in the UI: it and its subtree leave, and the
    lead is told in a note (`team_stopped`)."""
    if name == LEAD or team.get(name) is None:
        return []
    names = team.subtree(name)
    _remove(team, names)
    team.stopped_unsaid.extend(names)
    return names


# --- A turn ends ----------------------------------------------------------------------
def report_text(reply: str, reason: str = "stop") -> str:
    """A member's report: its final reply, or `no_report`; a turn that ended
    otherwise than by the model stopping says how, after a blank line."""
    text = str(reply or "").strip() or texts()["no_report"]
    if reason and reason != "stop":
        text += "\n\n" + _say("ended", reason=reason)
    return text


def finish(
    team: Team, by: str, reply: str = "", reason: str = "stop", *, now: float = 0.0
) -> dict[str, list[str]]:
    """An agent's turn ended. A member's reply is its report to its manager,
    posted in the room and delivered; its slot frees. Returns what was
    delivered."""
    agent = team.get(by)
    if agent is None:
        return {}
    agent.last_ended = now
    delivered: dict[str, list[str]] = {}
    if by == LEAD:
        agent.state = WAITING if agent.inbox else IDLE
        if agent.inbox:
            team.seq += 1
            agent.waiting_since = team.seq
    else:
        agent.state = FINISHED
        manager = agent.manager or LEAD
        _entry, delivered = post(
            team, by, [manager], [manager], report_text(reply, reason), report=True, now=now
        )
    schedule(team)
    return delivered


def person_message(team: Team) -> None:
    """The person's message to the lead: its turn runs (the server never
    holds the person back for a slot; the lead goes first anyway)."""
    lead = team.lead
    if lead.state != WORKING:
        lead.state = WORKING
        lead.inbox = []


def task_started(team: Team, by: str) -> None:
    team.helpers.append(by)


def task_done(team: Team, by: str) -> None:
    if by in team.helpers:
        team.helpers.remove(by)
    schedule(team)


def task_refusal(team: Team) -> str:
    """What `task` answers while a team is offered and one more helper would
    pass `max`: `one_agent` when `max` is 1 and the lead has no member,
    else `full`. "" when it may run."""
    if not team.offered:
        return ""
    if team.size() + 1 <= team.max:
        return ""
    if team.max == 1 and not team.members():
        return spec()["unavailable"]["one_agent"]
    return _say("full", max=team.max)


# --- §10: the note --------------------------------------------------------------------
def size_state(low: int, high: int, own: bool = False) -> str:
    """What the lead is told of the size, as one comparable value."""
    return f"{low}-{high}" + ("+own" if own else "")


def size_block(low: int, high: int, own: bool = False) -> str:
    """The note's block for the size setting: `team`, with `team_lead` after
    it when `min ≥ 2`; `team_one` in its place when `max` is 1. With `own`
    (spec 3.3.0, subagents.md §4: the session's model cannot serve subagents
    itself, another could), `team_own` after them in the same block."""
    from lembas.services import subagents as subagents_rules

    note = harness_spec.note()
    if high <= 1:
        return note["team_one"]
    block = harness_spec.fill(note["team"], {"min": str(low), "max": str(high)})
    if low >= 2:
        block += "\n" + harness_spec.fill(note["team_lead"], {"members": str(low - 1)})
    if own:
        block += "\n" + subagents_rules.note_own()
    return block


def stopped_block(names: list[str]) -> str:
    return harness_spec.fill(harness_spec.note()["team_stopped"], {"names": ", ".join(names)})


def note_blocks(team: Team) -> list[str]:
    """The team's blocks for the next user message, consuming what they say."""
    if not team.offered:
        return []
    blocks: list[str] = []
    own = bool(team.offered and team.serves and not capacity_allows(team.parallel))
    now = size_state(team.min, team.max, own)
    if team.told != now:
        blocks.append(size_block(team.min, team.max, own))
        team.told = now
    if team.stopped_unsaid:
        blocks.append(stopped_block(team.stopped_unsaid))
        team.stopped_unsaid = []
    return blocks


def wrap_note(blocks: list[str]) -> str | None:
    if not blocks:
        return None
    note = harness_spec.note()
    return f"{note['open']}\n" + "\n\n".join(blocks) + f"\n{note['close']}"


def state_rows(team: Team) -> list[dict[str, Any]]:
    return [{"name": a.name, "manager": a.manager, "state": a.state} for a, _ in team.tree()]


__all__ = [
    "ALL",
    "FINISHED",
    "IDLE",
    "LEAD",
    "MEMBER_TOOLS",
    "TOOLS",
    "WAITING",
    "WORKING",
    "Added",
    "Agent",
    "Effect",
    "Post",
    "Team",
    "add",
    "bounds_refusal",
    "capacity_allows",
    "defaults",
    "finish",
    "task_done",
    "task_started",
    "is_manager",
    "is_work",
    "manager_refusal",
    "mark",
    "may_see",
    "normalise_to",
    "note_blocks",
    "offered_tools",
    "person_message",
    "person_stop",
    "post",
    "read_setting",
    "read_text",
    "report_text",
    "schedule",
    "send",
    "size_block",
    "size_state",
    "slots",
    "state_rows",
    "stop",
    "stopped_block",
    "task_refusal",
    "unmet",
    "visible",
    "wrap_note",
]
