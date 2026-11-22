"""学情分析的两张行：这块板的知识点清单，和谁读过那张告知卡。

Split out of ``app/domain/space/repositories.py`` so that module keeps one
reason to change — a 知识点 belongs to the board and only this feature reads it,
and the 告知卡 record is append-only, so neither is touched by the space
domain's other writes. Both classes are re-exported from ``repositories``, so
``from app.domain.space.repositories import SpaceKnowledgePointRepository``
resolves like every other repository of this domain.

The 题目↔知识点 link (``app.domain.task.models.TaskKnowledgePoint``) is written
with the 题目 — a 题目's own list is replaced by ``TaskRepository`` in
``app.domain.task.repositories``. **Reading** it back out as 知识点 rows is here,
not there: it joins the link to this domain's ``SpaceKnowledgePoint``, and the
``space -> task`` direction is an edge the tree already has (``analytics_*``,
``learning_service``, …) while the reverse is one ``domains-acyclic`` names as a
cycle to cut. So the join lives on the side that already looks this way.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import SpaceKnowledgePoint, SpaceLearningNotice
from app.domain.task.models import TaskKnowledgePoint


class SpaceKnowledgePointRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_space(self, space_id: int) -> Sequence[SpaceKnowledgePoint]:
        """The board's live list, in the order the settings page shows it."""
        stmt: Select[tuple[SpaceKnowledgePoint]] = (
            select(SpaceKnowledgePoint)
            .where(
                SpaceKnowledgePoint.space_id == space_id,
                SpaceKnowledgePoint.deleted_at.is_(None),
            )
            .order_by(
                SpaceKnowledgePoint.display_order.asc(),
                SpaceKnowledgePoint.id.asc(),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, *, space_id: int, point_id: int) -> SpaceKnowledgePoint | None:
        """One live 知识点 of this board; a removed row is not one."""
        stmt: Select[tuple[SpaceKnowledgePoint]] = select(SpaceKnowledgePoint).where(
            SpaceKnowledgePoint.id == point_id,
            SpaceKnowledgePoint.space_id == space_id,
            SpaceKnowledgePoint.deleted_at.is_(None),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_by_ids(
        self, *, space_id: int, point_ids: Sequence[int]
    ) -> Sequence[SpaceKnowledgePoint]:
        """The live 知识点 of this board among ``point_ids``.

        A 题目 may only file under its own board's points, so the writer asks
        this and refuses ids that are not in the answer.
        """
        if not point_ids:
            return []
        stmt: Select[tuple[SpaceKnowledgePoint]] = select(SpaceKnowledgePoint).where(
            SpaceKnowledgePoint.id.in_(list(dict.fromkeys(point_ids))),
            SpaceKnowledgePoint.space_id == space_id,
            SpaceKnowledgePoint.deleted_at.is_(None),
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def name_taken(
        self, *, space_id: int, name: str, exclude_id: int | None = None
    ) -> bool:
        """Whether a live 知识点 of this board is already called ``name``.

        Asked before every write so a duplicate is a 400 with a sentence
        rather than the partial unique index's IntegrityError.
        """
        stmt = select(SpaceKnowledgePoint.id).where(
            SpaceKnowledgePoint.space_id == space_id,
            SpaceKnowledgePoint.name == name,
            SpaceKnowledgePoint.deleted_at.is_(None),
        )
        if exclude_id is not None:
            stmt = stmt.where(SpaceKnowledgePoint.id != exclude_id)
        return (await self._session.execute(stmt)).first() is not None

    async def next_display_order(self, space_id: int) -> int:
        """One past the last row's order — a new point goes to the end.

        Counts removed rows too: reusing an order a removed row holds would
        make the next re-add land in front of rows it should follow.
        """
        stmt = select(
            func.coalesce(func.max(SpaceKnowledgePoint.display_order), -1) + 1
        )
        value = await self._session.scalar(
            stmt.where(SpaceKnowledgePoint.space_id == space_id)
        )
        return int(value or 0)

    async def create(
        self, *, space_id: int, name: str, display_order: int
    ) -> SpaceKnowledgePoint:
        now = datetime.now(UTC)
        point = SpaceKnowledgePoint(
            space_id=space_id,
            name=name,
            display_order=display_order,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(point)
        await self._session.flush()
        return point

    async def update(
        self,
        *,
        point: SpaceKnowledgePoint,
        name: str | None = None,
        display_order: int | None = None,
    ) -> SpaceKnowledgePoint:
        if name is not None:
            point.name = name
        if display_order is not None:
            point.display_order = display_order
        point.updated_at = datetime.now(UTC)
        self._session.add(point)
        await self._session.flush()
        return point

    async def soft_delete(self, *, point: SpaceKnowledgePoint) -> None:
        """Remove it from the list, keep the row.

        Soft because a 题目 that already files under it still has to render the
        name (``SpaceKnowledgePoint`` says so); the partial unique index lets
        the same name be added back later.
        """
        now = datetime.now(UTC)
        point.deleted_at = now
        point.updated_at = now
        self._session.add(point)
        await self._session.flush()


class SpaceLearningNoticeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_acknowledged(self, *, space_id: int, user_id: int) -> bool:
        """Whether this person has already clicked 「我已阅读并知悉」 here.

        The card is shown exactly when this is False and the board has the
        analysis on, so that one question is the whole of 「弹不弹」.
        """
        stmt = select(SpaceLearningNotice.id).where(
            SpaceLearningNotice.space_id == space_id,
            SpaceLearningNotice.user_id == user_id,
        )
        return (await self._session.execute(stmt)).first() is not None

    async def acknowledge(self, *, space_id: int, user_id: int) -> None:
        """Record the click. Append-only, and a no-op the second time.

        The unique index decides, not a preceding read: two clicks do arrive
        together (a double tap), and ``ON CONFLICT DO NOTHING`` turns the
        second into nothing rather than a failure — the row that is there is
        the record either way, and ``acknowledged_at`` keeps the first moment.
        """
        now = datetime.now(UTC)
        await self._session.execute(
            pg_insert(SpaceLearningNotice)
            .values(
                space_id=space_id,
                user_id=user_id,
                acknowledged_at=now,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["space_id", "user_id"])
        )
        await self._session.flush()


class TaskKnowledgePointRepository:
    """一道题归在哪些知识点下（§ learning analysis, step 1）—— 只读。

    知识点本身是板上的行（``SpaceKnowledgePoint``），这里读的是「这道题连着哪几
    个」。软删掉的知识点不再出现在这道题的清单里 —— 与设置页那份「live」清单同一
    判据，只是这道题的那份关联行还留着。

    读这一半住在这里、写那一半（``TaskRepository.replace/…``）住在
    ``app.domain.task.repositories``：这个查询要 join 本域的 ``SpaceKnowledgePoint``，
    而 ``space -> task`` 是树里已有的方向（``domains-acyclic`` 只在反方向上有一刀要
    还）。写不 join 本域，只往 ``TaskKnowledgePoint`` 里插，所以留在题目那一域。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_task_id(self, task_id: int) -> Sequence[SpaceKnowledgePoint]:
        stmt: Select[tuple[SpaceKnowledgePoint]] = (
            select(SpaceKnowledgePoint)
            .join(
                TaskKnowledgePoint,
                TaskKnowledgePoint.knowledge_point_id == SpaceKnowledgePoint.id,
            )
            .where(
                TaskKnowledgePoint.task_id == task_id,
                SpaceKnowledgePoint.deleted_at.is_(None),
            )
            .order_by(
                SpaceKnowledgePoint.display_order.asc(),
                SpaceKnowledgePoint.id.asc(),
            )
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_by_task_ids(
        self, task_ids: Sequence[int]
    ) -> dict[int, list[SpaceKnowledgePoint]]:
        """一次取回多道题的知识点，按题目分组（组内仍按清单顺序排）。

        与 ``TopicRepository.list_by_task_ids`` 同一角色：列表页一屏一页题，逐题各查
        一次就是一屏 20 条查询。没有知识点的题不在结果里。
        """
        if not task_ids:
            return {}
        stmt: Select[tuple[int, SpaceKnowledgePoint]] = (
            select(TaskKnowledgePoint.task_id, SpaceKnowledgePoint)
            .join(
                SpaceKnowledgePoint,
                SpaceKnowledgePoint.id == TaskKnowledgePoint.knowledge_point_id,
            )
            .where(
                TaskKnowledgePoint.task_id.in_(list(task_ids)),
                SpaceKnowledgePoint.deleted_at.is_(None),
            )
            .order_by(
                TaskKnowledgePoint.task_id.asc(),
                SpaceKnowledgePoint.display_order.asc(),
                SpaceKnowledgePoint.id.asc(),
            )
        )
        result = await self._session.execute(stmt)
        grouped: dict[int, list[SpaceKnowledgePoint]] = {}
        for task_id, point in result.all():
            grouped.setdefault(int(task_id), []).append(point)
        return grouped
