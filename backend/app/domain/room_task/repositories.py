"""Task data access — a room's threads, the conversation in each, and the trees
those threads work on."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block, BlockKind
from app.domain.room_task.models import Residency, Task, TreeStatus, WorkTree


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

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        tree_id: uuid.UUID | None = None,
    ) -> WorkTree:
        """Start a batch.

        `tree_id` exists for the room's FIRST tree, which carries the room's own
        id so that every branch, worktree directory, jj workspace, container
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

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        tree_id: uuid.UUID,
        title: str,
        owner_handle: str | None,
        created_by: str | None,
        agent_instance_id: uuid.UUID | None,
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
            agent_instance_id=agent_instance_id,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def count_resident(self, room_id: uuid.UUID) -> int:
        """How many of this room's slots are in use right now."""
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(Task.room_id == room_id, Task.residency == Residency.running)
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def next_queued(self, room_id: uuid.UUID) -> Task | None:
        """The queued task that has been waiting longest, if any."""
        stmt = (
            select(Task)
            .where(Task.room_id == room_id, Task.queued_at.is_not(None))
            .order_by(Task.queued_at, Task.id)
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

    async def list_queued(self, room_id: uuid.UUID) -> list[Task]:
        """Everything waiting for a slot in this room, longest wait first."""
        stmt = (
            select(Task)
            .where(Task.room_id == room_id, Task.queued_at.is_not(None))
            .order_by(Task.queued_at, Task.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_resident(self, room_id: uuid.UUID) -> list[Task]:
        """Who is holding this room's slots — so a full room can say WHO."""
        stmt = (
            select(Task)
            .where(Task.room_id == room_id, Task.residency == Residency.running)
            .order_by(Task.last_turn_at, Task.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_stale_resident(self, older_than: datetime) -> list[Task]:
        """Tasks marked running whose last turn is too old to still be going.

        A backend that dies mid-turn leaves the row saying `running` forever,
        and that row holds a slot nobody can see or free. Materialised residency
        is the price of surviving a restart; this is the other half of it.
        """
        stmt = select(Task).where(
            Task.residency == Residency.running,
            Task.last_turn_at.is_not(None),
            Task.last_turn_at < older_than,
        )
        return list((await self._session.scalars(stmt)).all())

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
