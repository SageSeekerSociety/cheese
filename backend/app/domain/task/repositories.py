from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import CTE, Select, and_, delete, exists, func, not_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.reads import get_by_ids
from app.domain.search import bm25
from app.domain.tag.models import Tag
from app.domain.task.inputs import APPROVE_APPROVED, APPROVE_DISAPPROVED
from app.domain.task.models import (
    Task,
    TaskAccessDomain,
    TaskMembership,
    TaskMentor,
    TaskRubricItem,
    TaskSubmissionSchemaEntry,
    TaskTagRelation,
)
from app.domain.task.rubric import RubricLine
from app.domain.task.stages import stage_of
from app.domain.task.submission_repositories import (
    TaskSubmissionEntryRepository,
    TaskSubmissionRepository,
    TaskSubmissionReviewRepository,
)
from app.domain.task.visibility_service import TaskVisibilityService
from app.domain.team.models import TeamUserRelation

__all__ = [
    "TaskAccessDomainRepository",
    "TaskMembershipRepository",
    "TaskRepository",
    "TaskSubmissionEntryRepository",
    "TaskSubmissionRepository",
    "TaskSubmissionReviewRepository",
    "TaskSubmissionSchemaRepository",
    "TopicRepository",
]


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, task_id: int) -> Task | None:
        stmt: Select[tuple[Task]] = select(Task).where(
            and_(Task.id == task_id, Task.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_ids(self, task_ids: Sequence[int]) -> dict[int, Task]:
        return await get_by_ids(
            self._session, Task, task_ids, Task.deleted_at.is_(None)
        )

    @staticmethod
    def joined_by(user_id: int):
        """The tasks ``user_id`` takes part in: a live claim
        (``TaskMembership.live_claim``, approved or waiting) as themselves on an
        individual task, or through one of their teams on a team task."""
        user_member_exists = exists().where(
            TaskMembership.task_id == Task.id,
            TaskMembership.member_id == user_id,
            TaskMembership.live_claim,
        )
        team_member_exists = exists().where(
            TaskMembership.task_id == Task.id,
            TaskMembership.live_claim,
            TaskMembership.member_id == TeamUserRelation.team_id,
            TeamUserRelation.user_id == user_id,
            TeamUserRelation.deleted_at.is_(None),
        )
        return or_(
            and_(Task.submitter_type == 0, user_member_exists),
            and_(Task.submitter_type == 1, team_member_exists),
        )

    async def list_joined(self, user_id: int, *, limit: int) -> Sequence[Task]:
        """Every task ``user_id`` takes part in, across spaces, latest first."""
        stmt = (
            select(Task)
            .where(Task.deleted_at.is_(None), self.joined_by(user_id))
            .order_by(Task.updated_at.desc(), Task.id.desc())
            .limit(limit)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def search_in_space(
        self, space_id: int, words: Sequence[str], *, viewer_id: int, limit: int
    ) -> Sequence[Task]:
        """The space's 题目 whose name or intro holds every word, latest first:
        the published ones, and the viewer's own however they were reviewed."""
        stmt = select(Task).where(
            Task.space_id == space_id,
            Task.deleted_at.is_(None),
            or_(Task.approved == APPROVE_APPROVED, Task.creator_id == viewer_id),
        )
        for word in words:
            like = f"%{word}%"
            stmt = stmt.where(or_(Task.name.ilike(like), Task.intro.ilike(like)))
        stmt = stmt.order_by(Task.updated_at.desc(), Task.id.desc()).limit(limit)
        return (await self._session.execute(stmt)).scalars().all()

    async def list_tasks(
        self,
        *,
        space_id: int,
        category_id: int | None = None,
        approved: int | None = None,
        owner_id: int | None = None,
        keywords: str | None = None,
        topics: Sequence[int] | None = None,
        joined: bool | None = None,
        current_user_id: int | None = None,
        viewer_user_id: int | None = None,
        viewer_email_domain: str | None = None,
        viewer_is_space_admin: bool = False,
        apply_space_task_visibility: bool = False,
        visible_task_limit: int | None = None,
        lifecycle: str | None = None,
        limit: int,
        offset: int = 0,
        sort_by: str = "publishedAt",
        sort_order: str = "desc",
    ) -> Sequence[Task]:
        """List tasks with basic filtering and offset-based pagination.

        This is a simplified translation of Kotlin TaskService.enumerateTasks:
        - Always filters by space.
        - Optionally filters by category, approved status, and owner (creator_id).
        - Optionally searches name/intro for `keywords`, ordered by relevance.
        - Supports sorting by createdAt/updatedAt/deadline + id tie-breaker.
        """
        stmt: Select[tuple[Task]] = select(Task).where(
            and_(Task.deleted_at.is_(None), Task.space_id == space_id)
        )

        if category_id is not None:
            stmt = stmt.where(Task.category_id == category_id)
        if approved is not None:
            stmt = stmt.where(Task.approved == approved)
        if owner_id is not None:
            stmt = stmt.where(Task.creator_id == owner_id)  # type: ignore[attr-defined]

        hits = self._keyword_hits(space_id=space_id, keywords=keywords)
        if hits is not None:
            await bm25.serial_scans(self._session)
            stmt = stmt.join(hits, hits.c.id == Task.id)

        if topics:
            stmt = stmt.where(
                exists().where(
                    TaskTagRelation.task_id == Task.id,
                    TaskTagRelation.deleted_at.is_(None),
                    TaskTagRelation.tag_id.in_(list(topics)),
                )
            )

        # joined 过滤：如果传入 joined 且当前用户已知，则根据用户是否参与任务过滤。
        if joined is not None and current_user_id is not None:
            joined_predicate = self.joined_by(current_user_id)

            if joined:
                stmt = stmt.where(joined_predicate)
            else:
                stmt = stmt.where(~joined_predicate)

        if (
            viewer_user_id is not None
            and viewer_user_id > 0
            and not viewer_is_space_admin
        ):
            visibility_predicate = TaskVisibilityService.build_visibility_predicate(
                user_id=viewer_user_id,
                email_domain=viewer_email_domain,
            )
            stmt = stmt.where(visibility_predicate)

        if lifecycle is not None:
            stmt = cast(
                Select[tuple[Task]],
                self._apply_lifecycle_filter(stmt, lifecycle=lifecycle),
            )

        if apply_space_task_visibility:
            stmt = cast(
                Select[tuple[Task]],
                self._apply_space_task_visibility(
                    stmt,
                    space_id=space_id,
                    visible_task_limit=visible_task_limit,
                ),
            )

        # A search is ordered by relevance; the requested sort applies to browsing.
        if hits is not None:
            stmt = stmt.order_by(hits.c.score.desc(), Task.id.asc())
            stmt = stmt.limit(limit).offset(offset)
            result = await self._session.execute(stmt)
            return list(result.scalars().all())

        # Map sort_by to actual columns; default to updatedAt.
        if sort_by == "createdAt":
            sort_col = Task.created_at
        elif sort_by == "publishedAt":
            sort_col = func.coalesce(Task.published_at, Task.created_at)
        elif sort_by == "deadline":
            sort_col = Task.deadline
        elif sort_by == "reviewedAt":
            sort_col = Task.reviewed_at
        else:
            sort_col = Task.updated_at

        desc = sort_order.lower() == "desc"
        # reviewedAt 是后加的列，老题（以及这条迁移之前审过的题）它是 NULL。Postgres
        # 的 DESC 默认把 NULL 排在最前，而那正是「最近处理过」最不想要的一头 ——
        # 一屏全是不知道谁审过的旧题。这一列显式 NULLS LAST；其它列保持默认。
        if desc:
            primary = (
                sort_col.desc().nullslast()
                if sort_by == "reviewedAt"
                else sort_col.desc()
            )
            stmt = stmt.order_by(primary, Task.id.desc())
        else:
            stmt = stmt.order_by(sort_col.asc(), Task.id.asc())

        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_tasks(
        self,
        *,
        space_id: int,
        category_id: int | None = None,
        approved: int | None = None,
        owner_id: int | None = None,
        keywords: str | None = None,
        topics: Sequence[int] | None = None,
        joined: bool | None = None,
        current_user_id: int | None = None,
        viewer_user_id: int | None = None,
        viewer_email_domain: str | None = None,
        viewer_is_space_admin: bool = False,
        apply_space_task_visibility: bool = False,
        visible_task_limit: int | None = None,
        lifecycle: str | None = None,
    ) -> int:
        """Count tasks matching the same filters as list_tasks (without pagination)."""
        stmt = select(func.count(Task.id)).where(
            and_(Task.deleted_at.is_(None), Task.space_id == space_id)
        )

        if category_id is not None:
            stmt = stmt.where(Task.category_id == category_id)
        if approved is not None:
            stmt = stmt.where(Task.approved == approved)
        if owner_id is not None:
            stmt = stmt.where(Task.creator_id == owner_id)  # type: ignore[attr-defined]

        hits = self._keyword_hits(space_id=space_id, keywords=keywords)
        if hits is not None:
            await bm25.serial_scans(self._session)
            stmt = stmt.join(hits, hits.c.id == Task.id)

        if topics:
            stmt = stmt.where(
                exists().where(
                    TaskTagRelation.task_id == Task.id,
                    TaskTagRelation.deleted_at.is_(None),
                    TaskTagRelation.tag_id.in_(list(topics)),
                )
            )

        if joined is not None and current_user_id is not None:
            joined_predicate = self.joined_by(current_user_id)

            if joined:
                stmt = stmt.where(joined_predicate)
            else:
                stmt = stmt.where(~joined_predicate)

        if (
            viewer_user_id is not None
            and viewer_user_id > 0
            and not viewer_is_space_admin
        ):
            visibility_predicate = TaskVisibilityService.build_visibility_predicate(
                user_id=viewer_user_id,
                email_domain=viewer_email_domain,
            )
            stmt = stmt.where(visibility_predicate)

        if lifecycle is not None:
            stmt = cast(
                Select[tuple[int]],
                self._apply_lifecycle_filter(stmt, lifecycle=lifecycle),
            )

        if apply_space_task_visibility:
            stmt = cast(
                Select[tuple[int]],
                self._apply_space_task_visibility(
                    stmt,
                    space_id=space_id,
                    visible_task_limit=visible_task_limit,
                ),
            )

        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    @staticmethod
    def _keyword_hits(*, space_id: int, keywords: str | None) -> CTE | None:
        """The space's tasks matching every word of ``keywords``, with a score;
        a name match weighs twice an intro match (see `app.domain.search.bm25`).

        Scored in a MATERIALIZED CTE: the caller's other filters (lifecycle,
        visibility, EXISTS subqueries) are not in the index, and on the same
        scan they would turn the score NULL.
        """
        terms = bm25.words(keywords)
        if not terms:
            return None
        return (
            select(Task.id, func.paradedb.score(Task.id).label("score"))
            .where(
                bm25.match_all_words(Task.id, terms, {"name": 2, "intro": 1}),
                Task.deleted_at.is_(None),
                Task.space_id == space_id,
            )
            .cte("task_keyword_hits")
            .prefix_with("MATERIALIZED")
        )

    def _apply_lifecycle_filter(
        self,
        stmt: Select[tuple[Task]] | Select[tuple[int]],
        *,
        lifecycle: str,
    ) -> Select[tuple[Task]] | Select[tuple[int]]:
        if lifecycle == "ended":
            return stmt.where(Task.ended_at.is_not(None))
        if lifecycle == "notEnded":
            return stmt.where(Task.ended_at.is_(None))
        if lifecycle in ("recruiting", "closed"):
            # Still to be claimed: not ended, registration not over, a seat left.
            # A 题目 whose registration has not opened yet is still ahead of the
            # reader, so it counts as recruiting. This is the task's own state,
            # the same for every viewer; the list shows the tasks a viewer holds
            # a claim on in a group of their own (``joined``), not in these two.
            approved_count = (
                select(func.count(TaskMembership.id))
                .where(
                    TaskMembership.task_id == Task.id,
                    TaskMembership.deleted_at.is_(None),
                    TaskMembership.approved == 0,
                )
                .correlate(Task)
                .scalar_subquery()
            )
            open_ = and_(
                Task.ended_at.is_(None),
                or_(Task.deadline.is_(None), Task.deadline > func.now()),
                or_(
                    Task.participant_limit.is_(None),
                    Task.participant_limit <= 0,
                    approved_count < Task.participant_limit,
                ),
            )
            return stmt.where(open_ if lifecycle == "recruiting" else not_(open_))
        return stmt

    def _apply_space_task_visibility(
        self,
        stmt: Select[tuple[Task]] | Select[tuple[int]],
        *,
        space_id: int,
        visible_task_limit: int | None,
    ) -> Select[tuple[Task]] | Select[tuple[int]]:
        if visible_task_limit is None:
            return stmt.where(
                or_(
                    Task.ended_at.is_not(None),
                    and_(Task.approved == 0, Task.ended_at.is_(None)),
                )
            )
        if visible_task_limit == 0:
            return stmt.where(Task.ended_at.is_not(None))

        published_sort = func.coalesce(Task.published_at, Task.created_at)
        ranked = (
            select(
                Task.id.label("task_id"),
                func.row_number()
                .over(
                    partition_by=Task.creator_id,
                    order_by=(published_sort.asc(), Task.id.asc()),
                )
                .label("rn"),
            )
            .where(
                Task.deleted_at.is_(None),
                Task.space_id == space_id,
                Task.approved == 0,
                Task.ended_at.is_(None),
            )
            .subquery()
        )
        visible_ids = select(ranked.c.task_id).where(ranked.c.rn <= visible_task_limit)
        return stmt.where(
            or_(
                Task.ended_at.is_not(None),
                Task.id.in_(visible_ids),
            )
        )

    async def create_task(
        self,
        *,
        name: str,
        intro: str,
        description: str,
        creator_id: int,
        space_id: int,
        category_id: int,
        submitter_type: int,
        deadline: datetime | None,
        registration_start_at: datetime | None,
        participant_limit: int | None,
        default_deadline: int,
        resubmittable: bool,
        editable: bool,
        rank: int | None,
        require_real_name: bool,
        min_team_size: int | None,
        max_team_size: int | None,
        team_locking_policy: str,
        access_control_enabled: bool = False,
        start_mode: str = "DIRECT",
    ) -> Task:
        """Create and persist a new Task row."""
        now = datetime.now(UTC)
        task = Task(
            name=name,
            intro=intro,
            description=description,
            creator_id=creator_id,
            space_id=space_id,
            category_id=category_id,
            submitter_type=submitter_type,
            approved=2,  # ApproveType.NONE
            participant_limit=participant_limit,
            deadline=deadline,
            registration_start_at=registration_start_at,
            default_deadline=default_deadline,
            resubmittable=resubmittable,
            editable=editable,
            rank=rank,
            require_real_name=require_real_name,
            min_team_size=min_team_size,
            max_team_size=max_team_size,
            reject_reason="",
            team_locking_policy=team_locking_policy,
            access_control_enabled=access_control_enabled,
            start_mode=start_mode,
            published_at=None,
            ended_at=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def has_prior_pending_task_for_creator(self, task: Task) -> bool:
        stmt = select(Task.id).where(
            Task.deleted_at.is_(None),
            Task.space_id == task.space_id,
            Task.creator_id == task.creator_id,
            Task.id != task.id,
            Task.approved == 2,
            or_(
                Task.created_at < task.created_at,
                and_(Task.created_at == task.created_at, Task.id < task.id),
            ),
        )
        stmt = stmt.limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def is_task_visible_for_space_limit(
        self,
        *,
        task: Task,
        visible_task_limit: int | None,
    ) -> bool:
        if task.ended_at is not None:
            return True
        if task.approved != 0:
            return False
        if visible_task_limit is None:
            return True
        if visible_task_limit == 0:
            return False

        task_sort = task.published_at or task.created_at
        published_sort = func.coalesce(Task.published_at, Task.created_at)
        stmt = select(func.count(Task.id)).where(
            Task.deleted_at.is_(None),
            Task.space_id == task.space_id,
            Task.creator_id == task.creator_id,
            Task.approved == 0,
            Task.ended_at.is_(None),
            or_(
                published_sort < task_sort,
                and_(published_sort == task_sort, Task.id < task.id),
            ),
        )
        result = await self._session.execute(stmt)
        earlier_count = int(result.scalar_one() or 0)
        return earlier_count < visible_task_limit

    async def save(self, task: Task) -> Task:
        """Flush changes for an existing task."""
        self._session.add(task)
        await self._session.flush()
        return task

    async def replace_topic_relations(
        self, *, task_id: int, topic_ids: Sequence[int]
    ) -> None:
        """覆盖一道题的话题关联：先软删除现有的，再插入这批。

        与 ``PATCH /tasks/{id}`` 的 topics 字段同一语义（整份替换，不做校验）。
        原来这段 SQL 直接写在路由里 —— 「谁可以写这道题的话题、写成什么样」是这一域
        自己的规矩，路由带着 session 自己拼 select/insert 就绕过了 service 这一层。
        """
        now = datetime.now(UTC)
        stmt: Select[tuple[TaskTagRelation]] = select(TaskTagRelation).where(
            TaskTagRelation.task_id == task_id,
            TaskTagRelation.deleted_at.is_(None),
        )
        existing = (await self._session.execute(stmt)).scalars().all()
        for rel in existing:
            rel.deleted_at = now
        for topic_id in topic_ids:
            self._session.add(
                TaskTagRelation(
                    task_id=task_id,
                    tag_id=topic_id,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
        await self._session.flush()

    async def add_topic_relations(
        self, *, task_id: int, topic_ids: Sequence[int]
    ) -> None:
        """给一道新题挂上话题关联行（不做删除，建题时本就没有旧关系）。"""
        now = datetime.now(UTC)
        for topic_id in topic_ids:
            self._session.add(
                TaskTagRelation(
                    task_id=task_id,
                    tag_id=topic_id,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
        await self._session.flush()


class TaskMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_memberships_for_task(
        self,
        task_id: int,
        approved: int | None = None,
    ) -> Sequence[TaskMembership]:
        stmt: Select[tuple[TaskMembership]] = select(TaskMembership).where(
            and_(TaskMembership.task_id == task_id, TaskMembership.deleted_at.is_(None))
        )
        if approved is not None:
            stmt = stmt.where(TaskMembership.approved == approved)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_user_membership(
        self,
        task_id: int,
        user_id: int,
    ) -> TaskMembership | None:
        """The person's own (not team) claim of this task, as
        ``get_by_task_and_member`` picks it."""
        return await self.get_by_task_and_member(task_id, user_id, is_team=False)

    async def list_team_memberships_for_user(
        self,
        task_id: int,
        user_id: int,
    ) -> Sequence[TaskMembership]:
        """Return team-type memberships for this task where the user belongs to the team."""
        membership_stmt: Select[tuple[TaskMembership]] = select(TaskMembership).where(
            and_(
                TaskMembership.task_id == task_id,
                TaskMembership.is_team.is_(True),
                TaskMembership.deleted_at.is_(None),
                exists().where(
                    TeamUserRelation.team_id == TaskMembership.member_id,
                    TeamUserRelation.user_id == user_id,
                    TeamUserRelation.deleted_at.is_(None),
                ),
            )
        )
        result = await self._session.execute(membership_stmt)
        return list(result.scalars().all())

    async def hold_places(self, task_id: int) -> None:
        """锁住这道题的行，直到本事务结束：数名额和写下这一份通过要是一步。

        两份通过同时进来时，不锁的话各自数到「还剩一个」、各自写下，名额就超了。
        锁的是题目这一行（``FOR NO KEY UPDATE``）：两份通过彼此排队，新的申请
        （插入时只拿外键的 ``KEY SHARE``）不被挡住。调用方锁完紧接着数、写，然后提交。
        """
        await self._session.execute(
            select(Task.id).where(Task.id == task_id).with_for_update(key_share=True)
        )

    async def count_approved_for_task(self, task_id: int) -> int:
        """Count approved memberships for a task (ApproveType.APPROVED = 0)."""
        stmt = select(func.count(TaskMembership.id)).where(
            TaskMembership.task_id == task_id,
            TaskMembership.deleted_at.is_(None),
            TaskMembership.approved == 0,
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_memberships_for_task(self, task_id: int) -> int:
        stmt = select(func.count(TaskMembership.id)).where(
            TaskMembership.task_id == task_id,
            TaskMembership.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def count_team_members(self, team_id: int) -> int:
        """Count active members in a team using team_user_relation."""
        stmt = select(func.count(TeamUserRelation.id)).where(
            TeamUserRelation.team_id == team_id,
            TeamUserRelation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def get_by_id(self, membership_id: int) -> TaskMembership | None:
        stmt: Select[tuple[TaskMembership]] = select(TaskMembership).where(
            and_(
                TaskMembership.id == membership_id, TaskMembership.deleted_at.is_(None)
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_memberships_for_space(
        self, space_id: int
    ) -> Sequence[TaskMembership]:
        stmt: Select[tuple[TaskMembership]] = (
            select(TaskMembership)
            .join(Task, Task.id == TaskMembership.task_id)
            .where(
                Task.space_id == space_id,
                Task.deleted_at.is_(None),
                TaskMembership.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_task_and_member(
        self,
        task_id: int,
        member_id: int,
        is_team: bool | None = None,
    ) -> TaskMembership | None:
        """The member's claim of this task: the approved one, else the newest
        that was not rejected, else the newest rejected one. There can be
        several live rows: a rejected application stays beside the one that
        reapplied, and nothing in the database yet stops a duplicate claim
        (two requests before the claim lock existed). This picks one and never
        raises for that. ``is_team`` keeps a person and a team with the same
        number apart (when the caller knows which it means)."""
        conditions = [
            TaskMembership.task_id == task_id,
            TaskMembership.member_id == member_id,
            TaskMembership.deleted_at.is_(None),
        ]
        if is_team is not None:
            conditions.append(TaskMembership.is_team.is_(is_team))
        stmt: Select[tuple[TaskMembership]] = (
            select(TaskMembership)
            .where(and_(*conditions))
            .order_by(
                (TaskMembership.approved == APPROVE_APPROVED).desc(),
                (TaskMembership.approved == APPROVE_DISAPPROVED).asc(),
                TaskMembership.created_at.desc(),
                TaskMembership.id.desc(),
            )
            .limit(1)
        )
        return await self._session.scalar(stmt)

    async def try_lock_claim(self, task_id: int, member_id: int, is_team: bool) -> bool:
        """Take this member's claim of this task until commit, unless another
        transaction holds it now; never waits. The holder may be opening the
        claim's project and making its repository on the forge; a second
        request stuck behind it would time out, so it is told instead."""
        return bool(
            await self._session.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                {"key": f"task-membership:{task_id}:{member_id}:{is_team}"},
            )
        )

    async def save(self, membership: TaskMembership) -> TaskMembership:
        self._session.add(membership)
        await self._session.flush()
        return membership

    # `find_active_locked_memberships` 曾经在这儿：`team` 决定一次成员变更能不能做
    # 的时候问它。这一包在第 4 层、`team` 在第 5 层（团队读任务判权限，任务读团队判
    # 权限，方向是反的），所以那句话现在由 `team/services.py` 的
    # `check_team_locking_status` 自己读 `task` / `task_membership` 两张表来答
    # （同 `topic/repositories/statements.py` 的裸表规矩）。这一份留着只会是同一句
    # `WHERE` 的第二个副本。行为由 `tests/integration/test_bug5_team_locking.py` 看着。


class TopicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_task_id(self, task_id: int) -> Sequence[Tag]:
        stmt: Select[tuple[Tag]] = (
            select(Tag)
            .join(TaskTagRelation, TaskTagRelation.tag_id == Tag.id)
            .where(
                TaskTagRelation.task_id == task_id,
                TaskTagRelation.deleted_at.is_(None),
                Tag.deleted_at.is_(None),
            )
            .order_by(Tag.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_task_ids(self, task_ids: Sequence[int]) -> dict[int, list[Tag]]:
        """一次取回多道题的标签，按题目分组（组内仍按 ``Tag.id`` 排）。

        列表接口一屏就要给一整页题配标签，逐题各发一条是 20 条查询；这条是它的
        批量版本，条件与 ``list_by_task_id`` 逐字一致。没有标签的题不在结果里。
        """
        if not task_ids:
            return {}
        stmt: Select[tuple[int, Tag]] = (
            select(TaskTagRelation.task_id, Tag)
            .join(Tag, TaskTagRelation.tag_id == Tag.id)
            .where(
                TaskTagRelation.task_id.in_(task_ids),
                TaskTagRelation.deleted_at.is_(None),
                Tag.deleted_at.is_(None),
            )
            .order_by(TaskTagRelation.task_id.asc(), Tag.id.asc())
        )
        result = await self._session.execute(stmt)
        grouped: dict[int, list[Tag]] = {}
        for task_id, tag in result.all():
            grouped.setdefault(int(task_id), []).append(tag)
        return grouped


class TaskSubmissionSchemaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_task_id(
        self, task_id: int
    ) -> Sequence[TaskSubmissionSchemaEntry]:
        stmt: Select[tuple[TaskSubmissionSchemaEntry]] = (
            select(TaskSubmissionSchemaEntry)
            .where(TaskSubmissionSchemaEntry.task_id == task_id)
            .order_by(TaskSubmissionSchemaEntry.index.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_task_ids(
        self, task_ids: Sequence[int]
    ) -> dict[int, list[TaskSubmissionSchemaEntry]]:
        """一次取回多道题的表单，按题目分组（组内仍按 ``index`` 排）。

        列表接口一屏就要给一整页题配上表单，逐题各发一条就是 20 条查询；
        这条是它的批量版本。
        """
        if not task_ids:
            return {}
        stmt: Select[tuple[TaskSubmissionSchemaEntry]] = (
            select(TaskSubmissionSchemaEntry)
            .where(TaskSubmissionSchemaEntry.task_id.in_(task_ids))
            .order_by(
                TaskSubmissionSchemaEntry.task_id.asc(),
                TaskSubmissionSchemaEntry.index.asc(),
            )
        )
        result = await self._session.execute(stmt)
        grouped: dict[int, list[TaskSubmissionSchemaEntry]] = {}
        for entry in result.scalars().all():
            grouped.setdefault(entry.task_id, []).append(entry)
        return grouped

    async def replace_schema(
        self,
        task_id: int,
        entries: list[dict],
    ) -> list[TaskSubmissionSchemaEntry]:
        type_map = {"TEXT": 0, "FILE": 1}
        await self._session.execute(
            delete(TaskSubmissionSchemaEntry).where(
                TaskSubmissionSchemaEntry.task_id == task_id
            )
        )
        new_entries = []
        for idx, entry in enumerate(entries):
            prompt = entry.get("prompt", "")
            type_str = entry.get("type", "TEXT").upper()
            type_int = type_map.get(type_str, 0)
            row = TaskSubmissionSchemaEntry(
                task_id=task_id,
                index=idx,
                description=prompt,
                type=type_int,
                stage=stage_of(entry),
            )
            self._session.add(row)
            new_entries.append(row)
        await self._session.flush()
        return new_entries


class TaskRubricRepository:
    """A task's 评分要点, kept in order and replaced as a whole."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_task_id(self, task_id: int) -> list[TaskRubricItem]:
        stmt = (
            select(TaskRubricItem)
            .where(TaskRubricItem.task_id == task_id)
            .order_by(TaskRubricItem.index.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def replace(self, task_id: int, items: Sequence[RubricLine]) -> None:
        await self._session.execute(
            delete(TaskRubricItem).where(TaskRubricItem.task_id == task_id)
        )
        for idx, line in enumerate(items):
            self._session.add(
                TaskRubricItem(
                    task_id=task_id,
                    index=idx,
                    item=line.item,
                    points=line.points,
                    criteria=line.criteria,
                    stage=line.stage,
                )
            )
        await self._session.flush()


class TaskAccessDomainRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_task_id(self, task_id: int) -> list[str]:
        stmt = (
            select(TaskAccessDomain.domain)
            .where(
                TaskAccessDomain.task_id == task_id,
                TaskAccessDomain.deleted_at.is_(None),
            )
            .order_by(TaskAccessDomain.domain.asc())
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def replace_domains(self, *, task_id: int, domains: Sequence[str]) -> None:
        now = datetime.now(UTC)
        stmt: Select[tuple[TaskAccessDomain]] = select(TaskAccessDomain).where(
            TaskAccessDomain.task_id == task_id,
            TaskAccessDomain.deleted_at.is_(None),
        )
        existing = (await self._session.execute(stmt)).scalars().all()
        for item in existing:
            item.deleted_at = now
            item.updated_at = now
        for domain in domains:
            self._session.add(
                TaskAccessDomain(
                    task_id=task_id,
                    domain=domain,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
            )
        await self._session.flush()

    async def soft_delete_by_task(self, *, task_id: int) -> None:
        now = datetime.now(UTC)
        stmt: Select[tuple[TaskAccessDomain]] = select(TaskAccessDomain).where(
            TaskAccessDomain.task_id == task_id,
            TaskAccessDomain.deleted_at.is_(None),
        )
        existing = (await self._session.execute(stmt)).scalars().all()
        for item in existing:
            item.deleted_at = now
            item.updated_at = now
        await self._session.flush()


class TaskMentorRepository:
    """The mentors a task's publisher chose (#3830)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def for_tasks(self, task_ids: Sequence[int]) -> dict[int, list[str]]:
        if not task_ids:
            return {}
        rows = await self._session.execute(
            select(TaskMentor.task_id, TaskMentor.user_handle)
            .where(TaskMentor.task_id.in_(list(task_ids)))
            .order_by(TaskMentor.task_id, TaskMentor.user_handle)
        )
        out: dict[int, list[str]] = {}
        for task_id, handle in rows.all():
            out.setdefault(task_id, []).append(handle)
        return out

    async def of_task(self, task_id: int) -> list[str]:
        return (await self.for_tasks([task_id])).get(task_id, [])

    async def replace(self, task_id: int, handles: Sequence[str]) -> None:
        await self._session.execute(
            delete(TaskMentor).where(TaskMentor.task_id == task_id)
        )
        for handle in dict.fromkeys(handles):
            self._session.add(TaskMentor(task_id=task_id, user_handle=handle))
        await self._session.flush()
