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
| `tools/<name>.json` | One tool each: name, `scope`, `sessions`, `risk`, `exclusive`, `block` (how a call is shown: `shell`, `edit`, `read`, `search`, `web`, `task`, `plan` — not drawn, the plan card refreshes — `hidden`, `tool`), `purpose`, former names (`aliases`, per project), renamed arguments, `variables`, what a project offers of the parameters (`narrow`, per project), `offered` (`subagent`: given to subagents only, never to the main agent — `report`, `team_finish`, `team_request`; `main`, spec 3.4.0: given to the main agent only, never to a subagent — `team_add`, `team_assign`, `team_replace`, `team_stop`, `team_decide`), `requires` and `unavailable` (when it can be offered, and what a call answers when that has gone), `texts` (3.7.0: a tool's other fixed answers by key — `python`'s `no_answer` and `timeout`), `confirm` (`always`: the person is asked in every mode), the description and the JSON Schema of its parameters — exactly what a model is sent |
| `prompts/` | The prompt texts; `prompts/index.json` says which are shared, which LLeMbas fragment each one is, and what each `{{variable}}` in a text means. `prompts/personality/` holds the personality presets both offer |
| `permission/hardline.json` | The floor: commands refused in every mode, protected paths, and how a command is spelled plainly before it is checked again |
| `permission/arity.json` | How much of a command an "always allow" covers |
| `permission/defaults.json` | The rules every session starts from |
| `permission/modes.json` | The four modes — manual, edit, auto, plan — as what each does with each risk class |
| `models/schema.json` | Model metadata: context, output limit, efforts and the thinking switch, vision, tools, notes, capacity (`parallel`); `levels` — the effort levels a chat offers and what each sends |
| `loop.json` | How one prompt runs: the step backstop, budgets, wrap-up, nudges, parallel calls, capacity; `jobs` — background work, its marks and limits; `subagents` — the model refusals, what names a model, the setting's label; `team` — a team's size, names, marks, refusals and texts; `progress` — the phases' texts; `context` — result ids, stubs, the output cap, the compaction table, the environment lines, the session notes; `scheduled` (3.5.0) — scheduled work on a linked computer: the setting, the modes, the approval wait, the sentinel, the watch command's limits and the refusals; `phone` (3.6.0) — phone actions: the capability, each action's fields, what is required and checked, the wait and the texts; `display` (3.7.0) — a call's title keys, states and facts, the blocks, the cards' actions; `scheduled.notify` (3.8.0) — notify-when and its texts; `pages` (3.8.0) — the fence word, the frame's variables, the note and the projection |
| `filesystem.md` | The file system a session works in: paths, node kinds, the four access levels, errors, listings, search and indexing by tokens, stacked instruction files, skills, memory |
| `context.md` | Context hygiene: what happens to a tool result when it is inserted, and never afterwards; the prefix that stays still; reasoning sent back during its turn; no absolute paths in the prompt |
| `team.md` | Teams: a lead and the members it adds, the size setting, the room, who receives what, reports, a manager's refusals, capacity, stopping |
| `subagents.md` | Subagents: the word, which model one runs on (the session's, or one the person named), the capacity refusals, and when the Subagents setting is offered; from 3.4.0 (§5–§13) the main agent and its tree — only it adds, permissions inherited and never widened, request options, a subagent's life and its findings, what clients show |
| `phone.md` | Phone actions (3.6.0): `phone_action`, carried out by the LLeMbas app on the person's phone and confirmed there — who offers it, how a call is checked, the answer, navigation |
| `progress.md` | Progress (3.4.0): the thirteen phases every client shows the same way, the data each carries, their texts, and where they are sent |
| `reports.md` | Reports (3.8.0): a schedule's run files a report with `report_write`, or leaves a history line; notify-when (`always`, `when`, `never`) and the instruction for each; what a run leaves; the retired `[SILENT]`; a run on a linked computer |
| `pages.md` | HTML pages in answers (3.8.0, experimental): the `html-view` fence, what the model is told, the note and the text projection for a reader that cannot show a page, when pages are offered, the session note, the frame |
| `permission/rules.md` | Which permission rule decides (3.8.0): the layers, the candidate, the floors a later-made rule gives way to, and what making a rule does |
| `display.md` | Display (3.7.0): one display in every client — a tool call's title, its four states, the duration and the facts beside it, the kinds of block; how `stopped` and `failed` stay on a finished reply; what the approval, question and plan cards offer; numbers in the client's locale |
| `quirks.json` | What real servers do, and how both harnesses cope |
| `conformance/<area>.json` | Cases with their expected results, run by both test suites |
| `acp.md` | The link between them: ACP plus every `_lembas/*` method, field and capability |

## Vocabulary

- **Risk classes:** `read`, `write`, `execute`, `interact`. **Reading and writing are never one
  tool** among the shared ones, so a read-only subagent is given exactly the readers.
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
  plan mode; `team` (3.2.0): the session's team setting allows more than one agent, and a model could
  serve as a subagent (3.3.0: `subagents.md` §4; before, the chat's model serving two requests at
  once); `phone` (3.6.0): a message in the chat came from an app on the person's phone that
  carries out phone actions (`phone.md` §1). `scheduled` (3.8.0): the session was opened by a schedule for its
  run, and the turn is that run (`reports.md` §2). A requirement that goes away between the offer and the call makes the call answer the
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

## 3.8.0

Written ahead of the implementations; the CLI carries it in the same round.

- **Reports** (`reports.md`, `tools/report_write.json`, `loop.json` `scheduled.notify`). A
  schedule's run no longer answers into a conversation: the model files a report with
  **`report_write`** (title, body, summary, importance) or the run leaves one line in the
  schedule's history. New requirement **`scheduled`**: the tool is offered only in a session a
  schedule opened for its run, and is unavailable in a turn of it that is not the run. **Notify
  when** is `always` (a report every run), `when` (only if the person's condition holds; the
  default) or `never` (the tool is not offered); the instruction for each is a spec text the
  server puts into the run's prompt. A run files at most one report — a second call replaces the
  first; under `always` an answer without a report is filed as one. **`[SILENT]` is retired**:
  told to no model, and a reply that still carries it leaves no report. A run on a linked
  computer carries its report **in the turn's answer** (`acp.md`, capability `reports`,
  `scheduled.notify` in the request, `scheduled.report` in the answer) — no new method.
  Conformance area **`reports`**.
