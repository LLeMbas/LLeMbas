# Subagents

Spec 3.3.0; §5–§13 spec 3.4.0. **Which model a subagent runs on, and when subagents are offered at all.** A
**subagent** is any agent a session's agent starts to work for it: a `task` subagent (in the
foreground or as a background job) and a team's member (`team.md`) alike. The same rules in a chat
on the server and in an agent session on a device, in both projects. The texts are `loop.json`
`subagents`; the cases `conformance/subagents.json`.

## 1. The word

**Subagent** is the word in every text a person or a model reads: the UI (the web UI, Desktop,
Android, the CLI, the admin pages), the prompt texts and the tool texts. A team's members are
subagents too. The word *helper* is retired. Tool names, their former names (`aliases`) and
stored identifiers do not change.

## 2. The model a subagent runs on

**The session's own model**, always — unless **the person has named another model** in this
session. Nothing else chooses one: there is **no setting** for a subagent's model anywhere — not
in a chat, not in a person's or an instance's settings, not in the configuration, and an agent
definition's own `model` (the CLI's agent files) is not used for a subagent either.

- **Named** means: in a message **the person typed** in this session, the model's served id, its
  id at its connection, or its display name occurs as a whole word, letters compared without case.
  Only the person's own words count — never the model's text, a tool's output, a file, a note, a
  job's or a team's marked message, or a subagent's report. A message that was compacted away
  still counts: what the person named stays named for the whole session.
- **A whole word**: the character before the name is not a letter, a digit, `_`, `-`, `/`, `.` or
  `:`; the character after it is not a letter, a digit, `_`, `-` or `/`, nor a `.` or `:` followed
  by a letter or a digit. So `coder-30b.` at the end of a sentence names `coder-30b`, and
  `coder-30b-instruct` does not. Runs of white space in a display name match any run of white
  space.
- The `model` parameter of `task` and of `team_add` takes a model the person may use, by the same
  names. When it is **not the session's model and the person has not named it**, the call is
  refused, word for word, with `subagents.texts.not_named`:

  > Use another model for subagents only when the person names it.

  The session's own model given as `model` is the default spelled out, and is never refused for
  not being named.
- A team's members are of the lead's session: what the person named there counts for every
  `team_add` in the team, a sub-manager's included. A member's default is the session's model, not
  its manager's.

## 3. Capacity: the logical restrictions, and no others

What a model's server can do at once is `models/schema.json` `capacity`: `parallel` (`single_session`
being `parallel: 1`; neither said — no limit), and `group`, the server that holds one model at a
time. With **S** the session's model and **M** the subagent's, checked in this order, after
`not_named`:

1. **M is another model on S's one-model server** (both have the same `group`): refused with
   `subagents.texts.same_group` — starting it would unload the lead's model:

   > That model is on the same one-model server as this chat's model; name a model on another server.

2. **M is S, and S serves one request at a time** (`parallel` 1): refused with
   `subagents.texts.one_request` — the subagent would take the only slot from the lead:

   > This model serves one request at a time: name another model, on another server, for subagents.

   There is **no fallback**: nothing else is picked for the person, ever. A model the person named,
   on another server, is the way out — and a model that serves one request at a time is fine there.

These apply to `task` and to `team_add` alike, and before anything is started. Beyond them nothing
is refused for being the wrong model: a member past the free slots waits for one (`team.md` §8),
and `task` keeps its rule for a model that is already full (`loop.json` `capacity`).

## 4. When subagents are offered

The size setting `agents {min, max}` (`team.md` §2) is shown as **Subagents** — `subagents.setting`:
the label, and **Min** and **Max** for `min` and `max` (defaults 1 and 4, at most `cap` 32, kept
with the chat). **The numbers count the session's own agent**: Min 1 / Max 1 is the agent alone,
with no subagents; Max 4 is the agent and up to three subagents (`subagents.setting.hint`). A UI
shows the setting as **one range from 1 to `cap` with two handles, Min and Max** (Min never past
Max): a slider in a graphical UI, two numbers on one line in a terminal, each moved on its own. It, the team tools and `blocks/team_lead.md` are **offered** when both hold:

1. Subagents are enabled for the session (`team.md` §2, rule 1).
2. **At least one model could serve as a subagent**: S itself, when it serves two or more requests
   at once (`parallel ≥ 2`, or no limit); or **any other model the person may use that is not on
   S's one-model server** (no `group`, or another one) — whatever its own `parallel`.

