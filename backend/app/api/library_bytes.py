"""One file of a library as an HTTP response: the current one or a version
listed in its history, as bytes to download, or an Office document turned into
a PDF for the preview. A project's, a team's and a 题目's library answer the
same way; who may read which shelf is each route's to decide first."""

import uuid
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.byte_response import blob_response
from app.domain.library import records as library_records
from app.domain.library.shelf import Shelf
from app.domain.preview import office


async def library_file_response(
    db: AsyncSession,
    shelf: Shelf,
    name: str,
    version: uuid.UUID | None,
    preview_pdf: bool,
    request: Request,
) -> Response:
    """`version`: the id of a row in the file's history; None is the current
    one. Never cached by the browser: a name can be replaced by new bytes."""
    stored = (
        await library_records.stored_version(db, shelf, name, version)
        if version is not None
        else await library_records.stored_file(db, shelf, name)
    )
    filename = quote(name.rsplit("/", 1)[-1], safe="")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "private, no-cache",
    }
    if preview_pdf:
        data = await library_records.read_stored(stored)
        pdf = await office.preview_pdf(data, name)
        return Response(content=pdf, media_type="application/pdf", headers=headers)
    return blob_response(
        stored.location,
        stored.key,
        stored.size,
        "application/octet-stream",
        headers,
        request.headers.get("range"),
        request.headers.get("if-range"),
    )
