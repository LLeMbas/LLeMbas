"""The two models an editor works with: **autocomplete** and **main**.

Two separate entries, which may name the same model. *Autocomplete* is what
`POST /v1/lembas/complete` asks when a request names none; *main* is what an
editor's chat, "ask about this" and "rewrite this" start on.

The autocomplete model has three layers, and the server keeps two of them:

1. the **instance's default**, an administrator's choice (`GENERAL`
   `autocomplete_model`), which may be empty;
2. the **person's own**, kept in their account (`User.settings_json`
   `autocomplete_model`), so it follows them to every editor and app;
3. a quick switch in an editor's window, which is that request's `model` and
   is stored nowhere.

**Allowed for autocomplete** is an administrator's mark on a model
(`Model.autocomplete`). While no model carries it, every chat model a person
may use is allowed; once one does, only the marked ones are. The instance's
default counts where it is allowed, like anybody's choice.

A model is named by its ref, `<provider>/<model id>` -- the id `/v1/models`
lists it under. A stored choice is validated when it is read: a model the
person lost, or one no longer allowed, is not a choice, and the next layer
answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from lembas.db.models import Model, User
from lembas.services import chat as chat_service
from lembas.services import settings_store

# The key of both the instance's setting and the person's.
AUTOCOMPLETE = "autocomplete_model"
# The person's main model in an editor. Not `default_model`: that is what a
# new chat in the web UI starts on, and a person may want another one there.
MAIN = "editor_main_model"

SOURCE_PERSON = "person"
SOURCE_INSTANCE = "instance"


UNKNOWN = "unknown"
EMBEDDING = "embedding"
NOT_ALLOWED = "not_allowed"
NOT_A_NAME = "not_a_name"
NONE_CHOSEN = "none_chosen"


class Refused(Exception):
    """A choice that cannot be taken: the HTTP status that says why, the
    sentence a program reads on `/v1` (English, with where to look), and the
    reason as a word, for a door that says it in the reader's language."""

    def __init__(self, status_code: int, reason: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class Choice:
    """The autocomplete model in force for a person, and whose choice it is."""

    model: Model | None
    source: str | None


def offered(db: DBSession, user: User) -> list[Model]:
    """The chat models this person may use, in the administrator's order."""
    return chat_service.available_models(db, user)


def any_marked(db: DBSession) -> bool:
    """Whether an administrator has marked any model for autocomplete: the
    instance's own fact, whoever asks -- a person who can see none of the
    marked models is allowed none, not all."""
    return db.scalar(select(Model.id).where(Model.autocomplete.is_(True)).limit(1)) is not None


def allowed(db: DBSession, user: User) -> list[Model] | None:
    """The models this person may complete with, or None for "any of theirs"
    (nothing is marked)."""
    if not any_marked(db):
        return None
    return [row for row in offered(db, user) if row.autocomplete]


def is_allowed(db: DBSession, user: User, row: Model) -> bool:
    marked = allowed(db, user)
    return marked is None or any(m.id == row.id for m in marked)


def _named(rows: list[Model], ref: Any) -> Model | None:
    """The row a stored ref names: exactly, never the first with that id."""
    ref = ref.strip() if isinstance(ref, str) else ""
    return next((m for m in rows if chat_service.model_ref(m) == ref), None) if ref else None


def autocomplete(db: DBSession, user: User) -> Choice:
    """The person's own autocomplete model where it still stands, else the
    instance's default where this person may use it, else none."""
    rows = offered(db, user)
    marked = allowed(db, user)
    usable = rows if marked is None else marked
    own = _named(usable, (user.settings_json or {}).get(AUTOCOMPLETE))
    if own is not None:
        return Choice(own, SOURCE_PERSON)
    default = _named(usable, settings_store.get(db, AUTOCOMPLETE))
    if default is not None:
        return Choice(default, SOURCE_INSTANCE)
    return Choice(None, None)


def main(db: DBSession, user: User) -> Model | None:
    """The person's main model in an editor, or None: nothing chosen, and a
    client starts as a new session would."""
    return _named(offered(db, user), (user.settings_json or {}).get(MAIN))


def shape(
    autocomplete_model: str | None,
    source: str | None,
    allowed_models: list[str] | None,
    main_model: str | None,
) -> dict[str, Any]:
    """The settings as both doors answer them (pure)."""
    return {
        "autocomplete": {
            "model": autocomplete_model,
            "source": source if autocomplete_model else None,
            "allowed": allowed_models,
        },
        "main": {"model": main_model},
    }


def view(db: DBSession, user: User) -> dict[str, Any]:
    """`GET /v1/lembas/editor` and `GET /api/v2/editor`."""
    chosen = autocomplete(db, user)
    marked = allowed(db, user)
    main_row = main(db, user)
    return shape(
        chat_service.model_ref(chosen.model) if chosen.model is not None else None,
        chosen.source,
        None if marked is None else [chat_service.model_ref(m) for m in marked],
        chat_service.model_ref(main_row) if main_row is not None else None,
    )


def find(db: DBSession, user: User, wanted: str) -> Model:
    """The chat model `wanted` names for this person: its ref, or an older
    form `/v1` still takes (`openai.find`). Refused in words otherwise."""
    from lembas.api.v1 import openai as openai_api
    from lembas.db.models import MODEL_EMBEDDING

    found = openai_api.find(db, user, wanted)
    if found is None:
        raise Refused(
            404, UNKNOWN,
            f"There is no model {wanted!r} for this account. GET /v1/models lists them.",
        )
    if openai_api.kind_of(found.model) == MODEL_EMBEDDING:
        raise Refused(400, EMBEDDING, f"{found.id} is an embedding model: it cannot write text.")
    return found.model


def for_completion(db: DBSession, user: User, wanted: str = "") -> Model:
    """The model a completion runs on: the one the request names where it is
    allowed, else the person's, else the instance's."""
    if wanted:
        row = find(db, user, wanted)
        if not is_allowed(db, user, row):
            raise Refused(
                403, NOT_ALLOWED,
                f"{chat_service.model_ref(row)} is not allowed for autocomplete on this "
                "instance. GET /v1/lembas/editor lists the models that are.",
            )
        return row
    chosen = autocomplete(db, user)
    if chosen.model is None:
        raise Refused(
            409, NONE_CHOSEN,
            "No autocomplete model is chosen. Choose one in LLeMbas under Settings, "
            "Models, or ask an administrator to set the instance's default.",
        )
    return chosen.model


def refusal_text(exc: Refused) -> str:
    """A refusal in the reader's language, for a page or `/api/v2`."""
    from lembas.web import i18n

    said = {
        UNKNOWN: i18n.t("That model is not available to you."),
        EMBEDDING: i18n.t("That is an embedding model: it cannot write text."),
        NOT_ALLOWED: i18n.t("That model is not allowed for autocomplete here."),
        NOT_A_NAME: i18n.t("A model is named by its id, or null for none."),
    }
    return said.get(exc.reason, exc.message)


def choices(db: DBSession, user: User) -> dict[str, Any]:
    """What a person's settings page offers: for each entry the models as
    `(ref, label)` and the ref chosen ("" for none of their own), and what the
    instance's default is for them, by its label ("" where there is none)."""
    rows = offered(db, user)
    twins = chat_service.twin_ids(rows)

    def label(row: Model) -> str:
        return f"{row.label} ({row.connection.name})" if row.model_id in twins else row.label

    marked = allowed(db, user)
    usable = rows if marked is None else marked
    stored = user.settings_json or {}
    own = _named(usable, stored.get(AUTOCOMPLETE))
    default = _named(usable, settings_store.get(db, AUTOCOMPLETE))
    main_row = main(db, user)
    return {
        "autocomplete": [(chat_service.model_ref(m), label(m)) for m in usable],
        "autocomplete_chosen": chat_service.model_ref(own) if own is not None else "",
        "autocomplete_default": label(default) if default is not None else "",
        "restricted": marked is not None,
        "main": [(chat_service.model_ref(m), label(m)) for m in rows],
        "main_chosen": chat_service.model_ref(main_row) if main_row is not None else "",
    }


_UNSET = object()


def save(
    db: DBSession, user: User, *, autocomplete_model: Any = _UNSET, main_model: Any = _UNSET
) -> None:
    """Write the person's choices. A field left out keeps what is stored;
    `None` (or "") clears one, back to the instance's. Everything is checked
    before anything is written."""
    changes: dict[str, str | None] = {}
    for key, value, completing in (
        (AUTOCOMPLETE, autocomplete_model, True),
        (MAIN, main_model, False),
    ):
        if value is _UNSET:
            continue
        if value is None or value == "":
            changes[key] = None
            continue
        if not isinstance(value, str):
            raise Refused(400, NOT_A_NAME, "A model is named by its id, a string, or null.")
        row = find(db, user, value.strip())
        if completing and not is_allowed(db, user, row):
            raise Refused(
                403, NOT_ALLOWED,
                f"{chat_service.model_ref(row)} is not allowed for autocomplete on this "
                "instance.",
            )
        changes[key] = chat_service.model_ref(row)
    if not changes:
        return
    stored = {**(user.settings_json or {})}
    for key, ref in changes.items():
        if ref is None:
            stored.pop(key, None)
        else:
            stored[key] = ref
    user.settings_json = stored
    db.commit()


def instance_default(db: DBSession) -> str:
    """The instance's default autocomplete model, as stored (a ref, or "")."""
    return str(settings_store.get(db, AUTOCOMPLETE) or "")


def set_instance_default(db: DBSession, row: Model | None) -> None:
    """An administrator's choice. Where models are marked, the default is
    marked too: a default nobody is allowed would be a setting that does
    nothing."""
    if row is None:
        settings_store.update(db, {AUTOCOMPLETE: ""})
        return
    if any_marked(db) and not row.autocomplete:
        row.autocomplete = True
        db.commit()
    settings_store.update(db, {AUTOCOMPLETE: chat_service.model_ref(row)})


__all__ = [
    "AUTOCOMPLETE",
    "MAIN",
    "SOURCE_INSTANCE",
    "SOURCE_PERSON",
    "Choice",
    "Refused",
    "allowed",
    "any_marked",
    "autocomplete",
    "choices",
    "find",
    "for_completion",
    "instance_default",
    "is_allowed",
    "main",
    "offered",
    "refusal_text",
    "save",
    "set_instance_default",
    "shape",
    "view",
]
