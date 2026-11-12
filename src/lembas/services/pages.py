"""Which chats have a browser page attached, and running Python on one.

The `python` tool (harness spec 3.0.0, `requires: page`) runs in the person's
browser -- Pyodide, sandboxed by the browser, nothing on this machine -- so it
can be offered only while a page of the chat is open and has said it can run
it. A page **announces** itself (`announce`, again every `TTL / 3` seconds as
a heartbeat) once Pyodide is loaded, and **withdraws** when it closes; a page
that stops announcing is gone after `TTL`. A schedule, a background job and a
helper's hidden chat have no page and never get the tool.

A call is a **request** the page takes (`take`, long-polled or pushed over the
chat's event stream by whoever builds the runner) and **answers** (`answer`)
with what the code printed, its last value, an error, and the files it was
asked to save. `run` waits for that answer and says honestly which of three
things happened when there is none, because they call for different things
from the person: `NotAttached` -- no page of the chat is open; `NoAnswer` -- a
page is open and none took the call (asleep in a background tab, still loading
Python) or the one that took it went away; `TimeoutError` -- a page took it
and it ran past its time. The tool answers the spec's sentence for each.

Three things keep "no page" from being said of a page that is there:

* `wait_attached` gives a page a moment to arrive before the tool refuses --
  the first message of a new chat starts its reply while the browser is still
  being redirected to the chat's page;
* a page's long poll (`take`) counts as a sign of life like its heartbeat,
  and `TTL` is far above the heartbeat even where a browser slows a
  background tab's timers to one a minute;
* `run` offers a call to the newest page and, if that one has not taken it
  within `PICKUP` seconds, to the next: the newest page may be a tab nobody
  is looking at.

In memory, per process: an attachment is a fact about an open socket, not
something to keep across a restart. Nothing here touches the database.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

# How long an announcement holds without a sign of life. A page announces
# every 20 seconds; a browser may slow a background tab's timers to one a
# minute, and a long poll that is waiting says nothing for 25 seconds.
TTL = 150.0

# How long one page is given to take a call before the next page is offered it.
PICKUP = 4.0


class NotAttached(Exception):
    """No page of this chat can run Python now."""


class NoAnswer(Exception):
    """A page of this chat is open, but the call got no answer from one: none
    took it, or the one that took it went away."""


class UnknownPage(Exception):
    """A page asked for its calls without having announced itself (or after it
    was forgotten): it announces again, rather than asking again at once."""


@dataclass
class _Page:
    page_id: str
    seen: float
    capabilities: frozenset[str]
    requests: asyncio.Queue = field(default_factory=asyncio.Queue)


@dataclass
class _Call:
    chat_id: str
    # The page the call is offered to now; `run` moves it on when that page
    # does not take it.
    page_id: str
    future: asyncio.Future
    taken: asyncio.Event = field(default_factory=asyncio.Event)
    # The page it is offered to went away before taking it.
    gone: asyncio.Event = field(default_factory=asyncio.Event)


_pages: dict[str, dict[str, _Page]] = {}
_calls: dict[str, _Call] = {}


def _fresh(chat_id: str, now: float | None = None) -> dict[str, _Page]:
    now = time.monotonic() if now is None else now
    pages = _pages.get(chat_id) or {}
    for page_id in [p for p, page in pages.items() if now - page.seen > TTL]:
        _drop(chat_id, page_id)
    return _pages.get(chat_id) or {}


def _drop(chat_id: str, page_id: str) -> None:
    pages = _pages.get(chat_id)
    if pages is not None:
        pages.pop(page_id, None)
        if not pages:
            _pages.pop(chat_id, None)
    for call_id, call in list(_calls.items()):
        if call.chat_id != chat_id or call.page_id != page_id or call.future.done():
            continue
        if call.taken.is_set():
            # Taken by the page that has gone: there will be no answer.
            call.future.set_exception(NoAnswer())
            _calls.pop(call_id, None)
        else:
            # Not taken yet: `run` offers it to the next page at once.
            call.gone.set()


def announce(chat_id: str, page_id: str, capabilities: set[str] | frozenset[str]) -> None:
    """A page of `chat_id` is open and can do `capabilities` (`{"python"}`)."""
    pages = _pages.setdefault(chat_id, {})
    page = pages.get(page_id)
    if page is None:
        pages[page_id] = _Page(page_id, time.monotonic(), frozenset(capabilities))
    else:
        page.seen = time.monotonic()
        page.capabilities = frozenset(capabilities)


def withdraw(chat_id: str, page_id: str) -> None:
    """The page closed: a call it had taken ends as `NoAnswer`."""
    _drop(chat_id, page_id)


def attached(chat_id: str, capability: str = "python") -> bool:
    """Whether a page of this chat can do `capability` right now."""
    if not chat_id:
        return False
    return any(capability in page.capabilities for page in _fresh(chat_id).values())


async def wait_attached(
    chat_id: str, capability: str = "python", *, timeout: float, every: float = 0.2
) -> bool:
    """Whether a page of this chat can do `capability`, giving one up to
    `timeout` seconds to arrive."""
    deadline = time.monotonic() + timeout
    while not attached(chat_id, capability):
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(every)
    return True


async def run(
    chat_id: str, request: dict[str, Any], *, timeout: float, pickup: float | None = None
) -> dict[str, Any]:
    """Hand `request` to a page of the chat that can run Python, and wait for
    its answer: `timeout` seconds from the moment a page takes it.

    Offered to the newest page first and, when that one has not taken it within
    `pickup` seconds, to each of the others in turn. Raises `NotAttached` when
    there is no page, `NoAnswer` when none took it or the one that did went
    away, and `TimeoutError` when it ran past its time.
    """
    pickup = PICKUP if pickup is None else pickup
    candidates = sorted(
        (p for p in _fresh(chat_id).values() if "python" in p.capabilities),
        key=lambda p: p.seen,
        reverse=True,
    )
    if not candidates:
        raise NotAttached()
    call_id = secrets.token_hex(8)
    call = _Call(chat_id, "", asyncio.get_running_loop().create_future())
    _calls[call_id] = call
    try:
        for page in candidates:
            # Gone since the list was made: not worth the wait.
            if (_pages.get(chat_id) or {}).get(page.page_id) is not page:
                continue
            call.page_id = page.page_id
            await page.requests.put({"call_id": call_id, **request})
            call.gone.clear()
            waiting = [
                asyncio.ensure_future(call.taken.wait()),
                asyncio.ensure_future(call.gone.wait()),
            ]
            try:
                await asyncio.wait(
                    waiting, timeout=pickup, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for waiter in waiting:
                    waiter.cancel()
            if call.taken.is_set():
                return await asyncio.wait_for(call.future, timeout)
            # Not taken. The request stays in that page's queue under another
            # page's name, which is what `take` skips: a page that wakes up
            # later must not run a call somebody else already has.
        # Nobody took it. With a page still there, that page did not answer;
        # with none left, there is no page.
        raise NoAnswer() if attached(chat_id) else NotAttached()
    finally:
        call.page_id = ""
        _calls.pop(call_id, None)


async def take(chat_id: str, page_id: str, *, timeout: float = 25.0) -> dict[str, Any] | None:
    """The next request for this page, or None after `timeout` (a long poll).

    Asking is a sign of life, like announcing. Raises `UnknownPage` for a page
    that has not announced: answered at once, and the page asked again at
    once -- a loop as fast as the network -- so it is told to announce.
    """
    page = (_fresh(chat_id)).get(page_id)
    if page is None:
        raise UnknownPage()
    deadline = time.monotonic() + timeout
    while True:
        page.seen = time.monotonic()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            request = await asyncio.wait_for(page.requests.get(), remaining)
        except TimeoutError:
            page.seen = time.monotonic()
            return None
        call = _calls.get(str(request.get("call_id") or ""))
        # A call that ended, or was offered to another page meanwhile.
        if call is None or call.page_id != page_id or call.future.done():
            continue
        call.taken.set()
        page.seen = time.monotonic()
        return request


def answer(chat_id: str, page_id: str, call_id: str, result: dict[str, Any]) -> bool:
    """The page's answer to a call it took. False for a call that is not this page's."""
    call = _calls.get(call_id)
    if call is None or call.chat_id != chat_id or call.page_id != page_id:
        return False
    if not call.future.done():
        call.future.set_result(result)
    return True


def reset() -> None:
    """Forget every page (tests)."""
    for call in _calls.values():
        if not call.future.done():
            call.future.cancel()
    _calls.clear()
    _pages.clear()


__all__ = [
    "PICKUP",
    "TTL",
    "NoAnswer",
    "NotAttached",
    "UnknownPage",
    "announce",
    "answer",
    "attached",
    "reset",
    "run",
    "take",
    "wait_attached",
    "withdraw",
]
