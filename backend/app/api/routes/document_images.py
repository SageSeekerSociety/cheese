"""The images a document shows: ``GET /documents/{id}/image?path=…``.

Whoever may read a document sees the images it references, and nobody else:
the bytes are answered under the same check as the document itself
(``app.api.doc_access.reach``), and where a path points is decided by whose
document it is, never by the caller.

- ``library/<名字>`` is the project's 资料库, for any of the project's
  documents.
- Any other path is a file of the room a task's living document belongs to —
  where ``cheese show`` puts what 芝士 made, and where a pasted screenshot
  lands. A document of the project's own has no room, so it references the
  library only.

Not the task's branch: an image made to illustrate a document is not the work
the repository holds (``app.domain.library.service``), and a branch's
unfinished files live on the machine working it, which can be offline when
someone reads.
"""

import asyncio
import uuid

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.auth import ActorResolverDep
from app.api.doc_access import reach
from app.api.project_library import shelf_for
from app.api.request_deps import DbSession
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say
from app.domain.library import records as library_records
from app.domain.library import service as library
from app.domain.project.room_files import (
    ARTIFACT_MIME,
    artifact_kind_for,
    clean_artifact_path,
)

router = APIRouter(prefix="/documents", tags=["documents"])

#: The kinds a document draws inline. SVG is not one: it can carry script, and
#: the chat's image route refuses it for the same reason.
_IMAGE_KINDS = ("png", "jpg", "gif", "webp")


@router.get(
    "/{document_id}/image",
    response_class=Response,
    responses={
        200: {
            "description": "The image's bytes.",
            "content": {ARTIFACT_MIME[kind]: {} for kind in _IMAGE_KINDS},
        }
    },
)
async def document_image(
    document_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> Response:
    """The bytes of an image the document references by ``path``.

    A browser cannot send its credential with an ``<img src>``, so the editor
    fetches these and shows them from memory."""
    reached = await reach(db, resolver, document_id)
    clean = clean_artifact_path(path)
    kind = artifact_kind_for(clean)
    if kind not in _IMAGE_KINDS:
        raise UnprocessableEntityError(say("attachmentImageOnly"))
    project_id = reached.doc.project_id
    if project_id is None:
        # Pictures live in a project's library; a team's own document has none.
        raise UnprocessableEntityError(say("docTeamNoImages"))
    name = library.library_name(clean)
    if name is not None:
        shelf = await shelf_for(db, project_id)
        data = await library_records.read(db, shelf, name)
    elif reached.place is not None:
        data = await asyncio.to_thread(
            library.read_room_file, project_id, reached.place.room_id, clean
        )
    else:
        raise UnprocessableEntityError(say("docImageLibraryOnly"))
    return Response(
        content=data,
        media_type=ARTIFACT_MIME[kind],
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            # The same address can name new bytes: a library file replaced, a
            # room file shown again.
            "Cache-Control": "private, no-cache",
        },
    )
