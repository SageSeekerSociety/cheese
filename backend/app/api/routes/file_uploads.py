"""Where a browser puts one part of an upload, and where a machine fetches one
stored file, when files are kept on this server's disk (``files_store =
"local"``).

With the files bucket, each part goes straight into the bucket at a presigned
address. A local store has no such address of its own, so it hands out this one
(``blob_store.LocalStore.part_address``): the token in the path is the
permission, the way a presigned address's signature is, and it allows exactly
one part of one upload, one size and one MD5, for an hour. Who may upload
was decided when the upload was handed out.
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.byte_response import blob_response
from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core import blob_store
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say

router = APIRouter(tags=["files"])


class IncomingFileOut(BaseModel):
    """The part landed; registering the upload comes once every part has."""


@router.put(
    "/files/incoming/{token}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(IncomingFileOut),
)
async def put_incoming_file(token: str, request: Request) -> dict:
    """The bytes of one part, exactly as many as its address allows, and only
    if they are the MD5 it names."""
    part = blob_store.open_part(token)
    if part is None or part.location != blob_store.LOCAL:
        raise UnprocessableEntityError(say("uploadExpired"))
    await blob_store.local().receive_part(part, request.stream())
    return ok({})


@router.get(
    "/files/outgoing/{token}",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        }
    },
)
async def get_outgoing_file(token: str, request: Request) -> Response:
    """The bytes of one stored file, for whoever holds the address
    (``blob_store.LocalStore.download_url``): a person's 芝士 fetching a file
    of theirs onto its computer, which holds no credential of ours."""
    sealed = blob_store.open_download(token)
    if sealed is None or sealed.location != blob_store.LOCAL:
        raise UnprocessableEntityError(say("downloadExpired"))
    return blob_response(
        sealed.location,
        sealed.key,
        sealed.size,
        "application/octet-stream",
        {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        request.headers.get("range"),
        request.headers.get("if-range"),
    )
