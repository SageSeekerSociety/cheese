"""Readable original transcripts under the room's current access policy."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.domain.topic import transcript_stream
from app.domain.topic.models import RawTranscript
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics/{topic_id}/transcripts", tags=["topics"])
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _authorize(topic_id: uuid.UUID, db: AsyncSession, resolver) -> uuid.UUID:
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(
        fallback_handle=None, topic_id=topic_id, project_id=topic.project_id
    )
    await resolver.authorize_topic(
        actor, topic_id=topic_id, project_id=topic.project_id, enforce=True
    )
    return topic.project_id


@router.get("")
async def list_transcripts(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    project_id = await _authorize(topic_id, db, resolver)
    return ok(await transcript_stream.files(db, project_id, topic_id))


@router.get("/{file_id}")
async def read_transcript(
    topic_id: uuid.UUID, file_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> StreamingResponse:
    project_id = await _authorize(topic_id, db, resolver)
    row = await db.get(RawTranscript, file_id)
    if row is None or (row.project_id, row.topic_id) != (project_id, topic_id):
        raise NotFoundError("Transcript not found")
    chunks = list(row.chunks)
    await db.commit()
    return StreamingResponse(
        transcript_stream.contents(chunks),
        media_type="text/plain",
        headers={
            "Content-Disposition": f'attachment; filename="{file_id}.jsonl"',
            "Cache-Control": "no-store",
        },
    )
