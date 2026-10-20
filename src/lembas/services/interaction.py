"""Pausing a reply to ask the person reading it something.

Three features turn out to be one mechanism. A command that needs approving, a
question the model wants answered, and "this reply is waiting for you" are all:
stop the generation, put an interactive block in the bubble, wait for a POST,
carry on. So there is one primitive, and approval is a shape of question rather
than a separate machine.

Two things about where it sits matter.

**It pauses a round, not a call.** A round's tool calls run together under a
semaphore, and parking four coroutines on four separate answers inside that
gather would queue them behind each other invisibly -- and the reader would get
four cards, answerable in any order, for commands whose order matters. So one
card describes everything in the round that needs a decision, and the calls that
survive it run concurrently exactly as they did before.

**Stop has to keep working.** `generation.cancel` is read in one place, between
streamed chunks, and there are no chunks while paused. Rather than a second
poller, `generation.request_stop` resolves the pause directly; see the comment
there. Nothing in this module reaches back into `services.generation`, which is
what keeps it testable on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from lembas.web.i18n import t

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from lembas.services.generation import Generation

KIND_APPROVAL = "approval"
KIND_QUESTION = "question"
# A plan put to the person before anything is done: `plan_submit` in a chat's
# own Plan mode, or a linked device's model proposing one (`_lembas/plan`).
# Approved -- for a device, into Edit or Manual -- sent back for revision with
# what to change, or dismissed. The same card, a third shape; `plan_card`
# builds it for either source.
KIND_PLAN = "plan"

# How a pause ended.
ALLOW = "allow"
ALLOW_ALWAYS = "allow_always"
DENY = "deny"
ANSWER = "answer"
CANCELLED = "cancelled"  # Stop was pressed while the card was showing
EXPIRED = "expired"  # nobody answered in time
# Answered somewhere else first: in the terminal of a linked device whose session
# this chat shows. The card goes; what was decided is the terminal's.
ELSEWHERE = "elsewhere"

# Outcomes that mean "go ahead".
PERMITTED = (ALLOW, ALLOW_ALWAYS)

# A card offering more than this many buttons is a card nobody reads.
MAX_OPTIONS = 6

# And more than this many questions at once is a form, not a conversation. A
# model that wants twenty answers should ask for four and then ask again with
# what it learned.
MAX_QUESTIONS = 8

# The value the "Something else" row submits. A sentinel rather than a real
# option, because it is the one choice on the card the model did not write: it
# is added by this code, always, to every question. That is the whole reason the
# model is told never to offer an "Other" of its own -- two of them is one that
# does nothing, and the model's version would have no box behind it.
OTHER = "__other__"

# How many characters of an option's description are kept. It is a sentence
# explaining a choice, not a paragraph, and it is model output landing in a
# card somebody is meant to read at a glance.
MAX_OPTION_CHARS = 240

# How much of a refusal's reason is carried back to the model. Generous, because
# this is the reader saying what they want instead and truncating that mid-clause
# is worse than the tokens it saves -- but bounded, because it lands in a tool
# result inside a request that already has a window to fit in.
MAX_REASON_CHARS = 2000


@dataclass(frozen=True)
class Option:
    """One answer offered for a question.

    A `label` alone reads as a button; the optional `description` is what makes
    a real choice possible -- "Rewrite it" and "Patch it" say nothing about
    which loses your uncommitted work. Both are model output and are escaped
    where they are shown.
    """

    label: str
    description: str = ""
    # The one the model would choose itself (a device's question): shown
    # as such, and first -- the device already sorted it there.
    recommended: bool = False


@dataclass(frozen=True)
class Item:
    """One thing being asked about: a single question, or one command.

    `index` is the position of the *call* in its round, so an answer can be
    matched back to the call it belongs to -- the tool turns have to line up
    with the assistant turn's `tool_calls`, or an endpoint pairs the wrong
    result with the right id. Several items can share an index, because one
    `ask_user` call may carry several questions.

    `key` identifies this item within the card, and is what the form field is
    named after. Stable and opaque: a question's own text would make a terrible
    field name, and its position alone would collide across calls.
    """

    index: int
    key: str
    kind: str
    tool_name: str
    title: str
    detail: str = ""
    reason: str = ""
    # What the model says this call is for, in its own words -- distinct from
    # `reason`, which is why *we* stopped ("Edit mode asks before anything that
    # runs a command"). Model text, and shown as such: a card carrying an
    # explanation somebody reads as the application's own would be a card
    # vouching for it.
    purpose: str = ""
    options: tuple[Option, ...] = ()
    # Whether more than one option may be chosen. The model says which, because
    # only the model knows whether its options are alternatives ("rewrite or
    # patch") or a set ("which of these to include"). Exclusive is the default:
    # a radio group offered where checkboxes were meant costs one clarifying
    # round, while checkboxes offered for alternatives invite an answer that
    # contradicts itself.
    multiple: bool = False
    # Whether "Something else" is offered, with the box behind it. True for a
    # question -- the options are the model's guess at the answers and it can be
    # wrong -- and false for an approval, where the choice is Allow or Don't and
    # a third way out would mean nothing.
    allow_free_text: bool = True
    # Whether `detail` can be corrected before this is allowed. Only where the
    # detail *is* one argument and can be put back where it came from -- a tool
    # with no entry in `tool_labels.DETAIL_KEYS` gets a `k=repr(v)` summary that
    # cannot be parsed back, and offering a box that silently changed nothing
    # would be worse than offering none.
    editable: bool = False
    # A plan's text rendered: model output, so it is made only by
    # `services/markdown.render_markdown`, which sanitises -- the one thing the
    # card may show unescaped.
    detail_html: str = ""
    # A device question's short header ("Database"), shown above the question.
    header: str = ""
    # A plan as a structure (services/plans.py), when the card has one: drawn
    # as the plan card rather than as `detail_html`. A device without
    # `plan_state` sends only the text, which still works.
    plan: dict[str, Any] | None = None
    # Whether approving means choosing a mode to carry on in (a device's plan:
    # Edit or Manual). A chat's own plan has no modes to choose between --
    # approval ends Plan mode and that is all -- so its card offers none.
    mode_choice: bool = True
    # Which background subagent is asking, when one is (`_meta.lembas.job` on a
    # device's `request_permission`, spec 2.0.0): "explore (job7)". Empty for
    # the session's own call. Said on the card, because a background subagent
    # may ask while the conversation itself is idle.
    job: str = ""


@dataclass
class Interruption:
    """A reply, stopped, waiting for one answer to cover every item."""

    id: str
    items: tuple[Item, ...]
    expires_at: float = 0.0
    # The device's id for the call this card asks about: what
    # `_lembas/permission/settled` names, so the card it takes down is this one
    # and never another open beside it.
    tool_call_id: str = ""
    # Whether the card offers "Dismiss" -- a device's question or plan may be
    # put aside without an answer, and the model is told so.
    dismissable: bool = False
    # Why the model is asking, in its words (a device question's `why`).
    why: str = ""
    _future: asyncio.Future | None = field(default=None, repr=False, compare=False)

    @property
    def kind(self) -> str:
        if any(i.kind == KIND_PLAN for i in self.items):
            return KIND_PLAN
        return KIND_QUESTION if any(i.kind == KIND_QUESTION for i in self.items) else KIND_APPROVAL

    def resolve(
        self,
        outcome: str,
        *,
        answers: dict[str, str] | None = None,
        reason: str = "",
        picks: dict[str, list[str]] | None = None,
    ) -> bool:
        """Complete this pause. Idempotent -- a second answer is ignored.

        Returns whether this call was the one that answered it, which is what
        the endpoint reports back: a card answered twice (two tabs, a double
        click) should say so rather than pretend.
        """
        if self._future is None or self._future.done():
            return False
        self._future.set_result(
            Reply(
                outcome=outcome,
                answers=dict(answers or {}),
                reason=reason.strip()[:MAX_REASON_CHARS],
                picks={k: list(v) for k, v in (picks or {}).items()},
            )
        )
        return True


@dataclass(frozen=True)
class Reply:
    """How a card was answered.

    `answers` is keyed by `Item.key`, so a card carrying four questions comes
    back as four answers in one go. An approval carries none: the verdict is
    the whole of it -- except for `reason`.

    `reason` is why the reader refused, in their own words, and it belongs to
    the *card* rather than to an item. The card already covers everything in the
    round for the reason `interaction` opens with, one verdict answers the lot,
    and somebody who says "not in that directory" is saying it about the round.
    Keeping it off `answers` also keeps it clear of `text.<key>`, which on an
    approval card already means something else entirely -- a corrected command.
    """

    outcome: str
    answers: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    # The options ticked per item, as they were -- `answers` joins them
    # with commas, which a device question needs back as a list, and a label
    # may itself contain a comma.
    picks: dict[str, list[str]] = field(default_factory=dict)

    @property
    def permitted(self) -> bool:
        return self.outcome in PERMITTED

    @property
    def ended(self) -> bool:
        """Whether this outcome means the whole reply should stop."""
        return self.outcome == CANCELLED

    def answer_to(self, item: Item) -> str:
        return (self.answers.get(item.key) or "").strip()


def build(
    interaction_id: str, items: list[Item] | tuple[Item, ...], *, timeout: float
) -> Interruption:
    """An interruption with its future attached, ready to be waited on."""
    return Interruption(
        id=interaction_id,
        items=tuple(items),
        expires_at=time.monotonic() + timeout,
        _future=asyncio.get_running_loop().create_future(),
    )


async def wait_for(
    generation: Generation, interruption: Interruption, *, timeout: float
) -> Reply:
    """Park the generation on this interruption until somebody answers.

    Adds it to `generation.waiting` and touches, so the follower sends the card
    on its next frame; takes it out in `finally`, so answering makes it
    disappear -- and only it: a device may have several open at once,
    each its own card with its own answer, and answering one clears only that
    one.

    The time spent here is added to `generation.waited`: the loop's wall-clock
    budget is seconds of *work*, and time waiting for a person is not work
    (harness spec `loop.json`).
    """
    generation.waiting.append(interruption)
    generation.touch()
    started = time.monotonic()
    try:
        return await asyncio.wait_for(asyncio.shield(interruption._future), timeout)
    except TimeoutError:
        return Reply(outcome=EXPIRED)
    finally:
        with contextlib.suppress(ValueError):
            generation.waiting.remove(interruption)
        if hasattr(generation, "waited"):
            generation.waited += time.monotonic() - started
        generation.touch()


def plan_card(
    plan: dict[str, Any] | None = None,
    *,
    text: str = "",
    path: str = "",
    tool_name: str = "plan_submit",
    timeout: float,
    mode_choice: bool = False,
    dismissable: bool = True,
    tool_call_id: str = "",
) -> Interruption:
    """The plan card, ready to be waited on: a structured plan (drawn as the
    card the transcript uses), or a plan as text (rendered Markdown, sanitised
    -- what a device without `plan_state` sends), or both.

    One builder for both sources, so `_lembas/plan` and a chat's own
    `plan_submit` cannot drift into two cards: the device link passes the
    structure from `_meta.lembas.plan` with `mode_choice=True`; the generation
    loop passes the chat's plan.
    """
    from lembas.services import plans
    from lembas.services.markdown import render_markdown

    normalised = plans.normalise(plan) if plan else {}
    item = Item(
        index=0,
        key="plan",
        kind=KIND_PLAN,
        tool_name=tool_name,
        title="The plan",
        detail=str(path or "")[:1000],
        detail_html=render_markdown(text) if text.strip() and not normalised else "",
        plan=normalised or None,
        allow_free_text=False,
        mode_choice=mode_choice,
    )
    card = build(uuid.uuid4().hex, [item], timeout=timeout)
    card.tool_call_id = tool_call_id
    card.dismissable = dismissable
    return card


def summarise(items: tuple[Item, ...]) -> str:
    """What to show in the status line while the card is up."""
    if not items:
        return ""
    if items[0].kind == KIND_PLAN:
        return t("Waiting for you to look at the plan…")
    if items[0].kind == KIND_QUESTION:
        return t("Waiting for your answer…") if len(items) == 1 else t("Waiting for your answers…")
    if len(items) == 1:
        return t("Waiting for you to allow %(tool)s…", tool=items[0].tool_name)
    return t("Waiting for you to allow %(n)s actions…", n=len(items))


__all__ = [
    "ALLOW",
    "ALLOW_ALWAYS",
    "ANSWER",
    "CANCELLED",
    "DENY",
    "ELSEWHERE",
    "EXPIRED",
    "KIND_APPROVAL",
    "KIND_PLAN",
    "KIND_QUESTION",
    "MAX_OPTIONS",
    "MAX_QUESTIONS",
    "MAX_REASON_CHARS",
    "PERMITTED",
    "Interruption",
    "Item",
    "Reply",
    "build",
    "plan_card",
    "summarise",
    "wait_for",
]
