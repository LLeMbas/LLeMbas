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
  **2**. CLI `src/acp/agent.ts:LEMBAS_PROTOCOL`, LLeMbas
  `services/device_link.py:PROTOCOL`.
- **Discovery** (`/.well-known/lembas.json`) says `protocol: 1` **and** `protocols: [1, 2]` —
  `protocol` stays 1 on purpose, because a client that compares it for equality at login must
  still get in. The CLI reads `protocols` (falling back to `protocol` where it is absent) at
  login and again **every time the link dials**, keeps it with the login, and says protocol 2 in
  `hello` and `initialize` only to an instance that lists 2; to any other it says 1 (an instance that
  speaks only 1 closes a link whose hello says 2 with **4400**). A link closed with 4400 stops — it is not
  redialled — saying *the instance and this CLI speak different link protocols — update one of them*.
- **A protocol 1 peer keeps working.** Every feature beyond protocol 1 is named by a **capability
  string** in `initialize`'s `_meta.lembas.capabilities`, and **LLeMbas never calls a method, nor
  relies on a field, the device did not list**. The CLI logs in to an instance whose discovery says
  protocol 1 or 2.
- A client says it is LLeMbas with `clientCapabilities._meta.lembas` in `initialize`, and which
  protocol it speaks with `_meta.lembas.protocol` (absent: 1 — a protocol 1 client sends `lembas: true`).
  Only such a client gets `_lembas/event`; only one that says protocol **2 or later** gets
  `_lembas/question` and `_lembas/plan`, and one that then answers them with method-not-found is
  treated as protocol 1 for the rest of the link.

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
| `library` | `library` in `session/new` and `session/prompt` `_meta.lembas` is read; `_meta.lembas.scope` sent on the instance library's `tools/call` |
| `prototypes` | `prototypes` in `session/new` and `session/prompt` `_meta.lembas` is read |
| `plan_state` | the structured plan: `_meta.lembas.plan` on the `plan` session update and on `_lembas/plan`; `plan` in `_lembas/session/status` |
| `rewind` | `_lembas/session/rewind` |
| `jobs` | background work (spec 2.0.0): the `job` and `job_note` events; `_lembas/jobs`, `_lembas/job/output`, `_lembas/job/stop`, `_lembas/job/send`, `_lembas/job/transcript`; `jobs` in `_lembas/session/status`; `job` on `session/request_permission`; `machine` on a `prompt` event |

## Standard ACP, as used here

### `initialize` — client → agent

