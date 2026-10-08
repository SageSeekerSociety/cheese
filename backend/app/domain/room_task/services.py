"""Task services — reading a room's threads, and the trees they work on."""

import uuid
from collections.abc import Collection
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.block.models import Block
from app.domain.room_task.checkouts import after_close
from app.domain.room_task.models import (
    HEAVY_LOCK_TTL,
    LockKind,
    RoomLock,
    Task,
    TaskStatus,
    TaskTitle,
    TaskTitleSource,
)
from app.domain.room_task.repositories import TaskRepository
from app.domain.topic.models import Topic, TopicStatus


class TaskService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TaskRepository(session)

    def rename(
        self,
        task: Task,
        title: str,
        *,
        by: str | None,
        by_person: bool = True,
    ) -> None:
        """Give ``task`` a title. A person's is final; one its AI teammate gave
        it the platform may still change when the task changes direction.
        Either way the version moves, so a platform rename computed against the
        old title is not written over this one."""
        task.title = title
        task.title_source = TaskTitleSource.human if by_person else TaskTitleSource.auto
        task.title_version = task.title_version + 1
        if not by_person:
            task.title_calibrated = True
        self.record_title(task, reason="rename", by=by)

    def record_title(self, task: Task, *, reason: str, by: str | None) -> None:
        """Keep ``task``'s current title in its history."""
        self._session.add(
            TaskTitle(
                task_id=task.id,
                title=task.title,
                source=task.title_source,
                reason=reason,
                by=by,
            )
        )

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

    async def list_open(self) -> list[Task]:
        """全平台还开着的任务，交出 ORM 行（同 `list_in_project`，**暂留**）。"""
        return await self._repo.list_open()

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

    async def open_counts(self, room_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """How many open tasks each of these rooms has; a room with none is
        absent."""
        return await self._repo.open_counts(room_ids)

    async def list_in_room(self, room_id: uuid.UUID) -> list[Task]:
        """Every piece of work this room has dispatched, oldest first.

        The rows only — `threads_for_room` is the same set with each thread's
        conversation attached, and a caller that wants to know *which work
        exists* should not pay for every block ever written in the room to find
        out.
        """
        return await self._repo.list_for_room(room_id)

    async def open_branches_in_room(self, room_id: uuid.UUID) -> list[Task]:
        """This room's open work that has a branch, oldest first.

        A channel holds every task its project ever ran — over a thousand on
        dev — and only the few still open take commits.
        """
        rows = await self._session.scalars(
            select(Task)
            .where(
                Task.room_id == room_id,
                Task.status == TaskStatus.open,
                Task.branch_name.is_not(None),
            )
            .order_by(Task.created_at, Task.id)
        )
        return list(rows.all())

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
                raise UnprocessableEntityError(
                    say("contributorMustBeUser", handle=handle)
                )
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
            raise UnprocessableEntityError(say("taskStartedAlready"))
        project = await self._session.get(Project, task.project_id)
        reviewer = (
            (reviewer_handle or "").strip()
            or task.reviewer_handle
            or branch_protection_of(project).default_reviewer
        )
        if not reviewer:
            raise UnprocessableEntityError(say("reviewerRequired"))
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
        owner's own computer first is the caller's (``topics_tasks``). A
        collaborator who becomes the owner is no longer listed as one."""
        task.owner_handle = owner_handle
        task.contributor_handles = [
            h for h in task.contributor_handles or [] if h != owner_handle
        ]
        await self._session.flush()
        return task

    @staticmethod
    def takes_part(task: Task, handle: str | None) -> bool:
        """Whether this person works the task: its owner, or a collaborator
        the owner brought in. Both talk to its AI teammate and write its
        document; only the owner starts, closes or hands it over."""
        return handle is not None and (
            handle == task.owner_handle or handle in (task.contributor_handles or [])
        )

    async def set_contributors(self, task: Task, handles: list[str]) -> Task:
        """Who works the task beside its owner. They are credited on its
        commits too (``repository.identity``)."""
        await self.set_credits(
            task, reporter_handle=task.reporter_handle, contributor_handles=handles
        )
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
            raise UnprocessableEntityError(say("taskClosedNoTurn"))

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
                raise UnprocessableEntityError(say("pastTaskNoBranch"))
            # An open task's branch holds its step still to land; a closed
            # task's landed, unless it never delivered.
            if parent.status == TaskStatus.open or (
                parent.accepted_at is None and parent.delivered_head is None
            ):
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
        self._name_branch(task)
        task.base_branch, task.base_task_id = base, base_task_id
        await self._session.flush()
        return task

    async def next_step(self, task: Task) -> None:
        """A delivery of ``task`` landed and the task goes on: its next one is
        made on a branch of its own, cut from the project's latest code, and
        opens a PR of its own. The machine moves the task's checkout onto it
        (`cheese worktree`), carrying what had not landed."""
        from app.domain.project.forge import default_branch

        task.base_branch = await default_branch(task.project_id, self._session)
        task.base_task_id = None
        task.branch_name = _next_branch(task)
        task.pr_number = task.pr_url = None
        task.last_check_at = task.last_check_ok = None
        task.last_check_detail = ""
        await self._session.flush()

    async def reopen(self, task: Task) -> Task:
        """重新打开：the task is going again. One that has landed something goes
        on from the project's latest code, like a task whose step landed."""
        if task.status is not TaskStatus.closed:
            raise UnprocessableEntityError(say("taskStillOpen"))
        task.status = TaskStatus.open
        task.closed_at = None
        task.conclusion = None
        if task.branch_name is not None and task.delivered_head is not None:
            await self.next_step(task)
        await self._session.flush()
        return task

    async def give_branch(self, task: Task) -> None:
        """The branch a task that arrived without one is worked on, cut from the
        project's default branch like a new task's."""
        from app.domain.project.forge import default_branch

        task.base_branch = await default_branch(task.project_id, self._session)
        self._name_branch(task)
        await self._session.flush()

    @staticmethod
    def _name_branch(task: Task) -> None:
        task.branch_name = f"task/{task.id.hex[:8]}"
        task.workspace_name = f"task_{task.id.hex[:8]}"

    async def threads_for_room(
        self,
        room_id: uuid.UUID,
        *,
        limit: int | None = None,
        status: str | None = None,
        ids: Collection[uuid.UUID] | None = None,
        origins: Collection[uuid.UUID] | None = None,
        with_branch: bool = False,
        latest: int | None = None,
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

        `limit=0` is that rule at its smallest: every thread, no conversation.
        A caller drawing a roster never reads a block, and the block query is
        the one that costs — the whole room's history, or a window function
        over it. Skipped outright rather than run and discarded.

        The other keywords narrow which threads, as `TaskRepository.list_for_room`
        says.
        """
        tasks = await self._repo.list_for_room(
            room_id,
            status=status,
            ids=ids,
            origins=origins,
            with_branch=with_branch,
            latest=latest,
        )
        conversations = (
            {}
            if limit == 0
            else await self._repo.conversations_for_tasks(
                [t.id for t in tasks], limit=limit
            )
        )
        return [(task, conversations.get(task.id, [])) for task in tasks]


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


def _next_branch(task: Task) -> str:
    """``task/<id>`` for a task's first delivery, ``task/<id>-2`` for its
    second, and so on."""
    first = f"task/{task.id.hex[:8]}"
    current = task.branch_name or first
    step = current.removeprefix(first + "-")
    return f"{first}-{int(step) + 1 if step.isdigit() else 2}"


def said_title(task: Task) -> str:
    """``task``'s title as a parameter of a room line about it.

    An unnamed task's stored title is the Chinese placeholder, so it goes in as
    its own sentence and each reader sees their own word for it. A title
    someone gave is passed as it is, even one that reads like the placeholder."""
    if task.title_source == TaskTitleSource.placeholder:
        return say("taskUntitled")
    return task.title
