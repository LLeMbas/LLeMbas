"""Jinja environment and the context every template receives."""

from __future__ import annotations

import functools
import re
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from lembas import __version__
from lembas.config import settings
from lembas.db.models import User
from lembas.services import metrics as metrics_service
from lembas.services import steps as steps_service
from lembas.services import tool_blocks, tool_labels
from lembas.services.markdown import highlight_tokens
from lembas.services.reasoning import format_duration
from lembas.web import i18n

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.trim_blocks = True
templates.env.lstrip_blocks = True

# {{ t("Save") }} -> "Uložiť". A global and not a context value, for the reason
# `brand` below gives at length: `render()` is bypassed by 25 direct
# `TemplateResponse` calls and 8 `get_template().render()` calls, and the second
# group is the SSE frame path, which has no `Request` at all. The active language
# comes from a ContextVar rather than from here, because a global is bound once at
# import and the language is per person -- see `web/i18n/__init__.py`.
templates.env.globals["t"] = i18n.t
# {{ tm("Leave <code>0</code> for none.") }} -> a sentence with its markup kept
# whole, so it can be translated whole. See `i18n.tm` for why that is safe.
templates.env.globals["tm"] = i18n.tm
# The part of the catalogue the page's scripts use -- see `i18n.js_strings`.
templates.env.globals["js_strings"] = i18n.js_strings


def permissions_for_people(granted):
    """Settings → Account's "What you can do": see `permissions.FOR_PEOPLE`."""
    from lembas.security import permissions

    return permissions.for_people(granted or {})


templates.env.globals["permissions_for_people"] = permissions_for_people
# For `<html lang>` and `<html dir>`, answered from one place so a right-to-left
# language added later is not forgotten in the template.
templates.env.globals["language"] = i18n.active
templates.env.globals["text_direction"] = i18n.direction
# {{ stamp(row.created_at, "%d %B %Y") }} -> a date whose month name is in the
# reader's language. `strftime` alone is C-locale English on every page.
templates.env.globals["stamp"] = i18n.stamp


def compact_tokens(value: int | float | None) -> str:
    """A token count as a person reads it at a glance: 950, 14.2k, 180k, 1.54M."""
    n = int(value or 0)
    if n < 1000:
        return str(n)
    if n < 100_000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    if n < 1_000_000:
        return f"{round(n / 1000)}k"
    return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + "M"


templates.env.globals["compact_tokens"] = compact_tokens


def short_duration(ms: int | float | None) -> str:
    """A time as a person reads it beside a reply: 0.8s, 14s, 2m 05s, 1h 03m."""
    seconds = max(0.0, float(ms or 0) / 1000)
    if seconds < 10:
        return f"{seconds:.1f}s"
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    minutes, rest = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


templates.env.globals["short_duration"] = short_duration


def continue_prompt() -> str:
    """What Continue sends: the harness spec's `tasks/continue.md`, the
    same sentence as the LLeMbas CLI's /continue."""
    from lembas.services import harness_spec

    return harness_spec.prompt("tasks/continue.md")


templates.env.globals["continue_prompt"] = continue_prompt

# {{ message.reasoning_ms | duration }} -> "8 seconds"
templates.env.filters["duration"] = format_duration

# {{ message.usage_json | metrics }} -> a Metrics, so the finished bubble reads
# its numbers through the same object the live frames are built from.
templates.env.filters["metrics"] = metrics_service.from_message


def stable_hue(value: str) -> int:
    """A deterministic 0-359 hue for a string.

    Used for generated model avatars so each model gets its own colour without
    anyone choosing one, and the same model looks the same on every page and
    after every restart. Python's hash() is salted per process, hence md5.
    """
    import hashlib

    digest = hashlib.md5(value.encode("utf-8"), usedforsecurity=False).digest()
    return int.from_bytes(digest[:2], "big") % 360


templates.env.filters["stable_hue"] = stable_hue


