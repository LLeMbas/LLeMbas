# Changelog

What changed in each release of the LLeMbas server. The release notes are also
at <https://llembas.eu/changelog.html>.

## Unreleased

### Added

- **Loading the model, and how far the prompt has been read.** A reply on a self-hosted server now
  asks it how far it has read the prompt (llama.cpp's `return_progress`) and shows "Reading the
  prompt: 1,200 / 8,000 tokens (15%)"; a server that refuses the field is asked once and then left
  alone. While a one-model server (llama-swap) is still loading the model the reply needs, the reply
  says "Loading {model}" -- only when that server says so, from its list of running models or its
  loading banner, never guessed from a slow answer. The banner is no longer kept as the reply's
  thinking.
- **A prompt queued behind a reply says so.** On `/api/v2` a message typed while a reply is being
  written carries `progress` `{"phase": "queued", "since", "ahead"}` -- `ahead` counts the reply
  running and the prompts queued before it; every other message has `progress: null`. Fixtures:
  new `message_queued.json`; `message_user.json` gains `progress`.
- **A scheduled result says what it found.** The notification carries the task's name and the
  result's first lines for an unlocked phone or a desktop, and a public version for the lock screen
  (the name and "Finished" or "Failed", nothing the run wrote); its actions are Open and Run again
  (and Pause on a failure), labelled in the person's language. One notification, every channel: web
  push shows it and runs Run again from the notification, UnifiedPush carries it encrypted end to
  end, and on `/api/v2` every run has `notification` (null when nobody was told), so an app
  following the `schedules` event shows the same. Fixtures: new `push_schedule.json`,
  `push_schedule_failed.json`; `schedule*.json` and `event_schedules.json` gain `notification`.
- **Monitor mode: run only when a page changed.** A schedule may watch a web address ("Only when
  this page changes" in the form, `watch` on `/api/v2`, `watch_url` in the model's scheduling
  tools). Each run fetches it first -- on the server, through the same private-address guard as
  web fetching, every redirect checked, at most 256 KB and 30 seconds -- and when it is byte for
  byte what the last look saw the run is recorded **Unchanged**: no model runs and nobody is
  told. When it changed (and on the first look) the run is told what changed and what is there
  now, as data. A look that fails fails the run and never counts as a change. Fixtures: new
  `schedule_watching.json`; schedules and runs gain `watch` (null when nothing is watched).
- **Pinned chats.** A pin on each chat's row in the sidebar puts it in a **Pinned chats** section
  above `[ CHATS ]` (wherever it is filed) and takes it out again; on `/api/v2`, `PATCH
  /chats/{id}` takes `pinned`. The column was read for the sidebar's order but nothing ever set it.
- **`/api/v2` catches up with the web**, so the apps can: chat folders made, renamed, nested and
  deleted (`POST/PATCH/DELETE /folders`; a chat moves with `PATCH /chats/{id}` `folder_id`);
  `GET /search` over chats, files and memories (the search page's stores; snippets as plain text
  with the matched ranges); `GET/POST/PATCH/DELETE /memories`; the library's writes (`POST
  /files/upload`, `/files/mkdir`, `/files/rename`, `/files/move`, `DELETE /files`, to the trash);
  a chat's tool and skill switches, web search among them (`GET/POST /chats/{id}/scope`) and
  `POST /chats/{id}/allow/clear`; `PATCH /me` with `language` and `theme`, and `GET /me` gains
  `scopes`, `languages`, `themes` and `language_setting`. Voice and images stay on `/v1` (they
  need the `models` scope, which the apps ask for at sign-in beside `app`). Fixtures: new
  `folder.json`, `folders.json`, `search.json`, `memories.json`, `memory.json`, `scope.json`;
  `me.json` gains the four fields.

## 2.0.0-beta.8 — 2026-11-10

The harness spec is **3.4.0** (progress phases; subagents v2), pinned from LLeMbas CLI
v2.0.0-beta.6.

### Added

- **What a reply is doing, the same in every app.** Beside the status line, every reply now says its
  phase in one fixed set of words -- queued, waiting for a model (which, and why), loading the
  model, reading the prompt (how far, where the model's server says), thinking, writing (tokens a
  second), running a tool (which, and what), waiting for you (an approval, a question, the plan),
  waiting for subagents, compacting, retrying, stopped (by you, or at a limit), failed -- with how
  long it has lasted, ticking. The page draws it in your language; an agent chat shows its
  computer's phase (from LLeMbas CLI 2.0.0-beta.6), or one read off what the computer reports. A
  scheduled run waiting for a free model says which model and why.
- **Subagents as a tree the main agent builds** (harness spec 3.4.0, in chats on this server). The
  chat's own agent is the main agent: it alone adds, assigns, replaces and stops subagents, gives
  each a name, a position and a parent (itself, or a sub-manager it placed, which directs but does
  not add), and a mode no looser than the parent's -- stricter with request options, which the
  subagent asks the main agent for and the main agent decides. Min above 1 opens Min − 1 slots from
  the start. Every subagent knows who it is (`team_whoami`), keeps findings (`team_findings`), ends
  with its report (`team_finish`), and when its context fills it is paused, given one last turn to
  save what it found, and ends; its parent is told, and the main agent can replace it
  (`team_replace`) with the same name and position, briefed with its findings. Ended subagents stay
  in the tree with their findings, and their conversations are kept as long as the chat.
- **Subagents in agent chats.** An agent chat's Subagents setting (Min and Max) now means something:
  it goes to the computer with every message (LLeMbas CLI 2.0.0-beta.6 runs the subagents there,
  on your models), and a change between messages is sent at once. The computer's tree of subagents
  comes back and is kept with the chat; when a subagent needs something nobody in the tree may
  allow, the approval card says which subagent asks. A computer whose LLeMbas CLI does not run
  subagents is said beside the setting. The new-chat screen's setting for an agent chat is used.
- **`/api/v2`: the subagent tree.** The stream sends `tree` (`{tree}`, harness spec 3.4.0
  `subagents.md` §12) when it changes; an approval item carries `subagent` (`{id, name, position}`
  or null); an agent chat's `subagents.offered` is true where subagents are allowed. Fixtures: new
  `event_tree.json`; `event_ask.json` and `chat_agent.json` changed.
- **The activity panel draws the subagent tree.** The main agent at the root and every subagent
  under its parent: its state, name, id and position, what it is doing now (the phase, ticking),
  its context use, model, mode, messages and findings, and the last message to or from it; open
  any subagent's conversation (read-only, live or after it ended) or its findings, or stop it and
  those under it. Ended subagents are listed after, with how they ended. An agent chat's tree is
  its computer's, as last sent.
- **`/api/v2/models?kind=agent&device=`**: for a new agent chat, every model's `subagents` is the
  agent chat's fact (offered where the instance and the person allow subagents -- what the web's
  new-chat slider reads), and `agent_subagents {offered, device_runs}` says it once, `device_runs`
  being whether that device lists `agents` and `tree` (null while it is not linked). Fixture: new
  `models_agent.json`.
- **`/api/v2`: reading a subagent.** `GET /api/v2/chats/{chat}/tree` (the tree and the room,
  `live` false when an agent chat's device is away and the kept tree is sent),
  `GET .../subagents/{id}/transcript` (its conversation, read-only, live or after it ended),
  `GET .../subagents/{id}/findings`, and `POST .../subagents/{id}/stop` -- the same for a chat's own
  subagents and an agent chat's (asked of its device; 409 while the device is not linked). A chat's
  stream sends its own tree as it changes too. Fixtures: new `tree.json`, `subagent_transcript.json`,
  `subagent_findings.json`.
- **`/api/v2`: `me` has a contract fixture** (`me.json`): the account, `language`, `theme`,
  `default_model`, `permissions` and `services`, as the apps read them; the shape is unchanged.
- **`/api/v2`: `progress`** (harness spec 3.4.0, `progress.md`). The stream's `status` frame is
  `{text, progress}` -- the phase object `{phase, since, ...}` or null, sent when it changes (its
  data at most four times a second); a schedule's run carries `progress` too. Fixtures:
  `event_status.json` (changed), new `event_status_person.json`, `event_status_stopped.json`,
  `event_status_idle.json`; `schedule*.json` and `event_schedules.json` gain `progress`.

- **The Terminal before the first message.** On a new agent chat, the Terminal opens as soon as the
  computer and the folder are chosen -- before anything is sent -- on a device that opens terminals
  for you. The message that starts the chat there takes that shell over, with what it has printed,
  so the chat's Terminal is the same shell. Choosing another computer or folder opens the shell
  there instead, and the one left behind is closed.
- **`/api/v2`: the terminal, for the apps.** `/api/v2/chats/{chat}/terminal/ws` is an agent chat's
  shell (the same one its page opens), and `/api/v2/devices/{device}/terminal/ws?directory=` is one
  before the chat exists, which the chat takes over. Both take the app's token in the handshake's
  header; frames are the page's. `GET /api/v2/devices` says `terminal` for each device: whether a
  Terminal can be offered on it.

- **`scripts/smoke_live.py`**: a post-deploy check of a running instance -- healthz, its version, the
  token's models, one short temporary chat on a model you name whose stream must carry `status`
  with `progress` and end in `done`, then that chat deleted. The token comes from an environment
  variable and is never printed; nothing else is changed.

### Fixed

- **A command corrected on an agent chat's approval card ran as the model wrote it.** The edit never
  reached the device -- from the page or from the apps -- so Allow ran the original command. It runs
  what is in the box now.
- **`/api/v2` described a device's approval wrongly in its contract.** An approval's `title` is the
  heading ("Run a command") and its `detail` is the command -- what an `editable` item lets you
  correct; the contract's example had them the other way round, and the apps followed it. An
  approval also no longer claims to take a free-text answer (`free_text` is false).
- **`/api/v2`'s chat list no longer says "no subagents"** for every chat it lists. It did not work it
  out there; it says `null` now, and a chat's own `subagents` (opening it) is the answer.

## 2.0.0-beta.7 — 2026-11-09

The harness spec is **3.3.0**, pinned to LLeMbas CLI v2.0.0-beta.5.

### Changed

- **Subagents, not helpers.** Everything a model sends to work for it -- a `task` subagent, in the
  foreground or in the background, and a team's member -- is a *subagent* everywhere you read it:
  the chat, the activity panel, Settings, the admin pages, the permissions, and what the models
  themselves are told.
- **A subagent runs on the chat's own model** unless you name another model in the chat -- its id,
  its name, or connection/model, in your own words. There is no setting for a subagent's model, and
  a model that tries to pick one you did not name is told to use the chat's own.
- **Only the logical limits.** The chat's own model serves its subagents when it can answer two
  requests at once (or has no stated limit). A model that serves one request at a time cannot be
  its own subagent: you name another model, on another server, and nothing is picked for you.
  Another model on the same one-model-at-a-time server as the chat's is refused, because loading
  it would unload the chat's model.
- **The Subagents setting** is offered whenever some model could serve -- the chat's own, or another
  off its one-model server -- and says so beside it when only a model you name could.
- **Subagents is one slider with two handles, Min and Max**, from 1 to the instance's cap, in the
  chat's settings and as your default in Settings -- not two number fields called fewest and most.
  The numbers count the chat's own agent: **1 is the agent alone, with no subagents**, and Max 4 is
  the agent and up to three. The schedule form's Subagents field and the admin cap say the same.

### Added

- **The activity panel, as a side panel should be.** Context shows the share used, tokens against
  the window, a bar by category, the categories with tokens and share, how far it is to automatic
  compaction, and a **Compact** button. Background tasks lists every running subagent, team member
  and background command with its elapsed time and a stop button, each opening to its latest step,
  what it is writing and its whole conversation; what one started is folded under it ("Tasks: N"),
  and the finished ones are folded at the end. The room and the plan follow. On a phone it is a
  full-height sheet.
- **`/api/v2`**: a chat says whether it offers subagents, whether they need a model you name, and
  the instance's cap (`subagents`); so does every model before a chat exists (`/models`); the
  activity's context says how far automatic compaction is and whether Compact can run now
  (`until_compact`, `auto_compact`, `compactable`); and `POST /api/v2/chats/{chat}/compact`.

## 2.0.0-beta.6 — 2026-11-09

The harness spec is still **3.2.0** (LLeMbas CLI v2.0.0-beta.3).

### Added

- **Scheduled work keeps a history.** Every run is recorded: when it was due and when it ran (on
  time, late or caught up, and how many runs were missed), whether it found something, found nothing
  new, waited, was skipped or failed and why, which model ran it and why that one, what it cost, and
  where its result is. Records are kept 90 days by default (Admin → Scheduling).
- **Models for scheduled work.** Admin → Scheduling lists the chat models to tick and order; a
  person can narrow that list under Settings → Scheduled. A run takes its schedule's own model when
  it is free, else the first free one in order: a hosted model is free, a model with a request slot
  nobody is using is free, and a model on a server that holds one model at a time is free only when
  nothing is being answered there -- a run never pushes out a model that is answering someone. When
  none is free the run waits, tries again at 2, 5, 10 and 15 minutes and then every 15, and is
  skipped once it is too late to be worth running.
- **Each run in a fresh chat, or one chat that keeps growing**, per schedule. A fresh run is told
  what the previous run found.
- **Notifications when it matters.** Per schedule: every run, when it matters (the default for
  anything that repeats), or never. A run with nothing new answers so and is recorded without a
  notification. Failures are told the first time, then at most once every six hours for the same
  error, and after three in a row the notification asks whether to pause it, with a Pause action.
  The same decision drives the browser's notifications and the phone's (UnifiedPush); neither
  carries anything the run wrote, and tapping one opens that run.
- **Missed runs.** A run late by up to half its period (between 2 minutes and 2 hours, or the
  schedule's own setting) still runs; past that, one catch-up run or none, per schedule.
- **Safe retries.** A run whose model's server was never reached is tried again at 5, 15 and 30
  minutes; a rate limit holds until the time it names; a real failure is not retried. A run that
  writes nothing for 10 minutes is stopped, instead of being waited on for an hour.
- **Run settings per schedule**: the model, thinking, team size where the model offers it, and the
  tools a run may use (a safe set by default). A run is never offered the scheduling tools.
- **The place in the month**: the second Monday, the last Friday, the last day of the month.
- **The next five runs** are shown in every schedule form before saving, with the rule in words.
- **The Scheduled list** shows each schedule's next run and last status, with a switch to pause and
  resume it. **A schedule's page** shows when it runs, how, and its history, with Run now and Edit.
- **The model's scheduling tools** take the notify setting, fresh or continuing and the model, and
  `schedule_list` shows each schedule's last status and next runs.
- **`/api/v2`**: `/schedules` (list, create, get, change, delete, pause, resume, run), `compile`
  (words to a rule and its next runs), `preview`, `runs` (paged) and one run, `/schedules/models`
  (read and narrow), and a `schedules` event on `GET /events`.

### Fixed

- **Choosing a fallback model adds it.** The model page's fallback list needed a separate Add button
  after choosing; choosing and pressing Save dropped the choice without a word. Now the choice goes
  straight into the list, and Save keeps it.
- **An embedding model is no longer offered as a chat model.** Switched on for search and memory, it
  showed in the model picker, the new chat's default and the command palette; it is still served at
  `/v1/embeddings` and used by the index.
- **Test & refresh and Check balance on a connection no longer turn the page into an unstyled
  copy of the connection.** The button's request was sent as a save of the whole page, because the
  form's in-place header reached the buttons inside it (htmx merges headers from every parent);
  the header now goes only on a request the form sends itself. The fix in 2.0.0-beta.2 covered
  everything but the header.

## 2.0.0-beta.5 — 2026-11-09

The harness spec is **3.2.0**, pinned to LLeMbas CLI v2.0.0-beta.3.

### Added

- **Teams: agents that work together** (harness spec 3.2.0). A chat's agent can add **members**:
  background agents that each own a part of the work, briefed by it and run in parallel, while it
  leads them -- plans, splits the work, coordinates and reviews. Members message each other and
  their manager in the team's **room** (to one by name, to several, or to all); a member's final
  reply is its report to its manager, which wakes it. A member may lead an area with members of its
  own, so a team can grow from a few agents side by side into a tree. An agent that leads a team
  does not do the work itself: its calls that change things answer that it should give them to a
  member, and its reading tools stay for review.
- **Agents, fewest and most, in the chat settings** beside the effort: how many agents a chat's team
  may have, its own agent included (1 to 4 by default; up to an instance cap of 32, set under
  Admin → Agents). Your default for new chats is under Settings. Offered where subagents are on and
  the chat's model serves at least two requests at once; with the most at 1 there is no team and no
  helper. Changing it during a chat is told to the agent with your next message. Members beyond
  what the model serves at once wait for a free slot and start in turn.
- **The activity panel** beside a chat: what is in its context, by category, against the model's
  window; the files the chat and its agents wrote, with lines added and removed; every background
  agent -- a team's members as a tree, helpers and commands -- with what it was asked to do, its
  state, how long it has run, its model, its latest step and what it is writing, a button to read
  its whole conversation and one to stop it; the team's room; and the plan. The strip above the
  message box sums them up ("3 agents working · 1 queued") and opens the panel; on a phone the
  panel fills the screen.
- **`/api/v2`**: a chat's `agents` setting; `GET /chats/{chat}/activity` (everything the panel
  shows, with a `version`), `GET /chats/{chat}/activity/events` (an event whenever it changes),
  `GET /chats/{chat}/agents/{kind}/{id}` (one agent and its conversation) and
  `POST /chats/{chat}/agents/{kind}/{id}/stop`; `GET /chats/{chat}/team` and
  `POST /chats/{chat}/team/{name}/stop`.
- **Thinking can really be switched off.** A model's page has **Can switch thinking off**, for
  models whose server stops thinking when asked (Qwen3 and Nemotron 3 on llama.cpp, vLLM or
  SGLang). Only such a model offers *off*, and *off* now sends exactly that request and no effort.
  It used to send nothing, which on these models meant thinking at their server's default.
- **The chat settings menu shows only what the model offers.** A model with one level has no
  effort control; a model offering off and one effort gets a **Think before answering** switch;
  more levels keep the slider. Where the menu would hold nothing, the button opens the chat
  settings panel directly. Switching a chat's model redraws the menu for the new model. Your
  per-model default in Settings offers the same levels.
- **Requests at once** on a model's page (empty: no limit) replaces "Serves one request at a
  time"; 1 means the same. `/v1/models` reports it as `capacity.parallel`, with `thinking_switch`,
  `levels` and the start `effort`.
- **For the apps:** `/api/v2/models` gives each model's levels, start level and control, whether
  thinking can be switched off, its requests at once and a picture URL readable with the app's
  token; an agent chat says its `device_session_id`, so it can be continued in a terminal.

### Changed

- **A chat started without choosing a level starts at the model's start level** -- its own
  default, else medium where offered, else its first effort -- and a level a model does not offer
  (an *off* from before, or one another model took) is read as that start level.
- **A reply's thinking goes back to the model during its turn.** Between tool calls, and when a
  reply is continued, the model is sent what it thought before, so it carries on instead of
  reasoning its way to the same step again. Earlier turns are sent without it, as before.
- **Chat titles are asked for with thinking off** on a model that can switch it off: a title in a
  second instead of a paragraph of reasoning about six words.

## 2.0.0-beta.4 — 2026-11-07

The harness spec is **3.1.0**, pinned to LLeMbas CLI v2.0.0-beta.2.

### Changed

- **A chat's system prompt is made once and sent unchanged** (harness spec 3.1.0, "the prefix
  stays still"). It was composed again for every reply, so anything in it changing -- the date at
  midnight, a memory, the plan, Plan mode, a personality -- made the model's server read the whole
  chat again: on a long chat, minutes on a local model and the full input price on a cloud one.
  It is now kept with the chat and made again only after a compaction, for another model, when the
  offered tools change, or when you edit the chat's own system prompt. What changes during a chat
  reaches the model as a short note after your next message instead -- the date (on the first
  message and when it changes), a memory you edited, Plan mode switched, a plan you chose, a
  personality or custom instructions changed. The note is kept with that message and sent the same
  way from then on; it is not shown in the transcript. An instance's, a folder's or a model's
  system prompt edited during a chat applies from its next compaction or a new chat.
- **The tool list stays the same for the whole chat.** `python` is offered from the start wherever
  Python in the browser is switched on, and says it is unavailable while no page of the chat is
  open; Plan mode no longer takes the tools that change things out of the list but refuses them
  when called, and `plan_submit` is offered in every mode and says so outside Plan mode; reading
  skills stays offered with none. The last round after a limit is reached, or after a plan is put
  aside, keeps the list too: it is told the tools are withdrawn, and a call in it is not run.
- **Compaction asks for its summary as the next turn of the same conversation** -- the same system
  prompt, tools and messages, then the summary request -- instead of a separate request with the
  conversation pasted into it, which was a full re-read and, capped to fit, lost the middle of a
  long chat. The summary request's tokens, cached ones included, are counted in usage like a
  reply's.
- **`schedule_list` starts with the time it is now**, since the system prompt no longer carries a
  clock.

### Fixed

- **Saving a file that is not there yet makes it**, instead of answering 403 "No such file or
  directory".

## 2.0.0-beta.3 — 2026-11-04

### Changed

- **One way to say what an action did: a toast**, at the bottom right. Saving, deleting, testing a
  connection, uploading -- every page now says it the same way, whether it saves in place or
  reloads; before, a page that reloaded put a box at its top, where nobody was looking. A box stays
  in the page only for what is a state of it: a wrong password on the sign-in form, a standing
  warning, a connection's last error. The file editor says "Saved." as a toast too, and its bar
  keeps only whether the file has unsaved changes.
- **Every "are you sure?" is the same dialog**, in the theme. Removing the instance logo and a
  model's image now ask first, like every other delete. Tests keep it so: a result box without
  `data-flash`, or a browser `confirm()` / `alert()` / `prompt()`, fails the build.

### Fixed

- **Python in the browser runs.** Pyodide 314 is ES modules only and refused the classic worker
  the `python` tool and Run used ("Classic web workers are not supported"), even for
  `print("hello")`. The worker is a module worker importing `pyodide.mjs`, which the lock now pins in
  place of `pyodide.js`. **Fetch Pyodide again** (Admin → General): the copy already on a server is
  shown as missing until it has the new file.
- **Change password** has the same gap between its fields and its button as elsewhere (beta.2 left
  them touching).

## 2.0.0-beta.2 — 2026-11-04

### Changed

- **Files is a file manager in the whole window.** A path bar with New file, New folder and Upload;
  the folder tree and the list side by side, each scrolling on its own; a whole row opens what it
  names, with its type, size and date in line. On a phone the tree folds away and the buttons keep
  their icons.
- **A file opens in the editor.** A text file you may change opens straight in CodeMirror, with its
  highlighting, beside the tree; Markdown and HTML have Edit and Preview. A file you may only read,
  an older version, an image or a binary opens in the viewer. Versions are a menu on the file's bar.
  The old `/files/edit` address leads to the file.
- **A chat with no start folder works in its person's home**, not at the organisation's root: a
  model that saved `notes.md` put it where everybody could see it.

### Added

- **New file**: name it, and it opens in the editor.
- **Run, for Python files**, when an administrator has switched Python on and fetched Pyodide: a
  ▶ on every `.py` row and on the file's bar runs it in this browser (Pyodide, in a worker), with
  what it printed under the file, and Stop.

### Fixed

- **A 2.x installation is never offered a 1.x release.** The stable channel, automatic updates and
  `deploy/update.sh` keep to the running major (from 1.7.3), so the Update button on a 2.0 beta can
  no longer step back onto 1.7.x.
- **The Copy button beside a new invite link copies it.** It named the link instead of the field
  holding it, so it did nothing; it now copies and turns green.
- **Test & refresh and Check balance on a connection update their row again.** Inside a form that
  saves in place they took over that form's whole-page swap, and the row's answer replaced the
  page with an unstyled copy of the row.
- **Files opens in a member's home.** Only an organisation's owner and admins can read `/`, so
  for everyone else the explorer opened on "Access denied". It now starts in `/home/<name>/`, and
  `GET /api/v2/files?path=/` answers with that listing (its `path` says where).
- **A new chat on a phone opens at the top of its welcome**, not scrolled past the artwork.
- **Change password on a phone** no longer has an empty row under the current password.
- **The file editor highlights code again.** `@lezer/lr` imports esm.sh's `/node/process.mjs`, which
  this server does not have, so every language that parses failed to load and the editor opened as
  plain text. The import map now names a stub of its own for it, and a test checks every import.

## 2.0.0-beta.1 — 2026-11-03

The first beta of LLeMbas 2.0, a new major: one file system for chats, organisations, prices instead
of credits, provider types, and a new web UI. It starts on a new database: the 1.x one is renamed
`lembas.db.old` and nothing is carried over by itself — export from 1.7.3 first, then import (see
*Import and backups* on the wiki). Works with LLeMbas CLI 2.0.0-beta.1, LLeMbas Desktop
1.0.0-beta.1 and LLeMbas for Android 1.0.0-beta.1, and with no 1.x client. Harness spec 3.0.0.

### Changed

- `GET /api/v1/instance` also returns `scope`, the presented token's scopes, so `lembas login --token-stdin` can check that a handed-over token can link a computer.

- **Settings save in place.** Saving a form under Settings, Administration or Scheduled no longer
  reloads the page: the page is redrawn where it stands — same scroll position, the same sections
  open — and what was saved, or what went wrong, is said in a toast. Without JavaScript every form
  posts as it always did. A create that opens the new thing's page, or a delete that leaves it,
  still goes there.
- **Drag and drop.** A chat is moved into a folder by dragging it onto the folder (or onto *Chats*
  to take it out), or from its new *Move to folder* button, which works from the keyboard and on a
  phone. The models list and the custom tools list are put in order by dragging their numbers, by
  touch on the same handle, or with the arrow keys; dragging on a filtered page or page 2 moves
  only the rows shown. The models list has no up and down arrows any more. A model's fallback list
  is put in order the same way, and saved with the model's page.
- **Files.** The organisation's file tree has pages of its own (the folder button in the sidebar):
  an explorer with the path above, a folder tree beside the list on a wide screen, upload (also by
  dropping files on the list), new folder, rename, move (drag a row onto a folder, the tree or the
  path, or *Move* in its menu), delete to the trash and restore, and every version of a file with
  *Restore*. It shows what you can see and nothing more. A file opens in a viewer — Markdown
  rendered, code highlighted, text as text, an HTML file only in a sealed frame that cannot reach
  this page, its cookie or the network, an image as an image, anything else as a download (always
  `attachment`, never run) — and a text file up to 1 MB in an editor (CodeMirror 6, vendored),
  which saves without leaving the page and refuses to overwrite a file that changed since it was
  opened unless you say so. Without JavaScript the editor is a text box that still saves.
- **`show` is a card in the conversation.** A file a model shows you is a card with its name and
  path; *Open* shows it in the viewer beside the conversation, and a card that arrives while you
  watch opens by itself on a wide screen. The card is checked again when opened, against what the
  chat may reach now. A linked device's file opens the copy it sent.
- **What a chat may reach, as a tree of ticks.** *Chat settings → Files → What it may reach* shows
  the tree with a level per ticked path (no access, read only, read and write, read, write and
  commit); below a ticked folder everything comes with it unless ticked otherwise, and a folder
  above a ticked path that is not ticked itself is drawn as a *passage* — not listed, not readable,
  the ticked path inside reached by its full path only. Saving gives the chat grants of its own;
  *Use the data group's preset* goes back. The same editor sets a data group's *Preset* and
  *Ceiling* (Administration → Data groups, or your own group under Settings → Data).
- **Undo a chat's file changes.** *Chat settings → Files → Undo its file changes* lists every file
  the chat wrote — put back as it was before the chat's first change, or, for a file the chat made,
  moved to the trash — marks one somebody changed since, and does it on confirmation, each as a new
  version.
- **Memory: export, import, forget.** Settings → Memory exports your memories (one data group or
  all) as the export file LLeMbas 1.7 writes, imports such a file — into the group of the same name
  or one you choose — and reports what it took and, by name, what it left and why, and forgets every
  memory of one group at once.
- **One search over chats and files.** The search page has a *Files* tab, from the search index and
  only what you may read; chats are also found by meaning, not only by their words. The
  Extraction page shows the index — what is waiting, done and failed, pieces and vectors, the
  indexer's state — with *Rebuild the index*. *Piece size* and *Overlap* are gone: the index cuts
  text by tokens itself.
- **Ctrl/⌘ + K is a palette**: go to a chat, a file, a model or a setting by typing; *Commands…*
  in it opens the slash commands as Ctrl/⌘ + K used to.
- **`@` offers files in the tree** that the chat can read; the path goes into the message and the
  model reads the file itself.
- **Invites.** Administration → Invites makes a single-use link into your organisation — for one
  address or anyone, as a member or an organisation administrator, for 1, 7 or 30 days — shown
  once, and lists the ones waiting so they can be revoked. The link opens a sign-up page that makes
  the account and spends the invite.
- **First run.** After the first account, a short wizard names the organisation and the instance
  and hands over to Connections.
- **A logo.** Administration → General takes a PNG, JPEG or WebP (up to 512 KB) shown where the
  instance's name is, in place of the leaf; the themes stay as they are.
- **Web Push is off by default.** A browser's push goes through its maker's relay, so it is now an
  administrator's choice (Administration → General), with that said beside the switch; notifications
  while a page is open, and the apps' UnifiedPush, work either way. A push says only that something
  is waiting — never a chat's title or a reply's words.
- **Python in the browser can be switched on** (Administration → General, off by default) once
  Pyodide is on the server: a chat page then runs the `python` tool in a worker in your browser.
  Pyodide does not ship with LLeMbas (it is about 13 MB): *Download Pyodide* on the same card, or
  `lembas-server fetch-pyodide`, fetches the pinned release (314.0.7, the core and the standard
  library, no packages) once from its own address, shows how far it has got, and keeps the files
  only if every one matches the size and SHA-256 committed with LLeMbas. They are kept in the data
  directory and served by this server; nothing is loaded from anywhere else afterwards. *Remove*
  deletes them and switches the tool off.
- **A chat works with files.** In an ordinary chat the model has the file tools of the harness
  spec (3.0.0), over the organisation's file tree: `read`, `write`, `edit`, `list`, `glob`,
  `grep`, `search` (by meaning and words, over the search index), `mkdir`, `mv` and `rm` (to the
  trash), with the same names, descriptions and answers as the LLeMbas CLI's — `edit` matches the
  way the CLI's does, forgiving whitespace and indentation.
  Every one goes through the chat's access, so it reaches only what the chat was granted, and a
  refusal says why in the spec's words. An existing file is changed only after the chat has read
  it, and not if it changed since. `show` opens a file for you beside the conversation, and
  `python` will run Python in your browser once the page can (not yet: it is never offered).
  An agent chat keeps its device's own tools.
- **Tool results are kept short without losing anything.** Every tool result the model gets
  starts with an id (`[#0001]`); a read says its lines and a short hash of the file; reading an
  unchanged file again, or repeating a listing or a search with the same answer, gives a one-line
  pointer to the earlier result instead of the content twice; a long output keeps its beginning
  and end; an edit answers with its diff. Every result is stored with the chat, whole, and the
  new `recall` tool shows any of them again. Nothing already sent to the model is ever changed, so
  the prompt cache keeps working. Compacting a chat adds a table of the files read in the turns
  it summarises (the newest read of each file, at most 50); a reply that was regenerated or
  deleted takes its reads with it.
- **A chat reads its instruction files.** `AGENTS.md` (or `CLAUDE.md`) files are collected from the
  top of what the chat may see down to its starting directory, outermost first, and sent with the
  system prompt — only ones the chat can read. The model is also told which paths it can reach and
  how, and where relative paths start.
- **Skills are files.** A skill is `skills/<name>/SKILL.md` in the file tree — in your
  `/home/<you>/skills/`, the organisation's `/skills/`, or a `skills/` directory on the way to the
  chat's starting directory, the nearest winning a name. `skills_list`, `skill_view` and
  `skill_manage` work on those files (a new skill goes to your own skills directory; revert puts
  back the file's previous version), and editing the file works too. A new skill's name is
  lowercase letters, digits and `-`, up to 64 characters; a `SKILL.md` made elsewhere is offered
  under any one-line name up to 64 characters, and found ignoring case, with `_` and spaces read
  as `-`.
- **The library, reports and the canvas are gone; their work is files now.** Knowledge bases and
  their documents, notes, the database skills, reports, sharing of library items and the canvas
  panel with its scratch document are removed — their pages, the *Library* entry in the sidebar,
  the `@` menu's library section, the composer's *Knowledge* option, the chat settings' knowledge
  boxes, the `/knowledge`, `/notes`, `/skills` and `/reports` commands, the tools
  (`knowledge_search`, `knowledge_get`, `notes_search`, `note_view`, `note_manage`, `report_*`,
  `scratch_write`), their permissions and model switches, and the old library index with its
  rebuild card. Notes, reports and documents are files in the file tree, searched with `search`.
  A schedule that files a report now writes it as a Markdown file in `/home/<you>/reports/`.
  Memories stay as they were (Settings → Memory; the forms post to `/api/memories`), and `/mcp`
  serves the `memory` tool alone. `/api/v2` chats no longer carry `knowledge_bases`, and the
  stream has no `canvas` frame. A helper sent from a chat now sees exactly the files its chat
  does.
- **Organisations.** Every account belongs to one organisation, with a role there (owner, admin or
  member) beside its role on the instance. An existing account, and one made without saying
  otherwise, is in the first organisation; whoever sets an empty instance up runs the instance and
  owns that organisation.
- **An organisation can be narrowed to some of the instance's models.** With no list, it sees every
  model the instance offers, as before; with one, its people see only those (and still only the
  ones their groups may use). (The screen for it comes later in the beta.)
- **No open sign-up.** The *Registration* switch and `LEMBAS_ALLOW_SIGNUP` are gone, and so is the
  *Create one* link on the sign-in page. Past the first run, an account is made by an
  administrator or from an invite: a single-use link into an organisation that expires (a week by
  default) and can be tied to one email address. (The invite screens come later in the beta.)
- **A blob store.** File contents live in a second database beside the first,
  `lembas-blobs.db`, by content hash and in parts of about 4 MB, so the same file stored twice is
  kept once and nothing reads a large file whole. A backup takes it with the main database (see
  *Backups*).
- **A file tree per organisation**, kept in the database: `/home/<you>/` for each person (private
  to them), `/skills/` readable by everyone in the organisation, and directories the
  organisation's admins make. Every write is a version recording the chat and message that made
  it; deleting moves to a trash that can be restored from; a rename moves everything below it.
- **Access to the tree is decided in one place**, at four levels — none, read-only, read/write
  (stage), read/write (commit). A person has what they own and what is shared with them, a person,
  a group or the whole organisation; an instance administrator gets nothing for being one. A chat
  can be narrowed further by its data group's preset and floor and its own grants, and a directory
  granted under one that is not is reached only by its full path. Refusals use the wording of the
  harness spec's file system contract, so the server and the CLI say the same thing.
- **Upgrading from 1.x sets the old database aside.** The first start of 2.0 renames a 1.x
  `lembas.db` to `lembas.db.old` (its `-wal` and `-shm` with it, after a checkpoint) and starts on a
  new, empty database; nothing is carried over by itself. `uploads/`, the secret key and `backups/`
  are not touched, so going back is installing 1.7.x and renaming the file back. If
  `lembas.db.old` already exists and the database is still 1.x, the server refuses to start and
  says why, rather than overwrite either.
- **Import from 1.x** (Administration → Import, or `lembas-server import old`). The library in
  `lembas.db.old` comes across as files in the chosen person's home, under
  `/home/<person>/Imported/` — private to them until they share or move them: a Markdown
  file per note and per report, `Skills/<name>/SKILL.md` per skill (its 1.x revisions become the
  file's earlier versions), and `Knowledge/<base>/` with each document's original upload when it
  is still in `uploads/`, its extracted text otherwise. The old file is only read (through a
  private copy); running it again skips what is already there; with several 1.x accounts each gets
  a folder of its own. Memories come across only when asked for, into the 2.0 account with the same
  email address. A report names everything taken and everything left, with the reason.
- **Import an administrator's export** (Administration → Import, or `lembas-server import admin
  FILE`): the file 1.7.3 writes brings connections (an `anthropic` kind becomes the Anthropic type,
  a known cloud host its own type, anything else OpenAI-compatible; the provider name is kept),
  models (prices in money, settings, and capabilities as an override only where they were set by
  hand), data groups, speech, web search and image settings, image workflows, custom tools and MCP
  servers. The passphrase is checked before anything is written; keys the file left out are
  listed to be typed in again; what 2.0 has no place for is named and dropped. Memory import
  (Settings → Memory) and this share one reader.
- **Backups.** A backup is now one archive, `lembas-<time>.tar.zst` (`.tar.gz` without the new
  `backup` extra): both databases taken with SQLite's online backup and checked, `uploads/`, and a
  manifest with every file's size and SHA-256 — and the secret key, encrypted under a passphrase,
  when one is given. Administration → Backups lists them, backs up now, downloads, deletes, runs
  them daily at a set time (keeping the newest of each of the last N days and N weeks), and
  restores: an archive picked or uploaded is checked (manifest, checksums, `integrity_check`, same
  major version) and staged, and put in place at the next start, the data it replaces moved to
  `pre-restore-<time>/`; stored keys are re-encrypted when the backup carried its own secret key.
  `lembas-server backup` writes the archive, `lembas-server restore FILE` stages one, and
  `LEMBAS_BACKUP_DIR` puts backups on another mount.
- **The container image** installs the `backup` extra, and `.dockerignore` keeps a 1.x
  `lembas.db.old`, backups and a staged restore out of an image built from a working directory.
- **Credits, plans and the monthly token budget are gone.** So are the *Credits* admin page, the
  balance in the chat header and in Settings, the *Grant credits* permission, the `admin:credits`
  token scope, the *Tokens a month* group limit, and every refusal that came from them — in a chat,
  through `/v1` (no more 429 for a used-up month) and for speech and pictures. Nothing is refused
  for what it costs.
- **What a reply cost is kept on the reply.** A model's prices (Admin → Models) are plain money in
  the instance's currency (USD unless set; one setting for now); when a reply is written, its cost
  and the price applied are stored with it, so changing a price never rewrites old replies. A
  chat's usage panel shows the total. Usage is counted per person, month and model — tokens in,
  cached and out, requests, pictures, speech and cost — for the usage figures; `/v1/usage` reports
  the month's tokens and `cost_money`, with the old credit fields null.
- **A search index over the file tree and your chats.** Files are indexed when they are written —
  text, Markdown, code, JSON, and the text of PDFs and Word (`.docx`) documents, up to 25 MB — and
  chat messages when they are complete. Content is indexed once per content hash, so a copy, a
  rename or a move costs nothing. A `.lembasignore` in a directory leaves paths out the way a
  `.gitignore` does (`*` alone switches a directory off). Text is cut into pieces of at most 480
  tokens by the harness spec's rules, so an embedder that refuses long input no longer loses a
  document; a piece it still refuses is cut in two, never dropped.
- **Search fuses keywords and meaning, then asks access.** Keyword matches and, with an embedding
  model, the nearest pieces by meaning are fused by rank; only then is each hit checked against
  what the person, or the chat, may read, so a file outside a chat's grants is never found from it,
  not even as a count. A chat is found by its owner. The meaning half needs `sqlite-vec` (pinned
  to 0.1.9 in the `search` extra, which a standard install has); without it, or without an
  embedding model, search is keywords only and no vectors are written.
- **Link protocol 3, and only 3.** A LLeMbas CLI links to a 2.0 server only as 2.0: discovery
  (`/.well-known/lembas.json`) says `protocol: 3` and `protocols: [3]`, so a 1.x CLI refuses to log
  in and says which side to update, and a link whose hello says 1 or 2 is closed with 4400 and
  *update LLeMbas CLI to 2.0*. An agent chat runs on the chat's model and no other: the *answered
  on its own model* note is gone, and a device that cannot use the chat's model refuses to start
  the session, which the reply shows as its error.
- **`/mcp` is the memory and nothing else.** It offers the `memory` tool and a resource,
  `lembas://memory` — the entries as the memory block of a system prompt shows them, which a
  LLeMbas CLI reads as a session starts. Both answer within the chat's data group when a call
  names the session it is made for, and refuse a session that is not the device's. A token needs
  the new `memory` scope; `library`, its old name, still counts as `memory` but is no longer
  offered for a new token.
- **The app sees the `show` card: `/api/v2` is version 2.** A tool call in `/api/v2` may carry
  `show` — the file's path, name, kind, language, line, ids, hash, size and type, never its text
  (a device's card adds `source` and `shown_id`) — and `GET /api/v2/chats/{chat}/show?node=…`
  (`&version=…`) or `?shown=…` returns the file as plain text, at most 1 MB, checked again against
  what the chat may reach now; an HTML file comes back as its source. Because chats lost
  `knowledge_bases` and the stream its `canvas` frame, the app API's version (`app_api` in
  discovery, `api` in `/api/v2/me`) is now 2; an app written for 1 should be updated with this
  server.
- **A linked device can show you a file.** The CLI's `show` tool, in an agent chat, sends the
  file (up to 1 MB); it is kept with the chat and drawn as the same card a chat's own `show`
  makes, and read back as plain text only. Deleting the chat deletes it.
- **Prototypes are gone; `show` replaces them.** A model no longer answers with a live HTML page
  in a fenced `prototype-*` block: the renderer draws such a block as ordinary code, and the
  prototype panel and its route, the *Prototypes* settings under General, the *See prototypes*
  permission, the chat's prototype switch and the prompt's prototype guidance are removed. A
  device is sent no `prototypes` with its session any more. A file to look at is opened with
  `show`; an HTML file a linked device shows is drawn in a sealed frame that reaches neither the
  instance nor the network.
- **A connection has a provider type.** OpenAI-compatible (a server of your own: llama.cpp,
  llama-swap, vLLM, Ollama, LM Studio), OpenAI, Anthropic, DeepSeek or OpenRouter, replacing the
  *OpenAI dialect / Anthropic* choice (an Anthropic connection stays Anthropic, a connection to
  a known cloud address becomes its type). The type decides what used to be guessed or set by
  hand: the default address, where a reasoning effort is sent (OpenAI's `reasoning_effort`,
  OpenRouter's `reasoning`, nothing for DeepSeek, both forms for a server of your own), how cached
  tokens are read (DeepSeek's cache hits now count), and whether the provider reports what a reply
  cost. This holds for `/v1` too.
- **Detect.** Paste an address on the Connections page and press *Detect*: a known cloud address
  is recognised without a request; anything else is asked, at that address only, what it is —
  llama-swap, llama.cpp, Ollama, LM Studio or vLLM — and the form is filled in (llama-swap: one
  model at a time, unloaded with `/unload`).
- **Capabilities are detected, and what you set wins.** On every refresh a model's context
  length, vision, tools, reasoning and embeddings are read where the provider says (llama.cpp's
  `/props`, Ollama's `/api/show`, LM Studio, OpenRouter's model list, vLLM's and Anthropic's
  lists). The model page shows what was detected beside a choice of *As detected*, *Yes* or *No*
  for each, and a context length typed there; a choice made by hand is never overwritten.
- **Connection health.** Every enabled connection is checked every few minutes, and on *Test &
  refresh*: a dot on the Connections page says answering, loading a model (llama-swap starting
  one), unreachable, or answering with an error, with the time it took.
- **The provider's balance.** *Check balance* reads what DeepSeek or OpenRouter says is left, or,
  with an optional billing (admin) key, what OpenAI or Anthropic says was spent in the last 30
  days. It is shown as the provider's own figure, never mixed into the instance's estimate, and
  nothing a reply does waits for it or fails on it. The billing key is encrypted and shown masked.
- **A currency per connection.** A model's prices are in its connection's currency (USD unless
  set), and a reply's cost is in it; the General setting is now the default for a new connection
  and the currency of speech and picture prices. Where the provider reports a reply's cost itself
  (OpenRouter), that figure is kept instead, marked as the provider's. Costs in different
  currencies are never added together: `/v1/usage` gives the month's cost per currency
  (`month.cost_by_currency`), and `month.cost_money` only when one currency is involved;
  `/api/v1/admin/usage` names each row's currency (none, and no amount, for a row in which two
  currencies met) and lists the exact amounts per account, month and currency in `costs`; and an
  account's page under Administration → Users shows the month's cost, one amount per currency.
- **Fallback models, opt-in.** A model can list fallback models, in order. When its connection
  cannot be reached, times out or answers with a server error before anything was written, the
  next one answers instead — never after Stop, never for a refused request, and only models in the
  chat's data group. The reply opens with a line saying which model answered, and the message
  records that model and its cost. The line is kept with the reply's details, not in its text, so
  it is never sent back to the model in later turns. With none listed nothing changes.
- **One approval signs in an app and its agent.** A device login may ask for a second set of scopes,
  `agent_scope`, beside `scope`: the approval page shows both, and the one approval hands over two
  tokens — the app's and the agent's (the LLeMbas CLI the desktop app runs beside itself). A login
  that asks for one token gets one, as before; discovery says a server can do this
  (`login.device.agent_scope`).
- **An app is not a device.** A token with the `app` scope and not `link` (the Android app, the
  desktop app's window) is listed under *Apps* in Settings → Devices, and never among the devices
  an agent chat can be started on — on the page or in `/api/v2/devices`.
- **The apps can browse your files.** `GET /api/v2/files?path=` lists a directory and
  `GET /api/v2/files/content?path=` returns a file's text (up to 1 MB; an HTML file as its source;
  anything that is not text, or larger, with `withheld` and why), both through your own access to
  the tree, as the file pages are.

## 1.7.3 — 2026-10-27

The first long-term-support patch: export what you want to keep before 2.0, and updates that stay on 1.x.

### Added — for the move to 2.0

LLeMbas 2.0 starts on a new database and does not carry a 1.x one over. 1.7 is a long-term-support
line, and its patches may carry the tools for exporting what you want to keep; nothing else new.

- **One export format.** Every export is a JSON file that says what it is (`lembas-export`, format
  version 1), which LLeMbas version wrote it and when, and lists its items each with a type and a
  name -- so an importer can report exactly what it took and what it could not. Keys and other
  secrets are never in the file in clear: with a passphrase they are encrypted (scrypt and Fernet,
  a new salt per file); without one they are left out, and the file lists each one left out.
- **Export the configuration** (Admin → Updates → *Export the configuration*): connections and
  their models (with prices, kinds and every per-model setting), data groups, speech-to-text,
  text-to-speech, web search, image generation and the other service settings, image workflows,
  custom tools and MCP servers, as one file. Give a passphrase to take the keys along. The same from
  a shell: `lembas-server export admin --out FILE [--passphrase-env VARIABLE]`, which writes the
  file readable by its owner only and names every key it left out.
- **Export your memories** (Settings → Memory → *Export*): your memories as a file, each with its
  data group, all of them or one data group's. Only ever your own. For somebody who cannot reach the
  page, an administrator can run `lembas-server export memory --user EMAIL [--group GROUP] --out
  FILE`.

### Fixed

- **Updates stay on the major version that is running.** The stable channel, the Update button,
  automatic updates (including *Every release*) and `deploy/update.sh` now offer and install only
  releases of the running major version: a 1.x installation is never moved to 2.0 by itself, because
  2.0 does not carry a 1.x database over. When a newer major is out, the Updates page says so in one
  line; moving to it is done by hand.
- **An update helper installed by an older release is caught.** The helper runs its own copy of
  `deploy/update.sh`, installed by `install.sh`, and an older copy would still take the newest
  release whatever its major. The Updates page now says when the copy is an older one, and nothing
  is requested through it while a newer major exists. Re-run the installer with
  `INSTALL_UPDATE_HELPER=1` to replace it.
- **An agent chat no longer says the device "answered on its own model" when it did not.** The
  notice compared the model this server sent with the one the device reported, letter for letter
  and only in one form; a LLeMbas CLI that names the model another way -- without its connection
  prefix, without the provider, or in another case -- got the notice on every new session although
  it was running the chat's model. The two are now compared by the model's own name, and the notice
  appears only when the device really runs something else.

## 1.7.2 — 2026-10-25

A device that logs in again stays one device.

### Fixed

- **Logging a device in again left an offline twin holding its agent chats.** A device signing
  its old token out may now name the new one (`DELETE /api/v1/token` with
  `{"replaced_by": "<new token>"}`): when that is a live token of the same account, the old
  device's agent chats, and any deletes still waiting for it, move to the new one before the old is
  revoked, and the audit log says how many. LLeMbas CLI 1.5.2 does this on every login again.

## 1.7.1 — 2026-10-23

The chat window, fixed where it was felt: thinking that closes when it ends,
text that is not held back, a Stop that stops, a page that keeps up with a long
reply, and a message box that shows what is typed.

### Added

- **Markdown is highlighted as you type**: headings, emphasis, code, links,
  lists and quotes are coloured in the message box, every character kept
  where it is.

### Fixed

- **A thinking block closes when the model moves on.** It stayed animated --
  "Thinking…" -- until the round ended, and in the last round until the whole
  reply had; only then did it say "Thought for …". It now becomes a step of its
  own the moment the model starts writing prose or a tool call, and says how
  long it thought there and then. The same on a linked device's chats.
- **The last words before a tool call are no longer held back.** The text was
  complete, but its last few characters waited in a buffer -- against a
  `<think>` tag that was never coming -- until the tool call appeared. Only a
  tail that could really start a tag is held now (the LLeMbas CLI's rule).
- **"Preparing …" while a tool call is written.** A long `file_write` streams
  its whole file before anything runs, which looked like a hang; the status
  line now says which tool is being prepared, as the CLI does.
- **Stop stops at once.** It was read only between streamed chunks, so it did
  nothing while there were none: a long prompt still being processed upstream,
  a tool running (a picture: minutes), a compaction -- and after a tool round it
  even started the next request first. Stop now cancels whatever the reply is
  waiting on and closes the upstream connection, and the chat's title is not
  asked of the model after a Stop. A linked device is waited on for two seconds,
  not ten.
- **Editing or regenerating while a reply is written stops it and goes ahead**,
  instead of refusing with "wait for it to finish, or stop it".
- **A long reply no longer lags the page.** The text still being written was
  rendered whole -- code highlighted, languages guessed -- many times a second,
  on the one event loop that also answers Stop. Finished paragraphs are now
  rendered once, code is highlighted when the reply ends, and a busy follower
  backs off.
- **A sent message can no longer appear twice.** The transcript's five-second
  check for new messages could cross a send and add the same message and its
  streaming reply a second time -- both copies animating. Every swap into the
  thread now drops a message already on the page, the check stands down while a
  message is being sent, and a double submit is dropped.
- **Stop shows it is stopping.** The button stayed live and nothing changed
  until the reply ended; now it says "Stopping…" at once, and says so if the
  stop could not be sent.
- **The page does less on every streamed frame.** Handlers meant for whole
  page changes -- the composer's repaint, code-copy buttons, the steps' open
  state, read-aloud -- ran many times a second during a reply. Typing while a
  reply streams no longer lags.
- **The message box shows what is typed.** The interface font draws
  programming ligatures, so `...`, `###`, `->`, `==` and others turned into
  single merged glyphs as they were typed -- the first dot seemed to vanish.
  Ligatures are off everywhere: what you read is what is there.
- **The microphone says what went wrong**: permission refused, no microphone,
  or the microphone in use by another program, instead of one sentence for all.

- **An image counts by its dimensions** in every estimate the server makes. It
  counted nothing in the context estimate, and on an Anthropic connection its
  base64 counted as text -- a large picture left no room for the answer, which
  was cut off at a thousand tokens.

## 1.7.0 — 2026-10-21

A third door for a native app: the chats as JSON (`/api/v2`), behind a bearer
token with the new `app` scope -- what LLeMbas for Android signs in to. And four
fixes from a review: downloads of non-Latin-1 filenames, client addresses behind
Docker, `/v1` streams left early, and upload chat tags.

### Added

- **`/api/v2`, the app's API.** The chat list, a chat with its settings and its
  messages (paged by id), sending, stop, edit, regenerate, continue, carrying
  out a plan, answering a card, changing and deleting a chat, attachments,
  models, folders, linked devices and the account -- JSON in and out. Every
  route calls the core its page twin calls, so sending, queueing, the reply
  limit, model access, data groups and the card rules hold through either door
  alike. A token never reaches what its holder could not do in a browser.
- **The reply as JSON server-sent events**
  (`/api/v2/chats/{id}/messages/{id}/stream`): the same `Generation` the page
  follows, under the same rules -- every frame carries the whole block, a
  frame is sent only when it changed, a round closes in one frame -- so an app
  that reattaches late gets the whole state at once. Steps carry the model's
  markdown as written, never HTML.
- **The `app` scope**, grantable through device login like `models`, and
  `app_api` in `/.well-known/lembas.json`.
- **Push for the app.** `/api/v2/push` registers a UnifiedPush endpoint --
  the same Web Push the browsers get, sent by the same code -- under the
  app's token; signing the token out removes it (a new nullable
  `push_subscriptions.token_id` column).
- **A card waiting with nobody watching is announced**, as a finished reply
  already is. The notification carries the chat and the card, so the app can
  answer an approval from it.
- **The contract as files**: `tests/fixtures/api_v2/` holds one example of
  every shape and every stream frame, written by the serialisers themselves;
  the app pins them and parses each in its own tests.

### Changed

- The page's chat routes now call shared cores (`write_turn`,
  `start_new_chat`, `rewind_to`, `regenerate_reply`, `plan_prompt`,
  `answer_card`, `apply_chat_update`, `delete_chat_now`). No behaviour changed.
- **`LEMBAS_FORWARDED_ALLOW_IPS`** names the reverse proxy whose
  `X-Forwarded-For` is believed (addresses or networks; default `127.0.0.1`).
  `docker-compose.yml` sets it to the Docker bridge, so a proxy on the host is
  believed there too.

### Fixed

- **A file whose name is not Latin-1 can be downloaded.** `Žiadosť.pdf` was
  stored and then answered every download with a 500 -- in the web UI, the
  library, `/v1/files` and the app. The header now carries an ASCII name and
  the real one (`filename*`, RFC 6266), and a `"` in a name no longer breaks it.
- **Behind Docker, clients no longer share one address.** The proxy's
  connection came from the bridge's gateway and its forwarded headers were
  ignored, so every client looked alike: twenty failed sign-ins from anyone
  throttled sign-in for everybody, and a few device logins anywhere blocked
  every CLI, Desktop and app login for ten minutes.
- **A `/v1` stream the client left before its first byte is released.** Its
  in-flight count stayed up -- toward the account's reply limit -- until a
  restart, and the upstream connection stayed open.
- **An upload is tagged only with the uploader's own chat.** A chat id from
  the request naming somebody else's chat is dropped, and the agent prompt's
  list of a chat's files names only its owner's.

## 1.6.0 — 2026-10-20

Background helpers and a device's jobs, shown and controllable: one registry of
background work with the LLeMbas CLI (harness spec 2.0.0, from LLeMbas CLI 1.5.0).

### Added

- **A helper can run in the background.** `task` with `background: true`
  returns at once with a job id (`job1`, `job2`, ... per chat) and the reply
  carries on; when the helper ends, the chat is told with the spec's note --
  taken in at the reply's next step while it is answering, as a turn of its
  own when it is idle. `job_list`, `job_output`, `job_stop` and `job_send` work
  on that id across requests and restarts (a new `chat_jobs` table; nothing
  else in the schema changed), and `job_send` to a finished helper starts it
  again with its context intact. A helper has `report`, the one way to message
  the main agent before its final reply. The two kinds of helper are the spec's
  built-in agents: `explore` (reads and searches, the default) and `general`
  (the full toolset the chat allows; what `write: true` meant).
- **A Jobs strip above the composer** lists the chat's running jobs -- the
  helpers started here, and on an agent chat the commands and subagents the
  linked LLeMbas CLI runs in the background -- with how long each has run and
  a Stop button. A subagent row opens a side panel with the helper's
  transcript, followed live, and a box to message it. On an agent chat the
  strip reads the device's jobs (`_lembas/jobs`), stops over `_lembas/job/stop`
  and messages over `_lembas/job/send`; it polls itself while it has rows and
  is refilled from the device's status when a reply is taken up again.
- **A device's subagent and job events are shown.** A `task` block nests its
  subagent's tool lines; a job's note taken into a turn is shown where it went
  in; a harness-started turn (a job's note, a subagent's message) is mirrored
  into the chat as the harness's turn, not as typed in the terminal; an
  approval asked by a background subagent says which (`explore (job7) asks`).
  A device's `retract` now drops the step's partial text, as the CLI's own view
  does, and a plain notice reaches the status line.
- **`purpose`** -- the spec's one-sentence account of what a call is for -- is
  offered on every tool whose spec takes it (`task`, `memory`, `skill_manage`,
  `web_fetch`) and shown beside the call in the transcript; the tool itself
  never sees it.
- The prompt lists the models a helper may run on (`task`'s `model`), as its
  description promised; `{{agents}}` names the two kinds.

### Changed

- **The helper's preamble is the spec's** (`blocks/subagent.md`, the
  `core.subagent` fragment), with the agent's instructions
  (`prompts/agents/explore.md`, `general.md`) filled in -- the same words the
  LLeMbas CLI gives its subagents. The planning and asking bullets of the
  shared system text are the spec's `plan_tools` and `ask_tool` variables now,
  and a helper's prompt drops both: it has no plan tools and nobody to ask.
- `task`, `job_send` and `report` are the spec's `interact` risk class: a
  message to somebody, asked about in no mode and not withheld in Plan mode.
- The "Background job" badge says what wrote the turn: "Scheduled run" in a
  schedule's chat, "From the main agent" in a helper's, "Background job" for a
  job's or a helper's note.
- The admin Helpers card describes helpers as they are now.

### Fixed

- **Stop never reached a helper.** The reply's Stop was read only between
  streamed chunks, and a reply waiting on `task` streams nothing; the wait
  watches it now and stops the helper it was waiting on. A background helper
  is not stopped by its parent's Stop, as the CLI's are not.
- A helper's chat cut off by a shutdown was taken up again at the next start
  with nobody waiting for its answer. It is skipped.
- The helper setting `max_completion_tokens` was never enforced; it is the
  helper's own ceiling now, checked between rounds like every other.
- A helper stopped at its time limit showed as a success; its block says
  `stopped` and gives its account (which job, which agent, how many calls, how
  long, and that it was stopped at the time limit).
- The capacity rule ignored `Connection.one_model_at_a_time` and compared a
  helper only with the parent's model; it compares with every running helper's
  as well, as the spec says.
- The `{{agents}}` variable said "general (default)"; the hint on the helper's
  fragment named `subagent_run`.
- "Run now" on a report schedule held the browser's request open until the
  report was filed -- up to an hour. It fires and returns.
- Dead code and stale words: the unreachable crowd branch in the queue frames,
  the `allowed` set `_authorise` returned empty and nobody read, the `messages`
  schedule target the runner's docstring still listed, docstrings naming a
  `jobs.wake` that is gone, the orphaned "jobs chip" comment in the composer,
  and `Chat.rewound_at`, which was written and never read (the column stays;
  the schema is additive).

## 1.5.1 — 2026-10-20

A false warning on the update page.

### Fixed

- **"This is tagged 1.5.0 but reports version 1.3.0" during an update.** The
  note that flags a tag cut before its version bump compared the tag the
  checkout is at with the version of the running process. While an update is
  being installed the checkout is already at the new release and the old
  process is still answering, so a page drawn in that moment blamed a release
  that was cut correctly. It now compares the tag with the version the code at
  that tag declares, and names that version.

## 1.5.0 — 2026-10-20

Memory, notes and skills are the LLeMbas CLI's tools: one harness for both.

### Changed

- **The library's tools are the harness spec's.** A model is sent `memory`,
  `notes_search`, `note_view`, `note_manage`, `skills_list`, `skill_view` and
  `skill_manage` -- the same tools, words and shapes the LLeMbas CLI sends,
  with this server's facts filled in where the two differ (one memory about
  the person; notes without a scope; skills without supporting files or
  categories). A CLI linked to this instance now sees these names too.
- **Memory changes in one call.** `memory` takes one change or a list --
  add, replace, remove -- applied together or not at all, with the size limit
  (4,000 characters) checked on the result, so a full memory is made room in
  and added to in the same call. An entry over 400 characters is refused with
  the reason rather than cut short. A refused change shows the model what is
  remembered, so it can consolidate. A replaced memory keeps its place.
- **Skills change in one call** with `skill_manage`: create (from a
  description and instructions, or a whole SKILL.md), patch part of one,
  delete, or revert to the version before the last change -- a second revert
  undoes the first. Every operation is checked before any is written, so if
  one fails none is kept. `skills_list` lists them.
- Calls under the former names -- `memory_add`, `memory_forget`, `notes_get`,
  `notes_create`, `notes_edit`, `notes_delete`, `skill_get`, `skill_create`,
  `skill_edit` -- still run, as the call they mean, in a chat and over `/mcp`.
  Old transcripts keep their labels. The prompt texts name the new tools; an
  administrator's own wording, where there is one, is kept as it is.
- An approval card for a memory, note or skill change says what changes.
- Harness spec 1.2.0 (LLeMbas CLI 1.4.0): note and document ids are strings
  on both sides, and the conformance areas `fill`, `narrow` and `memory` run
  here as they do in the CLI.

## 1.4.0 — 2026-10-20

The web tools are the LLeMbas CLI's: the same names, words and behaviour.

### Changed

- **`fetch` is `web_fetch` now**, the harness spec's tool, word for word as
  the LLeMbas CLI sends it. A page comes back as markdown -- headings, lists,
  links with their full addresses, code blocks -- instead of flattened text,
  so a model can follow a link it read, and the page's own `<main>` or
  `<article>` is taken when it has one, with navigation, footers and scripts
  left out. A long page is read in parts: 20,000 characters at a time, with
  the offset to continue from at the end of each part, where before
  everything past the first part was cut off. A model or a transcript still
  calling it `fetch` gets `web_fetch`.
- **`web_search` is described the spec's way** too, and points a model at
  `web_fetch` for the pages that matter.
- **`/api/v1/fetch` returns markdown**, so a CLI reading pages through this
  instance gets the same page it would get by fetching it itself.
- Nothing to change in settings: the switch, the permission (`tools.fetch`)
  and the per-model tool settings are the ones they were. Harness spec 1.1.0.

## 1.3.1 — 2026-10-20

Reading a page after a search works again.

### Fixed

- **Fetch, notes, memories and skills answered "Unknown tool".** Since 1.3.0
  every call a model made to `fetch` was renamed to the harness spec's
  `web_fetch`, a tool this server does not have, so a model that searched and
  then tried to read a result failed every time, whichever model it was. The
  same happened to `notes_get`, `notes_create`, `notes_edit`, `notes_delete`,
  `memory_add`, `memory_forget`, `skill_create`, `skill_edit` and `skill_get`.
  A former tool name is now converted only when the name was not offered and
  the tool it stands for was; `todo` and `subagent_run` still convert as
  before. Harness spec 1.1.0, unchanged.

## 1.3.0 — 2026-10-18

Every chat can keep a plan, and any chat can be put into Plan mode.

### Added

- **A plan in every chat.** A model working on anything with a few steps now
  writes a plan -- what it found, what the work is for, and the work as phases
  of tasks -- and keeps it current as it goes: a task is marked as started,
  done or dropped, and work the plan did not foresee is added. The plan sits
  above the message box while anything on it is still to do (the task in
  progress, what is left, the last two done), with the whole card one click
  away, and the reply that last changed it shows the card as it stood then.
  It is the same plan the LLeMbas CLI keeps, from harness spec 1.1.0
  (`plan_write`, `plan_update`), and it is put in front of the model on every
  turn while it exists.
- **Plan mode for an ordinary chat.** The mode control beside the message box
  offers Normal or Plan. In Plan mode the model reads and searches freely but
  changes nothing -- every tool that writes to the library or runs something
  is withheld -- and finishes by putting its plan to you on a card: approve it
  and the model carries on and carries it out in the same reply with the tools
  it was planning for, send it back with what to change, or put it aside.
  Approval ends Plan mode. Agent chats keep their four modes.
- **Carry out this plan** on the plan card of any chat, not only an agent
  chat; an agent chat still switches to Edit, a chat just leaves Plan mode.
  The panel's own card carries the plan out as it stands now.
- A reply that says nothing -- only thinking, or cut off at the output limit
  -- is asked to continue, at most twice in a row, rather than ending with
  nothing said. The transcript shows that it was asked.
- Time, tool-output and output-token budgets for one reply, from the harness
  spec's defaults (15 minutes of work, 1 MiB of tool output, 200,000 tokens
  written); past one, the tools are withdrawn and the model is told which
  limit it reached and asked to answer from what it has. Time waiting for
  you does not count. They are instance settings (`loop_wall_seconds`,
  `loop_output_bytes`, `loop_completion_tokens` under General; 0 turns one
  off), without a field on the settings page yet.
- **An agent chat knows what the instance knows.** A chat on a linked device
  now gets the instructions it would get here -- the chat's own, its folder's,
  its model's or the instance's -- your personalization, which knowledge
  bases it may search, and whether it may write a prototype, sent with the
  session and again on every message, so a change made here reaches the
  device's next turn (harness spec 1.1.0, `_meta.lembas` on `session/new`
  and `session/prompt`; a LLeMbas CLI 1.3.0, each piece on its capability).
- **The plan card for a device chat is the structured one.** A device sends
  its plan as the same structure a chat here keeps, so the panel above the
  message box, the card in the reply and the plan it asks you to approve
  are drawn as for any other chat -- and a chat taken up again after a
  restart draws the plan the device has, without waiting for the next
  change. A device without it still sends its todo list, shown as before.
- **Edit and Regenerate on agent chats**, with a LLeMbas CLI 1.3.0: the
  device drops the turn and every later one from the conversation its model
  reads, and the edited or repeated message goes as a new turn. The files
  the device changed are not undone, and the controls say so. With an older
  CLI, or while the device is not linked, the controls stay hidden as before.
- **The library scoped to the chat for a device.** A library call the CLI
  makes for an agent chat names the chat's session (`_meta.lembas.scope` on
  `/mcp`), and is answered within that chat's scope -- its knowledge bases
  and its data group, exactly as the chat loop's own tools are; a session
  that is not that device's is refused. Without a scope, the account's
  default group, as before.

### Changed

- **The prompt is the shared one.** The core of what every model is told --
  tone, conventions, how to do a task, how to use tools, the plan -- is now
  the harness spec's own system text, word for word the one the LLeMbas CLI
  sends, as one fragment on the prompts page (*How to work*) in place of four.
  The prototype and library guidance and the four mode texts are the spec's
  too. An override saved for one of the replaced fragments is shown beside
  its successor as an older override to review; it is no longer sent.
- `knowledge_search` takes a `base`: the name or id of one knowledge base to
  search among those the chat may, with the available ones named back when
  there is no such base. `knowledge_get` takes an `offset` to read past what
  the first call returned. Both carry the spec's descriptions.
- `ask_user` is the spec's: a `why`, up to four questions each with a short
  header, options with the one the model recommends marked, and the answers
  handed back one line per question. The card shows the header and the
  recommendation.
- The helper tool is called `task`, with the spec's arguments (`description`,
  `prompt`, and `model` to run it on another model you may use); the old
  name `subagent_run` still works, as does `todo`, which becomes a plan with
  one phase. `agent` and `worktree` are accepted and ignored, with a note in
  the result.
