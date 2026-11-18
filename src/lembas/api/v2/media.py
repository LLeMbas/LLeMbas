"""Long generations, for an app: `GET /media/jobs`, one job, and its cancel.

A video, a track or a 3D model is a **job** (`services/media/jobs.py`). The
tool call that started it carries `media_job: {id, kind, state}` in the
message; this is where the app reads what has become of it, and where the
person stops one.

| Route | |
|---|---|
| `GET /media/jobs?chat_id=&active=` | `{jobs: [job]}`, newest first (at most 50) |
| `GET /media/jobs/{id}` | `job` |
| `DELETE /media/jobs/{id}` | cancel it → `job` (a job that has ended stays what it was) |

A `job`: `{id, kind, state, active, chat_id, message_id, source, prompt,
started, ended, deadline, seconds, error, result, cost, told}`. `state` is
`queued`, `running`, `done`, `failed`, `cancelled` or `expired`; `result` is
the typed result of the file once `done` (see `services/media/library.py`),
else null. Somebody else's job is a 404.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from lembas.api.deps import Db
from lembas.api.v2._common import Me
from lembas.services.media import jobs
from lembas.web.i18n import t

router = APIRouter()


@router.get("/media/jobs")
async def list_jobs(caller: Me, db: Db, chat_id: str = "", active: bool = False) -> dict[str, Any]:
    rows = jobs.listing(db, caller.user.id, chat_id=chat_id, active=active)
    return {"jobs": [jobs.view(row) for row in rows]}


def _missing() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, t("That job no longer exists."))


@router.get("/media/jobs/{job_id}")
async def one_job(caller: Me, db: Db, job_id: str) -> dict[str, Any]:
    row = jobs.owned(db, job_id, caller.user.id)
    if row is None:
        raise _missing()
    return jobs.view(row)


@router.delete("/media/jobs/{job_id}")
async def cancel_job(caller: Me, db: Db, job_id: str) -> dict[str, Any]:
    user_id = caller.user.id
    db.rollback()  # nothing is held while the far side is told
    found = await jobs.cancel(job_id, user_id)
    if found is None:
        raise _missing()
    return found
