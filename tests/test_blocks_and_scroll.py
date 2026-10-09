"""Two reading glitches in a streamed transcript.

A block that would not close: steps.js put back the open blocks it had written
down at the last stream frame after *every* swap on the page, so once a reply
had finished the unread poll reopened a block the reader had just closed. It now
writes the state down when the reader toggles a block.

A page that dragged the reader down: app.js followed the reply after every swap
anywhere -- the sidebar's poll included -- and only stopped following beyond 120
px from the bottom. It now follows only swaps inside the transcript, and any
scroll up by the reader stops it.

Asserted on the source, as the other listeners in these files are: the failure
both times was a listener in the wrong place, which a DOM stub shows no better.
"""

from __future__ import annotations

import re
from pathlib import Path

import lembas

JS = Path(lembas.__file__).parent / "web/static/js"


def test_a_toggled_block_is_written_down_at_once():
    source = (JS / "steps.js").read_text()
    listener = re.search(r'addEventListener\(\s*"toggle",(.*?)\n  \);', source, re.S)
    assert listener, "steps.js must listen for toggle"
    body = listener.group(1)
    assert "remember(box)" in body
    # toggle does not bubble: without capture the listener never runs.
    assert body.rstrip().endswith("true")


def test_only_the_transcript_moves_the_transcript():
    source = (JS / "app.js").read_text()
    follow = source.split("re-measure the composer, and follow new content", 1)[1][:900]
    assert "thread.contains(target)" in follow
    assert "scrollThread(false)" in follow


def test_scrolling_up_stops_following():
    source = (JS / "app.js").read_text()
    assert "if (up) stick = false;" in source
    assert "else if (isNearBottom(thread)) stick = true;" in source
