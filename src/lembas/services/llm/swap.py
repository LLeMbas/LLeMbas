"""A model being swapped in: seen only where its server says so.

Two things a one-model server (llama-swap) tells a waiting request, and the
only two `loading_model` (harness spec 3.4.0, `progress.md` §4) is set from:

- **`/running`** lists what it holds and in what state; `starting` or
  `loading` for the model this request asked for means the request is waiting
  for it to load. `LoadWatch` asks while a request has had no answer yet --
  never on a hosted API, never once anything has streamed.
- **The loading banner** (`quirks.json` `swap-banner`): with its loading state
  switched on, llama-swap streams the progress of the load as *reasoning*,
  fenced by `━━━━━` lines, before the model's own first token. `SwapBanner`
  takes it out of the reply -- it is not the model thinking -- and says when
  it begins and ends. The LLeMbas CLI's `provider/common.ts` `SwapBanner` is
  the same reader; a case one of them learns goes into the other.

Nothing is guessed from a slow first token: a long prompt on a loaded model
is slow too, and that is `reading_prompt`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from lembas.services.llm import health
from lembas.services.llm.openai_client import Endpoint

log = logging.getLogger(__name__)

FENCE = "━━━━━"
# A banner that never closes is not a banner: past this, what was held back
# is handed on as the reply's own reasoning.
MAX_BANNER = 4000

# The watch: first look after `FIRST`, then every `EVERY` seconds, each look
# given at most `TIMEOUT`.
FIRST = 0.3
EVERY = 1.0
TIMEOUT = 2.0


@dataclass
class Fed:
    """What one piece of reasoning becomes: the text that is the model's
    (`text`), and whether the banner is open (`loading`) or has just closed
    (`loaded`)."""

    text: str
    loading: bool = False
    loaded: bool = False


class SwapBanner:
    """Reads a reply's reasoning, piece by piece, and takes the banner out."""

    def __init__(self) -> None:
        self._held = ""
        self._state = "start"  # start | inside | done
        self._skip_space = False

    @property
    def inside(self) -> bool:
        return self._state == "inside"

    @property
    def done(self) -> bool:
        return self._state == "done"

    def feed(self, text: str) -> Fed:
        if self._state == "done":
            # The banner is followed by blank lines, streamed token by token:
            # they are its, not the model's.
            if self._skip_space:
                text = text.lstrip()
                if text:
                    self._skip_space = False
            return Fed(text)
        self._held += text
        if self._state == "start":
            probe = self._held.lstrip()
            if len(probe) < len(FENCE) and FENCE.startswith(probe):
                return Fed("")
            if not probe.startswith(FENCE):
                self._state = "done"
                out, self._held = self._held, ""
                return Fed(out)
            self._state = "inside"
        opened = self._held.find(FENCE)
        closed = self._held.find(FENCE, opened + len(FENCE))
        if closed == -1:
            if len(self._held) > MAX_BANNER:
                self._state = "done"
                out, self._held = self._held, ""
                return Fed(out)
            return Fed("", loading=True)
        rest = self._held[closed:].lstrip("━").lstrip()
        self._state = "done"
        self._held = ""
        self._skip_space = rest == ""
        return Fed(rest, loaded=True)


def _model_entry(entries: list[dict[str, str]], model_id: str) -> dict[str, str] | None:
    return next((e for e in entries if e.get("model") == model_id), None)


class LoadWatch:
    """Asks a one-model server whether the model a request waits for is being
    loaded, while nothing of the reply has come (`heard` not set).

    `on_loading()` when the server lists the model as starting, `on_loaded()`
    once it no longer does (only after an `on_loading`). Ends by itself on the
    first answer that is not llama-swap's, on `heard`, or when stopped."""

    def __init__(self, endpoint: Endpoint, model_id: str, on_loading, on_loaded) -> None:
        self.endpoint = endpoint
        self.model_id = model_id
        self.on_loading = on_loading
        self.on_loaded = on_loaded
        self.heard = asyncio.Event()
        self.loading = False
        self._task: asyncio.Task | None = None

    @staticmethod
    def applies(endpoint: Endpoint) -> bool:
        from lembas.services.llm import providers

        return providers.get(endpoint.provider_type).local and not endpoint.is_anthropic

    def start(self) -> LoadWatch:
        if self.applies(self.endpoint):
            self._task = asyncio.ensure_future(self._run())
        return self

    async def stop(self) -> None:
        self.heard.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    async def _run(self) -> None:
        wait = FIRST
        while not self.heard.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.heard.wait(), wait)
            if self.heard.is_set():
                return
            wait = EVERY
            try:
                entries = await health.running(self.endpoint, timeout=TIMEOUT)
            except Exception:  # a look that fails says nothing
                log.debug("could not read /running", exc_info=True)
                return
            if entries is None or self.heard.is_set():
                return  # not llama-swap: nothing to watch here
            entry = _model_entry(entries, self.model_id)
            starting = entry is not None and entry.get("state") in health.STARTING
            if starting and not self.loading:
                self.loading = True
                self.on_loading()
            elif self.loading and not starting:
                self.loading = False
                self.on_loaded()
                return


__all__ = ["FENCE", "Fed", "LoadWatch", "SwapBanner"]
