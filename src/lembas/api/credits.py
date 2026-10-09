"""Credits in the browser and over the API.

- **Admin → Credits** (administrators, and anybody with *Grant credits*): the
  settings, the plans, and every account's balance with a top-up, an adjustment
  and its plan.
- **`/api/v1/admin/credits/…`** with an `admin:credits` token: the same
  changes -- top-up, adjustment, plan (with an optional renewal date) and a
  renewal now -- each taking an `Idempotency-Key` header so a retried
  request is one change. **`/api/v1/admin/plans`** lists the plans, and
  makes, changes and deletes them: those three need the token's holder to be an
  administrator as well, as the page's plan forms do.

**Nothing a person does for themselves renews a plan or adds a credit** (pinned
by a test): every route here that moves credits or changes a plan asks
for an administrator, *Grant credits*, or a token whose holder still has one of
the two.
- **`/api/v1/me/credits`** with a `models` token: the holder's own balance, plan
  and windows -- what a logged-in LLeMbas CLI shows.

Every change is an audit event.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, Form, Header, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from lembas.api.deps import Db, RequiredUser
from lembas.db.models import (
    ENTRY_ADJUST,
    ENTRY_TOPUP,
    MICRO,
    RENEWALS,
    CreditAccount,
    Plan,
    User,
)
from lembas.security import permissions
from lembas.security.api_tokens import CreditsCaller, ModelsCaller
from lembas.services import audit, settings_store
from lembas.services import credits as credits_service
from lembas.web import i18n
from lembas.web.templating import render

router = APIRouter(tags=["credits"])


def _may_grant(db, user: User) -> bool:
    return user.is_admin or permissions.has(db, user, "credits.grant")


def _guard(db, user: User) -> None:
    if not _may_grant(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, i18n.t("You may not manage credits."))


def _back(message: str, ok: bool = True) -> Response:
    field = "saved" if ok else "error"
    return RedirectResponse(
        f"/admin/credits?{field}={quote(message)}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/admin/credits")
async def credits_page(
    request: Request, db: Db, user: RequiredUser, saved: str = "", error: str = ""
):
    _guard(db, user)
    users = list(db.scalars(select(User).order_by(User.email)))
    balances = {row.user_id: row.balance for row in db.scalars(select(CreditAccount))}
    plans = list(db.scalars(select(Plan).order_by(Plan.name)))
    values = settings_store.credits(db)
    return render(
        request,
        "admin/credits.html",
        {
            "values": values,
            "currencies": list(settings_store.CURRENCIES),
            "plans": plans,
            "plan_names": {plan.id: plan.name for plan in plans},
            "people": users,
            "balances": balances,
            "show": credits_service.format_credits,
            "money": lambda micro: credits_service.format_money(
                credits_service.micro_to_money(micro, values["credit_value"]), values["currency"]
            ),
            "micro": MICRO,
            "renewals": RENEWALS,
            "is_admin": user.is_admin,
            "saved": saved,
            "error": error,
        },
    )


@router.post("/admin/credits/settings")
async def save_settings(
    request: Request,
    db: Db,
    user: RequiredUser,
    enabled: bool = Form(False),
    starting_grant: str = Form("0"),
    default_plan_id: str = Form(""),
    show_balance: bool = Form(False),
    currency: str = Form(""),
    credit_value: str = Form(""),
) -> Response:
    if not user.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, i18n.t("Only an administrator can change this.")
        )
    try:
        grant = max(0.0, float(starting_grant or 0))
    except ValueError:
        grant = 0.0
    if default_plan_id and db.get(Plan, default_plan_id) is None:
        default_plan_id = ""
    before = settings_store.credits(db)
    # A currency nobody offers, or a value that is not a positive
    # number, keeps what was there rather than failing the whole form.
    chosen = currency.strip().upper()
    if chosen not in settings_store.CURRENCIES:
        chosen = before["currency"]
    worth = settings_store.money_amount(credit_value, 0.0)
    if worth <= 0:
        worth = before["credit_value"]
    settings_store.replace(
        db,
        {
            "enabled": enabled,
            "starting_grant": grant,
            "default_plan_id": default_plan_id,
            "show_balance": show_balance,
            "currency": chosen,
            "credit_value": worth,
        },
        key=settings_store.CREDITS,
    )
    audit.record(
        "credits.settings",
        user=user,
        request=request,
        summary=f"credits {'on' if enabled else 'off'}, starting grant {grant:g}, "
        f"a credit worth {worth:g} {chosen}"
        + (
            f" (was {before['credit_value']:g} {before['currency']})"
            if (worth, chosen) != (before["credit_value"], before["currency"])
            else ""
        ),
    )
    return _back(i18n.t("Saved."))


def _micro_field(raw: str) -> int:
    return credits_service.to_micro(raw)


@router.post("/admin/credits/plans")
async def save_plan(
    request: Request,
    db: Db,
    user: RequiredUser,
    plan_id: str = Form(""),
    name: str = Form(...),
    description: str = Form(""),
    monthly: str = Form("0"),
    renewal: str = Form("reset"),
    cap: str = Form("0"),
    limit_5h: str = Form("0"),
    limit_day: str = Form("0"),
    limit_week: str = Form("0"),
    limit_month: str = Form("0"),
    allowed_models: str = Form(""),
) -> Response:
    if not user.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, i18n.t("Only an administrator can change this.")
        )
    plan = db.get(Plan, plan_id) if plan_id else None
    if plan is None:
        plan = Plan(name=name.strip()[:120] or "Plan")
        db.add(plan)
    plan.name = name.strip()[:120] or plan.name
    plan.description = description.strip()[:1000]
    plan.monthly = _micro_field(monthly)
    plan.renewal = renewal if renewal in RENEWALS else "reset"
    plan.cap = _micro_field(cap)
    plan.limit_5h = _micro_field(limit_5h)
    plan.limit_day = _micro_field(limit_day)
    plan.limit_week = _micro_field(limit_week)
    plan.limit_month = _micro_field(limit_month)
    plan.allowed_models = [m.strip() for m in allowed_models.split(",") if m.strip()]
    db.commit()
    audit.record(
        "credits.plan",
        user=user,
        request=request,
        target_kind="plan",
        target_id=plan.id,
        summary=f"saved the plan {plan.name!r}",
    )
    return _back(i18n.t("Plan saved."))


@router.post("/admin/credits/plans/{plan_id}/delete")
async def delete_plan(request: Request, db: Db, user: RequiredUser, plan_id: str) -> Response:
    if not user.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, i18n.t("Only an administrator can change this.")
        )
    plan = db.get(Plan, plan_id)
    if plan is None:
        return _back(i18n.t("There is no such plan."), ok=False)
    on_it = _take_off(db, plan)
    name = plan.name
    db.delete(plan)
    db.commit()
    audit.record(
        "credits.plan_delete",
        user=user,
        request=request,
        target_kind="plan",
        target_id=plan_id,
        summary=f"deleted the plan {name!r} ({on_it} people were on it)",
    )
    return _back(i18n.t("Plan deleted."))


def _change(
    db,
    actor: User,
    person: User,
    *,
    amount: int,
    kind: str,
    note: str,
    key: str | None,
    request=None,
    token=None,
):
    credits_service.ensure_started(db, person)
    entry = credits_service.add(
        db,
        person.id,
        amount,
        kind,
        note=note,
        actor_id=actor.id,
        idempotency_key=key,
        token_id=token.id if token is not None else None,
    )
    audit.record(
        f"credits.{kind}",
        user=actor,
        token=token,
        request=request,
        target_kind="user",
        target_id=person.id,
        summary=f"{kind} {credits_service.show(amount)} credits for {person.email}"
        + (f": {note}" if note else ""),
    )
    return entry


@router.post("/admin/credits/users/{user_id}")
async def change_person(
    request: Request,
    db: Db,
    user: RequiredUser,
    user_id: str,
    action: str = Form(...),
    amount: str = Form("0"),
    note: str = Form(""),
    plan_id: str = Form(""),
    renews_at: str = Form(""),
) -> Response:
    _guard(db, user)
    person = db.get(User, user_id)
    if person is None:
        return _back(i18n.t("There is no such account."), ok=False)
    if action == "renew":
        # One renewal now, once per period -- a double click is one.
        if not person.plan_id:
            return _back(i18n.t("This account is on no plan."), ok=False)
        if not credits_service.renew_now(db, person):
            return _back(
                i18n.t("This plan has already renewed this month; it renews again on %(when)s.",
                       when=_day(person.plan_renews_at)),
                ok=False,
            )
        audit.record(
            "credits.renew",
            user=user,
            request=request,
            target_kind="user",
            target_id=person.id,
            summary=f"renewed the plan of {person.email} now",
        )
        return _back(i18n.t("Renewed."))
    if action == "renews_at":
        # When the plan next renews, set by hand.
        if not person.plan_id:
            return _back(i18n.t("This account is on no plan."), ok=False)
        when = _date(renews_at)
        if when is None:
            return _back(i18n.t("That is not a date."), ok=False)
        person.plan_renews_at = when
        db.commit()
        audit.record(
            "credits.renews_at",
            user=user,
            request=request,
            target_kind="user",
            target_id=person.id,
            summary=f"the plan of {person.email} renews on {when.date().isoformat()}",
        )
        return _back(i18n.t("Saved."))
    if action == "plan":
        credits_service.ensure_started(db, person)
        credits_service.assign(db, person, plan_id or None)
        audit.record(
            "credits.assign",
            user=user,
            request=request,
            target_kind="user",
            target_id=person.id,
            summary=f"put {person.email} on {plan_id and db.get(Plan, plan_id).name or 'no plan'}",
        )
        return _back(i18n.t("Plan changed."))
    try:
        value = float(amount or 0)
    except ValueError:
        return _back(i18n.t("That is not a number."), ok=False)
    micro = round(value * MICRO)
    if action == "topup":
        if micro <= 0:
            return _back(i18n.t("A top-up adds credits: give a positive number."), ok=False)
        _change(
            db,
            user,
            person,
            amount=micro,
            kind=ENTRY_TOPUP,
            note=note.strip(),
            key=None,
            request=request,
        )
    else:
        if not micro or not note.strip():
            return _back(i18n.t("An adjustment needs an amount and a note saying why."), ok=False)
        _change(
            db,
            user,
            person,
            amount=micro,
            kind=ENTRY_ADJUST,
            note=note.strip(),
            key=None,
            request=request,
        )
    return _back(i18n.t("Saved."))


def _date(raw: str) -> datetime | None:
    """`2026-11-01` (or a full ISO timestamp) as midnight UTC of that day, or None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw[:10]), datetime.min.time())
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _day(when: datetime | None) -> str:
    return when.strftime("%Y-%m-%d") if when else "—"


