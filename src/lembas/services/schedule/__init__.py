"""Scheduling: what should happen later, and what makes it happen.

Four modules, split by what each of them is allowed to touch:

- `clock.py`   -- whose idea of "now" is in force. No session, no rows.
- `rule.py`    -- the recurrence spec, and when it next comes due. Pure and
                  total: it never raises, never opens a session and never reads
                  the wall clock, which is what lets it be tested exhaustively
                  before anything calls it.
- `ticker.py`  -- the loop that notices a schedule is due, and claims it.
- `runner.py`  -- what actually happens when one fires.

The order matters and is the phasing: everything upstream of `ticker.py` can be
got wrong quietly, so it is settled first.
"""

from __future__ import annotations
