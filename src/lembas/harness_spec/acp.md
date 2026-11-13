# The `_lembas` extension of ACP

How LLeMbas (the web UI) and LLeMbas CLI (on a device) talk over the link: the Agent Client
Protocol, plus the `_lembas/*` methods and `_meta.lembas` fields below. LLeMbas CLI is the
**agent**; LLeMbas is the **client**. The device dials out (`wss://<instance>/api/devices/link`,
the device token in the `Authorization` header) and one WebSocket message is one JSON-RPC 2.0
message; `lembas serve --stdio` speaks the same protocol on stdin/stdout, one message a line, to an
editor (which gets plain ACP: none of the `_lembas` notifications, no question or plan cards).

This file is part of the harness spec: LLeMbas vendors it with the rest, and a
change to the protocol is a change here first, then in both projects.

## Versions and discovery

- **`protocol`** (`_meta.lembas.protocol` in `initialize`, `protocol` in `_lembas/hello`):
  **3**. CLI `src/acp/agent.ts:LEMBAS_PROTOCOL`, LLeMbas
  `services/device_link.py:PROTOCOL`.
- **3 is LLeMbas 2.0 and LLeMbas CLI 2.0, and they speak it alone** (spec 3.0.0). A 2.0 server keeps
  the server's file system, memory and models, and a 2.0 CLI talks to nothing else, so a 1.x peer
  and a 2.0 one **refuse each other**, each saying what to update:
  - **Discovery** (`/.well-known/lembas.json`) of a 2.0 server says `protocol: 3` **and**
    `protocols: [3]`. `protocol` was frozen at 1 for 1.x so that a client comparing it for equality
    could still log in; 3 raises it on purpose, so a 1.x CLI's login is refused there (it says the
    server speaks protocol 3 and to update the older of the two).
  - A **2.0 CLI** logs in only where discovery lists 3 (`protocols`, falling back to `protocol`).
    To a server that does not, at login and again at every dial of the link, it says *&lt;server&gt;
    runs LLeMbas X, which speaks link protocol 1 and 2; this LLeMbas CLI needs a LLeMbas 2.0 server
    (protocol 3) — update the server, or use LLeMbas CLI 1.x with it*, and the link stops.
  - A **2.0 server** closes a link whose hello says 1 or 2 with **4400** and the reason *update
    LLeMbas CLI to 2.0*; a 1.x CLI stops on 4400 without redialling. A 2.0 CLI's hello says 3, and
    a 1.x server closes it with 4400 likewise; the CLI stops and says the server needs updating.
- **Within protocol 3** every feature is still named by a **capability string** in `initialize`'s
  `_meta.lembas.capabilities`, and **LLeMbas never calls a method, nor relies on a field, the
  device did not list**.
- A client says it is LLeMbas with `clientCapabilities._meta.lembas` in `initialize`, and which
  protocol it speaks with `_meta.lembas.protocol` (absent: 1 — a protocol 1 client sends `lembas: true`).
  Only such a client gets `_lembas/event`; only one that says protocol **2 or later** gets
  `_lembas/question` and `_lembas/plan` (a 2.0 server says 3), and one that then answers them with
  method-not-found is treated as protocol 1 for the rest of the link — what an editor on
  `serve --stdio` is.

| Capability | What it promises |
|---|---|
| `directories` | `_lembas/directories` |
| `configure` | `_lembas/configure` |
| `steer` | `_lembas/steer`; `unsent` in a prompt's answer |
| `terminal` | `_lembas/terminal/*` (only with `remote.terminal: true`) |
| `sessions` | a session id lasts (opened from the store again); terminal sessions announced (`announce`, `left`, `history`) |
| `status` | `_lembas/session/status` |
| `delete` | `_lembas/session/delete`; `_lembas/session/deleted` |
| `compact` | `_lembas/session/compact` |
| `title` | `_lembas/session/title` |
| `files` | `_lembas/files/search`; `@path` in a prompt expanded |
| `commands` | `available_commands_update`, `_lembas/commands`; `/name` in a prompt expanded |
| `attachments` | `image` and `resource` blocks in `session/prompt`; `attachments` on prompt events and history |
| `ask` | `_lembas/question` |
| `plan` | `_lembas/plan` |
| `model` | the `model` event; `_meta.lembas.connection` in `initialize` |
| `turns` | `_meta.lembas.turnId` on `session/prompt`, deduplicated; `turnId` on `prompt`/`task` events; turn fields in `status` |
| `shell` | `shell` and `integration` in `_lembas/terminal/open`'s answer (listed only with `terminal`) |
| `context` | `system_prompt` and `personalization` in `session/new` and `session/prompt` `_meta.lembas` are read |
| `library` | `library` in `session/new` and `session/prompt` `_meta.lembas` is read; `_meta.lembas.scope` sent on the instance library's `tools/call`. Spec 3.0.0: an instance's chats have no knowledge bases, so a 3.0.0 instance sends no `library`; the scope is still sent on its `memory` calls (*The HTTP API*) |
| `plan_state` | the structured plan: `_meta.lembas.plan` on the `plan` session update and on `_lembas/plan`; `plan` in `_lembas/session/status` |
| `rewind` | `_lembas/session/rewind` |
| `show` | spec 3.0.0: `_lembas/show`; the `show` tool is offered in a session the client opened when the client said `show: true` in `clientCapabilities._meta.lembas` |
| `jobs` | background work (spec 2.0.0): the `job` and `job_note` events; `_lembas/jobs`, `_lembas/job/output`, `_lembas/job/stop`, `_lembas/job/send`, `_lembas/job/transcript`; `jobs` in `_lembas/session/status`; `job` on `session/request_permission`; `machine` on a `prompt` event |
| `progress` | spec 3.4.0 (`progress.md`): the `phase` event; `progress` in `_lembas/session/status` |
| `agents` | spec 3.4.0: the session's Subagents setting `{ min, max }` is read from `_meta.lembas.agents` on `session/new`, `session/prompt` and `_lembas/configure` (*Subagents in an agent chat*, below) |
| `tree` | spec 3.4.0 (`subagents.md` §5–§13): the `team` event; `_lembas/team`, `_lembas/team/transcript`, `_lembas/team/findings`, `_lembas/team/stop`; `subagent` on `session/request_permission` |
| `scheduled` | spec 3.5.0 (*Scheduled work on a linked computer*, below): `scheduled` in `_meta.lembas` of `session/new` and `session/prompt` read, and the run's outcome in the prompt's answer; `scheduled` on `session/request_permission`; `_lembas/monitor`; `limits.scheduled` in `_lembas/hello` and `initialize`. Listed by every build that has it, whatever the setting says: whether this device *accepts* scheduled work is `limits.scheduled` |
| `reports` | spec 3.8.0 (`reports.md` §6): `scheduled.notify` in `_meta.lembas` of `session/new` and `session/prompt` read; `report_write` offered to the model of a schedule's run that notifies; the report the run wrote carried in the prompt's answer (`scheduled.report`); the status `silent` no longer given to a turn whose request said `notify` |

## Standard ACP, as used here

### `initialize` — client → agent

Params: `{ protocolVersion: 1, clientCapabilities: { _meta?: { lembas?: { protocol: 3, show?: true } | true } } }`.

Result:

```json
{
  "protocolVersion": 1,
  "agentCapabilities": {
    "loadSession": false,
    "promptCapabilities": { "image": true, "audio": false, "embeddedContext": true },
    "mcpCapabilities": { "http": false, "sse": false }
  },
  "authMethods": [],
  "agentInfo": { "name": "LLeMbas CLI", "version": "2.0.0" },
  "_meta": { "lembas": {
    "protocol": 3,
    "harness_spec": "3.0.0",
    "limits": { "roots": ["~/work"], "max_mode": "edit", "scheduled": false },
    "connection": "example",
    "capabilities": ["directories", "configure", "…"]
  } }
}
```

`limits` is `null` on `serve --stdio`. `limits.scheduled` (spec 3.5.0, capability `scheduled`): whether
this device accepts scheduled work now (`remote.scheduled`, *Scheduled work on a linked computer*). `connection` is the device's name for the instance — the
login's connection — or `null`; the `model` event and `announce` say directly whether a model is
the instance's (`instance`).

**How the device names an instance's models.** The served id **is the device's ref** for a model
(`deepseek/deepseek-flash`; spec 3.0.0: a 2.0 device talks to one server, the one it is logged in
to, so there is nothing to tell apart — no connection prefix, no second login, no connection of
the device's own). The older forms (`<login>/<id>`, and from before providers `<login>/<model>` for
the one served `<provider>/<model>` of that name) still name the same model. `lembas_model` in
`session/new` and `model` in `_lembas/configure` are the instance's served name, as it serves it.
`model` in a `model` event and in a result is the device's ref, which is the served id.

### `session/new` — client → agent

Params: `{ cwd: <absolute>, mcpServers?: [], _meta?: { mode?, lembas_model?, model?, effort?, lembas?: Context } }`.
`cwd` must be a directory inside `remote.roots`, in a trusted project when `remote.require_trust`
(the default) — otherwise `-32602` with the reason. `mode` is lowered to `remote.max_mode`.
`lembas_model` is the instance's name of a model; `model` a ref the device knows (an editor's
choice); `effort` one of the model's levels (`models/schema.json` `levels`, spec 3.2.0: `"off"` where the model has `thinking_switch`, `"default"` where it takes no efforts, else an effort); a level the model does not offer is read as its start level, and the result says the level used.

Result: `{ sessionId: "ses_…", modes: { currentModeId, availableModes }, _meta: { lembas: { model, mode, effort, instance } } }`.
`instance` (spec 3.0.0) is a boolean, with the `model` event's meaning: true exactly when the
session's model is one this link's instance serves.

**The chat's model, full stop** (spec 3.0.0). A session the client opens with `lembas_model` runs on
**that** model and on no other: the device takes it as given, for every turn of the session, until
the client changes it (`_lembas/configure`). If the device cannot speak to it — the instance does not
serve it to this device, or this device is not logged in to the link's instance — `session/new`
fails with `-32602` and the reason (so does a `_lembas/configure` naming such a model, and the
session keeps the model it had), and a turn that loses the model midway fails with its error; the
device never answers on a model of its own instead, and never falls back on its own. So `instance`
is true for every session the client opened, and a client has nothing to say about a device having
"answered on its own model": that notice is gone. A session a terminal shares (`announce`) keeps the
terminal's model, and its `instance` says which it is.
The id is the device's store id and lasts: a session the client names later that is not in memory
is opened from the store under the same rules (capability `sessions`). With capability `commands`,
an `available_commands_update` follows the answer.

**`_meta.lembas` — what only the client knows** (`Context`, read on `session/new` and again on every
`session/prompt`, the newer replacing the older; the device composes its own prompt from the spec's
texts, as for its own sessions, and these are the pieces the instance holds):

| Field | Capability | What |
|---|---|---|
| `system_prompt: string` | `context` | the chat's effective authored prompt (chat over folder over model over instance), appended after the shared text as `## Instructions from <instance>`; empty: none |
| `personalization: { personality, personality_custom, instructions } \| null` | `context` | the account's, as `/v1/me/personalization` gives them, used in place of the device's own for this session; `null`: the device's own |
| `library: { bases: [{ id, name }] }` | `library` | the knowledge bases this chat may search, named to the model (`blocks/library.md`'s `{{knowledge_scope}}`) and sent back as the scope on every library call (below); an empty list: the account's whole library, as before |

### `session/prompt` — client → agent

Params: `{ sessionId, prompt: ContentBlock[], _meta?: { lembas?: { turnId?: string, ...Context } } }`.

Blocks (capability `attachments` for the last two kinds):

| Block | Becomes |
|---|---|
| `{ type: "text", text }` | the prompt's text (blocks joined by a blank line) |
| `{ type: "resource_link", uri }` | `(see <uri>)` in the text |
| `{ type: "image", data: <base64>, mimeType, uri? }` | written under the session's attachment directory and attached by path, as the TUI does with a pasted image: an image part for a vision model, a line saying it is there for any other. `mimeType`: `image/png`, `image/jpeg`, `image/webp`, `image/gif` |
| `{ type: "resource", resource: { uri, mimeType?, text } }` | written there and attached like an `@file` (line-numbered, counted as read) |
| `{ type: "resource", resource: { uri, mimeType?, blob: <base64> } }` | written there and referenced by path (`(binary file, N bytes)`), or attached as an image when it is one |

Limits: 10 MB a block and 25 MB a prompt, decoded; over either, `-32602` with a sentence. The
attachment directory is `~/.local/state/lembas/attachments/<sessionId>/`, removed with the session;
each upload gets a name of its own there (`<time>-<random>-<name>`), so two of one name stay two.
A command's `mode` in a session shared under the limits is lowered to `remote.max_mode`, as it is in
a session the service runs; a command's `model` holds for its prompt, and the model and effort the
session had come back afterwards.

The text is then expanded **as the TUI would** (capabilities `files`, `commands`):

- A text starting with `/name`, where `name` is in the session's command set (below), is that
  command: a prompt command sends its expanded text (shown in the transcript as typed); `compact`,
  `undo` and `plan` without a task are actions and send nothing to the model. A `/name` not in the
  set — a path, a regex, a TUI-only command — is sent to the model as plain text, `/` and all.
- **Writing a path:** a space or a `#` in a name is escaped with a backslash — `@my\ notes.md`,
  `@a\#b.md` — which is how the TUI's picker writes it and how the expander reads it; an unescaped
  `#` followed by digits is a line range (`@c.md#10-20`).
- `@path` mentions in what the person typed are attached by the TUI's expander, with its limits
  (`project/attach.ts`): files line-numbered, `@path#10-20` a range, `@dir/` a listing, images by
  vision. Never from a command's own text (a skill's, a custom command's body, a diff). **Only a
  path that is — symlinks followed — inside the session's project and inside `remote.roots`, and is
  not a protected path, is attached**; any other `@word` stays plain text and is not read. In a
  terminal's shared session the same holds for a prompt from the web (the device's roots; only the
  project when the session was shared by name and no roots are set).

Turn ids (capability `turns`): `turnId` is the client's id for this turn (LLeMbas: its user
message id). The device keeps, per session, the last 200 ids it accepted with their state
`queued | running | done` — the finished ones with their answers also in one file per session
(`~/.local/state/lembas/turns/<sessionId>.json`, written atomically and merged), which the service
and every terminal use alike, so they outlive a restart and a session moving between a terminal and
the service in either direction; files untouched for 30 days are pruned — and **a prompt with a known turnId does not run again**: it gets the
original's answer — when that one is done, or at once if it is. Without a `turnId` the device makes
one. A prompt that arrives while the session works waits its turn.

Result: `{ stopReason: "end_turn" | "max_turn_requests" | "max_tokens" | "cancelled", _meta: { lembas: { turnId, … } } }`,
where `_meta.lembas` may also carry:

