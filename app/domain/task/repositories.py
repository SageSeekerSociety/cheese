from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskTopicsRelation,
    TaskSubmission,
    TaskSubmissionEntry,
    TaskSubmissionReview,
)
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
        limit: int,
        offset: int = 0,
        sort_by: str = "updatedAt",
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

        # Map sort_by to actual columns; default to updatedAt.
        if sort_by == "createdAt":
            sort_col = Task.created_at
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

        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

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
        registration_start_at: datetime | None,
        deadline: datetime | None,
        participant_limit: int | None,
        default_deadline: int,
        resubmittable: bool,
        editable: bool,
        rank: int | None,
        require_real_name: bool,
        min_team_size: int | None,
        max_team_size: int | None,
        team_locking_policy: str,
    ) -> Task:
        """Create and persist a new Task row."""
        now = datetime.now(timezone.utc)
        task = Task(
            name=name,
            intro=intro,
            description=description,
            creator_id=creator_id,
            space_id=space_id,
            category_id=category_id,
            registration_start_at=registration_start_at,
            submitter_type=submitter_type,
            approved=2,  # ApproveType.NONE
            participant_limit=participant_limit,
            deadline=deadline,
            default_deadline=default_deadline,
            resubmittable=resubmittable,
            editable=editable,
            rank=rank,
            require_real_name=require_real_name,
            min_team_size=min_team_size,
            max_team_size=max_team_size,
            reject_reason="",
            team_locking_policy=team_locking_policy,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(task)
        await self._session.flush()
        return task

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
        now = datetime.now(timezone.utc)
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
        stmt: Select[tuple[TaskSubmission]] = select(TaskSubmission).join(
            TaskMembership, TaskSubmission.membership_id == TaskMembership.id
        ).join(Task, TaskMembership.task_id == Task.id)
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
        stmt = select(func.count(TaskSubmission.id)).join(
            TaskMembership, TaskSubmission.membership_id == TaskMembership.id
        ).join(Task, TaskMembership.task_id == Task.id)
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
        stmt: Select[tuple[TaskSubmissionEntry]] = select(TaskSubmissionEntry).where(
            TaskSubmissionEntry.task_submission_id == submission_id,
            TaskSubmissionEntry.deleted_at.is_(None),
        ).order_by(TaskSubmissionEntry.index.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def create_entries(
        self,
        *,
        submission_id: int,
        entries: list[tuple[int, str | None, int | None]],
    ) -> None:
        now = datetime.now(timezone.utc)
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
        now = datetime.now(timezone.utc)
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
        now = datetime.now(timezone.utc)
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
        review.deleted_at = datetime.utcnow()
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
            TaskAIAdviceContext.section.is_(section),
            TaskAIAdviceContext.section_index.is_(section_index),
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
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
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
        convo = AIConversation(
            conversation_id=conversation_id,
            context_id=task_id,
            owner_id=owner_id,
            title=title,
            module_type="task_ai_advice",
        )
        self._session.add(convo)
        await self._session.flush()
        return convo

    async def soft_delete(self, conversation: AIConversation) -> None:
        conversation.deleted_at = datetime.utcnow()
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

    async def soft_delete(self, review: TaskSubmissionReview) -> None:
        review.deleted_at = datetime.now(timezone.utc)
        review.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
