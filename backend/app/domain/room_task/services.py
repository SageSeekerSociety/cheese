"""Task services — reading a room's threads, and the trees they work on."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.block.models import Block
from app.domain.room_task.models import (
    HEAVY_LOCK_TTL,
    LockKind,
    RoomLock,
    Task,
    TaskStatus,
)
from app.domain.room_task.repositories import TaskRepository


class TaskService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TaskRepository(session)

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._repo.get(task_id)

    @staticmethod
    def _bind_workspace(task: Task) -> None:
        if task.branch_name:
            from app.domain.workspace import service as ws

            if task.workspace_name is None:
                raise ValidationError("任务分支缺少工作目录记录")
            if task.base_branch is None:
                task.base_branch, _ = ws.base_branch_head(task.project_id)
            ws.bind_task(
                task.id,
                branch=task.branch_name,
                directory=task.workspace_name,
                base=task.base_branch,
            )

    async def open_without_pr(self) -> list[Task]:
        rows = await self._session.scalars(
            select(Task).where(
                Task.status == TaskStatus.open,
                Task.branch_name.is_not(None),
                Task.pr_number.is_(None),
            )
        )
        return list(rows.all())

    async def claim_for_pr(self, task_id: uuid.UUID) -> Task | None:
        task = (
            await self._session.scalars(
                select(Task)
                .where(
                    Task.id == task_id,
                    Task.status == TaskStatus.open,
                    Task.branch_name.is_not(None),
                    Task.pr_number.is_(None),
                )
                .with_for_update(skip_locked=True)
            )
        ).first()
        if task is not None:
            self._bind_workspace(task)
        return task

    async def record_pr(self, task: Task, *, number: int, url: str | None) -> Task:
        task.pr_number, task.pr_url = number, url
        await self._session.flush()
        return task

    async def record_check(self, task: Task, *, ok: bool, detail: str) -> Task:
        task.last_check_at = datetime.now(UTC)
        task.last_check_ok, task.last_check_detail = ok, detail[:2000]
        await self._session.flush()
        return task

    async def require_in_room(self, room_id: uuid.UUID, task_id: uuid.UUID) -> Task:
        task = await self.get(task_id)
        if task is None or task.room_id != room_id:
            raise NotFoundError("这个房间里没有这条任务")
        self._bind_workspace(task)
        return task

    async def list_in_project(self, project_id: uuid.UUID) -> list[Task]:
        return await self._repo.list_for_project(project_id)

    async def list_in_room(self, room_id: uuid.UUID) -> list[Task]:
        """Every piece of work this room has dispatched, oldest first.

        The rows only — `threads_for_room` is the same set with each thread's
        conversation attached, and a caller that wants to know *which work
        exists* should not pay for every block ever written in the room to find
        out.
        """
        return await self._repo.list_for_room(room_id)

    async def list_by_ids(self, task_ids: list[uuid.UUID]) -> list[Task]:
        """These rows, oldest first, silently skipping ids that name nothing.

        Ordered by the table and not by the argument, so that a set of ids
        always renders in one fixed order however it was assembled — the caller
        is `Cheese-Task:`, and trailer order that depended on the order someone
        typed `--task` would make two identical declarations produce two
        different commit messages.
        """
        return await self._repo.list_by_ids(task_ids)

    async def mark_transcripts_archived(self, task_id: uuid.UUID, at: datetime) -> bool:
        """Stamp the thread with when its device home's transcripts reached
        the platform (topic/retire.py). False when no thread has this id."""
        return await self._repo.mark_transcripts_archived(task_id, at)

    async def open_by_subagent(
        self, *, room_id: uuid.UUID, subagent_id: str
    ) -> Task | None:
        """The open thread in *room_id* this worker is doing, if any.

        Asked once per hook event a worker produces, which is what the
        (room_id, subagent_id) index is for.
        """
        return await self._repo.open_by_subagent(room_id, subagent_id)

    async def bind_subagent(
        self, *, room_id: uuid.UUID, task_id: uuid.UUID, subagent_id: str
    ) -> Task:
        """Say which worker in *room_id*'s session is doing *task_id*.

        The room spawns a worker and then reports the id it got, because the id
        does not exist until the worker does — nothing the platform hands out
        in advance could name it. Everything downstream keys off this: without
        the binding a worker's events are indistinguishable from the room's own.

        Refuses rather than overwrites when the id is already doing other work
        in this room. Reassigning it would not move the work, it would silently
        re-address the events of a worker still running — the first thread would
        stop receiving its own tool calls and never say why.
        """
        subagent_id = subagent_id.strip()
        if not subagent_id:
            raise ValidationError("要绑定的分身 id 是空的")
        task = await self._repo.get(task_id)
        if task is None or task.room_id != room_id:
            # Same answer for "no such task" and "someone else's task": which of
            # the two it is, is exactly what a caller poking at ids wants told.
            raise NotFoundError("这个房间里没有这条活")
        if task.status is not TaskStatus.open:
            raise ConflictError("这条活已经收了，不能再绑分身")
        held = await self._repo.open_by_subagent(room_id, subagent_id)
        if held is not None and held.id != task.id:
            raise ConflictError(
                f"这个分身正在做「{held.title}」，一个分身同时只做一条活"
            )
        task.subagent_id = subagent_id
        # A worker starting IS this work starting, and this is the signal the
        # board falls back on before the worker has said anything: without it a
        # thread reads 失联 for the whole gap between being claimed and its first
        # tool call, which is the busiest moment it has.
        task.last_turn_at = datetime.now(UTC)
        await self._session.flush()
        return task

    async def record_conclusion(self, task: Task, conclusion: str) -> Task:
        """分身交回来的那句话，落在卡上 —— overwriting whatever was there.

        Called for every `SubagentStop` from a BOUND worker, and a worker stops
        more than once: parking a long command in its own background reads as
        finishing, and it stops again when it resumes and finishes for real. So
        the last one is the only one worth keeping. Acceptance closes delivered
        work; an explicit close abandons it. A stop notification does neither.
        """
        task.conclusion = conclusion
        await self._session.flush()
        return task

    async def set_credits(
        self,
        task: Task,
        *,
        reporter_handle: str | None,
        contributor_handles: list[str],
    ) -> None:
        """Record declared human contributions after validating their accounts."""
        from app.domain.identity.handles import names_a_person
        from app.domain.user.services import user_by_handle

        contributors = list(dict.fromkeys(contributor_handles))
        for handle in contributors + ([reporter_handle] if reporter_handle else []):
            if (
                not names_a_person(handle)
                or await user_by_handle(self._session, handle) is None
            ):
                raise ValidationError(f"贡献署名必须指向真实用户：{handle}")
        task.reporter_handle = reporter_handle
        task.contributor_handles = contributors
        await self._session.flush()

    async def close_thread(self, task: Task, *, conclusion: str | None = None) -> Task:
        """收卡 —— the room says this piece of work is over.

        The room is the only thing that can say it. It read what the worker
        handed back, folded the changes into its branch, and is the one place
        holding both halves; the platform sees a worker stop and cannot tell
        that from a worker pausing.

        `conclusion` overrides what the worker's last stop left, for the case
        where what came back was a fragment (a parked command's "running the
        tests…") and the room knows the real answer. Absent, the worker keeps
        the last word.

        Idempotent: closing a closed thread keeps the first `closed_at` — the
        moment it stopped being live is a fact, not a re-statement of intent.
        """
        if conclusion is not None:
            task.conclusion = conclusion
        if task.status is not TaskStatus.closed:
            task.status = TaskStatus.closed
            task.closed_at = datetime.now(UTC)
        await self._session.flush()
        return task

    async def open_thread(
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
        base_task_id: uuid.UUID | None = None,
    ) -> Task:
        """Create a task and its worktree before any executor starts writing."""
        from app.domain.workspace import service as ws

        base, _ = await asyncio.to_thread(ws.base_branch_head, project_id)
        if base_task_id is not None:
            parent = await self.require_in_room(room_id, base_task_id)
            if parent.branch_name is None:
                raise ValidationError("历史任务没有可依赖的工作分支")
            if parent.accepted_at is None and parent.delivered_head is None:
                base = parent.branch_name
        task = await self._repo.add(
            project_id=project_id,
            room_id=room_id,
            title=title,
            owner_handle=owner_handle,
            reviewer_handle=reviewer_handle,
            created_by=created_by,
        )
        await self.set_credits(
            task,
            reporter_handle=reporter_handle,
            contributor_handles=contributor_handles or [],
        )
        task.branch_name = f"task/{task.id.hex[:8]}"
        task.workspace_name = f"task_{task.id.hex[:8]}"
        task.base_branch, task.base_task_id = base, base_task_id
        await self._session.flush()
        self._bind_workspace(task)
        await asyncio.to_thread(ws.topic_worktree, project_id, task.id)
        return task

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

    async def blocks_for_thread(
        self, task_id: uuid.UUID, *, limit: int | None = None
    ) -> list[Block]:
        """One card's conversation, oldest first, newest *limit* blocks.

        The single-card counterpart of `threads_for_room`: opening one card
        must not fan out over every other card's history to reach it, and a
        long-lived room holds close to two hundred of them.
        """
        conversations = await self._repo.conversations_for_tasks([task_id])
        blocks = conversations.get(task_id, [])
        return blocks[-limit:] if limit is not None else blocks


class RoomLockService:
    """整块覆盖一个文件、跑重活 —— 一次一个。

    Narrow on purpose. It does not make concurrent writing safe in general: a
    lock around a write cannot prevent a lost update, because the read that the
    write is based on happened before the lock existed. It covers the two cases
    that have no other defence — a whole-file overwrite (an `Edit` defends
    itself; a `Write` cannot) and the things that fight over the machine rather
    than the tree.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def acquire(
        self,
        *,
        room_id: uuid.UUID,
        kind: LockKind,
        resource: str = "",
        holder_task_id: uuid.UUID | None,
    ) -> tuple[bool, str]:
        """Take the lock, or say who has it.

        Never waits. Blocking would spend a whole turn's compute sitting still,
        and the caller has something better to do with the answer — write a
        different file, use `Edit`, come back next turn.
        """
        now = datetime.now(UTC)
        existing = (
            await self._session.scalars(
                select(RoomLock).where(
                    RoomLock.room_id == room_id,
                    RoomLock.kind == kind,
                    RoomLock.resource == resource,
                )
            )
        ).first()
        if existing is not None:
            if existing.expires_at > now:
                if existing.holder_task_id == holder_task_id:
                    existing.expires_at = now + self._ttl(kind)
                    await self._session.flush()
                    return True, ""
                return False, await self._who(existing)
            # Expired: whoever held it is not coming back.
            await self._session.delete(existing)
            await self._session.flush()
        self._session.add(
            RoomLock(
                room_id=room_id,
                kind=kind,
                resource=resource,
                holder_task_id=holder_task_id,
                acquired_at=now,
                expires_at=now + self._ttl(kind),
            )
        )
        await self._session.flush()
        return True, ""

    async def release(
        self,
        *,
        room_id: uuid.UUID,
        kind: LockKind,
        resource: str = "",
        holder_task_id: uuid.UUID | None,
    ) -> bool:
        """Give it back. Releasing a lock somebody else holds does nothing."""
        existing = (
            await self._session.scalars(
                select(RoomLock).where(
                    RoomLock.room_id == room_id,
                    RoomLock.kind == kind,
                    RoomLock.resource == resource,
                )
            )
        ).first()
        if existing is None or existing.holder_task_id != holder_task_id:
            return False
        await self._session.delete(existing)
        await self._session.flush()
        return True

    async def sweep_expired(self) -> int:
        """Take back every lock whose holder never came home."""
        stale = list(
            (
                await self._session.scalars(
                    select(RoomLock).where(RoomLock.expires_at <= datetime.now(UTC))
                )
            ).all()
        )
        for lock in stale:
            await self._session.delete(lock)
        if stale:
            await self._session.flush()
        return len(stale)

    @staticmethod
    def _ttl(kind: LockKind) -> timedelta:
        return HEAVY_LOCK_TTL

    async def _who(self, lock: RoomLock) -> str:
        if lock.holder_task_id is None:
            return "这个房间自己正在改它"
        task = await TaskRepository(self._session).get(lock.holder_task_id)
        return f"「{task.title}」正在改它" if task else "另一条活正在改它"
