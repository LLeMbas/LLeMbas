# Context hygiene

Spec 3.0.0. What a session does to keep tool results from filling the model's context — the same in
a chat and in an agent session, in both projects. The texts and numbers are `loop.json` `context`;
the cases are `conformance/context.json`; what the model is told is `prompts/blocks/context.md`.

## The one rule

**Every measure acts when a result is inserted. Nothing already sent to the model is ever
rewritten.** Changing earlier context invalidates the prompt cache from that point on: a local
server processes everything after it again, and a cloud API charges full input price for it again —
on a long context that costs more than the measure saves. So no earlier result is ever shortened,
replaced by a stub or removed afterwards. The one deliberate break is compaction, which already
replaces the history with a summary.

## 1. Every result has an id

Every tool result is given an id when it is inserted: `#` and the result's number in the session —
counting from 1, in lowercase hex, at least `ids.digits` (4) digits — so `#0001`, `#0002`, …,
`#000a`, …, `#10000`. Unique in the session and never reused — not after a compaction either. It is
the first thing in the result, in brackets: `[#0001]`, then a newline and the result.

A read's bracket says more: the path **as the call gave it**, the lines shown, the file's length in
lines and the first `sha_digits` (8) hex digits of the SHA-256 of the file's content —

```
[#0012 src/app.ts lines 1-200 of 340 sha 1c9e04ab]
1: import …
```

— and an empty file `[#0013 notes.txt empty sha e3b0c442]`. The full hash is kept by the session;
only it decides whether a file is unchanged.

**Paths in a bracket are the call's.** A read's bracket, an edit's (§5) and the compaction table
(§7) name the path as the call gave it — what the model wrote, so it recognises it. What identifies
the file — for a stub, for the table's "newest read of each path", for whether a file was read before
it is changed — is its absolute, resolved path (`filesystem.md`, *Paths*). Every other answer (an
error, a refusal, a search's results) names the absolute path.

**Every result is stored with the session, in full** — before any cap, and for a stub the text it
stands for — so `recall` can show any of them for as long as the session exists.

## 2. An unchanged re-read is a stub

**A stub names only a result that is in the request being built** — one whose text is among the
tool results this request sends to the model. That set, not anything the session stored, is the
dedupe table. Where a request does not carry earlier results — a chat on the server sends no earlier
reply's tool results with the next reply; after an undo, a rewind or a compaction they are gone — a
read is answered in full, because what a stub would point at is not in front of the model. It
holds for §2 and §6 alike.

A read is answered with a stub when the file's content hash equals that of an **earlier full read
of the same file still in the dedupe table**, and the lines asked for lie **wholly inside** that
read's lines:

```
[#0019 src/app.ts lines 50-80 of 340 sha 1c9e04ab] Unchanged since #0012, which already shows lines 1-200.
```

The bracket is the new read's; the sentence names the earlier result and its lines. A stub points
at a full result, never at another stub. A file that changed — by this session's own edit or from
outside — has another hash and is read in full; a range reaching past the earlier read is read in
full.

## 3. Large output is capped on the way in

A result over `cap.max_lines` (400) lines or `cap.max_chars` (40,000) characters keeps its head —
as many whole lines as fit `head_lines` (200) and `head_chars` (20,000) — and its tail — likewise,
`tail_lines` (100) and `tail_chars` (10,000) — with one line between:

```
[1234 lines elided, full output #0020]
```

A first line longer than `head_chars` is cut to it and ends ` […cut]`; the elided line follows it
whatever it counts. Reads and `recall` are not capped this way: each has its own window (2000 lines
from an offset).

## 4. `recall`

`recall(id, offset?, limit?)` shows a stored result again, in full, up to 2000 lines from `offset`:

```
[#0021 recall #0020 lines 1-2000 of 3400]
…
```

An id the session never gave: `[#0021] No stored result #ffff in this session.` The `#` may be left
out of the argument. A recall is not capped, is not deduplicated, and is itself stored.

## 5. Edits answer with the diff

`edit`, `multiedit`, `apply_patch` and `write` over an existing file answer with the unified diff
of the change (three lines of context, hunks only — the path is in the bracket) under a bracket
naming the path and the new content's hash:

```
[#0022 src/app.ts sha 7d0a91c3]
@@ -12,3 +12,3 @@
```

`write` of a new file answers `[#0023 docs/new.md sha 52b6e3a0] Created, 14 lines.` — the model
wrote the content and has it. A deleted file is `[#0024 old.md deleted]`. An `apply_patch` over
several files answers the id alone, then each file in its own bracket with its change, in the
patch's order:

```
[#0025]
[src/a.ts sha 7d0a91c3]
@@ -1,2 +1,2 @@
[docs/new.md sha 52b6e3a0] Created, 14 lines.
[old.md deleted]
```

A note the tool adds (a skill file written outside skill_manage) follows the diff. The diff is
capped like any output, the first line kept.

## 6. The same search or listing twice is a stub

For `dedupe.by_result` — `list`, `glob`, `grep`, `search` — a call with the same arguments (compared
as JSON with sorted keys) as an earlier one in the dedupe table, **whose result is the same text**,
is answered `[#0027] Same result as #0026.` The call is run again and compared, so a change from
anywhere shows up as a full result. Other tools are never deduplicated: a command's output twice is
two results.

## 7. Compaction

Compaction is the one deliberate break of the cache, and it already exists. In 3.0.0 two things
come with it:

- After the summary the harness adds — itself, not the model — a table of the **reads the summary
  covers**: the full reads made since the previous compaction (or since the session began) in turns
  that are still part of the history being summarised — whether or not their results are still in
  the request. A turn taken back (an undo, a rewind, a reply regenerated or deleted) takes its reads
  with it. Of those, the newest full read of each file (by its absolute path), newest last, at most
  `compaction.rows` (50); a stub is not a read here. A session that resends every result (the CLI)
  finds exactly these in its dedupe table; a chat, which does not, finds them in what it stored for
  the turns being summarised:

  ```
  ## Files read before this summary

  Read a file again for its content; recall shows a result by its id.

  | Path | Lines | sha | Result |
  |---|---|---|---|
  | src/app.ts | 1-200 of 340 | 1c9e04ab | #0012 |
  ```

  With no reads there is no table.
- **The dedupe table is emptied**, so no stub ever points at content that is no longer in the
  context. A read after compaction is a full read; a `recall` of any earlier id still works.

## 8. Earlier reasoning

Left to the model's own chat template: the harness sends the history as it does, and neither strips
reasoning a template keeps nor adds what it drops.

## 9. What the model is told

`prompts/blocks/context.md`, in every session that has `recall`.

## Not in 3.0.0

Eliding old content in batches behind a threshold, and measuring quality and tokens with and without
these measures, are left for a later spec. Every measure above ships with its unit and conformance
cases.

## Over the link

A device keeps its sessions' stored results itself; `recall` in an agent session reads the device's
store. The ids are in the result text, so a client showing the transcript shows them as they are.