| Field | When |
|---|---|
| `unsent: string[]` | messages sent with `_lembas/steer` that the turn ended before taking in — send them next |
| `command: string` | the prompt was that command |
| `summary: string` | `/compact` |
| `undone: { prompt, files } \| null` | `/undo` (null: nothing to undo) |
| `mode: string` | `/plan` without a task: the mode now |
| `message: string` | a command that had nothing to do (`/review` with no changes), said instead |

### `session/set_mode` — client → agent

Params: `{ sessionId, modeId }`, lowered to `remote.max_mode`. Result `{}`.

### `session/cancel` — client → agent (notification)

Params: `{ sessionId }`. Stops the running turn.

### `session/update` — agent → client (notification)

`{ sessionId, update }` with `sessionUpdate` one of `agent_message_chunk`, `agent_thought_chunk`,
`tool_call`, `tool_call_update`, `plan` (below), `current_mode_update`, and
`available_commands_update` (capability `commands`).

From spec 3.7.0 a `tool_call`'s `title` is **the call's short title** (`display.md` §1) — the
tool's name where the call has none — and no `tool_call_update` changes it: `completed` is the
state `done`, `failed` is `failed`, and a call that was refused — it never started, so nothing
announced it — is one `tool_call` with status `failed`, `_meta.lembas.denied: true` and the reason
as its content (the state `denied`). The same title is
`title` on the `tool_start` event of `_lembas/event` and `what` of `running_tool`; `tool_end`
carries `ms`, the call's duration, and `tool_denied` its `reason`. A client of `_lembas/event`
that is given no `title` (a device before 3.7.0) makes it from `args` by the same rule.

`available_commands_update`:

```json
{ "sessionUpdate": "available_commands_update",
  "availableCommands": [ { "name": "review", "description": "…", "input": { "hint": "head | <branch>" },
                           "_meta": { "lembas": { "kind": "builtin" } } } ] }
```

Sent after `session/new`, after a session is opened from the store or taken over by a new link,
when a terminal's session is shared, and after a turn whenever the set changed (a skill made, a
command file written). `kind`: `builtin`, `custom` (`.agent/commands`, `~/.config/lembas/commands`)
or `skill`. The set is the shared one only: the built-ins `compact`, `plan`, `undo`, `review`,
`changelog`, `init`, `continue`, then custom commands, then skills.

`plan` (capability `plan_state`): the session's plan, as `plan_write` and `plan_update` keep it —
`{ sessionUpdate: "plan", entries: [{ content, priority: "medium", status }], _meta: { lembas: { plan: Plan } } }`.
`entries` is ACP's flat list for an editor (every task, `status` one of `pending`, `in_progress`,
`completed` — a dropped task is `completed` there, ACP having no word for it); `_meta.lembas.plan` is the structure (`conformance/plan.json`: `version`, `title`,
`summary`, `findings`, `objectives`, `phases` with their `tasks`, `steps`), which the web UI draws as
the plan card. Sent after every change, after `session/new` and after a session is opened from the
store when it has a plan, and with `plan: null` when the plan is cleared. Commands that need the TUI's
own screens (sessions, settings, theme, login, quit, …) are never listed; a custom command or skill
named like any TUI command is not a command (the built-in wins in the TUI).

### `session/request_permission` — agent → client

An approval card. Params: ACP's `{ sessionId, toolCall: { toolCallId, title, kind, status: "pending", rawInput, content? }, options }`
with options `once` (allow_once), `session` (allow_always, absent where "always" is never offered)
and `deny` (reject_once), and `_meta.lembas: { tool, reason, purpose?, always?, patient?, job? }`. `tool`
(spec 3.7.0): the tool's name — `toolCall.title` here is **the whole command, or the tool's name where
the call has no command**, as before 3.7.0 (a card is read before it is answered; the call's short
title of `display.md` §1 is `tool_call`'s, not the card's), so a client that titles a card by the
command still has the tool to name. `patient`: a
person may also answer at the terminal (a shared session), so the client does not time it out.
`job` (capability `jobs`): `{ id, agent, description }` when a subagent running as a background job asks, so the card
can say which — a background subagent may ask while the session itself is idle. `subagent` (capability `tree`, spec 3.4.0):
`{ id, name, position }` when the call is a tree subagent's that nobody in the tree could allow (`subagents.md` §7) —
the card names it; the answers are the same as for the main agent's own call.
`scheduled` (capability `scheduled`, spec 3.5.0): `{ schedule_id, run_id, wait_s }` when the call is
a scheduled turn's — nobody is at the chat, so the client puts the card to the person where they are
(LLeMbas: a notification on their phone carrying the card, answerable there and in every client),
and the device waits `wait_s` for it, not `remote.approval_timeout` (*Scheduled work*, below).
Answer: ACP's `{ outcome: { outcome: "selected", optionId } | { outcome: "cancelled" } }`, with
optional `_meta.lembas.command` (the corrected command line, `once` only) or
`_meta.lembas.feedback` (why it was denied). No answer within `remote.approval_timeout` is a denial.

## Subagents in an agent chat (spec 3.4.0)

**An agent chat's main agent is the CLI on the device.** Its subagents are the device's: it starts
them locally, on the models of the server it is logged in to, under every rule of `subagents.md` —
the model rules (§2–§3), Min/Max, slots, permissions only ever narrowed, request options, and a
call nobody in the tree can allow going to the person. The server runs no subagent of its own for
an agent chat and offers its own subagent tool to no agent chat; what it does is carry the setting
down and the tree up. A device without the capabilities `agents` and `tree` gets neither and runs no
subagents for the chat (the chat says so beside the setting).

**What goes down, with every turn.**

- `agents: { min, max }` in `_meta.lembas` of `session/new`, of **every** `session/prompt`, and of
  `_lembas/configure` when the person changes it between turns — the chat's setting as the server
  keeps it (`team.md` §2: 1 ≤ min ≤ max ≤ the instance's cap). The device takes the value as the
  session's setting for that turn: a change reaches the main agent as `team.md` §10's note, and
  nothing else reads the device's own configuration for the chat. Absent (an older server, or a
  client that is not LLeMbas): the device's own setting, as before 3.4.0. A value the device's own global cap is under is read as `team.md` §2
  says (lowered, never refused).
- `subagents: false` in the same `_meta.lembas` when the instance has subagents switched off: the
  device offers none whatever `agents` says.
- **What the person named** (`subagents.md` §2): the person's words reach the device as the turns'
  prompt texts, so the device judges a named model from them as in its own sessions — every
  `session/prompt` without `machine`; never a `machine` turn, a `_lembas/steer` from the harness, or
  a tool's output. A chat continued on a device that has not seen its earlier turns gets them in
  `_meta.lembas.said: string[]`, the person's earlier messages of the chat, oldest first (on
  `session/new` only); the device counts them as typed in the session.