So a session on a one-request model is offered subagents when another server has a model, and
**its first note says so** (`context.note.team_own`, after `team`): the session's model cannot
serve subagents itself, and they need a model the person names. A UI showing the setting says the
same beside it (`subagents.setting.own`). Nothing is offered when neither holds — the same as
before 3.3.0 for a single model.

What counts as **usable** is each project's: LLeMbas — the models the person may use in the chat's
picker; the CLI — the models of the server it is logged in to.

---

# Subagents v2 — the main agent and its tree

Spec 3.4.0. **How subagents are organised, what each may do, and how each ends.** The owner's model:
one permanent **main agent** per session, which alone makes subagents and places them in a tree;
permissions are inherited and never widened; a subagent is temporary and ends with its task or its
context. Sections 5–13 **supersede `team.md` where they say something different** — who adds a
subagent (§6), who stops one (§10), the states (§9), what a member is told (§13) — and keep the
rest of it: the size setting and its counting (§2–§3), the room and who receives what (§5),
delivery and waking (§7), a manager's refusals (§6 there), capacity and waiting for a slot (§8).
The tool names `team_*` stay (nothing stored or trained breaks); `task` and `report` are unchanged
for a one-off subagent. The texts are `loop.json` `team` (3.4.0 additions marked there); the cases
`conformance/team.json` (the tree, the tools, the lifecycle) and `conformance/delegation.json`
(permissions).

## 5. Words

- **The main agent** is the session's own agent: the chat's agent on LLeMbas, the session's in the
  CLI. It is **permanent**: it compacts and never ends. In the room and in every tool it is called
  `lead` (the name is kept from 3.2.0); a person reads it as *the main agent*.
- **A subagent** is an agent the main agent added (`team_add`). It has:
  - **`id`** — `s1`, `s2`, … in the order the session's subagents were added, never reused in the
    session (a replacement gets a new one);
  - **`name`** — the main agent's choice (`team.names.pattern`), unique among the subagents that
    have not ended, `lead`, `all` and `parent` reserved;
  - **`position`** — its designation, one line, given by the main agent (`team_add`'s `role`:
    *the API's tests*, *reviews the parser*);
  - **`parent`** — the main agent, or a **sub-manager** the main agent placed it under;
  - **`task`** — its brief, as given (and as replaced by `team_assign`);
  - **`mode`** and **`request`** — its permission mode and its request options (§7);
  - **`model`** — `subagents.md` §2–§3, unchanged.
- **A sub-manager** is a subagent added with `lead: true`. It **directs** the subagents the main
  agent places under it — they report to it, it messages them, it reviews their work — and it does
  **not** add, assign, replace or stop subagents: only the main agent does (§6). Like every manager
  it does not do the work itself (`team.md` §6, unchanged).
- **The tree**: the main agent at the root, every subagent under its parent, in the order added.
  **Under** an agent and **its subtree** keep their `team.md` meaning.

## 6. Only the main agent makes subagents

- **`team_add`, `team_assign`, `team_replace`, `team_stop` and `team_decide`** are the main agent's.
  A subagent's call to any of them is refused, word for word, with `team.texts.only_main`:

  > Only the main agent adds, assigns, replaces and stops subagents: ask it (team_send to lead).

  A subagent's tool list never has `task` (a one-off subagent is the main agent's too), and a call
  to it by a former name is refused with the same text.
- **`team_add`** takes, beside 3.2.0's `name`, `role` (the position), `task`, `goal`, `lead`,
  `agent`, `model`: **`parent`** — the name of a sub-manager to place it under (default: the main
  agent; refused with `team.texts.not_manager` for a subagent that is not a sub-manager, and
  `unknown` for no such name), **`mode`** — its mode (default: its parent's; §7), **`request`** —
  its request options (§7). Its refusals, in order: `unavailable`, `only_main`, then 3.2.0's (`all_name`,
  `bad_name`, `taken`, `no_goal`, `full`), then `unknown` and `not_manager` for `parent`, then the mode's (`wider`, `request_not_narrower`,
  `request_kind`), then the model's (§2–§3). `parent` and the reserved names are refused as
  `taken`.
