"""Updates that wait until nobody is using the instance.

The Update button has always meant "now": the helper restarts the service, and a
reply being written at that moment is lost with it. This waits instead.

**Checking.** Every `check_every_hours` the ticker fetches tags, exactly as the
Check button does. Nothing else touches the network.

**Deciding.** With `auto_apply` set -- `patch`, `minor` or `all`, off by default
-- a newer release is *wanted* when it is within the policy: `patch` only when the
major and minor are the same as the running version's, `minor` when the major
is. **No policy crosses a major version** -- `all` included: a new major is
installed by hand (services/updates.py says why), so `all` takes every release
of the running major. The helper always deploys the newest release of its channel, so the policy
is judged on that one: a minor release out beside a patch is not taken by a
`patch` policy at all, rather than taken as the minor it would really be. An
administrator's *Apply when idle* wants the newest release whatever the policy.

**Waiting.** A wanted update is requested only when the instance is idle -- no
reply being written or paused on an approval, no helper running, no terminal
open, no schedule firing, no indexing, no device writing a reply, and nobody has
done anything for `idle_minutes` -- and, for an automatic one, inside the
optional `window`. Then a **backup** is taken (services/backup.py), and the
request file is written for the root helper, which does the rest. The helper is
unchanged: existing installs need no reinstall.

**Draining.** An update that has been waiting `drain_after_minutes` stops new
replies from starting ("Updating in a moment") so that the running ones can
finish and the instance becomes idle. A drain never stops anything already
running.

Everything here is in memory: one worker, and a restart is what an update is.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from lembas import __version__
from lembas.web.i18n import t

log = logging.getLogger(__name__)

POLICIES = ("off", "patch", "minor", "all")
_WINDOW = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


@dataclass
class AutoState:
    last_check: float = 0.0
    last_check_at: str = ""
    wanted: str = ""  # the release label the next update would deploy
    wanted_since: float = 0.0
    manual: bool = False  # an administrator chose "Apply when idle"
    draining: bool = False
    requested: bool = False
    waiting_for: list[str] = field(default_factory=list)
    error: str = ""


STATE = AutoState()
LAST_ACTIVITY = {"at": 0.0}


def reset() -> None:
    global STATE
    STATE = AutoState()
    LAST_ACTIVITY["at"] = 0.0


def _version(label: str) -> tuple[int, int, int] | None:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", label or "")
    return tuple(int(x) for x in match.groups()) if match else None  # type: ignore[return-value]


def allowed(current: str, target: str, policy: str) -> bool:
    """Whether `policy` takes this jump. Never a downgrade, never a non-release,
    and never a new major version, whatever the policy."""
    have, want = _version(current), _version(target)
    if have is None or want is None or want <= have or policy not in POLICIES:
        return False
    if want[0] != have[0]:
        return False
    if policy in ("all", "minor"):
        return True
    if policy == "patch":
        return want[:2] == have[:2]
    return False


def in_window(window: str, moment: datetime) -> bool:
    """`02:00-05:00` (wrapping past midnight allowed); empty is always."""
    if not (window or "").strip():
        return True
    match = _WINDOW.match(window)
    if not match:
        return True
    h1, m1, h2, m2 = (int(x) for x in match.groups())
    start, end, now = h1 * 60 + m1, h2 * 60 + m2, moment.hour * 60 + moment.minute
    return start <= now < end if start <= end else (now >= start or now < end)


def stamp_activity() -> None:
    LAST_ACTIVITY["at"] = time.monotonic()


def busy(idle_minutes: float) -> list[str]:
    """What the instance is still doing, in words; empty when it is idle."""
    from lembas.services import device_link, generation, subagent
    from lembas.services.agent import terminal
    from lembas.services.library import indexing
    from lembas.services.schedule import ticker

    reasons: list[str] = []
    replies = [g for g in generation._RUNNING.values() if not g.done]
    if replies:
        paused = sum(1 for g in replies if g.pending is not None)
        n = len(replies)
        reasons.append(
            f"{n} repl{'y' if n == 1 else 'ies'} being written"
            + (f" ({paused} waiting for an approval)" if paused else "")
        )
    if subagent.live_count():
        reasons.append(f"{subagent.live_count()} helper(s) running")
    if getattr(terminal, "_SESSIONS", None):
        reasons.append(f"{len(terminal._SESSIONS)} terminal(s) open")
    if getattr(ticker, "_FIRING", None):
        reasons.append("a schedule firing")
    if [t for t in getattr(indexing, "_TASKS", {}).values() if not t.done()]:
        reasons.append("indexing")
    if any(link.writing for link in device_link.LINKS.values()):
        reasons.append("a device writing a reply")
    quiet = time.monotonic() - LAST_ACTIVITY["at"]
    if LAST_ACTIVITY["at"] and quiet < idle_minutes * 60:
        minutes = max(1, round((idle_minutes * 60 - quiet) / 60))
        reasons.append(
            f"somebody was active in the last {idle_minutes:g} minutes (about {minutes} more)"
        )
    return reasons


def refusing() -> str:
    """Why a new reply must not start, during a drain; "" otherwise."""
    if STATE.draining:
        return t("Updating in a moment — try again shortly.")
    return ""


def want_now(label: str) -> None:
    """An administrator's *Apply when idle*: the newest release, whatever the policy."""
    STATE.manual = True
    STATE.wanted = label
    STATE.wanted_since = time.monotonic()


