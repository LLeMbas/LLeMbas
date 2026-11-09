"""When a scheduled run is worth telling somebody about, and how.

**Three policies, per schedule.** `always`: every run that produced something.
`matters` (the default for a repeating schedule): every run except one that
answered `[SILENT]` -- the run is told it may (`HINT`), which is what lets "check
the build every hour" stay quiet for the twenty-three hours nothing broke.
`never`: the history only. A one-shot reminder defaults to `always`: being told
is the point of it.

**Failures are told apart, and not too often.** The first failure of a kind is
told at once; the same failure (`signature`: the error's class and its words
with the numbers taken out) again only once every `FAILURE_EVERY`; and after
`PAUSE_AFTER` in a row the notification asks whether to pause it, with a Pause
action. A schedule failing every five minutes must not become a notification
every five minutes -- that is how notifications get switched off for good, and
then the one that mattered is missed.

**What leaves the instance is ids, never content** (`services/push.py`): the
schedule's and the run's ids, the status, `"visibility": "private"` -- the app
keeps the notification's text off the lock screen -- and the actions it may
offer. The app reads the result from `/api/v2/schedules/{id}/runs/{run}`.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta

from lembas.db.models import (
    NOTIFY_ALWAYS,
    NOTIFY_MATTERS,
    NOTIFY_NEVER,
    NOTIFY_POLICIES,
    RUN_OK,
    RUN_SILENT,
)

# The literal a run answers when there is nothing worth telling. ASCII and
# never translated: it is a control word, not a sentence.
SILENT = "[SILENT]"

# What the run is told, when its schedule notifies only when it matters. In
# the turn's own words, after the instruction: it is a property of this run,
# and the chat's system prompt stays the same bytes (spec 3.1.0).
HINT = (
    "If there is genuinely nothing new worth telling the person this time, answer "
    f"with {SILENT} on a line by itself and nothing else; they are then not "
    f"notified. {SILENT} is a literal control word: never translate or rephrase it, "
    "and never combine it with a report. When there is something to say, say it "
    "normally."
)

# Said to every run: it is one run of a schedule that already exists.
NO_RECURSION = (
    "This is one run of an existing schedule: do the work now. Phrases like "
    "\"every Monday\" in the instruction describe the schedule, not a request to "
    "set up another one."
)

FAILURE_EVERY = timedelta(hours=6)
PAUSE_AFTER = 3

KIND_RESULT = "schedule"
KIND_FAILED = "schedule_failed"


def default_policy(rule: dict) -> str:
    """`always` for a one-shot, `matters` for anything that repeats."""
    rule = rule or {}
    repeats = bool(rule.get("every") or rule.get("at"))
    return NOTIFY_MATTERS if repeats else NOTIFY_ALWAYS


def policy(value: str | None, rule: dict) -> str:
    return value if value in NOTIFY_POLICIES else default_policy(rule)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").strip().splitlines() if line.strip()]


def is_silent(text: str) -> bool:
    """Whether a reply said `[SILENT]` on its first or last line (any case, a
    trailing full stop forgiven -- a model writes "[SILENT]." as often as not)."""
    lines = _lines(text)
    if not lines:
        return False

    def marks(line: str) -> bool:
        return line.rstrip(".").strip().upper() == SILENT

    return marks(lines[0]) or marks(lines[-1])


def strip_sentinel(text: str) -> str:
    """The reply without its `[SILENT]` line, for a preview."""
    kept = [
        line
        for line in (text or "").strip().splitlines()
        if line.strip().rstrip(".").strip().upper() != SILENT
    ]
    return "\n".join(kept).strip()


def preview(text: str, limit: int = 280) -> str:
    """The result's first lines, whitespace folded, capped. Model output: it is
    escaped wherever it is shown."""
    folded = " ".join(strip_sentinel(text).split())
    return folded if len(folded) <= limit else folded[: limit - 1].rstrip() + "…"


def should_notify(value: str, status: str) -> bool:
    """Whether a finished run's result is told, by its schedule's policy."""
    if value == NOTIFY_NEVER:
        return False
    if value == NOTIFY_ALWAYS:
        return status in (RUN_OK, RUN_SILENT)
    return status == RUN_OK


_NUMBERS = re.compile(r"\d+")


def signature(error_class: str, error: str) -> str:
    """The same failure, recognised again: its class and its words, numbers out
    (a port, a byte count, a request id), hashed short."""
    words = " ".join(_NUMBERS.sub("#", (error or "").lower()).split())[:200]
    return hashlib.sha256(f"{error_class}|{words}".encode()).hexdigest()[:16]


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def failure_decision(
    notes: dict, *, sig: str, streak: int, now: datetime
) -> tuple[bool, bool, dict]:
    """Whether to tell this failure, whether to ask about pausing, and the
    notes to keep (`Schedule.failure_notes_json`).

    Told: the first of its kind, then once every `FAILURE_EVERY`; and always
    the run that makes `PAUSE_AFTER` in a row, which asks the question.
    """
    notes = dict(notes or {})
    last = _stamp(notes.get(sig))
    ask_pause = streak == PAUSE_AFTER
    tell = ask_pause or last is None or now - last >= FAILURE_EVERY
    if tell:
        notes[sig] = now.isoformat()
    # Old signatures are forgotten after a day, so the dict cannot grow for ever.
    notes = {
        key: value
        for key, value in notes.items()
        if (stamp := _stamp(value)) is not None and now - stamp < timedelta(days=1)
    }
    return tell, ask_pause, notes


def payload(
    *, schedule_id: str, run_id: str, chat_id: str, status: str, failed: bool, ask_pause: bool
) -> dict:
    """What `push.announce` sends besides its own title and body: ids, the
    status, and what the notification may offer. No content."""
    actions = ["open"]
    if ask_pause or failed:
        actions.append("pause")
    return {
        "schedule_id": schedule_id,
        "run_id": run_id,
        "chat_id": chat_id,
        "status": status,
        "visibility": "private",
        "actions": actions,
        "ask": "pause" if ask_pause else "",
    }


async def announce(
    db,
    owner,
    *,
    schedule,
    run,
    failed: bool,
    ask_pause: bool = False,
) -> bool:
    """Tell every device the owner registered (web push and UnifiedPush): one
    run finished, or failed. True when at least one took it."""
    from lembas.services import push
    from lembas.web import i18n

    # The run's own task: the language set here is this task's alone.
    i18n.activate(i18n.for_user(owner))
    if ask_pause:
        body = i18n.t("A scheduled task failed 3 times in a row. Pause it?")
    elif failed:
        body = i18n.t("A scheduled task failed.")
    else:
        body = i18n.t("A scheduled task has finished.")
    extra = payload(
        schedule_id=schedule.id,
        run_id=run.id,
        chat_id=run.chat_id or schedule.chat_id or "",
        status=run.status,
        failed=failed,
        ask_pause=ask_pause,
    )
    extra["pause_label"] = i18n.t("Pause")
    delivered = await push.announce(
        db,
        owner,
        title=i18n.t("Scheduled"),
        body=body,
        url=f"/scheduled/{schedule.id}?run={run.id}",
        kind=KIND_FAILED if failed else KIND_RESULT,
        extra=extra,
        plain_body=True,
    )
    return delivered > 0


__all__ = [
    "HINT",
    "KIND_FAILED",
    "KIND_RESULT",
    "NO_RECURSION",
    "SILENT",
    "announce",
    "default_policy",
    "failure_decision",
    "is_silent",
    "payload",
    "policy",
    "preview",
    "should_notify",
    "signature",
    "strip_sentinel",
]