- **The person** can tell the main agent to spawn — it is the main agent's call, as any — or set
  **Min above 1**: then **`min − 1` slots** exist from the start of the main agent's reply (the
  first one after the setting was told, `team.md` §10). A **slot** is a place in the tree for a
  subagent not added yet: it is shown (§12) and `team_read` lists it, it is **not** an agent and
  does not count toward the size, and each subagent added fills one. Open slots are
  `max(0, min − 1 − subagents that have not ended)`. The main agent is told to use them in its
  note (`context.note.team_lead`, which 3.4.0 words as slots), and is a manager from the start
  (`team.md` §6).
- **Min/Max** (3.3.0) are unchanged: they count the main agent; 1 is the main agent alone. The
  model rules (§2–§3) are unchanged.

## 7. Permissions: inherited, never widened

**A subagent's mode is its parent's or stricter** — `permission/modes.json` `order`, loosest first:
`auto`, `edit`, `manual`, `plan`. The main agent's mode is the session's.

- `team_add`'s and `team_assign`'s `mode`, looser than the parent's, is refused, word for word,
  with `team.texts.wider`:

  > A subagent's mode cannot be looser than its parent's ({{parent_mode}}).

  When the parent's own mode becomes stricter during the session (the person switches the main
  agent to manual), every subagent looser than its parent is made as strict as it, down the tree,
  at once; a looser switch changes no subagent.
- **Request options** (`request`: a list of the risk classes `read`, `write`, `execute`) may be
  given **only with a mode stricter than the parent's** — otherwise refused with
  `team.texts.request_not_narrower` ("Request options go with a mode stricter than the parent's:
  without one there is nothing to ask for."); an unknown kind with `team.texts.request_kind`. They
  let the subagent **ask the main agent** for a call of that class that its own mode does not
  allow; **the main agent is the check**.

**Who decides a subagent's call.** The hardline and the deny rules first, as for any call. Then:

1. The call is evaluated in the **subagent's mode**, with the session's rules and approvals and
   the subagent's own grants (below). **Allow** — it runs. **Deny** (a rule, the hardline, plan
   mode's refusal) — refused, as always.
2. **Ask** — the call is evaluated in the **main agent's mode** (the session's rules and
   approvals, no grants):
   - **Allow** — the main agent could allow it: the subagent was narrowed. If the call's risk class
     is in its `request`, refused with `team.texts.requestable` — it asks with `team_request`, the
     main agent decides with `team_decide`, and once allowed the same call runs. Otherwise refused
     with `team.texts.not_delegated`:

     > Your mode does not allow this, and you may not ask for it: tell your parent what you need.

   - **Ask** — **nobody in the tree can allow it: it goes to the person**, as an approval card that
     names the subagent (its name and position), the same card and the same answers as the main
     agent's own; unattended, refused as `modes.json` `unattended` says. So in an all-Edit tree a
     subagent's `bash` reaches the person — not the main agent, which could not allow it either.
   - **Deny** — refused with that reason.

   A sub-manager is never asked: it may not allow more than the main agent, and the main agent is
   the check.

**`team_request`** (a subagent with request options; its tool list has it only then): `tool`, the
permission `pattern` it needs (as a rule writes it: `git push *`, a path) and `why`. Refused with
`team.texts.not_requestable` when the tool's risk class is not in its options. Posted to the main
agent as a request, under its own mark (`team.marks.request`), the body `team.texts.request_body`,
with an id (`r1`, `r2`, … per session); the answer `team.texts.requested` says it waits for the main agent's decision.

