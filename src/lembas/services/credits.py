"""Credits: what a reply costs, who may spend, and the monthly renewal.

The one module that writes `CreditEntry` and `CreditAccount`, so the cached
balance and the ledger cannot drift: every movement is an entry and the same
transaction moves the balance.

**Off by default** (`settings_store.credits(db)["enabled"]`): nothing is checked
or charged until an administrator turns it on, and a model priced at zero is
always free, so local models can stay free beside priced ones.

Prices are per model (`Model.pricing_json`), per million tokens for `input`,
`cached_input`, `output` and `reasoning` (the output price when unset), and
`per_request` per reply. They are **money**, in the instance's
currency, and a credit is worth `credit_value` of it: a price is turned into
credits when a reply is costed, so changing the credit's value changes what
every model costs in credits -- deliberately, it is the one lever. A dict
that carries no `"unit": "money"` mark holds credits; it is read as
such until `migrations.prices_to_money` converts it. Speech and pictures are
priced the same way, in the audio and image settings (`service_cost`).
Costs round **up** to the micro-credit, so nothing is ever free by rounding.

**Administrators are unlimited**: never refused, never deducted. What
they use is still written down -- a usage entry with a delta of 0 and the
would-be cost in `cost_micro` -- so their spend shows beside everybody else's.

The check runs before a reply is built, beside the monthly token budget; the
charge is made when the reply is persisted (and at the end of a `/v1` call). A
reply in flight can therefore overdraw slightly -- it was allowed to start, and
stopping it half way would be the worse failure. Helpers are charged to the
person whose reply sent them; titles and schedule compiling are not charged.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import (
    ENTRY_EXPIRE,
    ENTRY_GRANT,
    ENTRY_RENEWAL,
    ENTRY_USAGE,
    MICRO,
    RENEW_ADD,
    CreditAccount,
    CreditEntry,
    Model,
    Plan,
    User,
)

log = logging.getLogger(__name__)

PRICE_KEYS = ("input", "cached_input", "output", "reasoning", "per_request")
# What writing a prompt to the cache costs, against ordinary input (Anthropic's
# five-minute cache).
CACHE_WRITE_FACTOR = 1.25

WINDOWS: tuple[tuple[str, str, timedelta], ...] = (
    ("limit_5h", "5 hours", timedelta(hours=5)),
    ("limit_day", "a day", timedelta(days=1)),
    ("limit_week", "a week", timedelta(days=7)),
    ("limit_month", "30 days", timedelta(days=30)),
)


# What a credit is worth when nothing says: one cent.
DEFAULT_CREDIT_VALUE = 0.01
UNIT_MONEY = "money"


def enabled(db: DBSession) -> bool:
    from lembas.services import settings_store

    return bool(settings_store.credits(db).get("enabled"))


def credit_value(db: DBSession) -> float:
    """What one credit is worth in the instance's currency (> 0)."""
    from lembas.services import settings_store

    return float(settings_store.credits(db)["credit_value"])


def currency(db: DBSession) -> str:
    from lembas.services import settings_store

    return str(settings_store.credits(db)["currency"])


def money_to_micro(money: float, value: float) -> int:
    """Micro-credits that `money` buys at `value` a credit, rounded **up**: a
    price that costs anything costs at least a micro-credit."""
    if money <= 0 or value <= 0:
        return 0
    return math.ceil(round(money / value * MICRO, 6))


def micro_to_money(micro: int, value: float) -> float:
    """What `micro` micro-credits are worth in money."""
    return int(micro) / MICRO * value


def format_credits(micro: int, code: str | None = None) -> str:
    """Micro-credits as a person reads them, in their language: `123.45`,
    `1,204.00`, `0.0031` -- two places, and up to four below one credit, where
    two would round a reply's cost to nothing."""
    from lembas.web import i18n

    value = int(micro) / MICRO
    if value and abs(value) < 1:
        text = i18n.number(value, 4, code)
        point = i18n.NUMBER_MARKS.get(code or i18n.active(), ("", "."))[1]
        return text.rstrip("0").rstrip(point) or "0"
    return i18n.number(value, 2, code)


