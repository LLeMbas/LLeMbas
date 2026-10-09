"""Memory: the two ways it used to lose somebody's facts quietly.

`memory_forget` was a case-insensitive substring FIRST-match delete with nothing
warning about it, so a short fragment removed whichever memory happened to be
older -- and a wrong deletion here is not something anybody finds out about.
`memory_add` had no defence against the same fact being stored four times in
slightly different words, which costs the window forever *and* makes every
forget after it ambiguous.

Both are asserted on the rows, not on the wording.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from lembas.db.models import Memory, User
from lembas.security.passwords import hash_password
from lembas.services import tools as tools_service
from lembas.services.library import memories as memories_service


@pytest.fixture
def owner(db):
    user = User(name="Frodo", email="f@shire.test", password_hash=hash_password("x"))
    db.add(user)
    db.commit()
    return user


def _count(db, owner) -> int:
    return db.scalar(
        select(func.count()).select_from(Memory).where(Memory.owner_id == owner.id)
    )


def _forget(owner, text: str):
    import asyncio

    context = tools_service.ToolContext(owner_id=owner.id)
    return asyncio.run(tools_service.run_tool(context, "memory_forget", f'{{"content": "{text}"}}'))


# --- Forgetting ----------------------------------------------------------------
def test_an_ambiguous_forget_removes_nothing(db, owner):
    """Two memories about coffee; "coffee" names neither of them."""
    memories_service.add(db, owner=owner, content="Drinks coffee black.")
    memories_service.add(db, owner=owner, content="Allergic to coffee.")

    outcome = _forget(owner, "coffee")

    assert _count(db, owner) == 2
    assert outcome.event["status"] == "error"
    assert "Drinks coffee black." in outcome.content
    assert "Allergic to coffee." in outcome.content


def test_quoting_a_memory_in_full_removes_that_one(db, owner):
    """Exact-first is what makes this work. "Drinks coffee." is a substring of
    "Drinks coffee. Never tea." too, so a substring-only match would call the
    unambiguous case ambiguous and refuse to do anything at all."""
    short = memories_service.add(db, owner=owner, content="Drinks coffee.")
    long = memories_service.add(db, owner=owner, content="Drinks coffee. Never tea.")
    short_id, long_id = short.id, long.id

    _forget(owner, "Drinks coffee.")

    db.expire_all()
    assert db.get(Memory, short_id) is None
    assert db.get(Memory, long_id) is not None


def test_forgetting_something_that_is_not_there_says_so(db, owner):
    memories_service.add(db, owner=owner, content="Drinks coffee black.")
    outcome = _forget(owner, "tea")
    assert _count(db, owner) == 1
    assert outcome.event["status"] == "error"


def test_an_unambiguous_fragment_still_works(db, owner):
    """Quoting in full is what the description asks for, but a fragment that
    genuinely names one memory should not be made to fail."""
    memories_service.add(db, owner=owner, content="Drinks coffee black.")
    memories_service.add(db, owner=owner, content="Lives in Bree.")

    _forget(owner, "Bree")

    db.expire_all()
    assert _count(db, owner) == 1


# --- Adding --------------------------------------------------------------------
def test_an_exact_duplicate_creates_nothing(db, owner):
    first = memories_service.add(db, owner=owner, content="Prefers metric units.")
    again = memories_service.add(db, owner=owner, content="  Prefers   metric units.  ")

    assert _count(db, owner) == 1
    assert again.id == first.id


def test_the_limit_refuses_and_does_not_tell_the_model_to_guess(db, owner, monkeypatch):
    """Past MAX_TOTAL_CHARS the injected block is truncated, so the model is not
    shown every memory. Telling it to remove one to make room asks it to choose
    blind -- and the forget path above is exactly where blind guessing bites."""
    monkeypatch.setattr(memories_service, "MAX_RECORDS", 3)
    for index in range(3):
        memories_service.add(db, owner=owner, content=f"Fact {index}")

    with pytest.raises(ValueError) as caught:
        memories_service.add(db, owner=owner, content="One too many")

    assert _count(db, owner) == 3
    message = str(caught.value)
    assert "note instead" in message
    assert "Remove one first" not in message
