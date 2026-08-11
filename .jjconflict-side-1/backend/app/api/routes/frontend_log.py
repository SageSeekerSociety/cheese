"""Frontend error reports → 现场 (see app.domain.frontend_log)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.core.db import get_db
from app.core.errors import NotFoundError
from app.domain import frontend_log  # module import: tests swap the intake singleton
from app.domain.block.models import AuthorType, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.frontend_log import FrontendErrorBatchIn
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.repositories import TopicRepository

router = APIRouter(prefix="/api/frontend-errors", tags=["frontend-errors"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("")
async def report_frontend_errors(body: FrontendErrorBatchIn, db: DbSession) -> dict:
    """Persist browser-side errors as 现场 event blocks so agents (who cannot
    read a user's console) and humans can debug from the same timeline. Falls
    back to the project's root topic when no topic was open. Dedup and
    rate-limiting happen in the intake — a dropped duplicate still returns
    200, so the reporting client never retries."""
    topics = TopicRepository(db)
    topic = await topics.get(body.topic_id) if body.topic_id else None
    if topic is None or topic.project_id != body.project_id:
        project = await ProjectRepository(db).get(body.project_id)
        if project is None:
            raise NotFoundError("Project not found")
        topic = (
            await topics.get(project.root_topic_id) if project.root_topic_id else None
        )
    if topic is None:
        raise NotFoundError("No topic to attach frontend errors to")

    blocks = BlockRepository(db)
    accepted = 0
    for err in body.errors:
        if not frontend_log.intake.admit(
            str(body.project_id), frontend_log.fingerprint(err)
        ):
            continue
        await blocks.add(
            project_id=topic.project_id,
            topic_id=topic.id,
            author="frontend",
            author_type=AuthorType.system,
            content=frontend_log.event_content(err),
            kind=BlockKind.event,
            meta=frontend_log.event_meta(err),
        )
        accepted += 1
    await db.commit()
    return ok({"accepted": accepted, "dropped": len(body.errors) - accepted})
