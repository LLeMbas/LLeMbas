# Changelog

What changed in each release of the LLeMbas server. The release notes are also
at <https://llembas.eu/changelog.html>.

## Unreleased

### 2.0 (in progress)

2.0 is a new major on `dev`, released as `2.0.0-beta.N`. What is listed here is on `dev` and in no
release yet.

- **Organisations.** Every account belongs to one organisation, with a role there (owner, admin or
  member) beside its role on the instance. An existing account, and one made without saying
  otherwise, is in the first organisation; whoever sets an empty instance up runs the instance and
  owns that organisation.
- **No open sign-up.** The *Registration* switch and `LEMBAS_ALLOW_SIGNUP` are gone, and so is the
  *Create one* link on the sign-in page. Past the first run, an account is made by an
  administrator or from an invite: a single-use link into an organisation that expires (a week by
  default) and can be tied to one email address. (The invite screens come later in the beta.)
- **A blob store.** File contents live in a second database beside the first,
  `lembas-blobs.db`, by content hash and in parts of about 4 MB, so the same file stored twice is
  kept once and nothing reads a large file whole. `lembas-server backup` (and the backup before an
  update) copies it beside the main database as `…db-blobs`; keep the two together.
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
