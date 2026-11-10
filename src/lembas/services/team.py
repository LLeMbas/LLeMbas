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

# Every team tool (spec 3.4.0, subagents.md §8).
TOOLS = (
    "team_add",
    "team_assign",
    "team_decide",
    "team_findings",
    "team_finish",
    "team_read",
    "team_replace",
    "team_request",
    "team_send",
    "team_stop",
    "team_whoami",
)
# The main agent's (§8): it alone adds, assigns, replaces, stops and decides.
LEAD_TOOLS = (
    "team_add",
    "team_assign",
    "team_decide",
    "team_findings",
    "team_read",
    "team_replace",
    "team_send",
    "team_stop",
    "team_whoami",
)
# A subagent's, sub-managers included; `team_request` only with request options.
MEMBER_TOOLS = ("team_findings", "team_finish", "team_read", "team_send", "team_whoami")

LEAD = "lead"
ALL = "all"
PARENT = "parent"

WORKING = "working"
WAITING = "waiting"
FINISHED = "finished"
IDLE = "idle"
# Spec 3.4.0 (§9): a subagent whose context filled runs its last turn, then ends.
PAUSED = "paused"
DONE = "done"
OUT_OF_CONTEXT = "out_of_context"
STOPPED = "stopped"
ENDED = (DONE, OUT_OF_CONTEXT, STOPPED)

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
    # Spec 3.4.0 (subagents.md §5): `lead` for the main agent, s1, s2, ... for
    # subagents, never reused in the session.
    id: str = ""
    # Its task as given, and as `team_assign` replaced it.
    task: str = ""
    # Its permission mode and request options (§7), and what the main agent
    # allowed it (`team_decide`): `{"tool", "permission", "pattern"}` each.
    mode: str = ""
    request: list[str] = field(default_factory=list)
    grants: list[dict[str, str]] = field(default_factory=list)
    # Its findings (§8): `{"n", "text", "time"}`, kept past its end.
    findings: list[dict[str, Any]] = field(default_factory=list)
    # Its context use as its engine last said it: `{"used", "window"}`.
    context: dict[str, int] | None = None
    # Its current progress (progress.md), or None.
    progress: dict[str, Any] | None = None
    # How it ended and when (seconds since the epoch), once it has.
    ended_at: float | None = None
    # The sub-manager it was under before that one ended by itself.
    moved_from: str = ""


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
    # Spec 3.4.0: message, report, assignment, request or decision.
    kind: str = ""

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
    # Spec 3.3.0: how many requests at once each *other* model a member runs on
    # serves (its `connection/model` ref → `parallel`, None for no limit). A
    # member on the lead's model shares the lead's slots; one on a model the
    # person named has that model's own (team.md §8: a slot of *its* model).
    model_slots: dict[str, int | None] = field(default_factory=dict)
    # The lead and every member on the team (stopped ones are gone from it).
    agents: list[Agent] = field(
        default_factory=lambda: [Agent(LEAD, state=WORKING, id=LEAD, mode="auto")]
    )
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
    # Spec 3.4.0: subagents that ended, in the order they ended; the last id
    # and request number given; the requests to the main agent.
    ended: list[Agent] = field(default_factory=list)
    id_seq: int = 0
    request_seq: int = 0
    requests: list[dict[str, Any]] = field(default_factory=list)
    # Steps a turn may take, the main agent's and a subagent's (team_whoami).
    steps_main: int = 0
    steps_sub: int = 0
    # What each agent's latest turn was started with (its brief, or the
    # messages that woke it), by name.
    started: dict[str, str] = field(default_factory=dict)

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
        return sorted(LEAD_TOOLS)
    agent = team.get(by)
    if agent is None:
        return []
    return member_tools(agent)


def member_tools(agent: Agent) -> list[str]:
    """A subagent's team tools (§8): `team_request` only with request options."""
    return sorted([*MEMBER_TOOLS, *(["team_request"] if agent.request else [])])


def main_only_refusal(by: str, tool: str) -> str:
    """`only_main` for a subagent's call to a tool only the main agent has --
    `task` included (§6) -- or ""."""
    if by == LEAD:
        return ""
    main_only = set(spec()["main_only"]["tools"]) | {"task"}
    return texts()["only_main"] if tool in main_only else ""


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
def _model_of(team: Team, agent: Agent) -> str:
    return agent.model or team.lead.model