- **HTML pages in answers** (`pages.md`, `prompts/blocks/html-pages.md`, `loop.json` `pages`,
  `models/schema.json` `html_pages`) — experimental, off unless switched on. The reply stays
  Markdown; the model may add a complete, self-contained page in a fence whose info string is
  `html-view` and a title. **A page is static everywhere** — HTML, CSS and inline SVG; no script,
  no event handlers, no `<link>`/`<meta>`/`<base>`, frames, media or forms, no address but
  `#fragment` and an inline `data:image/…`; a client rebuilds it from the allow-list
  (`loop.json` `pages.static`) and shows it in a frame with scripts off, or shows the note.
  Offered only when the instance's switch, the model's flag and the
  chat's switch are all on (a scheduled run: instance and model); never to a subagent, the phone
  assistant or an agent chat. A reader that cannot show a page sees *This answer has a page:
  title*; anything that reads a reply as text sees `[page: title]`. A switch changed mid-chat is
  a session note (`context.note.pages_on`, `pages_off`). **`blocks/prototypes.md`, the context
  field `prototypes` and the capability of that name are gone** (the CLI alone had kept them);
  the conformance area `prototype` is replaced by **`pages`**.
- **Which permission rule decides** (`permission/rules.md`): the last matching rule is the
  candidate, and a candidate made after the written ones — a project's, an "always" answer, one
  set in a menu — gives way to a narrower stricter written rule, to the person's own config, and
  (an "always" answer) to a written deny. So `read: allow` leaves `read *.env: ask` asking,
  whether a menu wrote it or a person typed it. Making a rule: the action already in force is a
  no-op; a rule the person's own config or a written deny takes in whole is refused; the stricter
  rules that still apply are named. Conformance area **`rules`**. `permission/defaults.json`
  allows `report_write`.

