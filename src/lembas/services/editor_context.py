"""What the person is looking at in an editor, said with a message.

Harness spec 3.11.0, `acp.md` *Editor context in a prompt*: a client that is
an editor -- or an app with one, like LLeMbas Desktop's workspace -- sends
`editor` beside a message: the file in front, the cursor, the selection, the
diagnostics, the other open files. It is kept on the person's message
(`Message.editor_json`) and reaches the model one of two ways:

- **A chat on a linked computer**: passed on with the prompt as
  `_meta.lembas.editor`, where the device has the capability `editor`. The
  device words the block.
- **A chat on this server**: `block` below words it -- the spec's block, the
  CLI's `editorBlock` rule for rule -- and it goes after the person's words in
  that turn, like a note. Never into the transcript: the message shown is
  what was typed.

**It is the client's word, and data to the model.** Nothing is read because
of it: a path in it is a name, and a model that wants the file reads it with
its tools, under the chat's access. A path is one line of plain text with no
angle bracket in it, and text of a file's own cannot close the block.

What a client sends is cut to shape before it is kept (`clean`): the keys the
spec names and nothing else, each bounded.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

# `loop.json` `editor.context` (spec 3.11.0); read from the vendored spec
# where it has them.
LIMITS = {"selection_max_chars": 20000, "diagnostics_max": 20, "diagnostic_max_chars": 300,
          "open_max": 30}
SEVERITIES = ("error", "warning", "info", "hint")

# What is kept of a client's object, beyond what the block will say.
KEEP_DIAGNOSTICS = 100
KEEP_OPEN = 200
KEEP_PATH = 1000
KEEP_SELECTION = 262144
KEEP_MESSAGE = 2000

_CONTROL = re.compile("[\u0000-\u001f\u007f  ]")
_CLOSING = re.compile(r"</(editor|selection)\s*>", re.IGNORECASE)


def limits() -> dict[str, int]:
    from lembas.services import harness_spec

    said = (harness_spec.loop().get("editor") or {})
    said = said.get("context") if isinstance(said, dict) else None
    return {**LIMITS, **(said if isinstance(said, dict) else {})}


def _line(value: Any) -> int | None:
    ok = isinstance(value, int) and not isinstance(value, bool) and value >= 1
    return value if ok else None


def _one_line(text: str) -> str:
    return _CONTROL.sub("", text)


def _said_path(value: Any, root: str) -> str | None:
    """A path as the block says it: one line, no angle brackets, relative to
    the project when inside it."""
    if not isinstance(value, str):
        return None
    clean_ = _one_line(value).replace("<", "").replace(">", "").strip()[:KEEP_PATH]
    if not clean_:
        return None
    if not clean_.startswith("/") or not root:
        return clean_
    relative = posixpath.relpath(posixpath.normpath(clean_), posixpath.normpath(root))
    inside = relative != "." and not relative.startswith("..") and not relative.startswith("/")
    return relative if inside else clean_


def _cannot_close(text: str) -> str:
    return _CLOSING.sub(lambda m: f"< /{m.group(1)}>", text)


def block(editor: Any, root: str = "", limits_: dict[str, int] | None = None) -> str | None:
    """The block a message's editor context becomes, or None for none
    (conformance `editor.json`, `kind: context`). Nothing is read for it."""
    if not isinstance(editor, dict):
        return None
    most = {**limits(), **(limits_ or {})}
    lines: list[str] = []
    path = _said_path(editor.get("path"), root)
    cursor = editor.get("cursor")
    at = _line(cursor.get("line")) if isinstance(cursor, dict) else None
    if path:
        lines.append(f"Open file: {path}" + (f" (cursor at line {at})" if at else ""))
    elif at:
        lines.append(f"Cursor: line {at}")
    selection = editor.get("selection")
    if isinstance(selection, dict):
        start, end = _line(selection.get("start")), _line(selection.get("end"))
        if start and end and end >= start:
            where = _said_path(selection.get("path"), root) or path
            span = f"line {start}" if start == end else f"lines {start}-{end}"
            lines.append(f"Selection: {where + ' ' if where else ''}{span}")
            text = selection.get("text")
            if isinstance(text, str) and text:
                if len(text) > most["selection_max_chars"]:
                    lines.append(
                        f"(the selection is {len(text)} characters long: read the file for it)"
                    )
                else:
                    body = _cannot_close(text[:-1] if text.endswith("\n") else text)
                    lines.append(f"<selection>\n{body}\n</selection>")
    raw = editor.get("diagnostics")
    diagnostics = [
        d for d in (raw if isinstance(raw, list) else [])
        if isinstance(d, dict) and isinstance(d.get("message"), str) and d["message"].strip()
    ]
    if diagnostics:
        lines.append("Diagnostics:")
        for item in diagnostics[: most["diagnostics_max"]]:
            file, line = _said_path(item.get("path"), root), _line(item.get("line"))
            where = (f"{file}:{line}" if line else file) if file else (
                f"line {line}" if line else ""
            )
            severity = item.get("severity") if item.get("severity") in SEVERITIES else ""
            head = " ".join(part for part in (where, severity) if part)
            message = _cannot_close(" ".join(item["message"].split()))
            if len(message) > most["diagnostic_max_chars"]:
                message = message[: most["diagnostic_max_chars"]] + "…"
            source = item.get("source")
            said = ""
            if isinstance(source, str) and source.strip():
                name = _one_line(source).replace("<", "").replace(">", "").strip()[:40]
                said = f" ({name})"
            lines.append(f"- {head + ': ' if head else ''}{message}{said}")
        if len(diagnostics) > most["diagnostics_max"]:
            lines.append(f"({len(diagnostics) - most['diagnostics_max']} more)")
    raw = editor.get("open")
    opened = [
        p for p in (_said_path(p, root) for p in (raw if isinstance(raw, list) else []))
        if p is not None and p != path
    ]
    if opened:
        more = len(opened) - most["open_max"]
        lines.append(
            "Other open files: " + ", ".join(opened[: most["open_max"]])
            + (f" ({more} more)" if more > 0 else "")
        )
    return "<editor>\n" + "\n".join(lines) + "\n</editor>" if lines else None


def clean(value: Any) -> dict[str, Any] | None:
    """A client's `editor` object as it is kept and passed on: the spec's
    keys, each of its type and bounded; None when nothing of it is left.
    Raises `ValueError` when it is not an object at all."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("`editor` is an object: {path, cursor, selection, diagnostics, open}.")
    out: dict[str, Any] = {}

    def path_of(raw: Any) -> str:
        return raw[:KEEP_PATH] if isinstance(raw, str) and raw.strip() else ""

    if path := path_of(value.get("path")):
        out["path"] = path
    cursor = value.get("cursor")
    if isinstance(cursor, dict) and (line := _line(cursor.get("line"))):
        out["cursor"] = {"line": line}
    selection = value.get("selection")
    if isinstance(selection, dict):
        start, end = _line(selection.get("start")), _line(selection.get("end"))
        if start and end and end >= start:
            kept: dict[str, Any] = {"start": start, "end": end}
            if path := path_of(selection.get("path")):
                kept["path"] = path
            if isinstance(selection.get("text"), str) and selection["text"]:
                kept["text"] = selection["text"][:KEEP_SELECTION]
            out["selection"] = kept
    diagnostics = []
    raw = value.get("diagnostics")
    for item in (raw if isinstance(raw, list) else [])[:KEEP_DIAGNOSTICS]:
        if not isinstance(item, dict) or not isinstance(item.get("message"), str):
            continue
        if not item["message"].strip():
            continue
        kept = {"message": item["message"][:KEEP_MESSAGE]}
        if path := path_of(item.get("path")):
            kept["path"] = path
        if line := _line(item.get("line")):
            kept["line"] = line
        if item.get("severity") in SEVERITIES:
            kept["severity"] = item["severity"]
        if isinstance(item.get("source"), str) and item["source"].strip():
            kept["source"] = item["source"][:40]
        diagnostics.append(kept)
    if diagnostics:
        out["diagnostics"] = diagnostics
    raw = value.get("open")
    opened = [p for p in (path_of(p) for p in (raw if isinstance(raw, list) else [])) if p]
    if opened:
        out["open"] = opened[:KEEP_OPEN]
    return out or None


__all__ = ["LIMITS", "block", "clean", "limits"]
