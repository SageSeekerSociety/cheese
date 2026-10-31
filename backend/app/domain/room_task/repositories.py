"""Task data access — a room's threads, the conversation in each, and the trees
those threads work on."""

import uuid
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import (
    Row,
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

from app.core.request_facts import recall, remember
from app.domain.block.models import Block, BlockKind
from app.domain.project.address import Numbered, take_number
from app.domain.room_task.models import (
    Task,
    TaskProgress,
    TaskStatus,
    TaskTitleSource,
    people_of,
)


def _can_cache(session: object) -> bool:
    return isinstance(session, AsyncSession)


def _uuids(name: str, values: Collection[uuid.UUID] | None):
    return bindparam(name, list(values or ()), type_=ARRAY(Uuid()))


@dataclass(frozen=True, slots=True)
class ListedTask:
    """一条活摆上清单时要读的那几列，加上它最后一次说话的时刻 —— 一份纯值。

    看板和「待我处理」读一条活读的是状态、人、日期那几位，而 `Task` 那一行还背着
    交付记录、PR、执行环境、检查输出那一堆正文，装配一行比读这几列贵得多
    （`tests/integration/test_indexed_rows.py` 量了同一批行的两种读法）。所以这一份
    是**列投影交出来的值**，没有 session、没有懒加载：读得到什么，这里就列着什么。

    `last_said_at`（看板的心跳）和这几列一起回来，是因为它本来要单独问一趟
    （`last_block_at_for_tasks`）；两笔一起取，少一次往返，也少一次把活按 id 收窄。
    `None` 是「这条活还没人说过话」，由调用方决定它意味着什么 —— 这里不替它编一个
    时间。
    """

    id: uuid.UUID
    project_id: uuid.UUID
    room_id: uuid.UUID
    title: str
    title_source: TaskTitleSource
    status: TaskStatus
    progress_state: TaskProgress | None
    #: 报那个状态的座位、和它是在那个座位的哪一次跑里报的：那位队友在另一次跑里
    #: 跑着，那句话就让位（`presentation`）。
    progress_set_by: str | None
    progress_run_harness: str | None
    progress_run_epoch: int | None
    progress_run_seq: int | None
    #: 队友报那个状态时附的一句（`Task.progress_note`）：「待我处理」里卡住那一行
    #: 的第二行说的就是它要你做什么。
    progress_note: str | None
    #: 做它的队友坐的那个座位（`Task.agent_handle`）；空的时候是频道的那位。
    agent_handle: str | None
    owner_handle: str | None
    reporter_handle: str | None
    contributor_handles: tuple[str, ...]
    accepted_at: datetime | None
    conclusion: str | None
    closing_since: datetime | None
    updated_at: datetime
    last_said_at: datetime | None

    @property
    def people(self) -> tuple[str, ...]:
        """等谁 —— 和 `Task.people` 是同一份名单（`models.people_of`）。"""
        return people_of(self.owner_handle, self.contributor_handles)


class TaskRepository:
    # Artifacts are in the room's timeline, but a card's timeline has no way to
    # show one yet, so it leaves them out rather than send rows nothing renders.
    _NON_TIMELINE = (BlockKind.artifact,)

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, task_id: uuid.UUID) -> Task | None:
        if _can_cache(self._session):
            if self._session.autoflush:
                await self._session.flush()
            found, cached = recall(self._session, ("task_row", task_id))
            if found:
                return cached
        task = await self._session.get(Task, task_id)
        if _can_cache(self._session):
            remember(
                self._session,
                ("task_row", task_id),
                task,
                tables=frozenset({"tasks"}),
            )
        return task

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
        task_id: uuid.UUID | None = None,
    ) -> Task:
        """Create a task; its service assigns the branch before checkout."""
        task = Task(
            **({"id": task_id} if task_id is not None else {}),
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
        — the caller (`Link:`) has a list somebody wrote down, and a row
        that has since been deleted is a line it cannot write, not an error."""
        if not task_ids:
            return []
        if _can_cache(self._session):
            if self._session.autoflush:
                await self._session.flush()
            cached_tasks: list[Task] = []
            missing: list[uuid.UUID] = []
            for tid in dict.fromkeys(task_ids):
                found, cached = recall(self._session, ("task_row", tid))
                if found:
                    if cached is not None:
                        cached_tasks.append(cached)
                else:
                    missing.append(tid)
            if not missing:
                cached_tasks.sort(key=lambda t: (t.created_at, t.id))
                return cached_tasks
            stmt = (
                select(Task)
                .where(Task.id.in_(missing))
                .order_by(Task.created_at, Task.id)
            )
            fetched = list((await self._session.scalars(stmt)).all())
            by_id = {t.id: t for t in fetched}
            for tid in missing:
                remember(
                    self._session,
                    ("task_row", tid),
                    by_id.get(tid),
                    tables=frozenset({"tasks"}),
                )
            all_tasks = cached_tasks + fetched
            all_tasks.sort(key=lambda t: (t.created_at, t.id))
            return all_tasks
        stmt = (
            select(Task).where(Task.id.in_(task_ids)).order_by(Task.created_at, Task.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def listed_by_ids(self, task_ids: Collection[uuid.UUID]) -> list[ListedTask]:
        """These threads as a listing draws them, each with its last word's time.

        One query for what used to be two: the rows a listing reads
        (`list_by_ids`) plus the heartbeat (`last_block_at_for_tasks`). The
        heartbeat rides along as a correlated `max(created_at)` on this task's
        own conversation — the planner turns that into one backwards step on
        `ix_blocks_conversation_created_at` per task, so its cost follows the
        number of tasks, not how much they have said (see
        `last_block_at_for_tasks` for why not a GROUP BY).

        A column projection rather than the entity: 「待我处理」 takes a few
        hundred threads at once, and `Task` carries delivery records, the
        environment, check output — text and JSONB a listing never reads. Ids
        that name nothing are simply absent, as in `list_by_ids`.

        The ids go in as one array parameter (`_uuids`): a few hundred of them
        spelled as an `IN` list make the driver re-spell the statement every
        time it runs.
        """
        ids = list(task_ids)
        if not ids:
            return []
        last_said = (
            select(func.max(Block.created_at))
            .where(Block.conversation_id == Task.id)
            .scalar_subquery()
        )
        stmt = (
            select(
                Task.id,
                Task.project_id,
                Task.room_id,
                Task.title,
                Task.title_source,
                Task.status,
                Task.progress_state,
                Task.progress_set_by,
                Task.progress_run_harness,
                Task.progress_run_epoch,
                Task.progress_run_seq,
                Task.progress_note,
                Task.agent_handle,
                Task.owner_handle,
                Task.reporter_handle,
                Task.contributor_handles,
                Task.accepted_at,
                Task.conclusion,
                Task.closing_since,
                Task.updated_at,
                last_said.label("last_said_at"),
            )
            .where(Task.id == any_(_uuids("task_ids", ids)))
            .order_by(Task.created_at, Task.id)
        )
        return [
            ListedTask(
                id=row.id,
                project_id=row.project_id,
                room_id=row.room_id,
                title=row.title,
                title_source=row.title_source,
                status=row.status,
                progress_state=row.progress_state,
                progress_set_by=row.progress_set_by,
                progress_run_harness=row.progress_run_harness,
                progress_run_epoch=row.progress_run_epoch,
                progress_run_seq=row.progress_run_seq,
                progress_note=row.progress_note,
                agent_handle=row.agent_handle,
                owner_handle=row.owner_handle,
                reporter_handle=row.reporter_handle,
                contributor_handles=tuple(row.contributor_handles or ()),
                accepted_at=row.accepted_at,
                conclusion=row.conclusion,
                closing_since=row.closing_since,
                updated_at=row.updated_at,
                last_said_at=row.last_said_at,
            )
            for row in (await self._session.execute(stmt)).all()
        ]

    async def _underway_for_rooms(
        self, room_ids: list[uuid.UUID]
    ) -> list[tuple[Task, datetime | None]]:
        """Every underway task in ``room_ids``, paired with its newest block's
        timestamp, most recently moved first — one query per request for the
        whole room batch.

        ``GET /topics`` and ``GET /topics/{id}`` ask five questions about the
        same set of underway tasks: which tasks exist per room
        (:meth:`tasks_of_rooms`, twice), how many per room
        (:meth:`underway_counts`), which ones the viewer takes part in
        (:meth:`underway_with`), which ones belong to others ordered by when
        they last moved (:meth:`underway_others`), and when each shown task last
        spoke (:meth:`last_block_at_for_tasks`). Answered from this one read,
        remembered in ``request_facts`` until the session writes.
        """
        if not room_ids:
            return []
        key = ("underway_for_rooms", tuple(room_ids))
        found, cached = recall(self._session, key)
        if found:
            return cached
        said = (
            select(func.max(Block.created_at))
            .where(Block.conversation_id == Task.id)
            .correlate(Task)
            .scalar_subquery()
        )
        stmt = (
            select(Task, said)
            .where(
                Task.room_id.in_(room_ids),
                Task.status == TaskStatus.open,
                Task.closing_since.is_(None),
            )
            .order_by(TaskRepository._moved().desc())
        )
        rows = [(task, at) for task, at in (await self._session.execute(stmt)).all()]
        remember(self._session, key, rows)
        found_last, last_map = recall(self._session, "task_last_block_at")
        merged_last = dict(last_map) if found_last else {}
        for task, at in rows:
            merged_last[task.id] = at
        remember(self._session, "task_last_block_at", merged_last)
        return rows

    async def underway_counts(self, room_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """How many tasks each of these rooms has underway: open and not in the
        window where a merged task is being written up before it closes. A room
        with none is absent."""
        if not room_ids:
            return {}
        found, rows = recall(self._session, ("underway_for_rooms", tuple(room_ids)))
        if found:
            counts: dict[uuid.UUID, int] = {}
            for task, _ in rows:
                counts[task.room_id] = counts.get(task.room_id, 0) + 1
            return counts
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
        rows = await self._underway_for_rooms(room_ids)
        return [
            task
            for task, _ in rows
            if task.owner_handle == person or person in (task.contributor_handles or [])
        ]

    async def underway_others(
        self, room_ids: list[uuid.UUID], person: str
    ) -> list[Task]:
        """The tasks underway in these rooms that ``person`` neither owns nor
        helps on, most recently moved first: the sidebar borrows the first few
        of them to bring a channel up to its floor of rows (`rail_tasks`).

        Ordered here rather than left to the caller: 一个项目里这样的活能有几百条，
        调用方按房间各取前几条，排序在库里做才不用把它们都读出来再排。
        """
        rows = await self._underway_for_rooms(room_ids)
        return [
            task
            for task, _ in rows
            if task.owner_handle != person
            and person not in (task.contributor_handles or [])
        ]

    async def tasks_of_rooms(
        self, room_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, uuid.UUID]:
        """{还有事的活: 它所在的房间} —— 和 `threads_of_rooms` 同一个形状。

        一个频道那一格认的是它全部的对话（`topics._awaiting_an_answer` 把它的任务和
        支线一起算），所以调用方要的是「这些房间里有哪些活」，不是活本身。「还有事」
        取的是和 `underway_counts` 同一个判据：开着、且还没进收尾窗口。
        """
        rows = await self._underway_for_rooms(room_ids)
        return {task.id: task.room_id for task, _ in rows}

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
          blocks — shown under one in the main line, or made from one said in
          a 支线 — either one matching is enough;
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
                    Task.source_block_id == any_(_uuids("sources", origins)),
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

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        accepted: bool | None = None,
        rooms: Collection[uuid.UUID] | None = None,
        status: str | None = None,
    ) -> list[Task]:
        """Every thread in the project, oldest first — the whole tree at once.

        The rail draws rooms AND the work in them, and a project here already
        holds ~170 rooms: asking each room for its threads is 170 round trips to
        paint one sidebar. Same total order as `list_for_room` so a room's
        threads read identically whichever call produced them.

        `rooms` and `status` narrow it the way a project-wide list does: the
        rooms the reader sees, and one state. A caller that shows only part of
        the tree gives both and lets the database drop the rest — the project
        rail shows 82 open threads out of 1,437 on dev, and it used to assemble
        all 1,437 and filter them in Python (2026-10-15,
        `.claude/scripts/sql_census.py`).
        """
        stmt = (
            select(Task)
            .where(Task.project_id == project_id)
            .order_by(Task.created_at, Task.id)
        )
        if rooms is not None:
            stmt = stmt.where(Task.room_id.in_(list(rooms)))
        if status is not None:
            stmt = stmt.where(Task.status == TaskStatus(status))
        stmt = self._accepted(stmt, accepted)
        return list((await self._session.scalars(stmt)).all())

    @staticmethod
    def _accepted(stmt, accepted: bool | None):
        """Delivery approval is independent of whether a task remains open."""
        if accepted is None:
            return stmt
        return stmt.where(
            Task.accepted_at.is_not(None) if accepted else Task.accepted_at.is_(None)
        )

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
        if whose == "mine":
            return Task.owner_handle == me
        if whose == "helping":
            return cast(Task.contributor_handles, JSONB).contains([me])
        return TaskRepository.not_taking_part(me)

    @staticmethod
    def not_taking_part(person: str):
        """这个人没沾过手的活：`takes_part` 的反面，`whose=others` 问的就是这一批，
        侧栏给频道补足到 3 条时借的也是这一批。

        `is_distinct_from` 而不是 `!=`：没负责人的活 `owner_handle` 是 NULL，`!=` 在
        那里给 NULL，取反还是 NULL，那一行会既不算「我的」也不算「别人的」。
        """
        return and_(
            Task.owner_handle.is_distinct_from(person),
            not_(cast(Task.contributor_handles, JSONB).contains([person])),
        )

    @staticmethod
    def takes_part(person: str):
        """这个人沾过手的活：他负责的，或他在协作者名单里的。

        `_whose` 正面的那两半合起来，单独起个名字是因为服务层也要按它筛 ——
        `TaskService.unseen_deliveries` 那条跨域的查询读的是另一个域的表，所以它
        在服务层（那里这个进口已经在契约的名单上），而这条判据只有这一份写法。
        """
        return or_(
            TaskRepository._whose("mine", person),
            TaskRepository._whose("helping", person),
        )

    def _in_view(
        self,
        stmt,
        project_id: uuid.UUID,
        *,
        rooms: Collection[uuid.UUID],
        status: str,
        channel: uuid.UUID | None,
        accepted: bool | None,
    ):
        stmt = self._accepted(stmt, accepted)
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
        accepted: bool | None = None,
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
            select(Task, moved),
            project_id,
            rooms=rooms,
            status=status,
            channel=channel,
            accepted=accepted,
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
        accepted: bool | None = None,
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
            select(*counted),
            project_id,
            rooms=rooms,
            status=status,
            channel=channel,
            accepted=accepted,
        )
        row = (await self._session.execute(stmt)).one()
        return dict(zip(("all", "mine", "helping", "others"), row, strict=True))

    async def brief_for_projects(
        self, project_ids: list[uuid.UUID], *, open_only: bool = False
    ) -> list[Row]:
        """跨若干项目的活，只取判「这件事可能点到谁」要用的那几列。

        「待我处理」问的是我能看见的全部项目，但它只显示点到我的那些行 —— 而一条
        活写得出我的名字，只可能是我提的、我负责的、我协力的、卡递给我的，或者有
        一道题问在我、或者落在这条活的人身上。这几件事都在这几列里躺着，所以在取
        整行实体之前先按它们收一次范围：本机 500 行实测，列投影 3.6ms，整行实体
        11.4ms，而一屏的代价本该跟着「点到我多少」走，不是「我看得见多少」。

        `underway`（还开着）也在其中：关了的活落「已结束」那一列，那一列的下一步在
        平台手上，它永远点不到任何人（`addressing.address`），而一个项目有
        史以来派出去的活十之九都关了（dev 2026-10-12：1437 条里 1355 条），不先按它
        收一次，后面每一步都在为早就结束的事付钱。给的是算好的一个布尔而不是状态
        列本身：状态的词表是这个领域的，路由那边不认它。

        收出来的**只是候选**（题问到谁那一笔要多查一步，见调用点），所以只多不少；
        真正算数的还是 `addressing` 那份判据。

        ``open_only`` 再收一次：判据只对还开着的活跑，关了的活下一步在平台手上，
        永远点不到任何人（`addressing.address`），所以对它们要的只是「这条
        对话在哪个频道」那一个映射 —— 那一笔由调用点单独按 id 去认（见
        `awaiting.waiting_items`），不必把整行的钱先付了。dev 2026-10-12：一个项目
        有史以来派出去的活十之九都关了（1437 条里 1355 条），开着的一屏只有一百来
        条；本机 13 个项目实测，这一笔从 10.9ms 降到 1ms 上下。
        """
        if not project_ids:
            return []
        stmt = (
            select(
                Task.id,
                Task.room_id,
                (Task.status == TaskStatus.open).label("underway"),
                Task.reporter_handle,
                Task.owner_handle,
                Task.contributor_handles,
            )
            .where(Task.project_id.in_(project_ids))
            .order_by(Task.created_at, Task.id)
        )
        if open_only:
            stmt = stmt.where(Task.status == TaskStatus.open)
        return list((await self._session.execute(stmt)).all())

    async def list_open(self) -> list[Task]:
        """全平台还开着的任务 —— 停滞提醒每一拍问它。"""
        stmt = select(Task).where(Task.status == TaskStatus.open)
        return list((await self._session.scalars(stmt)).all())

    async def last_block_at_for_tasks(
        self, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """每条活最后一次有人或芝士说话是什么时候，一次查完 —— 侧栏按它排「最近
        有动静」，安静提醒也读它。

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
        found_last, cached_last = recall(self._session, "task_last_block_at")
        known: dict[uuid.UUID, datetime | None] = cached_last if found_last else {}
        missing = [tid for tid in task_ids if tid not in known]
        if missing:
            last = (
                select(func.max(Block.created_at))
                .where(Block.conversation_id == Task.id)
                .scalar_subquery()
            )
            stmt = select(Task.id, last).where(Task.id.in_(missing))
            known = dict(known)
            for task_id, at in (await self._session.execute(stmt)).all():
                known[task_id] = at
            remember(self._session, "task_last_block_at", known)
        return {
            task_id: at
            for task_id in task_ids
            if (at := known.get(task_id)) is not None
        }

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
