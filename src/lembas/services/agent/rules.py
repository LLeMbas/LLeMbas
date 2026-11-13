"""Which permission rule decides a call (harness spec 3.8.0,
`permission/rules.md`; cases: `conformance/rules.json`).

A rule is `{permission, pattern, action}` with a **layer** -- where it was
made: `default` (the spec's `defaults.json`), `user` (the person's own
configuration), `project`, and `made` (during the session: an "always" answer
or a rule set in a menu). Rules are read in that order, and within a layer in
the order written.

The last matching rule is the candidate. A candidate somebody wrote for
themselves (`default`, `user`) decides as it is: the person's own file is
last-match-wins, as it always was. A candidate that came after -- a
`project`'s, or one `made` -- gives way to whichever of these is stricter than
it: a **narrower** written rule (a broad allow never unlocks what was written
for the narrow case), **the person's own answer** (the `user` layer's last
matching rule), and, for an "always" answer, a **written deny**.

Pure: no database, no settings. The same file serves a rule typed into a
configuration and one a menu wrote, which is the point of the spec's section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

ALLOW, ASK, DENY = "allow", "ask", "deny"
RANK = {ALLOW: 0, ASK: 1, DENY: 2}

LAYER_DEFAULT, LAYER_USER, LAYER_PROJECT, LAYER_MADE = "default", "user", "project", "made"
# Rules somebody wrote down, as against those made while a session runs.
WRITTEN = (LAYER_DEFAULT, LAYER_USER, LAYER_PROJECT)
BY_ALWAYS, BY_MENU = "always", "menu"


@dataclass(frozen=True)
class Rule:
    permission: str
    pattern: str
    action: str
    layer: str = LAYER_USER
    # For a `made` rule: an "always" answer, or a rule set in a menu.
    by: str = ""

    def said(self) -> dict[str, str]:
        return {"permission": self.permission, "pattern": self.pattern, "action": self.action}


@lru_cache(maxsize=2048)
def _compiled(pattern: str) -> re.Pattern[str]:
    """The rules' wildcard: `*` is any run of characters (a `/` and a space
    included), `?` one; a pattern ending in ` *` also takes the bare command,
    so `ls *` is `ls` and `ls -la`."""
    tail = pattern.endswith(" *")
    body = pattern[:-2] if tail else pattern
    body = re.escape(body.replace("\\", "/")).replace(r"\*", ".*").replace(r"\?", ".")
    return re.compile(body + ("( .*)?" if tail else ""), re.DOTALL)


def match(text: str, pattern: str) -> bool:
    return _compiled(pattern).fullmatch(str(text).replace("\\", "/")) is not None


def from_config(config: dict[str, Any] | None, layer: str) -> list[Rule]:
    """A configuration's permission map as rules, in the order written: a
    key's value is an action (for everything, `*`) or a map of pattern to
    action."""
    out: list[Rule] = []
    for permission, value in (config or {}).items():
        if isinstance(value, str):
            out.append(Rule(permission, "*", value, layer))
        else:
            out.extend(
                Rule(permission, pattern, action, layer)
                for pattern, action in (value or {}).items()
            )
    return out


def defaults() -> list[Rule]:
    """The rules every session starts from (`permission/defaults.json`)."""
    from lembas.services import harness_spec

    return from_config(harness_spec._json("permission/defaults.json")["rules"], LAYER_DEFAULT)


def narrower(a: Rule, b: Rule) -> bool:
    """Whether `a` speaks of less than `b`: all its permission and pattern
    take in, `b`'s take in too, and they are not the same (`read *.env` under
    `read *`; `read *` under `* *`)."""
    if (a.permission, a.pattern) == (b.permission, b.pattern):
        return False
    return match(a.permission, b.permission) and match(a.pattern, b.pattern)


def _matches(rule: Rule, permission: str, pattern: str) -> bool:
    return match(permission, rule.permission) and match(pattern, rule.pattern)


def _last(rules, test) -> Rule | None:
    return next((r for r in reversed(rules) if test(r)), None)


def deciding(permission: str, pattern: str, rules: list[Rule]) -> Rule | None:
    """The rule that decides what one call asks for (§2), or None where no
    rule matches -- the call is then asked about or allowed by its class and
    the mode. `rules` in the order read."""
    hits = [r for r in rules if _matches(r, permission, pattern)]
    if not hits:
        return None
    candidate = hits[-1]
    if candidate.layer in (LAYER_DEFAULT, LAYER_USER):
        return candidate
    floors = [
        r for r in hits
        if r is not candidate and r.layer in WRITTEN and narrower(r, candidate)
    ]
    own = _last(hits, lambda r: r.layer == LAYER_USER)
    if own is not None:
        floors.append(own)
    if candidate.layer == LAYER_MADE and candidate.by == BY_ALWAYS:
        written = _last(hits, lambda r: r.layer in WRITTEN)
        if written is not None and written.action == DENY:
            floors.append(written)
    best = candidate
    for rule in floors:
        if RANK[rule.action] > RANK[best.action]:
            best = rule
    return best


def action(permission: str, pattern: str, rules: list[Rule]) -> str | None:
    found = deciding(permission, pattern, rules)
    return found.action if found is not None else None


def strictest(actions) -> str | None:
    """Several patterns in one call (a line of several commands, several
    paths): each is decided, and the strictest answer is the call's."""
    found = [a for a in actions if a is not None]
    return max(found, key=RANK.__getitem__) if found else None


def judge(new: Rule, rules: list[Rule]) -> dict[str, Any]:
    """What making a rule would do (§3), before anything is changed:
    `unchanged` -- the action is already in force; `refused`, with the rule in
    its way as `by`; or `made`, with `still`: the stricter, narrower written
    rules that go on applying, in the order read."""
    if action(new.permission, new.pattern, rules) == new.action:
        return {"result": "unchanged"}
    written = [r for r in rules if r.layer in WRITTEN]
    in_the_way = _last(
        written,
        lambda r: RANK[r.action] > RANK[new.action]
        and (r.layer == LAYER_USER or r.action == DENY)
        and match(new.permission, r.permission)
        and match(new.pattern, r.pattern),
    )
    if in_the_way is not None:
        return {"result": "refused", "by": in_the_way.said()}
    still = [
        r.said() for r in written
        if RANK[r.action] > RANK[new.action] and narrower(r, new)
    ]
    return {"result": "made", "still": still}


__all__ = [
    "ALLOW", "ASK", "BY_ALWAYS", "BY_MENU", "DENY", "LAYER_DEFAULT", "LAYER_MADE",
    "LAYER_PROJECT", "LAYER_USER", "Rule", "action", "deciding", "defaults", "from_config",
    "judge", "match", "narrower", "strictest",
]
