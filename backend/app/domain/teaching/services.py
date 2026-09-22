"""教学单元的读写。

判「谁能改」不在这里 —— 那是 ``app.auth.space_access.is_space_admin`` 的事，路由
先过门再调这里。本服务只管内容对不对：周次是正整数、作业指向的题确实在同一个题目板
里、发布与撤回发布各自是什么语义。
"""

from datetime import UTC, datetime

from app.core.errors import BadRequestError, NotFoundError
from app.domain.task.services import TaskService
from app.domain.teaching.models import TeachingUnit
from app.domain.teaching.repositories import TeachingUnitRepository


class TeachingUnitService:
    def __init__(
        self,
        repo: TeachingUnitRepository,
        task_service: TaskService | None = None,
    ) -> None:
        self._repo = repo
        self._task_service = task_service

    async def list_units(
        self, *, space_id: int, published_only: bool
    ) -> list[TeachingUnit]:
        return await self._repo.list_for_space(
            space_id=space_id, published_only=published_only
        )

    async def create_unit(
        self,
        *,
        space_id: int,
        actor_id: int,
        week: int,
        title: str,
        summary: str = "",
        knowledge_point_ids: list[int] | None = None,
        material_ids: list[int] | None = None,
        assignment_task_id: int | None = None,
        published: bool = False,
        due_at: datetime | None = None,
    ) -> TeachingUnit:
        _require_week(week)
        _require_title(title)
        await self._ensure_assignment_is_here(
            space_id=space_id, assignment_task_id=assignment_task_id
        )
        return await self._repo.create(
            space_id=space_id,
            week=week,
            title=title,
            summary=summary,
            knowledge_point_ids=list(knowledge_point_ids or []),
            material_ids=list(material_ids or []),
            assignment_task_id=assignment_task_id,
            published_at=datetime.now(UTC) if published else None,
            due_at=due_at,
            created_by=actor_id,
        )

    async def update_unit(
        self,
        *,
        space_id: int,
        unit_id: int,
        week: int | None = None,
        title: str | None = None,
        summary: str | None = None,
        knowledge_point_ids: list[int] | None = None,
        material_ids: list[int] | None = None,
        assignment_task_id: int | None = None,
        clear_assignment: bool = False,
        published: bool | None = None,
        due_at: datetime | None = None,
        clear_due_at: bool = False,
    ) -> TeachingUnit:
        """``published`` is tri-state: None leaves it alone, True publishes,
        False takes it back. Publishing twice keeps the FIRST timestamp — what a
        student's week is anchored on is when the unit went out, not the last
        time somebody pressed the button."""
        unit = await self._get(space_id=space_id, unit_id=unit_id)
        fields: dict[str, object] = {}

        if week is not None:
            _require_week(week)
            fields["week"] = week
        if title is not None:
            _require_title(title)
            fields["title"] = title
        if summary is not None:
            fields["summary"] = summary
        if knowledge_point_ids is not None:
            fields["knowledge_point_ids"] = list(knowledge_point_ids)
        if material_ids is not None:
            fields["material_ids"] = list(material_ids)
        if assignment_task_id is not None:
            await self._ensure_assignment_is_here(
                space_id=space_id, assignment_task_id=assignment_task_id
            )
            fields["assignment_task_id"] = assignment_task_id
        elif clear_assignment:
            fields["assignment_task_id"] = None
        if due_at is not None:
            fields["due_at"] = due_at
        elif clear_due_at:
            fields["due_at"] = None
        if published is True and unit.published_at is None:
            fields["published_at"] = datetime.now(UTC)
        elif published is False:
            fields["published_at"] = None

        return await self._repo.update(unit=unit, **fields)

    async def delete_unit(self, *, space_id: int, unit_id: int) -> None:
        unit = await self._get(space_id=space_id, unit_id=unit_id)
        await self._repo.soft_delete(unit=unit)

    async def _get(self, *, space_id: int, unit_id: int) -> TeachingUnit:
        unit = await self._repo.get_in_space(space_id=space_id, unit_id=unit_id)
        if unit is None:
            raise NotFoundError("Teaching unit not found")
        return unit

    async def _ensure_assignment_is_here(
        self, *, space_id: int, assignment_task_id: int | None
    ) -> None:
        """The assignment is a real ``Task`` **of this board**.

        Without this a teacher could point a week at another course's problem,
        and the student's 「本周任务」 would then submit into a membership they
        do not have."""
        if assignment_task_id is None or self._task_service is None:
            return
        task = await self._task_service.get_task(assignment_task_id)
        if task is None:
            raise NotFoundError("Assignment task not found")
        if task.space_id != space_id:
            raise BadRequestError(
                "The assignment must be a problem of this board",
                data={"taskId": assignment_task_id},
            )


def _require_week(week: int) -> None:
    if week < 1:
        raise BadRequestError("A unit belongs to a week, and week 1 is the first")


def _require_title(title: str) -> None:
    if not title.strip():
        raise BadRequestError("A unit needs a title; it is what students see")
