"""A Jupyter notebook (.ipynb), read for looking at: never run, never rendered.

A notebook is JSON that somebody else wrote -- a model, a download -- and its
outputs may hold HTML and script meant to run in a notebook application. None
of that is honoured here. What comes out is plain data for a template to
escape: each cell's source as text, each output as text or as a raster
picture. An HTML, SVG, script or widget output is named and not drawn; a
Markdown cell is shown as the text it was written as.

Bounded everywhere, because a notebook is a document and not a workload.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_CELLS = 500
MAX_SOURCE_CHARS = 40_000
MAX_OUTPUTS = 20
MAX_OUTPUT_CHARS = 20_000
MAX_IMAGES = 40
MAX_IMAGE_CHARS = 3_000_000  # of base64, about 2 MB of picture

# Raster only: a browser decodes these and never runs them. Not SVG.
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
_BASE64 = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# What a notebook application would run or lay out; here it is only named.
_NOT_DRAWN = ("text/html", "image/svg+xml", "application/javascript", "text/latex",
              "application/vnd.jupyter.widget-view+json")


@dataclass
class Output:
    kind: str  # "text", "error", "image", "skipped"
    text: str = ""
    src: str = ""  # a data: address of a raster picture, built here
    what: str = ""  # for "skipped": the type that was not drawn


@dataclass
class Cell:
    kind: str  # "code", "markdown", "raw"
    source: str
    count: str = ""
    outputs: list[Output] = field(default_factory=list)
    cut: bool = False


@dataclass
class Notebook:
    cells: list[Cell] = field(default_factory=list)
    language: str = ""
    more: int = 0  # cells past the cap
    problem: bool = False  # not a notebook this can read


def _text(value: Any) -> str:
    """A notebook's text field: a string, or a list of lines."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(part for part in value if isinstance(part, str))
    return ""


def _cut(text: str, limit: int) -> tuple[str, bool]:
    return (text[:limit], True) if len(text) > limit else (text, False)


def _outputs(raw: Any, budget: list[int]) -> list[Output]:
    out: list[Output] = []
    for entry in raw if isinstance(raw, list) else []:
        if len(out) >= MAX_OUTPUTS or not isinstance(entry, dict):
            break
        kind = entry.get("output_type")
        if kind == "stream":
            out.append(Output("text", _cut(_ANSI.sub("", _text(entry.get("text"))),
                                           MAX_OUTPUT_CHARS)[0]))
            continue
        if kind == "error":
            trace = entry.get("traceback")
            text = "\n".join(t for t in trace if isinstance(t, str)) if isinstance(
                trace, list) else ""
            text = text or f"{_text(entry.get('ename'))}: {_text(entry.get('evalue'))}"
            out.append(Output("error", _cut(_ANSI.sub("", text), MAX_OUTPUT_CHARS)[0]))
            continue
        data = entry.get("data")
        if kind not in ("execute_result", "display_data") or not isinstance(data, dict):
            continue
        drawn = False
        for mime in IMAGE_TYPES:
            encoded = "".join(_text(data.get(mime)).split())
            if (
                encoded
                and budget[0] > 0
                and len(encoded) <= MAX_IMAGE_CHARS
                and _BASE64.match(encoded)
            ):
                budget[0] -= 1
                out.append(Output("image", src=f"data:{mime};base64,{encoded}"))
                drawn = True
                break
        if drawn:
            continue
        plain = _text(data.get("text/plain"))
        if plain:
            out.append(Output("text", _cut(_ANSI.sub("", plain), MAX_OUTPUT_CHARS)[0]))
            continue
        named = next((m for m in _NOT_DRAWN if m in data), "") or next(
            (str(m)[:80] for m in data if isinstance(m, str)), ""
        )
        if named:
            out.append(Output("skipped", what=named))
    return out


def parse(text: str) -> Notebook:
    """The notebook in `text`. One that cannot be read says so (`problem`)
    and has no cells: its page shows it as the text it is."""
    try:
        raw = json.loads(text)
    except ValueError:
        return Notebook(problem=True)
    cells = raw.get("cells") if isinstance(raw, dict) else None
    if not isinstance(cells, list):
        return Notebook(problem=True)
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    info = meta.get("language_info") if isinstance(meta.get("language_info"), dict) else {}
    spec = meta.get("kernelspec") if isinstance(meta.get("kernelspec"), dict) else {}
    language = str(info.get("name") or spec.get("language") or "")[:24]
    if not re.fullmatch(r"[A-Za-z0-9_+#.-]*", language):
        language = ""

    book = Notebook(language=language.lower(), more=max(0, len(cells) - MAX_CELLS))
    budget = [MAX_IMAGES]
    for entry in cells[:MAX_CELLS]:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("cell_type")
        kind = kind if kind in ("code", "markdown", "raw") else "raw"
        source, cut = _cut(_text(entry.get("source")), MAX_SOURCE_CHARS)
        count = entry.get("execution_count")
        book.cells.append(
            Cell(
                kind=kind,
                source=source,
                count=str(count) if isinstance(count, int) else "",
                outputs=_outputs(entry.get("outputs"), budget) if kind == "code" else [],
                cut=cut,
            )
        )
    return book


__all__ = ["Cell", "Notebook", "Output", "parse"]
