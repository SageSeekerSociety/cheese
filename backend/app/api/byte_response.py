"""Single-range responses for media whose source already returns bytes."""

import re

from starlette.responses import Response


def media_bytes_response(
    data: bytes,
    media_type: str,
    headers: dict[str, str],
    range_header: str | None,
    if_range: str | None,
) -> Response:
    """Serve seeking without an unauthenticated file URL or temporary copy.

    Multiple ranges and unknown units are ignored (a full 200 is permitted).
    There is no ETag or Last-Modified validator here, so If-Range cannot match
    and also falls back to a full response.
    """
    headers = {**headers, "Accept-Ranges": "bytes"}
    if not range_header or if_range or not range_header.startswith("bytes="):
        return Response(data, media_type=media_type, headers=headers)
    if "," in range_header:
        return Response(data, media_type=media_type, headers=headers)
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
    size = len(data)
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
        return Response(
            status_code=416,
            headers={**headers, "Content-Range": f"bytes */{size}"},
        )
    return Response(
        data[start : end + 1],
        status_code=206,
        media_type=media_type,
        headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"},
    )
