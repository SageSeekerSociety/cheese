"""Task services — reading a room's threads, and the trees they work on."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.block.models import Block
from app.domain.room_task.checkouts import after_close
from app.domain.room_task.models import (
    HEAVY_LOCK_TTL,
    LockKind,
    RoomLock,
    Task,
    TaskStatus,
    TaskTitleSource,
)
from app.domain.room_task.repositories import TaskRepository
from app.domain.topic.models import Topic, TopicStatus


class TaskService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TaskRepository(session)

    @staticmethod
    def rename(task: Task, title: str) -> None:
        """Give ``task`` a title; from then on it is named, whatever the words."""
        task.title, task.title_source = title, TaskTitleSource.human

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._repo.get(task_id)

    async def record_author(self, task: Task, agent_handle: str) -> None:
        """Keep the first executing agent, including across concurrent opens."""
        await self._session.execute(
            update(Task)
            .where(
                Task.id == task.id,
                Task.status == TaskStatus.open,
                Task.author_handle.is_(None),
            )
            .values(author_handle=agent_handle)
        )
        await self._session.refresh(task)

    async def open_without_pr(self) -> list[Task]:
        """Open tasks on live rooms that could still need a draft PR.

        A room that is archived is not worked in, so its tasks are left out
        like the PR pollers leave them out: every one of them costs a GitHub
        request on each sweep, and rooms archived before archiving closed
        their tasks still hold open ones whose branches never reached GitHub.
        """
        rows = await self._session.scalars(
            select(Task)
            .join(Topic, Topic.id == Task.room_id)
            .where(
                Task.status == TaskStatus.open,
                Task.branch_name.is_not(None),
                Task.pr_number.is_(None),
                Topic.status != TopicStatus.archived,
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
            raise NotFoundError(say("taskNotFoundInRoom"))
        return task

    async def require_source_in_room(
        self, room_id: uuid.UUID, task_id: uuid.UUID
    ) -> None:
        """这份来源要是这个房间的一条「已经开了分支」的活。

        和 `require_in_room` 分开，因为**答复不一样**：一个是「这个房间里没有这
        条任务」，一个是「这不是一条能读文件的活」。读文件的那几个接口对外发的是
        后者，措辞不能跟着一个更严的同名检查走样 —— 卡在、房间也对、只是还没开
        分支，那是另一件事。

        `room_id` 是房间（也就是话题）：一份来源不是一个自由填的 id，要指名某个
        房间的某条活。"""
        task = await self.get(task_id)
        if task is None or task.room_id != room_id or task.branch_name is None:
            raise NotFoundError("Task not found")

    async def list_in_project(self, project_id: uuid.UUID) -> list[Task]:
        """这个项目里的活，最老的在前。

        交出去的是 ORM 行，**暂留**：room_task 这一期还没有自己的窄读出口，项目
        侧栏就是拿这批行就地折出展示态的（`presentation.facts_for_task`），行本身
        不出这个进程、更不上线。等这边也开了 `queries.py`，这条就该只交纯值。

        Consumers are task_liveness, facts_for_task and TaskOut.model_validate.
        Callers authorize and own this session and its transaction. This read
        does not explicitly begin, commit or roll back.
        """
        return await self._repo.list_for_project(project_id)

    async def last_block_at_for_tasks(
        self, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """These threads' heartbeats, newest block per task, in one query.

        A narrow read out of this domain for callers that hold ids and need the
        liveness signal the board draws from — the project rail is the one that
        does. It is on the service and not on the repository because the
        repository is this domain's own drawer and stays inside it: a caller
        outside `room_task` reaches for the service.

        Callers authorize and own this session and its transaction. This read
        does not explicitly begin, commit or roll back.
        """
        return await self._repo.last_block_at_for_tasks(task_ids)

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
                raise ValidationError(say("contributorMustBeUser", handle=handle))
        task.reporter_handle = reporter_handle
        task.contributor_handles = contributors
        await self._session.flush()

    async def close_thread(self, task: Task, *, conclusion: str | None = None) -> Task:
        """关闭任务 —— its owner or its own session says it is over; with a
        `conclusion` it is done, without one it was put down.

        Idempotent: closing a closed thread keeps the first `closed_at` — the
        moment it stopped being live is a fact, not a re-statement of intent.
        """
        if conclusion is not None:
            task.conclusion = conclusion
        if task.status is not TaskStatus.closed:
            task.status = TaskStatus.closed
            task.closed_at = datetime.now(UTC)
            after_close(self._session, task.room_id)
        await self._session.flush()
        return task

    async def ensure_document(self, task: Task) -> uuid.UUID:
        """The task's living document, created empty the first time it is
        asked for. The row is locked so two first asks make one document."""
        from app.domain.living_doc.services import Documents

        locked = await self._session.scalar(
            select(Task)
            .where(Task.id == task.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert locked is not None
        if locked.document_id is None:
            doc = await Documents(self._session).create(project_id=locked.project_id)
            locked.document_id = doc.id
            await self._session.flush()
        return locked.document_id

    async def of_document(self, document_id: uuid.UUID) -> Task | None:
        """The task whose living document this is."""
        return await self._session.scalar(
            select(Task).where(Task.document_id == document_id)
        )

    async def start(
        self, task: Task, *, by: str, reviewer_handle: str | None = None
    ) -> Task:
        """「开始」：written once, with who reviews its changes and what its
        document said at that moment.

        The reviewer is the one named here, else the project's default. It is
        written now rather than read back from the setting when changes are
        submitted: the setting can change, and who a task was handed to for
        review is a fact about the moment it started.
        """
        from app.domain.living_doc.services import Documents
        from app.domain.project.models import Project
        from app.domain.project.protection import branch_protection_of

        if task.started_at is not None:
            raise ValidationError(say("taskStartedAlready"))
        project = await self._session.get(Project, task.project_id)
        reviewer = (
            (reviewer_handle or "").strip()
            or task.reviewer_handle
            or branch_protection_of(project).default_reviewer
        )
        if not reviewer:
            raise ValidationError(say("reviewerRequired"))
        doc = (
            await Documents(self._session).get(task.document_id)
            if task.document_id is not None
            else None
        )
        task.reviewer_handle = reviewer
        task.started_at = datetime.now(UTC)
        task.started_by = by
        task.started_doc_version = doc.version if doc is not None else 0
        await self._session.flush()
        return task

    async def hand_over(self, task: Task, *, owner_handle: str) -> Task:
        """Another member owns the task from now. Moving it off its former
        owner's own computer first is the caller's (``topics_tasks``)."""
        task.owner_handle = owner_handle
        await self._session.flush()
        return task

    async def give_agent(self, task: Task, *, agent_handle: str | None) -> Task:
        """Another AI teammate works the task from its next turn (None: the
        project's default)."""
        task.agent_handle = (agent_handle or "").strip() or None
        await self._session.flush()
        return task

    @staticmethod
    def require_open(task: Task) -> None:
        """Refuse what only an open task takes: a message to its session."""
        if task.status != TaskStatus.open:
            raise ValidationError(say("taskClosedNoTurn"))

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
        title_source: TaskTitleSource = TaskTitleSource.human,
    ) -> Task:
        """Record a task's branch; its executor creates the worktree on its machine."""
        from app.domain.project.forge import default_branch

        base = await default_branch(project_id, self._session)
        if base_task_id is not None:
            parent = await self.require_in_room(room_id, base_task_id)
            if parent.branch_name is None:
                raise ValidationError(say("pastTaskNoBranch"))
            if parent.accepted_at is None and parent.delivered_head is None:
                base = parent.branch_name
        task = await self._repo.add(
            project_id=project_id,
            room_id=room_id,
            title=title,
            owner_handle=owner_handle,
            reviewer_handle=reviewer_handle,
            created_by=created_by,
            title_source=title_source,
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
        self,
        task_id: uuid.UUID,
        *,
        limit: int | None = None,
        through: uuid.UUID | None = None,
    ) -> list[Block] | None:
        """One card's conversation, oldest first, newest *limit* blocks.

        The single-card counterpart of `threads_for_room`: opening one card
        must not fan out over every other card's history to reach it, and a
        long-lived room holds close to two hundred of them.

        `through` names a block the window must reach back to, with a few
        blocks of context above it: a card opened at one of its messages.
        None when that block is not in this card's conversation.
        """
        conversations = await self._repo.conversations_for_tasks([task_id])
        blocks = conversations.get(task_id, [])
        start = max(len(blocks) - limit, 0) if limit is not None else 0
        if through is not None:
            at = next((i for i, b in enumerate(blocks) if b.id == through), None)
            if at is None:
                return None
            if at < start:
                start = max(at - _CONTEXT_ABOVE, 0)
        return blocks[start:]


#: Blocks kept above a card's `through` block, so it opens mid-conversation.
_CONTEXT_ABOVE = 10


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
