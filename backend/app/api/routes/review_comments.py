"""Comments on a task's changes (改动), written while it awaits review.

`GET` answers every sent comment and the caller's own drafts, each with where
its lines are in the version now under review. Writing, editing and deleting a
draft are a person's acts; 芝士 answers comments by what it reports when it
hands the task over again, not through these routes.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.place import task_conversation
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.review import document_compare
from app.domain.review.comments import ReviewCommentService, describe
from app.domain.review.schemas import ReviewCommentEdit, ReviewCommentIn
from app.domain.topic.services import TopicService

router = APIRouter(prefix="", tags=["review-comments"], dependencies=[ROUTE_DECIDES])
DbSession = Annotated[AsyncSession, Depends(get_db)]


def _person(actor: Actor) -> str:
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("reviewCommentSignIn"))
    if actor.via == "cheese":
        raise ForbiddenError(say("reviewCommentNotAllowed"))
    return actor.handle


@router.get("/topics/{task_id}/review-comments")
async def list_review_comments(
    task_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    _place, actor, task = await task_conversation(db, resolver, task_id)
    svc = ReviewCommentService(db)
    rows = await svc.comments(task.id, actor.handle or "")
    anchor = await svc.anchors(task, rows)
    return ok({"comments": [describe(row, anchor) for row in rows]})


@router.get("/topics/{task_id}/review-comparison")
async def review_comparison(
    task_id: uuid.UUID, path: str, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """A Word document, workbook or deck in the task, compared with the version
    it is read against: paragraphs, cells or slides. `comparison` is null for
    any other file, or when a version cannot be read."""
    _place, _actor, task = await task_conversation(db, resolver, task_id)
    return ok({"comparison": await document_compare.comparison(db, task, path)})


@router.post("/topics/{task_id}/review-comments")
async def write_review_comment(
    task_id: uuid.UUID, body: ReviewCommentIn, db: DbSession, resolver: ActorResolverDep
) -> dict:
    _place, actor, task = await task_conversation(db, resolver, task_id)
    author = _person(actor)
    comment = await ReviewCommentService(db).write(
        task.id,
        author,
        path=body.path,
        line_start=body.line_start,
        line_end=body.line_end,
        line_text=body.line_text,
        place=body.place,
        commit_sha=body.commit_sha,
        body=body.body,
        suggestion=body.suggestion,
        parent_id=body.parent_id,
    )
    await db.commit()
    return ok(describe(comment, {}))


async def _draft_actor(
    comment_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> str:
    task_id = await ReviewCommentService(db).task_of(comment_id)
    if task_id is None:
        raise NotFoundError(say("reviewCommentNotFound"))
    place = await TopicService(db).place_or_404(task_id)
    actor = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        actor, project_id=place.project_id, topic_id=place.room_id
    )
    return _person(actor)


@router.patch("/review-comments/{comment_id}")
async def edit_review_comment(
    comment_id: uuid.UUID,
    body: ReviewCommentEdit,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    author = await _draft_actor(comment_id, db, resolver)
    comment = await ReviewCommentService(db).edit(
        comment_id, author, body=body.body, suggestion=body.suggestion
    )
    await db.commit()
    return ok(describe(comment, {}))


@router.delete("/review-comments/{comment_id}")
async def delete_review_comment(
    comment_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    author = await _draft_actor(comment_id, db, resolver)
    await ReviewCommentService(db).delete(comment_id, author)
    await db.commit()
    return ok({"id": str(comment_id)})