def _own_model(team: Team, model: str) -> bool:
    return model == team.lead.model or model not in team.model_slots


def _has_slot(team: Team, agent: Agent | None = None) -> bool:
    """A free slot on the model `agent` runs on (the lead's when None): the
    lead's slots are shared by everything on its model and the `task`
    subagents; another model's, by the members on it."""
    model = _model_of(team, agent) if agent is not None else team.lead.model
    if _own_model(team, model):
        limit = team.slots()
        busy = len(
            [
                a
                for a in team.agents
                if a.state == WORKING and _own_model(team, _model_of(team, a))
            ]
        ) + len(team.helpers)
    else:
        limit = team.model_slots[model]
        busy = len(
            [a for a in team.agents if a.state == WORKING and _model_of(team, a) == model]
        )
    return limit is None or busy < max(int(limit), 1)


def _start(team: Team, agent: Agent) -> None:
    agent.state = WORKING
    team.effects.append(Effect("start", agent.name, list(agent.inbox)))
    team.started[agent.name] = "\n\n".join(agent.inbox)
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
    """Start waiting agents while their models have free slots: the lead first
    (the person waits on it), then the others in the order they began
    waiting -- each against its own model's slots."""
    waiting = [a for a in team.agents if a.state == WAITING]
    waiting.sort(key=lambda a: (a.name != LEAD, a.waiting_since))
    for agent in waiting:
        if _has_slot(team, agent):
            _start(team, agent)


# --- §5, §7: the room and delivery --------------------------------------------------
def mark(sender: str, to: str, *, report: bool = False, kind: str = "", id: str = "") -> str:
    marks = spec()["marks"]
    kind = kind or ("report" if report else "message")
    return harness_spec.fill(marks[kind], {"from": sender, "to": to, "id": id})


def _deliver(team: Team, name: str, text: str, delivered: dict[str, list[str]]) -> None:
    agent = team.get(name)
    if agent is None:
        return
    # Spec 3.4.0 (§9): a paused subagent is given nothing new; the message
    # stays in the room for whoever replaces it.
    if agent.state == PAUSED:
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
    kind: str = "",
    id: str = "",
) -> tuple[Post, dict[str, list[str]]]:
    """Put a message in the room and deliver it to each recipient, marked."""
    kind = kind or ("report" if report else "message")
    entry = Post(
        n=len(team.room) + 1 if not team.room else team.room[-1].n + 1,
        sender=sender,
        to=list(to),
        recipients=list(recipients),
        text=text,
        report=report,
        created=now,
        kind=kind,
    )
    team.room.append(entry)
    body = f"{mark(sender, entry.addressed(), kind=kind, id=id)}\n{text}"
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
    # Spec 3.4.0: `parent` is the sender's parent.
    if PARENT in to:
        sender = team.get(by)
        manager = sender.manager if sender is not None else None
        if not manager:
            return texts()["no_parent"], {}
        to = normalise_to([manager if n == PARENT else n for n in to])
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


def _mode_words(agent: Agent) -> str:
    mode = agent.mode or "auto"
    if not agent.request:
        return mode
    kinds = harness_spec.fill(spec()["read"]["request"], {"kinds": ", ".join(agent.request)})
    return f"{mode} {kinds}"


def percent(context: dict[str, int] | None) -> int | None:
    """floor(used × 100 / window), or None where the window is unknown."""
    if not context or int(context.get("window") or 0) <= 0:
        return None
    return int(context.get("used") or 0) * 100 // int(context["window"])


