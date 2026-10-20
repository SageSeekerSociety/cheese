"""Room-authorized image reads from the private transcript bucket."""

import re
import uuid

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.topics_deps import _actor_in_place
from app.core.errors import NotFoundError
from app.core.raster_images import sniff
from app.core.sentences import say
from app.core.storage import private_storage
from app.domain.room_task.place import place_or_404
from app.domain.session_record.images import image_key

router = APIRouter(prefix="/topics", tags=["topics"])


@router.get(
    "/{topic_id}/session-record-images/{digest}",
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
async def session_image(
    topic_id: uuid.UUID, digest: str, db: DbSession, resolver: ActorResolverDep
) -> Response:
    place = await place_or_404(db, topic_id)
    if place.room.deleted_at is not None:
        raise NotFoundError(say("topicNotFound"))
    await _actor_in_place(resolver, place)
    if re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise NotFoundError(say("stepNotFound"))
    body = await private_storage().download(image_key(place.conversation_id, digest))
    if body is None:
        raise NotFoundError(say("stepNotFound"))
    # The original image MIME is in the raw record. Never let content sniffing
    # execute an SVG or HTML document on the application's origin.
    return Response(
        body,
        media_type=sniff(body) or "application/octet-stream",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
