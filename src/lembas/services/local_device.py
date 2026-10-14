"""Which of a person's devices is the computer they are sitting at.

LLeMbas Desktop shows these pages in a webview and runs the bundled LLeMbas CLI
as this computer's linked device. Its webview appends one token to the
User-Agent::

    LLeMbasDesktop/<version> (host=<hostname>)

where the hostname is the operating system's, the same value the CLI sent as
`device_host` when it signed in. A device of the signed-in person's whose host
matches it is shown as **Local** and listed first, wherever devices are listed
to choose or browse. In a browser there is no such token and nothing changes:
every device keeps its host name.

**A display label, nothing more.** The User-Agent is whatever the client writes,
so a forged one can do exactly one thing: relabel one of the signed-in person's
own devices as Local in their own view. It grants nothing, changes no
permission, and never widens which devices are listed -- every caller has
already narrowed the list to the person's own devices before asking this module
which of them is local.

Pages read the host through `current()`, a `ContextVar` the HTTP middleware
sets for every request (`main.py`), because the sidebar is rendered by a few
dozen routes that have no `Request` to hand down. A render outside a request
(a stream frame, a background reply) sees None and shows plain host names.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any

from lembas.web import i18n

# The whole token: a version with no spaces or parentheses, then the host in
# parentheses. Anything else in the User-Agent is somebody else's.
_TOKEN = re.compile(r"(?:^|\s)LLeMbasDesktop/[0-9A-Za-z.+-]{1,64} \(host=([^)\s]*)\)")
# A hostname's characters, and DNS's ceiling on its length.
_HOST = re.compile(r"[A-Za-z0-9._-]{1,253}")

_current: ContextVar[str | None] = ContextVar("lembas_local_host", default=None)


def parse(user_agent: str | None) -> str | None:
    """The host a Desktop User-Agent names, or None for anything else -- a
    browser, a malformed token, a host with characters no hostname has, or two
    tokens that might disagree."""
    found = _TOKEN.findall(str(user_agent or ""))
    if len(found) != 1:
        return None
    host = found[0]
    return host if _HOST.fullmatch(host) else None


def local_host(request: Any) -> str | None:
    """The host of the computer this request comes from, when it comes from
    LLeMbas Desktop; None in a browser. `request` is anything with `headers`."""
    headers = getattr(request, "headers", None)
    return parse(headers.get("user-agent") if headers is not None else None)


def activate(request: Any) -> None:
    """Set the host for the rest of this request. Called for every request, so a
    task reused for the next one never carries the last one's host."""
    _current.set(local_host(request))


def current() -> str | None:
    return _current.get()


def is_local(*hosts: str | None) -> bool:
    """Whether a device known by any of these hosts (its login's `device_host`,
    its link's hello) is the computer this request comes from. Case does not
    matter: hostnames are not case-sensitive."""
    here = current()
    if not here:
        return False
    return any(host and host.strip().lower() == here.lower() for host in hosts)


def label(name: str, *hosts: str | None) -> dict[str, Any]:
    """How to show a device: `name` (its host) as it is, or "Local" with the host
    in the tooltip when it is this computer."""
    if is_local(*hosts):
        return {"name": i18n.t("Local"), "title": i18n.t("This computer (%(host)s)", host=name),
                "local": True}
    return {"name": name, "title": name, "local": False}
