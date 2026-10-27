"""The `edit` tool's matching: OpenCode's replacer cascade, in Python.

A port of the LLeMbas CLI's `src/tool/replace.ts`, which is kept byte-for-byte
with OpenCode's `packages/opencode/src/tool/edit.ts` (MIT, (c) 2025 opencode),
plus the one fix the CLI keeps beside it (`replace-text.ts`): a `$` in the new
text is literal with `all`, which `str.replace` already is here. OpenCode credits
its approaches to cline (evals/diff-edits) and gemini-cli (editCorrector.ts).

The point of the cascade is an edit that lands when a model gets whitespace,
indentation or escaping slightly wrong: exact first, then trimmed lines, block
anchors, normalised whitespace, flexible indentation, escapes, trimmed
boundaries and context anchors -- and a match must be unique unless `all`.
Held to the same behaviour as the CLI by the spec's `edit` conformance cases
and the CLI's own unit cases (`tests/test_chat_files.py`).

JavaScript's `trim()` and `\\s` are Python's `strip()` and `\\s`; a string's
length counts code points here and UTF-16 units there, which can differ for a
character outside the BMP in the similarity score and nowhere else.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator

Replacer = Callable[[str, str], Iterator[str]]

SINGLE_CANDIDATE_SIMILARITY_THRESHOLD = 0.65
MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD = 0.65

IDENTICAL = "No changes to apply: oldString and newString are identical."
EMPTY = (
    "oldString cannot be empty when editing an existing file. Provide the exact text to "
    "replace, or use write for an intentional full-file replacement."
)
NOT_FOUND = (
    "Could not find oldString in the file. It must match exactly, including whitespace, "
    "indentation, and line endings."
)
MULTIPLE = (
    "Found multiple matches for oldString. Provide more surrounding context to make the "
    "match unique."
)
DISPROPORTIONATE = (
    "Refusing replacement because the matched span is much larger than oldString. Re-read "
    "the file and provide the full exact oldString for the intended replacement."
)


class ReplaceError(ValueError):
    pass


def levenshtein(a: str, b: str) -> int:
    if a == "" or b == "":
        return max(len(a), len(b))
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
        previous = current
    return previous[len(b)]


def _span(lines: list[str], start: int, end: int) -> tuple[int, int]:
    """Character offsets of lines start..end (inclusive) in the joined text."""
    begin = sum(len(lines[k]) + 1 for k in range(start))
    finish = begin
    for k in range(start, end + 1):
        finish += len(lines[k])
        if k < end:
            finish += 1
    return begin, finish


def simple(content: str, find: str) -> Iterator[str]:
    yield find


def line_trimmed(content: str, find: str) -> Iterator[str]:
    original = content.split("\n")
    search = find.split("\n")
    if search[-1] == "":
        search.pop()
    for i in range(len(original) - len(search) + 1):
        if all(original[i + j].strip() == search[j].strip() for j in range(len(search))):
            begin, finish = _span(original, i, i + len(search) - 1)
            yield content[begin:finish]


def _middle_similarity(original: list[str], search: list[str], start: int, size: int,
                       *, early: bool) -> float:
    block = len(search)
    to_check = min(block - 2, size - 2)
    if to_check <= 0:
        return 1.0
    similarity = 0.0
    j = 1
    while j < block - 1 and j < size - 1:
        o = original[start + j].strip()
        s = search[j].strip()
        longest = max(len(o), len(s))
        if longest:
            score = 1 - levenshtein(o, s) / longest
            if early:
                similarity += score / to_check
                if similarity >= SINGLE_CANDIDATE_SIMILARITY_THRESHOLD:
                    break
            else:
                similarity += score
        j += 1
    return similarity if early else similarity / to_check


def block_anchor(content: str, find: str) -> Iterator[str]:
    original = content.split("\n")
    search = find.split("\n")
    if len(search) < 3:
        return
    if search[-1] == "":
        search.pop()
    first = search[0].strip()
    last = search[-1].strip()
    size = len(search)
    max_delta = max(1, size // 4)
    candidates: list[tuple[int, int]] = []
    for i in range(len(original)):
        if original[i].strip() != first:
            continue
        for j in range(i + 2, len(original)):
            if original[j].strip() == last:
                if abs((j - i + 1) - size) <= max_delta:
                    candidates.append((i, j))
                break
    if not candidates:
        return
    if len(candidates) == 1:
        start, end = candidates[0]
        similarity = _middle_similarity(original, search, start, end - start + 1, early=True)
        if similarity >= SINGLE_CANDIDATE_SIMILARITY_THRESHOLD:
            begin, finish = _span(original, start, end)
            yield content[begin:finish]
        return
    best: tuple[int, int] | None = None
    best_score = -1.0
    for start, end in candidates:
        similarity = _middle_similarity(original, search, start, end - start + 1, early=False)
        if similarity > best_score:
            best_score = similarity
            best = (start, end)
    if best_score >= MULTIPLE_CANDIDATES_SIMILARITY_THRESHOLD and best is not None:
        begin, finish = _span(original, *best)
        yield content[begin:finish]


def _normalise_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def whitespace_normalized(content: str, find: str) -> Iterator[str]:
    wanted = _normalise_ws(find)
    lines = content.split("\n")
    for line in lines:
        if _normalise_ws(line) == wanted:
            yield line
        elif wanted in _normalise_ws(line):
            words = find.strip().split()
            if words:
                pattern = r"\s+".join(re.escape(word) for word in words)
                try:
                    match = re.search(pattern, line)
                except re.error:
                    match = None
                if match:
                    yield match.group(0)
    find_lines = find.split("\n")
    if len(find_lines) > 1:
        for i in range(len(lines) - len(find_lines) + 1):
            block = "\n".join(lines[i : i + len(find_lines)])
            if _normalise_ws(block) == wanted:
                yield block


def _remove_indentation(text: str) -> str:
    lines = text.split("\n")
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return text
    # JavaScript's `^(\s*)` counts every leading whitespace character.
    least = min(len(re.match(r"^(\s*)", line).group(1)) for line in non_empty)
    return "\n".join(line if not line.strip() else line[least:] for line in lines)


def indentation_flexible(content: str, find: str) -> Iterator[str]:
    wanted = _remove_indentation(find)
    lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i : i + len(find_lines)])
        if _remove_indentation(block) == wanted:
            yield block


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"', "`": "`", "\\": "\\",
            "\n": "\n", "$": "$"}


def _unescape(text: str) -> str:
    return re.sub(r"\\(n|t|r|'|\"|`|\\|\n|\$)", lambda m: _ESCAPES[m.group(1)], text)


def escape_normalized(content: str, find: str) -> Iterator[str]:
    unescaped = _unescape(find)
    if unescaped in content:
        yield unescaped
    lines = content.split("\n")
    find_lines = unescaped.split("\n")
    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i : i + len(find_lines)])
        if _unescape(block) == unescaped:
            yield block


def multi_occurrence(content: str, find: str) -> Iterator[str]:
    start = 0
    while True:
        index = content.find(find, start)
        if index == -1:
            break
        yield find
        start = index + len(find)


def trimmed_boundary(content: str, find: str) -> Iterator[str]:
    trimmed = find.strip()
    if trimmed == find:
        return
    if trimmed in content:
        yield trimmed
    lines = content.split("\n")
    find_lines = find.split("\n")
    for i in range(len(lines) - len(find_lines) + 1):
        block = "\n".join(lines[i : i + len(find_lines)])
        if block.strip() == trimmed:
            yield block


def context_aware(content: str, find: str) -> Iterator[str]:
    find_lines = find.split("\n")
    if len(find_lines) < 3:
        return
    if find_lines[-1] == "":
        find_lines.pop()
    lines = content.split("\n")
    first = find_lines[0].strip()
    last = find_lines[-1].strip()
    for i in range(len(lines)):
        if lines[i].strip() != first:
            continue
        for j in range(i + 2, len(lines)):
            if lines[j].strip() == last:
                block_lines = lines[i : j + 1]
                if len(block_lines) == len(find_lines):
                    matching = total = 0
                    for k in range(1, len(block_lines) - 1):
                        b = block_lines[k].strip()
                        f = find_lines[k].strip()
                        if b or f:
                            total += 1
                            if b == f:
                                matching += 1
                    if total == 0 or matching / total >= 0.5:
                        yield "\n".join(block_lines)
                        break
                break


CASCADE: tuple[Replacer, ...] = (
    simple,
    line_trimmed,
    block_anchor,
    whitespace_normalized,
    indentation_flexible,
    escape_normalized,
    trimmed_boundary,
    context_aware,
    multi_occurrence,
)


def _disproportionate(search: str, old: str) -> bool:
    old_lines = len(old.split("\n"))
    search_lines = len(search.split("\n"))
    if search_lines >= max(old_lines + 3, old_lines * 2):
        return True
    if old_lines == 1:
        return False
    return len(search.strip()) > max(len(old.strip()) + 500, len(old.strip()) * 4)


def replace(content: str, old: str, new: str, replace_all: bool = False) -> str:
    """`content` with `old` replaced by `new`, or `ReplaceError` with the
    sentence the model is told."""
    if old == new:
        raise ReplaceError(IDENTICAL)
    if old == "":
        raise ReplaceError(EMPTY)
    not_found = True
    for replacer in CASCADE:
        for search in replacer(content, old):
            index = content.find(search)
            if index == -1:
                continue
            not_found = False
            if _disproportionate(search, old):
                raise ReplaceError(DISPROPORTIONATE)
            if replace_all:
                return content.replace(search, new)
            if index != content.rfind(search):
                continue
            return content[:index] + new + content[index + len(search) :]
    raise ReplaceError(NOT_FOUND if not_found else MULTIPLE)


__all__ = ["CASCADE", "ReplaceError", "levenshtein", "replace"]
