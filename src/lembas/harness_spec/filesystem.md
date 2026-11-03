# The file system a session works in

Spec 3.0.0. **Two worlds, one harness.** A **chat** (a conversation on the server) works in the
server's file system: a tree kept by the instance, which may hold repositories mounted from a forge.
An **agent** session (LLeMbas CLI, Desktop's agent, or an agent chat the web UI runs on a linked
device) works in its working directory on its own machine. Neither sees the other's files. What
the two share is this contract: how paths read, what kinds of node there are, how access is said,
what an error says, how a listing and a search look, and how instruction files are collected. The
tools are the same names and schemas on both sides (`tools/*.json`, `sessions`); where a tool exists
on one side only, it says so there.

The conformance areas are `fs_access` (the view and its errors) and `instructions` (the instruction
chain). An agent session's local disk is the special case of one node kind less (no mounts) and
one access level for the whole working directory, decided by the CLI's permission rules and modes
(`permission/`) rather than by the table below.

## Paths

- **Absolute** paths begin with `/`, the root of the tree: the organisation's root on the server,
  the file system's root on a device.
- **Relative** paths resolve against the session's **start directory** (its working directory).
- `.` and `..` are resolved by their text, before anything is looked up; `..` above `/` stays at
  `/`. Repeated `/` count as one, and a trailing `/` changes nothing. On the server there are no
  links: a path is what it says. On a device, a link is followed, and where the CLI's rules look at
  a path they look at where it really is (an `AGENTS.md` committed as a link to a file outside the
  project is not read).
- Every answer names a path in its **absolute, resolved** form, whatever the call gave — an
  error, a refusal, a search's results, a tool's own message. **The one exception is a result's
  bracket** (`context.md`): a read's header, an edit's bracket and the compaction table name the
  path as the call gave it, so the model recognises what it wrote. What identifies a file, there
  too, is its absolute path. A listing names entries by their names under the directory listed
  (*A listing*).
- Names are case-sensitive, `/`-separated, and never empty, `.` or `..`.

## Node kinds

| Kind | What |
|---|---|
| `dir` | a directory of the tree |
| `file` | a file: bytes, with a content hash (SHA-256) |
| `mount` | a directory that is a repository on a forge, at one branch. Below it, every node is a `dir` or a `file` of that repository. Server only |

A session has a **boundary** (what it may see at all: the root, a directory, one repository) and a
**start directory** inside it. On a device the boundary is the project root (the directory the
CLI's trust is given to) and the start directory is where the session started.

## Access levels

Each path is at one of four levels for a session:

| Level | Name | Read | Change | Commit |
|---|---|---|---|---|
| `none` | no access | no | no | no |
| `ro` | read-only | yes | no | no |
| `stage` | read/write, stage | yes | yes | no |
| `commit` | read/write, commit | yes | yes | yes |

- **Inside a mount**, `stage` means a change goes into the session's **working copy** (kept by the
  instance) and stops there; `commit` means the session may also send it to the forge (`commit`,
  `branch`, `pr`, `merge`, `tag`, `release`). Outside mounts there is nothing to commit, and the
  two write levels mean the same: write.
- **A grant** names a path and a level, and covers everything below the path. **The most specific
  grant wins**: `/` at `ro` and `/Notes` at `stage` make `/Notes/a.md` writable and `/b.md`
  read-only; `/Notes/Private` at `none` takes that directory away again.
