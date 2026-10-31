"""归档对账：房间归档了，它名下还在跑的规则要说一句话，并且只说一次。

规则的自己那一行不动 —— 归档是别处做的动作，所以这里每次对账扫一遍房间状态，
而不是让归档那几条路各自记得来敲这扇门。

`RoutineService` 上的同名方法是转发。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.domain.agent_instance.adapters import project_team
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.numbering import lock_conversations
from app.domain.event_author import AuthorType
from app.domain.notice_meta import (
    EVENT_ROUTINE_STOPPED,
    SEVERITY_INFO,
    WHO_PLATFORM,
    notice,
)
from app.domain.notification.models import NotificationLevel, NotificationType
from app.domain.notification.services import ProjectNotificationService
from app.domain.routine.models import (
    Routine,
    RoutineState,
)
from app.domain.routine.service._shared import (
    now,
)
from app.domain.topic.models import Topic, TopicStatus

if TYPE_CHECKING:
    from app.domain.routine import service as pkg


async def announce_archived_rooms(svc: pkg.RoutineService) -> int:
    """归档是别处做的动作：每次对账扫一遍，哪个房间的话还没说就补上。

    认房间的状态，不认是谁归档的 —— 手工归档、整个项目一起归档、脚本归档
    走的是同一个答案，话题那一域也就不用认识周期任务。

    规则自己那一行**不动**（结论：取消归档后从下一个时刻继续，
    ``_fire_schedules`` 一直是按房间状态挑的）。要说的是每个归档了、又还有
    在跑的例行事务的房间：房间里落一行「N 条例行事务已随归档停止」，加上给每条例行
    事务主人的一条通知。

    Idempotent per archive episode：房间自己的 ``archived_at`` 印在那行上，
    已经带着本轮印子的房间跳过 —— 所以取消归档后再归档会再说一次，而手工
    归档过的房间被项目归档又捎带一遍时不会再被说一次。

    Returns how many rules were announced as stopped.
    """
    rooms = list(
        await svc._session.scalars(
            select(Topic)
            .where(Topic.status == TopicStatus.archived)
            .where(
                Topic.id.in_(
                    select(Routine.topic_id).where(
                        Routine.state == RoutineState.active.value
                    )
                )
            )
            # 和这一串里别的几步一样：两个后端进程同时扫，锁住的那间这次跳过，
            # 下一轮（30 秒后）再补 —— 不然同一段归档会被说两遍。
            .with_for_update(skip_locked=True)
        )
    )

    # Every room it may speak in, at once, before the first line
    # (`conversation.numbering`).
    await lock_conversations(svc._session, [room.id for room in rooms])
    stopped = 0
    for room in rooms:
        rules = list(
            await svc._session.scalars(
                select(Routine).where(
                    Routine.topic_id == room.id,
                    Routine.state == RoutineState.active.value,
                )
            )
        )
        if not rules:
            continue
        stamp = (room.archived_at or now()).isoformat()
        already = await svc._session.scalar(
            select(Block.id)
            .where(
                Block.conversation_id == room.id,
                Block.meta["event_type"].as_string() == EVENT_ROUTINE_STOPPED,
                Block.meta["archived_at"].as_string() == stamp,
            )
            .limit(1)
        )
        if already is not None:
            continue
        svc._session.add(
            Block(
                id=uuid.uuid4(),
                project_id=room.project_id,
                conversation_id=room.id,
                author="system",
                author_type=AuthorType.platform,
                kind=BlockKind.event,
                content=f"{len(rules)} 条例行事务已随归档停止",
                meta={
                    **notice(
                        EVENT_ROUTINE_STOPPED,
                        severity=SEVERITY_INFO,
                        who=WHO_PLATFORM,
                        detail="\n".join(f"「{r.title}」" for r in rules),
                        detail_label="例行事务",
                    ),
                    "routine_ids": [str(r.id) for r in rules],
                    "archived_at": stamp,
                },
            )
        )
        for rule in rules:
            await ProjectNotificationService(svc._session, team=project_team).create(
                project_id=rule.project_id,
                level=NotificationLevel.light,
                kind=NotificationType.CHANGE_ALERT,
                title=f"例行事务「{rule.title}」已随频道归档停止",
                body=(
                    f"房间「{room.title}」归档了，这条例行事务随之停下。"
                    "取消归档后它会从下一个时刻继续，不用重新设置。"
                ),
                target_handle=rule.owner_handle,
                conversation_id=room.id,
                payload={"routine_id": str(rule.id)},
            )
        stopped += len(rules)
    await svc._session.flush()
    return stopped


async def archived_room_ids(
    svc: pkg.RoutineService, topic_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """这些房间里哪些归档了，一次查询问完。

    规则的自己那一行不记「房间没了」（归档不写规则），所以列表上的
    「已随话题归档停止」只能问房间 —— 一条一条问就是每行一次往返。
    """
    if not topic_ids:
        return set()
    stmt = select(Topic.id).where(
        Topic.id.in_(topic_ids), Topic.status == TopicStatus.archived
    )
    return set((await svc._session.scalars(stmt)).all())