def format_money(amount: float, currency_code: str, code: str | None = None) -> str:
    """An amount of money with its sign, in the reader's language: `€1.23` in
    English, `1,23 €` in Slovak. Four places below a cent, for the same reason
    `format_credits` keeps them."""
    from lembas.services.settings_store import CURRENCIES
    from lembas.web import i18n

    sign = CURRENCIES.get(currency_code, currency_code)
    places = 4 if amount and abs(amount) < 0.01 else 2
    text = i18n.number(abs(amount), places, code)
    minus = "-" if amount < 0 else ""
    if i18n.sign_first(code):
        return f"{minus}{sign}{text}"
    return f"{minus}{text}\u00a0{sign}"


def to_micro(credits: float | int | str | None) -> int:
    try:
        value = float(credits or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, math.ceil(round(value * MICRO, 6)))


def show(micro: int) -> str:
    """Micro-credits as a person reads them: 12.5, 0.0031, 1,204."""
    value = micro / MICRO
    if abs(value) >= 100:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{value:.4f}".rstrip("0").rstrip(".") or "0"


def prices(model: Model | None) -> dict[str, float]:
    """The model's non-zero prices as stored, in `price_unit(model)`."""
    raw = (getattr(model, "pricing_json", None) or {}) if model is not None else {}
    out: dict[str, float] = {}
    for key in PRICE_KEYS:
        try:
            value = float(raw.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            out[key] = value
    return out


def price_unit(model: Model | None) -> str:
    """"money" for a price list carrying the money mark, "credits" for one
    without it -- which is what such a dict has to be, whether or not the
    one-time conversion has reached it yet."""
    raw = (getattr(model, "pricing_json", None) or {}) if model is not None else {}
    return UNIT_MONEY if raw.get("unit") == UNIT_MONEY else "credits"


def credit_prices(model: Model | None, value: float = DEFAULT_CREDIT_VALUE) -> dict[str, float]:
    """The model's prices in credits per million tokens (per reply for
    `per_request`), at `value` a credit."""
    stored = prices(model)
    if price_unit(model) != UNIT_MONEY:
        return stored
    return {key: price / value for key, price in stored.items()} if value > 0 else {}


def free(model: Model | None) -> bool:
    return not prices(model)


def cost(
    model: Model | None,
    *,
    prompt_tokens: int = 0,
    cached_tokens: int = 0,
    completion_tokens: int = 0,
    reasoning_tokens: int = 0,
    requests: int = 1,
    cache_write_tokens: int = 0,
    value: float = DEFAULT_CREDIT_VALUE,
) -> int:
    """Micro-credits for one reply. Cached tokens are part of the prompt count
    and priced at `cached_input` instead of `input` when that is set. Tokens
    written to the cache (Anthropic) are part of it too, and priced at
    `CACHE_WRITE_FACTOR` times `input` -- what the provider charges for them.

    `value` is what a credit is worth: money prices are divided by it
    on the way in. A price list without the money mark is credits already and
    ignores it."""
    p = credit_prices(model, value)
    if not p:
        return 0
    cached = min(max(0, cached_tokens), max(0, prompt_tokens))
    written = min(max(0, cache_write_tokens), max(0, prompt_tokens) - cached)
    fresh = max(0, prompt_tokens) - cached - written
    cached_price = p.get("cached_input", p.get("input", 0.0))
    reasoning = min(max(0, reasoning_tokens), max(0, completion_tokens))
    answer = max(0, completion_tokens) - reasoning
    per_million = (
        fresh * p.get("input", 0.0)
        + cached * cached_price
        + written * p.get("input", 0.0) * CACHE_WRITE_FACTOR
        + answer * p.get("output", 0.0)
        + reasoning * p.get("reasoning", p.get("output", 0.0))
    )
    total = per_million  # credits * tokens / 1M  ->  micro-credits: * MICRO / 1M == * 1
    total += requests * p.get("per_request", 0.0) * MICRO
    return math.ceil(round(total, 6))


# --- The ledger -------------------------------------------------------------------
def _account(db: DBSession, user_id: str) -> CreditAccount:
    account = db.get(CreditAccount, user_id)
    if account is None:
        account = CreditAccount(user_id=user_id, balance=0)
        db.add(account)
        db.flush()
    return account


def balance(db: DBSession, user: User) -> int:
    ensure_started(db, user)
    return _account(db, user.id).balance


def add(
    db: DBSession,
    user_id: str,
    delta: int,
    kind: str,
    *,
    note: str = "",
    actor_id: str | None = None,
    plan_id: str | None = None,
    model_id: str = "",
    message_id: str | None = None,
    token_id: str | None = None,
    usage: dict[str, int] | None = None,
    idempotency_key: str | None = None,
    commit: bool = True,
    cost_micro: int = 0,
) -> CreditEntry | None:
    """Write one entry and move the balance with it. A repeated idempotency key
    returns the entry it made the first time, and moves nothing."""
    if idempotency_key:
        found = db.scalar(select(CreditEntry).where(CreditEntry.idempotency_key == idempotency_key))
        if found is not None:
            return found
    usage = usage or {}
    entry = CreditEntry(
        user_id=user_id,
        at=datetime.now(UTC),
        delta=int(delta),
        kind=kind,
        note=note[:1000],
        actor_id=actor_id,
        plan_id=plan_id,
        model_id=model_id[:300],
        message_id=message_id,
        token_id=token_id,
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        cached_tokens=int(usage.get("cached_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        idempotency_key=idempotency_key,
        cost_micro=max(0, int(cost_micro)),
    )
    db.add(entry)
    _account(db, user_id).balance += int(delta)
    if commit:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return db.scalar(
                select(CreditEntry).where(CreditEntry.idempotency_key == idempotency_key)
            )
    return entry


def ensure_started(db: DBSession, user: User) -> None:
    """The first time credits touch an account: its starting grant, and the
    instance's default plan if it has none. Idempotent by the key."""
    from lembas.services import settings_store

    if db.get(CreditAccount, user.id) is not None:
        return
    values = settings_store.credits(db)
    _account(db, user.id)
    grant = to_micro(values.get("starting_grant"))
    if grant:
        add(
            db,
            user.id,
            grant,
            ENTRY_GRANT,
            note="starting grant",
            idempotency_key=f"grant:{user.id}",
            commit=False,
        )
    default_plan = str(values.get("default_plan_id") or "")
    if not getattr(user, "plan_id", None) and default_plan and db.get(Plan, default_plan):
        assign(db, user, default_plan, commit=False)
    db.commit()


# What a usage entry cost: `cost_micro` where it was written, and the
# deduction for an entry from before the column existed and before
# `migrations.backfill_entry_costs` reached it.
ENTRY_COST = case((CreditEntry.cost_micro > 0, CreditEntry.cost_micro), else_=-CreditEntry.delta)


def spent_since(
    db: DBSession, user_id: str, since: datetime, *, token_id: str | None = None
) -> int:
    """Micro-credits of usage since `since` -- what it cost, deducted or not
    (an administrator's is not), optionally through one token only."""
    query = select(func.coalesce(func.sum(ENTRY_COST), 0)).where(
        CreditEntry.user_id == user_id,
        CreditEntry.kind == ENTRY_USAGE,
        CreditEntry.at >= since,
    )
    if token_id is not None:
        query = query.where(CreditEntry.token_id == token_id)
    return int(db.scalar(query) or 0)


def spent_on(db: DBSession, message_ids: list[str]) -> int:
    """Micro-credits the replies with these ids cost: a chat's own spend."""
    if not message_ids:
        return 0
    total = 0
    for start in range(0, len(message_ids), 500):
        total += int(
            db.scalar(
                select(func.coalesce(func.sum(ENTRY_COST), 0)).where(
                    CreditEntry.kind == ENTRY_USAGE,
                    CreditEntry.message_id.in_(message_ids[start : start + 500]),
                )
            )
            or 0
        )
    return total


def spent_by_model(db: DBSession, user_id: str, since: datetime) -> list[tuple[str, int]]:
    """This person's usage since `since`, per model (or service), the most
    expensive first."""
    rows = db.execute(
        select(CreditEntry.model_id, func.coalesce(func.sum(ENTRY_COST), 0))
        .where(
            CreditEntry.user_id == user_id,
            CreditEntry.kind == ENTRY_USAGE,
            CreditEntry.at >= since,
        )
        .group_by(CreditEntry.model_id)
    ).all()
    return sorted(
        ((model_id or "", int(total or 0)) for model_id, total in rows if total),
        key=lambda pair: -pair[1],
    )


@dataclass(frozen=True)
class Window:
    key: str
    label: str
    limit: int
    spent: int
    frees_at: datetime | None

    @property
    def full(self) -> bool:
        return bool(self.limit) and self.spent >= self.limit


def windows(db: DBSession, user: User, *, now: datetime | None = None) -> list[Window]:
    plan = db.get(Plan, user.plan_id) if getattr(user, "plan_id", None) else None
    now = now or datetime.now(UTC)
    out = []
    for key, label, span in WINDOWS:
        limit = int(getattr(plan, key, 0) or 0) if plan else 0
        if not limit:
            continue
        since = now - span
        spent = spent_since(db, user.id, since)
        frees = None
        if spent >= limit:
            oldest = db.scalar(
                select(func.min(CreditEntry.at)).where(
                    CreditEntry.user_id == user.id,
                    CreditEntry.kind == ENTRY_USAGE,
                    CreditEntry.at >= since,
                )
            )
            if oldest is not None:
                oldest = oldest if oldest.tzinfo else oldest.replace(tzinfo=UTC)
                frees = oldest + span
        out.append(Window(key, label, limit, spent, frees))
    return out


def refusal(db: DBSession, user: User | None, model: Model | None) -> str:
    """Why this person may not start a reply on this model, or "".

    An administrator is never refused: not for the balance, not for a
    window, not for a plan's list of models. They run the instance; a meter
    that could lock its own administrator out of it is a meter in the way."""
    if user is None or getattr(user, "is_admin", False) or not enabled(db) or free(model):
        return ""
    return _short(db, user, model)


def service_refusal(db: DBSession, user: User | None, price: float) -> str:
    """The same question for a priced service -- speech or pictures --
    whose price is `price` in money; 0 is free. A plan's list of models does
    not apply: a service is not a model, and a plan that names three chat
    models has said nothing about dictation."""
    if user is None or getattr(user, "is_admin", False) or not enabled(db) or price <= 0:
        return ""
    return _short(db, user, None)


def _short(db: DBSession, user: User, model: Model | None) -> str:
    """The balance, the plan's models and its windows, for a person who is not
    an administrator, on something that costs."""
    ensure_started(db, user)
    plan = db.get(Plan, user.plan_id) if getattr(user, "plan_id", None) else None
    if (
        plan is not None
        and plan.allowed_models
        and model is not None
        and model.model_id not in plan.allowed_models
    ):
        name = model.display_name or model.model_id
        return f"Your plan, {plan.name}, does not include {name}."
    if _account(db, user.id).balance <= 0:
        return "You have no credits left. Ask an administrator for more."
    for window in windows(db, user):
        if window.full:
            when = window.frees_at.strftime("%H:%M on %-d %B") if window.frees_at else "soon"
            return (
                f"You have used this plan's credits for {window.label} "
                f"({show(window.spent)} of {show(window.limit)}). More are free from {when}."
            )
    return ""


def charge(
    db: DBSession,
    user_id: str,
    model: Model | None,
    *,
    usage: dict[str, int],
    message_id: str | None = None,
    token_id: str | None = None,
    commit: bool = True,
) -> int:
    """Charge one reply. Returns the micro-credits it cost (0 when credits are
    off or the model is free) -- for an administrator, what it would have cost,
    recorded and not deducted. Never raises: a charge that cannot be written
    must not lose the reply."""
    try:
        if not enabled(db) or free(model):
            return 0
        amount = cost(
            model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            cached_tokens=usage.get("cached_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            reasoning_tokens=usage.get("reasoning_tokens", 0),
            cache_write_tokens=usage.get("cache_write_tokens", 0),
            value=credit_value(db),
        )
        return _take(
            db,
            user_id,
            amount,
            model_id=model.model_id if model is not None else "",
            message_id=message_id,
            token_id=token_id,
            usage=usage,
            commit=commit,
        )
    except Exception:  # noqa: BLE001 - bookkeeping must never break a reply
        log.warning("could not charge credits to %s", user_id, exc_info=True)
        return 0


def service_cost(db: DBSession, price: float, quantity: float) -> int:
    """Micro-credits for `quantity` units of a service at `price` money each:
    minutes of speech, millions of characters, pictures."""
    return money_to_micro(max(0.0, price) * max(0.0, quantity), credit_value(db))


def charge_service(
    db: DBSession,
    user_id: str,
    *,
    service: str,
    price: float,
    quantity: float,
    note: str,
    token_id: str | None = None,
    commit: bool = True,
) -> int:
    """Charge one use of a service -- `service` is what the ledger's
    model column says (`stt/whisper-1`, `image/sdxl`), `note` what was counted.
    The same rules as `charge`: nothing when credits are off or it is free,
    recorded but not deducted for an administrator, and it never raises."""
    try:
        if not enabled(db) or price <= 0 or quantity <= 0:
            return 0
        return _take(
            db,
            user_id,
            service_cost(db, price, quantity),
            model_id=service,
            note=note,
            token_id=token_id,
            commit=commit,
        )
    except Exception:  # noqa: BLE001 - bookkeeping must never break a request
        log.warning("could not charge %s to %s", service, user_id, exc_info=True)
        return 0


def _take(
    db: DBSession,
    user_id: str,
    amount: int,
    *,
    model_id: str = "",
    note: str = "",
    message_id: str | None = None,
    token_id: str | None = None,
    usage: dict[str, int] | None = None,
    commit: bool = True,
) -> int:
    """Write one usage entry of `amount` micro-credits: deducted from most
    people, recorded at a delta of 0 for an administrator."""
    if amount <= 0:
        return 0
    owner = db.get(User, user_id)
    # An account credits have never touched gets its starting grant first, or
    # the first reply would be charged against nothing.
    if owner is not None:
        ensure_started(db, owner)
    unlimited = bool(owner is not None and owner.is_admin)
    add(
        db,
        user_id,
        0 if unlimited else -amount,
        ENTRY_USAGE,
        note=note,
        model_id=model_id,
        message_id=message_id,
        token_id=token_id,
        usage=usage,
        commit=commit,
        cost_micro=amount,
    )
    return amount


# --- Plans ------------------------------------------------------------------------
def _next_month(moment: datetime) -> datetime:
    year, month = (moment.year + (moment.month // 12), moment.month % 12 + 1)
    day = min(moment.day, 28)
    return moment.replace(year=year, month=month, day=day)


def assign(db: DBSession, user: User, plan_id: str | None, *, commit: bool = True) -> None:
    """Put a person on a plan (None: off any plan). The plan's first month is
    credited at once and it renews a month from now."""
    plan = db.get(Plan, plan_id) if plan_id else None
    user.plan_id = plan.id if plan else None
    if plan is None:
        user.plan_renews_at = None
    else:
        now = datetime.now(UTC)
        _renew(db, user, plan, now, period=f"assign:{now.isoformat()}")
        user.plan_renews_at = _next_month(now)
    if commit:
        db.commit()


def _renew(db: DBSession, user: User, plan: Plan, now: datetime, *, period: str) -> None:
    """One month of a plan. `period` names which month, so a renewal retried
    after a crash is one renewal -- and an assignment and a renewal on the same
    day are two."""
    account = _account(db, user.id)
    stamp = period
    if plan.renewal == RENEW_ADD:
        room = plan.cap - account.balance if plan.cap else plan.monthly
        amount = max(0, min(plan.monthly, room))
        if amount:
            add(
                db,
                user.id,
                amount,
                ENTRY_RENEWAL,
                plan_id=plan.id,
                note=plan.name,
                idempotency_key=f"renew:{user.id}:{plan.id}:{stamp}",
                commit=False,
            )
        return
    # Reset: unused credits expire and the plan's amount is set.
    if account.balance > 0:
        add(
            db,
            user.id,
            -account.balance,
            ENTRY_EXPIRE,
            plan_id=plan.id,
            note=f"{plan.name}: unused credits expire",
            idempotency_key=f"expire:{user.id}:{plan.id}:{stamp}",
            commit=False,
        )
    if plan.monthly:
        add(
            db,
            user.id,
            plan.monthly,
            ENTRY_RENEWAL,
            plan_id=plan.id,
            note=plan.name,
            idempotency_key=f"renew:{user.id}:{plan.id}:{stamp}",
            commit=False,
        )


def last_renewal(db: DBSession, user: User, plan: Plan) -> datetime | None:
    """When this person's plan last gave its month, or None."""
    when = db.scalar(
        select(func.max(CreditEntry.at)).where(
            CreditEntry.user_id == user.id,
            CreditEntry.plan_id == plan.id,
            CreditEntry.kind.in_((ENTRY_RENEWAL, ENTRY_EXPIRE)),
        )
    )
    if when is None:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def renew_now(
    db: DBSession,
    user: User,
    *,
    force: bool = False,
    stamp: str = "",
    now: datetime | None = None,
) -> bool:
    """Apply one renewal of this person's plan now, and start the next month
    from today (Admin -> Credits' "Renew now", and the admin API).

    **Once per period** unless `force`: a plan that has already given its month
    within the month now running -- by being assigned, by renewing when due, or
    by an earlier "Renew now" -- is left alone, so a double click or a retried
    request is one renewal. `stamp` names the request (the API's Idempotency-
    Key), so even a forced retry of the same request writes its entries once.
    Returns whether anything was renewed."""
    plan = db.get(Plan, user.plan_id) if getattr(user, "plan_id", None) else None
    if plan is None:
        return False
    now = now or datetime.now(UTC)
    if not force:
        last = last_renewal(db, user, plan)
        if last is not None and _next_month(last) > now:
            return False
    ensure_started(db, user)
    _renew(db, user, plan, now, period=f"now:{stamp or now.isoformat()}")
    user.plan_renews_at = _next_month(now)
    db.commit()
    return True


def renew_due(db: DBSession, *, now: datetime | None = None) -> int:
    """Renew every plan whose month is up. From the ticker."""
    now = now or datetime.now(UTC)
    due = list(
        db.scalars(select(User).where(User.plan_id.is_not(None), User.plan_renews_at <= now))
    )
    for user in due:
        plan = db.get(Plan, user.plan_id)
        if plan is None:
            user.plan_id, user.plan_renews_at = None, None
            continue
        renews = user.plan_renews_at
        renews = renews if renews.tzinfo else renews.replace(tzinfo=UTC)
        _renew(db, user, plan, now, period=f"due:{renews.isoformat()}")
        while renews <= now:
            renews = _next_month(renews)
        user.plan_renews_at = renews
    if due:
        db.commit()
    return len(due)


def ledger(db: DBSession, user_id: str, limit: int = 50) -> list[CreditEntry]:
    return list(
        db.scalars(
            select(CreditEntry)
            .where(CreditEntry.user_id == user_id)
            .order_by(CreditEntry.at.desc())
            .limit(limit)
        )
    )


def month_start(now: datetime | None = None) -> datetime:
    """The first instant of this calendar month, UTC -- where a month's spend
    is counted from, as `usage.period_of` counts tokens."""
    now = now or datetime.now(UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def summary(db: DBSession, user: User) -> dict[str, Any]:
    """What a person's own usage page shows, the money side too --
    the currency and the credit's value, so every figure can be shown in both
    units -- whether this is an administrator (unlimited), and the month's
    spend in total and per model."""
    from lembas.services import settings_store

    plan = db.get(Plan, user.plan_id) if getattr(user, "plan_id", None) else None
    values = settings_store.credits(db)
    on = bool(values.get("enabled"))
    start = month_start()
    return {
        "enabled": on,
        "admin": bool(user.is_admin),
        "currency": values["currency"],
        "credit_value": float(values["credit_value"]),
        "balance": balance(db, user) if on else 0,
        "plan": plan,
        "renews_at": user.plan_renews_at if plan else None,
        "windows": windows(db, user) if plan else [],
        "month_from": start,
        "month_spent": spent_since(db, user.id, start) if on else 0,
        "month_by_model": spent_by_model(db, user.id, start) if on else [],
    }


__all__ = [
    "DEFAULT_CREDIT_VALUE",
    "PRICE_KEYS",
    "UNIT_MONEY",
    "Window",
    "add",
    "assign",
    "balance",
    "charge",
    "charge_service",
    "cost",
    "credit_prices",
    "credit_value",
    "currency",
    "enabled",
    "ensure_started",
    "format_credits",
    "format_money",
    "free",
    "last_renewal",
    "ledger",
    "micro_to_money",
    "money_to_micro",
    "month_start",
    "price_unit",
    "prices",
    "refusal",
    "renew_due",
    "renew_now",
    "service_cost",
    "service_refusal",
    "show",
    "spent_by_model",
    "spent_on",
    "spent_since",
    "summary",
    "to_micro",
    "windows",
]
