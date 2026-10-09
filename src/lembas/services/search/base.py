"""What every search provider produces, and how it fails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

# A snippet is context, not an article. Longer than this and a handful of
# results crowds out the conversation they were meant to inform.
MAX_SNIPPET = 400


class SearchError(Exception):
    """A search failure with a message fit to show a user.

    Same contract as LLMError in the chat client: one exception type, always
    carrying text that can be put on screen without editing.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str

    @property
    def host(self) -> str:
        try:
            return urlparse(self.url).netloc or self.url
        except ValueError:
            return self.url

    @property
    def is_linkable(self) -> bool:
        """Whether this result's URL may be rendered as a link.

        Only http and https. A search provider is an untrusted source, and a
        javascript: or data: URL arriving in a result and being turned into an
        anchor is the obvious way this feature would be abused.
        """
        try:
            return urlparse(self.url).scheme in ("http", "https")
        except ValueError:
            return False


def clean(title: Any, url: Any, snippet: Any) -> SearchResult | None:
    """Normalise one provider's row, or None if there is nothing usable in it."""
    url = str(url or "").strip()
    if not url:
        return None
    return SearchResult(
        title=" ".join(str(title or "").split())[:300] or url,
        url=url[:2000],
        snippet=" ".join(str(snippet or "").split())[:MAX_SNIPPET],
    )
