"""Which model a subagent runs on, and when subagents are offered (harness spec
3.3.0, `subagents.md`).

A **subagent** is any agent a chat's agent starts to work for it: a `task`
subagent (in the foreground or as a background job) and a team's member
(`services/team_run.py`) alike. Three rules, the same in the LLeMbas CLI:

- **The chat's own model, always** -- unless the person has *named* another
  model in this chat's own messages: its id, its served id, `connection/model`
  or its display name, as a whole word, without case, in a message the person
  typed (never a model's text, a tool's output, or a marked message). There is
  no setting for a subagent's model anywhere. `task`'s and `team_add`'s `model`
  naming any other model is refused with `not_named`.
- **Capacity, the logical restrictions only**: another model on the chat
  model's one-model server is refused (`same_group`) -- it would unload the
  lead's model; the chat's own model serving one request at a time is refused
  (`one_request`) -- the subagent would take the lead's only slot. No fallback:
  nothing is ever picked for the person.
- **Offered** when subagents are enabled and at least one model could serve as
  a subagent: the chat's model when it serves two requests at once, or any
  other usable model outside its one-model server.

The pure half (`refusal`, `could_serve`, `named_in`) takes plain values and is
what `tests/test_harness_spec.py` drives through the spec's
`conformance/subagents.json`; the rest reads them off the rows.

Every text a model reads comes from the spec (`loop.json` `subagents`), word for
word, and is never translated.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from lembas.services import harness_spec

# A whole word (subagents.md §2): before the name, not a letter, digit, `_`,
# `-`, `/`, `.` or `:`; after it, not a letter, digit, `_`, `-` or `/`, nor a
# `.` or `:` followed by a letter or a digit. `\w` is a letter, a digit or `_`.
_BEFORE = r"(?<![\w\-/.:])"
_AFTER = r"(?![\w\-/]|[.:][^\W_])"


def _spec() -> dict[str, Any]:
    return harness_spec.loop()["subagents"]


def texts() -> dict[str, str]:
    """`not_named`, `same_group`, `one_request`: said to a model, word for word."""
    return dict(_spec()["texts"])


def setting_texts() -> dict[str, str]:
    """How the setting is shown (`label`, `min`, `max`) and `own`, the line
    beside it where the chat's model cannot serve subagents itself."""
    return dict(_spec()["setting"])


def note_own() -> str:
    """The session note's `team_own` line."""
    return harness_spec.note()["team_own"]


# --- Named (§2) ------------------------------------------------------------------------
def pattern(name: str) -> re.Pattern[str] | None:
    """The whole-word pattern for one name: letters without case, a run of white
    space in it matching any run."""
    words = str(name or "").split()
    if not words:
        return None
    body = r"\s+".join(re.escape(w) for w in words)
    return re.compile(_BEFORE + body + _AFTER, re.IGNORECASE)


def named_in(texts_: Iterable[str], names: Iterable[str]) -> bool:
    """Whether any of `names` occurs as a whole word in any of the texts."""
    patterns = [p for p in (pattern(n) for n in names) if p is not None]
    return any(p.search(text or "") for text in texts_ for p in patterns)


# --- The model as these rules see it ----------------------------------------------------
@dataclass(frozen=True)
class Seat:
    """One model, as far as the rules read it: who it is, the names a person may
    call it by, how many requests it serves at once (None: no limit), and the
    one-model server it is on (None: not on one)."""

    key: str
    names: tuple[str, ...] = ()
    parallel: int | None = None
    group: str | None = None

    @property
    def serves_two(self) -> bool:
        return self.parallel is None or int(self.parallel) >= 2


def seat(model) -> Seat | None:
    """A `Model` row as a `Seat`."""
    if model is None:
        return None
    connection = getattr(model, "connection", None)
    names = [str(model.model_id)]
    display = (getattr(model, "display_name", "") or "").strip()
    if display:
        names.append(display)
    if connection is not None:
        names.append(f"{connection.name}/{model.model_id}")
        provider = getattr(connection, "provider_name", "")
        if provider:
            names.append(f"{provider}/{model.model_id}")
    group = None
    if connection is not None and getattr(connection, "one_model_at_a_time", False):
        group = str(connection.id)
    return Seat(
        key=str(model.id),
        names=tuple(dict.fromkeys(n for n in names if n)),
        parallel=model.parallel_limit,
        group=group,
    )


# --- The rules (pure) -------------------------------------------------------------------
def refusal(session: Seat | None, target: Seat | None, *, named: bool) -> str:
    """Why a subagent cannot run on `target` (None: the session's own model)
    for a session on `session`, or "" -- in the spec's order: `not_named`,
    `same_group`, `one_request`."""
    if session is None:
        return ""
    words = texts()
    other = target is not None and target.key != session.key
    if other:
        if not named:
            return words["not_named"]
        if target.group is not None and target.group == session.group:
            return words["same_group"]
        return ""
    if session.parallel is not None and int(session.parallel) <= 1:
        return words["one_request"]
    return ""


