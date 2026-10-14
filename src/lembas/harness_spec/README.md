# The harness spec

LLeMbas CLI (a terminal coding agent, TypeScript) and LLeMbas (a
self-hosted web UI, Python) are two independent projects that share **one harness design**: the
same tools, the same permission design, the same prompt texts, the same model quirks and model
metadata. This directory writes that design down as data. Each project implements it in its own
language; neither ever runs the other. LLeMbas CLI embeds these files at build time; LLeMbas vendors a pinned, hash-checked copy.

The concept-by-concept map of the two implementations, and where they still differ, is the wiki
page [Harness-parity](https://git.houmeres.sk/LLeMbas/LLeMbas-CLI/wiki/Harness-parity).

## What is here

| Path | What |
|---|---|
| `VERSION` | The spec's own version, apart from LLeMbas CLI's |
| `purpose.json` | The `purpose` argument added to a tool whose calls say what they are for |
| `tools/<name>.json` | One tool each: name, `scope`, `risk`, `exclusive`, `block` (how a call is shown: `shell`, `edit`, `read`, `search`, `web`, `task`, `hidden`, `tool`), `purpose`, former names (`aliases`, per project), renamed arguments, the description and the JSON Schema of its parameters — exactly what a model is sent |
| `prompts/` | The prompt texts; `prompts/index.json` says which are shared and which LLeMbas fragment each one is. `prompts/personality/` holds the personality presets both offer |
| `permission/hardline.json` | The floor: commands refused in every mode, protected paths, and how a command is spelled plainly before it is checked again |
| `permission/arity.json` | How much of a command an "always allow" covers |
| `permission/defaults.json` | The rules every session starts from |
| `permission/modes.json` | The four modes — manual, edit, auto, plan — as what each does with each risk class |
| `models/schema.json` | Model metadata: context, output limit, efforts, vision, tools, notes, capacity |
| `loop.json` | How one prompt runs: the step backstop, budgets, wrap-up, nudges, parallel calls, capacity |
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
- A tool's other names, per project, are listed in its `aliases` and work as the tool.

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

## Variables in a description

A description may hold `{{name}}` where the two projects differ in a fact, not in design — a
timeout's default, whether commands may run in the background, which subagents exist. The tool's
`variables` says what each one means; each project fills them before sending the description, and
none may reach a model unfilled.

## Changing it

1. Change the spec first, and bump `VERSION`: **patch** for a text or a new conformance case,
   **minor** for a new tool, rule or field, **major** for a rename or a removed tool.
2. A fix comes with a **conformance case** that fails without it. In LLeMbas CLI,
   `bun run harness record` fills in the expected result of a new case from the implementation,
   and `bun run harness record --all` shows every case whose result would change.
3. Implement it in both projects — in the same session, or with an open item recorded against the
   other. A bug found in one is searched for in the other.
4. Nothing here names a host, a path on a server or anybody's setup: both projects are public.

Licence: MIT, as LLeMbas CLI. Files that came from elsewhere say so in their `source` field
(Hermes Agent's hardline patterns, OpenCode's arity table) or in an HTML comment (prompt texts).
