"""Task services — reading a room's threads, and the trees they work on."""

import uuid
from collections.abc import Collection
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import Select, Uuid, and_, bindparam, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import NotFoundError, UnprocessableEntityError
from app.core.sentences import say
from app.domain.block.models import Block
from app.domain.identity.handles import names_a_person
from app.domain.identity.services import IdentityService
from app.domain.living_doc.services import Documents
from app.domain.project.models import Project
from app.domain.project.protection import branch_protection_of, can_take_review
from app.domain.room_task import progress
from app.domain.room_task.lock_order import lock_task_for_change
from app.domain.room_task.models import (
    Task,
    TaskStatus,
    TaskTitle,
    TaskTitleSource,
)
from app.domain.room_task.repositories import TaskRepository
from app.domain.team.models import TeamUserRelation
from app.domain.team.protocol import TeamMemberRole
from app.domain.topic.models import Topic, TopicReadState, TopicStatus
from app.domain.user.models import User
from app.domain.user.services import user_by_handle


class TaskService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = TaskRepository(session)

    async def credit_owner(self, task: Task) -> str | None:
        """A human owner pays for work; an AI's work stays with its requester."""
        for handle in (task.owner_handle, task.created_by):
            if (
                handle
                and names_a_person(handle)
                and not await IdentityService(self._session).is_agent(handle)
            ):
                return handle
        project = await self._session.get(Project, task.project_id)
        if project is None:
            return None
        if (
            project.owner_handle
            and names_a_person(project.owner_handle)
            and not await IdentityService(self._session).is_agent(project.owner_handle)
        ):
            return project.owner_handle
        return await self._session.scalar(
            select(User.username)
            .join(TeamUserRelation, TeamUserRelation.user_id == User.id)
            .where(
                TeamUserRelation.team_id == project.team_id,
                TeamUserRelation.role == TeamMemberRole.OWNER,
                TeamUserRelation.deleted_at.is_(None),
            )
        )

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

    async def tasks_of_rooms(
        self, room_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, uuid.UUID]:
        """{还在进行的那条活: 它所在的房间} —— 一个频道那一格认的是它全部的对话，
        所以调用方要的是「这些房间里有哪些活」（`api/routes/topics._awaiting_an_answer`）。"""
        return await self._repo.tasks_of_rooms(room_ids)

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

    async def open_without_pr(
        self, *, active_since: datetime | None = None
    ) -> list[Task]:
        """Open tasks on live rooms that could still need a draft PR.

        A room that is archived is not worked in, so its tasks are left out
        like the PR pollers leave them out: every one of them costs a GitHub
        request on each sweep, and rooms archived before archiving closed
        their tasks still hold open ones whose branches never reached GitHub.

        ``active_since``: only tasks whose last activity is at or after this
        instant — the newest block in the task's own conversation, or its
        creation when nobody has said anything. That is the same 「最后一次
        活动」 the board shows. A caller that would rather not pay two GitHub
        requests for every long-quiet branch passes it; ``None`` keeps them all.
        """
        stmt = (
            select(Task)
            .join(Topic, Topic.id == Task.room_id)
            .where(
                Task.status == TaskStatus.open,
                Task.branch_name.is_not(None),
                Task.pr_number.is_(None),
                Topic.status != TopicStatus.archived,
            )
        )
        if active_since is not None:
            last_said = (
                select(func.max(Block.created_at))
                .where(Block.conversation_id == Task.id)
                .scalar_subquery()
            )
            stmt = stmt.where(func.coalesce(last_said, Task.created_at) >= active_since)
        rows = await self._session.scalars(stmt)
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
                # FOR NO KEY UPDATE, after its numbering: the task lock order
                # (`lock_order`).
                .with_for_update(skip_locked=True, key_share=True)
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

    async def list_in_project(
        self,
        project_id: uuid.UUID,
        *,
        accepted: bool | None = None,
        rooms: Collection[uuid.UUID] | None = None,
        status: str | None = None,
    ) -> list[Task]:
        """这个项目里的活，最老的在前。

        `rooms` 和 `status` 是把清单收窄交给数据库的两把刀（见 `list_for_project`）：
        给了就只装配满足的那些行。项目任务列表接口把「我看得见的房间」和 `status`
        都给上，不再把整个项目的活读进来再用 Python 筛一遍。

        交出去的是 ORM 行，**暂留**：room_task 这一期还没有自己的窄读出口，项目
        侧栏就是拿这批行就地折出展示态的（`presentation.facts_for_task`），行本身
        不出这个进程、更不上线。等这边也开了 `queries.py`，这条就该只交纯值。

        Consumers are facts_for_task and TaskOut.model_validate.
        Callers authorize and own this session and its transaction. This read
        does not explicitly begin, commit or roll back.
        """
        return await self._repo.list_for_project(
            project_id, accepted=accepted, rooms=rooms, status=status
        )

    async def list_open(self) -> list[Task]:
        """全平台还开着的任务，交出 ORM 行（同 `list_in_project`，**暂留**）。"""
        return await self._repo.list_open()

    async def last_block_at_for_tasks(
        self, task_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, datetime]:
        """These threads' heartbeats, newest block per task, in one query.

        A narrow read out of this domain for callers that hold ids and need the
        latest-activity time the project rail sorts by. It is on the service and not on the repository because the
        repository is this domain's own drawer and stays inside it: a caller
        outside `room_task` reaches for the service.

        Callers authorize and own this session and its transaction. This read
        does not explicitly begin, commit or roll back.
        """
        return await self._repo.last_block_at_for_tasks(task_ids)

    async def page_in_project(
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
        """One page of the project's tasks, as `TaskRepository.page_for_project`
        reads it."""
        return await self._repo.page_for_project(
            project_id,
            rooms=rooms,
            status=status,
            channel=channel,
            whose=whose,
            me=me,
            limit=limit,
            before=before,
            accepted=accepted,
        )

    async def counts_in_project(
        self,
        project_id: uuid.UUID,
        *,
        rooms: Collection[uuid.UUID],
        status: str,
        channel: uuid.UUID | None,
        me: str,
        accepted: bool | None = None,
    ) -> dict[str, int]:
        """The project's tasks counted by whose they are, as
        `TaskRepository.counts_for_project` counts them."""
        return await self._repo.counts_for_project(
            project_id,
            rooms=rooms,
            status=status,
            channel=channel,
            me=me,
            accepted=accepted,
        )

    async def underway_counts(self, room_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        """How many tasks each of these rooms has underway (open, and not being
        written up after its last merge); a room with none is absent."""
        return await self._repo.underway_counts(room_ids)

    async def underway_with(self, room_ids: list[uuid.UUID], person: str) -> list[Task]:
        """The tasks underway in these rooms that ``person`` owns or helps on."""
        return await self._repo.underway_with(room_ids, person)

    async def underway_others(
        self, room_ids: list[uuid.UUID], person: str
    ) -> list[Task]:
        """The tasks underway in these rooms that ``person`` neither owns nor
        helps on, most recently moved first."""
        return await self._repo.underway_others(room_ids, person)

    async def unseen_deliveries(
        self, room_ids: list[uuid.UUID], person: str
    ) -> set[uuid.UUID]:
        """Which of these rooms hold a delivery ``person`` has not opened since:
        the delivered task's conversation id and its room's, both in the set.

        哪几件活「交付完了、而这个人从那以后还没点开看过」：那件活自己的交谈 id，
        和它所在的房间 id，两个都在返回的这一堆里 —— 侧栏的灯问的就是这两种行
        （`TopicOut.unseen_delivery` / `RoomTask.unseen_delivery`）。

        交付完了 = 卡片被采纳过（`accepted_at`）。「点开看过」认这个人在这段对话上
        的已读位那一行动没动过：点开会推已读位，那一行跟着写一次时间
        （`TopicRepository.mark_read_many` 每次都把 `updated_at` 写成当下），所以它
        最后被动过的时刻就是最后点开的时刻。

        频道要问的是「我在这间房里看过没有」，不只「我看过那件活没有」——点开频道
        这个动作落在房间的已读位上，点开那件活落在活自己的已读位上，两个都算看过。
        所以取两个时刻里晚的那个（`greatest` 在 Postgres 里跳过 NULL，全空才是
        空）。两处都没有 = 从来没点开过，那也是没看过。

        只算这个人自己参与的活（负责人或协作者，`TaskRepository.takes_part`）：一
        件与他无关的活交付了，不该在他的侧栏上亮灯 —— 和 `unread_counts` 给任务算
        未读是同一条规矩。

        这条查询读的是另一个域的表（已读位是 topic 域的），所以它在服务层：这个域
        已经在读 `topic.models` 的那几个模块（`checkouts` / `place` / `presentation`
        / `services`）在契约上都冻结过，仓库那一层再加一个进口就是新债。
        """
        if not room_ids:
            return set()
        # 语句按看的人建一次留着，房间每次当参数给（`_unseen_deliveries_statement`）。
        unseen: set[uuid.UUID] = set()
        for task_id, room_id, accepted_at, looked_at in (
            await self._session.execute(
                _unseen_deliveries_statement(person),
                {"unseen_rooms": list(room_ids)},
            )
        ).all():
            if looked_at is None or accepted_at > looked_at:
                unseen.add(task_id)
                unseen.add(room_id)
        return unseen

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
        is `Link:`, and trailer order that depended on the order someone
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

    async def ensure_document(self, task: Task) -> uuid.UUID:
        """The task's living document, created empty the first time it is
        asked for. The row is locked so two first asks make one document."""
        if task.document_id is not None:
            return task.document_id
        locked = await lock_task_for_change(self._session, task.id)
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

    @staticmethod
    def default_reviewer(
        project: Project | None, owner_handle: str | None
    ) -> str | None:
        """Who reviews a new task's changes: its owner, who asked for it and so
        should look at what changed; when the owner cannot take a review (an AI
        teammate in a project run as a collaboration), the project's default
        reviewer. None when neither can: the task is worked all the same, and
        its first delivery asks for a reviewer (`review.services.reviewers`).

        Written when the task is made rather than read back from the setting
        when changes are submitted: the setting can change, and who a task was
        handed to for review is a fact about the moment it was handed over."""
        owner = (owner_handle or "").strip()
        if can_take_review(project, owner):
            return owner
        fallback = branch_protection_of(project).default_reviewer
        return fallback if fallback and can_take_review(project, fallback) else None

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
        the owner brought in. What they say is what its AI teammate acts on,
        and they write its document, and close it; only the owner hands it
        over."""
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
        """Refuse what only an open task takes: its first instruction given
        again, a handover. A closed task is still talked in."""
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
        task_id: uuid.UUID | None = None,
    ) -> Task:
        """Record a task's branch; its executor creates the worktree on its machine.

        ``task_id`` is a 支线's id when the 支线 itself becomes the task: the
        conversation keeps its id, and with it everything said in it."""
        # deferred-import: tests replace this name on app.domain.project.forge
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
        if reviewer_handle is None:
            reviewer_handle = self.default_reviewer(
                await self._session.get(Project, project_id), owner_handle
            )
        task = await self._repo.add(
            project_id=project_id,
            room_id=room_id,
            title=title,
            owner_handle=owner_handle,
            reviewer_handle=reviewer_handle,
            created_by=created_by,
            title_source=title_source,
            task_id=task_id,
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

    async def next_step(self, task: Task, *, base_branch: str | None = None) -> None:
        """A delivery of ``task`` landed and the task goes on: its next one is
        made on a branch of its own, cut from the project's latest code, and
        opens a PR of its own. The machine moves the task's checkout onto it
        (`cheese worktree`), carrying what had not landed. ``base_branch``: the
        project's default branch, when the caller read it from the forge before
        locking ``task``."""
        # deferred-import: tests replace this name on app.domain.project.forge
        from app.domain.project.forge import default_branch

        task.base_branch = base_branch or await default_branch(
            task.project_id, self._session
        )
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
        # Reopened is 开启: what its teammate last reported was about the work
        # that closed.
        progress.clear(task)
        if task.branch_name is not None and task.delivered_head is not None:
            await self.next_step(task)
        else:
            # Closing the task closed its PR (`review.task_pr`) and kept its
            # branch. Forgetting the PR lets the draft sweep open one for the
            # branch again, or adopt the PR still open where that close failed.
            task.pr_number = task.pr_url = None
        await self._session.flush()
        return task

    async def give_branch(self, task: Task) -> None:
        """The branch a task that arrived without one is worked on, cut from the
        project's default branch like a new task's."""
        # deferred-import: tests replace this name on app.domain.project.forge
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


#: `TaskService.unseen_deliveries` 那条查询的房间参数。房间按百计，走数组参数；
#: `expanding` 让它在 SQL 里照样摊成 `IN ($1, …)` —— 和原先 `in_(room_ids)` 生成的
#: 文本逐字节相同，执行计划不变。
_unseen_rooms = bindparam("unseen_rooms", type_=Uuid, expanding=True)


@lru_cache(maxsize=64)
def _unseen_deliveries_statement(person: str) -> Select:
    """`TaskService.unseen_deliveries` 的那条查询，按看的人建一次留着。

    每个请求都来拼一遍这个双 `outerjoin` 的语句，量出来光构造本身就约 2ms
    （2026-10-14，48 万区块的库，一次频道详情）。房间每次当参数给；**谁在看**
    烧进了连接条件和 `takes_part` 里，所以缓存的键是人。语句是只读的，一份拿给
    并发用安全（`block/repositories.py` 的 `_awaiting_statements` 是同一个写法）。
    """
    task_read = aliased(TopicReadState)
    room_read = aliased(TopicReadState)
    return (
        select(
            Task.id,
            Task.room_id,
            Task.accepted_at,
            func.greatest(task_read.updated_at, room_read.updated_at),
        )
        .outerjoin(
            task_read,
            and_(
                task_read.topic_id == Task.id,
                task_read.user_handle == person,
            ),
        )
        .outerjoin(
            room_read,
            and_(
                room_read.topic_id == Task.room_id,
                room_read.user_handle == person,
            ),
        )
        .where(
            Task.room_id.in_(_unseen_rooms),
            Task.accepted_at.is_not(None),
            TaskRepository.takes_part(person),
        )
    )