def could_serve(session: Seat | None, usable: Iterable[Seat]) -> bool:
    """§4.2: the session's model serving two at once, or any other usable model
    not on its one-model server."""
    if session is None:
        return False
    if session.serves_two:
        return True
    return any(
        m.key != session.key and (m.group is None or m.group != session.group) for m in usable
    )


def needs_named(session: Seat | None, usable: Iterable[Seat]) -> bool:
    """The session's model cannot serve subagents itself, but another could:
    the note's `team_own` and the setting's `own` apply."""
    return session is not None and not session.serves_two and could_serve(session, usable)


# --- Off the rows -----------------------------------------------------------------------
def person_texts(db, chat_id: str) -> list[str]:
    """What the person typed in this chat: user turns that are not the
    harness's (`machine`). Compacted ones included -- they stay in the table."""
    from lembas.db.models import ROLE_USER, Message

    rows = db.scalars(
        select(Message.content).where(
            Message.chat_id == chat_id,
            Message.role == ROLE_USER,
            Message.machine.is_(False),
        )
    )
    return [r or "" for r in rows]


def usable_models(db, user, chat=None, model=None) -> list:
    """The models this person may use in the chat's data group -- or, before a
    chat exists, in `model`'s."""
    from lembas.services import chat as chat_service
    from lembas.services import data_groups

    if chat is not None:
        group = data_groups.for_chat(db, chat)
    elif model is not None:
        group = data_groups.connection_groups(db, user).get(
            model.connection_id, data_groups.DEFAULT_GROUP
        )
    else:
        group = None
    return chat_service.available_models(db, user, group)


def session_model(db, chat):
    from lembas.services import chat as chat_service

    return chat_service.model_for(db, chat)


def offered_for(db, user, chat=None, model=None) -> bool:
    """Whether at least one model could serve this chat's (or, with no chat,
    `model`'s) subagents (§4.2). The switch and the permission are the caller's
    (`team_run.subagents_allowed`)."""
    if model is None and chat is not None:
        model = session_model(db, chat)
    if model is None:
        return False
    usable = [s for s in (seat(m) for m in usable_models(db, user, chat, model)) if s]
    return could_serve(seat(model), usable)


def own_note(db, user, chat=None, model=None) -> str:
    """`subagents.setting.own` where the chat's model cannot serve subagents
    itself and another could, else "" -- what a UI says beside the setting."""
    if model is None and chat is not None:
        model = session_model(db, chat)
    if model is None:
        return ""
    usable = [s for s in (seat(m) for m in usable_models(db, user, chat, model)) if s]
    return setting_texts()["own"] if needs_named(seat(model), usable) else ""


def view(db, user, chat=None, model=None) -> dict[str, Any]:
    """What a UI needs to know before it shows the Subagents setting -- for a
    chat, or before one exists for `model` (`/api/v2`): whether subagents are
    offered (`team_run.offered`: enabled, permitted, some model could serve),
    whether they need a model the person names (`named_only`: the chat's own
    serves one request at a time), and the instance's cap on the setting."""
    from lembas.services import team_run

    offered = team_run.offered(db, chat, user, model) if (chat or model) else False
    return {
        "offered": bool(offered),
        "named_only": bool(offered and own_note(db, user, chat, model)),
        "cap": team_run.cap(db),
    }


def check(db, owner, lead_chat, target, *, session=None) -> str:
    """The refusal for a subagent of `lead_chat`'s session on `target` (None:
    the session's model), or "". `lead_chat` is the chat whose person's words
    count: the chat itself for `task`, the team's lead chat for `team_add`."""
    if session is None:
        session = session_model(db, lead_chat)
    s_seat, t_seat = seat(session), seat(target)
    named = False
    if t_seat is not None and s_seat is not None and t_seat.key != s_seat.key:
        named = named_in(person_texts(db, lead_chat.id), t_seat.names)
    return refusal(s_seat, t_seat, named=named)


def pick(db, owner, chat, wanted: str):
    """The model `wanted` names among those usable in the chat: its id, its
    served id, `connection/model` or its display name, without case. None for
    no such model, or more than one."""
    wanted = str(wanted or "").strip()
    if not wanted:
        return None
    candidates = usable_models(db, owner, chat)
    folded = wanted.casefold()
    exact = [m for m in candidates if folded in {n.casefold() for n in seat(m).names[:1]}]
    if len(exact) == 1:
        return exact[0]
    matches = [m for m in candidates if folded in {n.casefold() for n in seat(m).names}]
    return matches[0] if len(matches) == 1 else None


__all__ = [
    "Seat",
    "check",
    "could_serve",
    "named_in",
    "needs_named",
    "note_own",
    "offered_for",
    "own_note",
    "pattern",
    "person_texts",
    "pick",
    "refusal",
    "seat",
    "setting_texts",
    "texts",
    "usable_models",
    "view",
]
