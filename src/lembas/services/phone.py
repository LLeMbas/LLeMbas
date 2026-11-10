"""Phone actions (harness spec 3.6.0, `phone.md`): the rules, on plain data.

`phone_action` is carried out by the LLeMbas app on the person's phone and
confirmed there. What is decided here, and nothing else: whether a call is one
the phone can be sent (`check`), what it is sent (the action, its fields only),
and what the model is told of the phone's answer (`result_text`). The waiting
is `generation._settle_phone`; the card is `interaction.phone_card`.

The spec's `conformance/phone.json` drives exactly these functions.
"""

from __future__ import annotations

import re
from typing import Any

from lembas.services import harness_spec

# The capability the app declares with each message it sends (`phone.capability`).
CAPABILITY = "phone_actions"

DONE = "done"
DECLINED = "declined"
FAILED = "failed"

_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2})?$")
_NUMBER = re.compile(r"^[0-9 +()\-./#*]+$")
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def spec() -> dict[str, Any]:
    return harness_spec.loop()["phone"]


def _enum(field: str) -> tuple[str, ...]:
    """A field's allowed values, from the tool's own schema."""
    props = harness_spec.tools()["phone_action"]["parameters"]["properties"]
    return tuple(props[field]["enum"])


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _place(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    lat, lon = value.get("lat"), value.get("lon")
    if _number(lat) and _number(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
        return True
    return any(isinstance(value.get(k), str) and value[k].strip() for k in ("query", "name"))


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def _valid(field: str, value: Any) -> bool:
    if field == "time":
        return isinstance(value, str) and bool(_TIME.match(value.strip()))
    if field == "days":
        return isinstance(value, list) and all(v in _DAYS for v in value)
    if field == "seconds":
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 86400
    if field in ("start", "end"):
        return isinstance(value, str) and bool(_WHEN.match(value.strip()))
    if field in ("to", "cc"):
        return _strings(value)
    if field == "number":
        text = value.strip() if isinstance(value, str) else ""
        return bool(_NUMBER.match(text)) and sum(c.isdigit() for c in text) >= 3
    if field in ("destination", "stop"):
        return _place(value)
    if field == "stops":
        return isinstance(value, list) and all(_place(v) for v in value)
    if field in ("mode", "near", "command", "panel"):
        return value in _enum(field)
    if field == "url":
        text = value.strip().lower() if isinstance(value, str) else ""
        return any(text.startswith(s) and len(text) > len(s) for s in ("http://", "https://"))
    if field == "on":
        return isinstance(value, bool)
    if field == "values":
        return isinstance(value, dict) and all(isinstance(v, str) for v in value.values())
    return True


def _trimmed(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_trimmed(v) for v in value]
    if isinstance(value, dict):
        return {k: _trimmed(v) for k, v in value.items()}
    return value


def check(args: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """`(action, "")` -- what the phone is sent -- or `(None, refusal)`, the
    call's result in the spec's words (`phone.md` §2)."""
    rules = spec()
    texts = harness_spec.loop()["phone"]["texts"]
    action = args.get("action")
    if action not in rules["actions"]:
        return None, harness_spec.fill(
            texts["unknown_action"],
            {"action": str(action or ""), "actions": ", ".join(rules["actions"])},
        )

    missing: list[str] = []
    for entry in rules["required"][action]:
        group = entry if isinstance(entry, list) else [entry]
        if not any(_present(args.get(field)) for field in group):
            missing.append(" or ".join(group))
    if missing:
        return None, harness_spec.fill(
            texts["missing"], {"action": action, "fields": " and ".join(missing)}
        )

    reads = rules["fields"][action]
    for field in rules["check"]:
        if field in reads and _present(args.get(field)) and not _valid(field, args[field]):
            return None, harness_spec.fill(
                texts["bad"], {"field": field, "what": rules["what"][field]}
            )

    summary = args.get("summary")
    out: dict[str, Any] = {
        "action": action,
        "summary": summary.strip() if isinstance(summary, str) and summary.strip() else action,
    }
    for field in reads:
        if _present(args.get(field)) or isinstance(args.get(field), bool):
            out[field] = _trimmed(args[field])
    return out, ""


def result_text(status: str | None, message: str = "") -> str:
    """What the model is told of the phone's answer (`phone.md` §3). `None`
    is no answer in time."""
    rules = spec()
    texts = rules["texts"]
    if status is None:
        return texts["no_answer"]
    if status not in rules["statuses"]:
        status = FAILED
    head = texts[status]
    said = (message or "").strip()[: int(rules["message_max_chars"])].strip()
    if not said:
        return head
    return f"{head}\n\n{harness_spec.fill(texts['says'], {'message': said})}"


def wait_seconds() -> float:
    return float(spec()["wait_seconds"])


def declares(capabilities: Any) -> bool:
    """Whether a message's sender declared the capability: a list of names
    (`["phone_actions"]`), or an object naming it true."""
    if isinstance(capabilities, dict):
        return bool(capabilities.get(CAPABILITY))
    if isinstance(capabilities, (list, tuple)):
        return CAPABILITY in capabilities
    return False


__all__ = [
    "CAPABILITY",
    "DECLINED",
    "DONE",
    "FAILED",
    "check",
    "declares",
    "result_text",
    "spec",
    "wait_seconds",
]
