# The harness spec

LLeMbas CLI (a terminal coding agent, TypeScript) and LLeMbas (a
self-hosted web UI, Python) are two independent projects that share **one harness design**: the
same tools, the same permission design, the same prompt texts, the same model quirks and model
metadata. This directory writes that design down as data. Each project implements it in its own
language; neither ever runs the other. LLeMbas CLI embeds these files at build time; LLeMbas vendors a pinned, hash-checked copy.

The concept-by-concept map of the two implementations, and where they still differ, is the wiki
page *Harness-parity* in LLeMbas CLI's wiki.

## What is here

| Path | What |
|---|---|
| `VERSION` | The spec's own version, apart from LLeMbas CLI's |
| `purpose.json` | The `purpose` argument added to a tool whose calls say what they are for |
| `tools/<name>.json` | One tool each: name, `scope`, `sessions`, `risk`, `exclusive`, `block` (how a call is shown: `shell`, `edit`, `read`, `search`, `web`, `task`, `plan` — not drawn, the plan card refreshes — `hidden`, `tool`), `purpose`, former names (`aliases`, per project), renamed arguments, `variables`, what a project offers of the parameters (`narrow`, per project), `offered` (`subagent`: given to subagents only, never to the main agent — `report`), `requires` and `unavailable` (when it can be offered, and what a call answers when that has gone), `confirm` (`always`: the person is asked in every mode), the description and the JSON Schema of its parameters — exactly what a model is sent |
| `prompts/` | The prompt texts; `prompts/index.json` says which are shared, which LLeMbas fragment each one is, and what each `{{variable}}` in a text means. `prompts/personality/` holds the personality presets both offer |
| `permission/hardline.json` | The floor: commands refused in every mode, protected paths, and how a command is spelled plainly before it is checked again |
| `permission/arity.json` | How much of a command an "always allow" covers |
| `permission/defaults.json` | The rules every session starts from |
| `permission/modes.json` | The four modes — manual, edit, auto, plan — as what each does with each risk class |
| `models/schema.json` | Model metadata: context, output limit, efforts, vision, tools, notes, capacity |
| `loop.json` | How one prompt runs: the step backstop, budgets, wrap-up, nudges, parallel calls, capacity; `jobs` — background work, its marks and limits; `context` — result ids, stubs, the output cap, the compaction table |
| `filesystem.md` | The file system a session works in: paths, node kinds, the four access levels, errors, listings, search and indexing by tokens, stacked instruction files, skills, memory |
| `context.md` | Context hygiene: what happens to a tool result when it is inserted, and never afterwards |
| `quirks.json` | What real servers do, and how both harnesses cope |
| `conformance/<area>.json` | Cases with their expected results, run by both test suites |
| `acp.md` | The link between them: ACP plus every `_lembas/*` method, field and capability |

## Vocabulary

- **Risk classes:** `read`, `write`, `execute`, `interact`. **Reading and writing are never one
  tool** among the shared ones, so a read-only helper is given exactly the readers.
- **Modes:** `manual`, `edit`, `auto`, `plan` (`unrestricted` is read as `auto`).
- **Scopes:** `shared` — both projects implement it; `cli` or `llembas` — only that one. The same
  two names key what is per project elsewhere: a tool's `aliases`, `loop.json`'s budgets, a
  protected path's `scope`.
