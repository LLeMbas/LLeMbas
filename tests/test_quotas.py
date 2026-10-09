"""What an account may spend, and the arithmetic that decides.

The rule to hold is the one the permissions already hold, applied to numbers:
**a second group can only ever grant more.** Its awkward corner is zero, which
means "no limit" — so the maximum has to be taken with zero winning outright, or
a group saying "unlimited" would count for less than one saying "a million".
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lembas.db.models import ROLE_USER, Group, Usage, User
from lembas.security import permissions
from lembas.services import usage as usage_service


@pytest.fixture
def reader(db, registered) -> User:
    person = User(
        email="sam@shire.test", name="Sam", password_hash="x", role=ROLE_USER  # noqa: S106
    )
    db.add(person)
    db.commit()
    return person


def _group(db, reader, **limits) -> Group:
    group = Group(name=f"g{len(limits)}-{id(limits)}", limits_json=limits)
    group.users.append(reader)
    db.add(group)
    db.commit()
    return group


# --- Resolution -----------------------------------------------------------------
def test_nobody_is_limited_until_somebody_says_so(db, reader):
    """A quota that arrived with an upgrade and started refusing replies would
    be the worst possible way to introduce one."""
    assert permissions.limits_for(db, reader) == permissions.NO_LIMITS


def test_a_second_group_can_only_grant_more(db, reader):
    _group(db, reader, monthly_tokens=1000)
    _group(db, reader, monthly_tokens=5000)
    db.refresh(reader)

    assert permissions.limit(db, reader, "monthly_tokens") == 5000


def test_zero_means_no_limit_and_wins_outright(db, reader):
    """The union rule's awkward corner. A plain maximum would make "unlimited"
    count for less than "a million", which is the rule inverted for exactly the
    value somebody sets when they mean *stop limiting this person*."""
    _group(db, reader, monthly_tokens=1000)
    _group(db, reader, monthly_tokens=0)
    db.refresh(reader)

    assert permissions.limit(db, reader, "monthly_tokens") == 0


def test_a_group_with_no_opinion_contributes_nothing(db, reader):
    """Absent is not zero. If it were, adding a group that says nothing about
    tokens would silently make somebody unlimited."""
    _group(db, reader, monthly_tokens=1000)
    _group(db, reader, images_per_day=5)
    db.refresh(reader)

    assert permissions.limit(db, reader, "monthly_tokens") == 1000
    assert permissions.limit(db, reader, "images_per_day") == 5


def test_nonsense_in_the_column_is_ignored(db, reader):
    """A row can be written by hand or by an older version, and a limit nobody
    can parse must not become a limit of zero — which is *unlimited* here, the
    opposite of failing safe."""
    _group(db, reader, monthly_tokens="lots")
    _group(db, reader, monthly_tokens=1000)
    db.refresh(reader)

    assert permissions.limit(db, reader, "monthly_tokens") == 1000


def test_an_administrator_is_unlimited(db, registered):
    admin = db.scalars(select(User).order_by(User.created_at)).first()
    group = Group(name="tight", limits_json={"monthly_tokens": 1})
    group.users.append(admin)
    db.add(group)
    db.commit()

    assert permissions.limits_for(db, admin) == permissions.NO_LIMITS


# --- Recording ------------------------------------------------------------------
def test_usage_accumulates_into_one_row_per_month(db, reader):
    usage_service.record(db, reader.id, prompt_tokens=100, completion_tokens=50)
    usage_service.record(db, reader.id, prompt_tokens=10, completion_tokens=5)
    db.commit()

    rows = list(db.scalars(select(Usage).where(Usage.user_id == reader.id)))
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 110
    assert rows[0].completion_tokens == 55
    assert rows[0].replies == 2
    assert usage_service.month_tokens(db, reader.id) == 165


def test_recording_never_raises(db):
    """Bookkeeping that broke a reply would be worse than no bookkeeping."""
    usage_service.record(db, "", prompt_tokens=10)
    usage_service.record(db, "nobody-at-all", prompt_tokens=10)


def test_the_token_gate_answers_before_anything_is_spent(db, reader):
    _group(db, reader, monthly_tokens=100)
    db.refresh(reader)
    assert usage_service.over_token_budget(db, reader) == ""

    usage_service.record(db, reader.id, prompt_tokens=60, completion_tokens=60)
    db.commit()

    reason = usage_service.over_token_budget(db, reader)
    assert "month" in reason
    assert "100" in reason


def test_an_unlimited_account_is_never_over(db, reader):
    usage_service.record(db, reader.id, prompt_tokens=10**9)
    db.commit()

    assert usage_service.over_token_budget(db, reader) == ""


# --- Narrowing ------------------------------------------------------------------
@pytest.mark.parametrize(
    ("instance", "quota", "expected"),
    [
        (900, 300, 300),   # the group is tighter
        (300, 900, 300),   # the instance is tighter
        (0, 300, 300),     # the instance has no limit
        (300, 0, 300),     # the group has no opinion
        (0, 0, 0),         # neither
    ],
)
def test_two_ceilings_fold_to_the_tighter_one(instance, quota, expected):
    """Not `min`: a zero on either side would win and turn "no opinion" into
    "no time at all". Written once because getting it wrong in one place is a
    limit that silently stops working."""
    from lembas.services.generation import _narrower

    assert _narrower(instance, quota) == expected


# --- The screens ----------------------------------------------------------------
def test_a_group_stores_only_the_boxes_that_were_filled(db, client, registered, reader):
    group = Group(name="team")
    db.add(group)
    db.commit()

    client.post(
        f"/admin/groups/{group.id}",
        data={
            "name": "team",
            "limit_monthly_tokens": "5000",
            "limit_images_per_day": "",
            "limit_concurrent_replies": "not a number",
        },
        follow_redirects=False,
    )

    db.refresh(group)
    assert group.limits_json == {"monthly_tokens": 5000}


def test_the_user_page_says_where_each_permission_came_from(db, client, registered, reader):
    """The question the grids could not answer without opening every group by
    eye — which is exactly the simulation the union rule exists to avoid."""
    group = Group(name="writers", permissions_json={"tools.notes.write": True})
    group.users.append(reader)
    db.add(group)
    db.commit()

    explained = permissions.explain(db, reader)

    assert explained["tools.notes.write"]["on"] is True
    assert "writers" in explained["tools.notes.write"]["source"]
    # And something the baseline gives says baseline rather than naming a group.
    assert explained["chat.create"]["source"] == ["baseline"]

    page = client.get(f"/admin/users/{reader.id}").text
    assert "writers" in page
    assert "What this account can do" in page


def test_an_administrator_is_explained_as_bypassing(db, registered):
    admin = db.scalars(select(User).order_by(User.created_at)).first()
    explained = permissions.explain(db, admin)

    assert all(entry["source"] == ["admin"] for entry in explained.values())


def test_membership_is_edited_from_the_group_and_not_the_user(db, client, registered, reader):
    """Two controls writing one value is how each becomes the answer to "why did
    my change not stick?". The user page links to the group instead."""
    group = Group(name="team")
    db.add(group)
    db.commit()

    # No control for it on the user's page.
    assert 'name="group_ids"' not in client.get(f"/admin/users/{reader.id}").text

    # And a POST that tries anyway changes nothing.
    client.post(
        f"/admin/users/{reader.id}",
        data={"name": "Sam", "role": "user", "active": "true", "group_ids": [group.id]},
        follow_redirects=False,
    )
    db.refresh(reader)
    assert reader.groups == []

    client.post(
        f"/admin/groups/{group.id}",
        data={"name": "team", "user_ids": [reader.id]},
        follow_redirects=False,
    )
    db.refresh(reader)
    assert [g.name for g in reader.groups] == ["team"]

    # Once they are in it, their page links to where it is edited.
    assert f"/admin/groups/{group.id}" in client.get(f"/admin/users/{reader.id}").text


def test_the_user_list_is_paginated_and_searchable(db, client, registered):
    for n in range(30):
        db.add(
            User(
                email=f"p{n}@shire.test",
                name=f"Person {n}",
                password_hash="x",  # noqa: S106
            )
        )
    db.commit()

    first = client.get("/admin/users").text
    assert "Page 1 of" in first

    found = client.get("/admin/users?q=Person+7").text
    assert "Person 7" in found
    assert "Person 12" not in found
