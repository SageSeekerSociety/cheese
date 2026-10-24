"""提交相关仓储：提交、提交条目、评审。

从 ``repositories.py``（拆分前 1332 行）里拆出来：提交这一块的数据库访问集中在这里，
其余仓储留在原处。SQL 与方法签名与原文件逐字相同，``repositories.py`` 继续 re-export
下面三个类，所以 ``from app.domain.task.repositories import TaskSubmissionRepository``
照旧可用。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionEntry,
    TaskSubmissionReview,
)

__all__ = [
    "TaskSubmissionEntryRepository",
    "TaskSubmissionRepository",
    "TaskSubmissionReviewRepository",
]


def _latest_only(stmt):
    """Keep only each membership's latest live version in each stage: a stage is
    judged by its latest submission (``submission_state``), so "the latest" is
    per stage, not per membership."""
    latest = (
        select(
            TaskSubmission.membership_id,
            TaskSubmission.stage,
            func.max(TaskSubmission.version).label("max_version"),
        )
        .where(TaskSubmission.deleted_at.is_(None))
        .group_by(TaskSubmission.membership_id, TaskSubmission.stage)
        .subquery()
    )
    return stmt.join(
        latest,
        and_(
            TaskSubmission.membership_id == latest.c.membership_id,
            TaskSubmission.stage == latest.c.stage,
            TaskSubmission.version == latest.c.max_version,
        ),
    )


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
        stage: int = 0,
        package_revision: int | None = None,
    ) -> TaskSubmission:
        now = datetime.now(UTC)
        submission = TaskSubmission(
            membership_id=membership_id,
            submitter_id=submitter_id,
            version=version,
            stage=stage,
            package_revision=package_revision,
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
        stage: int | None = None,
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
          仅返回每个 membership 在每个阶段的最新版本（按 version 最大）。
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
        if stage is not None:
            stmt = stmt.where(TaskSubmission.stage == stage)

        if not all_versions:
            stmt = _latest_only(stmt)

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
        stage: int | None = None,
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
        if stage is not None:
            stmt = stmt.where(TaskSubmission.stage == stage)

        if not all_versions:
            stmt = _latest_only(stmt)

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

        stmt = _latest_only(stmt)

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
        """``list_for_space`` 的总数，同样是「每人每个阶段最新一版」的口径。"""
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

        stmt = _latest_only(stmt)

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

    async def count_members_handed_in_for_space(
        self, *, space_id: int, task_id: int | None = None
    ) -> int:
        """这块板上交过东西（任一阶段、任一版 live 提交）的领取有几条 —— 课程那一屏
        拿它算「还有多少人没交」。"""
        stmt = (
            select(func.count(func.distinct(TaskSubmission.membership_id)))
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
        return int((await self._session.execute(stmt)).scalar_one() or 0)


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

    async def list_by_submission_ids(
        self,
        submission_ids: Sequence[int],
    ) -> dict[int, list[TaskSubmissionEntry]]:
        """一次取回多份提交的条目，按提交分组（组内仍按 ``index`` 排）。

        提交列表一屏就要给一整页提交配条目，逐条各发一条就是 20 条查询；这条是
        ``list_by_submission_id`` 的批量版本，过滤条件与排序逐字一致。没有条目的
        提交不在结果里。
        """
        if not submission_ids:
            return {}
        stmt: Select[tuple[TaskSubmissionEntry]] = (
            select(TaskSubmissionEntry)
            .where(
                TaskSubmissionEntry.task_submission_id.in_(submission_ids),
                TaskSubmissionEntry.deleted_at.is_(None),
            )
            .order_by(
                TaskSubmissionEntry.task_submission_id.asc(),
                TaskSubmissionEntry.index.asc(),
            )
        )
        result = await self._session.execute(stmt)
        grouped: dict[int, list[TaskSubmissionEntry]] = {}
        for entry in result.scalars().all():
            grouped.setdefault(entry.task_submission_id, []).append(entry)
        return grouped

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
        reviewer_id: int | None = None,
    ) -> TaskSubmissionReview | None:
        """这一版的一条评审：给了 `reviewer_id` 就是那个人的那一条，没给就是最早的
        一条（旧读者只要一条）。`reviewer_id` 是必须的，因为一版可以有多条。"""
        stmt: Select[tuple[TaskSubmissionReview]] = (
            select(TaskSubmissionReview)
            .where(
                TaskSubmissionReview.submission_id == submission_id,
                TaskSubmissionReview.deleted_at.is_(None),
            )
            .order_by(TaskSubmissionReview.created_at, TaskSubmissionReview.id)
        )
        if reviewer_id is not None:
            stmt = stmt.where(TaskSubmissionReview.reviewer_id == reviewer_id)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_by_submission_ids(
        self,
        submission_ids: Sequence[int],
    ) -> dict[int, TaskSubmissionReview]:
        """一次取回多份提交的评审，按提交 id 归位。

        ``get_by_submission_id`` 的批量版本，条件与它逐字一致。一版可以有多条 live
        评审（每位老师一条），这里各取最早的那一条，与逐个调它再不带 `reviewer_id`
        一样。没有评审的提交不在结果里。
        """
        if not submission_ids:
            return {}
        stmt: Select[tuple[TaskSubmissionReview]] = (
            select(TaskSubmissionReview)
            .where(
                TaskSubmissionReview.submission_id.in_(submission_ids),
                TaskSubmissionReview.deleted_at.is_(None),
            )
            .order_by(TaskSubmissionReview.created_at, TaskSubmissionReview.id)
        )
        result = await self._session.execute(stmt)
        first: dict[int, TaskSubmissionReview] = {}
        for review in result.scalars().all():
            first.setdefault(review.submission_id, review)
        return first

    async def exists_by_submission_id(self, submission_id: int) -> bool:
        stmt = select(TaskSubmissionReview.id).where(
            TaskSubmissionReview.submission_id == submission_id,
            TaskSubmissionReview.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first() is not None

    async def create_review(
        self,
        *,
        submission_id: int,
        reviewer_id: int,
        accepted: bool,
        score: int,
        comment: str,
    ) -> TaskSubmissionReview:
        now = datetime.now(UTC)
        review = TaskSubmissionReview(
            submission_id=submission_id,
            reviewer_id=reviewer_id,
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