def _context_words(agent: Agent) -> str:
    value = percent(agent.context)
    if value is None:
        return "—"
    return harness_spec.fill(spec()["read"]["context"], {"percent": str(value)})


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
                    "id": agent.id or agent.name,
                    "role": role,
                    "state": states.get(agent.state, agent.state),
                    "model": agent.model or team.lead.model or "—",
                    "mode": _mode_words(agent),
                    "context": _context_words(agent),
                    "last": last,
                },
            )
        )
    if slots_open := open_slots(team):
        lines.append(harness_spec.fill(shape["slots"], {"n": str(slots_open)}))
    if team.ended:
        lines.append(shape["ended"])
        for agent in team.ended:
            lines.append(
                harness_spec.fill(
                    shape["ended_agent"],
                    {
                        "name": agent.name,
                        "id": agent.id,
                        "role": agent.role,
                        "state": states.get(agent.state, agent.state),
                        "findings": str(len(agent.findings)),
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
    if refused := main_only_refusal(by, "team_add"):
        return Added(refused)
    name = str(args.get("name") or "").strip()
    if name == ALL:
        return Added(texts()["all_name"])
    if not NAME.fullmatch(name):
        return Added(_say("bad_name", name=name))
    if team.get(name) is not None or name == PARENT:
        return Added(_say("taken", name=name))
    goal = str(args.get("goal") or "").strip()
    if not goal and not team.goal:
        return Added(texts()["no_goal"])
    if team.size() + 1 > team.max:
        return Added(_say("full", max=team.max))
    parent_name = str(args.get("parent") or "").strip() or LEAD
    placed = _placement(team, parent_name, args.get("mode"), args.get("request"))
    if isinstance(placed, str):
        return Added(placed)
    if model_refusal:
        return Added(model_refusal)
    role = " ".join(str(args.get("role") or "").split())
    brief = str(args.get("task") or "").strip()
    if not role or not brief:
        return Added("team_add needs a `role` (one line: what it owns) and a `task` (its brief).")

    if goal:
        team.goal = goal
    parent = team.get(parent_name) or team.lead
    agent_kind = str(args.get("agent") or "").strip() or AGENT_GENERAL
    team.seq += 1
    member = Agent(
        name=name,
        manager=parent.name,
        role=role,
        may_lead=bool(args.get("lead")),
        agent=agent_kind,
        model=model,
        state=FINISHED,
        goal=goal or parent.goal or team.goal,
        order=team.seq,
        inbox=[brief],
        id=_next_id(team),
        task=brief,
        mode=placed[0],
        request=placed[1],
    )
    team.agents.append(member)
    _wants(team, member)
    values = {"name": name, "role": role, "size": team.size(), "max": team.max}
    if member.state == WORKING:
        return Added(_say("added", **values), member)
    model = _model_of(team, member)
    limit = team.slots() if _own_model(team, model) else team.model_slots[model]
    return Added(_say("added_waiting", parallel=limit or 0, **values), member)


def _next_id(team: Team) -> str:
    team.id_seq += 1
    return f"{spec()['names']['id_prefix']}{team.id_seq}"


def _placement(
    team: Team, parent_name: str, mode: Any, request: Any
) -> tuple[str, list[str]] | str:
    """Spec 3.4.0 (§6, §7): `parent` is the main agent or a sub-manager, and
    the mode is the parent's or stricter -- the refusal, or (mode, request)."""
    parent = team.get(parent_name)
    if parent is None:
        return _say("unknown", name=parent_name, names=", ".join(team.names()))
    if parent.name != LEAD and not parent.may_lead:
        return _say("not_manager", name=parent.name)
    return check_mode(parent.mode or "auto", mode, request)


# --- Spec 3.4.0 §7: modes and request options -----------------------------------------
def mode_order() -> list[str]:
    """Loosest first (`permission/modes.json` `order`)."""
    return list(harness_spec.modes()["order"])


def _rank(mode: str) -> int:
    order = mode_order()
    # A mode the spec does not know reads as the strictest: fail towards asking.
    return order.index(mode) if mode in order else len(order)


def looser(a: str, b: str) -> bool:
    return _rank(a) < _rank(b)


def stricter(a: str, b: str) -> bool:
    return _rank(a) > _rank(b)


def strictest(a: str, b: str) -> str:
    return a if stricter(a, b) else b


def request_kinds() -> list[str]:
    return list(spec()["request"]["kinds"])


def check_mode(parent_mode: str, mode: Any, request: Any) -> tuple[str, list[str]] | str:
    """`team_add`'s and `team_assign`'s mode for a parent in `parent_mode`: the
    refusal, word for word, or (the mode, the request options) it gets."""
    chosen = str(mode).strip() if mode else parent_mode
    if looser(chosen, parent_mode):
        return _say("wider", parent_mode=parent_mode)
    kinds = [str(k) for k in (request or [])] if isinstance(request, list | tuple) else []
    if kinds and not stricter(chosen, parent_mode):
        return texts()["request_not_narrower"]
    bad = next((k for k in kinds if k not in request_kinds()), None)
    if bad is not None:
        return _say("request_kind", kind=bad)
    return chosen, list(dict.fromkeys(kinds))


def request_refusal(tool: str, risk: str, request: list[str]) -> str:
    """`team_request`'s own check: the tool's risk class must be an option."""
    if risk and risk in request:
        return ""
    return _say("not_requestable", tool=tool, kinds=", ".join(request) or "none")


def tighten(main: str, agents: list[dict[str, str]]) -> dict[str, str]:
    """The main agent made stricter (§7): every subagent looser than its parent
    is made as strict as it, down the tree. `agents`: `{name, parent, mode}`."""
    modes = {LEAD: main}
    out: dict[str, str] = {}
    pending = list(agents)
    while pending:
        index = next((i for i, a in enumerate(pending) if a["parent"] in modes), None)
        if index is None:
            break
        agent = pending.pop(index)
        modes[agent["name"]] = out[agent["name"]] = strictest(agent["mode"], modes[agent["parent"]])
    return out


ALLOW = "allow"
ASK = "ask"
DENY = "deny"


@dataclass(frozen=True)
class Verdict:
    """Who decides a subagent's call (§7): `run`, `person` (an approval card
    naming the subagent), or `refused` with its text."""

    answer: str
    text: str = ""


def who_decides(
    evaluate,
    *,
    mode: str,
    main: str,
    risk: str,
    request: list[str],
    grants: list[dict[str, str]],
    unattended: bool = False,
) -> Verdict:
    """Spec 3.4.0 (subagents.md §7). `evaluate(mode, grants)` is the
    project's own permission answer for the call in a mode -- `(action,
    reason)`, the action `allow`, `ask` or `deny` -- with the session's rules
    and these grants added. The call is judged in the subagent's mode (its
    grants count only where the main agent's own mode would allow the call);
    what it would ask is judged in the main agent's: allowed there, the
    subagent was narrowed (`requestable` or `not_delegated`); asked there
    too, nobody in the tree can allow it and it goes to the person."""
    main_action, main_reason = evaluate(main, [])
    own_action, own_reason = evaluate(mode, list(grants) if main_action == ALLOW else [])
    if own_action == ALLOW:
        return Verdict("run")
    if own_action == DENY:
        return Verdict("refused", f"Denied: {own_reason}")
    if main_action == ALLOW:
        return Verdict(
            "refused", texts()["requestable" if risk in request else "not_delegated"]
        )
    if main_action == DENY:
        return Verdict("refused", f"Denied: {main_reason}")
    if unattended:
        return Verdict("refused", "Refused: nobody can approve this in an unattended session.")
    return Verdict("person")


# The permission key of a tool, where it is not its name (`permission/defaults.json`).
_PERMISSION_KEYS = {
    "write": "edit",
    "edit": "edit",
    "multiedit": "edit",
    "apply_patch": "edit",
    "plan_write": "plan",
    "plan_update": "plan",
}


def permission_key(tool: str) -> str:
    return _PERMISSION_KEYS.get(tool, tool)


def _rule_action(rules: Any, subject: str) -> str:
    """One permission key's rules applied to a subject: a single action, or
    the last matching pattern of a map ("" when none matches)."""
    if isinstance(rules, str):
        return rules
    action = ""
    for pattern, verdict in (rules or {}).items():
        if _wildcard(pattern, subject):
            action = verdict
    return action


def spec_evaluate(
    tool: str, risk: str, subject: str, mode: str, grants: list[dict[str, str]]
) -> tuple[str, str]:
    """The spec's permission answer for one call in a mode (`modes.json` with
    the default rules, `defaults.json`), and these grants: `(allow | ask |
    deny, reason)`. What a subagent's call is judged by (subagents.md §7);
    `subject` is the command line of `bash`, else the path or the call's
    target. A chat's own tools on this server are never put to the person, so
    this is used only to judge a subagent against its main agent."""
    from lembas.services.agent import policy, shell

    command = subject if tool == "bash" else ""
    paths = (subject,) if subject and tool != "bash" else ()
    if why := policy.refusal(tool, command, paths):
        return DENY, why
    defaults = harness_spec._json("permission/defaults.json")["rules"]
    key = permission_key(tool)
    granted = {
        g["pattern"]: ALLOW
        for g in grants
        if (g.get("permission") or permission_key(g.get("tool", ""))) == key
    }
    rules = defaults.get(key, defaults.get("*", ASK))
    if tool == "bash":
        merged = {**(rules if isinstance(rules, dict) else {"*": rules}), **granted}
        split = shell.split_command(command)
        if split.unsafe or not split.commands:
            action = ASK
        else:
            found = [_rule_action(merged, one.strip()) or ASK for one in split.commands]
            action = DENY if DENY in found else ASK if ASK in found else ALLOW
    else:
        merged = {**(rules if isinstance(rules, dict) else {"*": rules}), **granted}
        action = _rule_action(merged, subject or "*") or str(defaults.get("*", ASK))
    if action == DENY:
        return DENY, "a rule refuses it"
    if mode == "auto":
        return ALLOW, ""
    if mode == "edit" and risk in ("read", "write"):
        # Inside what it may reach runs, unless a rule written for that tool
        # asks (reading .env asks in every mode but auto).
        asked = risk == "read" and action == ASK and isinstance(defaults.get(key), dict)
        return (ASK, "") if asked else (ALLOW, "")
    if mode == "plan" and risk in ("write", "execute"):
        return (ALLOW, "") if action == ALLOW else (DENY, harness_spec.plan_refused())
    return (ALLOW, "") if action == ALLOW else (ASK, "")


# --- §9: stopping -------------------------------------------------------------------
def _remove(team: Team, names: list[str], end: str = STOPPED, *, now: float = 0.0) -> None:
    """Agents leave the tree: ended (spec 3.4.0), kept in `ended` with how and
    when. The subagents under one that ended by itself move under its parent
    until the main agent replaces it; a stopped one's go with it (callers pass
    the whole subtree)."""
    for name in names:
        agent = team.get(name)
        if agent is None:
            continue
        team.agents.remove(agent)
        agent.state = end
        agent.ended_at = now
        agent.inbox = []
        team.ended.append(agent)
        for entry in team.requests:
            if entry["from"] == name:
                entry["open"] = False
        if end == STOPPED:
            team.effects.append(Effect("stop", name))
        else:
            team.effects.append(Effect("end", name))
            for child in team.agents:
                if child.manager == name:
                    child.manager = agent.manager or LEAD
                    child.moved_from = name
    schedule(team)


def stop(team: Team, by: str, name: str) -> tuple[str, list[str]]:
    """`team_stop`: (the answer, the stopped in tree order)."""
    if refused := unmet(team, by):
        return refused, []
    if refused := main_only_refusal(by, "team_stop"):
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
    delivered: dict[str, list[str]] = {}
    if agent.state == PAUSED:
        # Spec 3.4.0 (§9): the final turn after its context filled has ended.
        gone = _say("gone", name=agent.name, id=agent.id)
        said = str(reply or "").strip()
        text = f"{gone}\n\n{said}" if said else gone
        manager = agent.manager or LEAD
        _entry, delivered = post(team, by, [manager], [manager], text, report=True, now=now)
        _remove(team, [by], OUT_OF_CONTEXT, now=now)
        return delivered
    agent.last_ended = now
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


def task_refusal(team: Team, by: str = LEAD) -> str:
    """What `task` answers while a team is offered and one more helper would
    pass `max`: `one_agent` when `max` is 1 and the lead has no member,
    else `full`. "" when it may run. Spec 3.4.0: a subagent's `task` is the
    main agent's (`only_main`)."""
    if refused := main_only_refusal(by, "task"):
        return refused
    if not team.offered:
        return ""
    if team.size() + 1 <= team.max:
        return ""
    if team.max == 1 and not team.members():
        return spec()["unavailable"]["one_agent"]
    return _say("full", max=team.max)


# --- Spec 3.4.0: the main agent's tree (subagents.md §5-§13) -------------------------------
def find(team: Team, name_or_id: str) -> Agent | None:
    """A subagent by name (live first, else the latest that ended) or by id."""
    agent = team.get(name_or_id) or next((a for a in team.agents if a.id == name_or_id), None)
    if agent is not None:
        return agent
    return next(
        (a for a in reversed(team.ended) if name_or_id in (a.name, a.id)),
        None,
    )


def open_slots(team: Team) -> int:
    """§6: `min − 1` places for subagents not added yet, while offered."""
    if not team.offered or team.min < 2:
        return 0
    return max(0, team.min - 1 - len(team.members()))


def set_main_mode(team: Team, mode: str) -> None:
    """The main agent's mode changed: stricter reaches down the tree, looser
    changes nobody (§7)."""
    team.lead.mode = mode
    for agent, _depth in team.tree():
        if agent.manager:
            parent = team.get(agent.manager)
            agent.mode = strictest(agent.mode or mode, (parent.mode if parent else mode) or mode)


def set_context(team: Team, name: str, used: int, window: int) -> None:
    agent = team.get(name)
    if agent is not None:
        agent.context = {"used": int(used), "window": int(window)}


def set_progress(team: Team, name: str, progress: dict[str, Any] | None) -> None:
    agent = team.get(name)
    if agent is not None:
        agent.progress = progress


def _steps(team: Team, by: str) -> int:
    loop = harness_spec.loop()
    if by == LEAD:
        return team.steps_main or int(loop["steps"]["default"])
    return team.steps_sub or int(loop["jobs"]["subagent_steps"]["default"])


def whoami(team: Team, by: str) -> str:
    """`team_whoami` (§8), one line each in the spec's order."""
    agent = team.get(by)
    if agent is None:
        return _say("unknown", name=by, names=", ".join(team.names()))
    lines_spec = spec()["whoami"]["lines"]

    def line(key: str, **values: Any) -> str:
        return harness_spec.fill(lines_spec[key], {k: str(v) for k, v in values.items()})

    role = spec()["read"]["lead_role"] if by == LEAD else agent.role
    out = [line("who", name=agent.name, id=agent.id or agent.name, role=role)]
    if agent.manager:
        out.append(line("parent", parent=agent.manager))
        out.append(line("task", task=(agent.task or "").split("\n")[0]))
    out.append(line("mode", mode=agent.mode or "auto"))
    if agent.request:
        out.append(line("request", kinds=", ".join(agent.request)))
    out.append(line("model", model=agent.model or team.lead.model or "—"))
    spawn = lines_spec["spawn_yes"] if by == LEAD else lines_spec["spawn_no"]
    out.append(line("limits", size=team.size(), max=team.max, steps=_steps(team, by), spawn=spawn))
    if (value := percent(agent.context)) is not None:
        context = agent.context or {}
        out.append(
            line("context", used=context["used"], window=context["window"], percent=value)
        )
    return "\n".join(out)


def note_finding(team: Team, by: str, text: str, *, now: float = 0.0) -> str:
    """`team_findings` write (§8)."""
    agent = team.get(by)
    if agent is None:
        return _say("unknown", name=by, names=", ".join(team.names()))
    n = len(agent.findings) + 1
    agent.findings.append({"n": n, "text": str(text or "").strip(), "time": now})
    return _say("noted", n=n)


def _may_read(team: Team, by: str, target: Agent) -> bool:
    """Its own, one in its subtree (an ended one by where it was), or the main
    agent anybody's."""
    if by == LEAD or target is team.get(by):
        return True
    up = (target.moved_from or target.manager) if target.state in ENDED else target.manager
    for _guard in range(1000):
        if not up:
            return False
        if up == by:
            return True
        holder = team.get(up) or find(team, up)
        up = holder.manager if holder is not None else None
    return False


def findings_text(team: Team, target: Agent) -> str:
    shape = spec()["findings"]
    lines = [harness_spec.fill(shape["header"], {"name": target.name, "id": target.id})]
    if not target.findings:
        lines.append(texts()["no_findings"])
    for finding in target.findings:
        lines.append(
            harness_spec.fill(shape["item"], {"n": str(finding["n"]), "text": finding["text"]})
        )
    return "\n".join(lines)


def read_findings(team: Team, by: str, of: str = "") -> str:
    """`team_findings` read (§8): the caller's, or an agent's in its subtree."""
    target = find(team, of) if of else team.get(by)
    if target is None:
        return _say("unknown", name=of or by, names=", ".join(team.names()))
    if not _may_read(team, by, target):
        return texts()["not_yours_findings"]
    return findings_text(team, target)


def finish_task(team: Team, by: str, report: str, *, now: float = 0.0) -> tuple[str, dict]:
    """`team_finish` (§8): the report to its parent, and it ends (`done`)."""
    agent = team.get(by)
    if agent is None or not agent.manager:
        return texts()["no_parent"], {}
    manager = agent.manager
    body = str(report or "").strip() or texts()["no_report"]
    _entry, delivered = post(team, by, [manager], [manager], body, report=True, now=now)
    _remove(team, [by], DONE, now=now)
    return _say("finished", to=manager), delivered


def context_full_text(name: str) -> str:
    marks = spec()["marks"]
    head = harness_spec.fill(marks["context_full"], {"to": name})
    steps = spec()["context"]["final_steps"]
    return f"{head}\n{_say('context_full', steps=steps)}"


def pause(team: Team, name: str) -> str:
    """Its context is full (§9): paused, nothing new reaches it, and its one
    final turn is given this text (an Effect `final`). "" when not a live
    subagent or already paused."""
    agent = team.get(name)
    if agent is None or name == LEAD or agent.state == PAUSED:
        return ""
    agent.state = PAUSED
    agent.inbox = []
    text = context_full_text(name)
    team.effects.append(Effect("final", name, [text]))
    return text


def assign(team: Team, by: str, args: dict[str, Any], *, now: float = 0.0) -> tuple[str, dict]:
    """`team_assign` (§8): a new task for a subagent that has not ended."""
    if refused := unmet(team, by):
        return refused, {}
    if refused := main_only_refusal(by, "team_assign"):
        return refused, {}
    name = str(args.get("name") or "").strip()
    target = team.get(name)
    if target is None or name == LEAD:
        ended = find(team, name)
        if ended is not None and ended.state in ENDED:
            return _say("has_ended", name=name), {}
        return _say("unknown", name=name, names=", ".join(team.names())), {}
    parent_name = str(args.get("parent") or "").strip() or (target.manager or LEAD)
    parent = team.get(parent_name)
    if parent is None:
        return _say("unknown", name=parent_name, names=", ".join(team.names())), {}
    if parent.name != LEAD and not parent.may_lead:
        return _say("not_manager", name=parent.name), {}
    if parent.name in team.subtree(target.name):
        return _say("cycle", name=target.name, parent=parent.name), {}
    mode = strictest(target.mode or "auto", parent.mode or "auto")
    request = list(target.request)
    if args.get("mode"):
        wanted = str(args["mode"])
        keep = target.request if target.request and wanted != parent.mode else None
        checked = check_mode(parent.mode or "auto", wanted, keep)
        if isinstance(checked, str):
            return checked, {}
        mode = checked[0]
        request = [] if mode == parent.mode else list(target.request)
    task = str(args.get("task") or "").strip()
    target.task = task
    role = " ".join(str(args.get("role") or "").split())
    if role:
        target.role = role
    target.manager = parent.name
    target.moved_from = ""
    target.mode = mode
    target.request = request
    for child_name in team.under(target.name):
        child = team.get(child_name)
        holder = team.get(child.manager or LEAD) if child is not None else None
        if child is not None and holder is not None:
            child.mode = strictest(child.mode, holder.mode)
    _entry, delivered = post(team, LEAD, [target.name], [target.name], task, kind="assignment",
                             now=now)
    return _say("assigned", name=target.name), delivered


def replace(team: Team, by: str, args: dict[str, Any], *, now: float = 0.0) -> Added:
    """`team_replace` (§8): the same name, position, parent, mode, request
    options and model; a new id; briefed with the task and the old one's
    findings."""
    if refused := unmet(team, by):
        return Added(refused)
    if refused := main_only_refusal(by, "team_replace"):
        return Added(refused)
    name = str(args.get("name") or "").strip()
    old = team.get(name)
    live = old is not None
    if old is None:
        old = next((a for a in reversed(team.ended) if a.name == name), None)
    if old is None or name == LEAD:
        return Added(_say("unknown", name=name, names=", ".join(team.names())))
    if not live and team.size() + 1 > team.max:
        return Added(_say("full", max=team.max))
    if live:
        # It alone ends: the subagents under it move to the replacement.
        for child in team.children(old.name):
            child.moved_from = old.name
            child.manager = old.manager or LEAD
        team.agents.remove(old)
        old.state = STOPPED
        old.ended_at = now
        old.inbox = []
        team.ended.append(old)
        team.effects.append(Effect("stop", old.name))
    manager = old.moved_from or old.manager
    parent = team.get(manager) if manager else None
    parent = parent or team.lead
    task = str(args.get("task") or "").strip() or old.task
    if old.findings:
        found = "\n".join(
            harness_spec.fill(spec()["findings"]["item"], {"n": str(f["n"]), "text": f["text"]})
            for f in old.findings
        )
    else:
        found = texts()["no_findings"]
    brief = _say("replace_brief", task=task, name=old.name, id=old.id, findings=found)
    team.seq += 1
    fresh = Agent(
        name=old.name,
        manager=parent.name,
        role=old.role,
        may_lead=old.may_lead,
        agent=old.agent,
        model=old.model,
        state=FINISHED,
        goal=old.goal,
        order=team.seq,
        inbox=[brief],
        id=_next_id(team),
        task=task,
        mode=strictest(old.mode or "auto", parent.mode or "auto"),
        request=list(old.request),
    )
    team.agents.append(fresh)
    for child in team.agents:
        if child.moved_from == old.name:
            child.manager = fresh.name
            child.moved_from = ""
    _wants(team, fresh)
    return Added(
        _say("replaced", name=fresh.name, id=fresh.id, size=team.size(), max=team.max), fresh
    )


def request(
    team: Team, by: str, args: dict[str, Any], risk: str, *, now: float = 0.0
) -> tuple[str, dict]:
    """`team_request` (§7): `risk` is the tool's risk class."""
    agent = team.get(by)
    if agent is None or not agent.manager:
        return texts()["no_parent"], {}
    tool = str(args.get("tool") or "").strip()
    if refused := request_refusal(tool, risk, agent.request):
        return refused, {}
    team.request_seq += 1
    rid = f"{spec()['request']['id_prefix']}{team.request_seq}"
    pattern = str(args.get("pattern") or "").strip()
    why = str(args.get("why") or "").strip()
    team.requests.append(
        {"id": rid, "from": by, "tool": tool, "pattern": pattern, "why": why, "open": True}
    )
    body = _say("request_body", tool=tool, pattern=pattern, why=why)
    _entry, delivered = post(team, by, [LEAD], [LEAD], body, kind="request", id=rid, now=now)
    return _say("requested", id=rid), delivered


def decide(
    team: Team, by: str, args: dict[str, Any], *, permission=None, now: float = 0.0
) -> tuple[str, dict]:
    """`team_decide` (§7): a grant for the subagent when allowed; it is told
    either way. `permission(tool)` names the tool's permission key."""
    if refused := main_only_refusal(by, "team_decide"):
        return refused, {}
    rid = str(args.get("id") or "").strip()
    entry = next((r for r in team.requests if r["id"] == rid and r["open"]), None)
    target = team.get(entry["from"]) if entry is not None else None
    if entry is None or target is None:
        return _say("no_request", id=rid), {}
    entry["open"] = False
    allow = args.get("allow") is True or str(args.get("allow")).lower() == "true"
    if allow:
        key = (permission(entry["tool"]) if permission else None) or entry["tool"]
        target.grants.append(
            {"tool": entry["tool"], "permission": key, "pattern": entry["pattern"]}
        )
    head = _say(
        "decision_allowed" if allow else "decision_refused",
        tool=entry["tool"],
        pattern=entry["pattern"],
    )
    note = str(args.get("note") or "").strip()
    body = f"{head}\n\n{note}" if note else head
    _entry, delivered = post(
        team, LEAD, [target.name], [target.name], body, kind="decision", id=rid, now=now
    )
    decision = texts()["allowed" if allow else "refused"]
    return _say("decided", decision=decision, id=rid, name=target.name), delivered


def _messages_of(team: Team, name: str) -> int:
    return len([p for p in team.room if p.sender == name or name in p.recipients])


def tree_row(team: Team, agent: Agent) -> dict[str, Any]:
    """One agent of the tree as clients show it (§12)."""
    row: dict[str, Any] = {
        "id": agent.id or agent.name,
        "name": agent.name,
        "position": spec()["read"]["lead_role"] if agent.name == LEAD else agent.role,
        "parent": agent.manager,
        "state": agent.state,
        "model": agent.model or team.lead.model,
        "mode": agent.mode or "auto",
        "request": list(agent.request),
        "manager": agent.name == LEAD or agent.may_lead,
    }
    if agent.manager:
        row["task"] = agent.task
    if agent.context:
        row["context"] = dict(agent.context)
    row["progress"] = agent.progress
    row["findings"] = len(agent.findings)
    row["messages"] = _messages_of(team, agent.name)
    if agent.ended_at is not None and agent.state in ENDED:
        row["ended"] = int(agent.ended_at * 1000)
    return row


def tree_info(team: Team) -> dict[str, Any]:
    """The tree (§12): the agents in tree order, open slots, ended ones."""
    return {
        "min": team.min,
        "max": team.max,
        "size": team.size(),
        "open_slots": open_slots(team),
        "agents": [tree_row(team, agent) for agent, _ in team.tree()],
        "ended": [tree_row(team, agent) for agent in team.ended],
    }


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
