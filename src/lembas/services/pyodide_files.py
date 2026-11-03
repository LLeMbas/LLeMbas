"""Pyodide on this server: fetched once by an administrator, checked, kept in the data directory.

Pyodide (CPython in WebAssembly) is what the `python` tool runs in, in the
person's own browser (`api/pyodide.py`). It is about 13 MB, so it is not
committed with the other browser libraries: an administrator fetches it from
Admin → General (or `lembas-server fetch-pyodide`), once, from one fixed
address, and **every file is checked against `pyodide.lock.json`**, which is
committed beside this package. A file whose size or SHA-256 differs is refused
and nothing is kept, exactly as `scripts/fetch_vendor.py` refuses a library.

What is fetched is the least the runner needs: the loader, the WebAssembly
module and its JavaScript half, the standard library and Pyodide's own lock
(which it reads as it starts). No package repository -- `micropip` and
`loadPackage` have nothing to install from, which is the point.

The files go to `<data>/vendor/pyodide/<version>/` and are served from
`/vendor/pyodide/<version>/` by this server (`api/pyodide.py`); nothing is
loaded from anywhere else at runtime (hard rule 2). A download goes into a
directory of its own first and is moved into place only once every file
verified, so a half-finished or refused one is never what is served.
`VERIFIED` beside the files records the lock they were checked against.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import shutil
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from lembas.config import settings
from lembas.web.i18n import t

log = logging.getLogger(__name__)

LOCKFILE = Path(__file__).resolve().parent.parent / "pyodide.lock.json"
MARKER = "VERIFIED"

# What a downloader is: given a URL, the bytes of the answer, in parts. The
# real one is httpx; a test hands in its own and never reaches the network.
Downloader = Callable[[str], AsyncIterator[bytes]]


class PyodideError(Exception):
    """A download that was refused or failed, with a message fit to show."""


@lru_cache(maxsize=1)
def lock() -> dict:
    return json.loads(LOCKFILE.read_text(encoding="utf-8"))


def version() -> str:
    return str(lock()["version"])


def files() -> dict[str, dict]:
    return dict(lock()["files"])


def total_size() -> int:
    return sum(int(entry["size"]) for entry in files().values())


def base_url() -> str:
    return str(lock()["url"])


def root() -> Path:
    return settings.data_dir / "vendor" / "pyodide"


def directory() -> Path:
    return root() / version()


def _marker_text() -> str:
    return json.dumps({name: entry["sha256"] for name, entry in sorted(files().items())})


def available() -> bool:
    """Whether the pinned version is here, every file verified against the lock.

    The hashes were checked when the files were written; here the marker says
    against which lock, and each file's size is checked again, so a lock bumped
    since, or a file cut short on the disk, reads as absent rather than served."""
    where = directory()
    try:
        if (where / MARKER).read_text(encoding="utf-8") != _marker_text():
            return False
        return all(
            (where / name).stat().st_size == int(entry["size"]) for name, entry in files().items()
        )
    except OSError:
        return False


def path_of(name: str) -> Path | None:
    """A file to serve, or None: only a name the lock lists, only when available."""
    if name not in files() or not available():
        return None
    return directory() / name


# --- The download ------------------------------------------------------------------
@dataclass
class Progress:
    """Where a download is, for the admin card to draw."""

    state: str = "idle"  # idle | running | done | failed
    done_bytes: int = 0
    total_bytes: int = 0
    current: str = ""
    error: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def percent(self) -> int:
        if not self.total_bytes:
            return 0
        return min(100, int(self.done_bytes * 100 / self.total_bytes))


PROGRESS = Progress()


async def _httpx_download(url: str) -> AsyncIterator[bytes]:
    """The real downloader: one GET to the lock's fixed address, no redirects
    followed (the address is a release's, and a redirect is not expected)."""
    import httpx

    from lembas.services.fetch import check_url

    check_url(url)
    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client,
        client.stream("GET", url, follow_redirects=False) as response,
    ):
        if response.status_code != 200:
            raise PyodideError(
                t("The download was answered with %(status)s.", status=response.status_code)
            )
        async for part in response.aiter_bytes(64 * 1024):
            yield part