**`team_decide`** (the main agent): `id`, `allow` (a boolean), `note`. Allowed — the subagent gets
a **grant**: the rule `{ permission: <tool's key>, pattern, action: allow }` for itself alone, for
the rest of its life; **a grant only ever applies where the main agent's own mode would allow the
call** (step 2's evaluation, done again at the call), so it can never widen past the main agent.
Either way the subagent is told, under `team.marks.decision`: `team.texts.decision_allowed` or
`decision_refused`, then the note after a blank line. The answer to the main agent is
`team.texts.decided` (`decision` being `allowed` or `refused`). Refused:
`team.texts.no_request` for an id that is not an open request.

## 8. What each agent has

| Tools | Main agent | Sub-manager | Subagent |
|---|---|---|---|
| `team_whoami`, `team_read`, `team_send`, `team_findings` | ✓ | ✓ | ✓ |
| `team_finish` | — | ✓ | ✓ |
| `team_request` | — | with request options | with request options |
| `team_add`, `team_assign`, `team_replace`, `team_stop`, `team_decide` | ✓ | — | — |

Fixed when the prompt is made (the main agent's) or when the subagent is added (`context.md` §10,
rule 4). What each does:

- **`team_whoami`** — who the caller is: `team.whoami`, one line each, in its order — id, name, position, parent, task, mode and
  request options, model, the limits (the size and Max, the steps a turn may take, whether it may
  add subagents) and its **context use** (tokens in its context now, of its model's window, and the
  percentage). For the main agent: the same, its position *the main agent*, without the parent and task lines.
- **`team_read`** — the roster: the tree (every agent and open slot, §12's fields) and the room,
  as in 3.2.0 (`team.read`; 3.4.0's row adds the id, the mode and the context use).
- **`team_send`** — a message `to` names, `all`, or **`parent`** (3.4.0): the caller's parent
  (refused for the main agent with `team.texts.no_parent`). Otherwise 3.2.0.
- **`team_finish`** — `report`: the subagent's task is done. The report goes to its parent (the
  report mark, as a final reply's), and the subagent **ends** (§9): its turn stops after this call,
  it leaves the size, and its findings and transcript stay.
- **`team_findings`** — `write` (`text`): a finding kept for the caller, numbered from 1; `read`
  (`of`: a name or an id, default the caller): the findings of the caller, or of an agent in its
  subtree (the main agent: anybody's), ended ones included — as `team.findings` writes them. A
  name that has been used more than once means its latest holder; an id is exact. Otherwise refused with
  `team.texts.not_yours_findings`. The person reads every agent's (§12).
- **`team_assign`** — `name`, `task`, and optionally `role` (a new position), `parent` (another
  sub-manager, or `lead`), `mode` (§7): a new task for a subagent that has not ended. Its task
  becomes this one; it is told under `team.marks.assignment`, and a finished one is woken. Refused:
  `unknown`, `team.texts.ended` for one that has ended, `not_manager`, `wider`; a move under its
  own subtree with `team.texts.cycle`.
- **`team_replace`** — `name`, and optionally `task`: the subagent ends (stopped, if it has not
  already), and a new one takes its place — the same name, position, parent, mode, request options
  and model, a new id — briefed with `task` (default: the old task) followed by the old one's
  findings (`team.texts.replace_brief`). Its subagents move under the new one. Refused: `unknown`.
- **`team_stop`** — the main agent's only (3.2.0 let a manager stop its subtree); otherwise 3.2.0.
- **`team_decide`** — §7.

## 9. A subagent's life

States (`team.read.states`): **`working`** (a turn runs), **`waiting`** (it has work and waits for
a slot, `team.md` §8), **`finished`** (its last turn ended with a final reply, which was its report;
a message wakes it — 3.2.0), **`paused`** (its context is full; its last turn runs), and the ends:
**`done`** (`team_finish`), **`out_of_context`** (its last turn after `paused` ended), **`stopped`**
(the main agent's `team_stop`, a replacement, or the person). The main agent is `working`,
`waiting` or `idle`.

**An ended subagent** — `done`, `out_of_context`, `stopped` — leaves the size and its slot frees;
its name may be used again; it keeps its id, and the tree keeps it (§12) with its findings and
transcript for the rest of the session. Messages to it by name are refused as `unknown`.
The subagents under a sub-manager that ended by itself (`done`, `out_of_context`) move under its
parent until the main agent replaces it, and then back under the replacement; a stopped one's
subtree is stopped with it (§10).

**When its context fills.** A subagent does not compact. When its context reaches
`team.context.pause_at` of its model's window (default 0.85, the fraction compaction would start
at), after the step that took it there:

1. it is **`paused`**: nothing new reaches it — a message to it is posted in the room and not
   delivered; whoever replaces it reads it with `team_read`;
2. it is given **one final turn**, at most `team.context.final_steps` steps (8), whose user message
   is `team.texts.context_full`, under its own mark: save what it found with `team_findings`, and
   tell its parent with `team_send` that it is gone and what is left;
3. when that turn ends it is **`out_of_context`**, and its parent is sent `team.texts.gone` — then,
   after a blank line, that turn's final reply if it gave one — as its report (the report mark): the name, that it ran out of context, and how to read its
   findings. **The parent decides** — the main agent replaces it (`team_replace`) or uses the
   findings; a sub-manager tells the main agent.

**The main agent is permanent**: it compacts as every session does (`context.md` §7) and never
ends; deleting the session ends every subagent.

**Waiting for subagents.** The main agent's turn may end while subagents work; their reports wake
it (3.2.0 §7). Its progress is `waiting_for_subagents` (`progress.md`) while its turn waits on
them — a foreground `task` — and the session shows the subagents' own phases in the tree.

## 10. Stopping

Only the main agent stops (`team_stop`, 3.2.0's refusals but `not_yours`, which no longer arises),
and **the person** (§12) — said to the main agent as a note (`context.note.team_stopped`). A
stopped subagent's subtree is stopped with it, unless the main agent moved it first. A sub-manager
that wants a subagent of its own stopped asks the main agent.

## 11. Transcripts and findings

Every subagent's conversation is kept — its brief, what it wrote, every tool call with its output —
for as long as the session: **viewable read-only by the person, live while it runs and after it
ended**. Nobody types into it; the person speaks to a subagent only through the main agent (or by
stopping it). Findings (§8) are kept the same way, readable by its parent, the main agent, and the
person.

## 12. What clients show

**The tree** — the main agent, every subagent under its parent, open slots, and ended subagents
(after the live ones, dimmed, with how each ended):

| Field | Shown as |
|---|---|
| `name`, `id`, `position` | `parser` (`s2`) — *reviews the parser* |
| `state` | `team.read.states`, its word |
| `model` | display name |
| `mode` | the mode's name, and request options as `+ write` etc. |
| `context` | `{used, window}` → `42%` (a bar where there is room) |
| `progress` | its current phase line (`progress.md` §3) |
| messages | the room's messages to and from it: how many unread by the person, the last one's first line |

- **Web UI, Desktop and the CLI** show the whole tree, the room, each subagent's findings and its
  context (the CLI: `/context <name>`), and open any subagent's transcript read-only, live and after
  it ended; the person can stop any subagent there.
- **Android** shows a reduced list — name, position, state, progress line, context % — and opens a
  transcript read-only.

**The data**, the same in `/api/v2` (LLeMbas: a chat's stream carries the tree as it changes) and on
the link (`acp.md`, capability `tree`):

```json
{ "min": 1, "max": 4, "size": 2, "open_slots": 0,
  "agents": [
    { "id": "lead", "name": "lead", "position": "the main agent", "parent": null, "state": "working",
      "model": "qwen-27b", "mode": "edit", "request": [], "manager": true,
      "context": { "used": 41200, "window": 131072 }, "progress": { "phase": "thinking", "since": 1762790400000 } },
    { "id": "s1", "name": "parser", "position": "reviews the parser", "parent": "lead", "state": "working",
      "model": "qwen-27b", "mode": "manual", "request": ["write"], "manager": false, "task": "…",
      "context": { "used": 9100, "window": 131072 }, "progress": { "phase": "running_tool", "since": 1762790401000, "tool": "read", "what": "src/parse.ts" },
      "findings": 2, "messages": 3 }
  ],
  "ended": [ { "id": "s0", "name": "scout", "position": "…", "parent": "lead", "state": "done", "ended": 1762790300000, "findings": 1 } ] }
```

## 13. What each is told

- The main agent: `blocks/team_lead.md` (3.4.0: it alone adds; slots; positions; modes and request
  options; replacing a subagent that ran out of context) and the notes of `team.md` §10.
- A subagent: `blocks/team_member.md` (3.4.0: its id and position, `team_whoami`, `team_findings`,
  `team_finish`, that its context is limited and it will be told when it fills); a sub-manager,
  `blocks/team_submanager.md` in place of the whole lead block it got in 3.2.0.

## 14. In an agent chat

The main agent of an agent chat is the CLI on the linked device: it starts and runs the
subagents there, under every rule above. The server carries the chat's Subagents setting down with
every turn and the tree, approvals, findings and transcripts up, and offers no subagents of its own
to an agent chat. The protocol: `acp.md`, *Subagents in an agent chat* (capabilities `agents` and
`tree`).

