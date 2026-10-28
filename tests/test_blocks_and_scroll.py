"""Reading glitches in a streamed transcript, and the scroll that chased them.

A block that would not close: steps.js put back the open blocks it had written
down at the last stream frame after *every* swap on the page, so once a reply
had finished the unread poll reopened a block the reader had just closed. It now
writes the state down when the reader toggles a block.

A page that dragged the reader down: app.js followed the reply after every swap
anywhere -- the sidebar's poll included -- and only stopped following beyond 120
px from the bottom. It now follows only swaps inside the transcript, and any
scroll up by the reader stops it.

A page that jumped up and down while a reply streamed. Several causes, pinned
here one by one: `scroll-behavior: smooth` on the scroller made every
programmatic scroll an animation whose start was recorded as its end; the
follow ran synchronously in every swap and stream listener, before the
MutationObservers that add a label bar to each code block and after the
browser's own clamp-and-anchor scrolls; a round closed in two frames with the
text on screen twice between them; and every frame was re-sent whether or not
it had changed, so the bubble was laid out seven times a bump. The follower
now sends a frame only when its content moved, closes a round in one frame,
and the page scrolls once per animation frame from one owner.

Asserted on the source, as the other listeners in these files are: the failure
every time was a listener in the wrong place or at the wrong time, which a DOM
stub shows no better. The follower is driven for real, with a fake generation.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import lembas
from lembas.services import generation as generation_service

JS = Path(lembas.__file__).parent / "web/static/js"
CSS = Path(lembas.__file__).parent / "web/static/css"

APP = (JS / "app.js").read_text()
STEPS = (JS / "steps.js").read_text()
CHAT_CSS = (CSS / "chat.css").read_text()


def _rule(css: str, selector: str) -> str:
    start = css.index(selector + " {")
    return css[start : css.index("}", start)]


def _code(source: str) -> str:
    return re.sub(r"/\*.*?\*/", "", source, flags=re.S)


# --- The reader's blocks -------------------------------------------------------
def test_a_toggled_block_is_written_down_at_once():
    listener = re.search(r'addEventListener\(\s*"toggle",(.*?)\n  \);', STEPS, re.S)
    assert listener, "steps.js must listen for toggle"
    body = listener.group(1)
    assert "remember(box)" in body
    # toggle does not bubble: without capture the listener never runs.
    assert body.rstrip().endswith("true")


def test_only_the_reader_s_own_toggle_stops_following():
    """steps.js opens blocks too, and a programmatic `open = true` fires a
    `toggle` that is `isTrusted` exactly like a click's (measured in Chromium).
    So every open steps.js performs is marked first, and app.js consumes the
    mark rather than unsticking -- or the first round to close after somebody
    opened a tool block put back its copy and stopped following for good."""
    listener = re.search(r'"toggle",\s*function \(event\) \{([\s\S]*?)\n    \},\n    true', APP)
    assert listener, "the toggle listener is gone"
    body = listener.group(1)
    assert "event.isTrusted" in body
    assert 'hasAttribute("data-restored")' in body
    assert 'removeAttribute("data-restored")' in body
    assert "if (el.open) stick = false;" in body
    # And the mark is set before the open, by the only code that opens blocks.
    show = re.search(r"function show\(details\) \{([\s\S]*?)\n  \}", STEPS)
    assert show, "steps.js must open blocks through one function"
    assert show.group(1).index('setAttribute("data-restored"') < show.group(1).index("open = true")
    assert STEPS.count("open = true") == 1


def test_the_live_thinking_box_s_state_carries_onto_the_block_it_became():
    assert "liveOpen" in STEPS
    assert '"details.reasoning"' in STEPS


# --- Following the reply -------------------------------------------------------
def test_only_the_transcript_moves_the_transcript():
    follow = APP.split("only content that arrived in the transcript", 1)
    assert len(follow) == 2, "the afterSwap listener's comment moved; update this anchor"
    assert "thread.contains(target)" in follow[1][:1200]
    assert "scrollThread(false)" in follow[1][:1200]


def test_scrolling_up_stops_following():
    assert "if (up) stick = false;" in APP
    assert "else if (isNearBottom(thread)) stick = true;" in APP
    # The flag that swallowed the next real scroll up after a block closed.
    assert "resized" not in _code(APP)


def test_one_scroll_owner_one_scroll_a_frame():
    """`scrollThread` grants the bottom once, in a requestAnimationFrame, after
    the MutationObservers have inserted what they insert and in the same layout
    as any clamp -- see the comment above it in app.js."""
    found = re.search(r"function scrollThread\(force\) \{([\s\S]*?)\n  \}", APP)
    assert found, "scrollThread is gone"
    body = found.group(1)
    assert "requestAnimationFrame" in body
    assert "thread.scrollTop = thread.scrollHeight" in body
    assert "placed = thread.scrollTop" in body
    # Coalesced: a second request while one is pending is not a second scroll.
    assert "if (scrollFrame) return;" in body


def test_every_path_into_the_transcript_asks_the_same_owner():
    code = _code(APP)
    sse = re.search(r'htmx:sseMessage", function \(event\) \{([\s\S]*?)\n  \}\);', code)
    assert sse and "scrollThread(false)" in sse.group(1)
    after = re.search(r'htmx:afterSwap", function \(event\) \{([\s\S]*?)\n  \}\);', code)
    assert after and "scrollThread(false)" in after.group(1)


def test_no_swap_autosizes_every_textarea_on_the_page():
    """Measuring a textarea sets its height twice and forces a layout; doing it
    for the composer on every stream frame that swapped something else was a
    reflow of the whole shell twelve times a second."""
    after = re.search(r'htmx:afterSwap", function \(event\) \{([\s\S]*?)\n  \}\);', _code(APP))
    assert after, "the afterSwap listener is gone"
    assert 'document.querySelectorAll("[data-autosize]")' not in after.group(1)
    assert 'target.querySelectorAll("[data-autosize]")' in after.group(1)


def test_the_history_sentinel_is_gone():
    """No template has had one for some time; the block that compensated for
    its prepends ran on every swap and set `placed` for nothing."""
    assert "history-sentinel" not in APP
    assert "history-sentinel" not in CHAT_CSS


def test_the_scroller_neither_animates_nor_anchors():
    rule = _rule(CHAT_CSS, ".thread-scroll")
    assert "scroll-behavior" not in rule
    assert "overflow-anchor: none" in rule
    # Not sneaked back in elsewhere either, outside the reduced-motion reset.
    code = re.sub(r"/\*.*?\*/", "", CHAT_CSS, flags=re.S)
    assert "scroll-behavior: smooth" not in code


# --- The bubble's rows keep their height ----------------------------------------
def test_the_live_rows_never_change_height():
    """The waiting row and the metrics row are fixed slots while the reply
    streams: content arriving in them, or leaving, moves nothing."""
    waiting = _rule(CHAT_CSS, ".msg__waiting")
    assert "min-height: var(--row-h)" in waiting
    metrics = _rule(CHAT_CSS, ".msg__metrics")
    assert "white-space: nowrap" in metrics
    assert "overflow: hidden" in metrics
    assert "text-overflow: ellipsis" in metrics
    assert "flex-wrap" not in metrics
    live = _rule(CHAT_CSS, ".msg__metrics--live")
    assert "min-height: var(--row-h)" in live
    # The dots are "nothing has arrived yet" and do not come back each round.
    assert ".msg__main:has(.msg__steps:not(:empty)) .msg__waiting .dots" in CHAT_CSS


def test_the_caret_is_drawn_inside_text_not_after_a_block():
    code = re.sub(r"/\*.*?\*/", "", CHAT_CSS, flags=re.S)
    assert ".msg__body--live:not(:empty) > :last-child::after" not in code
    assert ".msg__body--live > :is(p, h1, h2, h3, h4, h5, h6):last-child::after" in code


def test_the_live_and_closed_steps_share_one_column():
    message = (Path(lembas.__file__).parent / "web/templates/chat/_message.html").read_text()
    assert message.count('<div class="msg__flow">') == 1
    assert "gap: var(--sp-2)" in _rule(CHAT_CSS, ".msg__flow")
    assert "gap: var(--sp-2)" in _rule(CHAT_CSS, ".msg__steps")


# --- The follower ---------------------------------------------------------------
def _frames(text: str) -> list[tuple[str, str]]:
    """`(event, data)` for every frame in a stretch of the stream."""
    out = []
    for frame in text.split("\n\n"):
        if not frame.startswith("event: "):
            continue
        name, _, rest = frame.partition("\n")
        data = "\n".join(line[6:] for line in rest.splitlines() if line.startswith("data: "))
        out.append((name[7:], data))
    return out


async def _drive(monkeypatch, script):
    """Run `_follow` against a fake generation.

    `script(generation, settle, chunks)` moves the generation; `settle()` waits
    long enough for the follower to have polled and written whatever it had to
    say into `chunks`, which is the stream so far -- so a script can note where
    in it something happened. Returns the whole stream, frame by frame.
    """
    from lembas.api import chats as chats_api

    generation = generation_service.Generation(chat_id="chat", message_id="reply")
    monkeypatch.setattr(generation_service, "ensure", lambda *_a, **_k: generation)
    monkeypatch.setattr(generation_service, "RENDER_INTERVAL", 0.01)
    monkeypatch.setattr(chats_api, "KEEPALIVE_AFTER", 1e9)
    monkeypatch.setattr(chats_api, "METRICS_INTERVAL", 1e9)
    chunks: list[str] = []

    async def pump():
        async for chunk in chats_api._follow("chat", "reply"):
            chunks.append(chunk)

    task = asyncio.create_task(pump())

    async def settle():
        await asyncio.sleep(0.08)

    await settle()
    await script(generation, settle, chunks)
    generation.done = True
    await asyncio.wait_for(task, 5)
    return chunks


async def test_a_frame_is_sent_only_when_its_content_moved(monkeypatch):
    """Every version bump used to re-send `reasoning`, `think`, `render`,
    `status` and `ask` whether or not anything in them had moved: seven swaps a
    bump, each one a layout and a scroll. A bump that changes nothing the
    bubble shows now sends nothing to it."""
    at: dict[str, int] = {}

    async def script(generation, settle, chunks):
        generation.content.append("Hello")
        generation.touch()
        await settle()
        at["text"] = len(chunks)
        for _ in range(3):
            generation.touch()
            await settle()
        at["idle"] = len(chunks)

    chunks = await _drive(monkeypatch, script)
    first = [n for n, _ in _frames("".join(chunks[: at["text"]]))]
    assert first.count("render") == 1
    idle = [n for n, _ in _frames("".join(chunks[at["text"] : at["idle"]]))]
    assert not [n for n in idle if n in ("render", "reasoning", "think", "status", "ask")], idle


async def test_a_round_closes_in_one_frame(monkeypatch):
    """The `steps` frame carries the emptied tail out of band, aimed at the
    tail's own ids, so the text is never on screen in its new step and in the
    tail at once -- and no separate `render` follows to empty it."""

    async def script(generation, settle, chunks):
        generation.content.append("First paragraph.")
        generation.touch()
        await settle()
        generation.close_step()
        generation.touch()
        await settle()

    frames = _frames("".join(await _drive(monkeypatch, script)))
    names = [n for n, _ in frames]
    assert names.count("steps") == 1
    steps = next(d for n, d in frames if n == "steps")
    assert "First paragraph." in steps
    assert 'id="stream-reply" hx-swap-oob="innerHTML"' in steps
    assert 'id="reasoning-body-reply" hx-swap-oob="innerHTML"' in steps
    assert 'id="think-stats-reply" hx-swap-oob="innerHTML"' in steps
    # One `render` -- the paragraph arriving -- and nothing after the close.
    assert names.count("render") == 1
    assert "render" not in names[names.index("steps") + 1 :]


async def test_a_late_follower_still_gets_the_whole_state(monkeypatch):
    """`sent` starts empty for every follower, so the first pass carries
    everything there is, however long the reply has been running."""

    async def script(generation, settle, chunks):
        generation.content.append("Already here.")
        generation.reasoning.append("thought")
        generation.touch()
        await settle()

    frames = _frames("".join(await _drive(monkeypatch, script)))
    assert any(n == "render" and "Already here." in d for n, d in frames)
    assert any(n == "reasoning" and "thought" in d for n, d in frames)
    assert frames[-1][0] == "close"
