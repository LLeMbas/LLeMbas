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

**One notification, every channel** (`notification`): made once, when the
decision to tell is taken, kept on the run (`ScheduleRun.notification_json`)
and sent the same to a browser (web push), an app (UnifiedPush) and the
`/api/v2` `schedules` event a running app follows. It has two versions:

- the **full** one (`title`, `body`): the task's name and the result's first
  lines -- for a phone that is unlocked, a desktop;
- the **public** one (`public.title`, `public.body`): the task's name and
  "Finished" or "Failed", nothing the run wrote -- for the lock screen.
  `"visibility": "private"` tells the app to show only this one there.

The push payload is encrypted end to end (RFC 8291, `services/push.py`): the
relay carries bytes it cannot read. Its actions are `open` and `run_again`, and
`pause` on a failure; their labels ride along in the reader's language.
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
from lembas.web.i18n import N_

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


def actions(*, failed: bool, ask_pause: bool) -> list[str]:
    out = ["open", "run_again"]
    if ask_pause or failed:
        out.append("pause")
    return out


TITLE_MAX = 120
BODY_MAX = 280


def notification(schedule, run, *, failed: bool, ask_pause: bool, translate) -> dict:
    """The notification for one run, in the owner's language (`translate`):
    the full version, the public one and the actions with their labels.

    The full body is the result's first lines (`run.preview`, model output)
    or, for a failure, the question about pausing or the error's first line;
    the public one names the task and says how it ended, and nothing else."""
    name = " ".join((schedule.title or "").split())[:TITLE_MAX]
    name = name or translate(N_("Scheduled task"))
    if ask_pause:
        body = translate(N_("Failed 3 times in a row. Pause it?"))
    elif failed:
        first = (run.error or "").strip().split("\n")[0].strip()
        body = preview(first, BODY_MAX) if first else translate(N_("Failed"))
    else:
        body = preview(run.preview or "", BODY_MAX) or translate(N_("Finished"))
    said = actions(failed=failed, ask_pause=ask_pause)
    labels = {"open": translate(N_("Open")), "run_again": translate(N_("Run again")),
              "pause": translate(N_("Pause"))}
    return {
        "title": name,
        "body": body,
        "public": {"title": name, "body": translate(N_("Failed") if failed else N_("Finished"))},
        "actions": said,
        "labels": {key: labels[key] for key in said},
        "ask": "pause" if ask_pause else "",
    }


def payload(
    *, schedule_id: str, run_id: str, chat_id: str, status: str, failed: bool, ask_pause: bool,
    told: dict | None = None,
) -> dict:
    """What `push.announce` sends besides its own title and body: ids, the
    status, the actions, and the notification (`told`, from `notification`)."""
    out = {
        "schedule_id": schedule_id,
        "run_id": run_id,
        "chat_id": chat_id,
        "status": status,
        "visibility": "private",
        "actions": actions(failed=failed, ask_pause=ask_pause),
        "ask": "pause" if ask_pause else "",
    }
    if told:
        out["notification"] = told
    return out


def push_parts(schedule, run, *, failed: bool, ask_pause: bool, translate) -> tuple[str, dict]:
    """The fixed sentence and the `extra` a run's push carries (`push.announce`
    adds the instance's name, the link and the kind)."""
    if ask_pause:
        body = translate(N_("A scheduled task failed 3 times in a row. Pause it?"))
    elif failed:
        body = translate(N_("A scheduled task failed."))
    else:
        body = translate(N_("A scheduled task has finished."))
    told = notification(schedule, run, failed=failed, ask_pause=ask_pause, translate=translate)
    extra = payload(
        schedule_id=schedule.id,
        run_id=run.id,
        chat_id=run.chat_id or schedule.chat_id or "",
        status=run.status,
        failed=failed,
        ask_pause=ask_pause,
        told=told,
    )
    extra["pause_label"] = translate(N_("Pause"))
    return body, extra


def link(schedule, run) -> str:
    return f"/scheduled/{schedule.id}?run={run.id}"


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
    body, extra = push_parts(schedule, run, failed=failed, ask_pause=ask_pause, translate=i18n.t)
    # Kept on the run before anything is sent: the apps following the
    # `schedules` event show it from there, pushed or not.
    run.notification_json = extra["notification"]
    delivered = await push.announce(
        db,
        owner,
        title=i18n.t("Scheduled"),
        body=body,
        url=link(schedule, run),
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
    "notification",
    "payload",
    "policy",
    "preview",
    "push_parts",
    "should_notify",
    "signature",
    "strip_sentinel",
]
