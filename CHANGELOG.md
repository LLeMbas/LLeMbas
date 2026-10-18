# Changelog

What changed in each release of the LLeMbas server. The release notes are also
at <https://llembas.eu/changelog.html>.

## Unreleased

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

### Removed

- The prompt fragments that only an agent chat used -- the machine, the
  project listing, the project's own instructions, background commands, the
  rewind note, the helper-on-a-machine guidance -- which the linked LLeMbas
  CLI has assembled itself since agent chats moved there. They were on the
  prompts page and reached no model.
- The todo list above the composer, replaced by the plan.

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
