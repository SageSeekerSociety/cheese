"""30 秒一次的那一趟：先响，再把变了的消息推给开着频道的人。

`sweep` 是这一域唯一的时针（`periodic.py` 注册的就是它），它把上面几件事按顺序
跑一遍，并在两个事务之间把新消息和结算后的消息各推一次 —— 推送必须在提交之后，
否则读到的是还没生效的行。

`publish_run_messages` 也被路由直接调用（手动执行那一条），所以它是公开名字。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.db import SessionFactory
from app.domain.agent.realtime.broker import get_broker
from app.domain.block.models import Block
from app.domain.block.repositories import BlockRepository
from app.domain.block.schemas import BlockOut
from app.domain.delivery.agent import dispatch_pending
from app.domain.routine.models import (
    RoutineRun,
)
from app.domain.routine.reads import runs_under
from app.domain.routine.service._shared import (
    now,
)
from app.domain.routine.service.scheduling import _fire_events, _fire_schedules

# 类的定义在子模块 `service.py` 里（包名与模块名同形，是这仓里拆 service
# 的既有形状：`app.domain.feedback.services.service` 等）。
from app.domain.routine.service.service import RoutineService
from app.domain.routine.service.settlement import (
    _announce_finished,
    _settle_open_runs,
)
from app.domain.thread import reads as thread_reads


async def publish_run_messages(
    sessions: SessionFactory, message_ids: list[uuid.UUID], *, new: bool
) -> None:
    """Runs' messages, whole, to whoever has their channel open: a new one as
    a message of the teammate's, a settled one as the line it replaces. Each
    carries what it is drawn with: how the run went, its reactions and the
    line under it of its 支线."""
    if not message_ids:
        return

    broker = get_broker()
    async with sessions() as session:
        messages = list(
            await session.scalars(select(Block).where(Block.id.in_(message_ids)))
        )
        runs = await runs_under(session, message_ids)
        threads = await thread_reads.under_messages(
            session,
            message_ids,
            replying=lambda thread: sorted(
                set(broker.activity.turn_agents(str(thread)).values())
            ),
        )
        reactions = await BlockRepository(session).reactions_for_blocks(message_ids)
    for message in messages:
        payload = BlockOut.model_validate(message).model_dump(mode="json")
        payload["routine_run"] = runs.get(message.id)
        if message.id in reactions:
            payload["reactions"] = reactions[message.id]
        if message.id in threads:
            payload["thread"] = threads[message.id]
        await broker.publish(
            str(message.conversation_id),
            {
                "type": "assistant_block" if new else "block_updated",
                "block": payload,
            },
        )


async def sweep(sessions: SessionFactory, *, chat, runner) -> dict[str, int]:
    began = now()
    # Each kind of firing in a transaction of its own: each names every room
    # it writes into first (`conversation.numbering`), and one transaction
    # takes one such batch. Nothing ties a scheduled fire to an event fire.
    async with sessions() as session:
        scheduled = await _fire_schedules(session)
        await session.commit()
    async with sessions() as session:
        triggered = await _fire_events(session)
        await session.commit()
    async with sessions() as session:
        started = [
            message
            for message in await session.scalars(
                select(RoutineRun.message_id).where(
                    RoutineRun.created_at >= began,
                    RoutineRun.message_id.is_not(None),
                )
            )
            if message is not None
        ]
    await publish_run_messages(sessions, started, new=True)
    async with sessions() as session:
        # 归档是别处做的动作，所以这里每次都问一遍「哪个房间的话还没说」，而不是
        # 让归档那几条路各自记得来敲这扇门（`announce_archived_rooms`）。
        stopped = await RoutineService(session).announce_archived_rooms()
        await _settle_open_runs(session)
        changed = await _announce_finished(session)
        await session.commit()
    await publish_run_messages(sessions, [m.id for m in changed], new=False)
    dispatched = 0
    if scheduled or triggered:
        dispatched = await dispatch_pending(sessions, chat=chat, runner=runner)
    return {
        "scheduled": scheduled,
        "triggered": triggered,
        "stopped": stopped,
        "dispatched": dispatched,
    }