- **Sessions** (a tool's `sessions`, spec 3.0.0): where a tool is offered — `chat`, a conversation
  on the server, working in the server's file system (`filesystem.md`); `agent`, a session on a
  device (the CLI's own, Desktop's agent — which is the CLI — or an agent chat the web UI runs on a
  linked device), working on that machine's disk; `both`. The two say different things: `read` is
  `shared` and `both` (each project implements it for its own world); `bash` is `cli` and `agent`;
  `mkdir` is `llembas` and `chat`.
- **Requires** (spec 3.0.0): what must hold for a tool to be offered at all — `page`: a browser
  page of the chat is open and attached (not a schedule, not a background job); `display`: the
  session is shown somewhere a file can be opened (the web UI, Desktop; not a bare terminal);
  `forge`: the session's view holds a repository mounted from a forge; `plan`: the session is in
  plan mode. A requirement that goes away between the offer and the call makes the call answer the
  tool's `unavailable` text, word for word, as its result. **From 3.1.0 a requirement that can come
  and go during a session does not take the tool out of the list** (`context.md` §10, rule 4): the
  tool is offered from the start and answers `unavailable` while the requirement is not met.
- **Confirm** (spec 3.0.0): `always` — the call is put to the person before it runs, in every mode,
  auto included, and runs on a yes only; nothing remembers the answer (`permission/modes.json`).
- A tool's other names, per project, are listed in its `aliases` and work as the tool. A former
  name may have taken different arguments; the project converts them. The one such case:
  **`todo` → `plan_write`**, `todos[]` becoming one phase called "Work" (`pending` → `todo`,
  `in_progress` → `doing`, `completed` → `done`, `cancelled` → `dropped`; `conformance/plan.json`).
- **The plan** (`plan_write`, `plan_update`, `plan_submit`) is one structure both keep per session —
  title, summary, findings, objectives, phases of tasks with a status each, ids given by the
  implementation and never by the model — shown as a card in every UI. From 3.1.0 it is in the
  system prompt (`{{plan_context}}`) only as it was when the prompt was made; the model sees it change
  in its own `plan_write` / `plan_update` results, and a change by the person comes as a note
  (`context.md` §10). All three are offered in every mode; `plan_submit` (`requires: plan`) is the
  approval in plan mode and answers its `unavailable` text outside it. Their permission key is
  `plan` (`permission/defaults.json`), which a rule written for the former `todo` key still reaches.

## 3.1.0

Written ahead of both implementations as `3.1.0-draft`, and `3.1.0` once both carried it (LLeMbas CLI 8e06590, LLeMbas 48e905f). **The prefix stays still** (`context.md` §10). Found on 2026-11-05 by measuring a local model's
prompt cache: a harness that rebuilds its system prompt for every request re-reads the whole session
whenever any part of it changes — the date at midnight, a permission mode, a memory, the plan. So:
the system prompt is made once per session and resent byte for byte; no clock is in it; what
changes during a session comes as a `<session-note>` on the next user message (`loop.json`
`context.note`); the tool list is fixed for the session (`requires` that can come and go no longer
remove a tool — `plan_submit` gains `requires: plan` and an `unavailable` text); compaction asks
for its summary as the next turn of the same conversation instead of a separate request with a
pasted transcript (§7); every request records its cached input tokens.

## 3.0.0

Written ahead of both implementations as `3.0.0-draft`, and `3.0.0` once both carried it (LLeMbas
CLI 2.0.0-beta.1, LLeMbas 2.0.0-beta.1). **Two worlds, one harness**: a chat works in the server's file
system, an agent session on its device's disk, and neither sees the other's files; what is shared is
this design.

- **`sessions`, `requires`, `unavailable`, `confirm`** — new fields on a tool (Vocabulary). Every
  tool says its `sessions`.
- **New tools.** `mkdir`, `mv`, `rm` (to the trash) and `search` (meaning and words over the files
  this session can see, filtered by its view after scoring) — LLeMbas, chat. `show` (a file in the
  user's viewer: Markdown, code, text, HTML in a sandboxed frame) — shared, `requires: display`: a
  chat in the web UI, an agent session through the link to the web UI or Desktop (`acp.md`
  `_lembas/show`), never a bare terminal. `python` (Pyodide in the user's browser) — LLeMbas, chat,
  `requires: page`; with no page it answers *Python cannot run now: no open browser page is attached
  to this chat.* The forge verbs `commit`, `branch`, `pr`, `merge`, `tag`, `release` — LLeMbas,
  chat, `requires: forge`; `merge`, `tag` and `release` `confirm: always`. `recall` — shared, both
  (`context.md`). An agent session keeps git through `bash`, as before.
- **The file tools are shared.** `read`, `write`, `edit`, `glob`, `grep` and `list` are `shared` and
  offered in both: on the device's disk, and over the server's tree in a chat. `multiedit`,
  `apply_patch` and `bash` stay the agent's. A chat's listing marks a mount and a read-only entry
  (`filesystem.md`, told to the model in `blocks/workspace.md`). `skills_list`, `skill_view` and `skill_manage` work over skill files on both
  sides; `memory` is the same tool on both, and its records are the server's for both
  (`filesystem.md`, *Memory*; `acp.md`, *The HTTP API*). `session_search` is shared.
- **Removed from chat** (LLeMbas): `knowledge_search`, `knowledge_get`, `notes_search`,
  `note_view`, `note_manage` — now `cli` and `agent` only: the CLI keeps its local library — and
  LLeMbas's own `report_*`, `scratch_write`, the prototype tools and the canvas, which were never
  in the spec. Notes, reports and knowledge become files; prototypes become `show`.
  `blocks/library.md` and `blocks/prototypes.md` are the CLI's alone.
- **`filesystem.md`** — the file system as a contract: paths, the kinds `dir`, `file`, `mount`, the
  levels `none`, `ro`, `stage`, `commit`, grants and floors, the exact error texts, and the rule
  that a granted path under one that is not is reached only by its full path, its ancestor denied
  for reading and writing and never listed. Paths absolute in every answer but a result's bracket;
  one `No such file or directory` for every tool, the `write`/`edit` refusals' texts, the `list`
  tree; where skills are found and in what order, and the name rule
  (`^[a-z0-9][a-z0-9-]{0,63}$`). Search results and **indexing by tokens** (at most 480
  tokens a piece, 64 repeated; a piece the embedder refuses is split, never dropped).
  **Instruction files stack** from the boundary down to the start directory, outermost first, one
  per directory (`AGENTS.md`, else `CLAUDE.md`), none from a directory the session cannot read —
  the CLI took only the nearest one before. `blocks/workspace.md` (LLeMbas) tells a chat's model its
  view.
- **`context.md`** — context hygiene, insert-time only, nothing sent ever rewritten: every result
  has an id (`#0001`: its number in the session, in hex, at least 4 digits); a read carries its
  lines and content hash, under the path as the call gave it; an unchanged re-read, or a range
  inside an earlier unchanged read, is a stub — naming only a result in the request being built; output over 400 lines or 40,000 characters keeps its
  head and tail with `[N lines elided, full output #id]`; `recall` shows any stored result; edits
  answer with their diff; the same listing or search with the same result is a stub; compaction
  adds a table of the reads it covers (those since the previous compaction, in turns still in the
  history) and empties the dedupe table; earlier reasoning is left to the chat
  template. `loop.json` `context` holds the texts and numbers, `blocks/context.md` tells the model.
- **`acp.md`**: **link protocol 3** — LLeMbas 2.0 and LLeMbas CLI 2.0 speak it alone, and a 1.x peer
  and a 2.0 one refuse each other, each saying what to update (discovery's `protocol` is raised to 3
  on purpose); the served id is the device's ref for a model (one server, no connection prefix);
  `session/new`'s result carries `instance`; a session the client opened runs on the
  chat's model and no other — refused rather than answered on the device's own, so the "answered on
  its own model" notice is gone; capability `show` with `_lembas/show`; memory through the
  instance; a 3.0.0 instance sends neither `library` nor `prototypes`. `GET /v1/usage` counts
  and prices without credits: the month's cost per currency (`month.cost_by_currency`), never
  summed across currencies; the credit fields are null. The device asks for the `memory` scope.
- **Permissions**: default rules for the new tools (`search`, `recall`, `show`, `python` allowed;
  the forge verbs asked); `modes.json` `confirm`.
- **Conformance**: `fs_access`, `instructions`, `context`, `chunk_tokens` — new, their answers
  written from a reference model of the rules, not recorded from either implementation (neither has
  them yet). `chunk` stays, noted as the character rule.

## 2.0.0

Background work is one thing in both projects: a **job**, owned by a session, is a command run in
the background (bash `background: true`, or one still running when its `timeout` passed — it is no
longer killed) or a subagent run in the background (task `background: true`, new). One id sequence
per session; `job_list`, `job_output`, `job_stop` (renamed from `bash_list`, `bash_output`,
`bash_kill` — the CLI's former names, read for one minor) now cover both kinds and are shared;
`job_send` (new, shared) messages a running subagent or starts a finished one again with its
context; `report` (new, shared, `offered: subagent`) lets a subagent message the main agent before
its final reply. A job that ends wakes the session that owns it with a marked message
(`loop.json` `jobs.marks`, `conformance/jobs.json`): at the next step while a prompt runs, else as
a turn of its own marked `machine`. The subagent's preamble and the two built-in agents' texts are
prompt texts now (`blocks/subagent.md`, `agents/*.md`). `acp.md`: capability `jobs` — the `job` and
`job_note` events, `_lembas/jobs`, `_lembas/job/output|stop|send|transcript`, `jobs` in `status`,
`job` on an approval, `machine` on a `prompt`. The capacity rule counts the subagents already
running.

## 1.2.0

The library's tools fit both projects: `memory`, `notes_search`, `note_view`, `note_manage`,
`skills_list`, `skill_view` and `skill_manage` say the CLI's facts (files, projects, supporting files,
sessions) through `variables`, so LLeMbas sends the same tools with its own facts; filled with the
CLI's values, every description is word for word what 1.1.0 said. Tool descriptions are filled by
the prompt texts' rules, including the descriptions inside the parameters. A tool's `narrow` says
what a project offers of its parameters where it offers less (LLeMbas: one memory target, no note
scope, no skill categories, supporting files or project skills). Note and knowledge-document ids are
strings on both sides. Three conformance areas: `fill`, `narrow`, `memory` (one change or a batch,
applied together, against the size limit).

## 1.1.0

Planning as one shared structure: `plan_write` and `plan_update` (new, shared, in every mode) and
`plan_submit` now shared; `todo` folded into `plan_write` as a former name. The shared system text
(`system/default.md`) is now one text for both projects, with variables for what differs;
`modes/*.md` shared; `blocks/prototypes.md` and `blocks/library.md` new and shared. `acp.md`: the
client hands the device what only it knows (`context`, `library`, `prototypes`), the plan travels
as a structure (`plan_state`), the device scopes library calls to the chat (`_meta.lembas.scope`),
and a client may rewind a session (`rewind`). `loop.json` says its numbers as numbers. Four
conformance areas: `plan`, `prototype`, `library_scope`, `toolcall_index`.

## 1.0.1

`acp.md`: a device links even with remote work off or no roots, saying `limits.roots: []` and
refusing every session, directory and terminal; and one whose limits change dials the link again so
the client hears the new ones. No change for a client: it already takes empty roots and a link that
drops and comes back.

## 1.0.0

The spec as described here: the shared tools with their scopes, risk classes, call blocks and
aliases; the prompt texts and the six personality presets; the permission floor, arity table,
default rules and the four modes; model metadata; the loop; the quirks of real servers; the
conformance cases; and `acp.md`, the link between the two projects (`_lembas` protocol 2, every
feature beyond protocol 1 gated on its capability string).

## Variables in a description, and in a prompt text

A description may hold `{{name}}` where the two projects differ in a fact, not in design — a
timeout's default, whether commands may run in the background, which subagents exist, where a
skill's supporting files are. The tool's `variables` says what each one means; each project fills
them before sending the description — and the `description` of every parameter, which may hold
them too — and none may reach a model unfilled.

A prompt text may hold them too, declared in `prompts/index.json` under its entry. The same rules
for both, and one more: **a line that is nothing but a bullet and a variable (`- {{name}}`)
disappears when the variable is empty**, so one text serves a project that has the fact and one that
does not (`conformance/fill.json`).

## Offering less of a shared tool

A project that does not have part of a shared tool says so in the tool's `narrow.<project>` rather
than in a different tool: `drop` leaves out optional properties, `enum` keeps some of a property's
values (`a.b` is b inside a; `a[].b` is b inside the items of the array a). It offers a subset and
nothing else — never a property the spec does not have, never another type — so a call valid for the
narrower tool is valid for the whole one (`conformance/narrow.json`). The description says the
narrower tool's facts through its `variables`.

## Changing it

1. Change the spec first, and bump `VERSION`: **patch** for a text or a new conformance case,
   **minor** for a new tool, rule or field, **major** for a rename or a removed tool — removed
   from either project. A spec written ahead of its implementations carries a pre-release suffix
   (`3.0.0-draft`) until both have it. A tool folded
   into another, its name kept as an alias that still runs, is minor: nothing stored or trained
   breaks.
2. A fix comes with a **conformance case** that fails without it. In LLeMbas CLI,
   `bun run harness record` fills in the expected result of a new case from the implementation,
   and `bun run harness record --all` shows every case whose result would change.
3. Implement it in both projects — in the same session, or with an open item recorded against the
   other. A bug found in one is searched for in the other.
4. Nothing here names a host, a path on a server or anybody's setup: both projects are public.

Licence: MIT, as LLeMbas CLI. Files that came from elsewhere say so in their `source` field
(Hermes Agent's hardline patterns, OpenCode's arity table) or in an HTML comment (prompt texts).
