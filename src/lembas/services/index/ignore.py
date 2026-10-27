"""`.lembasignore`: which paths the index leaves out, per directory.

A `.lembasignore` in a directory applies to everything below it, in the
patterns `.gitignore` uses:

- one pattern per line; blank lines and `#` comments are ignored;
- `!pattern` takes a path back in;
- a pattern ending in `/` matches directories only;
- a pattern with a `/` anywhere but the end is anchored to the directory the
  file is in; one without matches a name at any depth;
- `*` is anything but `/`, `?` one character but `/`, `[...]` a class, and
  `**` any number of directories (`**/x`, `a/**`, `a/**/b`).

Deeper files are read after shallower ones and, within a file, later lines
after earlier ones; the last pattern that matches decides. A directory left out
takes everything below it along, and nothing below it can be taken back in --
git's rule, because a pattern that re-includes a file inside an excluded
directory has nothing to walk to.

`*` alone switches indexing off for a directory. `.git/` is always left out.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

NAME = ".lembasignore"
ALWAYS = (".git/",)


@dataclass(frozen=True)
class Rule:
    regex: re.Pattern[str]
    negate: bool
    dir_only: bool


def _translate(glob: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(glob):
        char = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if glob.startswith("/**", i) and i + 3 == len(glob):
            out.append("/.*")
            i += 3
            continue
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            close = glob.find("]", i + 1)
            if close < 0:
                out.append(re.escape(char))
            else:
                inner = glob[i + 1 : close]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                out.append("[" + inner.replace("\\", "\\\\") + "]")
                i = close
        elif char == "\\" and i + 1 < len(glob):
            i += 1
            out.append(re.escape(glob[i]))
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


def parse(text: str) -> list[Rule]:
    rules: list[Rule] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        if line.startswith("\\"):
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = "/" in line
        line = line.lstrip("/")
        body = _translate(line)
        prefix = "" if anchored else "(?:.*/)?"
        rules.append(Rule(re.compile(f"^{prefix}{body}$"), negate, dir_only))
    return rules


@dataclass
class Matcher:
    """Every `.lembasignore` of one tree, by the directory it sits in."""

    by_dir: dict[str, list[Rule]] = field(default_factory=dict)

    @classmethod
    def build(cls, files: Iterable[tuple[str, str]]) -> Matcher:
        """From `(path of the .lembasignore, its text)` pairs."""
        matcher = cls()
        matcher.by_dir["/"] = parse("\n".join(ALWAYS))
        for path, text in files:
            directory = path.rsplit("/", 1)[0] or "/"
            matcher.by_dir.setdefault(directory, []).extend(parse(text))
        return matcher

    def _decide(self, path: str, is_dir: bool) -> bool | None:
        verdict: bool | None = None
        # Shallow to deep, so a deeper file's word comes last.
        for directory in sorted(self.by_dir, key=lambda d: (d != "/", d.count("/"))):
            if directory != "/" and not path.startswith(directory.rstrip("/") + "/"):
                continue
            relative = path[len(directory.rstrip("/")) + 1 :] if directory != "/" else path[1:]
            for rule in self.by_dir[directory]:
                if rule.dir_only and not is_dir:
                    continue
                if rule.regex.match(relative):
                    verdict = not rule.negate
        return verdict

    def ignored(self, path: str) -> bool:
        """Whether a file at `path` is left out (it or a directory above it)."""
        parts = path.strip("/").split("/")
        for depth in range(1, len(parts)):
            if self._decide("/" + "/".join(parts[:depth]), True):
                return True
        return bool(self._decide(path, False))


__all__ = ["ALWAYS", "NAME", "Matcher", "parse"]