def context_size(tokens: int | None) -> str:
    """A context window as a model list shows it: 131072 -> "131K".

    Decimal thousands, because that is how the number is quoted everywhere a
    person reads it, and a picker that said "128K" for a 131072-token model
    would disagree with the admin page's own figure. Empty for an unknown size,
    so the column slot is still emitted and the next row does not shift.
    """
    if not tokens or tokens <= 0:
        return ""
    if tokens < 1000:
        return str(tokens)
    if tokens < 1_000_000:
        return f"{round(tokens / 1000)}K"
    return f"{tokens / 1_000_000:.1f}".removesuffix(".0") + "M"


templates.env.filters["context_size"] = context_size

# A user's own message: escaped here and marked up, so `@mentions` read as
# references rather than as punctuation. A filter rather than a context value
# because the message templates are included from four different handlers and
# every one of them would otherwise have to remember to pass it.
templates.env.filters["tokens"] = highlight_tokens

# What a tool call is called and what it looks like. Globals rather than
# context values because a message bubble is rendered from four different
# handlers -- pages, post_message, regenerate and the SSE follower -- and every
# one of them would otherwise have to remember to pass them. That is the exact
# trap `audio_service.template_flags` fell into.
templates.env.globals["tool_label"] = tool_labels.label_for
templates.env.globals["tool_icon"] = tool_labels.icon_for

def asset(path: str) -> str:
    """A static asset's URL, with the release stamped into it.

    🚨 This is not cache politeness, it is what stops a release drawing itself
    from two versions at once.

    The service worker caches `/static/...` under a cache named for the
    release, and a *page* is fetched network-first while its assets come from
    that cache. So the moment the worker stops taking over open tabs the
    instant it installs -- which it must, or it swaps the stylesheets under
    somebody mid-reply -- the new HTML and the old CSS are served together and
    the interface is subtly wrong until the worker is replaced. That has shipped
    once: a close button intended for a phone drawer appeared, unstyled, on
    every desktop, because the markup knew about it and the stylesheet did not.

    A version in the URL settles it without anybody having to be careful: the
    new HTML asks for a URL the old cache has never heard of, so it goes to the
    network. The two can no longer disagree, whichever worker is in charge.

    Not a hash of the file: `__version__` is the one thing that already moves
    with every release, and a hash would mean reading every asset on every
    render or a build step, and there is deliberately no build step here.
    """
    return f"/static/{path.lstrip('/')}?v={__version__}"


templates.env.globals["asset"] = asset


# One source for the two colours the browser paints before any stylesheet has
# loaded. `tokens.css` is hand-written and is the place colours live, so the
# `theme-color` metas read their values out of it rather than repeating the hex
# -- the pair in base.html had already drifted once from a token nobody
# re-copied. Read once per process: the file is part of the release.
_THEME_BG = re.compile(r'\[data-theme="(?P<theme>[a-z]+)"\][^{]*\{(?P<block>[^}]*)\}')
_BG = re.compile(r"--bg:\s*(?P<hex>#[0-9A-Fa-f]{3,8})\s*;")


@functools.lru_cache(maxsize=1)
def _theme_colours() -> dict[str, str]:
    text = (STATIC_DIR / "css" / "tokens.css").read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for block in _THEME_BG.finditer(text):
        colour = _BG.search(block.group("block"))
        if colour and block.group("theme") not in found:
            found[block.group("theme")] = colour.group("hex")
    return found


def theme_colour(theme: str) -> str:
    """A built-in theme's `--bg` as declared in tokens.css, or "" if it has
    none -- a meta with an empty `content` is ignored by the browser, which is
    the right failure for a theme that stopped declaring a background."""
    return _theme_colours().get(theme, "")


templates.env.globals["theme_colour"] = theme_colour

# A finished reply as the sequence of steps it was. A global for exactly the
# reason the two above are, and it is why turning the bubble into a sequence
# needed no change in `pages.py`, `post_message`, `regenerate` or the `done`
# frame -- all four of which render `chat/_message.html`.
templates.env.globals["message_steps"] = steps_service.for_message
# How a tool call is drawn, by its kind (services/tool_blocks.py).
templates.env.globals["tool_block"] = tool_blocks.block_for
# The user messages a reply shows inline, where its model took them in.
templates.env.globals["inline_steers"] = lambda messages: {
    sid for m in messages or [] for sid in steps_service.steered_ids(getattr(m, "steps_json", None))
}


