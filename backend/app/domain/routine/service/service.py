"""`RoutineService` —— 状态在这里，方法全是一句转发。

`_session` 由 `__init__` 建立；每个方法的实现都在同级子模块里同名的那个函数中
（`rules` / `archive` / `settlement`），所以行为和 patch 点都和拆文件之前一样。
包的说明在 `app/domain/routine/service/__init__.py`。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.routine.models import Routine, RoutineRun

from . import archive, rules, settlement, spend

#: `create` 的签名里用到 `Topic`，但这里只转发、不碰它（`from __future__ import
#: annotations` 下标注不求值），所以按本仓的惯例放进 TYPE_CHECKING。
if TYPE_CHECKING:
    from app.domain.topic.models import Topic


class RoutineService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _check_feedback_batch(self, spec: dict, project_id: uuid.UUID) -> None:
        return await rules._check_feedback_batch(self, spec=spec, project_id=project_id)

    async def get(self, routine_id: uuid.UUID) -> Routine:
        return await rules.get(self, routine_id=routine_id)

    async def list(
        self, project_id: uuid.UUID, *, topic_id: uuid.UUID | None = None
    ) -> list[Routine]:
        return await rules.list_for_project(
            self,
            project_id=project_id,
            topic_id=topic_id,
        )

    async def runs(
        self,
        routine_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 50,
        failed_only: bool = False,
    ) -> tuple[list[RoutineRun], int]:
        return await rules.runs(
            self,
            routine_id=routine_id,
            offset=offset,
            limit=limit,
            failed_only=failed_only,
        )

    async def credits_by_rule(
        self, routine_ids: list[uuid.UUID], *, since: datetime, until: datetime
    ) -> dict[uuid.UUID, float]:
        return await spend.credits_by_rule(
            self._session, routine_ids, since=since, until=until
        )

    async def credits_by_run(
        self, runs: list[RoutineRun], *, since: datetime, until: datetime
    ) -> dict[uuid.UUID, float]:
        return await spend.credits_by_run(self._session, runs, since=since, until=until)

    async def latest_run_statuses(
        self, routine_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        return await rules.latest_run_statuses(self, routine_ids=routine_ids)

    async def _agent_for(self, topic_id: uuid.UUID, requested: str | None) -> str:
        return await rules._agent_for(self, topic_id=topic_id, requested=requested)

    async def create(
        self,
        *,
        topic: Topic,
        by: str,
        by_agent: bool,
        title: str,
        instructions: str,
        note: str,
        trigger: str,
        spec: dict,
        tz: str | None,
        owner_handle: str | None,
        agent_handle: str | None,
    ) -> Routine:
        return await rules.create(
            self,
            topic=topic,
            by=by,
            by_agent=by_agent,
            title=title,
            instructions=instructions,
            note=note,
            trigger=trigger,
            spec=spec,
            tz=tz,
            owner_handle=owner_handle,
            agent_handle=agent_handle,
        )

    def _activate(self, row: Routine) -> None:
        return rules._activate(self, row=row)

    async def _settled(self, row: Routine, content: str) -> None:
        return await rules._settled(self, row=row, content=content)

    async def pause(self, row: Routine) -> Routine:
        return await rules.pause(self, row=row)

    async def resume(self, row: Routine) -> Routine:
        return await rules.resume(self, row=row)

    async def update(self, row: Routine, *, changes: dict) -> Routine:
        return await rules.update(self, row=row, changes=changes)

    async def run_now(self, row: Routine, *, by: str) -> RoutineRun:
        return await rules.run_now(self, row=row, by=by)

    async def delete(self, row: Routine, *, by: str) -> None:
        return await rules.delete(self, row=row, by=by)

    async def announce_archived_rooms(self) -> int:
        return await archive.announce_archived_rooms(self)

    async def archived_room_ids(self, topic_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        return await archive.archived_room_ids(self, topic_ids=topic_ids)

    async def report(
        self,
        run: RoutineRun,
        *,
        by: str,
        status: str,
        summary: str,
        outputs: list[str],
    ) -> RoutineRun:
        return await settlement.report(
            self,
            run=run,
            by=by,
            status=status,
            summary=summary,
            outputs=outputs,
        )
