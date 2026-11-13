"""When a scheduled run is worth telling somebody about, and how.

**A notification comes from a report** (harness spec 3.8.0, `reports.md`). A
schedule's notify-when is `always` (every run files a report), `when` (the
default: only when its condition holds; otherwise the run leaves one line in
the history) or `never` (no report, the history only). A run that left a
report is told once, with the report's title and summary
(`report_notification`); a run that left none sends nothing. `[SILENT]`, what
a quiet run answered before 3.8.0, is no longer told to any model -- it is
still recognised (`is_silent`), so a model that says it out of habit leaves no
report by accident.

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
    NOTIFY_POLICIES,
    NOTIFY_WHEN,
)
from lembas.web.i18n import N_

# The literal a run answered, before 3.8.0, when there was nothing worth
# telling. Retired, and still recognised. ASCII and never translated.
SILENT = "[SILENT]"

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
    """`always` for a one-shot -- somebody who asked to be reminded once wants
    to hear -- and `when` (the spec's default) for anything that repeats."""
    rule = rule or {}
    repeats = bool(rule.get("every") or rule.get("at"))
    return NOTIFY_WHEN if repeats else NOTIFY_ALWAYS


def policy(value: str | None, rule: dict) -> str:
    """A schedule's notify-when as one of `always`, `when`, `never` (harness
    spec 3.8.0). `matters`, what `when` was called before, is read as it."""
    if value == NOTIFY_MATTERS:
        return NOTIFY_WHEN
    # One that says nothing, or something unknown, is `when` -- the spec's
    # default -- never `always`. (`default_policy` is what a *new* schedule
    # is given, and that is stored.)
    return value if value in NOTIFY_POLICIES else NOTIFY_WHEN


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


KIND_REPORT = "report"


def report_notification(report, *, translate) -> dict:
    """A report's notification (harness spec 3.8.0, reports.md §4): its title
    and its summary -- or the first lines of its body -- for a screen that is
    unlocked, and a public version that says only that there is a new report:
    nothing of the account's on a lock screen."""
    from lembas.services import html_pages

    title = " ".join((report.title or "").split())[:TITLE_MAX] or translate(N_("Report"))
    body = report.summary or html_pages.project(report.body or "")
    return {
        "title": title,
        "body": preview(body, BODY_MAX),
        "public": {"title": translate(N_("New report")), "body": ""},
        "actions": ["open"],
        "labels": {"open": translate(N_("Open"))},
        "ask": "",
        "importance": report.importance,
    }


def report_push_parts(report, *, translate) -> tuple[str, dict]:
    """The fixed sentence and the `extra` a report's push carries. The
    sentence says only that there is a report; the report's own words ride in
    `notification`, inside the encrypted payload, for the person's own apps."""
    told = report_notification(report, translate=translate)
    return translate(N_("New report")), {
        "report_id": report.id,
        "schedule_id": report.schedule_id,
        "run_id": report.run_id,
        "visibility": "private",
        "actions": ["open"],
        "notification": told,
    }


def report_link(report) -> str:
    return f"/reports/{report.id}"


async def announce_report(db, owner, *, report, run=None) -> bool:
    """Tell every device the owner registered that a report was filed. True
    when at least one took it."""
    from lembas.services import push
    from lembas.web import i18n

    i18n.activate(i18n.for_user(owner))
    body, extra = report_push_parts(report, translate=i18n.t)
    if run is not None:
        run.notification_json = extra["notification"]
    delivered = await push.announce(
        db, owner, title=i18n.t("Reports"), body=body, url=report_link(report),
        kind=KIND_REPORT, extra=extra, plain_body=True,
    )
    return delivered > 0


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
    "KIND_FAILED",
    "KIND_RESULT",
    "NO_RECURSION",
    "SILENT",
    "KIND_REPORT",
    "announce",
    "announce_report",
    "default_policy",
    "report_notification",
    "failure_decision",
    "is_silent",
    "notification",
    "payload",
    "policy",
    "preview",
    "push_parts",
    "signature",
    "strip_sentinel",
]
