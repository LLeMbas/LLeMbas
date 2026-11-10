"""Which model a scheduled run runs on: the first free one, in order.

A schedule names a first choice. Behind it stands the **scheduling list**: the
models an administrator lets scheduled work use, in order (Admin → Scheduling),
narrowed by each person to the ones they want (Settings → Scheduled). At fire
time the first model in that order that is **free** runs the work; when none
is, the run waits and asks again (`runs.py`), and is skipped when its window
closes. A scheduled run never takes a model away from somebody it is
answering.

## Free

- **Hosted, or no stated limit** (`Model.parallel_limit` is None on a
  connection that holds many models): always free. A hosted API queues for us.
- **A connection that holds one model at a time** (llama-swap): free when
  nothing of ours is being answered on *any* of its models -- switching the
  model would end that reply's cached prompt, or the reply itself -- when the
  model has a slot left (`parallel`, else one), and when the server is not
  starting a model already (`/running`). What runs there for somebody outside
  this instance cannot be seen, and is not guessed at.
- **A model with `parallel` slots**: free while fewer of ours than that are
  being answered on it.
- **A connection whose health check found it down**: not free; the next
  model in order is tried rather than a run that will fail.

"Ours" is every reply this instance is writing (`generation`), every request
the `/v1` relay is passing through (`api/v1/openai.py:in_flight_on`), and the
runs that have chosen a model and not yet started (`reserve`), so two
schedules due at the same minute do not both take the one free slot.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Message, Model, Schedule, User

log = logging.getLogger(__name__)

# Settings → Scheduled: the person's narrowing of the instance's list.
PERSON_KEY = "schedule_models"

REASON_PINNED = "pinned"
REASON_SWITCHED = "switched"

# Runs that have chosen a model and not yet started their reply: model row id
# -> how many. Counted as ours while they wait for `wake_chat`.
_PENDING: Counter[str] = Counter()
_CHOOSING = asyncio.Lock()


@dataclass
class Choice:
    """What `choose` decided. `model` is None when nothing was free; `busy`
    says why each model in order was passed over, as (label, reason)."""

    model: Model | None = None
    reason: str = ""
    switched_from: str = ""
    busy: list[tuple[str, str]] = field(default_factory=list)

    @property
    def waiting_on(self) -> dict[str, str]:
        """The first model passed over, as progress says it (spec 3.4.0):
        `{model, reason}` with reason busy, slots, group or loading; empty
        when none was named."""
        for label, why in self.busy:
            if label:
                return {"model": label, "reason": busy_kind(why)}
        return {}

    @property
    def why_waiting(self) -> str:
        return "; ".join(f"{label}: {why}" for label, why in self.busy)


def instance_list(db: DBSession) -> list[str]:
    from lembas.services import settings_store

    return list(settings_store.schedules(db).get("models") or [])


def person_list(user: User | None) -> list[str]:
    stored = ((getattr(user, "settings_json", None) or {}).get(PERSON_KEY)) or []
    return [str(m) for m in stored if isinstance(m, str) and m]


def scheduling_list(db: DBSession, user: User | None) -> list[str]:
    """The instance's list, narrowed to the person's choice when they made one
    (in the instance's order: the administrator orders, a person picks)."""
    offered = instance_list(db)
    mine = set(person_list(user))
    if not mine:
        return offered
    narrowed = [m for m in offered if m in mine]
    return narrowed or offered


def offered_models(db: DBSession, user: User | None, group: str | None = None) -> list[Model]:
    """The scheduling list as rows this person may use (in `group` when given),
    or every chat model they may use when the instance has no list."""
    from lembas.services import chat as chat_service

    available = chat_service.available_models(db, user, group)
    wanted = instance_list(db)
    if not wanted:
        return available
    by_id: dict[str, Model] = {}
    for row in available:
        by_id.setdefault(row.model_id, row)
    return [by_id[m] for m in wanted if m in by_id]


def candidates(db: DBSession, owner: User, schedule: Schedule) -> list[Model]:
    """The models a run of this schedule may use, in the order they are tried:
    its first choice, then the scheduling list. Only models the owner may use
    in the schedule's data group -- a run must not carry its group's memories
    to a model of another."""
    from lembas.services import chat as chat_service

    group = schedule.data_group_id or None
    available = chat_service.available_models(db, owner, group)
    if not available:
        return []
    first: Model | None = None
    if schedule.model_id:
        first = next(
            (
                m
                for m in available
                if m.model_id == schedule.model_id
                and (not schedule.connection_id or m.connection_id == schedule.connection_id)
            ),
            None,
        ) or next((m for m in available if m.model_id == schedule.model_id), None)
    if first is None:
        listed = scheduling_list(db, owner)
        default = chat_service.default_model(db, owner)
        wanted = listed[0] if listed else (default[0] if default else "")
        first = next((m for m in available if m.model_id == wanted), None) or available[0]
    order = [first]
    for model_id in scheduling_list(db, owner):
        row = next((m for m in available if m.model_id == model_id), None)
        if row is not None and all(row.id != seen.id for seen in order):
            order.append(row)
    return order


def ours(db: DBSession) -> Counter[str]:
    """Requests of ours in flight, per model row id."""
    from lembas.api.v1 import openai as v1
    from lembas.services import chat as chat_service
    from lembas.services import generation as generation_service

    counted: Counter[str] = Counter()
    for generation in list(generation_service._RUNNING.values()):
        if generation.done:
            continue
        message = db.get(Message, generation.message_id)
        chat = message.chat if message is not None else None
        if chat is None:
            continue
        row = chat_service.model_row(db, chat_service.speaker_for(db, chat, message))
        if row is not None:
            counted[row.id] += 1
    for row_id, count in list(v1._IN_FLIGHT_MODELS.items()):
        if count:
            counted[row_id] += count
    counted.update(_PENDING)
    return counted


async def _swap(connection) -> list[dict[str, str]] | None:
    """llama-swap's `/running` for a connection, or None. Short: a run waiting
    on a slow answer here is the thing this module exists to avoid."""
    from lembas.services.llm import health
    from lembas.services.llm.openai_client import Endpoint

    try:
        return await health.running(Endpoint.from_connection(connection), timeout=3.0)
    except Exception:  # unknown is "nothing loading", never an error
        log.debug("could not read /running for %s", connection.id, exc_info=True)
        return None


def busy_kind(why: str) -> str:
    """`busy_reason`'s words as progress's reasons (progress.md §2)."""
    if "loading" in why:
        return "loading"
    if "same server" in why:
        return "group"
    if "every slot" in why:
        return "slots"
    return "busy"


async def busy_reason(model: Model, counted: Counter[str], swaps: dict[str, Any]) -> str:
    """"" when `model` is free for a scheduled run now, else why not (English,
    for the run's record). `swaps` caches `/running` per connection."""
    connection = model.connection
    if connection is None or not connection.enabled:
        return "its connection is switched off"
    if str((connection.health_json or {}).get("status") or "") == "down":
        return "its server is not answering"
    mine = counted.get(model.id, 0)
    limit = model.parallel_limit
    if connection.one_model_at_a_time:
        for other in connection.models:
            if other.id != model.id and counted.get(other.id, 0):
                return f"{other.label} is answering someone on the same server"
        if mine >= (limit or 1):
            return "it is answering someone"
        if connection.id not in swaps:
            swaps[connection.id] = await _swap(connection)
        entries = swaps[connection.id]
        if entries:
            from lembas.services.llm import health

            if loading := health.starting(entries):
                return f"its server is loading {loading}"
        return ""
    if limit is None:
        return ""
    if mine >= limit:
        return "it is answering someone" if limit == 1 else "every slot is answering someone"
    return ""


async def choose(db: DBSession, owner: User, schedule: Schedule) -> Choice:
    """The first free model in order, reserved for this run until `release`.

    Under one lock, so two runs due at the same moment see each other's
    reservation. The caller must `release(choice)` once the reply has started
    (or did not)."""
    order = candidates(db, owner, schedule)
    choice = Choice()
    if not order:
        choice.busy.append(("", "there is no model this schedule may use"))
        return choice
    async with _CHOOSING:
        counted = ours(db)
        swaps: dict[str, Any] = {}
        for index, model in enumerate(order):
            why = await busy_reason(model, counted, swaps)
            if why:
                choice.busy.append((model.label, why))
                continue
            choice.model = model
            choice.reason = REASON_PINNED if index == 0 else REASON_SWITCHED
            choice.switched_from = order[0].label if index else ""
            _PENDING[model.id] += 1
            break
    return choice


def release(choice: Choice) -> None:
    """The reply has started (and now counts itself) or will not."""
    if choice.model is None:
        return
    with contextlib.suppress(Exception):
        _PENDING[choice.model.id] -= 1
        if _PENDING[choice.model.id] <= 0:
            del _PENDING[choice.model.id]


def forget() -> None:
    """For tests."""
    _PENDING.clear()


__all__ = [
    "Choice",
    "busy_reason",
    "candidates",
    "choose",
    "instance_list",
    "offered_models",
    "ours",
    "person_list",
    "release",
    "scheduling_list",
]
