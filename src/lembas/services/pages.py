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
asked to save. `run` waits for that answer, and raises `NotAttached` when there
is no page or the page went away before answering -- the tool then answers the
spec's `unavailable` sentence, word for word.

In memory, per process: an attachment is a fact about an open socket, not
something to keep across a restart. Nothing here touches the database.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

# How long an announcement holds without a heartbeat.
TTL = 60.0


class NotAttached(Exception):
    """No page of this chat can run Python now."""


@dataclass
class _Page:
    page_id: str
    seen: float
    capabilities: frozenset[str]
    requests: asyncio.Queue = field(default_factory=asyncio.Queue)


@dataclass
class _Call:
    chat_id: str
    page_id: str
    future: asyncio.Future


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
        if call.chat_id == chat_id and call.page_id == page_id and not call.future.done():
            call.future.set_exception(NotAttached())
            _calls.pop(call_id, None)


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
    """The page closed: its pending calls end as `NotAttached`."""
    _drop(chat_id, page_id)


def attached(chat_id: str, capability: str = "python") -> bool:
    """Whether a page of this chat can do `capability` right now."""
    if not chat_id:
        return False
    return any(capability in page.capabilities for page in _fresh(chat_id).values())


async def run(chat_id: str, request: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """Hand `request` to a page of the chat that can run Python, and wait for its answer."""
    pages = [p for p in _fresh(chat_id).values() if "python" in p.capabilities]
    if not pages:
        raise NotAttached()
    page = max(pages, key=lambda p: p.seen)
    call_id = secrets.token_hex(8)
    future = asyncio.get_running_loop().create_future()
    _calls[call_id] = _Call(chat_id, page.page_id, future)
    await page.requests.put({"call_id": call_id, **request})
    try:
        return await asyncio.wait_for(future, timeout)
    finally:
        _calls.pop(call_id, None)


async def take(chat_id: str, page_id: str, *, timeout: float = 25.0) -> dict[str, Any] | None:
    """The next request for this page, or None after `timeout` (a long poll)."""
    page = (_fresh(chat_id)).get(page_id)
    if page is None:
        return None
    try:
        return await asyncio.wait_for(page.requests.get(), timeout)
    except TimeoutError:
        return None


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


__all__ = ["TTL", "NotAttached", "announce", "answer", "attached", "reset", "run", "take",
           "withdraw"]
