# Reports

Spec 3.8.0. **A schedule's run does not answer into a conversation: it files a report, or it does
not.** Before 3.8.0 every run's answer was a message — a chat per run, a notification per run — and
a run with nothing to say hid behind the word `[SILENT]`. Now the model writes a report with a tool
when there is something to tell, the schedule's **notify when** says when that is, and a run
without a report leaves one line in the schedule's history. The tool is `tools/report_write.json`;
the values and texts `loop.json` `scheduled.notify`; the cases `conformance/reports.json`. (The
subagents' `report` tool is another thing and keeps its name.)

## 1. The tool

`report_write(title, body, summary, importance?)`:

- `title` — what the report is about, as a headline; `body` — the report itself, in Markdown;
  `summary` — one or two sentences a person reads in a list or a notification; `importance` —
  `normal` (the default) or `high` (the person should look now).
- **A run files at most one report.** A second call in the same run **replaces** the first: the
  run's report is the last one written. The result says which happened, word for word
  (`scheduled.notify.result.filed`, `.replaced`), and both tell the model the same thing next:
  *the report is filed; end with one short line for the run's history.*
- It is a tool of the session (`risk: interact`), allowed without asking: it writes nothing on
  the machine and reaches nobody but the schedule's owner.

## 2. When it is offered: `requires: scheduled`

`report_write` is offered **only in a session a schedule opened for its run**, and only when the
schedule's notify-when is not `never` (§3). In such a session a turn that is not the schedule's —
the person writing into a run's chat afterwards — is not a run: the tool stays in the list (the
list of a session does not change, `context.md` §10 rule 4) and answers its `unavailable` text,
word for word. Everywhere else — a chat, an agent chat, a subagent, the phone assistant — it is
not in the list at all.

| The session | Notify when | The turn | `report_write` |
|---|---|---|---|
| a schedule's run | `always`, `when` | the run | offered |
| a schedule's run | `always`, `when` | the person's own | `unavailable` |
| a schedule's run | `never` | any | not in the list |
| anything else | — | any | not in the list |

A subagent of a run is never offered it: the run's own agent files the report.

## 3. Notify when

A schedule says when its owner wants to hear from it (`scheduled.notify.values`):

| Value | The form says | The model is told | The tool |
|---|---|---|---|
| `always` | Every run | write a report every run — also when nothing was found, and when nothing matters | offered |
| `when` | When… — with the person's own words; `scheduled.notify.default_condition` (*something needs my attention*) when they wrote none | write a report **only** when the condition holds; otherwise end with one line saying why not | offered |
| `never` | Never | nothing about reports | not offered |

A schedule that says nothing is `when` with the default condition (`scheduled.notify.default`).
The side that creates a schedule may choose otherwise for **a schedule that runs once**: a
person who asks for one thing, once, wants its answer, so a one-shot may be created as `always`
(`scheduled.notify.one_shot_default`). The value is the schedule's from then on, and is always
sent and stored as one of the three — only a schedule that truly carries none is read as `when`.

**The instruction** is `scheduled.notify.texts.<value>`, word for word, `{{condition}}` the
person's words (trimmed, one line, without a full stop at the end) — `never` has no text. It is
part of **the run's prompt**: the server composes that prompt for every run — the schedule's
instruction, the run's time, then this — and a device that runs the turn adds nothing (`acp.md`
*Scheduled work*). So one side writes the words, and a model reads the same ones wherever it runs.

## 4. What a run leaves

When the run ends, by what happened in it:

| Notify when | `report_write` was called | The run leaves |
|---|---|---|
| any | yes | **the report** (the last one written), and the history line |
| `always` | no | the final answer **filed as the report** — title the schedule's, body the answer, summary the answer's first paragraph on one line (as written, white space collapsed) cut to `scheduled.notify.summary_max` characters with `…`, importance `normal` — unless the answer is empty: then no report, and the note `scheduled.texts.no_reply` |
| `when` | no | no report; the history line says why not (the model's own line) |
| `never` | — | no report; the history line |

**The history line** (`note`) is the final answer's first line that is not blank, trimmed and cut
to `scheduled.notify.note_max` characters with `…`; `scheduled.texts.no_reply` where there is
none. A run that failed or was cancelled leaves its failure, as before, and no report — unless
one was already written, which is kept: what the model found is not thrown away with the error.

**Notifications come from the report**, not from the run: one notification for a report (its
`title`; `importance: high` may be shown as urgent), none for a run without one. A failed run
still says it failed.

## 5. `[SILENT]` is retired

The sentinel of 3.5.0 (`scheduled.sentinel`) is no longer told to a model: the notify-when
texts replace the hint — but for a run on a device that has no `reports` capability (§6). A reply that still carries it — an older prompt, a model's habit — is
read as **no report**: its sentinel lines (first or last line, as `conformance/silent.json`
strips them) are taken out before the history line is made, and under `always` an answer that is
only the sentinel files nothing. The run's status is never `silent` under 3.8.0; a device that
still answers `silent` (§6) is read as `ok` without a report.

## 6. A run on a linked computer

A schedule may run as an agent turn on a device (`acp.md` *Scheduled work on a linked computer*).
The report travels **with the turn's answer** — there is no method of its own:

- The server marks the turn: `scheduled.notify` (`always`, `when` or `never`) in `_meta.lembas`
  of `session/new` and `session/prompt`, beside the schedule's ids. The prompt it sends already
  carries the instruction (§3).
- A device with the capability **`reports`** offers `report_write` to the run's model by §2 —
  the tool is the device's own, it keeps the last report written in the turn — and puts it into
  the prompt's answer: `_meta.lembas.scheduled.report: { title, body, summary, importance }`,
  absent when none was written. Its `status` is `ok`, `failed` or `cancelled`; `text` the final
  answer.
- The server then applies §4 to that answer exactly as to a run of its own: the `always`
  fallback, the history line and the notification are the server's, never the device's.
- A device **without** the capability (before 3.8.0) is not sent `notify`, offers no tool, and
  **must not be sent an instruction that names `report_write`** — its model would be told to
  call a tool it does not have. Its run's prompt is the one of 3.5.0 instead: for `when`, the
  `[SILENT]` hint as it was (say the sentinel alone when there is nothing to tell); for `always`
  and `never`, no hint at all. This is the one place the sentinel is still told to a model. The
  server files by §4 from its `text` alone — under `always` the answer is the report; under
  `when` there is none, and the model's closing line is the note. `silent` from such a device is
  `ok` without a report.

Why with the answer, and not a call relayed while the turn runs: a report is the run's *outcome*.
Carried in the answer it is atomic with the run's end — a link that drops mid-turn cannot leave a
report without a run, a prompt sent again gets the same answer with the same report (`turnId`),
and "the second call replaces the first" needs no protocol at all.

## 7. Reading reports

Reports are the server's: it stores them (the owner, the schedule and run, title, summary, body,
importance, unread, the chat a reply continues in) and lists them for every client over
`/api/v2/reports`. A report's body is Markdown as a reply is, and may carry pages (`pages.md`),
which a scheduled run may be offered. Replying to a report continues it as a conversation that
stays with the report.