## 3.7.0

Written ahead of the implementations; the CLI carries it in the same round (`display.md`,
`loop.json` `display`).

- **One display in every client** (`display.md`). **A tool call has one short title**, made from
  its arguments alone — `questions` and `patch` by what they name, else the first non-blank string
  among `summary`, `command`, `code`, `pattern`, `query`, `url`, `path`, `file_path`, `from`,
  `description`, `name`, `id`, `title`, `key`, `target`, `to`, `action`; first line, 80 characters
  — and it is the same string in the transcript, as `running_tool`'s `what` and as the link's
  `tool_call` title. **This fixes the order `what` is taken in**: before, both implementations
  looked at `path` before `pattern` and `query`, so a `grep` was titled by its directory.
- **Four states** — `running`, `done`, `failed`, `denied` — told apart in every client; a finished
  call shows **how long it took** (`0.4s`, `1m 05s`, `2h 03m`), a denied one never; an edit shows
  **`+added −removed`**, counted from the diff **by its hunks** (a header without `a/` `b/` is
  still a header; a removed line beginning `-- ` is still a line); a command its non-zero
  `exit N`; a search its hits.
- **The blocks** every client distinguishes: `shell`, `edit`, `read`, `search`, `web`, `task`,
  `tool`; `plan` and `hidden` are not drawn.
- **`stopped` and `failed` stay on the finished reply**: the progress text alone (`Stopped: by
  you`, `Stopped: budget`, `Failed: …`) at the end of that reply, in every client, and again
  wherever the reply is shown from what was stored. Nothing else says it twice.
- **The cards**: an approval offers Allow once / Always allow (where the request offers it) /
  Deny / Deny, and say why; a question its options and always "Something else"; a plan Approve
  with the mode to continue in, and Revise (`loop.json` `display.actions`).
- **Numbers in the client's locale format**; durations, ids, exit codes and line numbers never.
  Conformance texts are in the plain format.