# --- The API ------------------------------------------------------------------------
def _account_json(db, person: User) -> dict[str, Any]:
    summary = credits_service.summary(db, person)
    plan = summary["plan"]
    value = summary["credit_value"]
    return {
        "user_id": person.id,
        "email": person.email,
        "enabled": summary["enabled"],
        "balance": summary["balance"] / MICRO,
        # The money side, and whether the account is unlimited.
        "currency": summary["currency"],
        "credit_value": value,
        "balance_money": round(credits_service.micro_to_money(summary["balance"], value), 6),
        "admin": summary["admin"],
        "plan": {"id": plan.id, "name": plan.name} if plan else None,
        "renews_at": summary["renews_at"].isoformat() if summary["renews_at"] else None,
        "windows": [
            {
                "window": w.key,
                "limit": w.limit / MICRO,
                "spent": w.spent / MICRO,
                "frees_at": w.frees_at.isoformat() if w.frees_at else None,
            }
            for w in summary["windows"]
        ],
    }


@router.get("/api/v1/me/credits")
async def my_credits(db: Db, caller: ModelsCaller) -> dict[str, Any]:
    return _account_json(db, caller.user)


def _person(db, user_id: str) -> User:
    person = db.get(User, user_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no such account.")
    return person


@router.get("/api/v1/admin/credits/{user_id}")
async def get_credits(db: Db, caller: CreditsCaller, user_id: str) -> dict[str, Any]:
    person = _person(db, user_id)
    out = _account_json(db, person)
    out["ledger"] = [
        {
            "at": e.at.isoformat(),
            "delta": e.delta / MICRO,
            "kind": e.kind,
            "note": e.note,
            "model": e.model_id,
            # What a usage entry cost, taken or not (an
            # administrator's is not).
            "cost": e.cost_micro / MICRO,
        }
        for e in credits_service.ledger(db, person.id)
    ]
    return out


@router.post("/api/v1/admin/credits/{user_id}/topup")
async def api_topup(
    request: Request,
    db: Db,
    caller: CreditsCaller,
    user_id: str,
    body: dict[str, Any] = Body(...),
    idempotency_key: str | None = Header(None),
) -> dict[str, Any]:
    person = _person(db, user_id)
    try:
        micro = round(float(body.get("amount") or 0) * MICRO)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "`amount` must be a number.") from exc
    if micro <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A top-up adds credits: `amount` > 0.")
    key = f"api:{caller.token.id}:{idempotency_key}" if idempotency_key else None
    _change(
        db,
        caller.user,
        person,
        amount=micro,
        kind=ENTRY_TOPUP,
        note=str(body.get("note") or "")[:1000],
        key=key,
        request=request,
        token=caller.token,
    )
    return _account_json(db, person)