Params: `{ protocolVersion: 1, clientCapabilities: { _meta?: { lembas?: { protocol: 2 } | true } } }`.

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
  "agentInfo": { "name": "LLeMbas CLI", "version": "1.3.0" },
  "_meta": { "lembas": {
    "protocol": 2,
    "harness_spec": "1.1.0",
    "limits": { "roots": ["~/work"], "max_mode": "edit" },
    "connection": "example",
    "capabilities": ["directories", "configure", "…"]
  } }
}
```

`limits` is `null` on `serve --stdio`. `connection` is the device's name for the instance — the
login's connection — or `null`; an instance that does not speak protocol 2 recognises its own
models by it, as `<connection>/<name>`, while the `model` event and `announce` say it directly
(`instance`).

**How the device names an instance's models.** An instance that speaks protocol 2 serves every model
as `<provider>/<model>` (`deepseek/deepseek-flash`), and that served id **is the device's ref** for
it — each provider is, as the person sees it, a connection of its own, backed by the instance
(requests still go to its `/v1`, with the served id). Exceptions: a connection of the device's own
with the provider's name keeps the short form, and the instance's model is then
`<login>/<provider>/<model>`; with two instances serving one provider, the one logged in to first
keeps the short form and the other's are `<login>/<provider>/<model>`; an instance that does not
speak protocol 2 serves bare ids, which stay `<login>/<id>`. The provider is the model's `provider` field where the
instance gives one, else — only from an instance whose discovery lists protocol 2 — the part of the
id before its first `/`; an id with a `/` from any other instance is just an id (`<login>/<id>`). The old forms (`<login>/<model>`,
`<login>/<provider>/<model>`) still name the same model. `lembas_model` in `session/new` and
`model` in `_lembas/configure` are the instance's served name, as it serves it.

### `session/new` — client → agent

Params: `{ cwd: <absolute>, mcpServers?: [], _meta?: { mode?, lembas_model?, model?, effort?, lembas?: Context } }`.
`cwd` must be a directory inside `remote.roots`, in a trusted project when `remote.require_trust`
(the default) — otherwise `-32602` with the reason. `mode` is lowered to `remote.max_mode`.
`lembas_model` is the instance's name of a model (it becomes `<connection>/<name>` here); `model`
a ref of the device's own; `effort` a level or `"off"`.

Result: `{ sessionId: "ses_…", modes: { currentModeId, availableModes }, _meta: { lembas: { model, mode, effort } } }`.
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
| `prototypes: { offered: boolean, sources: string }` | `prototypes` | whether the model may write a prototype (`blocks/prototypes.md`), and the sentence saying what a page may load (a device offering them on its own, with no instance, sends nothing and the bullet drops) |

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
`available_commands_update` (capability `commands`):

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
and `deny` (reject_once), and `_meta.lembas: { reason, purpose?, always?, patient?, job? }`. `patient`: a
person may also answer at the terminal (a shared session), so the client does not time it out.
`job` (capability `jobs`): `{ id, agent, description }` when a subagent running as a background job asks, so the card
can say which — a background subagent may ask while the session itself is idle.
Answer: ACP's `{ outcome: { outcome: "selected", optionId } | { outcome: "cancelled" } }`, with
optional `_meta.lembas.command` (the corrected command line, `once` only) or
`_meta.lembas.feedback` (why it was denied). No answer within `remote.approval_timeout` is a denial.

## `_lembas/*` methods

Direction is who sends it. "→ result" marks a request; the others are notifications.

### `_lembas/hello` — agent → client

Sent once when the link opens, before `initialize`:
`{ protocol, version, harness_spec, device: { host, platform }, limits: { roots, max_mode, require_trust } }`.

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
`steered`, `task`, `notice`, `retract`, `plan`, `compacted`, `sub_tool`, `job`, `job_note`, `mode`, `model`, `flash`,
`setting`, `error`, `done` (`src/bus/index.ts`). Those this protocol relies on:

| Event | Fields |
|---|---|
| `prompt` | `text` (as the person sees it: a command's name, not its body), `turnId`, `attachments?: [{ name, mimeType, size }]`, `machine?: true` (capability `jobs`) — the turn was started by the harness, not typed: a finished job's or subagent's note, or a subagent's message, beginning with its mark (`loop.json` `jobs.marks`); a client shows it as such and sends it to no model of its own |
| `task` | `state: "start" \| "end"`, `turnId` — every turn has one: the client's, or one made here for a turn typed in a terminal |
| `steered` | `texts`, `ids?` — the `_lembas/steer` message ids taken in at this step |
| `mode` | `mode` |
| `model` | `ref` (the device's ref, above), `effort` (a level or `"off"`), `connection` (the device connection it is spoken to through) and **`instance: boolean`** — true exactly when that connection is the login of **this link's** instance, i.e. the model is one the instance serves (its served id is `ref`, or `ref` less a leading `<connection>/`). Whenever either changes, for any reason: `/model`, `/effort`, the settings tool, a fallback, `_lembas/configure` |
| `plan` | `plan` — the structure, or `null` when cleared (what the `plan` session update carries) |
| `compacted` | `summary` |
| `title` | `id`, `title` |
| `job` | `state: "start" \| "update" \| "end"`, `job: JobInfo` (capability `jobs`) — a background job of the session began, has news (new output, a subagent's step; at most four a second per job), or ended. `JobInfo = { id, kind: "command" \| "subagent", state: "running" \| "done" \| "stopped" \| "error" \| "lost", description, command?, agent?, model?, started, ended?, exit?, unread, steps?, tools?, ownerJobId? }` — `description` what is shown for it (a command's `description` or its first line; a subagent's description), `unread` characters of output the model has not read, `exit` a command's exit code (null: a signal), `ownerJobId` set on a job a subagent started, `lost` a job that was running when the device's process ended |
| `job_note` | `jobId`, `text` (capability `jobs`) — the note about that job was given to the model: inside a turn at the next step, or as the `machine` turn whose `prompt` follows |
| `sub_tool` | `callId` (the `task` call the subagent belongs to), `jobId?` (its job, when it runs as one), `name`, `title`, `isError?` — a tool call of a subagent finished; a client draws it under the task line, or in the job's panel |

### `_lembas/directories` — client → agent → result

Params `{ path?: string }`. Without a path: `{ path: "", places: Dir[] }`, the remote roots and every
trusted directory inside them. With one (absolute, inside the roots): `{ …Dir, parent, entries: Dir[], error? }`.
`Dir = { path, name, accepted: boolean, reason? }` — whether `session/new` would accept it.
Only on the link (an error on `serve --stdio`).

### `_lembas/configure` — client → agent → result

Params `{ sessionId, model?: <instance's name>, effort?: <level> | "off" }` between prompts. Result
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
`{ sessionId, exists, held: "here" | "terminal" | "none", busy, title, turnId?, queued: string[], lastTurnId?, plan?, jobs? }`
— `plan` the session's plan or `null` (capability `plan_state`), so a client reattaching draws it
without waiting for the next change; `jobs` the session's background jobs, `JobInfo[]` running first (capability `jobs`), for the same reason; the turn fields with capability `turns`: the turn running now, the ones waiting, the last
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
- **`GET /v1/usage`** →
  `{ plan: { name, credits_per_month } | null, balance: number | null, month: { from, tokens_in, tokens_out, cost: number | null }, device: { tokens_in, tokens_out, cost: number | null }, unit: "credits" }`.
  The CLI shows the balance in its status bar when `plan` is not null, and the whole of it under
  `/usage`. A 404 (an instance without it) shows nothing.

  Also read, every one optional (an instance without them gets credits alone):
  `currency` (`"EUR"` | `"USD"` | `"GBP"`), `credit_value` (money per credit), `admin` (boolean:
  no limit, never refused), `balance_money`, `month.cost_money` and `device.cost_money` (numbers or
  `null`), and `windows: [{ window, limit, spent, frees_at }]` (rolling spending windows: `window`
  names it, `limit` and `spent` in credits, `frees_at` an ISO time or `null`). Credits are shown
  with up to two decimals below 1,000 and whole above; beside each, its money in `currency` where
  given (`€1.23`, `$1.23`, `£1.23`) — `*_money` when sent, else the credits times `credit_value`.
  `admin: true` shows `unlimited` in the status bar whatever the plan, and `/usage` says so, still
  listing the month's and the device's spend; its windows are not listed. Otherwise `/usage` lists
  each window's spend, limit and when it frees.
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
- **The library**: logged in to one instance whose token has the `library` scope, a config that sets
  no `library:` uses the account's (`lembas`); `library: local` written stays local.
  **Scope** (capability `library`): on a `tools/call` to `/mcp` made for a session the client opened,
  the device sends `params._meta.lembas.scope: <sessionId>`. The instance then answers within that
  chat's scope — its knowledge bases (`conformance/library_scope.json` for how `base` is read
  against them) and its data group — and refuses (`-32602`) a session id that is not one of this
  device's. Without `scope`, the account's default data group, as before.

## Notes

- `_lembas/files/search`'s `root` is the session's working directory, which `@path` mentions
  resolve against (for a session opened at the project root, the project root). `/plan` without a
  task is an action, with a task a prompt run in plan mode. Questions and plans also stop waiting
  after the device's `remote.approval_timeout`, like approvals.
- The CLI's login accepts an instance whose discovery says protocol 1 or 2.