- The plan is kept on the chat, with a snapshot on each message that changed
  it; a chat from before this release shows the plan it had.
- Carries harness spec 1.1.0 (planning, the shared system text, four new
  conformance areas), run here against `plan`, `prototype` and
  `library_scope`.
- The browser's own colour before any stylesheet has loaded is read from
  `tokens.css` rather than repeated by hand in the page.
- The sidebar's header makes room for a phone's status bar the way the chat's
  header does, and the composer's minimum height no longer applies on a phone,
  where there is no sidebar footer for it to line up with.
- Every size a stylesheet named as a bare number -- menu, dialog and chip
  widths, list and block heights -- is a token now, and every inline `style`
  attribute in a template is a class. The service worker precaches the two
  scripts it had missed, so the installed app works offline the first time.
- Dead stylesheet rules for the background-jobs panel, the talk-rules matrix
  and form, the old model picker, the admin page header, the connection card's
  header and footer, the directory row and the history sentinel are gone.
- Every event stream LLeMbas reads -- a model's reply, an Anthropic reply, a
  reply relayed through the `/v1` API, an MCP server's answer -- is read by one
  parser, which knows what servers really send: a last line with no newline
  after it, Windows line endings, a frame split over several `data:` lines,
  keep-alive comments, and frames with no blank line between them.