- **`python` says why it could not run** (`tools/python.json`): one text used to answer three
  situations. `unavailable` is now only *no page of this chat is open* and says where to open
  it; `texts.no_answer` — a page is open but did not answer; `texts.timeout` — the run passed
  its `{{seconds}}`. Conformance area **`python`** (the server's).
- **The approval card keeps its own title** (`acp.md` `session/request_permission`): the whole
  command, or the tool's name — not the short title — and `_meta.lembas.tool` names the tool.
- Conformance area **`display`** (titles, durations, hunk counts, facts, the end line, drawn
  blocks); `progress` gains the `budget` stop.

## 3.6.0

- **Phone actions** (`phone.md`, `tools/phone_action.json`, `loop.json` `phone`): a chat's model
  may do something on the person's phone when they write from the LLeMbas app on it — an alarm or a
  timer, a calendar event, an SMS or email draft, the dialer, a route (`navigate`, `add_stop`,
  `search_nearby`, `stop_navigation`), an app or an address, media, the torch, a settings panel,
  an automation. The app declares the capability `phone_actions` with each message; the person
  confirms every call on the phone (`confirm: always`); the result is `done`, `declined` or
  `failed` with what the phone said. The server's alone (`scope: llembas`, `sessions: chat`); new
  requirement `phone`, offered once a message came from the phone and kept, answering
  `unavailable` when the message being answered did not. Conformance area **`phone`** (the checks
  and the result texts).

## 3.5.0

Written ahead of both implementations; the server vendors it as written, both carry it in the same
round (`acp.md` *Scheduled work on a linked computer*, `loop.json` `scheduled`).

- **Scheduled turns on a linked computer.** A schedule may run each time as an agent turn on a
  linked device, in a folder of it. The device must allow it: **Accept scheduled work**
  (`remote.scheduled`, off by default; `lembas service scheduled on|off`), said to the server as
  `limits.scheduled` and judged again at every request — off, every scheduled request is refused
  with one fixed text. The server opens an agent session marked `scheduled` (schedule, run, mode,
  approval wait) with the schedule's Min/Max as an agent chat's, and prompts it with the run's own
  text. **The mode** is the schedule's — Manual, Edit or Auto, **Edit when none was chosen**, so never
  wider than Edit unless the person picked Auto; Plan refused — lowered to `remote.max_mode`. The
  answer reports the run: `status` `ok`, `silent` (the `[SILENT]` sentinel on the first or last
  line, the server's rule), `failed` or `cancelled`, and the final reply's `text`. A device that is
  away is waited for like a busy model, then the run is missed; a refusal fails the run.
- **Approvals in a scheduled turn** go to the person through the server (a phone notification with
  the card), carry `scheduled` on `session/request_permission`, and wait the schedule's wait
  (default 30 minutes) instead of `remote.approval_timeout`; then they count as denied and the turn
  goes on. `ask_user` and `plan_submit` have nobody to answer; `show` is not offered.
- **Monitor mode, the device's half**: `_lembas/monitor` runs a schedule's watch command in its
  folder, with a timeout, under the mode's permission rules — **a command the mode would ask about
  is refused** — and answers the output's hash (`sha256:` over the bytes and then the exit status) and a
  short excerpt; the server runs the model only when the hash changed, and records `unchanged`
  otherwise.
- **Schedules over `/api/v2` with the device's token** (scope `link`): the CLI's `/schedule` lists,
  creates from words (compile), pauses, resumes, runs now and reads history. A run may be
  `unchanged`, may wait for the `device`; a schedule may say `runs_on` and `watch`.
- **`team_add` under a sub-manager** says where the report goes: `team.texts.added_under` and
  `added_waiting_under` ("Its report goes to <parent> when it finishes").
- Capability `scheduled`. Conformance areas **`scheduled`** (the mode, the wait, the refusals),
  **`silent`** (the sentinel and the stripped text) and **`monitor`** (the hash, and what a watch
  command may run); `team` cases changed for `added_under`.

## 3.4.0

Written ahead of both implementations; the server vendors it as written, both carry it in the same
round (`progress.md`, `subagents.md` §5–§13).

- **Progress** (`progress.md`, `loop.json` `progress`): one fixed set of phases — `queued`,
  `waiting_for_model`, `loading_model`, `reading_prompt`, `thinking`, `writing`, `running_tool`,
  `waiting_for_person`, `waiting_for_subagents`, `compacting`, `retrying`, `stopped`, `failed` —
  each a structured object `{ phase, since, …data }` with fixed fields, an English text that is
  the shared i18n key, and one line every client draws: `text · elapsed`. Sent beside the old
  `status` text in `/api/v2` and over the link (`acp.md` capability `progress`: the `phase` event,
  `progress` in `status`); `status` stays. Area `progress`.
- **Subagents v2** (`subagents.md` §5–§13; supersedes `team.md` where they differ, which says so):
  the **main agent** is the session's, permanent, and **alone adds, assigns, replaces, stops and
  decides** (`offered: main`); a member with `lead: true` is a **sub-manager** that directs those
  placed under it (`team_add`'s `parent`). Every subagent has an id (`s1`…), a name, a position, a
  parent, a task, a mode and a model. Min above 1 opens `min − 1` **slots** from the start of the
  reply. **Permissions are inherited and never widened**: a subagent's mode is its parent's or
  stricter; a narrowed one may get **request options** (`read`, `write`, `execute`) and ask the
  main agent (`team_request` → `team_decide`, a grant that never reaches past the main agent's own
  mode); a call nobody in the tree can allow goes **to the person**. A subagent ends with
  `team_finish`, is stopped, or **runs out of context**: paused, one final turn to save its
  findings and tell its parent, then `out_of_context` and a report; findings and transcripts are
  kept, readable by the parent, the main agent and the person. New tools `team_whoami`,
  `team_finish`, `team_findings`, `team_request`, `team_decide`, `team_assign`, `team_replace`;
  `team_add` gains `parent`, `mode`, `request`; `team_send` takes `parent`; `team_stop` is the main
  agent's. `blocks/team_submanager.md`; `team_lead.md` and `team_member.md` reworded. Over the link,
  capability `tree`: the `team` event, `_lembas/team`, `/transcript` (read-only), `/findings`,
  `/stop`, `subagent` on an approval. **An agent chat's subagents are the device's** (`subagents.md`
  §14, `acp.md` *Subagents in an agent chat*): capability `agents` carries the chat's `{ min, max }`
  down on every turn (and `said`, the person's earlier words, on `session/new`); the tree, progress,
  approvals, findings and transcripts come up. Areas `team` (cases changed where a member used to add or
  stop, and new ones) and `delegation`.

## 3.3.0

Written ahead of both implementations as LLeMbas CLI 02c7282; both carry it in the same round (`subagents.md`).

- **Subagent** is the word everywhere a person or a model reads one — a `task` subagent and a
  team's member alike; *helper* is retired. Tool names and aliases are unchanged. The `team`
  conformance area's step `helper_done` is now **`task_done`**.
- **The model**: a subagent runs on the session's model, unless the person named another one in
  this session's own messages (its served id, its id at its connection or its display name, a whole
  word, without case — never the model's text or a tool's output). `task`'s and `team_add`'s
  `model` is refused otherwise: "Use another model for subagents only when the person names it."
  No setting chooses a subagent's model; an agent definition's own `model` is not used for one.
- **Capacity, the logical restrictions only**: another model on the session's model's one-model
  server is refused ("That model is on the same one-model server as this chat's model; name a
  model on another server."); the session's model as its own subagent needs `parallel ≥ 2` or no
  limit, else "This model serves one request at a time: name another model, on another server,
  for subagents." — and nothing else is picked. A member is refused for these too, and still only
  waits for a full model.
- **The offer**: the `agents {min, max}` setting is shown as **Subagents** (**Min**, **Max**;
  `loop.json` `subagents.setting`) — one range from 1 to the cap with two handles, and a hint that
  the numbers count the session's own agent: 1 is the agent alone, Max 4 the agent and up to three
  subagents — and with it the team tools, when subagents are enabled and at
  least one model could serve: the session's, with the capacity, or any usable model off its
  one-model server. `context.note.team_own` tells the model, and `subagents.setting.own` the
  person, when the session's model cannot serve subagents itself.
- `task`'s and `team_add`'s `model` descriptions, `task`'s description (a line on the model),
  `models/schema.json` capacity texts, `team.md` §2, §4, §8. Conformance area **`subagents`**.

## 3.2.0

Written ahead of both implementations; both carry it in the same round.

- **Effort levels and the thinking switch** (`models/schema.json`). `thinking_switch`: the model's
  server turns thinking off for `chat_template_kwargs.enable_thinking: false`. The level `off`
  exists only then, and sends that and no effort — before, `off` sent nothing, which left the model
  at its server's default effort. A model that takes no efforts has the one level `default`, which
  sends nothing. The levels a chat offers are `off` (with the switch) and the efforts; one level
  shows no control, `[off, X]` a Thinking on/off switch, more a slider. `capacity.parallel`: how
  many requests the model serves at once (`single_session` is `parallel: 1`). Conformance area
  `levels`; `quirks.json` `thinking-default`; in `acp.md` an `effort` is one of these levels.
- **Reasoning goes back during its turn** (`context.md` §8): every assistant message after the last
  message that started a turn carries its reasoning (`reasoning_content`; Anthropic thinking
  blocks); earlier turns carry none. Area `reasoning`.
- **No absolute paths in the prompt** (`context.md` §11): an agent session's environment block names
  the project and says paths are relative to its root (`loop.json` `context.env`); relative paths
  resolve against the project root, absolute ones are still taken. Area `paths`.
- **Teams** (`team.md`): `team_add`, `team_send`, `team_read`, `team_stop` (shared, both,
  `requires: team`); the chat setting `agents {min, max}`; the room and its marks; reports to the
  manager; the size across the tree, `task` helpers included; a manager's work tools refused with a
  fixed text while the tool list stays the same; members waiting for a slot past the model's
  capacity; `blocks/team_lead.md` and `blocks/team_member.md`; size notes in `context.note`. Area
  `team`.

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