@router.post("/api/v1/admin/credits/{user_id}/adjust")
async def api_adjust(
    request: Request,
    db: Db,
    caller: CreditsCaller,
    user_id: str,
    body: dict[str, Any] = Body(...),
    idempotency_key: str | None = Header(None),
) -> dict[str, Any]:
    person = _person(db, user_id)
    note = str(body.get("note") or "").strip()
    try:
        micro = round(float(body.get("amount") or 0) * MICRO)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "`amount` must be a number.") from exc
    if not micro or not note:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An adjustment needs `amount` and `note`.")
    key = f"api:{caller.token.id}:{idempotency_key}" if idempotency_key else None
    _change(
        db,
        caller.user,
        person,
        amount=micro,
        kind=ENTRY_ADJUST,
        note=note[:1000],
        key=key,
        request=request,
        token=caller.token,
    )
    return _account_json(db, person)


@router.put("/api/v1/admin/credits/{user_id}/plan")
async def api_plan(
    request: Request, db: Db, caller: CreditsCaller, user_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """`{"plan_id": …}` puts the account on a plan (null: off any), crediting
    its first month. An optional `renews_at` (an ISO date) says when it
    next renews instead of a month from now."""
    person = _person(db, user_id)
    plan_id = body.get("plan_id")
    if plan_id and db.get(Plan, str(plan_id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no such plan.")
    renews_at = None
    if body.get("renews_at") not in (None, ""):
        renews_at = _date(str(body["renews_at"]))
        if renews_at is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "`renews_at` must be an ISO date.")
        if not plan_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "`renews_at` needs a plan to renew."
            )
    credits_service.ensure_started(db, person)
    credits_service.assign(db, person, str(plan_id) if plan_id else None)
    if renews_at is not None:
        person.plan_renews_at = renews_at
        db.commit()
    audit.record(
        "credits.assign",
        user=caller.user,
        token=caller.token,
        request=request,
        target_kind="user",
        target_id=person.id,
        summary=f"plan for {person.email}: {plan_id}"
        + (f", renews {renews_at.date().isoformat()}" if renews_at else ""),
    )
    return _account_json(db, person)


@router.post("/api/v1/admin/credits/{user_id}/renew")
async def api_renew(
    request: Request,
    db: Db,
    caller: CreditsCaller,
    user_id: str,
    body: dict[str, Any] | None = Body(None),
    idempotency_key: str | None = Header(None),
) -> dict[str, Any]:
    """One renewal of the account's plan now, and the next a month from
    today. Once per period: a plan that has already given its month in the
    month now running is left alone (`"renewed": false`) unless the body says
    `{"force": true}`. With an `Idempotency-Key` a retried request -- forced or
    not -- writes its entries once."""
    person = _person(db, user_id)
    if not person.plan_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This account is on no plan.")
    force = bool((body or {}).get("force"))
    stamp = f"api:{caller.token.id}:{idempotency_key}" if idempotency_key else ""
    renewed = credits_service.renew_now(db, person, force=force, stamp=stamp)
    if renewed:
        audit.record(
            "credits.renew",
            user=caller.user,
            token=caller.token,
            request=request,
            target_kind="user",
            target_id=person.id,
            summary=f"renewed the plan of {person.email} now" + (" (forced)" if force else ""),
        )
    return {**_account_json(db, person), "renewed": renewed}


def _plan_json(plan: Plan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "name": plan.name,
        "description": plan.description,
        "monthly": plan.monthly / MICRO,
        "renewal": plan.renewal,
        "cap": plan.cap / MICRO,
        "limit_5h": plan.limit_5h / MICRO,
        "limit_day": plan.limit_day / MICRO,
        "limit_week": plan.limit_week / MICRO,
        "limit_month": plan.limit_month / MICRO,
        "allowed_models": plan.allowed_models,
    }


@router.get("/api/v1/admin/plans")
async def api_plans(db: Db, caller: CreditsCaller) -> dict[str, Any]:
    return {"data": [_plan_json(p) for p in db.scalars(select(Plan).order_by(Plan.name))]}


def _plans_admin(caller) -> None:
    """Making, changing and deleting plans is an administrator's, as on the
    page: *Grant credits* moves credits and chooses among the plans there are,
    and does not decide what a plan is."""
    if not caller.user.is_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only an administrator may make, change or delete plans."
        )


