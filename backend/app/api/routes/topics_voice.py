"""Voice input at the composer: a recording in, the message it says out.

Speaking into the composer is writing a message, so it is held to what a
message is: the caller has to be able to speak in this room. The room's
roster is read here, at the API layer that owns it, and handed to the voice
service as the names the recording is heard with.
"""

import uuid

from fastapi import APIRouter, File, UploadFile

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok
from app.api.topic_member_rows import roster_rows
from app.api.topics_deps import _actor_in_place, require_person_speaks
from app.api.write_access import ROUTE_DECIDES
from app.core.config import settings
from app.core.errors import ForbiddenError, ValidationError
from app.core.sentences import say
from app.domain.room_task.place import place_or_404
from app.domain.voice.service import VoiceInputs

router = APIRouter(prefix="/topics", tags=["topics"])

#: A full-length recording at 16 kHz, 16-bit mono is 1.92 MB; anything far
#: past that is not one the composer made.
_MAX_BYTES = 4 * 1024 * 1024


@router.post("/{topic_id}/voice", dependencies=[ROUTE_DECIDES])
async def hear_voice(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    file: UploadFile = File(...),
) -> dict:
    """Hear a WAV recording and answer the message it says: ``text`` tidied
    for sending, ``raw`` word for word, ``seconds`` it lasted and
    ``remaining_seconds`` of the speaker's allowance for today."""
    place = await place_or_404(db, topic_id)
    await resolver.require_verified_caller(
        project_id=place.project_id, topic_id=topic_id
    )
    actor = await _actor_in_place(resolver, place)
    await require_person_speaks(db, actor, place)
    if actor.user_id is None or actor.via != "token":
        raise ForbiddenError(say("voicePeopleOnly"))
    audio = await file.read(_MAX_BYTES + 1)
    if len(audio) > _MAX_BYTES:
        raise ValidationError(say("voiceTooLong", seconds=settings.voice_max_seconds))
    names = [row.get("name") or "" for row in await roster_rows(db, place.room)]
    heard = await VoiceInputs(db).hear(
        user_id=actor.user_id,
        project_id=place.project_id,
        topic_id=place.conversation_id,
        audio=audio,
        names=names,
    )
    return ok(
        {
            "text": heard.text,
            "raw": heard.raw,
            "seconds": round(heard.seconds, 1),
            "remaining_seconds": round(heard.remaining_seconds),
        }
    )
