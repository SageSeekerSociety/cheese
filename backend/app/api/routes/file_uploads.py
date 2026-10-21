"""Where a browser puts an upload when new files are kept on this server's
disk (``files_store = "local"``).

With the files bucket, the browser puts an upload straight into the bucket at a
presigned address. A local store has no such address of its own, so it hands
out this one (``blob_store.LocalStore.upload_url``): the token in the path is
the permission, the way a presigned address's signature is, and it allows
exactly one key and one size for a few minutes. Who may upload was decided when
the address was handed out.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.response import ok, typed_response
from app.api.write_access import ROUTE_DECIDES
from app.core import blob_store
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say

router = APIRouter(tags=["files"])


class IncomingFileOut(BaseModel):
    """The bytes landed; registering them is the next request."""


@router.put(
    "/files/incoming/{token}",
    dependencies=[ROUTE_DECIDES],
    **typed_response(IncomingFileOut),
)
async def put_incoming_file(token: str, request: Request) -> dict:
    """The bytes of one upload, exactly as many as its address allows."""
    sealed = blob_store.open_upload(token, bound_to=blob_store.INCOMING)
    if sealed is None or sealed.location != blob_store.LOCAL:
        raise UnprocessableEntityError(say("uploadExpired"))
    await blob_store.local().receive(sealed.key, sealed.size, request.stream())
    return ok({})
