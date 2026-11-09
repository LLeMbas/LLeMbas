# Teams

Spec 3.2.0; the model a member runs on and when a team is offered, 3.3.0 (`subagents.md`). **Subagents that work together.** A session's agent — the **lead** — may add
**members**, background agents that each own a part of the work, and lead them as a manager: it
plans, splits the work, coordinates and reviews, and the members do it. Members talk to each other
and to their managers in the team's **room**. A member may itself lead an area with members of its
own, so a team grows from a few agents side by side into a small company with one goal. The same in
a chat on the server and in an agent session on a device, in both projects.

The tools are `tools/team_add.json`, `team_send.json`, `team_read.json`, `team_stop.json`; the texts
and numbers `loop.json` `team` (and the size notes in `context.note`); the prompt texts
`prompts/blocks/team_lead.md` and `team_member.md`; the cases `conformance/team.json`.

## 1. Words

- **Agent**: the lead or a member. Every agent has a **name**: the lead is `lead`; a member the name
  it was added under. Names are unique in the team.
- **Member**: an agent added with `team_add`, by the lead or by a member that leads. Its **manager**
  is the agent that added it. The members an agent added, and theirs, down the tree, are **under**
  it; with the agent itself they are its **subtree**.
- **Manager**: an agent with at least one member, and the lead from the start when the team's `min`
  is 2 or more (§2). A manager does not do the work (§6).
- **Team**: the lead and every member, one per lead session; **its size** is how many agents it has
  now (§3).
- **Room**: the team's messages, numbered from 1 in the order they were posted, each with `n`, `from`
  (a name), `to` (as addressed: names, or `all`), its recipients (resolved when it was posted), the
  text, the time, and whether it is a report.

## 2. The size setting, and when a team is offered

A chat has the setting **`agents: {min, max}`** — the agents the team may have, the lead included.

- `1 ≤ min ≤ max ≤ cap`. `cap` is the instance's (LLeMbas) or the global configuration's (the CLI),
  default `loop.json` `team.agents.cap` (32); a project's configuration never raises it. A value
  outside is refused where it is set, with what the bounds are, never clamped silently; a stored
  setting the cap was lowered under is read as `max = min(max, cap)`, `min = min(min, max)`.
- Defaults for a new chat: `min` 1, `max` 4 (`team.agents`) — the lead and up to three members. LLeMbas
  keeps a per-person default for new chats, seeded with those; the CLI reads them from its
  configuration.
- The setting is kept with the chat. A change during a session is told to the lead as a note on its
  next user message (`context.md` §10; `context.note.team`, below), never by changing the system
  prompt.

**A team is offered** — the setting shown, the team tools listed, `blocks/team_lead.md` in the
system prompt — when both hold, and only then:

1. Subagents are enabled for the session: LLeMbas — the instance allows them and the chat's model
   has its subagent tool; the CLI — its subagents are not switched off in the configuration.
2. **At least one model could serve as a subagent** (spec 3.3.0, `subagents.md` §4): the chat's
   model, when it serves at least two requests at once (`capacity.parallel ≥ 2`, or neither
   `parallel` nor `single_session` is set — a hosted API; `models/schema.json`); or any other model
   the person may use that is not on the chat's model's one-model server (`capacity.group`).

The setting is shown as **Subagents**, *fewest* to *most* (`loop.json` `subagents.setting`).

Both are fixed for as long as the system prompt is (`context.md` §10, rule 4: a model switch makes
a new prompt and a new list). Otherwise the setting is not shown and none of the four tools is
listed, and the session's `task` works as before.

