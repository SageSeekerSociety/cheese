"""项目总览上的「最近进展」：这个项目最近发生了什么。

不另存一张表：每一条都是已经记在别处的事实 —— 任务什么时候建的、开始的、采纳
的、关掉的，卡什么时候被退回，产物什么时候出了新的一版，任务从什么时候起停住了。
这里只把它们按时间摆在一起。

看得见什么由调用方给：`rooms` 是读者看得见、而且没有归档的频道，不在里面的频道
的任务不进来，同一条规矩（私密频道只给它的成员看）不在这里重新判一遍。
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.queries import awaiting_an_answer
from app.domain.project import artifacts
from app.domain.review.queries import (
    accepted_by_task,
    latest_cards_by_task,
    returned_since,
)
from app.domain.room_task import presentation
from app.domain.room_task.services import TaskService

#: 往回看多久。总览说的是「最近」，再早的去任务列表里翻。
WINDOW = timedelta(days=14)

#: 一次最多给多少条。
LIMIT = 40
#: 关闭离最后一次采纳不到这么久，就是那次采纳关的。
CLOSED_BY_LANDING = timedelta(minutes=5)

CREATED = "created"
STARTED = "started"
ACCEPTED = "accepted"
COMPLETED = "completed"
CLOSED = "closed"
RETURNED = "returned"
STALLED = "stalled"
VERSION = "version"


@dataclass(frozen=True, slots=True)
class Happening:
    """进展里的一条。"""

    kind: str
    at: datetime
    #: 是哪件任务；产物的新版本没有任务。
    task_id: uuid.UUID | None = None
    task_title: str = ""
    task_title_source: str | None = None
    room_id: uuid.UUID | None = None
    #: 谁做的这件事；停滞、新版本没有人。
    by: str | None = None
    #: 产物的新版本：哪一项、第几版。
    artifact_id: uuid.UUID | None = None
    artifact_name: str = ""
    version: int | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "at": self.at.isoformat(),
            "taskId": str(self.task_id) if self.task_id else None,
            "taskTitle": self.task_title,
            "taskTitleSource": self.task_title_source,
            "roomId": str(self.room_id) if self.room_id else None,
            "by": self.by,
            "artifactId": str(self.artifact_id) if self.artifact_id else None,
            "artifactName": self.artifact_name,
            "version": self.version,
        }


async def recent(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    rooms: Collection[uuid.UUID],
    hidden_rooms: Collection[uuid.UUID],
    now: datetime,
) -> list[Happening]:
    """`now` 往前 `WINDOW` 之内的进展，新的在前，最多 `LIMIT` 条。

    `hidden_rooms` 是读者看不见的私密频道：只在那里交付过的产物版本不算给他。
    """
    since = now - WINDOW
    seen = set(rooms)
    tasks = [
        t
        for t in await TaskService(session).list_in_project(project_id)
        if t.room_id in seen
    ]
    found: list[Happening] = []

    def about(task, kind: str, at: datetime | None, by: str | None) -> None:
        if at is not None and at >= since:
            found.append(
                Happening(
                    kind=kind,
                    at=at,
                    task_id=task.id,
                    task_title=task.title,
                    task_title_source=str(task.title_source),
                    room_id=task.room_id,
                    by=by,
                )
            )

    # 一件任务可以交付好几次，每一次采纳都是一条进展。
    accepted = await accepted_by_task(session, [t.id for t in tasks])
    for task in tasks:
        about(task, CREATED, task.created_at, task.created_by or task.owner_handle)
        about(task, STARTED, task.started_at, task.started_by)
        for landed in accepted.get(task.id, []):
            about(task, ACCEPTED, landed.at, landed.by)
        if task.id not in accepted and task.accepted_at is not None:
            # 没走交付、在代码仓库直接合并的那一次。
            about(task, ACCEPTED, task.accepted_at, task.accepted_by)
        # 最后一次采纳就把任务关了的，关闭不另算一条；采纳过几步之后才关的算。
        if task.closed_at is not None and (
            task.accepted_at is None
            or task.closed_at - task.accepted_at > CLOSED_BY_LANDING
        ):
            kind = COMPLETED if task.conclusion else CLOSED
            about(task, kind, task.closed_at, task.owner_handle)

    by_id = {t.id: t for t in tasks}
    for returned in await returned_since(session, list(by_id), since):
        task = by_id[returned.task_id]
        about(task, RETURNED, returned.at, returned.by)

    # 停住的那一刻：安静满 `STALLED_AFTER` 的时候。判据和侧栏、任务列表是同一个。
    open_tasks = [t for t in tasks if str(t.status) == "open"]
    ids = [t.id for t in open_tasks]
    last_said = await TaskService(session).last_block_at_for_tasks(ids)
    cards = await latest_cards_by_task(session, ids)
    asked = await awaiting_an_answer(session, ids)
    for task in open_tasks:
        shown = presentation.task_presentation(
            presentation.facts_for_task(
                task, cards.get(task.id), awaiting_answer=task.id in asked
            ),
            now=now,
        )
        quiet = presentation.quiet_since(
            shown,
            running=False,
            last_activity=last_said.get(task.id) or task.created_at,
        )
        if quiet is not None and quiet + presentation.STALLED_AFTER <= now:
            about(task, STALLED, quiet + presentation.STALLED_AFTER, None)

    for item in await artifacts.list_for_project(
        session, project_id, hidden=hidden_rooms
    ):
        if item.delivered_at is not None and item.delivered_at >= since:
            found.append(
                Happening(
                    kind=VERSION,
                    at=item.delivered_at,
                    artifact_id=item.id,
                    artifact_name=item.name,
                    version=item.version,
                )
            )

    found.sort(key=lambda h: h.at, reverse=True)
    return found[:LIMIT]
