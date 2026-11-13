"""A schedule that runs on a linked computer (harness spec 3.5.0, `acp.md`
*Scheduled work on a linked computer*).

The server decides *when*; the device decides *whether* and *how far*. A run
of such a schedule is an agent chat on that device, in a folder of it, marked
`scheduled` in `_meta.lembas` of `session/new` and `session/prompt`:

- **The device must accept it**: `limits.scheduled` in its hello (the CLI's
  "Accept scheduled work", off by default). Off, the run fails with the
  device's own sentence and is not retried -- it is the person's setting.
- **The mode** is the schedule's, `edit` when none was chosen, never Plan, and
  never wider than Edit unless the person picked Auto on that schedule; the
  device lowers it further to its own `remote.max_mode` (`effective`, the
  conformance area `scheduled`).
- **Approvals** wait as cards -- on the phone too, through the push every
  card sends -- up to the schedule's wait (30 minutes by default), and then
  the device counts them as denied and the turn goes on.
- **Away** when the run is due: the run waits for the device on the steps a
  run waits for a free model, then it is `missed`.

Monitor mode's **command** watch is run on the same device by
`_lembas/monitor`: the device judges the command line in the schedule's mode
and refuses one that would ask (nobody is there), and answers a hash
(`sha256:<hex of the output>` + newline + exit status) and an excerpt.

What a run sends is kept here per chat while it runs (`TURNS`), so
`device_link.context_for` can put it on every request of that session.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from lembas.services import harness_spec
from lembas.web.i18n import N_

# The modes a schedule may choose, in widening order after Plan (which only a
# device's max_mode can impose).
ORDER = ("plan", "manual", "edit", "auto")

# A run on a device waiting for it to come back.
WAIT_DEVICE = "device"
ERROR_DEVICE = "device"
ERROR_DEVICE_AWAY = "device_away"

# chat id -> the `scheduled` object its session is told while the run lasts.
from lembas.services.schedule import notify as notify_service  # noqa: E402

TURNS: dict[str, dict[str, Any]] = {}


def spec() -> dict[str, Any]:
    return harness_spec.loop()["scheduled"]


def modes() -> tuple[str, ...]:
    return tuple(spec()["modes"])


def default_mode() -> str:
    return str(spec()["default_mode"])


def wait_bounds() -> dict[str, int]:
    return {k: int(v) for k, v in spec()["approval_wait_seconds"].items()}


def text(key: str, **values: Any) -> str:
    return harness_spec.fill(spec()["texts"][key], {k: str(v) for k, v in values.items()})


def chosen_mode(value: Any) -> str:
    """The schedule's mode as it is stored and sent: one of `modes()`, the
    default for anything else -- never something wider."""
    value = str(value or "").strip().lower()
    return value if value in modes() else default_mode()


def wait_seconds(value: Any) -> int:
    """An approval's wait in whole seconds, the default when none, clamped."""
    bounds = wait_bounds()
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return bounds["default"]
    if math.isnan(seconds):
        return bounds["default"]
    return max(bounds["min"], min(bounds["max"], int(seconds)))


def lower(mode: str, max_mode: str) -> str:
    """`mode`, no wider than the device's `max_mode` (unknown: no limit)."""
    if max_mode not in ORDER or mode not in ORDER:
        return mode
    return ORDER[min(ORDER.index(mode), ORDER.index(max_mode))]


def effective(
    *, accept: bool, mode: str | None, max_mode: str, approval_wait_s: Any = None
) -> dict[str, Any]:
    """What a device makes of a scheduled turn (`conformance/scheduled.json`):
    `{mode, wait_s}`, or `{refused}` with the reason of its -32602. The
    server asks it to say, in the schedule's form, what will happen there."""
    if not accept:
        return {"refused": "scheduled_off"}
    asked = str(mode or "").strip().lower()
    if asked == "plan":
        return {"refused": "plan"}
    return {"mode": lower(chosen_mode(asked), max_mode), "wait_s": wait_seconds(approval_wait_s)}


def monitor_hash(output: bytes, exit_status: int) -> str:
    """The watch command's hash (`conformance/monitor.json` `hash`): the
    output's bytes, then a newline and the exit status. Computed by the
    device; here for the conformance cases and the server's own tests."""
    import hashlib

    digest = hashlib.sha256(output)
    digest.update(f"\n{int(exit_status)}".encode())
    return "sha256:" + digest.hexdigest()


# --- What a schedule says about it --------------------------------------------------------------
@dataclass
class RunsOn:
    device: str
    cwd: str
    mode: str
    wait_minutes: int


class DeviceChoiceError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def runs_on_of(schedule) -> RunsOn | None:
    if not getattr(schedule, "device_token_id", None):
        return None
    minutes = schedule.approval_wait_minutes
    if not minutes:
        minutes = wait_bounds()["default"] // 60
    return RunsOn(
        device=schedule.device_token_id,
        cwd=schedule.device_dir or "",
        mode=chosen_mode(schedule.device_mode),
        wait_minutes=int(minutes),
    )


def device_name(db, token_id: str | None) -> str:
    from lembas.db.models import ApiToken

    token = db.get(ApiToken, token_id) if token_id else None
    if token is None:
        return ""
    return token.device_host or token.name or ""


def runs_on_dict(db, schedule) -> dict[str, Any] | None:
    """`runs_on` as `/api/v2` says it (acp.md *The HTTP API*), or None."""
    where = runs_on_of(schedule)
    if where is None:
        return None
    return {
        "device": where.device,
        "device_name": device_name(db, where.device) if db is not None else "",
        "cwd": where.cwd,
        "mode": where.mode,
        "approval_wait_minutes": where.wait_minutes,
    }


