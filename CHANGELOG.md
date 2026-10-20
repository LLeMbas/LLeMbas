# Changelog

What changed in each release of the LLeMbas server. The release notes are also
at <https://llembas.eu/changelog.html>.

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
