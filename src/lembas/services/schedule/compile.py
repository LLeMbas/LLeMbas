"""Turning "remind me every Monday to check the build" into a schedule.

One request, once, when a schedule is created. It does two things a person
should not have to do by hand: work out the recurrence, and rewrite the
description into something that reads sensibly with **no conversation around
it** — because that is how it will be read, days later, by a model that was not
present when it was typed.

Three rules hold this up:

- **The rule goes through `rule.validate` and nothing else.** That function is
  total and clamping, and this is the reason it had to be: what arrives here is
  model output that becomes a *timer*. There is one normaliser, shared with the
  manual form, so there cannot be two ideas of what a legal schedule is.
- **A compile that fails is not an error.** It hands back what it could work out
  and the caller shows the manual form with the reader's own words in it. A
  model that answers in prose must never quietly produce a schedule that never
  fires.
- **Clearing `task.schedule_compile` switches off the compiling, not the
  feature.** That is what makes "an empty override means off" safe here, and it
  is only safe because the manual form exists. `task.compact` set the precedent
  that clearing a fragment kills a feature, so this one says otherwise in its
  own hint.

Deliberately no `response_format`. Several local endpoints reject unknown
parameters outright, and this is exactly the `apply_effort` lesson: a request
that 400s here would be the compile silently switching itself off.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from lembas.db.models import TARGET_CHAT, TARGETS, Chat, User
from lembas.services.llm.openai_client import Endpoint, LLMError, complete
from lembas.services.reasoning import strip_reasoning
from lembas.services.schedule import clock
from lembas.services.schedule import rule as rule_service

log = logging.getLogger(__name__)

# Enough for a small model that thinks before answering. The title lesson
# applies: too small is not a shorter answer, it is no answer, because the
# thinking consumes the budget and content comes back empty.
MAX_TOKENS = 900
MAX_REQUEST_CHARS = 2000

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class Compiled:
    """What the compile worked out. `ok` is False when the reader must finish
    the job by hand -- the fields are still filled in as far as they went."""

    ok: bool = False
    title: str = ""
    instruction: str = ""
    target: str = TARGET_CHAT
    rule: dict = field(default_factory=dict)
    reason: str = ""


def _payload(raw: str) -> dict:
    """The first JSON object in a reply, however it was wrapped.

    Lenient for the reason `tools.parse_arguments` is: a small model sends
    something close to the shape rather than the shape, and refusing it costs a
    whole round trip to end up showing the manual form anyway.
    """
    text = (raw or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return {}
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def render_prompt(template: str, *, request: str, user: User | None) -> str:
    """Fill the fragment in. Separate so a test can read what was asked."""
    from lembas.services import prompts as prompts_service

    zone = clock.zone_for(user)
    now = datetime.now(tz=UTC).astimezone(zone)
    return prompts_service.substitute(
        template,
        {
            "request": request[:MAX_REQUEST_CHARS],
            "now": now.strftime("%A %-d %B %Y, %H:%M"),
            "timezone": clock.name_for(user) or str(clock.server_zone()),
            "targets": ", ".join(TARGETS),
        },
    )


async def compile_request(
    endpoint: Endpoint,
    model_id: str,
    request: str,
    *,
    template: str,
    user: User | None = None,
) -> Compiled:
    """Work a plain-language request into a schedule.

    Never raises. Every failure -- a cleared fragment, an endpoint that is down,
    prose instead of JSON, a rule that normalises to nothing -- comes back as
    `ok=False` with whatever was salvageable, and the route shows the manual form.
    """
    plain = (request or "").strip()
    if not plain:
        return Compiled(reason="Say what you want to happen.")
    if not template.strip():
        # An administrator cleared the fragment. That switches off the
        # *compiling*: the reader fills the form in themselves, with their own
        # words already in it.
        return Compiled(instruction=plain, title=plain[:80], reason="")

    prompt = render_prompt(template, request=plain, user=user)
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.2,
    }

    # Asked twice before giving up, and only when the *reply* was unusable.
    # Measured against a 4B model on this machine: the prompt itself is sound --
    # ten realistic requests compiled ten times over, twice -- but roughly one
    # call in six came back empty or truncated, which a local runner swapping
    # models under the request will do. One retry costs a second on a screen
    # somebody is already waiting at, and turns "fill this in yourself" from
    # something seen regularly into something seen rarely.
    #
    # Deliberately not retried on an LLMError: an endpoint that refused the
    # connection will refuse it again, and the reader is better served by the
    # form than by waiting twice for the same answer.
    payload: dict = {}
    for attempt in range(2):
        try:
            raw = await complete(endpoint, body)
        except LLMError as exc:
            log.info("schedule compile failed: %s", exc)
            return Compiled(
                instruction=plain,
                title=plain[:80],
                reason="The model could not be reached, so fill this in yourself.",
            )
        # A model that thinks inline puts its reasoning in `content`, which is
        # the field `complete` hands back verbatim -- the trap auto-titling hit.
        answered, _ = strip_reasoning(raw)
        payload = _payload(answered)
        if payload:
            break
        log.info("schedule compile produced no JSON (attempt %s)", attempt + 1)

    if not payload:
        return Compiled(
            instruction=plain,
            title=plain[:80],
            reason="The model did not answer with a schedule, so fill this in yourself.",
        )

    raw_rule = payload.get("schedule") or payload.get("rule") or {}
    if isinstance(raw_rule, dict):
        # A model asked for "every six hours" writes `{"every": {"hours": 6}}`
        # and nothing else, which is the natural reading and cannot fire: a
        # timer measures from a start, and `rule.py` has no clock to invent one.
        # Filled in here, exactly as the manual form's `_rule_from_form` does,
        # so the two paths agree about what a startless timer means. A model
        # that puts `start` at the top level instead is read the same way rather
        # than being told its schedule means nothing.
        raw_rule = dict(raw_rule)
        if raw_rule.get("every") and not raw_rule.get("start"):
            raw_rule["start"] = payload.get("start") or datetime.now(tz=UTC).isoformat()
    clean = rule_service.validate(raw_rule)
    title = str(payload.get("title") or "").strip() or plain[:80]
    instruction = str(payload.get("instruction") or "").strip() or plain
    target = str(payload.get("target") or TARGET_CHAT)
    if target not in TARGETS:
        target = TARGET_CHAT

    if not clean:
        return Compiled(
            title=title,
            instruction=instruction,
            target=target,
            reason="The model could not work out when this should run — say when below.",
        )
    if rule_service.next_after(clean, datetime.now(tz=UTC), zone=clock.zone_for(user)) is None:
        # Normalised, but with nothing left to fire. Refused for the same reason
        # `schedules.create` refuses it: a schedule that can never run looks
        # exactly like a working one on every screen it appears on.
        return Compiled(
            title=title,
            instruction=instruction,
            target=target,
            rule=clean,
            reason="That time has already passed — say when it should run.",
        )

    return Compiled(ok=True, title=title, instruction=instruction, target=target, rule=clean)


def endpoint_for(db, user: User, model_id: str = "") -> tuple[Endpoint, str] | None:
    """A connection and model to compile with, or None if there is none.

    Built on a throwaway `Chat` that is never added to a session, exactly as
    `agent/draft.py` does: `resolve_endpoint` reads `model_id` and
    `connection_id` and nothing else, so it works unchanged and did not have to
    learn what a compile is.

    **From the schedule's own data group.** The request being compiled is the
    person's words about their own work, and it goes to whichever model does the
    compiling -- so that model is chosen among the ones that will run the
    schedule, never merely the first one pinned. `model_id` is the model the
    form has chosen; without one, the person's default model decides the group.
    """
    from lembas.services import chat as chat_service
    from lembas.services import data_groups

    if model_id:
        group = data_groups.for_pair(db, user, model_id)
    else:
        chosen = chat_service.default_model(db, user)
        group = data_groups.for_pair(db, user, *chosen) if chosen else data_groups.DEFAULT_GROUP
    models = chat_service.available_models(db, user, group)
    if not models:
        return None
    chosen = next((m for m in models if m.pinned), models[0])
    stand_in = Chat(user_id=user.id, model_id=chosen.model_id, connection_id=chosen.connection_id)
    try:
        return chat_service.resolve_endpoint(db, stand_in)
    except LLMError:
        return None
