from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.llm.models import AIConversation, AIMessage
from app.domain.task.models import (
    Task,
    TaskAccessDomain,
    TaskAIAdvice,
    TaskAIAdviceContext,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionEntry,
    TaskSubmissionReview,
    TaskSubmissionSchemaEntry,
    TaskTopicsRelation,
)
from app.domain.task.visibility_service import TaskVisibilityService
from app.domain.team.models import TeamUserRelation
from app.domain.topics.models import Topic


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, task_id: int) -> Task | None:
        stmt: Select[tuple[Task]] = select(Task).where(
            and_(Task.id == task_id, Task.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

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
        - Optionally does a naive ILIKE search on name/intro for `keywords`.
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

        if keywords:
            like = f"%{keywords.strip()}%"
            stmt = stmt.where(
                or_(  # type: ignore[name-defined]
                    Task.name.ilike(like),
                    Task.intro.ilike(like),
                )
            )

        if topics:
            stmt = stmt.where(
                exists().where(
                    TaskTopicsRelation.task_id == Task.id,
                    TaskTopicsRelation.deleted_at.is_(None),
                    TaskTopicsRelation.topic_id.in_(list(topics)),
                )
            )

        # joined 过滤：如果传入 joined 且当前用户已知，则根据用户是否参与任务过滤。
        if joined is not None and current_user_id is not None:
            user_member_exists = exists().where(
                TaskMembership.task_id == Task.id,
                TaskMembership.member_id == current_user_id,
                TaskMembership.deleted_at.is_(None),
            )

            team_member_exists = exists().where(
                TaskMembership.task_id == Task.id,
                TaskMembership.deleted_at.is_(None),
                TaskMembership.member_id == TeamUserRelation.team_id,
                TeamUserRelation.user_id == current_user_id,
                TeamUserRelation.deleted_at.is_(None),
            )

            joined_predicate = or_(
                and_(Task.submitter_type == 0, user_member_exists),
                and_(Task.submitter_type == 1, team_member_exists),
            )

            if joined:
                stmt = stmt.where(joined_predicate)
            else:
                stmt = stmt.where(~joined_predicate)

        if viewer_user_id is not None and viewer_user_id > 0 and not viewer_is_space_admin:
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

        # Map sort_by to actual columns; default to updatedAt.
        if sort_by == "createdAt":
            sort_col = Task.created_at
        elif sort_by == "publishedAt":
            sort_col = func.coalesce(Task.published_at, Task.created_at)
        elif sort_by == "deadline":
            sort_col = Task.deadline
        else:
            sort_col = Task.updated_at

        desc = sort_order.lower() == "desc"
        if desc:
            stmt = stmt.order_by(sort_col.desc(), Task.id.desc())
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

        if keywords:
            like = f"%{keywords.strip()}%"
            stmt = stmt.where(
                or_(
                    Task.name.ilike(like),
                    Task.intro.ilike(like),
                )
            )

        if topics:
            stmt = stmt.where(
                exists().where(
                    TaskTopicsRelation.task_id == Task.id,
                    TaskTopicsRelation.deleted_at.is_(None),
                    TaskTopicsRelation.topic_id.in_(list(topics)),
                )
            )

        if joined is not None and current_user_id is not None:
            user_member_exists = exists().where(
                TaskMembership.task_id == Task.id,
                TaskMembership.member_id == current_user_id,
                TaskMembership.deleted_at.is_(None),
            )

            team_member_exists = exists().where(
                TaskMembership.task_id == Task.id,
                TaskMembership.deleted_at.is_(None),
                TaskMembership.member_id == TeamUserRelation.team_id,
                TeamUserRelation.user_id == current_user_id,
                TeamUserRelation.deleted_at.is_(None),
            )

            joined_predicate = or_(
                and_(Task.submitter_type == 0, user_member_exists),
                and_(Task.submitter_type == 1, team_member_exists),
            )

            if joined:
                stmt = stmt.where(joined_predicate)
            else:
                stmt = stmt.where(~joined_predicate)

        if viewer_user_id is not None and viewer_user_id > 0 and not viewer_is_space_admin:
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
                or_(Task.participant_limit.is_(None), approved_count < Task.participant_limit),
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
        video_url: str | None = None,
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
            video_url=video_url,
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
            and_(TaskMembership.id == membership_id, TaskMembership.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_memberships_for_space(self, space_id: int) -> Sequence[TaskMembership]:
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
            and_(TaskSubmission.id == submission_id, TaskSubmission.deleted_at.is_(None))
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
        - 当 all_versions=False 时，仅返回每个 membership 的最新版本（按 version 最大）。
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
        entries: list[tuple[int, str | None, int | None]],
    ) -> None:
        now = datetime.now(UTC)
        for idx, text, attachment_id in entries:
            row = TaskSubmissionEntry(
                task_submission_id=submission_id,
                index=idx,
                content_text=text,
                content_attachment_id=attachment_id,
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


class TaskAIAdviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_task(self, task_id: int) -> list[TaskAIAdvice]:
        stmt: Select[tuple[TaskAIAdvice]] = (
            select(TaskAIAdvice)
            .where(TaskAIAdvice.task_id == task_id)
            .order_by(TaskAIAdvice.updated_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest(self, task_id: int) -> TaskAIAdvice | None:
        stmt = (
            select(TaskAIAdvice)
            .where(TaskAIAdvice.task_id == task_id)
            .order_by(TaskAIAdvice.updated_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_model_hash(self, task_id: int, model_hash: str) -> TaskAIAdvice | None:
        stmt = select(TaskAIAdvice).where(
            TaskAIAdvice.task_id == task_id,
            TaskAIAdvice.model_hash == model_hash,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        task_id: int,
        model_hash: str,
        status: str,
        topic_summary: str | None = None,
        knowledge_fields: str | None = None,
        learning_paths: str | None = None,
        methodology: str | None = None,
        team_tips: str | None = None,
        raw_response: str | None = None,
    ) -> TaskAIAdvice:
        advice = TaskAIAdvice(
            task_id=task_id,
            model_hash=model_hash,
            status=status,
            topic_summary=topic_summary,
            knowledge_fields=knowledge_fields,
            learning_paths=learning_paths,
            methodology=methodology,
            team_tips=team_tips,
            raw_response=raw_response,
        )
        self._session.add(advice)
        await self._session.flush()
        return advice

    async def save(self, advice: TaskAIAdvice) -> TaskAIAdvice:
        self._session.add(advice)
        await self._session.flush()
        return advice


class TaskAIAdviceContextRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self,
        *,
        task_id: int,
        section: str | None,
        section_index: int | None,
    ) -> TaskAIAdviceContext:
        stmt = select(TaskAIAdviceContext).where(
            TaskAIAdviceContext.task_id == task_id,
            TaskAIAdviceContext.section == section
            if section is not None
            else TaskAIAdviceContext.section.is_(None),
            TaskAIAdviceContext.section_index == section_index
            if section_index is not None
            else TaskAIAdviceContext.section_index.is_(None),
            TaskAIAdviceContext.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        entity = result.scalar_one_or_none()
        if entity is not None:
            return entity
        ctx = TaskAIAdviceContext(
            task_id=task_id,
            section=section,
            section_index=section_index,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._session.add(ctx)
        await self._session.flush()
        return ctx


class AIConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_task(self, task_id: int) -> list[AIConversation]:
        stmt = (
            select(AIConversation)
            .where(
                AIConversation.context_id == task_id,
                AIConversation.module_type == "task_ai_advice",
                AIConversation.deleted_at.is_(None),
            )
            .order_by(AIConversation.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_conversation_id(self, conversation_id: str) -> AIConversation | None:
        stmt = select(AIConversation).where(
            AIConversation.conversation_id == conversation_id,
            AIConversation.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        conversation_id: str,
        task_id: int,
        owner_id: int,
        title: str | None,
    ) -> AIConversation:
        now = datetime.now(UTC)
        convo = AIConversation(
            conversation_id=conversation_id,
            context_id=task_id,
            owner_id=owner_id,
            title=title,
            module_type="task_ai_advice",
            created_at=now,
            updated_at=now,
        )
        self._session.add(convo)
        await self._session.flush()
        return convo

    async def soft_delete(self, conversation: AIConversation) -> None:
        conversation.deleted_at = datetime.now(UTC)
        self._session.add(conversation)
        await self._session.flush()


class AIMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_conversation(self, conversation_id: int) -> list[AIMessage]:
        stmt = (
            select(AIMessage)
            .where(
                AIMessage.conversation_id == conversation_id,
                AIMessage.deleted_at.is_(None),
            )
            .order_by(AIMessage.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_message(
        self,
        *,
        conversation_id: int,
        role: str,
        content: str,
        parent_id: int | None = None,
        tokens_used: int | None = None,
    ) -> AIMessage:
        msg = AIMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            parent_id=parent_id,
            tokens_used=tokens_used,
        )
        self._session.add(msg)
        await self._session.flush()
        return msg

    async def soft_delete(self, message: AIMessage) -> None:
        now = datetime.now(UTC)
        message.deleted_at = now
        message.updated_at = now
        await self._session.flush()


class TopicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_task_id(self, task_id: int) -> Sequence[Topic]:
        stmt: Select[tuple[Topic]] = (
            select(Topic)
            .join(TaskTopicsRelation, TaskTopicsRelation.topic_id == Topic.id)
            .where(
                TaskTopicsRelation.task_id == task_id,
                TaskTopicsRelation.deleted_at.is_(None),
                Topic.deleted_at.is_(None),
            )
            .order_by(Topic.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class TaskSubmissionSchemaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_by_task_id(self, task_id: int) -> Sequence[TaskSubmissionSchemaEntry]:
        stmt: Select[tuple[TaskSubmissionSchemaEntry]] = (
            select(TaskSubmissionSchemaEntry)
            .where(TaskSubmissionSchemaEntry.task_id == task_id)
            .order_by(TaskSubmissionSchemaEntry.index.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def replace_schema(
        self,
        task_id: int,
        entries: list[dict],
    ) -> list[TaskSubmissionSchemaEntry]:
        type_map = {"TEXT": 0, "FILE": 1}
        await self._session.execute(
            TaskSubmissionSchemaEntry.__table__.delete().where(
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
