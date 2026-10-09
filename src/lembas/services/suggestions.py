"""The cards offered on the new-chat screen.

A blank composer is the least helpful thing a chat client can show someone who
has just installed one. These are three starting points an administrator can
replace with their own.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Suggestion
from lembas.services import settings_store
from lembas.web.i18n import N_

log = logging.getLogger(__name__)

# A short list stays a short list. Past a dozen it is a menu, and a menu on the
# empty screen is a worse blank page than a blank page.
MAX_SUGGESTIONS = 12
MAX_SHOWN = 6

MAX_NAME = 120
MAX_DESCRIPTION = 300
MAX_PROMPT = 4000

# A card is sent the moment it is clicked, so each of these has to work cold --
# with nothing pasted and nothing typed. These four are *self-contained* rather
# than question-asking: they are what somebody clicks on an instance they have
# just stood up, to find out whether the thing works and what the model behind
# it is. That is the honest first use, and it is a different job from the task
# cards that preceded them, which opened by asking for material the reader had
# not given.
#
# The name and description are read by somebody who has never seen this
# instance; the prompt is read by the model. They are allowed to differ, and
# here they do -- "Start a session" says what the card is for, and its prompt is
# the bare line that produces a clean opening turn.
#
# Only a fresh install gets these: `SEEDED_KEY` means an instance that has
# already seeded keeps whatever its administrator has since made of the list.
#
# No Middle-earth flavour: this is functional UI.
# The name and the description are marked for the catalogue and
# translated where the card is drawn -- so a card nobody has edited reads in the
# reader's language, and one an administrator rewrote reads as they wrote it,
# because their words are not a key. The prompt is what the model receives, and
# stays as written.
DEFAULTS: tuple[tuple[str, str, str], ...] = (
    (
        N_("Start a session"),
        N_("Open a fresh conversation with nothing assumed."),
        "Initializing a new session.",
    ),
    (
        N_("What can you do?"),
        N_("Have the model say which tools, instructions and limits it has."),
        "Who are you and what are you capable of? What are your available tools? "
        "What do you already know from your instructions? Do you have any set "
        "personality?",
    ),
    (
        N_("Show me some code"),
        N_("See how the model writes, in a language of its own choosing."),
        "Pick a random programming language and produce a coding example showing "
        "your coding capabilities.",
    ),
    (
        N_("Tell me something"),
        N_("A fact worth knowing, from a subject picked at random."),
        "Give me a random fun fact from a random topic of your choosing. "
        "Available topics are science, history, sociology, theology etc.",
    ),
)

# Guards the seed. Not "is the table empty", because an administrator who
# deletes all three would get them back on every restart.
SEEDED_KEY = "suggestions_seeded"


def visible(db: DBSession) -> list[Suggestion]:
    """What the new-chat screen shows, in order."""
    return list(
        db.scalars(
            select(Suggestion)
            .where(Suggestion.enabled.is_(True))
            .order_by(Suggestion.position, Suggestion.name)
            .limit(MAX_SHOWN)
        )
    )


def all_of_them(db: DBSession) -> list[Suggestion]:
    """Every suggestion, enabled or not, for the admin page."""
    return list(
        db.scalars(select(Suggestion).order_by(Suggestion.position, Suggestion.name))
    )


def next_position(db: DBSession) -> int:
    """New rows land at the end rather than at 0, where they would shuffle.

    No `or -1` after the coalesce: position 0 is falsy, so that idiom sends the
    second row back to 0 on top of the first.
    """
    highest = db.scalar(select(func.coalesce(func.max(Suggestion.position), -1)))
    return int(highest if highest is not None else -1) + 1


def create(db: DBSession, *, name: str, description: str = "", prompt: str = "") -> Suggestion:
    suggestion = Suggestion(
        name=name.strip()[:MAX_NAME],
        description=description.strip()[:MAX_DESCRIPTION],
        prompt=prompt[:MAX_PROMPT],
        position=next_position(db),
    )
    db.add(suggestion)
    db.commit()
    return suggestion


def seed_defaults(db: DBSession) -> int:
    """Write the built-in suggestions, once ever.

    Runs from the startup housekeeping block. The flag is what makes it once:
    checking whether the table is empty would restore all three every restart
    for anyone who decided they did not want them.
    """
    if settings_store.get(db, SEEDED_KEY):
        return 0

    for name, description, prompt in DEFAULTS:
        create(db, name=name, description=description, prompt=prompt)

    settings_store.update(db, {SEEDED_KEY: True})
    log.info("seeded %d default suggestion(s)", len(DEFAULTS))
    return len(DEFAULTS)