async def download(
    downloader: Downloader | None = None,
    *,
    progress: Progress | None = None,
) -> Path:
    """Fetch every file the lock lists, verify each, and move them into place.

    Refuses -- and keeps nothing -- on any file larger than the lock says, of a
    different size, or with a different SHA-256. Returns the directory."""
    fetch = downloader or _httpx_download
    state = progress if progress is not None else Progress()
    state.state, state.error, state.done_bytes = "running", "", 0
    state.total_bytes = total_size()
    final = directory()
    staging = root() / f".partial-{version()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        for name, entry in files().items():
            state.current = name
            want_size, want_hash = int(entry["size"]), str(entry["sha256"])
            digest = hashlib.sha256()
            got = 0
            with (staging / name).open("wb") as out:
                async for part in fetch(base_url() + name):
                    got += len(part)
                    if got > want_size:
                        raise PyodideError(
                            t("%(name)s is larger than its pinned size.", name=name)
                        )
                    digest.update(part)
                    out.write(part)
                    state.done_bytes += len(part)
            if got != want_size:
                raise PyodideError(t("%(name)s is not its pinned size.", name=name))
            if digest.hexdigest() != want_hash:
                raise PyodideError(t("%(name)s does not match its pinned SHA-256.", name=name))
        (staging / MARKER).write_text(_marker_text(), encoding="utf-8")
        shutil.rmtree(final, ignore_errors=True)
        staging.rename(final)
    except BaseException as problem:
        shutil.rmtree(staging, ignore_errors=True)
        state.state, state.current = "failed", ""
        state.error = str(problem) if isinstance(problem, PyodideError) else (
            t("The download failed: %(why)s", why=str(problem) or type(problem).__name__)
        )
        if isinstance(problem, Exception) and not isinstance(problem, PyodideError):
            raise PyodideError(state.error) from problem
        raise
    # Older versions are not served any more; they go with the new one in place.
    for other in root().iterdir():
        if other.is_dir() and other.name != version() and not other.name.startswith("."):
            shutil.rmtree(other, ignore_errors=True)
    state.state, state.current = "done", ""
    log.info("pyodide %s fetched and verified into %s", version(), final)
    return final


def start(downloader: Downloader | None = None) -> bool:
    """Start a download in the background, once; False when one is running."""
    if PROGRESS.state == "running" and PROGRESS.task is not None and not PROGRESS.task.done():
        return False

    async def run() -> None:
        with contextlib.suppress(PyodideError):
            await download(downloader, progress=PROGRESS)

    PROGRESS.state = "running"
    PROGRESS.done_bytes, PROGRESS.total_bytes, PROGRESS.error = 0, total_size(), ""
    PROGRESS.task = asyncio.get_running_loop().create_task(run())
    return True


def remove() -> bool:
    """Delete every Pyodide this server holds. True when there was one."""
    if PROGRESS.state == "running" and PROGRESS.task is not None and not PROGRESS.task.done():
        return False
    existed = root().exists() and any(root().iterdir())
    shutil.rmtree(root(), ignore_errors=True)
    PROGRESS.state, PROGRESS.error, PROGRESS.done_bytes = "idle", "", 0
    return existed


def forget() -> None:
    """For tests: no download in flight, nothing remembered."""
    PROGRESS.state, PROGRESS.error, PROGRESS.current = "idle", "", ""
    PROGRESS.done_bytes = PROGRESS.total_bytes = 0
    PROGRESS.task = None


__all__ = [
    "PROGRESS",
    "Downloader",
    "Progress",
    "PyodideError",
    "available",
    "base_url",
    "directory",
    "download",
    "files",
    "forget",
    "lock",
    "path_of",
    "remove",
    "start",
    "total_size",
    "version",
]
