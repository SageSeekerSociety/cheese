from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.domain.space.models import Space, SpaceCategory
from app.domain.space.repositories import SpaceCategoryRepository, SpaceRepository
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)

APPROVED_MAP = {
    "APPROVED": 0,
    "DISAPPROVED": 1,
    "NONE": 2,
}
APPROVED_REVERSE_MAP = {value: key for key, value in APPROVED_MAP.items()}
PENDING_REVIEW_STATUSES = {"PENDING_REVIEW"}
FAILED_STATUSES = {"FAILED", "REJECTED_RESUBMITTABLE"}
SUCCESS_STATUSES = {"SUCCESS"}
MY_PUBLISHING_SORT_FIELDS = {
    "createdAt",
    "publishedAt",
    "participantCount",
    "pendingReviewCount",
    "successRate",
}


class SpaceMemberPublishingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._space_repo = SpaceRepository(session=session)
        self._category_repo = SpaceCategoryRepository(session=session)

    async def get_my_publishing_overview(self, *, space_id: int, user_id: int) -> dict:
        space = await self._ensure_space_exists(space_id)

        tasks = await self._list_my_publishing_tasks(space_id=space_id, user_id=user_id)
        task_items = await self._build_my_published_task_items(
            tasks=tasks,
            space_id=space_id,
            visible_task_limit=getattr(space, "visible_task_limit", None),
        )

        return {
            "spaceId": space_id,
            "taskCount": len(task_items),
            "approvedTaskCount": sum(
                1 for task in tasks if task.approved == APPROVED_MAP["APPROVED"]
            ),
            "pendingTaskApprovalCount": sum(
                1 for task in tasks if task.approved == APPROVED_MAP["NONE"]
            ),
            "disapprovedTaskCount": sum(
                1 for task in tasks if task.approved == APPROVED_MAP["DISAPPROVED"]
            ),
            "participantCount": sum(item["participantCount"] for item in task_items),
            "approvedParticipantCount": sum(
                item["approvedParticipantCount"] for item in task_items
            ),
            "pendingParticipantApprovalCount": sum(
                item["pendingParticipantApprovalCount"] for item in task_items
            ),
            "submittedParticipantCount": sum(
                item["submittedParticipantCount"] for item in task_items
            ),
            "pendingReviewCount": sum(
                item["pendingReviewCount"] for item in task_items
            ),
            "successfulParticipantCount": sum(
                item["successfulParticipantCount"] for item in task_items
            ),
        }

    async def get_my_published_tasks(
        self,
        *,
        space_id: int,
        user_id: int,
        from_ts: int | None = None,
        to_ts: int | None = None,
        category_id: int | None = None,
        approved: str | None = None,
        has_pending_participant_approval: bool | None = None,
        has_pending_review: bool | None = None,
        sort_by: str = "publishedAt",
        sort_order: str = "desc",
    ) -> list[dict]:
        space = await self._ensure_space_exists(space_id)

        approved_value = self._parse_approved_filter(approved)
        normalized_sort_by = self._normalize_sort_by(sort_by)
        normalized_sort_order = self._normalize_sort_order(sort_order)

        tasks = await self._list_my_publishing_tasks(
            space_id=space_id,
            user_id=user_id,
            from_ts=from_ts,
            to_ts=to_ts,
            category_id=category_id,
            approved_value=approved_value,
        )
        items = await self._build_my_published_task_items(
            tasks=tasks,
            space_id=space_id,
            visible_task_limit=getattr(space, "visible_task_limit", None),
        )

        if has_pending_participant_approval is not None:
            items = [
                item
                for item in items
                if (item["pendingParticipantApprovalCount"] > 0)
                == has_pending_participant_approval
            ]
        if has_pending_review is not None:
            items = [
                item
                for item in items
                if (item["pendingReviewCount"] > 0) == has_pending_review
            ]

        reverse = normalized_sort_order == "desc"
        items.sort(
            key=lambda item: (
                item[normalized_sort_by] if item[normalized_sort_by] is not None else 0,
                item["taskId"],
            ),
            reverse=reverse,
        )
        return items

    async def _ensure_space_exists(self, space_id: int) -> Space:
        space = await self._space_repo.get_by_id(space_id)
        if space is None:
            raise NotFoundError(
                "Resource space not found", data={"type": "space", "id": space_id}
            )
        return space

    async def _list_my_publishing_tasks(
        self,
        *,
        space_id: int,
        user_id: int,
        from_ts: int | None = None,
        to_ts: int | None = None,
        category_id: int | None = None,
        approved_value: int | None = None,
    ) -> list[Task]:
        stmt = select(Task).where(
            Task.deleted_at.is_(None),
            Task.space_id == space_id,
            Task.creator_id == user_id,
        )

        if category_id is not None:
            stmt = stmt.where(Task.category_id == category_id)
        if approved_value is not None:
            stmt = stmt.where(Task.approved == approved_value)

        from_dt = self._parse_timestamp_param(from_ts, "from")
        to_dt = self._parse_timestamp_param(to_ts, "to")
        if from_dt is not None:
            stmt = stmt.where(Task.created_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(Task.created_at <= to_dt)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _build_my_published_task_items(
        self,
        *,
        tasks: list[Task],
        space_id: int,
        visible_task_limit: int | None,
    ) -> list[dict]:
        if not tasks:
            return []

        task_ids = [int(task.id) for task in tasks]
        categories_by_id = await self._load_categories_by_id(space_id=space_id)
        memberships_by_task_id = await self._load_memberships_by_task_id(
            task_ids=task_ids
        )
        membership_ids = [
            int(membership.id)
            for memberships in memberships_by_task_id.values()
            for membership in memberships
        ]
        latest_submissions_by_membership_id = (
            await self._load_latest_submissions_by_membership_id(
                membership_ids=membership_ids
            )
        )
        reviews_by_submission_id = await self._load_reviews_by_submission_id(
            submission_ids=[
                int(submission.id)
                for submission in latest_submissions_by_membership_id.values()
            ]
        )
        visible_task_ids = self._compute_visible_approved_task_ids(
            tasks=tasks,
            visible_task_limit=visible_task_limit,
        )

        return [
            self._build_task_item(
                task=task,
                category=categories_by_id.get(int(task.category_id)),
                memberships=memberships_by_task_id.get(int(task.id), []),
                latest_submissions_by_membership_id=latest_submissions_by_membership_id,
                reviews_by_submission_id=reviews_by_submission_id,
                is_visible=(
                    int(task.id) in visible_task_ids
                    or getattr(task, "ended_at", None) is not None
                ),
            )
            for task in tasks
        ]

    async def _load_categories_by_id(
        self, *, space_id: int
    ) -> dict[int, SpaceCategory]:
        categories = await self._category_repo.list_categories_for_space(
            space_id, include_archived=True
        )
        return {int(category.id): category for category in categories}

    async def _load_memberships_by_task_id(
        self,
        *,
        task_ids: list[int],
    ) -> dict[int, list[TaskMembership]]:
        memberships_by_task_id: dict[int, list[TaskMembership]] = defaultdict(list)
        if not task_ids:
            return memberships_by_task_id

        stmt = select(TaskMembership).where(
            TaskMembership.deleted_at.is_(None),
            TaskMembership.task_id.in_(task_ids),
        )
        result = await self._session.execute(stmt)
        for membership in result.scalars().all():
            memberships_by_task_id[int(membership.task_id)].append(membership)
        return memberships_by_task_id

    async def _load_latest_submissions_by_membership_id(
        self,
        *,
        membership_ids: list[int],
    ) -> dict[int, TaskSubmission]:
        if not membership_ids:
            return {}

        latest_version_subquery = (
            select(
                TaskSubmission.membership_id.label("membership_id"),
                func.max(TaskSubmission.version).label("max_version"),
            )
            .where(
                TaskSubmission.deleted_at.is_(None),
                TaskSubmission.membership_id.in_(membership_ids),
            )
            .group_by(TaskSubmission.membership_id)
            .subquery()
        )

        stmt = (
            select(TaskSubmission)
            .join(
                latest_version_subquery,
                and_(
                    TaskSubmission.membership_id
                    == latest_version_subquery.c.membership_id,
                    TaskSubmission.version == latest_version_subquery.c.max_version,
                ),
            )
            .where(TaskSubmission.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return {
            int(submission.membership_id): submission
            for submission in result.scalars().all()
        }

    async def _load_reviews_by_submission_id(
        self,
        *,
        submission_ids: list[int],
    ) -> dict[int, TaskSubmissionReview]:
        if not submission_ids:
            return {}

        stmt = select(TaskSubmissionReview).where(
            TaskSubmissionReview.deleted_at.is_(None),
            TaskSubmissionReview.submission_id.in_(submission_ids),
        )
        result = await self._session.execute(stmt)
        return {int(review.submission_id): review for review in result.scalars().all()}

    def _build_task_item(
        self,
        *,
        task: Task,
        category: SpaceCategory | None,
        memberships: list[TaskMembership],
        latest_submissions_by_membership_id: dict[int, TaskSubmission],
        reviews_by_submission_id: dict[int, TaskSubmissionReview],
        is_visible: bool = True,
    ) -> dict:
        participant_count = len(memberships)
        approved_participant_count = 0
        pending_participant_approval_count = 0
        submitted_participant_count = 0
        pending_review_count = 0
        successful_participant_count = 0
        failed_participant_count = 0
        latest_submission_at: datetime | None = None

        for membership in memberships:
            if membership.approved == APPROVED_MAP["APPROVED"]:
                approved_participant_count += 1
            elif membership.approved == APPROVED_MAP["NONE"]:
                pending_participant_approval_count += 1

            latest_submission = latest_submissions_by_membership_id.get(
                int(membership.id)
            )
            review = (
                reviews_by_submission_id.get(int(latest_submission.id))
                if latest_submission is not None
                else None
            )
            completion_status = (membership.completion_status or "").upper()

            if latest_submission is not None:
                submitted_participant_count += 1
                if (
                    latest_submission_at is None
                    or latest_submission.created_at > latest_submission_at
                ):
                    latest_submission_at = latest_submission.created_at

            if completion_status in SUCCESS_STATUSES:
                successful_participant_count += 1
                continue
            if completion_status in FAILED_STATUSES:
                failed_participant_count += 1
                continue

            if latest_submission is None:
                continue

            if completion_status in PENDING_REVIEW_STATUSES or review is None:
                pending_review_count += 1
            elif review.accepted:
                successful_participant_count += 1
            else:
                failed_participant_count += 1

        submission_conversion_rate = (
            submitted_participant_count / approved_participant_count
            if approved_participant_count > 0
            else 0.0
        )
        success_rate = (
            successful_participant_count / submitted_participant_count
            if submitted_participant_count > 0
            else 0.0
        )

        return {
            "taskId": task.id,
            "taskName": task.name,
            "category": {
                "id": task.category_id,
                "name": category.name if category is not None else "",
            },
            "approved": APPROVED_REVERSE_MAP.get(task.approved, "NONE"),
            "visibilityStatus": self._derive_visibility_status(
                task=task, is_visible=is_visible
            ),
            "isVisible": is_visible,
            "createdAt": self._to_timestamp_ms(task.created_at) or 0,
            "publishedAt": self._to_timestamp_ms(getattr(task, "published_at", None)),
            "endedAt": self._to_timestamp_ms(getattr(task, "ended_at", None)),
            "deadline": self._to_timestamp_ms(task.deadline),
            "participantCount": participant_count,
            "approvedParticipantCount": approved_participant_count,
            "pendingParticipantApprovalCount": pending_participant_approval_count,
            "submittedParticipantCount": submitted_participant_count,
            "pendingReviewCount": pending_review_count,
            "successfulParticipantCount": successful_participant_count,
            "failedParticipantCount": failed_participant_count,
            "submissionConversionRate": submission_conversion_rate,
            "successRate": success_rate,
            "latestSubmissionAt": self._to_timestamp_ms(latest_submission_at),
        }

    @staticmethod
    def _to_timestamp_ms(value: datetime | None) -> int | None:
        if value is None:
            return None
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return int(aware.timestamp() * 1000)

    @staticmethod
    def _compute_visible_approved_task_ids(
        *,
        tasks: list[Task],
        visible_task_limit: int | None,
    ) -> set[int]:
        approved_not_ended = [
            task
            for task in tasks
            if task.approved == APPROVED_MAP["APPROVED"]
            and getattr(task, "ended_at", None) is None
        ]
        if visible_task_limit is None:
            return {int(task.id) for task in approved_not_ended}
        if visible_task_limit == 0:
            return set()

        by_creator: dict[int, list[Task]] = defaultdict(list)
        for task in approved_not_ended:
            by_creator[int(task.creator_id)].append(task)

        visible_ids: set[int] = set()
        for creator_tasks in by_creator.values():
            creator_tasks.sort(
                key=lambda task: (
                    getattr(task, "published_at", None) or task.created_at,
                    int(task.id),
                ),
            )
            visible_ids.update(
                int(task.id) for task in creator_tasks[:visible_task_limit]
            )
        return visible_ids

    @staticmethod
    def _derive_visibility_status(*, task: Task, is_visible: bool) -> str:
        if getattr(task, "ended_at", None) is not None:
            return "ENDED"
        if task.approved == APPROVED_MAP["NONE"]:
            return "PENDING_APPROVAL"
        if task.approved == APPROVED_MAP["DISAPPROVED"]:
            return "REJECTED"
        return "APPROVED_VISIBLE" if is_visible else "APPROVED_HIDDEN"

    @staticmethod
    def _parse_timestamp_param(value: int | None, field_name: str) -> datetime | None:
        if value is None:
            return None
        if value < 0:
            raise BadRequestError(f"{field_name} must be non-negative")

        epoch_value = value / 1000 if value >= 10**11 else value
        try:
            return datetime.fromtimestamp(epoch_value, UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise BadRequestError(f"{field_name} is not a valid timestamp") from exc

    @staticmethod
    def _parse_approved_filter(
        value: str | None, field_name: str = "approved"
    ) -> int | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        mapped = APPROVED_MAP.get(normalized)
        if mapped is None:
            raise BadRequestError(
                f"{field_name} must be APPROVED, DISAPPROVED, or NONE"
            )
        return mapped

    @staticmethod
    def _normalize_sort_order(sort_order: str) -> str:
        normalized = sort_order.strip().lower()
        if normalized not in {"asc", "desc"}:
            raise BadRequestError("sortOrder must be asc or desc")
        return normalized

    @staticmethod
    def _normalize_sort_by(sort_by: str) -> str:
        normalized = sort_by.strip()
        if normalized not in MY_PUBLISHING_SORT_FIELDS:
            raise BadRequestError(
                "sortBy must be one of createdAt, publishedAt, "
                "participantCount, pendingReviewCount, successRate"
            )
        return normalized