PLAN_AMOUNTS = ("monthly", "cap", "limit_5h", "limit_day", "limit_week", "limit_month")


def _apply_plan(plan: Plan, body: dict[str, Any], *, partial: bool) -> None:
    """Fields from a JSON body onto a plan, amounts in credits. `partial` (a
    PUT) leaves out what the body leaves out."""
    if "name" in body or not partial:
        name = str(body.get("name") or "").strip()[:120]
        if not name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A plan needs a `name`.")
        plan.name = name
    if "description" in body or not partial:
        plan.description = str(body.get("description") or "").strip()[:1000]
    if "renewal" in body or not partial:
        renewal = str(body.get("renewal") or "reset")
        if renewal not in RENEWALS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"`renewal` is one of {', '.join(RENEWALS)}."
            )
        plan.renewal = renewal
    for key in PLAN_AMOUNTS:
        if key in body or not partial:
            raw = body.get(key) or 0
            try:
                number = float(raw)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, f"`{key}` must be a number."
                ) from exc
            if number < 0:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"`{key}` cannot be negative.")
            setattr(plan, key, credits_service.to_micro(number))
    if "allowed_models" in body or not partial:
        models = body.get("allowed_models") or []
        if not isinstance(models, list):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "`allowed_models` is a list of model ids."
            )
        plan.allowed_models = [str(m).strip()[:300] for m in models if str(m).strip()]


