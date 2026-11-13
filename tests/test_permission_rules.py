"""Which permission rule decides (harness spec 3.8.0, `permission/rules.md`).

The cases of the spec are `conformance/rules.json` (`tests/test_harness_spec.py`).
Here: the two places this server judges a call by rules -- a subagent against
what its main agent allowed it, and an agent chat against the administrator's
lists and the chat's "always allow" answers -- give the answers that order
gives.
"""

from __future__ import annotations

import pytest

from lembas.services import team
from lembas.services.agent import policy, rules
from lembas.services.tools import RISK_EXECUTE


def test_a_broad_grant_leaves_a_narrower_default_asking():
    """The main agent allowed a subagent every read. `.env` still asks: the
    default rule for the narrow case was written on purpose, and a later,
    broader rule does not change it. To allow the narrow thing, name it."""
    broad = [{"tool": "read", "pattern": "*"}]
    assert team.spec_evaluate("read", "read", "notes.txt", "manual", broad)[0] == "allow"
    assert team.spec_evaluate("read", "read", ".env", "manual", broad)[0] == "ask"
    named = [*broad, {"tool": "read", "pattern": ".env"}]
    assert team.spec_evaluate("read", "read", ".env", "manual", named)[0] == "allow"


def test_a_grant_for_a_command_covers_that_command_only():
    grants = [{"tool": "bash", "pattern": "make *"}]
    assert team.spec_evaluate("bash", "execute", "make all", "manual", grants)[0] == "allow"
    assert team.spec_evaluate("bash", "execute", "make all && rm -rf x", "manual", grants)[0] == (
        "ask"
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("git status", policy.ALLOW),       # allowed by the chat's "always"
        ("git push origin main", policy.ASK),  # the always-ask list is the person's own answer
        ("make all", policy.ALLOW),         # allowed by the administrator
        ("rm -rf build", policy.ASK),       # nobody said: the mode asks
    ],
)
def test_the_lists_of_an_agent_chat_decide_as_the_spec_orders_them(command, expected):
    """The administrator's allow list and always-ask list are one `user`
    layer (allows, then asks); a chat's "always allow" answers are `made`.
    `policy.decide` -- an always-ask entry before any allow -- gives exactly
    what permission/rules.md §2 gives for those layers."""
    allow, ask, always = ("make *",), ("git push *",), ("git *",)
    read = [
        *(rules.Rule("bash", p, rules.ALLOW, rules.LAYER_USER) for p in allow),
        *(rules.Rule("bash", p, rules.ASK, rules.LAYER_USER) for p in ask),
        *(rules.Rule("bash", p, rules.ALLOW, rules.LAYER_MADE, rules.BY_ALWAYS) for p in always),
    ]
    by_spec = rules.action("bash", command, read) or rules.ASK
    decided = policy.decide(
        mode=policy.MODE_MANUAL, risk=RISK_EXECUTE, tool_name="bash", command=command,
        allow=(*allow, *always), deny=ask,
    )
    assert decided.verdict == by_spec == expected


def test_the_wildcard_is_the_specs():
    assert rules.match("ls", "ls *") and rules.match("ls -la", "ls *")
    assert not rules.match("lsblk", "ls *")
    assert rules.match("config/.env", "*.env") and not rules.match(".env.local", "*.env")
    assert rules.match("a?c", "a?c") and rules.match("abc", "a?c")