- **A floor** is a maximum level per path, set where the session cannot widen it (the data group's).
  The most specific floor entry applies; the level a session has is its grant, lowered to the floor.
- A path with no grant on it or above it is at `none`. **Only what is at `ro` or above exists for
  the model.** On a mount, the forge decides first: a level is never above what the user's own
  forge account may do there.
- The levels a session has are told to the model (`prompts/blocks/workspace.md`, `{{view}}`): every
  granted path with its level, so it knows the full paths it may use.

### A granted path under a path that is not

The tree `/AGENTS.md`, `/Dir1/`, `/Dir1/FILE1.txt`, `/Dir1/Dir3/`, `/Dir2/`, with `/AGENTS.md` and
`/Dir1/Dir3/` granted and nothing else:

- The model sees **`/AGENTS.md` and `/Dir1/Dir3/`**, each **by its full path**, and everything below
  `/Dir1/Dir3/`.
- `/` and `/Dir1` are **passages**: at `none`, with something granted below. A passage is denied for
  reading and writing alike: `list /Dir1`, `read /Dir1/FILE1.txt`, `write /Dir1/new.txt` and
  `mkdir /Dir1/x` all answer `Access denied: … is not open to this session.`
- A listing never reveals a sibling: nothing lists `/Dir1`, and a listing of a granted directory
  shows only its entries at `ro` or above.
- **A path the session cannot see is answered by what the model already knows**: by its nearest
  ancestor that is either visible or a passage. Under a visible directory it is
  `No such file or directory`; under a passage, `Access denied`. So `/Dir2/b.md` (hidden) and
  `/Nope.md` (missing) get the same answer here — both under the passage `/` — and a hidden entry
  under a granted directory reads exactly as a missing one.
- A search (`glob`, `grep`, `search`) rooted at a granted directory finds what is granted below it,
  however it is reached; one rooted at a passage is denied like a listing.
- **An instruction file in a passage is not loaded** (below).

**One thing cannot be hidden**, and the spec accepts it: under a directory the session may write, a
name taken by an entry at `none` cannot be created. Making it answers `Access denied` for that path,
which says the name is taken and nothing else — not its kind, its size or what is in it.

## What each operation needs

| Operation | Tools | Needs |
|---|---|---|
| look | `list`, `read`, `glob`, `grep`, `search`, `show`, `python`'s `files`, `skill_view`, `skills_list` | `ro` |
| change | `write`, `edit`, `mkdir`, `mv` (the source and the target's directory), `rm`, `python`'s `save`, `skill_manage` | `stage` |
| commit | `commit`, `branch`, `pr`, `merge`, `tag`, `release` | `commit`, inside a mount |

A new path takes the level of the most specific grant above it. `mv` between a mount and the rest
of the tree, or between two mounts, is refused; `rm` of a mount itself is refused (a mount is
removed by whoever mounted it, not by a session). `rm` on the server moves to the **trash**,
restorable from the file explorer; inside a mount it stages the deletion.

## Errors

A tool's error is its result (`loop.json` `parallel`). The texts, exactly, with `{path}` the
resolved absolute path:

| Case | Text |
|---|---|
| not visible (missing, or at `none`), and its nearest visible-or-passage ancestor is visible | `No such file or directory: {path}` |
| a passage; not visible with a passage as that ancestor; a name taken by an entry at `none` | `Access denied: {path} is not open to this session.` |
| a change at `ro` | `Read-only: {path} can be read in this session but not changed.` |
| a commit at `stage` | `Not committed: this session may change {path} but not commit it; the change stays staged.` |
| a commit outside every mount | `Nothing to commit: {path} is not in a repository.` |
| a directory where a file was meant | `Is a directory: {path}` |
| a file where a directory was meant | `Not a directory: {path}` |
| `mv`'s target, or a file `mkdir` was asked to make | `Already exists: {path}` |
| a tool whose `requires` is not met at the time of the call | the tool's `unavailable` text |

A missing file is `No such file or directory` for **every** tool that needs it to exist — `read`,
`edit`, `show`, `list`, `glob`, `grep`, `python`'s `files` — with no hint after it: in a chat a
hidden file must read exactly as a missing one, and a hint ("use write to create it") would say
which. The tool's description says what it needs.

`write` and `edit` refuse to change an existing file the session has not seen, or one that changed
since it last saw it (by reading it, or by its own change). The texts, exactly:

| Case | Text |
|---|---|
| the file exists and this session has not read it | `{path} exists and has not been read in this session. Read it first.` |
| it changed since this session last read or changed it | `{path} changed since you last read it. Read it again before changing it.` |

How a change is noticed is each side's: the content hash on the server, the modification time on a
device.

A device's own errors (a file too large, a binary file, a protected path) are the CLI's and stay as
they are; the ones above it says the same way.

## A listing

`list` and a directory given to `read`: one entry per line, directories (and mounts) with a trailing
`/`, then — in a chat — a mount marked ` [mount]` and an entry the session can read but not change,
in a directory it can, marked ` [read-only]`. Directories first, then by name in code-point order
(`B` before `a`). An empty directory is `(empty directory)` from `read`, `(empty)` from `list`.

`list` with a `depth` (default 2, at most 6) is a tree: the entries of the directory listed, and
under each directory the entries of that directory, each level indented by two more spaces, down to
`depth` levels in all (depth 1 is the directory's own entries). The order and the marks are a single
listing's, at every level; a directory's entries come right after it. At most 400 lines, then
`(more entries — list a directory further down)` on its own line. A device leaves out ignored and
build directories (`.git`, `node_modules`, …, and what `.gitignore` says) and shows only
directories that hold a file it would list.

```
docs/
  guide/
  index.md
repo/ [mount]
  src/
  README.md [read-only]
notes.md
```

## A search

`search(query, path?, limit?)` ranks passages by meaning (embeddings) and by words (full-text) fused
by rank, then keeps only what the session's view allows — **after** scoring, so a hidden file
changes neither the results nor how many there are. A result is the passage's path and line range,
then the passage:

```
/Notes/setup.md:12-30
<the passage>
```

`grep` is the exact counterpart (text or a pattern), over the same view.

### Indexing, by tokens

What `search` reads is built when a file is written (a mount: its default branch, and again for the
files a new commit changes). The index is keyed by **content hash**, so a file in many places, or
unchanged across commits and branches, is embedded once; a rename or a move costs nothing.

Text is cut into pieces **by tokens, not by characters**: an embedder refuses input past its
context (512 tokens is common), and dense text — another language than English, code, hashes —
holds far more tokens per character than prose. A piece is at most **480 tokens** (`size`; room for
the embedder's own markers under 512) and repeats the last **64 tokens** (`overlap`) of the one
before it. The cut rules are the character splitter's (`conformance/chunk.json`) measured in tokens:
the window is the longest stretch of at most `size` tokens; the cut is just after a paragraph
break, else a line break, else a sentence's `. `, looked for in the window's last third (by
characters), else at the window's end; the next piece begins at the start of the token `overlap`
tokens before the cut. Pieces under 40 characters are dropped; a text that fits is one piece.

Tokens are counted with the embedder's own tokenizer where the server says how (llama.cpp's
`/tokenize`, for one). Where it does not, a count of `ceil(UTF-8 bytes / 2)` stands in — an
estimate, not a bound: it has not been measured against a real tokenizer. **A piece the embedder
still refuses for its length is split in two at the best cut and both are embedded; nothing is
dropped.**
`conformance/chunk_tokens.json` fixes the rules with a stand-in tokenizer (a token is a run of
non-whitespace), so both projects can be checked without an embedder.

## Instruction files, stacked

Instruction files are **collected from the boundary down to the start directory, outermost first**:
every directory on the way may contribute one, and all of them go to the model — the root's, then a
section's, then a repository's. (Before 3.0.0 the CLI took only the nearest one.)

- **The names**, in order of preference within one directory: `AGENTS.md`, then `CLAUDE.md`. A
  directory contributes the first of them that is a file the session can read; never both.
- **Only what the session can read is loaded.** A file at `none` — in a passage, or taken away under
  a granted directory — is not loaded, and the walk goes on below it: the next directory down may
  still contribute. A file granted on its own (`/AGENTS.md` ticked while `/` is not) is loaded.
- A start directory outside the boundary is read as the boundary.
- On a device the boundary is the project root, a file counts only when it really is a file inside
  the project (a link to elsewhere is not read), and the CLI's global `AGENTS.md` comes before the
  chain, the `instructions` named in its configuration after it.
- **Below the start directory**, a file under a directory with its own instruction file hands the
  model that file the first time something under it is read, once per session (the CLI's "nearby
  instructions"), by the same reading rules.
- Each is given to the model under its path relative to the boundary.

`conformance/instructions.json` gives the cases, including the passage.

## Skills

A skill is a directory with a `SKILL.md` (frontmatter `name`, `description`), and `skills_list`,
`skill_view` and `skill_manage` are its meaning over files on both sides: the index in the prompt
(`prompts/blocks/skills.md`), `skill_view` to load one, `skill_manage` to make or change one, which
checks the frontmatter, the name and the size; `edit` works on the file too. Neither side syncs
skills on its own.

**Where they are found**, in order of precedence — a name found in an earlier place wins, and the
later one is not offered:

| Side | Places, the most specific first |
|---|---|
| chat | the `skills/` directory of every directory from the start directory up to the boundary, the nearest first, **the root's excluded**; then the person's own `/home/<user>/skills/`; then the organisation's `/skills/` (a skills repository mounted there, or a plain directory) |
| device | the project's `.agent/skills/` (a trusted project); then the user's own skills directory; then the directories the configuration adds, in its order |

So on both sides the session's own place comes first, the person's next, what is shared last. Only
what the session can read is a skill there; `skill_manage` makes a new one in the person's own
place.

**A skill's name**, which `skill_manage` checks when it makes or renames one: lowercase letters,
digits and `-`, starting with a letter or a digit, at most 64 characters —
`^[a-z0-9][a-z0-9-]{0,63}$`, the Agent Skills format's rule. A `SKILL.md` made elsewhere whose name
does not fit is still offered when its name is one line of at most 64 characters; a name is looked
up ignoring case, with `_` and spaces read as `-`.

## Memory

Memory is **records, not files**, and **the server keeps them for both sides**: a chat's memory is
its data group's partition, and a device logged in to an instance reads and writes the same records
through the instance (`acp.md`, *The HTTP API*), for a session the web UI opened in that chat's data
group, and for a session of its own in the group of the session's model. The `memory` tool is the
same tool on both sides (`tools/memory.json`); what each offers of its targets is its `narrow`.