@router.post("/api/v1/admin/plans", status_code=status.HTTP_201_CREATED)
async def api_create_plan(
    request: Request, db: Db, caller: CreditsCaller, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    _plans_admin(caller)
    plan = Plan(name="Plan")
    _apply_plan(plan, body, partial=False)
    db.add(plan)
    db.commit()
    audit.record(
        "credits.plan",
        user=caller.user,
        token=caller.token,
        request=request,
        target_kind="plan",
        target_id=plan.id,
        summary=f"made the plan {plan.name!r}",
    )
    return _plan_json(plan)


def _plan(db, plan_id: str) -> Plan:
    plan = db.get(Plan, plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "There is no such plan.")
    return plan


@router.put("/api/v1/admin/plans/{plan_id}")
async def api_update_plan(
    request: Request, db: Db, caller: CreditsCaller, plan_id: str, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    _plans_admin(caller)
    plan = _plan(db, plan_id)
    _apply_plan(plan, body, partial=True)
    db.commit()
    audit.record(
        "credits.plan",
        user=caller.user,
        token=caller.token,
        request=request,
        target_kind="plan",
        target_id=plan.id,
        summary=f"changed the plan {plan.name!r}",
    )
    return _plan_json(plan)


@router.delete("/api/v1/admin/plans/{plan_id}")
async def api_delete_plan(
    request: Request, db: Db, caller: CreditsCaller, plan_id: str
) -> dict[str, Any]:
    """Everybody on the plan is taken off it, and their credits stay -- what
    the page's Delete plan does."""
    _plans_admin(caller)
    plan = _plan(db, plan_id)
    on_it = _take_off(db, plan)
    name = plan.name
    db.delete(plan)
    db.commit()
    audit.record(
        "credits.plan_delete",
        user=caller.user,
        token=caller.token,
        request=request,
        target_kind="plan",
        target_id=plan_id,
        summary=f"deleted the plan {name!r} ({on_it} people were on it)",
    )
    return {"id": plan_id, "deleted": True, "people": on_it}


def _take_off(db, plan: Plan) -> int:
    """Everybody off `plan`, and the plan out of the default; how many."""
    on_it = list(db.scalars(select(User).where(User.plan_id == plan.id)))
    for person in on_it:
        person.plan_id, person.plan_renews_at = None, None
    if settings_store.credits(db).get("default_plan_id") == plan.id:
        settings_store.update(db, {"default_plan_id": ""}, key=settings_store.CREDITS)
    return len(on_it)


__all__ = ["router"]
