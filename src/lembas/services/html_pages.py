"""HTML pages in answers (harness spec 3.8.0, `pages.md`): the text side.

A reply stays Markdown. Where pages are switched on, a model may put a whole
HTML document into a fenced block whose info word is `html-view`, with the
page's title after it. This module is everything that reads such a reply *as
text*: it finds the finished blocks, and gives the reply's **projection** --
each page replaced by one line, `[page: Title]` -- which is what search,
titles, summaries, notifications, read-aloud and a client that cannot draw a
page see instead of a screenful of markup.

Pure, and held to `conformance/pages.json` (`tests/test_harness_spec.py`).
Drawing a page is another matter entirely (a sealed frame; see the wiki's
security page) and is not here.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from lembas.services import harness_spec


def spec() -> dict[str, Any]:
    return harness_spec.loop()["pages"]


@dataclass(frozen=True)
class Page:
    title: str
    html: str

    @property
    def digest(self) -> str:
        """What a page is found by: a hash of its title and its source."""
        return hashlib.sha256(f"{self.title}\n{self.html}".encode()).hexdigest()[:32]


_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(\S*)[ \t]*(.*?)[ \t]*$")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.I | re.S)


def _title(said: str, html: str) -> str:
    shape = spec()
    title = " ".join(said.split())
    if not title:
        found = _TITLE.search(html)
        title = " ".join(found.group(1).split()) if found else ""
    title = title or shape["untitled"]
    return title[: int(shape["title_max"])].rstrip()


def fence(text: str) -> tuple[list[Page], str]:
    """`(pages, projection)` of a reply: every **closed** `html-view` block in
    order, and the reply with each replaced by its `[page: title]` line. A
    block still open (the reply is being written, or was cut off) is not a
    page and stays as it is, as does every other fenced block."""
    shape = spec()
    word = shape["fence"]
    lines = (text or "").split("\n")
    pages: list[Page] = []
    out: list[str] = []
    index = 0
    while index < len(lines):
        opened = _OPEN.match(lines[index])
        if not opened:
            out.append(lines[index])
            index += 1
            continue
        marker, info, rest = opened.group(1), opened.group(2), opened.group(3)
        # The block's end: a line of the same character, at least as long,
        # with nothing after it.
        end = None
        for later in range(index + 1, len(lines)):
            closing = lines[later].strip()
            if (
                closing
                and set(closing) == {marker[0]}
                and len(closing) >= len(marker)
                and len(lines[later]) - len(lines[later].lstrip(" ")) <= 3
            ):
                end = later
                break
        if end is None:
            out.extend(lines[index:])
            break
        if info == word:
            html = "\n".join(lines[index + 1 : end])
            page = Page(_title(rest, html), html)
            pages.append(page)
            out.append(shape["projection"].replace("{{title}}", page.title))
        else:
            out.extend(lines[index : end + 1])
        index = end + 1
    return pages, "\n".join(out)


def project(text: str) -> str:
    """A reply as text: its pages as one line each."""
    if not text or spec()["fence"] not in text:
        return text or ""
    return fence(text)[1]


def pages_of(text: str) -> list[Page]:
    if not text or spec()["fence"] not in text:
        return []
    return fence(text)[0]


def note(title: str) -> str:
    """What a client that cannot draw a page shows in its place."""
    return spec()["note"].replace("{{title}}", title)


def offered(*, instance: bool, model: bool, chat: bool, where: str = "") -> bool:
    """Whether the pages block is in a prompt: all three switches on -- the
    instance's, the model's flag, the chat's -- and never for a subagent, the
    phone assistant or an agent chat. A schedule's run has no chat switch: the
    instance's and the model's decide."""
    if where in spec()["never"]:
        return False
    if not (instance and model):
        return False
    return True if where == "scheduled" else bool(chat)


__all__ = ["Page", "fence", "note", "offered", "pages_of", "project", "spec"]
