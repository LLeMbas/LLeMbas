"""Agentic execution: running commands and touching files on the model's behalf.

Four parts, and the split is the safety argument. `policy` decides what may
happen without asking and knows nothing about how anything runs. `base` is the
interface a target implements. Nothing runs on this machine: an agent chat
runs on a linked device (`services/device_link.py`), somebody's own machine
running the LLeMbas CLI, which decides what it allows; `terminal` holds the
shell panel open on it, and `shell` reads a command line the harness spec's
way.

The mode is enforced in the generation loop, not in the prompt. A model is told
which mode it is in so it can behave sensibly, but being told is not what stops
it: everything it reads is untrusted, and a rule written only into a system
message is a rule a poisoned README can argue with.
"""

from lembas.services.agent.policy import (
    MODE_AUTO,
    MODE_EDIT,
    MODE_MANUAL,
    MODE_PLAN,
    MODES,
    Decision,
    decide,
)

__all__ = [
    "MODES",
    "MODE_AUTO",
    "MODE_EDIT",
    "MODE_MANUAL",
    "MODE_PLAN",
    "Decision",
    "decide",
]
