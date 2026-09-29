"""Find what the project already knows, from where the caller stands.

One query across the rooms the caller may read: room titles, messages and
documents, decisions, tasks, library file names and the artifact list. Every
word of the query has to be found; each group lists its best matches first (see
`app.domain.search.bm25`), library file names aside, which are matched as
written. Each hit names where it lives, so it can be cited; rooms the caller may
not read are not searched and are only counted.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
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
from app.domain.search import bm25
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


# Written out rather than bound: the blocks index is partial on exactly these
# kinds (migration 2d2fc3a8ce36), and Postgres only uses it when it can prove
# the query's predicate implies the index's — which it cannot do against bind
# parameters once a prepared statement goes generic.
_SEARCHED_BLOCKS_SQL = text(
    "blocks.kind IN (" + ", ".join(f"'{k.value}'" for k in SEARCHED_BLOCKS) + ")"
)


def _snippet(body: str, terms: list[str], width: int = 160) -> str:
    body = " ".join((body or "").split())
    lowered = body.lower()
    found = [at for at in (lowered.find(t.lower()) for t in terms) if at >= 0]
    at = min(found) if found else 0
    start = max(at - width // 2, 0)
    piece = body[start : start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(body) else "")


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
    terms = bm25.words(q)
    await bm25.serial_scans(db)

    def where(room_id: uuid.UUID) -> dict:
        room = readable[room_id]
        return {"room_id": str(room.id), "room_title": room.title}

    hits: dict[str, list[dict]] = {"rooms": [], "records": [], "tasks": []}
    if readable:
        in_readable = list(readable)
        found_rooms = await db.scalars(
            select(Topic)
            .where(
                bm25.match_all_words(
                    Topic.id,
                    terms,
                    {"title": 1},
                    filters=[bm25.any_of("id", in_readable)],
                )
            )
            .order_by(func.paradedb.score(Topic.id).desc(), Topic.id)
            .limit(limit)
        )
        hits["rooms"] = [
            {**where(r.id), "status": str(r.status.value)} for r in found_rooms
        ]
        blocks = await db.scalars(
            select(Block)
            .where(
                bm25.match_all_words(
                    Block.id,
                    terms,
                    {"content": 1},
                    filters=[bm25.any_of("topic_id", in_readable)],
                ),
                _SEARCHED_BLOCKS_SQL,
            )
            .order_by(func.paradedb.score(Block.id).desc(), Block.id)
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
                "snippet": _snippet(b.content, terms),
            }
            for b in blocks
        ]
        tasks = await db.scalars(
            select(Task)
            .where(
                bm25.match_all_words(
                    Task.id,
                    terms,
                    {"title": 2, "brief": 1, "conclusion": 1},
                    filters=[bm25.any_of("room_id", in_readable)],
                )
            )
            .order_by(func.paradedb.score(Task.id).desc(), Task.id)
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
                    " ".join(filter(None, (t.title, t.brief, t.conclusion))), terms
                ),
            }
            for t in tasks
        ]

    hits["library"] = [
        {"path": f["path"], "bytes": f["bytes"], "modified": f["modified"]}
        for f in library.list_library_files(project_id)
        if q.lower() in f["path"].lower()
    ][:limit]
    artifacts = await db.scalars(
        select(ProjectArtifact)
        .where(
            bm25.match_all_words(
                ProjectArtifact.id,
                terms,
                {"name": 2, "about": 1},
                filters=[bm25.any_of("project_id", [project_id])],
            )
        )
        .order_by(func.paradedb.score(ProjectArtifact.id).desc(), ProjectArtifact.id)
        .limit(limit)
    )
    hits["artifacts"] = [
        {"id": str(a.id), "name": a.name, "about": a.about} for a in artifacts
    ]

    return ok(
        {
            "query": q,
            "searched_rooms": len(readable),
            "skipped_rooms": skipped,
            "hits": hits,
            "total": sum(len(v) for v in hits.values()),
        }
    )
