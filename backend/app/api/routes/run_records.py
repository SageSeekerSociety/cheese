"""运行记录，给做这个平台本身的项目里的 AI 队友读：和管理后台「运行记录」同一份
（`app.domain.run_record.admin`），从它所在的频道读。

为什么不是管理后台那两条：`/admin/*` 不让带 agent 绑定的身份进（管理面的每个
endpoint 都是管理动作）。这里只读，而且只在平台自己的项目里读得到——报错里有别的
项目的名字和对话，只该给在修这个平台的人看。
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Query

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.admin_common import DbSession
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.feedback.claims import is_platform_project
from app.domain.run_record import admin
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/run-records", tags=["run-records"])

Window = Literal["24h", "7d", "30d"]


async def _from_platform_room(
    db: DbSession, resolver: ActorResolverDep, topic: uuid.UUID
) -> None:
    """The caller is in this room, and the room's project works on the platform."""
    place = await TopicService(db).place_or_404(topic)
    who = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        who, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    if not await is_platform_project(db, place.project_id):
        raise ForbiddenError(say("runRecordsPlatformRoomsOnly"))


@router.get("")
async def run_records_overview(
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID,
    window: Window = "24h",
    group: Literal["all", "errors", "recovered"] = "all",
    q: str | None = Query(None, max_length=200),
) -> dict:
    await _from_platform_room(db, resolver, topic)
    return ok(await admin.overview(db, window=window, group=group, query=q))


@router.get("/detail")
async def run_records_detail(
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID,
    key: str = Query(..., max_length=2000),
    kind: str = Query(..., max_length=48),
    window: Window = "24h",
) -> dict:
    await _from_platform_room(db, resolver, topic)
    found = await admin.detail(db, key=key, kind=kind, window=window)
    if found is None:
        raise NotFoundError("No such run records in this window")
    return ok(found)
