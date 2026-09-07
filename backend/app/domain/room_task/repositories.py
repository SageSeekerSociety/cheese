"""Task data access — a room's threads, the conversation in each, and the trees
those threads work on."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block, BlockKind
from app.domain.room_task.models import (
    Task,
    TaskStatus,
    TreeStatus,
    WorkTree,
)


class WorkTreeRepository:
    """一棵树 = 一个分支 = 一个 PR = 一批活."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, tree_id: uuid.UUID) -> WorkTree | None:
        return await self._session.get(WorkTree, tree_id)

    async def open_tree_for_room(self, room_id: uuid.UUID) -> WorkTree | None:
        """The tree this room is currently taking work into, if any.

        None means the room is sealed — its tree's PR is in flight and the next
        batch has not been started yet. That is a real state, not a missing row:
        it is what stops a task from writing into a PR that CI is checking.
        """
        stmt = select(WorkTree).where(
            WorkTree.room_id == room_id, WorkTree.status == TreeStatus.open
        )
        return (await self._session.scalars(stmt)).first()

    async def list_for_room(self, room_id: uuid.UUID) -> list[WorkTree]:
        """Every tree this room has had, oldest first."""
        stmt = (
            select(WorkTree)
            .where(WorkTree.room_id == room_id)
            .order_by(WorkTree.created_at, WorkTree.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_for_project(self, project_id: uuid.UUID) -> list[WorkTree]:
        """Every tree of every room in the project — the set whose directories
        the storage sweep may find on disk."""
        stmt = select(WorkTree).where(WorkTree.project_id == project_id)
        return list((await self._session.scalars(stmt)).all())

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        tree_id: uuid.UUID | None = None,
    ) -> WorkTree:
        """Start a batch.

        `tree_id` exists for the room's FIRST tree, which carries the room's own
        id so that every branch, worktree directory, container
        workdir and tmux session keeps the name it already had. Every later tree
        gets a fresh id and therefore a fresh branch.
        """
        tree = WorkTree(project_id=project_id, room_id=room_id, status=TreeStatus.open)
        if tree_id is not None:
            tree.id = tree_id
        self._session.add(tree)
        await self._session.flush()
        return tree

    async def seal(self, tree: WorkTree) -> WorkTree:
        """封口: its PR is in flight, so nothing new may be written here."""
        tree.status = TreeStatus.sealed
        tree.sealed_at = datetime.now(UTC)
        await self._session.flush()
        return tree

    async def mark_merged(self, tree: WorkTree) -> WorkTree:
        tree.status = TreeStatus.merged
        tree.merged_at = datetime.now(UTC)
        await self._session.flush()
        return tree

    async def list_tasks(self, tree_id: uuid.UUID) -> list[Task]:
        """The batch: every task working on this tree, oldest first."""
        stmt = (
            select(Task)
            .where(Task.tree_id == tree_id)
            .order_by(Task.created_at, Task.id)
        )
        return list((await self._session.scalars(stmt)).all())


class TaskRepository:
    # Same exclusions the topic timeline uses: doc nodes, inline comments and
    # artifacts belong to the document view, not to the conversation.
    _NON_TIMELINE = (BlockKind.doc_node, BlockKind.comment, BlockKind.artifact)

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._session.get(Task, task_id)

    async def mark_transcripts_archived(self, task_id: uuid.UUID, at: datetime) -> bool:
        """Record that the thread's raw session files reached the platform.
        False when no thread has this id."""
        stamped = await self._session.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(transcripts_archived_at=at)
            .returning(Task.id)
        )
        return stamped.scalar() is not None

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        tree_id: uuid.UUID,
        title: str,
        owner_handle: str | None,
        created_by: str | None,
    ) -> Task:
        """A new thread in *room_id*, working on *tree_id*.

        The tree is passed in rather than looked up here: which tree a task
        joins is a decision (the room's currently open one, and a sealed room
        has none), and a repository that made it would be making it silently at
        the moment of writing rather than where it can be explained.
        """
        task = Task(
            project_id=project_id,
            room_id=room_id,
            tree_id=tree_id,
            title=title,
            owner_handle=owner_handle,
            created_by=created_by,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def open_by_subagent(
        self, room_id: uuid.UUID, subagent_id: str
    ) -> Task | None:
        """The open thread in *room_id* this worker is doing, if any.

        `open` is part of the question, not a filter on the answer: a worker id
        is only meaningful while the work is live, and a finished thread that
        kept its id would silently swallow the events of whatever came after it.

        Newest first so that even if a stale binding somehow survived, the
        events land on the work that is actually going on.
        """
        stmt = (
            select(Task)
            .where(
                Task.room_id == room_id,
                Task.subagent_id == subagent_id,
                Task.status == TaskStatus.open,
            )
            .order_by(Task.created_at.desc(), Task.id)
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

    async def list_for_room(self, room_id: uuid.UUID) -> list[Task]:
        """This room's threads, oldest first.

        Ties on created_at break by id so the order is total — the backfill
        stamps a whole project's tasks from `topics.created_at`, and rows that
        were created in the same instant must still come back in one fixed
        order rather than whatever the planner felt like.
        """
        stmt = (
            select(Task)
            .where(Task.room_id == room_id)
            .order_by(Task.created_at, Task.id)
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

    async def last_block_at_for_tasks(
        self, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """每条活最后一次说话是什么时候，一次查完 —— 看板的心跳。

        `Task.last_turn_at` 只在一轮**开始**时盖一次，跑起来之后不再刷新，所以它
        回答不了「这一轮现在还在动吗」。一轮里每一步都会落 block，这才是持续的
        信号：在这条活自己身上实测，一轮之内 block 间隔中位数 8 秒、p90 34 秒。

        便宜：`ix_blocks_task_id_created_at` 就是为 (task_id, created_at) 建的
        部分索引，所以这是一次按索引取每组最大值，和侧栏每次都要跑的
        `last_activity_for_topics` 同一个成本量级。没说过话的活直接不在结果里，
        由调用方决定它意味着什么 —— 这里不替它编一个时间。

        放在 task 这边而不是 block 那边：问的是「这条活还活着吗」，主语是活。
        """
        if not task_ids:
            return {}
        stmt = (
            select(Block.task_id, func.max(Block.created_at))
            .where(Block.task_id.in_(task_ids))
            .group_by(Block.task_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {task_id: last for task_id, last in rows if task_id is not None}

    async def conversations_for_tasks(
        self, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[Block]]:
        """Every thread's conversation, oldest first, in ONE query.

        Keyed by task id and batched deliberately: the caller wants a whole
        room, and a room can hold hundreds of threads — asking per thread turns
        opening a room into hundreds of round trips. Tasks with nothing said in
        them are simply absent from the result, so the caller supplies the
        empty list rather than this doing a second pass to invent one.
        """
        if not task_ids:
            return {}
        stmt = (
            select(Block)
            .where(
                Block.task_id.in_(task_ids),
                Block.kind.not_in(self._NON_TIMELINE),
            )
            .order_by(Block.created_at, Block.id)
        )
        grouped: dict[uuid.UUID, list[Block]] = {}
        for block in (await self._session.scalars(stmt)).all():
            # Narrowing, not a filter: `task_id` is what the query selected on,
            # so it is never None here — this is how the type says so.
            if (task_id := block.task_id) is not None:
                grouped.setdefault(task_id, []).append(block)
        return grouped
