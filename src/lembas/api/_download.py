"""A file's bytes going out: one answer for the page's door and the apps'.

**Nothing here runs as this application.** Every answer carries `nosniff` and
a policy that allows nothing and sandboxes the document. A file goes out as
`application/octet-stream` with `attachment` -- unless it was asked for
`inline` *and* its kind is one a browser decodes and never runs
(`file_kinds.INLINE`: a raster picture, sound, video), decided from the
file's own bytes, never from a type somebody claimed. SVG, HTML and PDF are
never among them.

**`Range` is answered** (one range; `bytes=a-b`, `a-`, `-n`), which is what
lets sound and video seek: 206 with `Content-Range`, 416 when the range is
past the end. Several ranges at once get the whole file, which a client must
accept. The bytes are read part by part in a session of the stream's own,
because the answer outlives the request that asked for it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from fastapi.responses import Response, StreamingResponse

from lembas.services import blobs, file_kinds

_RANGE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None | bool:
    """`(start, end)` inclusive for a range that can be served; `None` for no
    range (or one not understood: the whole file); `False` when it cannot be
    satisfied."""
    if not header:
        return None
    found = _RANGE.match(header)
    if found is None:
        return None
    first, last = found.group(1), found.group(2)
    if not first and not last:
        return None
    if not first:
        count = int(last)
        if count == 0 or size == 0:
            return False
        return max(size - count, 0), size - 1
    start = int(first)
    if start >= size:
        return False
    end = min(int(last), size - 1) if last else size - 1
    if end < start:
        return None
    return start, end


def _stream(digest: str, start: int, end: int) -> Iterator[bytes]:
    from lembas.db.session import session_scope

    with session_scope() as db:
        yield from blobs.iter_range(db, digest, start, end)


def respond(
    name: str,
    digest: str,
    size: int,
    kind: file_kinds.Kind,
    *,
    inline: bool = False,
    range_header: str | None = None,
    if_range: str | None = None,
) -> Response:
    from lembas.services.files import content_disposition

    etag = f'"{digest}"' if digest else ""
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Accept-Ranges": "bytes",
    }
    if etag:
        headers["ETag"] = etag
    if inline and kind.inline:
        media_type = kind.mime
    else:
        media_type = "application/octet-stream"
        headers["Content-Disposition"] = content_disposition("attachment", name)
    wanted = parse_range(range_header, size)
    if if_range and if_range.strip() != etag:
        wanted = None  # the file they began with is not this one: all of it
    if wanted is False:
        headers["Content-Range"] = f"bytes */{size}"
        return Response(status_code=416, headers=headers)
    if not digest or size == 0:
        headers["Content-Length"] = "0"
        return Response(b"", media_type=media_type, headers=headers)
    if wanted is None:
        headers["Content-Length"] = str(size)
        return StreamingResponse(
            _stream(digest, 0, size - 1), media_type=media_type, headers=headers
        )
    start, end = wanted
    headers["Content-Length"] = str(end - start + 1)
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        _stream(digest, start, end), status_code=206, media_type=media_type, headers=headers
    )


def zip_response(entries: list, name: str) -> Response:
    """A zip going out (`services/file_ops.zip_stream`): written as it is read."""
    from lembas.services import file_ops
    from lembas.services.files import content_disposition

    return StreamingResponse(
        file_ops.zip_stream(entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition("attachment", name),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


__all__ = ["parse_range", "respond", "zip_response"]
