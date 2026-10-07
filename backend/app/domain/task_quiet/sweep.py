"""好几天没人动的任务，提醒负责人一次。

判据只有一份，在 `room_task/presentation.py`（`quiet_since`）：侧栏收起、任务列表的
「已停滞」、这里的提醒读的是同一个函数。这一拍只管它的第一档 —— 安静满
`QUIET_NOTICE_AFTER` 而还没到 `STALLED_AFTER` 的那些。已经停了两周的不再提醒：它已经
从侧栏收起、在列表里收进了「已停滞」，这时候再来一条通知只是噪音。

「一次」靠投递账本去重，不另记一笔：事件的身份由「哪个任务」加「从哪一刻起安静」
决定，同一段安静算多少遍都是同一条事件。有人说了话、又安静下来，就是新的一段，
再提醒一次。

刻意不往任务里写一行：那一行本身会被当成「有动静」，安静就永远数不满两周。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sentences import notice_keys, notice_message, say
from app.domain.block.queries import awaiting_an_answer
from app.domain.delivery.addressing import Event, Hand, address
from app.domain.delivery.ledger import DeliveryEvent, deliver
from app.domain.notification.models import NotificationType
from app.domain.review.queries import latest_cards_by_task
from app.domain.room_task import presentation
from app.domain.room_task.services import TaskService
from app.domain.topic.services import TopicService

#: 这条事件的身份从「任务 + 安静的起点」算出来，同一段安静永远是同一个 id。
_QUIET = uuid.UUID("6f1d8a52-3c5e-4f0b-9a5e-2f6b6d1c7e31")

EVENT_TASK_QUIET = "task_quiet"


async def remind_quiet_tasks(sessions, *, now: datetime | None = None) -> int:
    """提醒安静满三天、还没到两周的任务的负责人。返回这一拍提醒了几件。"""
    at = now or datetime.now(UTC)
    async with sessions() as session:
        reminded = await _remind(session, at)
        await session.commit()
    return reminded


async def _remind(session: AsyncSession, now: datetime) -> int:
    tasks = [t for t in await TaskService(session).list_open() if t.owner_handle]
    if not tasks:
        return 0
    ids = [t.id for t in tasks]
    last_said = await TaskService(session).last_block_at_for_tasks(ids)
    cards = await latest_cards_by_task(session, ids)
    asked = await awaiting_an_answer(session, ids)
    topics = TopicService(session)
    reminded = 0
    for task in tasks:
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task, cards.get(task.id), awaiting_answer=task.id in asked
            ),
            now=now,
        )
        # 「在跑」不问跑轮次的进程：一轮在跑就会一直落 block，安静三天的任务不会有
        # 一轮正在跑。
        since = presentation.quiet_since(
            shown,
            running=False,
            last_activity=last_said.get(task.id) or task.created_at,
        )
        if since is None:
            continue
        quiet = now - since
        if not (presentation.QUIET_NOTICE_AFTER <= quiet < presentation.STALLED_AFTER):
            continue
        room = await topics.get(task.room_id)
        if room is None or str(room.status) == "archived":
            continue
        content = say("taskQuiet", title=task.title, days=quiet.days)
        await deliver(
            session,
            DeliveryEvent(
                id=uuid.uuid5(_QUIET, f"{task.id}:{since.isoformat()}"),
                type=NotificationType.ROOM_NOTICE,
                payload={
                    "projectId": str(task.project_id),
                    "topicId": str(room.id),
                    "topicTitle": room.title,
                    "taskId": str(task.id),
                    "content": str(content),
                    **notice_message(notice_keys(content=content)),
                    "eventType": EVENT_TASK_QUIET,
                    "severity": "info",
                },
                occurred_at=now,
            ),
            address(Event(owner=task.owner_handle), Hand.participant),
        )
        reminded += 1
    return reminded
