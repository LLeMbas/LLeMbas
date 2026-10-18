"""Querying the full-text indexes.

One helper for all three stores. The interesting part is turning what somebody
typed into something FTS5 will accept: its MATCH syntax has operators (`AND`,
`NEAR`, `*`, `^`, `:`) and a quoting rule, so a bare question mark or an
unbalanced quote is a syntax error rather than a search that finds nothing.

Every token is therefore quoted and the operators are dropped. That costs the
ability to type an FTS expression on purpose, which nobody was going to do, and
buys a search box that cannot be made to throw.

Search returns ids and leaves loading to the caller, which is what keeps the
visibility filter in one place: `services.sharing.visible_to` is applied to the
row query, not here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session as DBSession

log = logging.getLogger(__name__)

# Anything that is not a word character or an apostrophe is a separator. Keeps
# accented letters (\w is Unicode-aware here) and loses the operators.
_TOKENS = re.compile(r"[^\W_]+(?:'[^\W_]+)*", re.UNICODE)

MAX_TERMS = 24


@dataclass(frozen=True)
class SearchHit:
    id: str
    rank: float


def _terms(needle: str) -> list[str]:
    tokens = _TOKENS.findall(needle or "")[:MAX_TERMS]
    # Doubling any embedded quote is the FTS5 escape; tokens cannot contain one
    # after the regex above, but the rule is written out so it stays true if the
    # pattern is ever loosened.
    return ['"' + token.replace('"', '""') + '"' for token in tokens]


def fts_query(needle: str, *, operator: str = "AND") -> str:
    """Turn typed text into a safe FTS5 MATCH expression."""
    terms = _terms(needle)
    return f" {operator} ".join(terms) if terms else ""


def search_ids(
    db: DBSession, index: str, needle: str, *, limit: int = 20
) -> list[SearchHit]:
    """Ids matching `needle`, best first.

    `index` is a table name from db.migrations.FTS_INDEXES and never comes from
    a request -- it is interpolated because SQLite cannot parameterise an
    identifier, so it must stay that way.

    Every term is required first, then any of them. AND alone is right for a
    search box, where more words should narrow the result -- but the caller here
    is usually a *model*, which writes "who built the west gate of Moria and
    what is its password" rather than "moria gate". One word absent from the
    document then loses the match entirely. Falling back to OR keeps precision
    where it works and recall where it does not, and bm25 sorts the difference
    out: documents matching more terms rank higher anyway.
    """
    if not fts_query(needle):
        return []

    def run(query: str) -> list[SearchHit]:
        try:
            rows = db.execute(
                text(
                    f"SELECT id, bm25({index}) AS rank FROM {index} "  # see above
                    f"WHERE {index} MATCH :q ORDER BY rank LIMIT :limit"
                ),
                {"q": query, "limit": max(1, min(limit, 100))},
            ).fetchall()
        except Exception:  # a broken index must not break the page
            log.exception("full-text search failed on %s", index)
            # Rolled back because a failed statement leaves the session
            # unusable: without this, one broken search turns into every later
            # query in the same request failing too, which looks nothing like a
            # search problem.
            db.rollback()
            return []
        # bm25 returns a negative number, better matches being more negative.
        return [SearchHit(id=row[0], rank=float(row[1])) for row in rows]

    return run(fts_query(needle)) or run(fts_query(needle, operator="OR"))