def cancel() -> None:
    STATE.manual = False
    STATE.wanted = ""
    STATE.draining = False


async def apply(who: str) -> str:
    """Back up, then ask the helper. Returns a problem in words, or ""."""
    from lembas.services import audit, backup, updates

    if not updates.helper_installed():
        return t("The update helper is not installed on this host.")
    if not updates.helper_guarded():
        # Root's copy of update.sh predates the same-major rule and takes the
        # newest tag whatever it is. Refused only when that would cross: a
        # fetch first, so the answer is the remote's and not the last check's.
        state = await asyncio.to_thread(updates.read, fetch=True)
        if state.newer_major:
            return t(
                "The update helper on this host would install %(label)s, a new major "
                "version. Re-run the installer with INSTALL_UPDATE_HELPER=1 to replace "
                "it; nothing was requested.",
                label=state.newer_major,
            )
    try:
        taken = await asyncio.to_thread(backup.take, f"before-{STATE.wanted or 'update'}")
    except Exception as exc:  # no update without a backup
        log.exception("pre-update backup failed")
        return t(
            "The backup before the update failed, so nothing was requested: %(error)s", error=exc
        )
    problem = updates.request_update(who)
    if problem:
        return problem
    STATE.requested = True
    audit.record(
        "update.apply",
        actor_label=who,
        system=who == "automatic",
        summary=f"update to {STATE.wanted or 'the newest release'} requested by {who}; "
        f"backup {taken.name}",
    )
    _tell_admins(f"Updating to {STATE.wanted}", "The instance restarts in a moment.")
    return ""


def _tell_admins(title: str, body: str) -> None:
    try:
        from sqlalchemy import select

        from lembas.db.models import ROLE_ADMIN, User
        from lembas.db.session import session_scope
        from lembas.services import push

        with session_scope() as db:
            for admin in db.scalars(select(User).where(User.role == ROLE_ADMIN)):
                push.announce_later(
                    admin.id, title=title, body=body, url="/admin/updates", kind="update"
                )
    except Exception:  # a notification is never worth failing over
        log.debug("could not tell the administrators", exc_info=True)


async def tick(*, now: float | None = None, moment: datetime | None = None) -> None:
    """One pass, from the ticker. Never raises."""
    from lembas.db.session import session_scope
    from lembas.services import audit, settings_store, updates

    now = now if now is not None else time.monotonic()
    try:
        with session_scope() as db:
            values = settings_store.updates(db)
        policy = str(values.get("auto_apply") or "off")
        every = float(values.get("check_every_hours") or 6) * 3600
        if STATE.requested:
            return
        if (policy != "off" or STATE.manual) and (
            not STATE.last_check or now - STATE.last_check >= every
        ):
            state = await asyncio.to_thread(updates.read, fetch=True)
            STATE.last_check = now
            STATE.last_check_at = datetime.now(UTC).isoformat(timespec="minutes")
            STATE.error = state.error
            target = state.available.label if state.available else ""
            audit.record(
                "update.check",
                system=True,
                summary=f"checked: newest {target or 'none'}, running {__version__}",
            )
            if not STATE.manual:
                if target and allowed(__version__, target, policy):
                    if STATE.wanted != target:
                        STATE.wanted, STATE.wanted_since = target, now
                        _tell_admins(
                            f"LLeMbas {target} is out",
                            "It will be installed when the instance is idle.",
                        )
                else:
                    STATE.wanted = ""
        if not STATE.wanted:
            STATE.draining = False
            STATE.waiting_for = []
            return
        moment = moment or datetime.now()
        if not STATE.manual and not in_window(str(values.get("window") or ""), moment):
            STATE.waiting_for = ["the update window"]
            return
        reasons = busy(float(values.get("idle_minutes") or 10))
        STATE.waiting_for = reasons
        if reasons:
            drain = float(values.get("drain_after_minutes") or 60) * 60
            if drain and now - STATE.wanted_since >= drain and not STATE.draining:
                STATE.draining = True
                audit.record(
                    "update.drain",
                    system=True,
                    summary=f"draining for {STATE.wanted}: {'; '.join(reasons)}",
                )
            return
        problem = await apply("an administrator" if STATE.manual else "automatic")
        STATE.error = problem
    except Exception:  # the ticker must outlive one bad pass
        log.exception("the automatic update check raised")


class ActivityMiddleware:
    """Notes when somebody last *did* something: a page opened, or a change
    posted. Polls (an open tab asking for unread dots every few seconds) and
    static files do not count, or an open tab would keep the instance busy for
    ever."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") == "http":
            method = scope.get("method", "GET")
            path = str(scope.get("path", ""))
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
            signed_in = "lembas_session=" in headers.get("cookie", "") or headers.get(
                "authorization", ""
            ).lower().startswith("bearer ")
            page = (
                method == "GET"
                and "text/html" in headers.get("accept", "")
                and ("hx-request" not in headers)
            )
            if signed_in and not path.startswith("/static/") and (method != "GET" or page):
                stamp_activity()
        await self.app(scope, receive, send)


__all__ = [
    "POLICIES",
    "STATE",
    "ActivityMiddleware",
    "allowed",
    "apply",
    "busy",
    "cancel",
    "in_window",
    "refusing",
    "reset",
    "stamp_activity",
    "tick",
    "want_now",
]
