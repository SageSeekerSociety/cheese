"""Find what the project already knows, from where the caller stands.

One literal query across the rooms the caller may read: room titles, messages
and documents, decisions, tasks, library file names and the artifact list. Each
hit names where it lives, so it can be cited; rooms the caller may not read are
not searched and are only counted.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.db import get_db
from app.core.errors import NotFoundError, ValidationError
from app.domain.block.models import Block, BlockKind
from app.domain.identity.actor import Actor
from app.domain.library import service as library
from app.domain.project.models import ProjectArtifact
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="", tags=["project-context"])

DbSession = Annotated[AsyncSession, Depends(get_db)]

SEARCHED_BLOCKS = (
    BlockKind.message,
    BlockKind.doc,
    BlockKind.doc_node,
    BlockKind.comment,
    BlockKind.decision,
    BlockKind.weekly,
)


def _pattern(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _snippet(text: str, q: str, width: int = 160) -> str:
    text = " ".join((text or "").split())
    at = text.lower().find(q.lower())
    start = max(at - width // 2, 0) if at >= 0 else 0
    piece = text[start : start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")


@router.get("/projects/{project_id}/context/search")
async def search_project_context(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    topic: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict:
    q = q.strip()
    if not q:
        raise ValidationError("要搜的关键词不能是空的")
    if topic is not None:
        place = await TopicService(db).place_or_404(topic)
        if place.project_id != project_id:
            raise NotFoundError("Topic not found")
        actor = await resolver.resolve(
            fallback_handle=None, project_id=project_id, topic_id=topic
        )
        await resolver.authorize_topic(
            actor, project_id=project_id, topic_id=topic, enforce=True
        )
        if not actor.authenticated:
            # The development credential speaks as the room's own teammate.
            seat = await TopicMemberService(db).resolve_agent_handle(place.room_id)
            actor = Actor(handle=seat, user_id=None, via="cheese")
    else:
        actor = await resolver.resolve(fallback_handle=None, project_id=project_id)
        await resolver.authorize_project(actor, project_id=project_id)

    rooms = list(await db.scalars(select(Topic).where(Topic.project_id == project_id)))
    readable: dict[uuid.UUID, Topic] = {}
    for room in rooms:
        if await resolver.can_access_topic(
            actor, project_id=project_id, topic_id=room.id
        ):
            readable[room.id] = room
    skipped = len(rooms) - len(readable)
    pattern = _pattern(q)

    def where(room_id: uuid.UUID) -> dict:
        room = readable[room_id]
        return {"room_id": str(room.id), "room_title": room.title}

    hits: dict[str, list[dict]] = {}
    hits["rooms"] = [
        {**where(r.id), "status": str(r.status.value)}
        for r in readable.values()
        if q.lower() in (r.title or "").lower()
    ][:limit]

    if readable:
        blocks = await db.scalars(
            select(Block)
            .where(
                Block.topic_id.in_(list(readable)),
                Block.kind.in_(SEARCHED_BLOCKS),
                Block.content.ilike(pattern, escape="\\"),
            )
            .order_by(Block.created_at.desc())
            .limit(limit)
        )
        hits["records"] = [
            {
                **where(b.topic_id),
                "id": str(b.id),
                "kind": str(b.kind.value),
                "author": b.author,
                "created_at": b.created_at.isoformat(),
                "task_id": str(b.task_id) if b.task_id else None,
                "snippet": _snippet(b.content, q),
            }
            for b in blocks
        ]
        tasks = await db.scalars(
            select(Task)
            .where(
                Task.room_id.in_(list(readable)),
                or_(
                    Task.title.ilike(pattern, escape="\\"),
                    Task.brief.ilike(pattern, escape="\\"),
                    Task.conclusion.ilike(pattern, escape="\\"),
                ),
            )
            .order_by(Task.created_at.desc())
            .limit(limit)
        )
        hits["tasks"] = [
            {
                **where(t.room_id),
                "id": str(t.id),
                "title": t.title,
                "status": str(t.status.value),
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                "snippet": _snippet(
                    " ".join(filter(None, (t.title, t.brief, t.conclusion))), q
                ),
            }
            for t in tasks
        ]
    else:
        hits["records"] = []
        hits["tasks"] = []

    hits["library"] = [
        {"path": f["path"], "bytes": f["bytes"], "modified": f["modified"]}
        for f in library.list_library_files(project_id)
        if q.lower() in f["path"].lower()
    ][:limit]
    artifacts = await db.scalars(
        select(ProjectArtifact).where(
            ProjectArtifact.project_id == project_id,
            or_(
                ProjectArtifact.name.ilike(pattern, escape="\\"),
                ProjectArtifact.about.ilike(pattern, escape="\\"),
            ),
        )
    )
    hits["artifacts"] = [
        {"id": str(a.id), "name": a.name, "about": a.about} for a in artifacts
    ][:limit]

    return ok(
        {
            "query": q,
            "searched_rooms": len(readable),
            "skipped_rooms": skipped,
            "hits": hits,
            "total": sum(len(v) for v in hits.values()),
        }
    )
