"""A linked device's directories, and the model last used in one (harness spec
3.9.0, `acp.md` *The model last used in a directory*).

LLeMbas cannot look at a device's disk: it asks the device
(`_lembas/directories`), which answers only inside its own remote roots, says
of each directory whether it would open a session there, and -- a device with
the capability `directory_model` -- which model was last used in exactly that
directory.

**The server keeps none of it.** A directory's model is asked of the device
when it is needed and used for the session being opened: a new agent chat the
person chose no model for (`model_for`), a schedule's run on the device that
names none (`services/schedule/runner.py`). It is stored nowhere but as that
chat's own model, and the device is asked again next time.

Two doors read this module and must agree: the web's directory dialog
(`api/devices.py`, a fragment) and `/api/v2/devices/{id}/directories` (JSON).
`browse` is the one answer both draw.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session as DBSession

from lembas.db.models import TOKEN_DEVICE, ApiToken, Model, User
from lembas.web.i18n import N_

log = logging.getLogger(__name__)

CAPABILITY = "directory_model"

# How long a listing may take, and how long the one-directory ask a new chat
# or a run waits on: short, because a chat's first message is waiting behind
# it and "as before" is always a good answer.
LIST_TIMEOUT = 10.0
ASK_TIMEOUT = 2.0

# Why there is no listing, as a code an app can act on and a sentence.
NOT_LINKED = "device_not_linked"
CLI_TOO_OLD = "cli_too_old"
NO_ANSWER = "no_answer"
REFUSED = "refused"
ERRORS = {
    NOT_LINKED: N_("This device is not linked right now."),
    CLI_TOO_OLD: N_(
        "This device's LLeMbas CLI is too old to list its directories. Type the path below."
    ),
    NO_ANSWER: N_("The device did not answer."),
}

FROM_NAMED = "named"
FROM_DIRECTORY = "directory"
FROM_DEFAULT = "default"


# --- The client's rule (pure; conformance `last_model.json`, kind `client`) ------------------
def start(
    *, named: str | None, directory: str | None, has: list[str], default: str | None,
    scheduled: bool = False, interactive_only: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """The model a new agent chat on a device and directory, or a schedule's
    run there, starts on: what was named, else the directory's model when the
    person still has it (compared exactly), else what it started on before.
    For a schedule's run (`scheduled`) the directory's model counts only when
    it is not interactive-only (`interactive.md` §2); a person's chat may
    well start on one."""
    if named:
        return {"model": named, "from": FROM_NAMED}
    barred = scheduled and directory in interactive_only
    if directory and directory in has and not barred:
        return {"model": directory, "from": FROM_DIRECTORY}
    return {"model": default, "from": FROM_DEFAULT}


# --- Whose device ---------------------------------------------------------------------------
def owned_device(db: DBSession, user: User, token_id: str) -> ApiToken | None:
    """One of this person's device tokens they may run an agent turn on now,
    or None -- the same door the web's dialog stands behind."""
    from lembas.services import device_link

    token = db.get(ApiToken, token_id) if token_id else None
    if (
        token is None
        or token.user_id != user.id
        or token.kind != TOKEN_DEVICE
        or device_link.refusal(db, user, token.id)
    ):
        return None
    return token


def ref_of(model: Model) -> str:
    """A model as a device names it: `<provider>/<model id>`, the served id."""
    return f"{model.connection.provider_name}/{model.model_id}"


def _mine(db: DBSession, user: User) -> dict[str, Model]:
    """The chat models this person has now, by ref."""
    from lembas.services import chat as chat_service

    return {
        ref_of(m): m
        for m in chat_service.available_models(db, user)
        if m.connection is not None
    }


def _ref(value: Any) -> str:
    return value.strip()[:300] if isinstance(value, str) else ""


def _entry(raw: dict[str, Any], mine: dict[str, Model]) -> dict[str, Any]:
    """One `Dir` as both doors say it. `model` is the remembered model **as
    one the person still has** (its id and name), else None: a ref nobody
    serves them any more is not offered as a start."""
    row = mine.get(_ref(raw.get("model")))
    path = str(raw.get("path") or "")
    return {
        "path": path,
        "name": str(raw.get("name") or "") or path.rstrip("/").rsplit("/", 1)[-1] or path,
        # Whether a session may be opened there, by the device's own rule.
        "accepted": bool(raw.get("accepted", True)),
        "reason": str(raw.get("reason") or ""),
        "model": {"id": row.model_id, "name": row.display_name or row.model_id}
        if row is not None
        else None,
    }


