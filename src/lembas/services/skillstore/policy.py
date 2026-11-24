"""Who may change what in the store: the settings, the caller, the decision.

**The main policy alone decides, with no special cases** (three levels):

- `manual` -- only a person edits, by hand in the web UI;
- `approve` -- an agent's change waits in the queue until a person approves;
- `auto` -- an agent's change applies at once.

Skills have one level (`skills_edit`, default *approve*). Notes have two:
adding a note, adding to one and confirming one (`notes_add`, default *auto*:
free), and changing or removing what **somebody else** wrote (`notes_change`,
default *approve*, settable per kind in `notes_kinds`). A note is one's own
when the same account wrote it with the same token; for a person in the web
UI, when their account did. **No decision here reads anything a caller says
about itself**: the account and the token are the server's knowledge, the
data group comes from a session the token owns, and the harness, the model
and the session are labels.

**Every door calls `decide`** -- the MCP tools, a chat's `skill_manage`, the
web forms, the queue's approval -- so no route applies a change the policy
would have held.

Who decides a waiting change: for skills (shared by everyone) an instance
administrator; for a note, a person who can see it and wrote it, proposed the
change's own note, or administers the instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import DEFAULT_GROUP, User
from lembas.services import settings_store

KEY = "skills"

MANUAL = "manual"
APPROVE = "approve"
AUTO = "auto"
LEVELS = (MANUAL, APPROVE, AUTO)

ORIGIN_WEB = "web"
ORIGIN_MCP = "mcp"
ORIGIN_CHAT = "chat"
ORIGIN_REMOTE = "remote"
ORIGIN_IMPORT = "import"

SKILL_APPROVERS = ("admins", "managers")
NOTE_APPROVERS = ("involved", "admins")

APPLY = "apply"
QUEUE = "queue"
REFUSE = "refuse"

# What a note may be called; anything else a note says is read as "note".
KINDS = ("note", "plan", "finding", "incident", "crash", "decision", "reference")


def defaults() -> dict[str, Any]:
    return {
        "skills_edit": APPROVE,
        "notes_add": AUTO,
        "notes_change": APPROVE,
        # kind -> level, for changing somebody else's note of that kind.
        "notes_kinds": {},
        # Who decides a waiting change (`may_decide_skill`, `may_decide_note`):
        # skills -- "admins" (instance administrators) or "managers" (they and
        # the organisations' owners and admins); notes -- "involved" (the note's
        # author, the proposing account, an administrator; each only if they
        # can see the note) or "admins".
        "skills_approvers": "admins",
        "notes_approvers": "involved",
        # Kinds every data group sees.
        "shared_kinds": [],
        "remote_address": "",
        "remote_branch": "main",
        "remote_username": "",
        # Fernet-encrypted (services/crypto.py); never rendered.
        "remote_token_encrypted": "",
        "remote_ssh_key_encrypted": "",
        # Whether a sync with this remote has succeeded once: until then a remote
        # that shares no history is adopted rather than called diverged.
        "remote_adopted": False,
        "sync_state": "off",
        "sync_message": "",
        "sync_at": "",
        "sync_minutes": 15,
        # The organisation's old `/skills/<name>/` files already brought in, by
        # name: each once, so a skill removed here later does not come back.
        "tree_imported": [],
    }


def settings(db: DBSession) -> dict[str, Any]:
    values = defaults()
    values.update(settings_store.get_group(db, KEY))
    for key in ("skills_edit", "notes_add", "notes_change"):
        if values.get(key) not in LEVELS:
            values[key] = defaults()[key]
    if values.get("skills_approvers") not in SKILL_APPROVERS:
        values["skills_approvers"] = "admins"
    if values.get("notes_approvers") not in NOTE_APPROVERS:
        values["notes_approvers"] = "involved"
    kinds = values.get("notes_kinds")
    values["notes_kinds"] = (
        {str(k): v for k, v in kinds.items() if v in LEVELS} if isinstance(kinds, dict) else {}
    )
    shared = values.get("shared_kinds")
    values["shared_kinds"] = [str(k) for k in shared] if isinstance(shared, list) else []
    try:
        values["sync_minutes"] = min(max(int(values.get("sync_minutes") or 15), 1), 1440)
    except (TypeError, ValueError):
        values["sync_minutes"] = 15
    return values


def save(db: DBSession, changes: dict[str, Any]) -> None:
    settings_store.update(db, changes, key=KEY)


@dataclass(frozen=True)
class Who:
    """Who is asking, as every change records it."""

    account_id: str = ""
    account_name: str = ""
    is_admin: bool = False
    # A person who decides waiting changes to skills (`may_decide_skill`).
    approver: bool = False
    # A person acting by hand in the web UI; False for every agent and program.
    person: bool = False
    # What the caller is known by to the server: a digest of the token it came
    # with ("web" for a person's hand, "chat" for a chat here). **This, with
    # the account, is what "its own note" is decided on** -- never the harness
    # below, which an MCP client names itself.
    key: str = ""
    token_name: str = ""
    harness: str = ""
    model: str = ""
    # Whether the model's name is only the caller's own claim (a tool's
    # argument, the client's metadata) and not what the server knows from the
    # session the call was made for.
    model_said: bool = False
    session: str = ""
    origin: str = ORIGIN_MCP
    # The data group this caller works under (what a new note is stamped with)...
    data_group: str = DEFAULT_GROUP
    # ... and the groups whose notes it may read.
    groups: frozenset[str] = field(default_factory=lambda: frozenset({DEFAULT_GROUP}))
    # Whether the account may have a model write here at all (`tools.skills.write`).
    may_write: bool = True

    def trailers(self, reason: str = "") -> dict[str, str]:
        return {
            "Account": self.account_name,
            "Account-Id": self.account_id,
            "Token": self.token_name,
            "Harness": self.harness,
            "Model": "" if self.model_said else self.model,
            "Model-Said": self.model if self.model_said else "",
            "Session": self.session,
            "Origin": self.origin,
            "Reason": reason,
        }


def person(db: DBSession, user: User) -> Who:
    """A person at the web UI: every group they may use, by hand."""
    from lembas.services import data_groups

    groups = frozenset(group.id for group in data_groups.usable(db, user))
    return Who(
        account_id=user.id,
        account_name=user.name,
        is_admin=user.is_admin,
        approver=may_decide_skill(db, user),
        person=True,
        key="web",
        harness="web",
        origin=ORIGIN_WEB,
        data_group=DEFAULT_GROUP,
        groups=groups or frozenset({DEFAULT_GROUP}),
    )


def agent(
    db: DBSession,
    user: User,
    *,
    harness: str,
    token_id: str = "",
    token_name: str = "",
    model: str = "",
    model_said: bool = False,
    session: str = "",
    data_group: str = DEFAULT_GROUP,
    origin: str = ORIGIN_MCP,
) -> Who:
    """An agent of this account, working under one data group and no other."""
    from lembas.security import permissions

    allowed = permissions.resolve(db, user)
    group = data_group or DEFAULT_GROUP
    return Who(
        account_id=user.id,
        account_name=user.name,
        is_admin=False,
        person=False,
        key=token_key(token_id) if token_id else ("chat" if origin == ORIGIN_CHAT else ""),
        token_name=" ".join((token_name or "").split())[:80],
        harness=" ".join((harness or "").split())[:80] or "unknown",
        model=" ".join((model or "").split())[:200],
        model_said=bool(model_said and model),
        session=" ".join((session or "").split())[:120],
        origin=origin,
        data_group=group,
        groups=frozenset({group}),
        may_write=bool(allowed.get("tools.skills.write", True)),
    )


def in_group(who: Who, group: str) -> Who:
    return replace(who, data_group=group or DEFAULT_GROUP)


def _by_level(level: str) -> str:
    return {MANUAL: REFUSE, APPROVE: QUEUE, AUTO: APPLY}[level]


def decide_skill(db: DBSession, who: Who) -> str:
    """What happens to a change to a skill asked for by `who`."""
    level = settings(db)["skills_edit"]
    if who.person:
        # By hand: an administrator's edit is the policy's "a person edits".
        # Anybody else's hand is held like an agent's unless everything applies.
        return APPLY if who.approver or level == AUTO else QUEUE
    if not who.may_write:
        return REFUSE
    return _by_level(level)


def token_key(token_id: str) -> str:
    """What a token is known by in a note's header: a digest of its id (the
    id itself stays in the database)."""
    import hashlib

    return hashlib.sha256(f"lembas-store:{token_id}".encode()).hexdigest()[:24]


def own_note(who: Who, author_id: str, author_key: str) -> bool:
    """Whether a note is this caller's own: the same account, and for an
    agent the same token. Nothing the caller says about itself is asked."""
    if not author_id or author_id != who.account_id:
        return False
    if who.person:
        return True
    return bool(who.key) and who.key == author_key


def decide_note(
    db: DBSession, who: Who, action: str, *, kind: str = "", author_id: str = "",
    author_key: str = "",
) -> str:
    """What happens to a change to a note. `action`: add, append, confirm
    (the free ones), or change, archive, remove (somebody's writing)."""
    values = settings(db)
    if who.person:
        return APPLY
    if not who.may_write:
        return REFUSE
    if action in ("add", "append", "confirm"):
        return _by_level(values["notes_add"])
    if own_note(who, author_id, author_key):
        return APPLY
    return _by_level(values["notes_kinds"].get(kind, values["notes_change"]))


def may_decide_skill(db: DBSession, user: User) -> bool:
    """Whether this person decides waiting changes to skills (and applies
    their own by hand): the one place that says who approves skills."""
    if user.is_admin:
        return True
    return settings(db)["skills_approvers"] == "managers" and bool(user.manages_org)


def may_decide_note(
    db: DBSession, user: User, *, sees: bool, author_id: str, proposer_id: str
) -> bool:
    """Whether this person decides a waiting change to a note: the one place
    that says who approves notes. Nobody decides on a note they cannot see."""
    if not sees:
        return False
    if user.is_admin:
        return True
    if settings(db)["notes_approvers"] == "admins":
        return False
    return user.id in (author_id, proposer_id)


def describe(db: DBSession, who: Who) -> dict[str, str]:
    """What this caller may do, in the words `skills_policy` answers with."""
    values = settings(db)
    words = {
        APPLY: "applies at once",
        QUEUE: "waits for a person's approval",
        REFUSE: "is not taken: only a person edits here, by hand",
    }
    return {
        "skills": words[decide_skill(db, who)],
        "notes_add": words[decide_note(db, who, "add")],
        "notes_change": words[decide_note(db, who, "change", author_id="-")],
        "levels": (
            f"skills {values['skills_edit']}; adding notes {values['notes_add']}; "
            f"changing others' notes {values['notes_change']}"
            + "".join(f"; {kind} notes {level}" for kind, level in values["notes_kinds"].items())
        ),
    }


__all__ = [
    "APPLY",
    "APPROVE",
    "AUTO",
    "KEY",
    "KINDS",
    "LEVELS",
    "NOTE_APPROVERS",
    "SKILL_APPROVERS",
    "may_decide_note",
    "MANUAL",
    "ORIGIN_CHAT",
    "ORIGIN_IMPORT",
    "ORIGIN_MCP",
    "ORIGIN_REMOTE",
    "ORIGIN_WEB",
    "QUEUE",
    "REFUSE",
    "Who",
    "agent",
    "decide_note",
    "decide_skill",
    "defaults",
    "describe",
    "in_group",
    "may_decide_skill",
    "own_note",
    "person",
    "save",
    "token_key",
    "settings",
]
