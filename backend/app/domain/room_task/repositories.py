"""Task data access — a room's threads, the conversation in each, and the trees
those threads work on."""

import uuid
from collections.abc import Collection
from datetime import datetime

from sqlalchemy import (
    Uuid,
    and_,
    any_,
    bindparam,
    cast,
    func,
    not_,
    or_,
    select,
    tuple_,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block, BlockKind
from app.domain.project.address import Numbered, take_number
from app.domain.room_task.models import Task, TaskStatus, TaskTitleSource


def _uuids(name: str, values: Collection[uuid.UUID] | None):
    return bindparam(name, list(values or ()), type_=ARRAY(Uuid()))


class TaskRepository:
    # Artifacts are in the room's timeline, but a card's timeline has no way to
    # show one yet, so it leaves them out rather than send rows nothing renders.
    _NON_TIMELINE = (BlockKind.artifact,)

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._session.get(Task, task_id)

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        title: str,
        owner_handle: str | None,
        created_by: str | None,
        reviewer_handle: str | None = None,
        reporter_handle: str | None = None,
        contributor_handles: list[str] | None = None,
        title_source: TaskTitleSource = TaskTitleSource.human,
    ) -> Task:
        """Create a task; its service assigns the branch before checkout."""
        task = Task(
            project_id=project_id,
            room_id=room_id,
            number=await take_number(self._session, project_id, Numbered.task),
            title=title,
            title_source=title_source,
            owner_handle=owner_handle,
            reviewer_handle=reviewer_handle,
            reporter_handle=reporter_handle,
            contributor_handles=contributor_handles or [],
            created_by=created_by,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def list_by_ids(self, task_ids: list[uuid.UUID]) -> list[Task]:
        """These threads, oldest first. Ids that name nothing are simply absent
        — the caller (`Cheese-Task:`) has a list somebody wrote down, and a row
        that has since been deleted is a line it cannot write, not an error."""
        if not task_ids:
            return []
        stmt = (
            select(Task).where(Task.id.in_(task_ids)).order_by(Task.created_at, Task.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def open_counts(self, room_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """How many open tasks each of these rooms has, in one read. A room
        with none is absent."""
        if not room_ids:
            return {}
        stmt = (
            select(Task.room_id, func.count(Task.id))
            .where(Task.room_id.in_(room_ids), Task.status == TaskStatus.open)
            .group_by(Task.room_id)
        )
        return {room: n for room, n in (await self._session.execute(stmt)).all()}

    async def underway_counts(self, room_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """How many tasks each of these rooms has underway: open and not in the
        window where a merged task is being written up before it closes. A room
        with none is absent."""
        if not room_ids:
            return {}
        stmt = (
            select(Task.room_id, func.count(Task.id))
            .where(
                Task.room_id.in_(room_ids),
                Task.status == TaskStatus.open,
                Task.closing_since.is_(None),
            )
            .group_by(Task.room_id)
        )
        return {room: n for room, n in (await self._session.execute(stmt)).all()}

    async def underway_with(self, room_ids: list[uuid.UUID], person: str) -> list[Task]:
        """The tasks underway in these rooms that ``person`` owns or helps on."""
        if not room_ids:
            return []
        stmt = select(Task).where(
            Task.room_id.in_(room_ids),
            Task.status == TaskStatus.open,
            Task.closing_since.is_(None),
            or_(Task.owner_handle == person, self._whose("helping", person)),
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_for_room(
        self,
        room_id: uuid.UUID,
        *,
        status: str | None = None,
        ids: Collection[uuid.UUID] | None = None,
        origins: Collection[uuid.UUID] | None = None,
        with_branch: bool = False,
        latest: int | None = None,
    ) -> list[Task]:
        """This room's threads, oldest first, narrowed:

        - ``status``: only open, or only closed;
        - ``ids`` / ``origins``: only these tasks, or the ones made from these
          blocks — either one matching is enough;
        - ``with_branch``: only tasks that have a branch of their own;
        - ``latest``: only the newest N — by when they closed for closed tasks,
          by when they were created otherwise — newest first.

        Ties on created_at break by id so the order is total — the backfill
        stamps a whole project's tasks from `topics.created_at`, and rows that
        were created in the same instant must still come back in one fixed
        order rather than whatever the planner felt like.
        """
        stmt = select(Task).where(Task.room_id == room_id)
        if status is not None:
            stmt = stmt.where(Task.status == TaskStatus(status))
        if ids is not None or origins is not None:
            # One array parameter each, not one per id: a whole timeline's
            # blocks (a room read without a limit) can be tens of thousands,
            # past the driver's 32,767 parameters a statement.
            stmt = stmt.where(
                or_(
                    Task.id == any_(_uuids("ids", ids)),
                    Task.upgraded_from_block_id == any_(_uuids("origins", origins)),
                )
            )
        if with_branch:
            stmt = stmt.where(Task.branch_name.is_not(None))
        if latest is None:
            stmt = stmt.order_by(Task.created_at, Task.id)
        else:
            moment = Task.closed_at if status == TaskStatus.closed else Task.created_at
            stmt = stmt.order_by(moment.desc().nulls_last(), Task.id.desc()).limit(
                latest
            )
        return list((await self._session.scalars(stmt)).all())

    async def list_for_project(self, project_id: uuid.UUID) -> list[Task]:
        """Every thread in the project, oldest first — the whole tree at once.

        The rail draws rooms AND the work in them, and a project here already
        holds ~170 rooms: asking each room for its threads is 170 round trips to
        paint one sidebar. Same total order as `list_for_room` so a room's
        threads read identically whichever call produced them.
        """
        stmt = (
            select(Task)
            .where(Task.project_id == project_id)
            .order_by(Task.created_at, Task.id)
        )
        return list((await self._session.scalars(stmt)).all())

    @staticmethod
    def _moved():
        """When a task last moved: its newest block, or its creation."""
        said = (
            select(func.max(Block.created_at))
            .where(Block.conversation_id == Task.id)
            .scalar_subquery()
        )
        return func.coalesce(said, Task.created_at)

    @staticmethod
    def _whose(whose: str, me: str):
        mine = Task.owner_handle == me
        helping = cast(Task.contributor_handles, JSONB).contains([me])
        if whose == "mine":
            return mine
        if whose == "helping":
            return helping
        return and_(Task.owner_handle.is_distinct_from(me), not_(helping))

    def _in_view(
        self,
        stmt,
        project_id: uuid.UUID,
        *,
        rooms: Collection[uuid.UUID],
        status: str,
        channel: uuid.UUID | None,
    ):
        stmt = stmt.where(
            Task.project_id == project_id,
            Task.room_id.in_(list(rooms)),
            Task.status == TaskStatus(status),
        )
        return stmt if channel is None else stmt.where(Task.room_id == channel)

    async def page_for_project(
        self,
        project_id: uuid.UUID,
        *,
        rooms: Collection[uuid.UUID],
        status: str,
        channel: uuid.UUID | None,
        whose: str | None,
        me: str,
        limit: int,
        before: tuple[datetime, uuid.UUID] | None,
    ) -> tuple[list[tuple[Task, datetime]], bool]:
        """One page of the project's tasks in ``status``, the most recently moved
        first, each with when it last moved; and whether more follow.

        Narrowed to the rooms the reader sees, to one ``channel``, and to
        ``whose`` they are to ``me``: ``mine`` (I own it), ``helping`` (I am
        among its contributors) or ``others`` (neither). ``before`` is the
        (moved, id) of the last row of the previous page: (moved, id) is a total
        order, so a page never repeats or skips a row that ties on time.
        """
        moved = self._moved()
        stmt = self._in_view(
            select(Task, moved), project_id, rooms=rooms, status=status, channel=channel
        )
        if whose is not None:
            stmt = stmt.where(self._whose(whose, me))
        if before is not None:
            stmt = stmt.where(tuple_(moved, Task.id) < before)
        stmt = stmt.order_by(moved.desc(), Task.id.desc()).limit(limit + 1)
        rows = [(task, at) for task, at in (await self._session.execute(stmt)).all()]
        return rows[:limit], len(rows) > limit

    async def counts_for_project(
        self,
        project_id: uuid.UUID,
        *,
        rooms: Collection[uuid.UUID],
        status: str,
        channel: uuid.UUID | None,
        me: str,
    ) -> dict[str, int]:
        """How many of the project's tasks in ``status`` there are, in all and
        by whose they are to ``me``, narrowed as `page_for_project` is."""
        counted = [
            func.count(),
            *(
                func.count().filter(self._whose(whose, me))
                for whose in ("mine", "helping", "others")
            ),
        ]
        stmt = self._in_view(
            select(*counted), project_id, rooms=rooms, status=status, channel=channel
        )
        row = (await self._session.execute(stmt)).one()
        return dict(zip(("all", "mine", "helping", "others"), row, strict=True))

    async def list_for_projects(self, project_ids: list[uuid.UUID]) -> list[Task]:
        """同一个列表，跨若干个项目 —— 「待我处理」要问的是我能看见的全部项目。

        逐个项目问一遍是一个人几个项目就几次往返；而这个列表是一个页面打开就要的
        东西，所以它一次问完。
        """
        if not project_ids:
            return []
        stmt = (
            select(Task)
            .where(Task.project_id.in_(project_ids))
            .order_by(Task.created_at, Task.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_open(self) -> list[Task]:
        """全平台还开着的任务 —— 停滞提醒每一拍问它。"""
        stmt = select(Task).where(Task.status == TaskStatus.open)
        return list((await self._session.scalars(stmt)).all())

    async def last_block_at_for_tasks(
        self, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """每条活最后一次说话是什么时候，一次查完 —— 看板的心跳。

        `Task.last_turn_at` 只在一轮**开始**时盖一次，跑起来之后不再刷新，所以它
        回答不了「这一轮现在还在动吗」。一轮里每一步都会落 block，这才是持续的
        信号：在这条活自己身上实测，一轮之内 block 间隔中位数 8 秒、p90 34 秒。

        每条活单独取一次最大值：PostgreSQL 会把它变成在
        `ix_blocks_conversation_created_at` 上倒着读一行，所以成本跟着活的条数走，跟
        这些活说过多少话无关。写成对 block 的一个 GROUP BY 是同一个答案，但没有
        哪个计划能按组只读最新一行，它就把这些活的每一个 block 都读一遍 —— 在
        dev 上是一次读全表的并行扫描。没说过话的活直接不在结果里，由调用方决定
        它意味着什么 —— 这里不替它编一个时间。

        放在 task 这边而不是 block 那边：问的是「这条活还活着吗」，主语是活。
        """
        if not task_ids:
            return {}
        last = (
            select(func.max(Block.created_at))
            .where(Block.conversation_id == Task.id)
            .scalar_subquery()
        )
        stmt = select(Task.id, last).where(Task.id.in_(task_ids))
        rows = (await self._session.execute(stmt)).all()
        return {task_id: at for task_id, at in rows if at is not None}

    async def conversations_for_tasks(
        self, task_ids: list[uuid.UUID], *, limit: int | None = None
    ) -> dict[uuid.UUID, list[Block]]:
        """Every thread's conversation, oldest first, in ONE query; with
        `limit`, each thread's newest `limit` blocks.

        Keyed by task id and batched deliberately: the caller wants a whole
        room, and a room can hold hundreds of threads — asking per thread turns
        opening a room into hundreds of round trips. Tasks with nothing said in
        them are simply absent from the result, so the caller supplies the
        empty list rather than this doing a second pass to invent one.
        """
        if not task_ids:
            return {}
        where = (
            Block.conversation_id.in_(task_ids),
            Block.kind.not_in(self._NON_TIMELINE),
        )
        if limit is None:
            stmt = select(Block).where(*where).order_by(Block.created_at, Block.id)
        else:
            # Each thread's newest `limit`, cut in the database: a room's
            # threads together can hold hundreds of thousands of blocks.
            newest = (
                select(
                    Block.id,
                    func.row_number()
                    .over(
                        partition_by=Block.conversation_id,
                        order_by=(Block.created_at.desc(), Block.id.desc()),
                    )
                    .label("rank"),
                )
                .where(*where)
                .subquery()
            )
            stmt = (
                select(Block)
                .join(newest, newest.c.id == Block.id)
                .where(newest.c.rank <= limit)
                .order_by(Block.created_at, Block.id)
            )
        grouped: dict[uuid.UUID, list[Block]] = {}
        for block in (await self._session.scalars(stmt)).all():
            grouped.setdefault(block.conversation_id, []).append(block)
        return grouped
