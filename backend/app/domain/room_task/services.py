"""Task services — reading a room's threads."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import Block
from app.domain.room_task.models import Task
from app.domain.room_task.repositories import TaskRepository


class TaskService:
    def __init__(self, session: AsyncSession):
        self._repo = TaskRepository(session)

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._repo.get(task_id)

    async def open_thread(
        self,
        *,
        project_id: uuid.UUID,
        room_id: uuid.UUID,
        title: str,
        owner_handle: str | None,
        created_by: str | None,
        agent_instance_id: uuid.UUID | None,
    ) -> Task:
        """Open a new thread of work in a room.

        A service, not the repository, because the callers are in other domains
        (dispatch and 讨论升级 both live in `topic`), and a domain reaching into
        another's repository is what the import guard forbids.
        """
        return await self._repo.add(
            project_id=project_id,
            room_id=room_id,
            title=title,
            owner_handle=owner_handle,
            created_by=created_by,
            agent_instance_id=agent_instance_id,
        )

    async def threads_for_room(
        self, room_id: uuid.UUID, *, limit: int | None = None
    ) -> list[tuple[Task, list[Block]]]:
        """Every thread in this room, each with its conversation, oldest first.

        `limit` caps EACH thread at its newest N blocks — the same
        bottom-anchored slice a chat window wants, applied per thread rather
        than across the room, because a room where one thread ran long would
        otherwise starve every other thread of its opening line. None returns
        everything, matching `/topics/{id}/blocks`: an agent reading history
        must not be silently truncated.

        Threads with nothing said in them come back with an empty list, not
        missing — a task that exists and has not been talked in is a real
        answer, and dropping it would make the room's thread count depend on
        whether anyone had spoken yet.
        """
        tasks = await self._repo.list_for_room(room_id)
        conversations = await self._repo.conversations_for_tasks([t.id for t in tasks])
        out: list[tuple[Task, list[Block]]] = []
        for task in tasks:
            blocks = conversations.get(task.id, [])
            out.append((task, blocks[-limit:] if limit is not None else blocks))
        return out
