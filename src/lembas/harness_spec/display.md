# Display

Spec 3.7.0. **One display in every client** — the web UI, Desktop, the CLI, Android — for the
things every one of them draws: a tool call, the end of a reply that did not finish, the card that
waits for the person, and a number. `progress.md` says what a session is doing *now*; this says
what stays on the screen afterwards. The data is `loop.json` `display`; the cases
`conformance/display.json`.

Each client keeps its own look — a terminal row, a card, a chip — and its own translation of the
fixed words. What is the same is **what is said, from which data, in which state**.

## 1. A tool call's title

One rule, and one title per call: **the call's short title** — the same string in the transcript,
as `what` of `running_tool` (`progress.md` §2) and as `title` of the link's `tool_call`
(`acp.md`). It is made from the call's arguments alone, so it is known the moment the call starts
and never changes afterwards:

1. `questions` (a list, `ask_user`): its `header`s, joined with ` · `.
2. `patch` (a string, `apply_patch`): the files it names — every `*** Add File: `,
   `*** Update File: ` and `*** Delete File: ` line's path, else every `+++ ` line's path without
   a leading `b/` (`/dev/null` left out) — joined with `, `.
3. Otherwise the first of these arguments that is a string with anything but white space in it,
   in this order (`display.title.keys`): `summary`, `command`, `code`, `pattern`, `query`, `url`,
   `path`, `file_path`, `from`, `description`, `name`, `id`, `title`, `key`, `target`, `to`,
   `action`.
4. Then: trimmed, **its first line only**, trimmed again, and cut to **80 characters**
   (`progress.what_max`) — 79 and `…` — counted in code points.

A call with none of these has **no title**: the client draws the tool's name alone, and
`running_tool` goes without `what`.

The order puts what a call *does* before where: a `grep` is titled by its pattern, not its
directory; a `bash` by its command, not its description; `phone_action` by its `summary`. The
tool's own name (or the client's label for it — `Bash`, `Search files`; a client's own table) is
drawn beside the title and is not part of it.

The short title is for a call **as it is listed**. The approval card (§5) is not titled by it: a
card shows what is asked in full — the whole command, the change — and names the tool
(`acp.md` `session/request_permission`: `toolCall.title` the whole command or the tool's name,
`_meta.lembas.tool` the tool).

A tool the spec marks `block: plan` or `block: hidden` is not drawn as a call at all (§3).

## 2. A call's states

A drawn call is in exactly one of four states (`display.states`), and every client tells the four
apart at a glance — by a mark, a colour or a word of its own; never by the title, which is the
same in all four:

| State | When | What is shown beside the title |
|---|---|---|
| `running` | from the call's start until its result | a mark that it is running (a spinner, `…`); no duration is required — a client that shows one counts it from the call's start, as `progress.md` §3 writes elapsed time |
| `done` | its result came and is not an error | **how long it took** (below) |
| `failed` | its result is an error — the tool's own failure, a non-zero exit a tool reports as one, a timeout | how long it took, and the **first line of the error** where there is room |
| `denied` | it was not run: the person, a rule, the mode or the hardline refused it | **no duration**; the first line of the reason where there is room |

A call that was `running` when its turn was stopped, or whose result never came (the session
ended, the device went away), is drawn `failed` once that is known — never left spinning.

**The duration** of a finished call is the time from its start to its result, as the
implementation that ran it measured it, written the one way (`display.duration`): under a minute
in seconds with one decimal (`0.4s`, `8.4s`) — a time that would round to `60.0s` is a minute,
`1m 00s`; from a minute `1m 05s`; from an hour `2h 03m` —
`progress.md`'s elapsed form with tenths under a minute. A duration is not a locale number (§6):
it is written with `.` everywhere. A stored call whose duration was not kept shows none.

**Facts from the result** (`display.facts`) go after the title, never in its place:

- **an edit** (`block: edit`) shows **`+{{added}} −{{removed}}`** (`display.change`; the minus is
  U+2212) once its change is known: the lines its diff adds and removes. Counted from the unified
  diff by its hunks — after each `@@ -a,b +c,d @@` header, the next lines that begin with `+`, `-`
  or a space until `b` old lines and `d` new lines are used up (`\ No newline at end of file` counts
  for nothing; a missing `,b` or `,d` is 1); a `+` there is one added, a `-` one removed. Nothing
  outside a hunk is counted, so a file header never is and a removed line that itself begins with
  `-- ` always is. Several files in one diff add up.
- **a command** (`block: shell`) shows **`exit {{code}}`** (`display.exit`) when its exit code is
  known and not 0.
- **a search** (`block: search`) shows **`{{count}} hits`** (`display.hits`, `display.hit` for 1)
  where the implementation knows the count.

