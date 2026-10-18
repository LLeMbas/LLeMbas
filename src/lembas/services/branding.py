"""What this installation is called, and how it looks.

Instance customization -- logos, favicons, custom themes, custom CSS, rewritten
flavour lines -- is not offered: an instance looks
like LLeMbas. What remains is the **name** (Settings → General, because device
logins and `/.well-known/lembas.json` show it), the Middle-earth lines as they
ship, and the two built-in themes, Moria and Shire, of which each person still
chooses their own.

A process-level snapshot behind a Jinja global, as before: the login page, the
error pages and the SSE path do not go through `render()`, and every one of them
needs the name. `forget()` is called when the name is saved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from lembas.services import settings_store
from lembas.web.i18n import N_

log = logging.getLogger(__name__)

DEFAULT_NAME = "LLeMbas"

# --- Flavour ------------------------------------------------------------------
# Every Middle-earth string in the interface, with its current wording as the
# default. Keyed rather than positional so a template names what it wants, and
# a key nobody has overridden costs nothing to store.
#
# The label is what the admin page calls the field; the hint says where it is
# seen, because a string with no context is one nobody can safely rewrite.
#
# The lines are marked with `N_` and translated where they are shown --
# `t(brand.text.chat_empty)` -- because this table is read once per process and
# the reader's language is per request.
FLAVOUR: dict[str, tuple[str, str, str]] = {
    "login_tagline": (
        "Under the sign-in mark",
        "The one line on the sign-in page, beneath the name.",
        N_("Waybread for the long road of thought."),
    ),
    "chat_empty": (
        "Empty chat",
        "Above the composer on a chat with nothing in it yet.",
        # No commas, on purpose. It is the riddle on the Doors of Durin, and
        # its answer is to *say* "friend" -- the password is the word itself.
        # With commas it is an invitation to a friend, which is the misreading
        # that kept the Fellowship outside the door.
        N_("Speak friend and enter."),
    ),
    "offline_title": (
        "Offline heading",
        "The page the service worker shows when the server cannot be reached.",
        N_("No road from here"),
    ),
    "offline_line": (
        "Offline line",
        "Beneath that heading. The sentence below it is functional and is not "
        "editable here.",
        N_("The Road goes ever on and on — but not without a connection."),
    ),
    "error_403": (
        "403 — not yours",
        "Shown on a page somebody is not allowed to see.",
        N_("Speak friend and enter. This door is not yours to open."),
    ),
    "error_404": (
        "404 — not found",
        "Shown on a page that does not exist.",
        N_("Not all those who wander are lost. This page, however, is."),
    ),
    "error_500": (
        "500 — something broke",
        "Shown when something went wrong on the server.",
        N_("The Road goes ever on, but this stretch of it has washed out."),
    ),
    "theme_moria": (
        "Dark theme name",
        "What the built-in dark theme is called, in the settings screen and in "
        "the /theme command.",
        "Moria",
    ),
    "theme_shire": (
        "Light theme name",
        "What the built-in light theme is called.",
        "Shire",
    ),
}

# --- Themes -------------------------------------------------------------------
# The two built-ins. Their tokens are declared in tokens.css, which is the one
# place colours live.
SCHEME_DARK = "dark"
SCHEME_LIGHT = "light"

BUILT_IN = (
    ("moria", SCHEME_DARK, "#0B0D10"),
    ("shire", SCHEME_LIGHT, "#F1EAD6"),
)


@dataclass(frozen=True)
class Theme:
    """One theme somebody can choose."""

    id: str
    label: str
    scheme: str
    base: str = "moria"
    colour: str = ""
    built_in: bool = True


@dataclass(frozen=True)
class Branding:
    """What a page needs to know about this instance."""

    name: str = DEFAULT_NAME
    text: dict[str, str] = field(default_factory=dict)
    themes: tuple[Theme, ...] = ()

    def default_theme_id(self, fallback: str) -> str:
        """`fallback` (LEMBAS_DEFAULT_THEME) when it names a theme, else Moria."""
        return fallback if fallback in self.theme_ids else "moria"

    @property
    def theme_ids(self) -> tuple[str, ...]:
        return tuple(theme.id for theme in self.themes)

    @property
    def theme_list(self) -> str:
        """`id:base` pairs, space separated, for the `data-themes` attribute."""
        return " ".join(f"{theme.id}:{theme.base}" for theme in self.themes)

    def theme(self, theme_id: str) -> Theme:
        for theme in self.themes:
            if theme.id == theme_id:
                return theme
        return self.themes[0]


def build(name: str = "") -> Branding:
    text = {key: default for key, (_, _, default) in FLAVOUR.items()}
    themes = tuple(
        Theme(id=theme_id, label=text[f"theme_{theme_id}"], scheme=scheme, base=theme_id,
              colour=colour)
        for theme_id, scheme, colour in BUILT_IN
    )
    return Branding(name=name.strip() or DEFAULT_NAME, text=text, themes=themes)


_CACHE: Branding | None = None


def _read(db) -> Branding:
    general = settings_store.get_group(db, settings_store.GENERAL)
    return build(str(general.get("instance_name") or ""))


def snapshot() -> Branding:
    """The current branding. Never raises: an error page that cannot render
    because the name could not be read hides the error it was about to show."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        from lembas.db.session import session_scope

        with session_scope() as db:
            _CACHE = _read(db)
    except Exception:  # defaults are a usable answer, an exception is not
        log.debug("could not read the instance name; using the default", exc_info=True)
        return build()
    return _CACHE


def for_db(db) -> Branding:
    global _CACHE
    if _CACHE is None:
        _CACHE = _read(db)
    return _CACHE


def forget() -> None:
    """Drop the cache: the name was saved (Settings → General), or a test."""
    global _CACHE
    _CACHE = None


__all__ = ["BUILT_IN", "DEFAULT_NAME", "FLAVOUR", "Branding", "Theme", "build", "for_db",
           "forget", "snapshot"]
