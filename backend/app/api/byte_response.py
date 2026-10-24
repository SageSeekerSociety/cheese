"""Single-range responses: for media whose source already returns bytes, and
for bytes a blob store reads a stretch at a time."""

import re

from starlette.responses import Response, StreamingResponse

from app.core import blob_store

#: A request that names no range, or one this module does not serve (several
#: ranges, another unit): the whole body, which a 200 is permitted to be.
WHOLE = None
#: A range that starts past the end, or does not parse: 416.
UNSATISFIABLE = "unsatisfiable"


def requested_range(
    range_header: str | None, if_range: str | None, size: int
) -> tuple[int, int] | str | None:
    """The one byte range a request asks for, as inclusive ``(start, end)``.

    Multiple ranges and unknown units are ignored (a full 200 is permitted).
    There is no ETag or Last-Modified validator here, so If-Range cannot match
    and also falls back to a full response.
    """
    if not range_header or if_range or not range_header.startswith("bytes="):
        return WHOLE
    if "," in range_header:
        return WHOLE
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
    try:
        if not match or not any(match.groups()):
            raise ValueError
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        else:
            length = int(last)
            if length <= 0:
                raise ValueError
            start, end = max(0, size - length), size - 1
        if start >= size or start > end:
            raise ValueError
    except ValueError:
        return UNSATISFIABLE
    return start, end


def _unsatisfiable(headers: dict[str, str], size: int) -> Response:
    return Response(
        status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
    )


def media_bytes_response(
    data: bytes,
    media_type: str,
    headers: dict[str, str],
    range_header: str | None,
    if_range: str | None,
) -> Response:
    """Serve seeking without an unauthenticated file URL or temporary copy."""
    headers = {**headers, "Accept-Ranges": "bytes"}
    size = len(data)
    wanted = requested_range(range_header, if_range, size)
    if wanted is WHOLE:
        return Response(data, media_type=media_type, headers=headers)
    if wanted == UNSATISFIABLE:
        return _unsatisfiable(headers, size)
    assert isinstance(wanted, tuple)
    start, end = wanted
    return Response(
        data[start : end + 1],
        status_code=206,
        media_type=media_type,
        headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"},
    )


def blob_response(
    location: str,
    key: str,
    size: int,
    media_type: str,
    headers: dict[str, str],
    range_header: str | None = None,
    if_range: str | None = None,
) -> Response:
    """The bytes under ``key`` in the store at ``location``, streamed: a
    two-hundred-megabyte recording is never read into memory whole, and a
    player seeking in it is sent only the stretch it asked for."""
    headers = {**headers, "Accept-Ranges": "bytes"}
    store = blob_store.store(location)
    wanted = requested_range(range_header, if_range, size)
    if wanted == UNSATISFIABLE:
        return _unsatisfiable(headers, size)
    if wanted is WHOLE:
        if size == 0:
            return Response(b"", media_type=media_type, headers=headers)
        return StreamingResponse(
            store.chunks(key, 0, size - 1),
            media_type=media_type,
            headers={**headers, "Content-Length": str(size)},
        )
    assert isinstance(wanted, tuple)
    start, end = wanted
    return StreamingResponse(
        store.chunks(key, start, end),
        status_code=206,
        media_type=media_type,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        },
    )