Anything else an implementation knows (the lines read, a job's id) it may add the same way.

## 3. The kinds of block

A tool's `block` (`tools/<name>.json`; `display.blocks`) says how its call is read, and every
client distinguishes these — at least by what it shows collapsed and what it opens to:

| `block` | Collapsed | Opened |
|---|---|---|
| `shell` | the command (the title), `exit N` | the output |
| `edit` | the path, `+N −M` | the change as a diff — a client may show the first of it without a click |
| `read` | the path (or the id) that was read | the text that came back, where the client keeps it |
| `search` | the pattern or the query, the hits | the hits |
| `web` | the query or the address | the results or the page's text |
| `task` | the task handed to a subagent | what the subagent did — its own calls, its progress (`subagents.md` §12) — and its report |
| `tool` | the tool's name and the title | the result |
| `plan` | **not drawn**: the plan card refreshes instead | |
| `hidden` | **not drawn** | |

A tool the client does not know (an MCP tool, a custom tool) is `tool`.

## 4. How `stopped` and `failed` end a reply

A turn that did not finish ends with the progress `stopped` or `failed` (`progress.md` §4). That
progress is not only the last thing on the progress line: **its line stays on the finished
reply** — the text alone, no elapsed time (`progress.md` §3):

- `Stopped: by you` · `Stopped: step limit` · `Stopped: budget` · `Stopped` (no reason known)
- `Failed: {{reason}}` — the error, one line

Every client draws it **at the end of that reply**, in the transcript, where it remains when the
next turn begins and the progress line has moved on — `failed` in the client's error colour,
`stopped` in its muted one. Nothing else says the same thing beside it: a client does not also
print the raw error or a second "cancelled" notice of its own wording. A reply with no text at all
(stopped before the first token) still gets the line, on its own.

Where the reply is shown again from what was stored — a reload, a resumed session, another client
— it is drawn again from the stored end; an implementation that stores a reply stores how it
ended (`stopped` with its reason, `failed` with its reason). A turn that finished normally shows
nothing.

## 5. What the card offers

When a session waits for the person (`waiting_for_person`), every client offers the same actions
(`display.actions`), under these words or their translation:

**An approval** (`what: approval`; `acp.md` `session/request_permission`):

| Action | Words | Answer |
|---|---|---|
| `once` | Allow once | the call runs; nothing is remembered |
| `always` | Always allow | the call runs and the offered pattern is allowed from now on — **offered only where the request offers it** (`always` absent: `permission/arity.json` `exact_only`, a `confirm: always` tool). A client with more than one place to remember it (this session, this project) may offer each |
| `deny` | Deny | the call is not run |
| `deny_reason` | Deny, and say why | the call is not run and the person's words go to the model as the reason (`feedback`) |

A client may also let the person **correct a command before allowing it once** (the answer's
`command`); what is written is judged again. Closing the card without choosing is `deny`.
The card shows what is asked about itself — the command, or the change as a diff — and the call's
`purpose` where one was given.

**A question** (`what: question`; `ask_user`, `acp.md` `_lembas/question`): the options as given,
the recommended one first and marked; several may be chosen where `multiple`; and **always a row
of the person's own — "Something else"** (`something_else`) — that takes free text and answers
`custom`. A client may offer asking back (`back`) and skipping one of several questions
(`skipped`). Closing the card is `dismissed`.

**A plan** (`what: plan`; `plan_submit`, `acp.md` `_lembas/plan`): **Approve** — together with
**the mode the work continues in** (`approve_edit`: Approve, continue in Edit; `approve_manual`:
Approve, continue in Manual; never a mode beyond what the session may have) — and **Revise**
(`revise`), which takes the person's words as feedback. Closing the card is `dismissed`: nothing
is decided.

A client that cannot offer one of these (a notification with two buttons) offers what fits and
opens the full card for the rest; it never answers for the person.

## 6. Numbers

A number a person reads is written **in the client's own locale format** — its grouping and its
decimal mark, from the platform's number formatter and the locale the client runs in (the
browser's, the phone's, a terminal's `LC_NUMERIC` / `LC_ALL` / `LANG`), not from a rule here. That is every count and
measure: tokens (`12,480` · `12 480` · `12.480`), the rate (`12.5` · `12,5`), `done` / `total` and
`percent`, hits, lines, `+added −removed`, costs and credits.

Not numbers in this sense, and written as they are everywhere: durations (§2, `progress.md` §3),
ids (`s1`, `job7`, `#12`), an exit code, a line number in a path (`a.ts:120`), a version, a port.

The texts in `conformance/` are written in the **plain format** — no grouping, `.` for the
decimal mark — which is what an implementation's text function produces when it is given no
locale; a client passes its locale's formatter to the same function. Nothing sent between
implementations is ever formatted: `progress` carries numbers, and each client formats its own.
