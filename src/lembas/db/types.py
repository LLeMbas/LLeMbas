"""Reusable column types.

SQLite stores JSON as text. Wrapping the JSON type in SQLAlchemy's mutation
tracking means ``obj.settings_json["theme"] = "shire"`` marks the row dirty --
without it, in-place edits of a dict column are silently dropped on flush.
"""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.ext.mutable import MutableDict, MutableList

JSONDict = MutableDict.as_mutable(JSON)
JSONList = MutableList.as_mutable(JSON)
