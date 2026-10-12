"""频道「之后怎么办」那几个动作：归档、取消归档、清理进度、删除。

四件走的是同一个门：谁管得了这个频道（或这个项目），谁就能动它。摆成单独一个模块，
是因为它们和 `topics.py` 里另外那些（读写消息、跑任务、看进度）问的不是同一件事，
而那个文件也在文件大小上限上 —— 拆出来的这一块正好是一个整体。

模块自己挂载：`app.main._discover_routers` 会把 `app.api.routes` 下每个模块级的
`APIRouter` 都装上去。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.write_access import ROUTE_DECIDES
from app.core.db import get_db
from app.core.errors import ForbiddenError
from app.core.sentences import say
from app.domain.agent.project_feed import TOPICS, tell_project
from app.domain.topic.schemas import TopicOut
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

router = APIRouter(prefix="/topics", tags=["topics"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/{topic_id}/archive")
async def archive_topic(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """手动归档 (归档去向): explicit archive, independent of 采纳."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    # A channel's manager, or whoever manages the project, in it or not: the
    # settings' channels page archives a private channel its admin is outside.
    await resolver.authorize_project(actor, project_id=topic.project_id)
    if not actor.authenticated:
        raise ForbiddenError(say("archiveSignIn"))
    await TopicMemberService(db).require_administrator(topic_id, actor.handle)
    topic = await TopicService(db).archive(topic_id, by=actor.handle)
    out = TopicOut.model_validate(topic).model_dump(mode="json")
    await db.commit()
    await tell_project(topic_id, TOPICS)
    return ok(out)


@router.get("/{topic_id}/cleanup")
async def cleanup_status(
    topic_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:

    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id, enforce=True
    )
    operation = await TopicService(db).cleanup_operation(topic)
    if operation is None:
        return ok({"state": "not_scheduled"})
    return ok(
        {
            "state": operation.state,
            "due_at": operation.due_at.isoformat(),
            "reason": operation.last_error,
            "resources": len(operation.resources),
            "removed": sum(bool(entry.get("removed")) for entry in operation.resources),
        }
    )


@router.post("/{topic_id}/unarchive")
async def unarchive_topic(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """取消归档: bring an archived topic back to active."""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    # A channel's manager, or whoever manages the project, in it or not: the
    # settings' channels page archives a private channel its admin is outside.
    await resolver.authorize_project(actor, project_id=topic.project_id)
    if not actor.authenticated:
        raise ForbiddenError(say("unarchiveSignIn"))
    await TopicMemberService(db).require_administrator(topic_id, actor.handle)
    topic = await TopicService(db).unarchive(topic_id, by=actor.handle)
    return ok(TopicOut.model_validate(topic).model_dump(mode="json"))


@router.delete("/{topic_id}", dependencies=[ROUTE_DECIDES])
async def delete_topic(
    topic_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """删除频道 (清单第 2 条): 频道连同消息、任务和文档一起走，不能恢复。

    It goes the way archiving goes — session stopped, cards settled, machines
    freed — and then does not come back: no list shows it, and 取消归档 is
    refused for it.
    """
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    # Who may archive a channel may delete it; the project's own overview is
    # refused in the service, where the rule lives.
    await resolver.authorize_project(actor, project_id=topic.project_id)
    if not actor.authenticated:
        raise ForbiddenError(say("deleteChannelSignIn"))
    await TopicMemberService(db).require_administrator(topic_id, actor.handle)
    await TopicService(db).delete(topic_id, by=actor.handle)
    await db.commit()
    await tell_project(topic_id, TOPICS)
    return ok({"id": str(topic_id), "deleted": True})
