# Progress

Spec 3.4.0. **One way to say what a session is doing**, the same in every client — the web UI,
Desktop, the CLI, Android — and for every agent in it, the main agent and each subagent alike.
Before 3.4.0 each client invented its own subset from a free `status` text; that text stays, for
compatibility, and beside it goes a structured **progress** object with one of a fixed set of
**phases**. The texts are `loop.json` `progress`; the cases `conformance/progress.json`.

## 1. The object

```json
{ "phase": "running_tool", "since": 1762790400000, "tool": "bash", "what": "bun test" }
```

- **`phase`** — one of the thirteen names below, never another. A session doing nothing has no
  progress: `null`.
- **`since`** — when this phase began, Unix time in **milliseconds** (an integer). A change of the
  phase's data (tokens read, the rate) keeps `since`; a new phase, or the same phase for another
  thing (another tool call, another model), starts a new one.
- The phase's **data**, as listed: every field named here, with these names and these types. A
  field that is not known is left out (never `null`, never a guess), and the text says less.

## 2. The phases

In the order a turn usually meets them. `{{x}}` in a text is the field `x`; the texts are
`loop.json` `progress.texts`, word for word, and they are the shared i18n keys
`progress.<phase>` (and `progress.<phase>.<variant>`, `progress.reason.<reason>`,
`progress.what.<what>`) in every client's translations.

| Phase | Data | Text (`progress.texts`) |
|---|---|---|
| `queued` | `ahead?: int` — turns before it | `Queued` · with `ahead`: `Queued: {{ahead}} ahead` |
| `waiting_for_model` | `model: string` (its display name, else its id); `reason: "busy" \| "slots" \| "group"` | `Waiting for {{model}}: {{reason}}` |
| `loading_model` | `model: string` | `Loading {{model}}` |
| `reading_prompt` | `done: int`, `total: int` — prompt tokens processed of all | `Reading the prompt: {{done}} / {{total}} tokens ({{percent}}%)` · without `total`: `Reading the prompt` |
| `thinking` | — | `Thinking` |
| `writing` | `rate?: number` — tokens a second, as measured over this reply | `Writing: {{rate}} tok/s` · without `rate`: `Writing` |
| `running_tool` | `tool: string` (the tool's name); `what?: string` — the call's short title (the command, the path, the query), one line, at most 80 characters | `Running {{tool}}: {{what}}` · without `what`: `Running {{tool}}` |
| `waiting_for_person` | `what: "approval" \| "question" \| "plan"`; `tool?: string` (approval: the tool asked about) | `Waiting for you: {{what}}` |
| `waiting_for_subagents` | `running: int` — subagents working or waiting now | `Waiting for subagents: {{running}} running` |
| `compacting` | — | `Compacting the conversation` |
| `retrying` | `reason: string` — the server's error, one line; `in: int` — seconds until the retry, counted down by the client from `since`; `attempt?: int` | `Retrying in {{in}} s: {{reason}}` |
| `stopped` | `reason?: "person" \| "steps" \| "budget"` | `Stopped` · with `reason`: `Stopped: {{reason}}` |
| `failed` | `reason: string` — the error, one line | `Failed: {{reason}}` |

The fixed words a field stands for:

- `reason` of `waiting_for_model` (`progress.reason`): `busy` — *it serves one request at a time
  and is answering another*; `slots` — *all its slots are in use*; `group` — *its server holds
  another model now*.
- `what` of `waiting_for_person` (`progress.what`): `approval` — *allow {{tool}}?* (*an approval*
  without `tool`); `question` — *a question*; `plan` — *the plan*.
- `reason` of `stopped` (`progress.stopped_reason`): `person` — *by you*; `steps` — *step limit*;
  `budget` — *budget*.
- `percent` is `floor(done × 100 / total)`; numbers are written as integers, `rate` with one
  decimal (`12.5`), in the client's own number format.

## 3. The line a client draws

**`{{text}} · {{elapsed}}`** (`progress.line`) for every phase but `stopped` and `failed`, which
draw their text alone. `elapsed` is now − `since`, written in whole seconds as a ticking clock writes durations
(`5s`, `1m 05s`, `1h 02m`), and kept moving by the client at least once a second while the
phase lasts. `retrying`'s `in` is likewise counted down from `since` (never below 0).

Every client draws the same line for the same object — the same words, from the same keys — and
its own look around it (a spinner, a colour, a bar for `reading_prompt`). Where a line has no room
(a phone's notification, a tree row) the text alone, without `· elapsed`, is enough; nothing else
is shortened.

## 4. When each phase holds

What decides the phase, so that two implementations agree on the same moment:

- `queued` — a prompt waits behind a turn that runs (the device's turn queue; the server's per
  chat). `waiting_for_model` — the turn may start but its model may not take it yet: a one-request
  server answering another request (`busy`), every slot of the model held (`slots`; `team.md` §8),
  another model of its one-model server in use (`group`). `loading_model` — only where the
  implementation is told the model is being loaded (the server sees it; a device that cannot tell
  skips it).
- `reading_prompt` — the request is sent and no output has come; with `done`/`total` from the
  server's prompt-progress frames (`quirks.json` `prompt-progress`), without them where none come.
- `thinking` — reasoning streams; `writing` — reply text (or a tool call's arguments) streams.
- `running_tool` — from a tool call's start to its end; with calls in parallel, the one that
  started first. `waiting_for_person` — an approval card, a question or a plan to approve is open
  for this agent. `waiting_for_subagents` — the agent's turn waits on subagents: a `task` call in
  the foreground, or the main agent with nothing to do but its subagents' reports (`subagents.md`
  §9).
- `compacting` — a compaction runs. `retrying` — a failed request will be sent again after a wait.
- `stopped` / `failed` — the turn ended other than by finishing: stopped by the person, a step limit
  or a budget; failed with an error. They are the last progress of that turn and stay until the
  next one begins; a turn that finished normally ends with `null`.

## 5. Where it is sent

- **`/api/v2` (LLeMbas)**: every frame of a chat's stream that carries the `status` text carries
  `progress` beside it — the object, or `null` — and a frame is sent when the progress changes:
  a new phase at once, a data-only change (`done`, `rate`) at most four times a second. `status`
  stays as it was, its text unchanged, for clients that do not read `progress`.
- **The link (`acp.md`, capability `progress`)**: the `phase` event `{ progress, agent? }` —
  `agent` the subagent's id (`subagents.md` §5) when the progress is a subagent's, absent for the
  main agent's — under the same pacing; `progress` in `_lembas/session/status`, so a client
  reattaching draws it at once. The device sends it; the server passes it on in `/api/v2` as its
  own.
- **The subagent tree** (`subagents.md` §12): every agent carries its current `progress`.

A client that reads `progress` draws it and ignores `status`; one that does not, draws `status` as
before.