- A model's server failing before it had sent any of the reply -- llama-swap
  saying the model was unloaded because another client loaded a different
  one, or any other server error -- is asked once more before the failure is
  shown, since a second request usually succeeds.

### Fixed

- A reply being written no longer makes the page jump up and down. The
  transcript was scrolled by several listeners at once into a scroller that
  animated every scroll, every frame re-sent every block of the reply whether
  or not it had changed, and a round of tool calls closed in two frames with
  the text on screen twice in between. The stream now sends a block only when
  it changed, closes a round in one frame, and the page scrolls once per
  animation frame from one place. The waiting row and the metrics under a
  live reply keep one height; the metrics stay on one line and are cut with an
  ellipsis rather than wrapping; the "writing" dots do not come back after
  every tool call; a prototype shown inline keeps its height across rounds;
  and an image a model writes with `width` and `height` reserves its space
  before it loads.
- Opening a thinking or tool block while a reply streams no longer stops the
  page following the reply after the next tool call, and the open thinking
  box stays open as the block it becomes.
- A streaming error a server sends as a bare line inside a 200 reply --
  llama.cpp's way, often as the very last line -- is shown as the error it is,
  not as an empty reply; through the `/v1` API it is passed on as a proper
  error frame. An error a server frames as data is read the same way.
