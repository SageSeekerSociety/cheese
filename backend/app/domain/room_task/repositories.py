"""Task data access — a room's threads, and the conversation in each."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block, BlockKind
from app.domain.room_task.models import Task


class TaskRepository:
    # Same exclusions the topic timeline uses: doc nodes, inline comments and
    # artifacts belong to the document view, not to the conversation.
    _NON_TIMELINE = (BlockKind.doc_node, BlockKind.comment, BlockKind.artifact)

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._session.get(Task, task_id)

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