- The models: the device's subagents may run on any model of the person's (`/v1/models` as the
  device's token sees it), as in its own sessions.

**What comes up.**

- The tree, as it changes: the `team` event (`{ tree }`, `subagents.md` §12's data, at most four a
  second), and on request `_lembas/team` (the tree and the whole room). Every agent's `progress`
  is in it; the main agent's own also as the `phase` event (capability `progress`).
- Approvals: a subagent's call that only the person can allow is an ordinary
  `session/request_permission` with `_meta.lembas.subagent: { id, name, position }`; the server
  shows it as its usual approval card naming the subagent, in every client. Requests the main agent
  decides (`team_request` / `team_decide`) never reach the server as approvals: they are room
  messages.
- Findings and transcripts on request: `_lembas/team/findings`, `_lembas/team/transcript` (live and
  ended subagents, read-only — the link has no method that writes into a subagent's session).
- The person stopping a subagent: `_lembas/team/stop`.

**What the server keeps.** The latest tree with the chat (so a client opening the chat draws it at
once, and after the device has gone), and the room as `_lembas/team` last answered. Findings and
transcripts stay on the device; a client asking for one while the device is not linked is told it
is on the device, and gets it once the device links again. Over `/api/v2` the chat's stream carries
the tree as it changes and the endpoints to read a subagent's transcript and findings, the same for
a chat's own subagents and for an agent chat's — so Web, Desktop and Android draw both alike.

## Scheduled work on a linked computer (spec 3.5.0)

A schedule (the server's: its rule, its runs, its history and notifications) may run **on a linked
computer**: each run is an agent turn on that device, in a folder of it, instead of a chat on the
server. The server decides *when*; the device decides *whether*, and *how far* — every rule an agent
chat has still holds, and the ones below are added. Numbers and texts: `loop.json` `scheduled`.
Conformance areas `scheduled`, `silent`, `monitor`.

### The device's setting: Accept scheduled work

- **`remote.scheduled`** in the device's global config, **off by default**; shown as **Accept
  scheduled work** (`loop.json` `scheduled.setting`). Set from the command line with
  `lembas service scheduled on|off` (also `lembas remote scheduled on|off`; `lembas remote list
  --json` says `scheduled`), and by LLeMbas Desktop through the same command. It is global only: a
  project's config cannot turn it on.
- It narrows remote work and widens nothing: a scheduled turn also needs `remote.enabled`, a folder
  inside `remote.roots`, a trusted project where `remote.require_trust`, and a mode within
  `remote.max_mode`, exactly as an agent chat does.
- The device says it in `limits.scheduled` (hello and `initialize`). Turning it on or off changes
  the limits, so the device dials again once nothing works (as for any limit) and the client hears
  it. The client may offer the device in a schedule's form only while `limits.scheduled` is true,
  and says why not otherwise. The device judges it again at every `session/new`, `session/prompt`
  and `_lembas/monitor` that is scheduled, whatever it said earlier.
- **Off** (or remote work off): every scheduled request is refused with `-32602`, the message
  `loop.json` `scheduled.texts.off` word for word, and `data: { reason: "scheduled_off" }`. Not
  retried: it is the person's setting. A folder the device does not accept is `-32602` with the
  usual reason and `data: { reason: "folder" }`.

### Starting a scheduled turn

The client opens an agent session as for an agent chat (`session/new`: `cwd`, `lembas_model`,
`effort`, `_meta.lembas` with `agents` — the schedule's **Min/Max**, as an agent chat's — and the
`Context`), marked scheduled:

```json
"_meta": { "mode": "edit", "lembas_model": "qwen", "lembas": {
  "agents": { "min": 1, "max": 4 },
  "scheduled": { "schedule_id": "sched1", "run_id": "run7", "title": "Waybread check",
                 "mode": "edit", "approval_wait_s": 1800 } } }
```

and runs the turn with `session/prompt`, its `_meta.lembas` carrying the same `scheduled` object
(and a `turnId`, the run's — so a prompt sent again after a dropped link gets the run's answer
instead of a second run). A schedule that continues one conversation (`continue`) prompts the same
session again on its next run; a fresh run opens a new one. The prompt's text is the server's own
for a scheduled run — the instruction, the run's time, and for a schedule that notifies only when
it matters the `[SILENT]` hint — composed exactly as for a run on the server; the device adds nothing.

**The mode** (`scheduled.mode`; `_meta.mode` is ignored for a scheduled session): `manual`, `edit`
or `auto`, and **`edit` when absent** — so a run is never wider than Edit unless the person picked
Auto on that schedule. `plan` is refused (`scheduled.texts.plan`, `data: { reason: "plan" }`):
nobody is there to approve a plan. Then lowered to `remote.max_mode`, as every remote session is —
Auto runs only where both the schedule and the device allow it (`conformance/scheduled.json`). The
mode holds for the turn; neither `_lembas/configure` nor a command in the prompt widens it.

In a scheduled turn:

- **Approvals** go to the person through the client (`session/request_permission` with
  `_meta.lembas.scheduled: { schedule_id, run_id, wait_s }`), and the device waits
  `approval_wait_s` — the schedule's wait, 30 minutes by default, clamped to
  `scheduled.approval_wait_seconds` — instead of `remote.approval_timeout`. Unanswered by then, the
  call is denied with `scheduled.texts.denied` as the feedback the model reads (`{{minutes}}` the
  wait in whole minutes), the card is settled (`_lembas/permission/settled`), and the turn goes on —
  the model works around the refusal or fails. Not `patient`.
- **`ask_user` and `plan_submit`** have nobody to answer: the device answers them as it does with
  no client (`ask_user` tells the model nobody can be asked; `plan_submit` saves the plan and says
  to stop). `show` is not offered: nobody is looking.
- Subagents as in an agent chat (`agents`, the tree, findings); a subagent's call only the person
  can allow is an approval like the main agent's, with `scheduled` beside `subagent`.

### How the turn ends and reports

`session/prompt`'s answer carries the run's outcome in `_meta.lembas.scheduled`:

```json
{ "stopReason": "end_turn", "_meta": { "lembas": { "turnId": "run7",
  "scheduled": { "run_id": "run7", "status": "ok", "text": "All the waybread is wrapped.",
                 "error": null } } } }
```

**From spec 3.8.0 (capability `reports`, `reports.md` §6)** the client marks the turn with the
schedule's notify-when — `"scheduled": { …, "notify": "always" | "when" | "never" }` in
`session/new` and `session/prompt` — and the prompt it composes carries the instruction for it
(`loop.json` `scheduled.notify.texts`) where the `[SILENT]` hint used to be. A device with the
capability offers `report_write` in that session unless `notify` is `never`
(`tools/report_write.json`, `requires: scheduled`: unavailable in a turn of the session that is
not the schedule's), keeps the last report the run's model wrote, and answers with it:

```json
"scheduled": { "run_id": "run7", "status": "ok", "text": "Filed a report: the disk is almost full.",
               "error": null,
               "report": { "title": "Disk almost full", "summary": "The log partition is at 94%.",
                           "body": "## What\n\n…", "importance": "high" } }
```

`report` is absent when none was written; it is kept even when the turn then failed. For a turn
whose request said `notify`, the sentinel means nothing and `status` is never `silent`. What the
run leaves — the report, the `always` fallback, the history line, the notification — is the
client's to decide from this answer (`reports.md` §4). A device without the capability is sent no
`notify`; a client without it sends none, and everything below is as in 3.5.0.

- **`text`**: the turn's final reply — the text of the last assistant message the turn wrote, as
  written (`[SILENT]` included). Model output: untrusted wherever it is shown.
- **`status`**: `cancelled` — the turn was cancelled (`session/cancel`: the person stopped it, or the
  client's inactivity limit); `failed` — the turn ended on an error (`error` its text), or with no
  reply text at all (`scheduled.texts.no_reply`); `silent` — the reply says the sentinel
  `[SILENT]` on its first or last non-empty line (any case, a trailing full stop forgiven;
  `conformance/silent.json`, the server's own rule for its runs); otherwise `ok`. A turn that hit the
  step backstop or a budget and still wrote a reply is `ok`; `stopReason` says why it stopped.
- The client records the run from this (its history, its notification by the schedule's policy —
  `silent` is never notified, a failure is told as a failure), and keeps the session as the run's
  agent chat, whose events it received as for any agent chat. What the sentinel stripped looks like
  is `silent.json`'s `stripped`.

### When the device is away, or refuses

- **Away** (not linked when the run is due, or the link drops before `session/new` is answered): the
  client treats the device like a busy model — the run is `waiting` (`waiting_for: "device"`) and
  tried again on the same steps a run waiting for a free model is, until the run's window closes;
  then it is **missed**. A turn already running when the link drops is followed again by
  `_lembas/session/status` and the turn id, as for any agent chat.
- **Refused** (`-32602` from a scheduled `session/new`, `session/prompt` or `_lembas/monitor`): the
  run **fails** with the device's message, said in the run's history and in the failure
  notification; `data.reason` (`scheduled_off`, `folder`, `plan`, `permission`) lets the client say
  what to change. Not retried.

### Monitor mode: a command watched on the device

A schedule may **watch** something and run the model only when it changed. A URL is fetched by the
server and needs nothing from the device; a **command's output** is watched on the device with
`_lembas/monitor` (below): the client asks the device to run the watch command, compares the
answer's `hash` with the one the previous check stored, and when it is the same records the run as
`unchanged` — no model, nobody notified. Changed (or the first check), the model runs as the
schedule says — on the server, or as a scheduled turn on the device — told what changed through the
`excerpt`. A check that fails (timed out, refused) is a failed run.

The command runs under the **same permission rules as the schedule's mode**, with one difference:
**a command the mode would ask about is refused**, because a watch has nobody to ask. The device
judges the command line as `bash` would be judged in a session in that folder (the hardline, the
default rules, the global rules and — in a trusted project — the project's), and runs it only on
`allow`; `ask` is refused with `scheduled.texts.monitor_asks`, `deny` with
`scheduled.texts.monitor_denied`, both `-32602` with `data: { reason: "permission" }`
(`conformance/monitor.json`).

**The hash** (`monitor.json` `hash` cases): `sha256:` and the lowercase hex SHA-256 of the
output's bytes exactly as read — standard output and standard error as one stream, as the `bash`
tool reads them — followed by a newline and the exit status as decimal digits (after the bytes, so
the device hashes the output as it streams, however long). Nothing is normalised: a watch command
that prints a time changes every time, and should not.

## `_lembas/*` methods

Direction is who sends it. "→ result" marks a request; the others are notifications.

### `_lembas/hello` — agent → client

Sent once when the link opens, before `initialize`:
`{ protocol, version, harness_spec, device: { host, platform }, limits: { roots, max_mode, require_trust, scheduled } }`
(`scheduled` since spec 3.5.0: whether the device accepts scheduled work; a change of it is a
change of the limits, below).

A device links whether its remote work is on or not, and with no roots at all. Off, or with no
roots, it says `limits.roots: []` here and in `initialize`, and refuses every `session/new`, every
`_lembas/directories` path and every `_lembas/terminal/open` (`-32602`, with the reason) as it does
a directory outside its roots — so the client can tell a device that allows no folder yet from one
that is not linked.

The device's limits apply to every request as they are at that moment. When what the hello and
`initialize` said changes (the device's `remote:` block was edited — a folder added, remote work
turned on), the device closes the link once no session is working and dials again at once, so the
client hears the new limits.

### `_lembas/event` — agent → client

`{ sessionId, event }`: every engine event of the session, to a client that said `_meta.lembas`.
`event.type` is one of `session`, `prompt`, `step`, `title`, `text`, `reasoning`,
`tool_call_delta`, `progress`, `tool_start`, `tool_end`, `tool_denied`, `usage`, `inbox`,
`steered`, `task`, `notice`, `retract`, `plan`, `compacted`, `sub_tool`, `job`, `job_note`, `phase`, `team`, `mode`, `model`, `flash`,
`setting`, `error`, `done` (`src/bus/index.ts`). Those this protocol relies on:

| Event | Fields |
|---|---|
| `prompt` | `text` (as the person sees it: a command's name, not its body), `turnId`, `attachments?: [{ name, mimeType, size }]`, `machine?: true` (capability `jobs`) — the turn was started by the harness, not typed: a finished job's or subagent's note, or a subagent's message, beginning with its mark (`loop.json` `jobs.marks`); a client shows it as such and sends it to no model of its own |
| `task` | `state: "start" \| "end"`, `turnId` — every turn has one: the client's, or one made here for a turn typed in a terminal |
| `steered` | `texts`, `ids?` — the `_lembas/steer` message ids taken in at this step |
| `mode` | `mode` |
| `model` | `ref` (the device's ref, above), `effort` (one of the model's levels: an effort, `"off"` or `"default"`), `connection` (the device connection it is spoken to through) and **`instance: boolean`** — true exactly when that connection is the login of **this link's** instance, i.e. the model is one the instance serves (its served id is `ref`, or `ref` less a leading `<connection>/`). Whenever either changes, for any reason: `/model`, `/effort`, the settings tool, a fallback, `_lembas/configure` |
| `plan` | `plan` — the structure, or `null` when cleared (what the `plan` session update carries) |
| `compacted` | `summary` |
| `title` | `id`, `title` |
| `job` | `state: "start" \| "update" \| "end"`, `job: JobInfo` (capability `jobs`) — a background job of the session began, has news (new output, a subagent's step; at most four a second per job), or ended. `JobInfo = { id, kind: "command" \| "subagent", state: "running" \| "done" \| "stopped" \| "error" \| "lost", description, command?, agent?, model?, started, ended?, exit?, unread, steps?, tools?, ownerJobId? }` — `description` what is shown for it (a command's `description` or its first line; a subagent's description), `unread` characters of output the model has not read, `exit` a command's exit code (null: a signal), `ownerJobId` set on a job a subagent started, `lost` a job that was running when the device's process ended |
| `job_note` | `jobId`, `text` (capability `jobs`) — the note about that job was given to the model: inside a turn at the next step, or as the `machine` turn whose `prompt` follows |
| `sub_tool` | `callId` (the `task` call the subagent belongs to), `jobId?` (its job, when it runs as one), `name`, `title`, `isError?` — a tool call of a subagent finished; a client draws it under the task line, or in the job's panel |
| `phase` | `progress` — the progress object (`progress.md` §1), or `null` when nothing runs; `agent?` — the subagent's id when it is a subagent's, absent for the main agent's (capability `progress`). Sent when the phase changes, and for a change of its data at most four times a second. The existing `progress` event (the prompt's tokens read) is unchanged |
| `team` | `tree` — the session's subagent tree (`subagents.md` §12, the data) — whenever it changes, at most four times a second (capability `tree`). A client draws it and fetches nothing else to do so |

### `_lembas/monitor` — client → agent → result (capability `scheduled`)

The watch command of a schedule in monitor mode (*Monitor mode*, above). Params
`{ cwd, command, mode?, timeout_s?, schedule_id?, run_id? }` — `cwd` absolute, judged as
`session/new`'s; `mode` the schedule's (`manual`, `edit` or `auto`; `edit` when absent; `plan`
refused), lowered to `remote.max_mode`; `timeout_s` defaulting to and clamped by
`loop.json` `scheduled.monitor.timeout_seconds`. Refused like any scheduled request when the device
does not accept scheduled work. The command runs as `bash -c` in `cwd`, in its own process group,
with no input; at the timeout the group is stopped.

Result `{ hash, exit, timed_out, excerpt, truncated, bytes, duration_ms }`: `hash` as above (`null`
when it timed out), `exit` the exit status (`null` on a signal or a timeout), `excerpt` the start of
the output decoded as UTF-8 (invalid bytes replaced), at most `scheduled.monitor.excerpt_chars`
characters, `truncated` whether there was more, `bytes` the output's length. The device hashes all
of the output as it streams and keeps at most `scheduled.monitor.output_max_bytes` of it.

### `_lembas/directories` — client → agent → result

Params `{ path?: string }`. Without a path: `{ path: "", places: Dir[] }`, the remote roots and every
trusted directory inside them. With one (absolute, inside the roots): `{ …Dir, parent, entries: Dir[], error? }`.
`Dir = { path, name, accepted: boolean, reason? }` — whether `session/new` would accept it.
Only on the link (an error on `serve --stdio`).

### `_lembas/configure` — client → agent → result

Params `{ sessionId, model?: <instance's name>, effort?: <level>, agents?: { min, max } }` between prompts (a level as in `session/new`; `agents` with capability `agents`, spec 3.4.0). Result
`{ model: <ref>, effort }`. Emits a `model` event when something changed.

### `_lembas/steer` — client → agent → result

Params `{ sessionId, text, messageId? }`: a message for the running task, given to the model at its
next step. Result `{ queued: number }`. `-32602` when nothing runs: send it as a prompt instead.

### `_lembas/session/history` — client → agent → result

Params `{ sessionId }`. Result `{ sessionId, title, root, turns }`, each turn
`{ role: "user", text, turnId?, attachments?: [{ name, mimeType, size }] }` (`turnId`
the turn the message started — the client's, or the one made for a turn typed in a terminal — kept
with the message in the store; `attachments`, and the text without them) or `{ role: "assistant", text, reasoning, tools: [{ id, name, args, output?, isError? }] }`.

### `_lembas/session/status` — client → agent → result

Params `{ sessionId }`. Result
`{ sessionId, exists, held: "here" | "terminal" | "none", busy, title, turnId?, queued: string[], lastTurnId?, plan?, jobs?, progress?, tree? }`
— `plan` the session's plan or `null` (capability `plan_state`), so a client reattaching draws it
without waiting for the next change; `progress` the main agent's progress object or `null` (capability `progress`), and `tree` the subagent tree or `null` when the session has none (capability `tree`), for the same reason; `jobs` the session's background jobs, `JobInfo[]` running first (capability `jobs`), for the same reason; the turn fields with capability `turns`: the turn running now, the ones waiting, the last
one finished (for a session not open, `held: "none"`, the last one it finished before). **Reattaching** after a restart or a dropped link, for an incomplete row with turn T:
T running → follow it; T queued → wait, and follow it when it starts; T done → import it from
`history`; T unknown → send the prompt again (safe: the device deduplicates).

### `_lembas/jobs` — client → agent → result (capability `jobs`)

Params `{ sessionId }`. Result `{ jobs: JobInfo[] }` — the session's background jobs, running ones
first, then the finished ones the device still keeps (`loop.json` `jobs.finished_kept`). The same
list `job_list` gives the model. For a session held by a terminal, answered by the terminal through
the hub, as `status` is.

### `_lembas/job/output` — client → agent → result (capability `jobs`)

Params `{ sessionId, id, since?: number }`. Result `{ text, length, state }`: a command's output
from character `since` (default 0; the device keeps the last 2 MB), `length` how much it holds now
so the next call can carry on, `state` as in `JobInfo`. For a subagent, `text` is its progress as
`job_output` gives the model: one line per tool call and what it wrote. Reading here does **not**
move the model's own read mark.

### `_lembas/job/stop` — client → agent → result (capability `jobs`)

Params `{ sessionId, id }`. Result `{ state }` — `stopped`, or the state it already had. The
owning session is woken with the end note as it would be for any end: the person's stop is a fact
the model is told, not a silent disappearance. `-32602` for an id the session does not have.

### `_lembas/job/send` — client → agent → result (capability `jobs`)

Params `{ sessionId, id, text }`: a message for a subagent job — what `job_send` does for the
model, from the person. Running: given to it at its next step under the `to_subagent` mark;
finished: starts it again with that text, under the same id, and a new `job` `start` event follows.
Result `{ state }`. `-32602` for a command job, or an id the session does not have.

### `_lembas/job/transcript` — client → agent → result (capability `jobs`)

Params `{ sessionId, id }`. Result `{ id, turns }` in the shape of `_lembas/session/history`'s
`turns`: the subagent's own conversation so far — its prompt, what it wrote and every tool call
with its output — so a client can show the subagent as a panel and follow it with the `sub_tool` and
`job` events that come after. `-32602` for a command job. A subagent's transcript lasts as long as
its session row on the device: it is kept with the parent session and deleted with it.

### `_lembas/team` — client → agent → result (capability `tree`)

Params `{ sessionId, since?: number }`. Result `{ tree, room }`: the subagent tree (`subagents.md`
§12) and the room's messages numbered above `since` (default 0), every one — the person sees the
whole room — each `{ n, from, to, recipients, text, time, report, kind }` with `kind` one of
`message`, `report`, `assignment`, `request`, `decision`. `tree` is `null` for a session with no
subagents. For a session held by a terminal, answered through the hub, as `status` is.

### `_lembas/team/transcript` — client → agent → result (capability `tree`)

Params `{ sessionId, id }` — a subagent's id (`s1`), live or ended. Result `{ id, state, turns }` in
the shape of `_lembas/session/history`'s `turns`: its conversation so far, **read-only** — the link
has no method to write into it. A client following it live refetches on the `team` event and draws
the `sub_tool` events meanwhile. `-32602` for an id the session never had.

### `_lembas/team/findings` — client → agent → result (capability `tree`)

Params `{ sessionId, id }`. Result `{ id, findings: [{ n, text, time }] }`, ended subagents' too.

### `_lembas/team/stop` — client → agent → result (capability `tree`)

Params `{ sessionId, id }`: the person stops a subagent and its subtree (`subagents.md` §10), said to
the main agent as a note. Result `{ stopped: [id, …] }`; `-32602` for an id that is not live.

### `_lembas/session/delete` — client → agent → result

Params `{ sessionId }`: the chat was deleted. The session stops if it works, leaves the store (its
attachments too), and a terminal that has it open starts a new one. Result `{ deleted: boolean }`;
`false` when there is no such session (any more), which is done as well.

Which sessions the device deletes: one the client holds
or a terminal shares now, one the client was ever given (made by `session/new`, opened from the
store, or shared from a terminal — the device keeps a mark), and otherwise one whose root is inside
`remote.roots` on a device whose remote work is on. **Trust is not asked**: deleting runs nothing in
the directory. Anything else is `-32602` with the reason. (Not the rule for *opening* a session,
trust included: that one would refuse sessions the client had been showing.)

The client keeps a delete it could not deliver — the device was not linked, did not answer, or
refused — and sends it again when the device next links, until it is answered (LLeMbas keeps it in its
database and gives up after five refusals). A delete is idempotent, so sending it twice
is safe. A session pending deletion is not made into a chat again by an `announce`.

### `_lembas/session/rewind` — client → agent → result (capability `rewind`)

Params `{ sessionId, turnId }`: the client is editing or regenerating from the turn `turnId` (a
client's turn id; `-32602` for one the session does not have, or while the session works). The
device marks that turn's messages and every later one as reverted — they leave the conversation the
model sees and the history, as `/undo` does; a turn already folded into a compaction summary stays
in that summary — and answers `{ reverted: <turns> }`. **Files are not
touched**: what the turns changed on disk stays, and the client says so beside the control. The
client then sends the new prompt as a new turn.

### `_lembas/show` — agent → client → result (capability `show`)

The `show` tool (spec 3.0.0), in a session the client opened, where the client said `show: true`:
the device sends the file, since the client cannot reach the device's disk. Params
`{ sessionId, toolCallId, path, kind: "markdown" | "code" | "text" | "html", language?, line?, text }`
— `path` relative to the session's working directory, `kind` from the file's name (`.md` markdown;
`.html`/`.htm` html; a known source extension code, with `language`; anything else that is text,
text), `text` the content, at most 1 MB (a larger file is refused before sending, and the model is
told). The client shows it in its viewer beside the chat — the web UI, or Desktop's — and HTML only
in a sandboxed frame that reaches neither the app nor the instance. Result `{ shown: true }` or
`{ shown: false, reason }`, which the tool answers the model with.

### `_lembas/session/compact` — client → agent → result

Params `{ sessionId }`. Result `{ summary }`. `-32602` while it works.

### `_lembas/session/title` — client → agent → result

Params `{ sessionId, title }` (at most 200 characters). Result `{ title }`.

### `_lembas/session/announce` — agent → client

A terminal on the device shares its session:
`{ sessionId, cwd, title, model, instance, mode, effort, busy, origin: "terminal" }`. Sent when it is
shared and again whenever the link comes up. `instance`: the session's model is this link's
instance's, as on the `model` event.

### `_lembas/session/left` — agent → client

`{ sessionId, available: boolean }`: the terminal let go of it. `available`: the service can open
it from the store at the next prompt.

### `_lembas/session/deleted` — agent → client

`{ sessionId }`: deleted on the device (`/delete`, `/sessions delete`); delete the chat.

A delete is never dropped for want of a link. One made while no link is up — no
service running, the instance unreachable, or `lembas sessions delete` / `prune` from a shell — is
kept on the device, and every one kept is sent right after the next link's `initialize` (to a
client that said `_meta.lembas`), then forgotten. So the client must expect a `deleted` some time
after the fact, for a session it may never have had a chat for, or has deleted already: either is
ignored. Also sent for the empty session a terminal's resume discards (`-c`, `/sessions`) where the
web UI had been shown it.

### `_lembas/permission/settled` — agent → client

`{ sessionId, toolCallId }`: take the card with this `toolCallId` down — answered elsewhere (at the
terminal), or no longer waited for (the device's time ran out). It covers approvals,
questions and plans alike. Cards are keyed by `toolCallId`, never by session: several can be open
at once, and settling one never hides another.

### `_lembas/question` — agent → client → result (capability `ask`)

The `ask_user` tool, in a session the device holds or a terminal shares, asked of a client that said
`_meta.lembas`. Params:

```json
{ "sessionId": "ses_…", "toolCallId": "call_…", "why": "optional",
  "questions": [ { "header": "DB", "question": "Which database?", "multiple": false,
                   "options": [ { "label": "SQLite", "description": "…", "recommended": true } ] } ],
  "_meta": { "lembas": { "patient": true } } }
```

At most 4 questions of at most 6 options; the recommended option, if any, is first. An option's
`label` is at most 240 characters on the card (LLeMbas shows no more); answered with the cut label,
the model is given the whole one. Result
`{ outcome: "answered", answers: Answer[] }`, one per question in order, or `{ outcome: "dismissed" }`.
`Answer` is `{ kind: "options", labels: string[] }`, `{ kind: "custom", text }` or
`{ kind: "back", text }` (a question of the person's own, which the model answers before asking
again) or `{ kind: "skipped" }` (that question left blank; the model is told it has no answer).
Anything else reads as dismissed.

### `_lembas/plan` — agent → client → result (capability `plan`)

`plan_submit`, likewise. Params `{ sessionId, toolCallId, path, text, plan?, _meta: { lembas: { patient? } } }`
— `path` the plan file written for the record (relative to the project), `text` the plan rendered as
markdown, `plan` the structure (capability `plan_state`; a client without it draws `text`). Result
`{ outcome: "approve", mode: "edit" | "manual" }` (the session continues in that mode, lowered to
`remote.max_mode`; where even `manual` is beyond it, nothing is decided),
`{ outcome: "revise", feedback }`, or `{ outcome: "dismissed" }`.

For both: **first answer wins.** In a terminal's shared session the card is shown at the terminal
and in the web UI at once (`patient: true`); whichever answers first decides, and the other card is
taken down (`_lembas/permission/settled` when the terminal answered). Not `patient`: LLeMbas times
the card out like an approval and answers `dismissed`; the device also stops waiting after
`remote.approval_timeout`, answers the model as dismissed, and sends `settled`. A client that did
not say `_meta.lembas` gets neither request: `ask_user` then tells the model nobody can be asked,
and `plan_submit` saves the plan and tells it to stop.

### `_lembas/files/search` — client → agent → result (capability `files`)

The TUI's `@` picker, for the web composer. Params
`{ sessionId?: string, cwd?: string, query: string, limit?: number }` — one of `sessionId` (the
session's directory) or `cwd` (absolute; the same rules as `session/new`); `limit` 20 by default, at
most 50. Answered from the device's own file index (`project/files.ts`: ripgrep's list, so
gitignored files are left out; fuzzy, nudged by frecency; an empty query gives the recent ones).
Result `{ root, files: [{ path, kind: "file" | "dir" }] }`, `path` relative to `root` and
`/`-separated. `root` is the directory `@path` mentions in that session's prompts are read from —
its working directory (for a terminal's session, the directory the terminal works in, which may be
below the project root the `announce` names), the project root for a session opened there. A chosen file goes into the
composer as `@path` text, which the device expands when the prompt comes.

### `_lembas/commands` — client → agent → result (capability `commands`)

Params `{ sessionId?: string, cwd?: string }`, for a composer that has a directory but no session
yet. Result `{ commands: [same shape as availableCommands] }`.

### `_lembas/terminal/open` — client → agent → result

Only with `remote.terminal: true`. Params `{ cwd?, cols?, rows? }` (`cwd` defaults to the first
root; 20–500 × 5–300). A login shell as the device's user, in a PTY, at most four per link. Result
`{ terminalId, cwd, cols, rows, shell: "bash" | "zsh" | "fish" | "other", integration: boolean }`
(`shell` and `integration` with capability `shell`).

**Shell integration**: for bash, zsh and fish, unless `remote.terminal_integration: false`
(global config), the shell starts with an rc wrapper that sources the user's own startup files first
— as a login shell reads them — and then marks every prompt with OSC 133 (`A` prompt start, `B`
command start, `C` output start, `D;<exit status>` when a command has finished; an empty line ends
no command) and OSC 7 (`file://<host><cwd>`, the path percent-encoded byte by byte). `B` is put back after any hook that
rebuilds the prompt (starship, powerline). bash runs as an interactive shell that reads the login
files itself (`/etc/profile` with PS1 unset, so `/etc/bash.bashrc` is read once), and keeps
`logout` and `~/.bash_logout`; zsh follows a `ZDOTDIR` set in the user's own `.zshenv`, and our
variables are gone before their `.zshrc` runs. Nothing else in the user's environment changes. The
web terminal reads the marks; nothing on the server parses them.

### `_lembas/terminal/input` — client → agent

`{ terminalId, data: <base64> }`: keystrokes.

### `_lembas/terminal/resize` — client → agent

`{ terminalId, cols, rows }`.

### `_lembas/terminal/close` — client → agent

`{ terminalId }`: hung up (SIGHUP). Every terminal closes with the link.

### `_lembas/terminal/output` — agent → client

`{ terminalId, data: <base64> }`.

### `_lembas/terminal/exit` — agent → client

`{ terminalId, code }`: the shell ended.

## The HTTP API the CLI uses (LLeMbas `/v1`, the device token's own user)

Besides `/v1/models`, the chat completions, `/api/v1/instance` and `/mcp`:

- **`GET /v1/models`**: each entry may carry **`kind`** — `"chat"`, `"embedding"`, `"stt"`,
  `"tts"` or `"image"`. Only `chat` (or no `kind`, from an instance that lists chat models only) is
  a model the CLI talks to: its model refs, `/model` and the login's list are those. An `embedding`
  model is the library's: logging in (or in again) sets config.yaml's `embedding:` to the first one
  as `<connection>/<served id>` when no `embedding:` is set, and never replaces one that is;
  `embedding: <served id>` also finds it. Voice uses the instance's own speech endpoints, not these
  entries. Any other kind is ignored.
- **`GET /api/v1/instance`** (the `models` scope) →
  `{ service, version, name, connection, account: { email, name }, scope, default_model, services }`.
  **`scope`** is what the token presented may do, space-separated, as its login granted it
  (`models memory link`). `lembas login --token-stdin --server <url>` takes a token another
  program already holds (LLeMbas Desktop: the `agent_token` of the one approval, App API) on
  stdin, never from an argument or the environment, and proves it here before writing anything:
  discovery must list protocol 3 (else the device login's own refusal), a 401 is a token the
  instance does not accept, a 404 or an answer without `scope` an older server, and a `scope`
  without `link` is refused. Then the login is stored exactly as a device login's, and `--json`
  prints the device login's `done` line: `{ event: "done", fresh: true, connection, base_url,
  version, models, changes }` (or `{ event: "error", message }`).
- **`GET /v1/usage`** →
  `{ month: { from, tokens_in, tokens_out, cost_money: number | null, cost_by_currency: { [currency]: number } }, device: { tokens_in, tokens_out }, currency, admin, unit: "money" }`.
  Counting and money, no credits (spec 3.0.0): `cost_by_currency` is the month's cost in each
  currency the account's use was priced in (a connection prices in its own; `{}` when nothing was
  priced), and two currencies are never added together. `cost_money` is that cost when exactly one
  currency is involved, in `currency`; with several it is `null`. `plan`, `balance`,
  `balance_money`, `month.cost`, `device.cost` and `device.cost_money` are still sent, as `null`,
  and `windows` as `[]`, for a 1.x client; a 3.0.0 client does not read them. The CLI shows the
  month's cost (each currency's amount, `€2.00 · $0.75`) in its status bar when anything was
  priced, and the whole of it under `/usage`. A 404 (an instance without it) shows nothing.
- **`GET /v1/me/personalization`** →
  `{ enabled, available, personality, personality_custom, instructions }`; **`PUT`** the same fields
  (403 when personalization is not `available` **or** the token is not a device's token — the
  response's `detail` says which, and the CLI shows it, else "not allowed"; 422 for an `enabled`
  that is not a boolean or a field that is not a string). Logged in to exactly one instance that has it `available`, the CLI uses
  these in place of its local `personality`, `personality_custom` and `instructions` (none of them
  when `enabled` is false; a preset the CLI does not know is none here), and `/settings` (or
  `lembas config set`) writes a change back with a `PUT` of **only the field changed** and
  `enabled: true` — never the session's copy of the other two, which may be blanked or mapped — so
  the instance keeps what it has saved. LLeMbas must accept a partial `PUT`. Logged out, the local
  keys apply.
- **The library**: logged in to one instance whose token has the `memory` scope (or the older
  `library`), a config that sets no `library:` uses the account's (`lembas`); `library: local`
  written stays local. The device's login asks for `models memory link`.
  **Scope** (capability `library`): on a `tools/call` to `/mcp` made for a session the client opened,
  the device sends `params._meta.lembas.scope: <sessionId>`. The instance then answers within that
  chat's scope — its knowledge bases (`conformance/library_scope.json` for how `base` is read
  against them) and its data group — and refuses (`-32602`) a session id that is not one of this
  device's. Without `scope`, the account's default data group, as before.

- **Memory** (spec 3.0.0): memory records are the instance's, for the device as for a chat. Logged
  in to an instance whose token has the `memory` scope, the CLI's `memory` tool and the memory it
  puts into the prompt are the instance's records, read and written through `/mcp`: the tool
  `memory` (the spec's, `tools/memory.json`), and the resource **`lembas://memory`**, whose text is
  the entries as the memory block of a system prompt shows them (empty: none), which the device
  reads as a session starts and keeps for that session (the prompt does not change under a
  session). Only `memory` is taken from `/mcp`: notes, knowledge bases and skills stay the device's
  own. A call made for a session the client opened carries `params._meta.lembas.scope:
  <sessionId>` (on the tool call and the resource read alike) and lands in that chat's data group;
  a session of the device's own lands in the data group of the session's model. A token with the
  older `library` scope counts as `memory`. Local memory files are not synced up; logged out, there
  is no memory.

- **Schedules** (spec 3.5.0): the schedule routes of `/api/v2` — `GET`/`POST /api/v2/schedules`,
  `GET`/`PATCH`/`DELETE /api/v2/schedules/{id}`, `POST …/{id}/pause`, `/resume`, `/run`,
  `GET …/{id}/runs`, `GET …/{id}/runs/{run}`, `POST /api/v2/schedules/compile` and `/preview` —
  **accept the device's token** (scope `link`) as well as an app's (`app`), for the same person and
  under the same `schedule.use` permission; nothing else of `/api/v2` does. The shapes are the
  server's (its contract fixtures); what the CLI's `/schedule` relies on: a schedule's `id`,
  `title`, `instruction`, `description`, `enabled`, `next_run`, `last_run` (`status`, `timing`,
  `due_at`, `finished_at`, `preview`, `error.text`), `running`; a run's `id`, `status`, `due_at`,
  `started_at`, `finished_at`, `model.name`, `preview`, `error`; compile's `ok`, `reason`, `title`,
  `instruction`, `rule`, `description`, `next_runs`, `timezone`. A run's `status` may also be
  **`unchanged`** (monitor mode: the watch saw nothing new; no model ran), and `waiting_for` may be
  **`device`**. A schedule that runs on a linked computer has
  **`runs_on: { device, device_name, cwd, mode, approval_wait_minutes }`** (else `null`); one that
  watches has **`watch: { kind: "url", url } | { kind: "command", command, timeout_s }`** (else
  `null`). Creating one with the device's token, `runs_on.device: "this"` names the calling device.
  A server without these answers 401 or 403 to the device's token, and the CLI says the server is
  older.

## Notes

- `_lembas/files/search`'s `root` is the session's working directory, which `@path` mentions
  resolve against (for a session opened at the project root, the project root). `/plan` without a
  task is an action, with a task a prompt run in plan mode. Questions and plans also stop waiting
  after the device's `remote.approval_timeout`, like approvals.
- The CLI's login accepts an instance whose discovery lists protocol 3 (*Versions*).
