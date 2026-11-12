"""Which part of a conversation a page draws.

A chat page used to carry every message of its chat: three hundred bubbles,
twelve thousand elements, each one rendered through Markdown and the
highlighter on every load and then restyled by the browser at every keystroke.
It draws the newest `PAGE` now, and the ones before them are fetched a page
at a time when the reader goes up to them (`GET /api/chats/{id}/earlier`).

The rows are still all read: knowing which messages were taken into a reply
(`inline_steers`) needs every reply's marks, and reading rows is not what was
slow -- rendering them was.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The same page the apps ask `/api/v2` for.
PAGE = 50

# How many messages before the one asked for (`at`) are drawn with it, so it
# is not the first thing on the screen with nothing said before it.
LEAD = 2


@dataclass(frozen=True)
class Window:
    """The messages a page draws, and what is above them."""

    messages: list[Any]
    # The id of the first message drawn, when there are earlier ones to fetch
    # before it; "" when the window starts at the first message.
    earlier: str = ""
    # How many messages are above the window.
    above: int = 0


def window(
    messages: list[Any], *, before: str = "", at: str = "", size: int = PAGE
) -> Window:
    """The page of `messages` (oldest first) to draw.

    By default the newest `size`. `before`: the `size` messages just above
    that message -- the next page up. `at`: from a little before that message
    to the end, however many that is, so a link to one message always shows
    it with everything after it in place. An `at` that is not in the list is
    ignored: the default page is the honest answer to a stale link. A `before`
    that is not in the list has no page above it: the answer is empty.
    """
    count = len(messages)
    index = {message.id: position for position, message in enumerate(messages)}
    end = count
    if before:
        # The page above a message that is not in the list (summarised since,
        # or taken into a reply) is nothing -- never the newest page again,
        # which the asking page already holds: every bubble twice, and a
        # second stream opened on the reply being written.
        if before not in index:
            return Window(messages=[])
        end = index[before]
        start = max(0, end - size)
    elif at and at in index:
        start = max(0, min(index[at] - LEAD, count - size))
    else:
        start = max(0, count - size)
    shown = messages[start:end]
    return Window(
        messages=shown,
        earlier=shown[0].id if shown and start > 0 else "",
        above=start,
    )


__all__ = ["LEAD", "PAGE", "Window", "window"]
