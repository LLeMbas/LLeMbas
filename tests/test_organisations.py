"""Organisations and invites: every account in one organisation, no open sign-up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from lembas.db.models import (
    DEFAULT_ORG,
    ORG_ADMIN,
    ORG_MEMBER,
    Group,
    Invite,
    Organisation,
    User,
)
from lembas.security.passwords import hash_password
from lembas.services import organisations


def test_the_first_organisation_exists_before_any_account(db):
    org = db.get(Organisation, DEFAULT_ORG)
    assert org is not None and org.slug == DEFAULT_ORG


def test_an_account_made_without_an_organisation_lands_in_the_first(db):
    user = User(name="Old", email="old@shire.test", password_hash=hash_password("x" * 10))
    db.add(user)
    db.commit()
    assert (user.org_id, user.org_role) == (DEFAULT_ORG, ORG_MEMBER)


def test_an_invite_makes_one_account_in_its_organisation(db):
    org = organisations.create(db, "Rivendell")
    made = organisations.create_invite(db, org.id, created_by=None, org_role=ORG_ADMIN)

    # The token is shown once and only its hash is kept.
    stored = db.get(Invite, made.invite.id)
    assert made.token not in stored.token_hash
    assert made.token.startswith(organisations.INVITE_PREFIX)

    user = organisations.accept_invite(
        db, made.token, name="Elrond", email="Elrond@Rivendell.test", password="half-elven-lord"
    )
    assert (user.org_id, user.org_role, user.email) == (org.id, ORG_ADMIN, "elrond@rivendell.test")
    db.refresh(stored)
    assert stored.used_at is not None and stored.used_by == user.id


def test_an_invite_is_single_use(db):
    made = organisations.create_invite(db, DEFAULT_ORG, created_by=None)
    organisations.accept_invite(db, made.token, name="A", email="a@x.test", password="first-of-two")
    with pytest.raises(organisations.InviteError, match="already been used"):
        organisations.accept_invite(
            db, made.token, name="B", email="b@x.test", password="second-of-two"
        )
    assert db.scalar(select(User).where(User.email == "b@x.test")) is None


def test_the_losing_side_of_a_race_gets_nothing(db):
    """Both acceptances read the invite as unused; the conditional UPDATE lets
    exactly one through, and the other's account is rolled back."""
    made = organisations.create_invite(db, DEFAULT_ORG, created_by=None)
    real_find = organisations.find_invite
    invite_id = made.invite.id

    # The second caller found the invite before the first one spent it.
    organisations.accept_invite(
        db, made.token, name="A", email="a@x.test", password="first-in-line"
    )
    stale = db.get(Invite, invite_id)
    try:
        organisations.find_invite = lambda *_a, **_k: stale  # type: ignore[assignment]
        with pytest.raises(organisations.InviteError):
            organisations.accept_invite(
                db, made.token, name="B", email="b@x.test", password="second-in-line"
            )
    finally:
        organisations.find_invite = real_find  # type: ignore[assignment]
    assert db.scalar(select(User).where(User.email == "b@x.test")) is None


def test_an_expired_invite_is_refused(db):
    made = organisations.create_invite(db, DEFAULT_ORG, created_by=None, ttl=timedelta(hours=1))
    later = datetime.now(UTC) + timedelta(hours=2)
    with pytest.raises(organisations.InviteError, match="expired"):
        organisations.accept_invite(
            db, made.token, name="Late", email="late@x.test", password="too-late-now", now=later
        )


def test_an_invite_for_one_address_is_refused_to_another(db):
    made = organisations.create_invite(db, DEFAULT_ORG, created_by=None, email="sam@shire.test")
    with pytest.raises(organisations.InviteError, match="another email"):
        organisations.accept_invite(
            db, made.token, name="Ted", email="ted@shire.test", password="sandyman-mill"
        )
    user = organisations.accept_invite(
        db, made.token, name="Sam", email="SAM@shire.test", password="potatoes-po-ta-toes"
    )
    assert user.email == "sam@shire.test"


def test_an_unknown_token_is_refused(db):
    with pytest.raises(organisations.InviteError, match="not valid"):
        organisations.find_invite(db, "lmi_nothing")


def test_a_bad_password_leaves_the_invite_unspent(db):
    made = organisations.create_invite(db, DEFAULT_ORG, created_by=None)
    with pytest.raises(organisations.AccountError):
        organisations.accept_invite(db, made.token, name="A", email="a@x.test", password="short")
    assert db.get(Invite, made.invite.id).used_at is None


def test_a_revoked_invite_is_gone(db):
    made = organisations.create_invite(db, DEFAULT_ORG, created_by=None)
    assert [i.id for i in organisations.pending_invites(db, DEFAULT_ORG)] == [made.invite.id]
    assert organisations.revoke_invite(db, made.invite.id) is True
    with pytest.raises(organisations.InviteError):
        organisations.find_invite(db, made.token)


def test_slugs_are_unique(db):
    first = organisations.create(db, "Bree")
    second = organisations.create(db, "Bree")
    assert first.slug == "bree" and second.slug == "bree-2"


def test_group_names_are_unique_per_organisation_only(db):
    other = organisations.create(db, "Gondor")
    db.add_all([Group(name="Rangers"), Group(name="Rangers", org_id=other.id)])
    db.commit()
    assert len(list(db.scalars(select(Group).where(Group.name == "Rangers")))) == 2
