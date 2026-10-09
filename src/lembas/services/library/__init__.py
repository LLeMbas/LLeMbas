"""The four stores the model can reach for.

Knowledge, notes and skills are searched; memory is small enough to be handed
over whole. Everything here answers to one visibility rule -- see
``services.sharing`` -- and nothing here queries a table without it.
"""

from lembas.services.library.fts import SearchHit, fts_query, search_ids
from lembas.services.library.memories import MAX_MEMORY_CHARS
from lembas.services.library.skills import SKILL_NAME_PATTERN

__all__ = [
    "MAX_MEMORY_CHARS",
    "SKILL_NAME_PATTERN",
    "SearchHit",
    "fts_query",
    "search_ids",
]