- A tool call whose fragments arrive without an index is assembled onto the
  right call: the one started last, not the first. A reply through an
  Anthropic connection to the non-streaming `/v1` API reassembles its tool
  calls by the same rule.

### Removed

- The prompt fragments that only an agent chat used -- the machine, the
  project listing, the project's own instructions, background commands, the
  rewind note, the helper-on-a-machine guidance -- which the linked LLeMbas
  CLI has assembled itself since agent chats moved there. They were on the
  prompts page and reached no model.
- The todo list above the composer, replaced by the plan.
- The old `POST /api/preferences/pinned-open` route, which nothing has posted
  to since 1.2.0 moved every sidebar section's state into one map. A choice
  stored under the old key is still read until the next change retires it.

## 1.2.0 — 2026-10-14

LLeMbas Desktop shows your computer as Local, and a device that allows no folders says so.

### Added

- In LLeMbas Desktop, the computer you are sitting at is shown as **Local**
  and listed first: in the new agent chat's device list, in the Agents
  sidebar's device sections and their session filter, and in an agent chat's
  header. Hover it to see the computer's name. Desktop says which computer it
  runs on, and a device of yours that signed in from that computer gets the
  label. In a browser nothing changes: every device keeps its own name. The
  label is for display only and changes nothing about what a device may do.