def device_offers(chat, capability: str) -> bool:
    """Whether the device a chat runs on is linked now and lists this
    capability -- Edit and Regenerate on a device chat need `rewind`. A global
    for the reason `message_steps` is: `_message.html` is rendered from five
    places, and a link's capabilities live in memory, not on the row."""
    from lembas.services import device_link

    return device_link.offers(getattr(chat, "device_token_id", None), capability)


templates.env.globals["device_offers"] = device_offers


class _Brand:
    """Whose instance this is, as a Jinja global.

    A **global** and not a context value, because `render()` has no database
    session and four render paths never reach it at all -- the login page, the
    error pages, the offline page and the SSE fragments. Threading it through
    every one of those would still leave the ones that bypass `render()`.

    A proxy rather than the snapshot itself, because a global is bound once at
    import and the snapshot changes when an administrator saves. Every attribute
    goes through `branding.snapshot()`, which is a process-level cache: one
    query per process, and one after each save.
    """

    def __getattr__(self, name: str):
        from lembas.services import branding

        return getattr(branding.snapshot(), name)


templates.env.globals["brand"] = _Brand()


def instance_api_only() -> bool:
    """Whether the instance serves the API only, for templates rendered
    without `sidebar_context` -- the admin layout's way back. A function, read
    when called, for the reason `brand` is a proxy: a global is bound once."""
    try:
        from lembas.db.session import session_scope
        from lembas.services import settings_store

        with session_scope() as db:
            return settings_store.api_only(db)
    except Exception:  # a link is not worth failing a page over
        return False


templates.env.globals["instance_api_only"] = instance_api_only


def resolve_theme(user: User | None) -> str:
    """Theme to render with on the server.

    Only ever a first guess: the inline script in base.html corrects it from
    localStorage before first paint. Getting it close server-side is what stops
    a signed-in user seeing a flash of the wrong theme on every navigation.

    Validated against the themes that actually exist rather than against a
    hard-coded pair, or a custom theme would be stored on the account, refused
    here, and rendered as Moria on every page load until localStorage corrected
    it -- a flash on every navigation, which is what this function exists to
    prevent.
    """
    from lembas.services import branding

    brand = branding.snapshot()
    if user is not None:
        chosen = (user.settings_json or {}).get("theme")
        if chosen in brand.theme_ids:
            return chosen
    return brand.default_theme_id(settings.default_theme)


def resolve_layout(user: User | None) -> str:
    """Stored panel widths as a `style` value for <html>, or "".

    Same shape as the theme and for the same reason: a first guess, corrected
    from localStorage before first paint. This is what carries a dragged width
    to a second browser, where localStorage has nothing to say.

    Re-clamped on the way out rather than trusted from the column. The bounds
    could have tightened since it was stored, and a width outside them is a
    panel somebody cannot see well enough to drag back.
    """
    if user is None:
        return ""
    from lembas.api.preferences import LAYOUT_BOUNDS

    parts = []
    for name, raw in ((user.settings_json or {}).get("layout") or {}).items():
        bounds = LAYOUT_BOUNDS.get(str(name))
        if bounds is None:
            continue
        try:
            value = min(max(int(float(raw)), bounds[0]), bounds[1])
        except (TypeError, ValueError):
            continue
        parts.append(f"{name}:{value}px")
    return ";".join(parts)


def render(
    request: Request,
    template: str,
    context: dict[str, Any] | None = None,
    **kwargs: Any,
):
    """Render a template with the globals every page expects.

    Using this instead of templates.TemplateResponse directly is what
    guarantees `user` and `theme` are always defined, so templates never need
    to guard against a missing variable.
    """
    user = getattr(request.state, "user", None)
    payload: dict[str, Any] = {
        "request": request,
        "user": user,
        "theme": resolve_theme(user),
        "layout": resolve_layout(user),
        "version": __version__,
        "allow_signup": settings.allow_signup,
    }
    payload.update(context or {})
    return templates.TemplateResponse(request, template, payload, **kwargs)
