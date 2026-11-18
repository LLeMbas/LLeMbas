# Interactive-only models

Spec 3.9.0. **Some models may be used for a person's own turns and for nothing else.** A
provider that sells a subscription for interactive use forbids automated services, batch scripts
and scheduled background jobs on it, and may suspend a key used for those. Whether a model is
one is the server's word — a property of the connection it is served through — and it says so
on the model: **`interactive_only: true`** (`models/schema.json`; in `/v1/models` and the model
objects of `/api/v2`, sent only when true). Nothing matches a name.

The server enforces this for what it runs itself. A device talks to the server's `/v1`, and the
server cannot tell what a request there is *for* — so the rules below are every client's, and a
device's most of all. The field and the texts: `loop.json` `interactive`; the cases:
`conformance/interactive.json`.

## 1. What is interactive, and what is not

**Interactive** — and so allowed on such a model — is a turn a person started and everything
that turn starts: the reply, the tool calls inside it, a subagent or a team the person's session
starts (on the session's model, or one the person named), a turn woken by one of their notes
(`machine`), and a command the person runs that asks a model for an answer shown to them at
once (the CLI's commit-message draft).

**Not interactive**, in two kinds:

- **Scheduled work**: a schedule's run — on the server, or as an agent turn on a linked
  computer — with its subagents and its fallbacks, which are the same run. And a **one-shot run
  with nobody at it**: the CLI's `lembas run` when none of its standard input, output and error
  is a terminal (a cron job, a pipeline), which is the batch script the terms name.
- **Background calls**: what a client asks a model on a session's behalf that is not the answer
  to the person's turn — **naming the session** (its title) and **compacting it** (the summary,
  asked for by the person or by the harness when the window fills). A **subagent's own
  automatic compaction** is one too: the subagent is the person's, the summary of its
  conversation is not their turn.

## 2. Scheduled work never runs on one

- **A schedule may not be given one.** Wherever a schedule's model is chosen, an
  interactive-only model is not offered, and one typed or sent is refused with
  `interactive.texts.scheduled` (`{{model}}` the model as the person knows it).
- **A run whose model is one fails**, with that sentence, before anything is sent to the model —
  never quietly on another model. (A model can become interactive-only after the schedule was
  made.) For an Agentic schedule that names no model, the directory's model counts only when it
  is not interactive-only (`conformance/last_model.json`, `kind: client` with `scheduled`).
- **On a linked computer** the device checks again, because it is the one that sends: a
  scheduled `session/new` or `session/prompt` whose session's model is interactive-only is
  refused — `-32602`, the sentence, `data: { reason: "interactive_only" }` — and the run fails
  as any refused run does (`acp.md` *When the device is away, or refuses*). A scheduled
  session opened with no model skips an interactive-only directory's model as it skips one no
  longer served. During a scheduled turn the session's model cannot be changed to one, and a
  subagent that would run on one is not started: the tool call answers the sentence, and the
  run goes on without it.
- A one-shot run with nobody at it ends at once with the sentence, having sent nothing.

A person writing into a run's chat afterwards is a person: that turn is interactive. What the
run left working is still the run: in a session a schedule opened, a turn woken by one of its
jobs, and a subagent started when no person's turn is running, are scheduled work and are held
to this section.

## 3. Background calls go elsewhere

When the model a background call would use is interactive-only, the call goes to the first of
(`kind: background`):

1. **its twin** — the same model on a connection that is not interactive-only. Among served ids
   (`provider/model`): another id with the same part after its first `/`, not interactive-only,
   the first in the list's order (`kind: twin`). The server, which knows its connections, also
   requires the twin to be one the person may use in the chat's data group;
2. **a default that is not interactive-only** — in order: on a device, its global `model`
   setting, then the instance's default model; on the server, the instance's default model;
3. **nowhere**. Then:

| The call | With nowhere to go |
|---|---|
| a title | skipped: the session keeps the name made from its first prompt |
| compaction the person asked for | refused in words: `interactive.texts.compact` |
| automatic compaction | skipped, and the turn goes on — a client may say so once; the person can switch model, or start anew |

**A model that cannot take the call is not somewhere to go**: for a summary, one whose context
window is below what must be sent, or that takes no tools while the conversation holds tool
calls, is passed over like one that is not listed (`unfit` in the cases). A title fits anywhere.
A skipped automatic compaction is not an attempt: it does not count toward a client's limit on
compacting within one turn.

**The call is the other model's.** Its tokens are counted under the model that was asked, never
under the session's, and the client says which model that was — for a title once, for a
compaction each time.

A call that goes elsewhere is otherwise the same call: the summary is asked for with the same
conversation and comes back into the same session, whose model does not change. A model that is
not interactive-only is asked itself, as before 3.9.0. The model a title is asked of is the
client's small model where it has one (the CLI's `small_model`), else the session's; the rule is
applied to whichever it is. For a subagent's automatic compaction it is the subagent's model
that is looked at — its twin, the same defaults, or skipped and said once to the session it
works for.

## 4. What is outside this

Embedding, speech and image models are not chat models and carry no such field. A device's
watch command (`_lembas/monitor`) asks no model.
