"""Interactive shells, one per agent chat, held open behind the panel.

A tool call is a request-and-answer and holds no state. Here the connection
*is* the state: a PTY with a shell on a linked device, its scrollback, and
whoever is currently watching it.

Shaped after `services/generation.py` -- a registry, a background task that owns
the work, and a socket that merely follows it -- and it differs in three ways
worth knowing:

* **Keyed on the chat, not on a session of its own.** A reload is
  indistinguishable from a second tab, so anything finer needs an id in the
  browser's storage, and then an abandoned tab leaks a shell nothing in the UI
  can find. One chat, one shell. Two tabs share it, like `tmux attach` twice,
  which is the only reading under which "it is still there when you come back"
  means anything. They also share a size, and the smaller one wins.

* **Nothing here ends by itself.** A generation finishes, so `generation.ensure`
  can prune inside itself. A shell sits at a prompt forever and nothing calls in
  again, so there is a reaper task instead. Copying the generation shape here
  would mean nothing was ever swept.

* **A slow reader is dropped, not buffered.** Every viewer has a bounded queue;
  one that fills is disconnected and reattaches with the scrollback. Blocking
  the pump instead would stall every other viewer and buffer without bound
  inside the server -- and `yes` is one word to type.

What a person types here is deliberately not run past `agent/policy.py`. The
modes and the two lists govern a *model*, which reads untrusted pages and files
and can be talked into things. Somebody at a keyboard holds the credential
already and could open the same shell in the device's own terminal; asking them to approve
their own keystrokes would be theatre.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from lembas.services.agent.base import ExecError
from lembas.web import i18n

log = logging.getLogger(__name__)

# What one shell keeps to hand a returning viewer. Bytes rather than lines: a
# line budget is dishonest about a program that writes one very long line.
SCROLLBACK_BYTES = 256 * 1024

# How much output one viewer may fall behind by before it is dropped. Frames
# are whatever the far side wrote, so this is generous in wall-clock terms and
# only reached by a browser that has genuinely stopped reading.
VIEWER_QUEUE = 512

# A size is a number the browser sends. `change_terminal_size(100000, 100000)`
# is a way to ask the far side to allocate.
MAX_COLS = 500
MAX_ROWS = 300
MIN_COLS = 20
MIN_ROWS = 5

# How long a closed session stays in the registry. A tab attaching a second
# after the shell exited should be told what happened rather than silently
# handed a fresh one.
KEEP_CLOSED = 60.0

# How often the reaper looks. Nothing here is urgent: the idle timeout is
# measured in minutes.
REAP_INTERVAL = 30.0

# Why a session ended. The browser is told, and the wording differs enough to be
# worth the constants.
CLOSED_EXITED = "exited"
CLOSED_IDLE = "idle"
CLOSED_SHUTDOWN = "shutdown"
CLOSED_REVOKED = "revoked"
CLOSED_ERROR = "error"


@dataclass
class Viewer:
    """One browser watching one shell."""

    cols: int = 80
    rows: int = 24
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=VIEWER_QUEUE))
    # Everything the shell has said so far, handed over in the same synchronous
    # call that subscribes. Reading the buffer and subscribing as two awaits
    # loses whatever arrives between them.
    snapshot: bytes = b""
    # Set when this viewer fell behind. It is woken with the sentinel below and
    # told to reconnect, which costs it nothing: the scrollback is the state.
    dropped: bool = False


class Session:
    """A shell on the far side of one chat's connection."""

    def __init__(
        self,
        chat_id: str,
        *,
        owner_id: str,
        label: str,
        project_dir: str = "",
        idle_timeout: float = 1800.0,
        cols: int = 80,
        rows: int = 24,
    ) -> None:
        self.chat_id = chat_id
        self.owner_id = owner_id
        self.label = label
        self.project_dir = project_dir
        self.idle_timeout = idle_timeout

        self.viewers: dict[str, Viewer] = {}
        self._scrollback: deque[bytes] = deque()
        self._scrollback_bytes = 0

        self._process: Any = None

        self.closed = False
        self.closed_reason = ""
        self.closed_at = 0.0
        self.started_at = time.monotonic()
        # Bumped by a keystroke and by a viewer coming or going. Idle is this
        # going quiet *with nobody attached*: a build running behind a closed
        # panel is the case this whole lifetime exists for.
        self.last_active = time.monotonic()
        # The size the PTY is *created* with, which matters: a shell prints its
        # prompt before anything could resize it, and a prompt drawn at 80
        # columns inside a 140-column window stays wrong until the next one.
        self.cols = _clamp(cols, MIN_COLS, MAX_COLS)
        self.rows = _clamp(rows, MIN_ROWS, MAX_ROWS)

    # --- Opening -------------------------------------------------------------

    async def start(self) -> None:
        """Open the shell. Only a linked device opens one -- see
        `DeviceSession`."""
        raise ExecError(i18n.t("A terminal opens only on a linked LLeMbas CLI device."))

    # --- Following -----------------------------------------------------------

    def attach(self, cols: int = 80, rows: int = 24) -> Viewer:
        """Subscribe, and take the scrollback, in one synchronous step.

        One step on purpose: reading the buffer and subscribing as two awaits
        loses whatever the shell says between them, which is exactly the moment
        somebody reattaches to a build that is still writing.
        """
        viewer = Viewer(
            cols=_clamp(cols, MIN_COLS, MAX_COLS),
            rows=_clamp(rows, MIN_ROWS, MAX_ROWS),
        )
        viewer.snapshot = b"".join(self._scrollback)
        self.viewers[viewer.id] = viewer
        self.last_active = time.monotonic()
        self.apply_size()
        return viewer

    def detach(self, viewer: Viewer) -> None:
        self.viewers.pop(viewer.id, None)
        # Counts as activity: the timeout is "nobody has been here and nothing
        # has happened for a while", so it starts when the last viewer leaves.
        self.last_active = time.monotonic()
        self.apply_size()

    async def send(self, data: bytes) -> None:
        """Type into the shell."""
        if self.closed or self._process is None:
            return
        self.last_active = time.monotonic()
        try:
            self._process.stdin.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError):
            await self._finish(CLOSED_EXITED)

    def resize(self, viewer: Viewer, cols: int, rows: int) -> None:
        """Record this viewer's size and give the far side the smallest.

        Two tabs on one PTY cannot each have their own geometry. The smaller
        wins in both directions, so nothing is drawn off the edge of the smaller
        window -- the larger one gets an unused margin, which is the harmless
        half of the trade.
        """
        viewer.cols = _clamp(cols, MIN_COLS, MAX_COLS)
        viewer.rows = _clamp(rows, MIN_ROWS, MAX_ROWS)
        self.apply_size()

    def apply_size(self) -> None:
        """Synchronous: `change_terminal_size` only queues a window-change
        message, so there is nothing to await and no reason to make every
        caller a coroutine."""
        if self.closed or self._process is None or not self.viewers:
            return
        cols = min(v.cols for v in self.viewers.values())
        rows = min(v.rows for v in self.viewers.values())
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        with contextlib.suppress(Exception):
            self._process.change_terminal_size(cols, rows)

    # --- Output --------------------------------------------------------------

    def _remember(self, data: bytes) -> None:
        self._scrollback.append(data)
        self._scrollback_bytes += len(data)
        while self._scrollback_bytes > SCROLLBACK_BYTES and len(self._scrollback) > 1:
            self._scrollback_bytes -= len(self._scrollback.popleft())

    def _fan_out(self, data: bytes) -> None:
        for viewer in list(self.viewers.values()):
            try:
                viewer.queue.put_nowait(data)
            except asyncio.QueueFull:
                # Emptied first so the sentinel fits and so the socket does not
                # spend its last moments writing frames nobody will see.
                _drain(viewer.queue)
                viewer.dropped = True
                with contextlib.suppress(asyncio.QueueFull):
                    viewer.queue.put_nowait(None)
                self.viewers.pop(viewer.id, None)

    # --- Closing -------------------------------------------------------------

    async def close(self, reason: str = CLOSED_SHUTDOWN) -> None:
        await self._finish(reason)

    async def _finish(self, reason: str) -> None:
        """Mark this session over and wake everybody watching.

        Called when the shell exits on the far side, and from `close`. The entry
        stays in the registry for KEEP_CLOSED so a late attachment gets an
        explanation.
        """
        if self.closed:
            return
        self.closed = True
        self.closed_reason = reason
        self.closed_at = time.monotonic()
        for viewer in list(self.viewers.values()):
            with contextlib.suppress(asyncio.QueueFull):
                viewer.queue.put_nowait(None)
        await self._teardown()
        log.info(
            "terminal closed chat=%s owner=%s reason=%s after=%.0fs",
            self.chat_id,
            self.owner_id,
            reason,
            time.monotonic() - self.started_at,
        )

    async def _teardown(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            with contextlib.suppress(Exception):
                process.terminate()

    @property
    def idle_for(self) -> float:
        if self.viewers:
            return 0.0
        return time.monotonic() - self.last_active


class DeviceSession(Session):
    """A shell on a linked device, for an agent chat on that device.

    The same session -- registry, scrollback, viewers, idle reaping -- over the
    link: the device opens a PTY when asked
    (`_lembas/terminal/open`), its output arrives as notifications that
    `device_link` hands to `feed`, and keystrokes and sizes go back the same
    way. The device decides whether it opens terminals at all
    (`remote.terminal`) and where (its remote roots, trusted directories), as
    it decides everything else about remote work. No shell integration from
    this side: a device's shell marks its own commands, and the panel reads
    those marks itself.
    """

    def __init__(self, chat_id: str, *, token_id: str, **kwargs: Any) -> None:
        super().__init__(chat_id, **kwargs)
        self.token_id = token_id
        self.terminal_id = ""
        self._link: Any = None
        # Capability `shell`: the device starts its shell with its own
        # integration -- OSC 133 marks and OSC 7 -- and says so. The panel's
        # xterm reads them; this side parses nothing.
        self.osc = False

    async def start(self) -> None:
        from lembas.services import device_link

        link = device_link.link_for(self.token_id)
        if link is None:
            raise ExecError(f"{self.label} is not linked right now.")
        if not link.can("terminal"):
            raise ExecError(
                f"{self.label} does not open terminals for the web UI. On it, set "
                "remote.terminal: true in the LLeMbas CLI's config and "
                "restart its service."
            )
        try:
            opened = await link.request(
                "_lembas/terminal/open",
                {"cwd": self.project_dir, "cols": self.cols, "rows": self.rows},
                timeout=20,
            )
        except device_link.DeviceError as exc:
            raise ExecError(f"{self.label} refused: {exc}") from exc
        except (device_link.LinkClosed, TimeoutError) as exc:
            raise ExecError(f"{self.label} did not open a terminal.") from exc
        self.terminal_id = str((opened or {}).get("terminalId") or "")
        if link.can("shell"):
            self.osc = bool((opened or {}).get("integration"))
        self._link = link
        # The base class reads "is there a process" off this; the link is it.
        self._process = link
        link.terminals[self.terminal_id] = self

    def feed(self, data: bytes) -> None:
        """Output from the device's PTY."""
        if self.closed:
            return
        # Passed through untouched, marks and all: a device's shell marks its
        # own commands in OSC 133, which the panel's xterm reads. Parsing them
        # here as well once turned the server's Copy and Auto on for a shell
        # whose command lines it can never see.
        self._remember(data)
        self._fan_out(data)

    async def ended(self, reason: str) -> None:
        await self._finish(CLOSED_EXITED if reason == "exited" else CLOSED_ERROR)

    async def send(self, data: bytes) -> None:
        if self.closed or self._link is None:
            return
        self.last_active = time.monotonic()
        import base64

        await self._link.notify(
            "_lembas/terminal/input",
            {"terminalId": self.terminal_id, "data": base64.b64encode(data).decode()},
        )

    def apply_size(self) -> None:
        if self.closed or self._link is None or not self.viewers:
            return
        cols = min(v.cols for v in self.viewers.values())
        rows = min(v.rows for v in self.viewers.values())
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(
                self._link.notify(
                    "_lembas/terminal/resize",
                    {"terminalId": self.terminal_id, "cols": cols, "rows": rows},
                )
            )

    async def _teardown(self) -> None:
        link, self._link = self._link, None
        self._process = None
        if link is None:
            return
        link.terminals.pop(self.terminal_id, None)
        with contextlib.suppress(Exception):
            await link.notify("_lembas/terminal/close", {"terminalId": self.terminal_id})


# --- The registry ------------------------------------------------------------

_SESSIONS: dict[str, Session] = {}
_REAPER: asyncio.Task | None = None


def get(chat_id: str) -> Session | None:
    """The live session for a chat, if there is one. Closed ones do not count."""
    session = _SESSIONS.get(chat_id)
    if session is None or session.closed:
        return None
    return session


def count() -> int:
    return sum(1 for s in _SESSIONS.values() if not s.closed)


def count_for(owner_id: str) -> int:
    return sum(1 for s in _SESSIONS.values() if not s.closed and s.owner_id == owner_id)


async def open_session(
    chat_id: str,
    *,
    owner_id: str,
    label: str,
    project_dir: str = "",
    idle_timeout: float = 1800.0,
    max_sessions: int = 20,
    max_per_user: int = 3,
    cols: int = 80,
    rows: int = 24,
    device_token_id: str = "",
) -> Session:
    """The shell for this chat, opening one if it is not already there.

    Idempotent for the same reason `generation.ensure` is: a second tab, or the
    same tab after a reload, must attach to what is running rather than start a
    second shell on the same machine.
    """
    existing = get(chat_id)
    if existing is not None:
        return existing

    _reap()
    if count() >= max_sessions:
        raise ExecError(
            i18n.t(
                "This instance already has as many terminals open as it allows. "
                "Close one, or ask an administrator to raise the limit."
            )
        )
    if count_for(owner_id) >= max_per_user:
        raise ExecError(
            f"You already have {max_per_user} terminal"
            f"{'' if max_per_user == 1 else 's'} open. Close one first."
        )

    common = {
        "owner_id": owner_id,
        "label": label,
        "project_dir": project_dir,
        "idle_timeout": idle_timeout,
        "cols": cols,
        "rows": rows,
    }
    if not device_token_id:
        raise ExecError(i18n.t("A terminal opens only on a linked LLeMbas CLI device."))
    session = DeviceSession(chat_id, token_id=device_token_id, **common)
    await session.start()
    _SESSIONS[chat_id] = session
    _ensure_reaper()
    log.info(
        "terminal opened chat=%s owner=%s host=%s dir=%s",
        chat_id,
        owner_id,
        label,
        project_dir or "~",
    )
    return session


async def close_chat(chat_id: str, reason: str = CLOSED_REVOKED) -> bool:
    session = _SESSIONS.pop(chat_id, None)
    if session is None:
        return False
    await session.close(reason)
    return True


async def close_for_owner(owner_id: str) -> int:
    doomed = [s for s in _SESSIONS.values() if s.owner_id == owner_id and not s.closed]
    for session in doomed:
        _SESSIONS.pop(session.chat_id, None)
        await session.close(CLOSED_REVOKED)
    return len(doomed)


async def shutdown() -> None:
    """End every shell. Called from the lifespan, beside stop_generations."""
    global _REAPER
    reaper, _REAPER = _REAPER, None
    if reaper is not None:
        reaper.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await reaper
    for session in list(_SESSIONS.values()):
        await session.close(CLOSED_SHUTDOWN)
    _SESSIONS.clear()


def _reap() -> None:
    """Drop sessions that have been closed long enough to stop explaining."""
    now = time.monotonic()
    for chat_id, session in list(_SESSIONS.items()):
        if session.closed and now - session.closed_at > KEEP_CLOSED:
            _SESSIONS.pop(chat_id, None)


def _ensure_reaper() -> None:
    global _REAPER
    if _REAPER is None or _REAPER.done():
        _REAPER = asyncio.create_task(_reaper_loop())


async def _reaper_loop() -> None:
    """Close idle shells, then forget closed ones.

    A task rather than a sweep inside `open_session`, which is the shape
    `generation` uses. That works there because a generation ends on its own and
    something calls in again; a shell at a prompt does neither, so a lazy sweep
    would run only when somebody opened the *next* terminal.
    """
    while True:
        try:
            await asyncio.sleep(REAP_INTERVAL)
            for session in list(_SESSIONS.values()):
                if not session.closed and session.idle_for > session.idle_timeout:
                    _SESSIONS.pop(session.chat_id, None)
                    await session.close(CLOSED_IDLE)
            _reap()
        except asyncio.CancelledError:
            raise
        except Exception:  # the reaper must outlive one bad sweep
            log.exception("the terminal reaper raised")


def _drain(queue: asyncio.Queue) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


def _clamp(value: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return low
    return min(max(number, low), high)


__all__ = [
    "CLOSED_EXITED",
    "CLOSED_IDLE",
    "CLOSED_REVOKED",
    "CLOSED_SHUTDOWN",
    "Session",
    "Viewer",
    "close_chat",
    "close_for_owner",
    "count",
    "count_for",
    "get",
    "open_session",
    "shutdown",
]