While the tools are listed they need **`team`** (`requires`): met when `max ≥ 2` and a model could
still serve as a subagent (rule 2) — or, for the lead, when its team still has a member (so a lead whose person
lowered `max` to 1 can still talk to, read and stop the members it has). A call while it is not
met answers, word for word, `team.unavailable.one_request` when no model could serve as a subagent
any more (the model's metadata changed after the prompt was made), else `team.unavailable.one_agent`
— which is also the tools' own `unavailable` text.

The meaning of the setting:

- **`max = 1`**: one agent, no team, and no subagent of any kind: `task` answers `one_agent` too
  (while the lead has no member; with members left from a larger setting, `full`).
- **`min ≥ 2`**: the lead works through a team from the start — it is a manager before its first
  member (§6), and the note asks for at least `min − 1` members.
- **`min = 1`, `max ≥ 2`**: the lead decides; a small job it may do alone. Once it has a member it is
  a manager.

## 3. Size, and what counts

**The size** of a team is: the lead, every member (whatever its state — a finished member can be
woken by a message, so it keeps its place), and every `task` subagent running in the team, started by
any agent in it (the CLI's background `task` jobs and foreground ones alike, while they run).
A stopped member does not count: stopping is how room is made.

- **`team_add` is refused past `max`** — when the size would become more than `max`, with
  `team.texts.full`. So is `task`, while a team is offered: with `full`, or with `one_agent` when
  `max` is 1 and the lead has no member. A `job_send` that would start a finished background
  `task` subagent again is refused the same way.
- **Lowering `max` stops nobody.** Members already there stay; nothing new is added until the size
  is under the new `max`.
- The size is across **the whole tree**: a member that leads adds within the same `max` as the lead.

## 4. Members

- `team_add` starts the member **at once, in the background**, under the caller: the existing job
  machinery (a hidden chat on LLeMbas with its `chat_jobs` row, a subagent job in the CLI), running
  unattended — no `ask_user`, the chat's own permission ceiling, its mode no looser than its
  manager's (`permission/modes.json`).
- **Its agent** is `general` (the default: it does the work) or `explore` (read-only). **Its model**
  is the session's (the lead's) unless `model` gives one the person named in this session — as
  `task`'s, refused with `subagents.texts` otherwise (`subagents.md` §2, §3).
- **Its tools** are those its agent gives a subagent, plus `team_send` and `team_read`; one added
  with `lead: true` also has `team_add` and `team_stop`. They are fixed when it is made (§10 rule 4:
  a subagent's narrowing). `report` (`offered: subagent`) sends its manager a message before its
  final reply: posted in the room to the manager, delivered with the message mark.
- **Its system prompt** is built once, when it is added: what a subagent of that agent gets, with
  `blocks/team_member.md` in place of `blocks/subagent.md` — its name, role, manager, the team's
  goal, the agent's own instructions, and for one with `lead: true` the whole of
  `blocks/team_lead.md`. Its first user message is its brief (`task`), as given.
- **The team's goal** is what a member is told the team is for: the `goal` the caller gave in
  `team_add`, else the caller's own goal. The lead's own goal is the last `goal` it gave; **the
  lead's first `team_add` must give one** (`team.texts.no_goal`). A member already working keeps the
  goal it was told; a new goal reaches it only as a message.
- **`team_add`'s refusals are checked in this order**, the first that applies answering:
  `unavailable` (§2); `all_name` (the name `all`); `bad_name` (not `team.names.pattern`); `taken`
  (the name of an agent on the team, `lead` included); `no_goal`; `full`. Then the member is added:
  `added`, or `added_waiting` when it has to wait for a slot (§8). An unknown `agent` or `model`
  answers as `task` does; then `model`'s refusals, `not_named`, `same_group`, `one_request`
  (`subagents.md`), in that order — before anything is added.
- **Members are not jobs to the model.** `job_list` does not show them and the `job_*` tools do not
  take them; the team tools do. (An implementation may run them on its job machinery.)
- **States**: `working` (a turn is running), `waiting` (it has work, and waits for a slot, §8),
  `finished` (its last turn ended; it can be woken). The lead is `working`, `waiting` or `idle`.
- A member's background commands stop when its turn ends (`loop.json` `jobs.lifecycle`). Deleting
  the lead's session stops the whole team.

## 5. The room: who receives what

- **`team_send`** posts one message: `to` a list of names, or `["all"]` (a lone string is read as a
  one-name list). Names are deduplicated, kept in the order given. `all` anywhere in the list means
  all, and the other names in it are ignored.
- **Recipients**: for named agents, exactly those named; for `all`, every agent of the team but the
  sender — the lead and every member, whoever manages them (the room is one channel for the whole
  company). Resolved when the message is posted: an agent added later does not get it.
- **Refused, and nothing posted**, checked in this order: `all` when nobody else is on the team
  (`nobody`); a name that is not on the team (`unknown`, the first such name in the order given);
  the sender's own name among them (`self`). The answer when
  posted: `team.texts.sent`, `to` as addressed.
- **What an agent receives** is only what is addressed to it, by name or as `all`, and — a manager
  — **the reports of its own members**. A manager is not sent its members' messages to each other;
  it reads them with `team_read`, on demand, so nothing floods its context.
- **A report**: a member's final reply, when its turn ends, is posted to its manager (`report`
  true) and delivered to it. An empty reply reads `team.texts.no_report`; a turn that ended
  otherwise than by the model stopping adds `team.texts.ended` (`steps`, `budget`, `error — <text>`),
  after a blank line. A sub-manager's final reply is likewise its report to its manager.
- **The person's** messages go to the lead as its user messages, not into the room; the lead's
  answers to the person are not room messages either. The person sees the whole room and the tree
  in the UI (LLeMbas: the Team panel; the CLI: `/team`), and can stop a member there — said to the
  lead as a note (`context.note.team_stopped`).
- **`team_read`** answers the tree — every agent, the whole team, in tree order: name, role, its
  manager, state, model, last activity — and the room's messages numbered above `since` (default 0)
  that the caller may see, oldest first, at most `team.read_max` (50), and how to read on. **Who may
  see a message**: the lead every one; any other agent those whose sender or one of whose recipients
  is in its subtree — for a member that leads nobody, what it sent and what it received.

## 6. A manager does not do the work

A manager's **work** calls are refused, before the permission check — nothing is asked — with
`team.texts.manager`, word for word:

> You lead a team: give this to a member (team_add or team_send).

**Work** is a call whose risk class (`risk`; an MCP tool's: `read` when it says `readOnlyHint`, else
`execute`) is `write` or `execute`, with two exceptions that stay a manager's:

- `job_stop` — managing what runs is not work;
- a `bash` call **the default rules alone allow in manual mode** (`permission/defaults.json`, the
  `permission` conformance area's evaluation with no rules of the person's): reading commands —
  `git status`, `git diff`, `cat`, `ls`, `grep` — for review.

So with the spec's tools: **refused** — `write`, `edit`, `multiedit`, `apply_patch`, `mkdir`, `mv`,
`rm`, `skill_manage` (write); `bash` beyond reading, `python`, `commit`, `branch`, `pr`, `merge`,
`tag`, `release` (execute); an MCP tool that does not say it only reads. **Kept** — every `read`
tool (`read`, `glob`, `grep`, `list`, `search`, `recall`, `web_search`, `web_fetch`, …), every
`interact` tool (`ask_user` for the lead, `plan_*`, `task`, `memory`, `show`, `job_send`, `report`,
the team tools, …) and `job_stop`. `team.work` lists the same, and a new tool is placed by its risk
class.

**The tool list does not change** when an agent becomes a manager (`context.md` §10, rule 4): the
same tools are listed and the refused ones answer the refusal. An agent is a manager while it has
a member — a finished one included; one whose members were all stopped is not, unless it is the
lead and `min ≥ 2` with the team offered. `task` stays: a one-off subagent is delegation, and counts
toward the size (§3).

## 7. Delivery, and waking

A message or a report reaches its recipient **as a marked message** — the jobs mechanism
(`loop.json` `jobs`): while the recipient's turn runs, as a user message at its next step boundary
(which does not start a turn: `context.md` §8); otherwise it **wakes** the recipient — a finished
member, or the idle lead — as a new turn of its own, marked `machine`, with its context intact, as
`job_send` restarts a finished subagent. A woken agent needs a slot (§8). Several waiting at one step
boundary go in one user message, in room order, separated by a blank line.

Every one begins with its mark, on a line of its own, then the text:

```
[Team message from parser to lead:]
The grammar is done; the tests are next.
```

- message: `[Team message from {{from}} to {{to}}:]` — `to` as addressed: the names joined with
  `, `, or `all`; the same for every recipient.
- report: `[Team report from {{from}} to {{to}}:]` — `to` the manager.

A session that is not open keeps them until it is resumed, as a job's note.

## 8. Capacity: a member waits for a slot

A model serves `parallel` requests at once (`models/schema.json`). **An agent holds a slot of its
model while its turn runs** — the lead, a member, a `task` subagent. An agent with work (added, woken,
given a message while finished, the lead given the person's message) starts when its model has a
free slot and none of its `capacity.group`'s other models is busy; otherwise it is `waiting`.
`team_add` is never refused for a full model (only for size, and for the model rules of
`subagents.md` §3: another model on the lead's one-model server, or a one-request session model):
its answer says the member waits.

Waiting agents start **in order**: the lead first (the person waits on it), then the others in the
order they began waiting. A slot frees when a turn ends or an agent is stopped. `task` keeps its own
rule (`loop.json` `capacity`: refused where its model is full), and its subagent holds a slot.

## 9. Stopping

`team_stop` stops one member **and its whole subtree**, or with `all` every member under the caller:
their turns stop where they are, their background commands with them, messages waiting for them
are dropped, and they **leave the team** — the size drops, their slots free, their names may be
used again. No report comes back from a stopped member. Refused, in this order: `all` with nobody
under the caller (`nobody_to_stop`); a name not on the team (`unknown`); the caller itself or an
agent not under it (`not_yours`). The answer, `stopped`, names the stopped in tree order.

## 10. What the lead is told

- `blocks/team_lead.md` — constant, no numbers — is in the system prompt whenever the team tools
  are listed: how to lead.
- The size is in a **note** (`context.md` §10, `context.note`): on the first user message of the
  session, and on the next one after the setting changed — `team` (`min`, `max`), with `team_lead`
  after it when `min ≥ 2` (`members` = `min − 1`); when `max` is 1, `team_one` instead. One block.
  A member the person stopped is its own block, `team_stopped`, with the names. Members are told
  nothing of the size: `team_add`'s answer says how big the team is.

## 11. Over the link

An agent chat's team runs on the device, as its jobs do. Spec 3.2.0 does not carry the room over
ACP yet: a device with the `jobs` capability reports each member as a subagent job (`job` events,
`description` being `<name>: <role>`), so the client's job strip shows them, and the room is shown
on the device (`/team`). Carrying the room is a later spec's.
