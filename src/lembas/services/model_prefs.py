"""A person's own defaults for one model: tools and a reasoning effort.

Settings -> Models carries a section per model with the tools offered there and,
on a reasoning model, the effort a new chat starts at. Both override the
instance for this person only, and both only ever **narrow**:

- tools: a gate can be switched off, never on. What is listed is what the
  model's capabilities, the person's permissions and the instance already
  offer (`offered`), and `tools.resolve_tools` applies the row *after* those
  gates -- so a crafted POST naming something else switches off what was not on.
- effort: one of the model's own levels, or off, and only on a model an
  administrator marked as reasoning. Checked again where it is read, because a
  model's vocabulary can narrow after it was saved (`generation._narrow_efforts`).

**A chat's own setting wins.** The effort is a seed, like the model's own
default: `_new_chat` writes it onto the row, and from then on the row is what is
sent. A tool gate is read on every reply instead -- so changing it reaches chats
already open -- and a chat that switched one back on stores that explicitly
(`scope_json.families[gate] = True`), which this default does not override.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import UserModelSetting

# What `reasoning_effort` holds besides a level.
FOLLOW = ""  # the model's default, as an administrator set it
OFF = "off"  # no effort sent


def row(db: DBSession, user, model_id: str) -> UserModelSetting | None:
    if user is None or not model_id:
        return None
    return db.scalar(
        select(UserModelSetting).where(
            UserModelSetting.user_id == user.id, UserModelSetting.model_id == model_id
        )
    )


def tools_off(db: DBSession, user, model_id: str) -> frozenset[str]:
    """Gates this person switched off for this model. Empty without a row."""
    found = row(db, user, model_id)
    if found is None:
        return frozenset()
    return frozenset(str(gate) for gate in (found.tools_off_json or []))


def reasoning(model) -> bool:
    return bool((getattr(model, "capabilities_json", None) or {}).get("reasoning"))


def effort_choice(db: DBSession, user, model) -> str:
    """This person's effort for this model as stored and still valid: "" (follow
    the model), "off", or one of the model's levels."""
    from lembas.services import chat as chat_service

    if model is None or not reasoning(model):
        return FOLLOW
    found = row(db, user, model.model_id)
    value = (found.reasoning_effort if found is not None else "") or FOLLOW
    if value == OFF or value in chat_service.efforts_for(model):
        return value
    return FOLLOW


def default_effort(db: DBSession, user, model) -> str:
    """The effort a new chat with this model starts at, or "" for none: this
    person's choice, else the model's own default."""
    from lembas.services import chat as chat_service

    if model is None:
        return ""
    chosen = effort_choice(db, user, model)
    if chosen == OFF:
        return ""
    if chosen:
        return chosen
    seeded = (model.params_json or {}).get("reasoning_effort")
    return seeded if seeded in chat_service.EFFORTS else ""


def seed(db: DBSession, user, model, params: dict) -> dict:
    """`params` (the model's defaults, as `_new_chat` copies them) with this
    person's effort in place of the model's."""
    chosen = effort_choice(db, user, model)
    if chosen == OFF:
        return {k: v for k, v in params.items() if k != "reasoning_effort"}
    if chosen:
        return {**params, "reasoning_effort": chosen}
    return params


def offered(db: DBSession, user, model) -> list[dict]:
    """The tool gates this model offers this person before any default of
    theirs applies, labelled as the chat's own menu labels them, with whether
    each is on. Gates switched off already are kept, so they can come back."""
    from lembas.db.models import KIND_CHAT, Chat
    from lembas.services import prototypes as prototypes_service
    from lembas.services import tools as tools_service

    if model is None:
        return []
    stand_in = Chat(
        user_id=user.id, kind=KIND_CHAT, model_id=model.model_id,
        connection_id=model.connection_id, scope_json={},
    )
    seen: dict[str, str] = {}
    for tool in tools_service.resolve_tools(db, stand_in, user, user_defaults=False).defs:
        seen.setdefault(tools_service.gate_of(tool.family), tool.name)
    if prototypes_service.allowed(db, user):
        seen.setdefault(prototypes_service.GATE, "")
    off = tools_off(db, user, model.model_id)
    return [
        {"gate": gate, "label": tools_service.gate_label(gate, example), "on": gate not in off}
        for gate, example in sorted(seen.items())
    ]


def save(
    db: DBSession, user, model, *, tools_off_now: set[str], offered_gates: set[str], effort: str
) -> UserModelSetting | None:
    """Store this person's choices for this model. Only gates that were
    offered can be switched off; an effort that is not the model's is ignored.
    A row that would say nothing is removed rather than kept empty."""
    from lembas.services import chat as chat_service

    found = row(db, user, model.model_id)
    kept = sorted(gate for gate in tools_off_now if gate in offered_gates)
    # A gate switched off earlier that this model no longer offers stays off:
    # it is not on the form, so the form cannot have said anything about it.
    if found is not None:
        kept = sorted(
            {*kept, *(g for g in (found.tools_off_json or []) if g not in offered_gates)}
        )
    wanted = (effort or "").strip().lower()
    if not reasoning(model) or (wanted != OFF and wanted not in chat_service.efforts_for(model)):
        wanted = FOLLOW
    if not kept and wanted == FOLLOW:
        if found is not None:
            db.delete(found)
            db.commit()
        return None
    if found is None:
        found = UserModelSetting(user_id=user.id, model_id=model.model_id)
        db.add(found)
    found.tools_off_json = kept
    found.reasoning_effort = wanted
    db.commit()
    return found
