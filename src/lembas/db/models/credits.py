"""Credits: plans, a ledger, and a cached balance.

**Credits are not a payment system.** Nothing is bought or billed here: an
administrator decides how many credits a person gets, and the instance counts.
A credit has a money value (`credit_value`, in the instance's
`currency`) and model prices are set in money -- so a price list copied from a
provider is typed in as it is, and the instance works out the credits. Off by
default, so an upgrade changes nothing.

Amounts are **integer micro-credits** (a millionth of a credit) everywhere they
are stored, the way Money keeps cents: a price per million tokens times a token
count divides exactly, and no float is ever summed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lembas.db.base import Base, Timestamps, UUIDPrimaryKey
from lembas.db.types import JSONList

MICRO = 1_000_000

# Why a ledger row exists.
ENTRY_GRANT = "grant"  # the starting grant of a new account
ENTRY_TOPUP = "topup"  # credits added by an administrator or through the API
ENTRY_ADJUST = "adjust"  # a correction either way, with a note
ENTRY_USAGE = "usage"  # a reply, a /v1 call or an image, charged
ENTRY_RENEWAL = "renewal"  # a plan's monthly credits
ENTRY_EXPIRE = "expire"  # unused credits a resetting plan takes back
ENTRY_KINDS = (ENTRY_GRANT, ENTRY_TOPUP, ENTRY_ADJUST, ENTRY_USAGE, ENTRY_RENEWAL, ENTRY_EXPIRE)

# What a plan does with unused credits when it renews.
RENEW_RESET = "reset"  # back to the plan's amount: unused credits expire
RENEW_ADD = "add"  # the amount is added, up to the plan's cap
RENEWALS = (RENEW_RESET, RENEW_ADD)


class Plan(UUIDPrimaryKey, Timestamps, Base):
    """What a person gets each month, and how fast they may spend it."""

    __tablename__ = "plans"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    # Micro-credits a month.
    monthly: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    renewal: Mapped[str] = mapped_column(String(8), default=RENEW_RESET, nullable=False)
    # For RENEW_ADD: the balance renewal never takes it above. 0 = no cap.
    cap: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Rolling windows over usage entries, in micro-credits. 0 = no limit.
    limit_5h: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    limit_day: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    limit_week: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    limit_month: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    # Model ids a person on this plan may spend credits on. Empty = any.
    allowed_models: Mapped[list[str]] = mapped_column(JSONList, default=list)


class CreditEntry(UUIDPrimaryKey, Base):
    """One movement of credits. The balance is their sum; nothing is ever edited."""

    __tablename__ = "credit_entries"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Micro-credits; negative for usage and expiry.
    delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_id: Mapped[str | None] = mapped_column(String(32))
    model_id: Mapped[str] = mapped_column(String(300), default="")
    message_id: Mapped[str | None] = mapped_column(String(32))
    token_id: Mapped[str | None] = mapped_column(String(32))
    actor_id: Mapped[str | None] = mapped_column(String(32))
    # What was counted, for a usage entry: tokens in, cached, out, images.
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # What a usage entry cost, in micro-credits, whether or not it was taken
    # For most people it is `-delta`; for an administrator, whose use
    # is never deducted, `delta` is 0 and this is the only place the spend is
    # recorded -- so what an administrator used stays visible beside everybody
    # else's. 0 on every other kind of entry.
    cost_micro: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    # A client's key for a top-up or an adjustment, so a retried request is one
    # entry. Unique where set.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)


Index("ix_credit_entries_user_at", CreditEntry.user_id, CreditEntry.at)


class CreditAccount(Base):
    """The balance, cached: the sum of a person's entries, kept in step by the
    one module that writes entries (services/credits.py)."""

    __tablename__ = "credit_accounts"

    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)


__all__ = [
    "ENTRY_ADJUST",
    "ENTRY_EXPIRE",
    "ENTRY_GRANT",
    "ENTRY_KINDS",
    "ENTRY_RENEWAL",
    "ENTRY_TOPUP",
    "ENTRY_USAGE",
    "MICRO",
    "RENEWALS",
    "RENEW_ADD",
    "RENEW_RESET",
    "CreditAccount",
    "CreditEntry",
    "Plan",
]
