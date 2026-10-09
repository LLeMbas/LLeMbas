"""Reading a shell command line, the harness spec's way.

Ported from the LLeMbas CLI (`src/permission/bash.ts`, `hardline.ts`, `arity.ts`;
MIT, © Jaroslav Beneš) so that both projects judge a command line identically --
the conformance cases `split`, `plain`, `hardline` and `arity` in
`harness_spec/conformance/` are run against this module by the test suite.

A deliberately small reader: enough to split a line into the simple commands it
runs, to know when it cannot be sure, and to spell each command plainly --
wrappers (`sudo -u x`, `env A=1`, `timeout 5`, `xargs`), quotes, escapes and
program paths taken off, `sh -c "..."` read as the line it runs. It never needs
to be a full parser, because every case it does not understand falls back to
asking.

The data -- the hardline rules, the wrappers and their options, the arity table
-- is the spec's (`harness_spec/permission/`).
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from functools import cache

from lembas.services import harness_spec

_SAFE_REDIRECT = re.compile(r"^(\d?>&\d|\d?>\s*/dev/null|&>\s*/dev/null)$")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


@dataclass
class Split:
    """Each simple command, trimmed, in order; and why an allow cannot be trusted for the line."""

    commands: list[str] = field(default_factory=list)
    unsafe: list[str] = field(default_factory=list)


def split_command(line: str) -> Split:
    commands: list[str] = []
    unsafe: dict[str, None] = {}  # insertion-ordered set, as the CLI's
    cur = ""
    quote: str | None = None
    n = len(line)

    def flush() -> None:
        nonlocal cur
        c = cur.strip()
        if c:
            commands.append(c)
        cur = ""

    i = 0
    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else None
        if quote == "'":
            cur += ch
            if ch == "'":
                quote = None
            elif ch == "\n":
                unsafe["a quoted string across lines"] = None
            i += 1
            continue
        if ch == "\\" and nxt is not None:
            cur += ch + nxt
            i += 2
            continue
        if quote == '"':
            cur += ch
            if ch == '"':
                quote = None
            elif ch == "`" or (ch == "$" and nxt == "("):
                unsafe["command substitution"] = None
            elif ch == "\n":
                unsafe["a quoted string across lines"] = None
            i += 1
            continue
        if ch == "#" and (cur == "" or cur[-1:].isspace()):
            while i + 1 < n and line[i + 1] != "\n":
                i += 1
            i += 1
            continue
        if ch == "$" and nxt == "'":
            unsafe["$'…' quoting"] = None
        if ch == "$" and nxt == "{":
            unsafe["parameter expansion"] = None
        if (
            ch == "<"
            and nxt == "<"
            and (line[i + 2] if i + 2 < n else None) != "<"
            and (line[i - 1] if i > 0 else None) != "<"
        ):
            unsafe["here-document"] = None
        if ch in ("'", '"'):
            quote = ch
            cur += ch
            i += 1
            continue
        if ch == "`" or (ch == "$" and nxt == "("):
            unsafe["command substitution"] = None
        if ch in ("<", ">") and nxt == "(":
            unsafe["process substitution"] = None
        if ch in ("\n", ";"):
            flush()
            i += 1
            continue
        if ch == "&" and nxt == "&":
            flush()
            i += 2
            continue
        if ch == "|":
            flush()
            i += 2 if nxt == "|" else 1
            continue
        if ch == "&" and nxt != ">" and (line[i - 1] if i > 0 else None) != ">":
            flush()
            i += 1
            continue
        if ch == ">":
            j = i + 1
            if j < n and line[j] == ">":
                j += 1
            if j < n and line[j] == "&":
                j += 1
            while j < n and line[j] == " ":
                j += 1
            while j < n and not re.match(r"[\s;&|]", line[j]):
                j += 1
            start = i - 1 if i > 0 and re.match(r"\d|&", line[i - 1]) else i
            redir = re.sub(r"\s+", "", line[start:j])
            if not _SAFE_REDIRECT.match(redir.replace(">>", ">")):
                unsafe["redirection to a file"] = None
            cur += line[i:j]
            i = j
            continue
        cur += ch
        i += 1
    if quote:
        unsafe["unclosed quote"] = None
    flush()
    for c in commands:
        head = (words(c) or [""])[0]
        if head in ("eval", "source", "."):
            unsafe[f"`{head}`"] = None
        if head in ("sh", "bash", "zsh", "dash", "ksh") and re.search(r"\s-\w*c\b", c):
            unsafe["nested shell"] = None
    return Split(commands, list(unsafe))


def words(command: str) -> list[str]:
    """Shell words with quotes removed. Leading VAR=value assignments are skipped."""
    out: list[str] = []
    cur = ""
    quote: str | None = None
    started = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote:
            if ch == quote:
                quote = None
            else:
                cur += ch
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            started = True
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            cur += command[i + 1]
            started = True
            i += 2
            continue
        if ch.isspace():
            if started:
                out.append(cur)
            cur = ""
            started = False
            i += 1
            continue
        cur += ch
        started = True
        i += 1
    if started:
        out.append(cur)
    while len(out) > 1 and _ASSIGNMENT.match(out[0]):
        out.pop(0)
    return out


# --- The plain spelling -------------------------------------------------------


@cache
def _plain_tables() -> tuple[dict[str, list[str]], frozenset[str], frozenset[str], frozenset[str]]:
    plain = harness_spec.hardline()["plain"]
    return (
        plain["wrappers"],
        frozenset(plain["keywords"]),
        frozenset(plain["shells"]),
        frozenset(plain["git_value_options"]),
    )


def _plain_path(arg: str, home: str | None) -> str:
    """A path argument spelled plainly: normalised, the home directory written `~`."""
    if arg == "~" or arg.startswith("~/"):
        p = arg if home is None else home + arg[1:]
    else:
        p = (
            re.sub(r"^\$\{?HOME\}?(?=/|$)", "~", arg)
            if home is None
            else re.sub(r"^\$\{?HOME\}?(?=/|$)", home, arg)
        )
    if p.startswith("~"):
        rest = p[1:]
        if not rest:
            return "~"
        p2 = posixpath.normpath("/" + rest.lstrip("/"))
        return "~" if p2 == "/" else "~" + p2
    if not p.startswith("/"):
        return arg
    p = posixpath.normpath(p)
    if p.startswith("//"):
        p = "/" + p.lstrip("/")
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    if home:
        if p == home:
            return "~"
        if p.startswith(home + "/"):
            return "~" + p[len(home) :]
    return p


def substitutions(line: str) -> list[str]:
    """The command lines inside a line's `$( … )` and backtick substitutions
    (outermost first). Inside single quotes nothing is a substitution."""
    out: list[str] = []
    quote: str | None = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = None
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "'" and not quote:
            quote = "'"
            i += 1
            continue
        if ch == '"':
            quote = None if quote == '"' else '"'
            i += 1
            continue
        if ch == "$" and line[i + 1 : i + 2] == "(" and line[i + 2 : i + 3] != "(":
            depth = 0
            j = i + 1
            while j < n:
                if line[j] == "(":
                    depth += 1
                elif line[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append(line[i + 2 : j])
            i = j + 1
            continue
        if ch == "`":
            end = line.find("`", i + 1)
            if end == -1:
                break
            out.append(line[i + 1 : end])
            i = end + 1
            continue
        i += 1
    return out


def plain_commands(line: str, depth: int = 0, home: str | None = None) -> list[str]:
    """Each simple command of a line, rewritten plainly (see the module docstring)."""
    if depth > 3:
        return []
    wrappers, keywords, shells, git_value = _plain_tables()
    out: list[str] = []
    # What a substitution runs is a command too: `echo $(git push)` runs `git push`.
    for inner in substitutions(line):
        out.extend(plain_commands(inner, depth + 1, home))
    for c in split_command(line).commands:
        w = [re.sub(r"\)+$", "", re.sub(r"^\(+", "", x)) for x in words(c)]
        w = [x for x in w if x != ""]
        while True:
            while w and (w[0] in keywords or _ASSIGNMENT.match(w[0])):
                w = w[1:]
            prog = posixpath.basename(w[0]) if w else ""
            takes = wrappers.get(prog)
            if takes is None:
                break
            i = 1
            while i < len(w):
                a = w[i]
                if a == "--":
                    i += 1
                    break
                if prog == "env" and _ASSIGNMENT.match(a):
                    i += 1
                    continue
                if prog == "env" and (a == "-S" or a.startswith("--split-string")):
                    inner = (
                        a[a.index("=") + 1 :]
                        if "=" in a
                        else (w[i + 1] if i + 1 < len(w) else None)
                    )
                    if inner:
                        out.extend(plain_commands(inner, depth + 1, home))
                    i = len(w)
                    break
                if not a.startswith("-"):
                    break
                i += 2 if a in takes else 1
            if prog == "timeout" and i < len(w):
                i += 1
            w = w[i:]
        if not w:
            continue
        prog = posixpath.basename(w[0])
        args = w[1:]
        if prog in shells:
            flag = next(
                (k for k, a in enumerate(args) if re.match(r"^-[a-zA-Z]*c[a-zA-Z]*$", a)), -1
            )
            if flag >= 0 and flag + 1 < len(args):
                out.extend(plain_commands(args[flag + 1], depth + 1, home))
        if prog == "git":
            i = 0
            while i < len(args) and args[i].startswith("-"):
                i += 2 if args[i] in git_value else 1
            args = args[i:]

        def quote(x: str) -> str:
            return "'" + x.replace("'", "") + "'" if re.search(r"[\s;&|`$()<>'\"]", x) else x

        out.append(" ".join(quote(x) for x in [prog, *(_plain_path(a, home) for a in args)]))
    return out


# --- The hardline -------------------------------------------------------------


@dataclass(frozen=True)
class HardlineRule:
    id: str
    description: str
    pattern: re.Pattern[str]


@cache
def hardline_rules() -> tuple[HardlineRule, ...]:
    spec = harness_spec.hardline()
    flags = 0
    if "i" in spec["flags"]:
        flags |= re.IGNORECASE
    if "s" in spec["flags"]:
        flags |= re.DOTALL
    return tuple(
        HardlineRule(r["id"], r["description"], re.compile(r["pattern"], flags))
        for r in spec["rules"]
    )


def hardline_command(line: str, home: str | None = None) -> HardlineRule | None:
    """The floor rule a command line trips, if any: checked raw, so splitting cannot hide one,
    and on each command in a plain spelling, so writing it differently cannot hide one either."""
    for candidate in [line, *plain_commands(line, home=home)]:
        for rule in hardline_rules():
            if rule.pattern.search(candidate):
                return rule
    return None


def protected_path(path: str, home: str | None = None) -> str:
    """The protected place a path is in (`~/.ssh`, `~/.gnupg`), or "". Paths on the remote
    machine, so only the spec's shared entries apply, never the CLI's own config. Without
    the machine's home directory, an absolute path through a directory of that name counts."""
    plain = _plain_path(path, home)
    for entry in harness_spec.hardline()["protected_paths"]:
        if entry.get("scope") != "shared":
            continue
        guarded = entry["path"]
        if plain == guarded or plain.startswith(guarded + "/"):
            return guarded
        if home is None and guarded.startswith("~/") and guarded[2:] in plain.split("/"):
            return guarded
    return ""


# --- "Always allow" -----------------------------------------------------------


def prefix(tokens: list[str]) -> list[str]:
    """The human-meaningful prefix of a command (`git commit -m x` → `git commit`)."""
    table: dict[str, int] = harness_spec.arity()["arity"]
    if (
        len(tokens) > 1
        and any(t.startswith("-") for t in tokens[1:])
        and table.get(tokens[0], 0) > 1
        and tokens[1].startswith("-")
    ):
        return tokens
    for length in range(len(tokens), 0, -1):
        n = table.get(" ".join(tokens[:length]))
        if n is not None:
            return tokens[:n]
    return tokens[:1]


def exact_only() -> frozenset[str]:
    """Commands that run another command: approved exactly, never with `*`."""
    return frozenset(harness_spec.arity()["exact_only"])


__all__ = [
    "HardlineRule",
    "Split",
    "exact_only",
    "hardline_command",
    "hardline_rules",
    "plain_commands",
    "prefix",
    "protected_path",
    "split_command",
    "substitutions",
    "words",
]
