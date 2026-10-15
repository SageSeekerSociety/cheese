"""每个席位生成得有多快，给做这个平台的 AI 队友自己查。

为什么不是 `/admin/stats/*`：管理面每条 endpoint 都是管理动作，`/admin/*` 不让带
agent 绑定的身份进。而这条读的是「我们几个席位谁快谁慢」，问的人恰恰是席位自己。
门照 `/run-records` 那套（`run_records._from_platform_room` 的孪生兄弟）：只在平台
自己的项目里读得到 —— 数字里有席位名和生成速率，只该给在修这个平台的人看。

口径（窗口吞吐）写在 `domain/platform_stats/throughput.py` 的模块 docstring 上。
"""

import uuid
from typing import Literal

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.api.response import ok, typed_response
from app.core.errors import ForbiddenError
from app.core.sentences import say
from app.domain.feedback.claims import is_platform_project
from app.domain.platform_stats import throughput
from app.domain.platform_stats.throughput import SeatThroughputOut
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/throughput", tags=["throughput"])

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


@router.get("/seats", **typed_response(SeatThroughputOut))
async def seat_throughput(
    db: DbSession,
    resolver: ActorResolverDep,
    topic: uuid.UUID,
    window: Window = "24h",
) -> dict:
    """一个窗口里每个席位 × 模型 × 供给一行的速率，另附只按席位的汇总。

    `topic` 是调用方自己所在的房间：门在这里，不在参数上。
    """
    await _from_platform_room(db, resolver, topic)
    return ok(await throughput.seats(db, window=window))