# --- Listing --------------------------------------------------------------------------------
async def browse(db: DBSession, user: User, token: ApiToken, path: str = "") -> dict[str, Any]:
    """A directory on the device (or, with no path, its places), as one
    answer for the page and the apps.

    `{device, path, name, parent, roots, accepted, reason, model, entries,
    remembers, code, error}`: `code` is "" for a listing, else why there is
    none (`device_not_linked`, `cli_too_old`, `no_answer`, `refused`) with
    `error` the sentence for it; a typed path still works then. `remembers`:
    whether this device says a directory's model at all."""
    from lembas.services import device_link

    out: dict[str, Any] = {
        "device": token.id,
        "path": "",
        "name": "",
        "parent": "",
        "roots": [],
        "accepted": True,
        "reason": "",
        "model": None,
        "entries": [],
        "remembers": False,
        "code": "",
        "error": "",
    }
    link = device_link.link_for(token.id)
    if link is None:
        out["code"] = NOT_LINKED
        return out
    out["roots"] = list(link.roots)
    out["remembers"] = link.can(CAPABILITY)
    try:
        got = await link.request("_lembas/directories", {"path": path}, timeout=LIST_TIMEOUT)
    except device_link.DeviceError as exc:
        message = str(exc)
        if "not found" in message.lower():
            out["code"] = CLI_TOO_OLD
        else:
            out["code"], out["error"] = REFUSED, message
        return out
    except (device_link.LinkClosed, TimeoutError):
        out["code"] = NO_ANSWER
        return out
    answer = got if isinstance(got, dict) else {}
    mine = _mine(db, user)
    here = str(answer.get("path") or "")
    listed = answer.get("entries") if here else answer.get("places")
    out["entries"] = [
        _entry(e, mine) for e in (listed or []) if isinstance(e, dict) and e.get("path")
    ]
    if here:
        out.update(_entry(answer, mine))
        out["parent"] = str(answer.get("parent") or "")
    if answer.get("error"):
        # The device listed what it could and says what it could not.
        out["error"] = str(answer.get("error"))
    return out


# --- One directory's model --------------------------------------------------------------------
async def remembered(token_id: str | None, path: str, *, timeout: float = ASK_TIMEOUT) -> str:
    """The ref of the model last used in exactly `path` on the device, or "".

    The cheap ask (`list: false`: nothing is listed). "" for everything that
    is not an answer with a model -- a device that is away, one without the
    capability (it is not asked at all), a refusal, a slow answer: the caller
    then starts as it did before 3.9.0. Never raises."""
    from lembas.services import device_link

    link = device_link.link_for(token_id)
    if link is None or not path or not link.can(CAPABILITY):
        return ""
    try:
        got = await link.request(
            "_lembas/directories", {"path": path, "list": False}, timeout=timeout
        )
    except (device_link.DeviceError, device_link.LinkClosed, TimeoutError):
        return ""
    except Exception:  # a start must never fail on a nicety
        log.debug("asking %s for a directory's model failed", token_id, exc_info=True)
        return ""
    if not isinstance(got, dict) or not got.get("accepted", True):
        return ""
    return _ref(got.get("model"))


async def model_for(
    db: DBSession, user: User, token_id: str | None, path: str, *,
    usable=None, scheduled: bool = False, timeout: float = ASK_TIMEOUT,
) -> Model | None:
    """The directory's model as a row this person has now, or None (start as
    before). `scheduled`: for a schedule's run, which an interactive-only
    model does not count for. `usable` narrows what counts as having it (a
    run stays inside its schedule's data group)."""
    from lembas.services import interactive

    ref = await remembered(token_id, path, timeout=timeout)
    if not ref:
        return None
    mine = _mine(db, user)
    only = [key for key, row in mine.items() if interactive.model_only(row)]
    if usable is not None:
        mine = {key: row for key, row in mine.items() if usable(row)}
    chosen = start(
        named=None, directory=ref, has=list(mine), default=None,
        scheduled=scheduled, interactive_only=only,
    )
    return mine[ref] if chosen["from"] == FROM_DIRECTORY else None


__all__ = [
    "ASK_TIMEOUT",
    "CAPABILITY",
    "CLI_TOO_OLD",
    "ERRORS",
    "NOT_LINKED",
    "NO_ANSWER",
    "REFUSED",
    "browse",
    "model_for",
    "owned_device",
    "ref_of",
    "remembered",
    "start",
]
