# Subagents

Spec 3.3.0. **Which model a subagent runs on, and when subagents are offered at all.** A
**subagent** is any agent a session's agent starts to work for it: a `task` subagent (in the
foreground or as a background job) and a team's member (`team.md`) alike. The same rules in a chat
on the server and in an agent session on a device, in both projects. The texts are `loop.json`
`subagents`; the cases `conformance/subagents.json`.

## 1. The word

**Subagent** is the word in every text a person or a model reads: the UI (the web UI, Desktop,
Android, the CLI, the admin pages), the prompt texts and the tool texts. A team's members are
subagents too. The word *helper* is retired. Tool names, their former names (`aliases`) and
stored identifiers do not change.

## 2. The model a subagent runs on

**The session's own model**, always — unless **the person has named another model** in this
session. Nothing else chooses one: there is **no setting** for a subagent's model anywhere — not
in a chat, not in a person's or an instance's settings, not in the configuration, and an agent
definition's own `model` (the CLI's agent files) is not used for a subagent either.

- **Named** means: in a message **the person typed** in this session, the model's served id, its
  id at its connection, or its display name occurs as a whole word, letters compared without case.
  Only the person's own words count — never the model's text, a tool's output, a file, a note, a
  job's or a team's marked message, or a subagent's report. A message that was compacted away
  still counts: what the person named stays named for the whole session.
- **A whole word**: the character before the name is not a letter, a digit, `_`, `-`, `/`, `.` or
  `:`; the character after it is not a letter, a digit, `_`, `-` or `/`, nor a `.` or `:` followed
  by a letter or a digit. So `coder-30b.` at the end of a sentence names `coder-30b`, and
  `coder-30b-instruct` does not. Runs of white space in a display name match any run of white
  space.
- The `model` parameter of `task` and of `team_add` takes a model the person may use, by the same
  names. When it is **not the session's model and the person has not named it**, the call is
  refused, word for word, with `subagents.texts.not_named`:

  > Use another model for subagents only when the person names it.

  The session's own model given as `model` is the default spelled out, and is never refused for
  not being named.
- A team's members are of the lead's session: what the person named there counts for every
  `team_add` in the team, a sub-manager's included. A member's default is the session's model, not
  its manager's.

## 3. Capacity: the logical restrictions, and no others

What a model's server can do at once is `models/schema.json` `capacity`: `parallel` (`single_session`
being `parallel: 1`; neither said — no limit), and `group`, the server that holds one model at a
time. With **S** the session's model and **M** the subagent's, checked in this order, after
`not_named`:

1. **M is another model on S's one-model server** (both have the same `group`): refused with
   `subagents.texts.same_group` — starting it would unload the lead's model:

   > That model is on the same one-model server as this chat's model; name a model on another server.

2. **M is S, and S serves one request at a time** (`parallel` 1): refused with
   `subagents.texts.one_request` — the subagent would take the only slot from the lead:

   > This model serves one request at a time: name another model, on another server, for subagents.

   There is **no fallback**: nothing else is picked for the person, ever. A model the person named,
   on another server, is the way out — and a model that serves one request at a time is fine there.

These apply to `task` and to `team_add` alike, and before anything is started. Beyond them nothing
is refused for being the wrong model: a member past the free slots waits for one (`team.md` §8),
and `task` keeps its rule for a model that is already full (`loop.json` `capacity`).

## 4. When subagents are offered

The size setting `agents {min, max}` (`team.md` §2) is shown as **Subagents** — `subagents.setting`:
the label, and **Min** and **Max** for `min` and `max` (defaults 1 and 4, at most `cap` 32, kept
with the chat). **The numbers count the session's own agent**: Min 1 / Max 1 is the agent alone,
with no subagents; Max 4 is the agent and up to three subagents (`subagents.setting.hint`). A UI
shows the setting as **one range from 1 to `cap` with two handles, Min and Max** (Min never past
Max): a slider in a graphical UI, two numbers on one line in a terminal, each moved on its own. It, the team tools and `blocks/team_lead.md` are **offered** when both hold:

1. Subagents are enabled for the session (`team.md` §2, rule 1).
2. **At least one model could serve as a subagent**: S itself, when it serves two or more requests
   at once (`parallel ≥ 2`, or no limit); or **any other model the person may use that is not on
   S's one-model server** (no `group`, or another one) — whatever its own `parallel`.

So a session on a one-request model is offered subagents when another server has a model, and
**its first note says so** (`context.note.team_own`, after `team`): the session's model cannot
serve subagents itself, and they need a model the person names. A UI showing the setting says the
same beside it (`subagents.setting.own`). Nothing is offered when neither holds — the same as
before 3.3.0 for a single model.

What counts as **usable** is each project's: LLeMbas — the models the person may use in the chat's
picker; the CLI — the models of the server it is logged in to.
