from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import CTE, Select, and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.search import bm25
from app.domain.tag.models import Tag
from app.domain.task.models import (
    Task,
    TaskAccessDomain,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionEntry,
    TaskSubmissionReview,
    TaskSubmissionSchemaEntry,
    TaskTagRelation,
)
from app.domain.task.visibility_service import TaskVisibilityService
from app.domain.team.models import TeamUserRelation


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, task_id: int) -> Task | None:
        stmt: Select[tuple[Task]] = select(Task).where(
            and_(Task.id == task_id, Task.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def joined_by(user_id: int):
        """The tasks ``user_id`` takes part in: as themselves on an individual
        task, through one of their teams on a team task."""
        user_member_exists = exists().where(
            TaskMembership.task_id == Task.id,
            TaskMembership.member_id == user_id,
            TaskMembership.deleted_at.is_(None),
        )
        team_member_exists = exists().where(
            TaskMembership.task_id == Task.id,
            TaskMembership.deleted_at.is_(None),
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
        if lifecycle == "recruiting":
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
            return stmt.where(
                Task.ended_at.is_(None),
                or_(
                    Task.participant_limit.is_(None),
                    approved_count < Task.participant_limit,
                ),
            )
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
        """Return membership row for a USER-type participant, if any."""
        stmt: Select[tuple[TaskMembership]] = select(TaskMembership).where(
            and_(
                TaskMembership.task_id == task_id,
                TaskMembership.member_id == user_id,
                TaskMembership.is_team.is_(False),
                TaskMembership.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_team_memberships_for_user(
        self,
        task_id: int,
        user_id: int,
    ) -> Sequence[TaskMembership]:
        """Return team-type memberships for this task where the user belongs to the team."""  # noqa: E501
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
    ) -> TaskMembership | None:
        stmt: Select[tuple[TaskMembership]] = select(TaskMembership).where(
            and_(
                TaskMembership.task_id == task_id,
                TaskMembership.member_id == member_id,
                TaskMembership.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, membership: TaskMembership) -> TaskMembership:
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def find_active_locked_memberships(
        self,
        team_id: int,
        locking_policies: list[str],
    ) -> Sequence[TaskMembership]:
        """Find team task memberships that impose a lock on team changes.

        Mirrors NT TaskMembershipRepository.findActiveMembershipsWithOngoingLock:
        returns TaskMembership rows where the team is APPROVED, the task has a
        matching locking policy, and the completion status is still ongoing.
        """
        ongoing_statuses = ["NOT_SUBMITTED", "PENDING_REVIEW", "REJECTED_RESUBMITTABLE"]
        now = datetime.now(UTC)
        stmt: Select[tuple[TaskMembership]] = (
            select(TaskMembership)
            .join(Task, Task.id == TaskMembership.task_id)
            .where(
                TaskMembership.member_id == team_id,
                TaskMembership.is_team.is_(True),
                TaskMembership.approved == 0,  # APPROVED
                TaskMembership.deleted_at.is_(None),
                Task.deleted_at.is_(None),
                Task.team_locking_policy.in_(locking_policies),
                TaskMembership.completion_status.in_(ongoing_statuses),
                or_(
                    TaskMembership.deadline.is_(None),
                    TaskMembership.deadline > now,
                ),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class TaskSubmissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, submission_id: int) -> TaskSubmission | None:
        stmt: Select[tuple[TaskSubmission]] = select(TaskSubmission).where(
            and_(
                TaskSubmission.id == submission_id, TaskSubmission.deleted_at.is_(None)
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_submission(
        self,
        *,
        membership_id: int,
        submitter_id: int,
        version: int,
    ) -> TaskSubmission:
        now = datetime.now(UTC)
        submission = TaskSubmission(
            membership_id=membership_id,
            submitter_id=submitter_id,
            version=version,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(submission)
        await self._session.flush()
        return submission

    async def get_latest_version_for_membership(self, membership_id: int) -> int:
        stmt = select(func.max(TaskSubmission.version)).where(
            TaskSubmission.membership_id == membership_id,
            TaskSubmission.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        value = result.scalar_one()
        return int(value or 0)

    async def get_by_membership_and_version(
        self,
        membership_id: int,
        version: int,
    ) -> TaskSubmission | None:
        stmt: Select[tuple[TaskSubmission]] = select(TaskSubmission).where(
            TaskSubmission.membership_id == membership_id,
            TaskSubmission.version == version,
            TaskSubmission.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, submission: TaskSubmission) -> TaskSubmission:
        self._session.add(submission)
        await self._session.flush()
        return submission

    async def list_review_verdicts_for_memberships(
        self,
        *,
        membership_ids: Sequence[int],
    ) -> dict[int, list[bool | None]]:
        """这些领取名下**每条 live 提交**的判决，按领取分组。

        判决三态照 ``app.domain.task.submission_state`` 的口径：``True`` = 判过、
        ``False`` = 退回、``None`` = 还没判（没有 live 评审行）。两个「live」的条件
        与那边一致：提交行与评审行各自 ``deleted_at IS NULL``。

        一页题一次性问完：逐题各发一条就是 20 条查询，而这一条按领取去重后只跑一次
        （`has_work_in_hand` 那边是相关的 EXISTS 谓词，用途不同，不通用）。
        """
        if not membership_ids:
            return {}
        stmt = (
            select(TaskSubmission.membership_id, TaskSubmissionReview.accepted)
            .select_from(TaskSubmission)
            .outerjoin(
                TaskSubmissionReview,
                and_(
                    TaskSubmissionReview.submission_id == TaskSubmission.id,
                    TaskSubmissionReview.deleted_at.is_(None),
                ),
            )
            .where(
                TaskSubmission.membership_id.in_(membership_ids),
                TaskSubmission.deleted_at.is_(None),
            )
            .order_by(TaskSubmission.id.asc())
        )
        result = await self._session.execute(stmt)
        verdicts: dict[int, list[bool | None]] = {}
        for membership_id, accepted in result.all():
            verdicts.setdefault(int(membership_id), []).append(
                None if accepted is None else bool(accepted)
            )
        return verdicts

    async def list_submissions(
        self,
        *,
        task_id: int,
        participant_id: int | None = None,
        all_versions: bool = False,
        reviewed: bool | None = None,
        limit: int,
        offset: int = 0,
        sort_by: str = "updatedAt",
        sort_order: str = "desc",
    ) -> Sequence[TaskSubmission]:
        """List submissions for a task with optional participant/review filters.

        NOTE: 简化实现：
        - 使用 offset 分页；
        - 当 all_versions=False 时，
          仅返回每个 membership 的最新版本（按 version 最大）。
        """
        # Base query: join membership -> task for filtering
        stmt: Select[tuple[TaskSubmission]] = (
            select(TaskSubmission)
            .join(TaskMembership, TaskSubmission.membership_id == TaskMembership.id)
            .join(Task, TaskMembership.task_id == Task.id)
        )
        stmt = stmt.where(
            Task.deleted_at.is_(None),
            TaskSubmission.deleted_at.is_(None),
            TaskMembership.deleted_at.is_(None),
            Task.id == task_id,
        )

        if participant_id is not None:
            stmt = stmt.where(TaskMembership.id == participant_id)

        if not all_versions:
            latest_subq = (
                select(
                    TaskSubmission.membership_id,
                    func.max(TaskSubmission.version).label("max_version"),
                )
                .where(TaskSubmission.deleted_at.is_(None))
                .group_by(TaskSubmission.membership_id)
                .subquery()
            )
            stmt = stmt.join(
                latest_subq,
                and_(
                    TaskSubmission.membership_id == latest_subq.c.membership_id,
                    TaskSubmission.version == latest_subq.c.max_version,
                ),
            )

        if reviewed is not None:
            # LEFT JOIN review and filter by existence
            review_exists = exists().where(
                TaskSubmissionReview.submission_id == TaskSubmission.id,
                TaskSubmissionReview.deleted_at.is_(None),
            )
            if reviewed:
                stmt = stmt.where(review_exists)
            else:
                stmt = stmt.where(~review_exists)

        if sort_by == "createdAt":
            sort_col = TaskSubmission.created_at
        else:
            sort_col = TaskSubmission.updated_at
        desc = sort_order.lower() == "desc"
        if desc:
            stmt = stmt.order_by(sort_col.desc(), TaskSubmission.id.desc())
        else:
            stmt = stmt.order_by(sort_col.asc(), TaskSubmission.id.asc())

        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_submissions(
        self,
        *,
        task_id: int,
        participant_id: int | None = None,
        all_versions: bool = False,
        reviewed: bool | None = None,
    ) -> int:
        stmt = (
            select(func.count(TaskSubmission.id))
            .join(TaskMembership, TaskSubmission.membership_id == TaskMembership.id)
            .join(Task, TaskMembership.task_id == Task.id)
        )
        stmt = stmt.where(
            Task.deleted_at.is_(None),
            TaskSubmission.deleted_at.is_(None),
            TaskMembership.deleted_at.is_(None),
            Task.id == task_id,
        )

        if participant_id is not None:
            stmt = stmt.where(TaskMembership.id == participant_id)

        if not all_versions:
            latest_subq = (
                select(
                    TaskSubmission.membership_id,
                    func.max(TaskSubmission.version).label("max_version"),
                )
                .where(TaskSubmission.deleted_at.is_(None))
                .group_by(TaskSubmission.membership_id)
                .subquery()
            )
            stmt = stmt.join(
                latest_subq,
                and_(
                    TaskSubmission.membership_id == latest_subq.c.membership_id,
                    TaskSubmission.version == latest_subq.c.max_version,
                ),
            )

        if reviewed is not None:
            review_exists = exists().where(
                TaskSubmissionReview.submission_id == TaskSubmission.id,
                TaskSubmissionReview.deleted_at.is_(None),
            )
            if reviewed:
                stmt = stmt.where(review_exists)
            else:
                stmt = stmt.where(~review_exists)

        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_for_space(
        self,
        *,
        space_id: int,
        task_id: int | None = None,
        reviewed: bool | None = None,
        limit: int,
        offset: int = 0,
        sort_by: str = "createdAt",
        sort_order: str = "desc",
    ) -> Sequence[tuple[TaskSubmission, TaskMembership, Task]]:
        """一个题目板里每个人的最新一版提交，连它属于哪道题。

        课程的「作业与验收」要一屏看整门课。逐道题 × 逐个成员地问一遍是 N×M 次
        请求，所以这一处按板子取一次；每行都带着 ``Task``，因为管理员看的是「谁的
        哪份作业」，只有提交是不知道是哪道题的。

        与 ``list_submissions`` 的关系：那条按一道题取，这条按一块板取，其余
        （只取每人最新版、按是否评审过滤、排序与分页）逐条相同。
        """
        stmt: Select[tuple[TaskSubmission, TaskMembership, Task]] = (
            select(TaskSubmission, TaskMembership, Task)
            .join(TaskMembership, TaskSubmission.membership_id == TaskMembership.id)
            .join(Task, TaskMembership.task_id == Task.id)
            .where(
                Task.deleted_at.is_(None),
                TaskSubmission.deleted_at.is_(None),
                TaskMembership.deleted_at.is_(None),
                Task.space_id == space_id,
            )
        )
        if task_id is not None:
            stmt = stmt.where(Task.id == task_id)

        latest_subq = (
            select(
                TaskSubmission.membership_id,
                func.max(TaskSubmission.version).label("max_version"),
            )
            .where(TaskSubmission.deleted_at.is_(None))
            .group_by(TaskSubmission.membership_id)
            .subquery()
        )
        stmt = stmt.join(
            latest_subq,
            and_(
                TaskSubmission.membership_id == latest_subq.c.membership_id,
                TaskSubmission.version == latest_subq.c.max_version,
            ),
        )

        if reviewed is not None:
            review_exists = exists().where(
                TaskSubmissionReview.submission_id == TaskSubmission.id,
                TaskSubmissionReview.deleted_at.is_(None),
            )
            if reviewed:
                stmt = stmt.where(review_exists)
            else:
                stmt = stmt.where(~review_exists)

        if sort_by == "updatedAt":
            sort_col = TaskSubmission.updated_at
        else:
            sort_col = TaskSubmission.created_at
        if sort_order.lower() == "desc":
            stmt = stmt.order_by(sort_col.desc(), TaskSubmission.id.desc())
        else:
            stmt = stmt.order_by(sort_col.asc(), TaskSubmission.id.asc())

        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return [(row[0], row[1], row[2]) for row in result.all()]

    async def count_for_space(
        self,
        *,
        space_id: int,
        task_id: int | None = None,
        reviewed: bool | None = None,
    ) -> int:
        """``list_for_space`` 的总数，同样是「每人最新一版」的口径。

        因为每人只算一版，这个数也正好是**交过东西的人数**——课程那一屏拿它算
        「还有多少人没交」。
        """
        stmt = (
            select(func.count(TaskSubmission.id))
            .join(TaskMembership, TaskSubmission.membership_id == TaskMembership.id)
            .join(Task, TaskMembership.task_id == Task.id)
            .where(
                Task.deleted_at.is_(None),
                TaskSubmission.deleted_at.is_(None),
                TaskMembership.deleted_at.is_(None),
                Task.space_id == space_id,
            )
        )
        if task_id is not None:
            stmt = stmt.where(Task.id == task_id)

        latest_subq = (
            select(
                TaskSubmission.membership_id,
                func.max(TaskSubmission.version).label("max_version"),
            )
            .where(TaskSubmission.deleted_at.is_(None))
            .group_by(TaskSubmission.membership_id)
            .subquery()
        )
        stmt = stmt.join(
            latest_subq,
            and_(
                TaskSubmission.membership_id == latest_subq.c.membership_id,
                TaskSubmission.version == latest_subq.c.max_version,
            ),
        )

        if reviewed is not None:
            review_exists = exists().where(
                TaskSubmissionReview.submission_id == TaskSubmission.id,
                TaskSubmissionReview.deleted_at.is_(None),
            )
            if reviewed:
                stmt = stmt.where(review_exists)
            else:
                stmt = stmt.where(~review_exists)

        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)


class TaskSubmissionEntryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_submission_id(
        self,
        submission_id: int,
    ) -> Sequence[TaskSubmissionEntry]:
        stmt: Select[tuple[TaskSubmissionEntry]] = (
            select(TaskSubmissionEntry)
            .where(
                TaskSubmissionEntry.task_submission_id == submission_id,
                TaskSubmissionEntry.deleted_at.is_(None),
            )
            .order_by(TaskSubmissionEntry.index.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_entries(
        self,
        *,
        submission_id: int,
        entries: list[tuple[int, str | None, int | None, str | None]],
    ) -> None:
        now = datetime.now(UTC)
        for idx, text, attachment_id, prompt in entries:
            row = TaskSubmissionEntry(
                task_submission_id=submission_id,
                index=idx,
                content_text=text,
                content_attachment_id=attachment_id,
                prompt=prompt,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            self._session.add(row)
        await self._session.flush()

    async def soft_delete_by_membership_and_version(
        self,
        membership_id: int,
        version: int,
    ) -> None:
        """Soft delete entries for all submissions of given (membership, version)."""
        now = datetime.now(UTC)
        subq = select(TaskSubmission.id).where(
            TaskSubmission.membership_id == membership_id,
            TaskSubmission.version == version,
            TaskSubmission.deleted_at.is_(None),
        )
        stmt = select(TaskSubmissionEntry).where(
            TaskSubmissionEntry.task_submission_id.in_(subq),
            TaskSubmissionEntry.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        for row in rows:
            row.deleted_at = now
        if rows:
            await self._session.flush()


class TaskSubmissionReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_submission_id(
        self,
        submission_id: int,
    ) -> TaskSubmissionReview | None:
        stmt: Select[tuple[TaskSubmissionReview]] = select(TaskSubmissionReview).where(
            TaskSubmissionReview.submission_id == submission_id,
            TaskSubmissionReview.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists_by_submission_id(self, submission_id: int) -> bool:
        stmt = select(TaskSubmissionReview.id).where(
            TaskSubmissionReview.submission_id == submission_id,
            TaskSubmissionReview.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def create_review(
        self,
        *,
        submission_id: int,
        accepted: bool,
        score: int,
        comment: str,
    ) -> TaskSubmissionReview:
        now = datetime.now(UTC)
        review = TaskSubmissionReview(
            submission_id=submission_id,
            accepted=accepted,
            score=score,
            comment=comment,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(review)
        await self._session.flush()
        return review

    async def save(self, review: TaskSubmissionReview) -> TaskSubmissionReview:
        self._session.add(review)
        await self._session.flush()
        return review

    async def soft_delete(self, review: TaskSubmissionReview) -> None:
        review.deleted_at = datetime.now(UTC)
        self._session.add(review)
        await self._session.flush()


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
            )
            self._session.add(row)
            new_entries.append(row)
        await self._session.flush()
        return new_entries


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