- When a device allows no folders at all, the directory browser in a new agent
  chat now says so, and how to add one: in LLeMbas Desktop, or with
  `lembas remote add <dir>` on that computer. Before, it said only that the
  device offered no directories.

### Changed

- Carries harness spec 1.0.1 (from LLeMbas CLI 1.2.0): a device links even before it allows any
  folder, and then says it allows none, so the web UI can tell that apart from a device that is
  not running.

## 1.1.0 — 2026-10-14

A new agent chat starts where you last worked, and more code that never ran is gone.

### Changed

- A new agent chat now starts in the directory you last worked in on the chosen
  device, rather than in the device's first allowed directory. The directory
  button opens a short list of the directories you have already used there,
  newest first, with "Browse…" at the bottom for the device's directory
  browser. On a device you have not used yet, no directory is chosen: the
  button goes straight to the browser, and the message is not sent until you
  pick one. Starting a chat inside a folder that names a directory still uses
  the folder's.

### Removed

- The server's own shell-integration code for the terminal panel, which never
  ran: a linked device's shell marks its own commands, and the panel already
  reads those marks itself. With it went the "last command" address the panel
  could never reach, and the fields of the terminal's opening message that were
  always empty or that nothing read. The terminal's Copy, Send and automatic
  modes work as before.
