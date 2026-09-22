from datetime import UTC, datetime

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.teaching.models import TeachingUnit


class TeachingUnitRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_space(
        self, *, space_id: int, published_only: bool, now: datetime | None = None
    ) -> list[TeachingUnit]:
        """This board's units, in course order.

        ``published_only`` is the student's view and the same query an agent
        context would use: what has not been published does not exist yet. It is
        an argument here rather than a filter at the call site so that "students
        see published units" has exactly one implementation.
        """
        stmt: Select[tuple[TeachingUnit]] = (
            select(TeachingUnit)
            .where(
                TeachingUnit.space_id == space_id,
                TeachingUnit.deleted_at.is_(None),
            )
            .order_by(TeachingUnit.week, TeachingUnit.id)
        )
        if published_only:
            stmt = stmt.where(TeachingUnit.published_at <= (now or datetime.now(UTC)))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_in_space(self, *, space_id: int, unit_id: int) -> TeachingUnit | None:
        stmt: Select[tuple[TeachingUnit]] = select(TeachingUnit).where(
            TeachingUnit.id == unit_id,
            TeachingUnit.space_id == space_id,
            TeachingUnit.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        space_id: int,
        week: int,
        title: str,
        summary: str,
        knowledge_point_ids: list[int],
        material_ids: list[int],
        assignment_task_id: int | None,
        published_at: datetime | None,
        due_at: datetime | None,
        created_by: int,
    ) -> TeachingUnit:
        now = datetime.now(UTC)
        unit = TeachingUnit(
            space_id=space_id,
            week=week,
            title=title,
            summary=summary,
            knowledge_point_ids=knowledge_point_ids,
            material_ids=material_ids,
            assignment_task_id=assignment_task_id,
            published_at=published_at,
            due_at=due_at,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(unit)
        await self._session.flush()
        return unit

    async def update(self, *, unit: TeachingUnit, **fields: object) -> TeachingUnit:
        for name, value in fields.items():
            setattr(unit, name, value)
        unit.updated_at = datetime.now(UTC)
        await self._session.flush()
        return unit

    async def soft_delete(self, *, unit: TeachingUnit) -> None:
        unit.deleted_at = datetime.now(UTC)
        await self._session.flush()