def validate(
    db, owner, value: Any, *, calling_device: str = ""
) -> tuple[str, str, str, int | None]:
    """`runs_on` from a form, an app or a model: `(token id, cwd, mode, wait
    minutes)`, or all empty for "on the server". `device: "this"` is the
    calling device. Raises `DeviceChoiceError` with a reason a person reads."""
    from lembas.db.models import TOKEN_DEVICE, ApiToken
    from lembas.security import api_tokens

    if value in (None, "", {}, False):
        return "", "", "", None
    if not isinstance(value, dict):
        raise DeviceChoiceError(N_("Say which computer to run it on."))
    wanted = str(value.get("device") or "").strip()
    if not wanted:
        return "", "", "", None
    if wanted == "this":
        if not calling_device:
            raise DeviceChoiceError(N_("Only a linked computer can say \"this\" computer."))
        wanted = calling_device
    token = db.get(ApiToken, wanted)
    if token is None:
        # A device named by its host, as a model or a person says it.
        lowered = wanted.lower()
        for row in db.query(ApiToken).filter(
            ApiToken.user_id == owner.id, ApiToken.kind == TOKEN_DEVICE
        ):
            if lowered in ((row.device_host or "").lower(), (row.name or "").lower()):
                token = row
                break
    if (
        token is None
        or token.user_id != owner.id
        or token.kind != TOKEN_DEVICE
        or not api_tokens.live(token)
    ):
        raise DeviceChoiceError(N_("That computer is not one of your linked computers."))
    cwd = str(value.get("cwd") or "").strip()
    if not cwd.startswith("/") and not (len(cwd) > 2 and cwd[1] == ":"):
        raise DeviceChoiceError(N_("Say which folder on the computer, as a full path."))
    mode = str(value.get("mode") or "").strip().lower() or default_mode()
    if mode == "plan":
        raise DeviceChoiceError(
            N_("A scheduled run cannot use plan mode: nobody is there to approve a plan.")
        )
    if mode not in modes():
        raise DeviceChoiceError(N_("Choose Manual, Edit or Auto."))
    minutes = value.get("approval_wait_minutes")
    wait = None
    if minutes not in (None, ""):
        wait = max(1, wait_seconds(float(minutes) * 60) // 60) if _number(minutes) else None
    return token.id, cwd[:1000], mode, wait


def _number(value: Any) -> bool:
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


# --- A run on the device ------------------------------------------------------------------------
def scheduled_meta(schedule, run) -> dict[str, Any]:
    """The `scheduled` object of `session/new` and `session/prompt`."""
    where = runs_on_of(schedule)
    minutes = where.wait_minutes if where is not None else wait_bounds()["default"] // 60
    return {
        "schedule_id": schedule.id,
        "run_id": run.id,
        "title": schedule.title or "",
        "mode": where.mode if where is not None else default_mode(),
        "approval_wait_s": wait_seconds(minutes * 60),
        # Notify when (harness spec 3.8.0, reports.md §6): told only to a
        # device that declares the capability `reports` -- `for_link` takes it
        # out for any other -- which then offers its own `report_write` and
        # sends the report with the turn's answer.
        "notify": notify_service.policy(schedule.notify, schedule.rule_json or {}),
    }


def for_link(meta: dict[str, Any], link) -> dict[str, Any]:
    """The `scheduled` object as one device is sent it: without `notify` for a
    device that does not declare `reports` (before spec 3.8.0)."""
    if link is not None and link.can("reports"):
        return dict(meta)
    return {key: value for key, value in meta.items() if key != "notify"}


def linked(token_id: str | None):
    """The device's link now, or None (away)."""
    from lembas.services import device_link

    return device_link.link_for(token_id)


def accepts(link) -> bool:
    """Whether the device says it accepts scheduled work (`limits.scheduled`)."""
    limits = (link.hello.get("limits") or {}) if link is not None else {}
    if "scheduled" in limits:
        return bool(limits.get("scheduled"))
    init = ((link.initialized.get("_meta") or {}).get("lembas") or {}) if link else {}
    return bool((init.get("limits") or {}).get("scheduled"))


def refusal_of(link) -> str:
    """Why a linked device will not take a scheduled run now, or ""."""
    if not link.can("scheduled"):
        return N_("That computer's LLeMbas CLI is too old to run scheduled work. Update it.")
    if not accepts(link):
        return text("off")
    return ""


async def look(schedule, run, *, link) -> dict[str, Any]:
    """`_lembas/monitor` for a command watch: the device's answer. Raises
    `device_link.DeviceError` on a refusal, `TimeoutError` past the wait."""
    where = runs_on_of(schedule)
    params = {
        "cwd": where.cwd if where is not None else "",
        "command": schedule.watch_target or "",
        "mode": where.mode if where is not None else default_mode(),
        "schedule_id": schedule.id,
        "run_id": run.id,
    }
    if schedule.watch_timeout:
        params["timeout_s"] = int(schedule.watch_timeout)
    limit = int(spec()["monitor"]["timeout_seconds"]["max"])
    answer = await link.request("_lembas/monitor", params, timeout=limit + 30)
    return answer if isinstance(answer, dict) else {}


__all__ = [
    "ERROR_DEVICE",
    "ERROR_DEVICE_AWAY",
    "TURNS",
    "WAIT_DEVICE",
    "DeviceChoiceError",
    "RunsOn",
    "accepts",
    "chosen_mode",
    "effective",
    "linked",
    "look",
    "lower",
    "monitor_hash",
    "refusal_of",
    "runs_on_dict",
    "runs_on_of",
    "scheduled_meta",
    "validate",
    "wait_seconds",
]