- The per-reply time, output and token checks for agent chats in the reply
  loop, which could no longer trigger since an agent chat's work moved to its
  device. The device enforces its own limits; the round ceiling for ordinary
  chats and helpers is unchanged.
- Unused helpers: four readers of the shared harness specification and a
  folder check the sidebar no longer calls.

## 1.0.1 — 2026-10-14

A clean-up release: code nothing reached any more is gone. Nothing changes in
use, and no stored data is touched.

### Removed

- Code left over from when an agent chat could run over SSH, which nothing
  reached any more: the executor interface and its request, result and listing
  types, the reply limits in the agent policy, and the terminal's SSH reader,
  re-keying and per-connection closing. Agent chats and their terminals run on
  linked devices, as before.
- Agent settings nothing read: the loopback switch, the command timeouts and
  output caps, the step, time and token budgets, the default allow and deny
  lists, the project listing and instructions budgets, the unfinished-plan
  nudge, background jobs and the terminal integration switch. Values already
  stored are ignored.
- The project-file half of the `@` picker, which was always empty, and the
  unused "open a file" dialog behind the canvas.
- Unused helpers across the services and models, and comments naming modules
  that no longer exist. The hint under "Most shells per person" now says what
  the setting holds open: a shell on the linked device.

## 1.0.0 — 2026-10-09

The first public release. A self-hosted web UI for language models: chats in
folders and a searchable library, tools and web search, agent chats on devices
linked through the LLeMbas CLI, accounts with credits and usage, and an
OpenAI-compatible API — packaged to run with Docker or as a system service.
