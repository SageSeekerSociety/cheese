from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
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
from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation
from app.domain.user.models import User
from app.domain.user.repositories import UserRepository

APPROVED_MAP = {"APPROVED": 0, "DISAPPROVED": 1, "NONE": 2}
APPROVED_REVERSE_MAP = {value: key for key, value in APPROVED_MAP.items()}

IDENTITY_TYPES = {"USER", "TEAM"}
COMPLETION_STATUSES = {
    "NOT_SUBMITTED",
    "PENDING_REVIEW",
    "REJECTED_RESUBMITTABLE",
    "FAILED",
    "SUCCESS",
}
PARTICIPATION_SORT_FIELDS = {
    "joinedAt",
    "deadline",
    "latestSubmissionAt",
    "completionStatus",
}


class _Context:
    """In-memory aggregate used by both overview and list endpoints."""

    __slots__ = (
        "admin_team_ids",
        "categories_by_id",
        "creators_by_id",
        "current_user_id",
        "memberships",
        "reviews_by_submission_id",
        "submissions_by_membership_id",
        "tasks_by_id",
        "teams_by_id",
    )

    def __init__(
        self,
        *,
        memberships: list[TaskMembership],
        tasks_by_id: dict[int, Task],
        categories_by_id: dict[int, SpaceCategory],
        creators_by_id: dict[int, User],
        teams_by_id: dict[int, Team],
        admin_team_ids: set[int],
        submissions_by_membership_id: dict[int, TaskSubmission],
        reviews_by_submission_id: dict[int, TaskSubmissionReview],
        current_user_id: int,
    ) -> None:
        self.memberships = memberships
        self.tasks_by_id = tasks_by_id
        self.categories_by_id = categories_by_id
        self.creators_by_id = creators_by_id
        self.teams_by_id = teams_by_id
        self.admin_team_ids = admin_team_ids
        self.submissions_by_membership_id = submissions_by_membership_id
        self.reviews_by_submission_id = reviews_by_submission_id
        self.current_user_id = current_user_id


class SpaceMemberParticipatingService:
    """Implements the `GET /spaces/{spaceId}/me/participating` and
    `GET /spaces/{spaceId}/me/participations` endpoints aligned with NT-API."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._space_repo = SpaceRepository(session=session)
        self._category_repo = SpaceCategoryRepository(session=session)
        self._user_repo = UserRepository(session=session)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_overview(self, *, space_id: int, user_id: int) -> dict:
        await self._ensure_space_exists(space_id)
        context = await self._load_context(space_id=space_id, user_id=user_id)
        rows = [self._build_row(membership, context) for membership in context.memberships]

        return {
            "spaceId": space_id,
            "participationCount": len(rows),
            "approvedParticipationCount": sum(1 for r in rows if r["approved"] == "APPROVED"),
            "pendingApprovalCount": sum(1 for r in rows if r["approved"] == "NONE"),
            "awaitingSubmissionCount": sum(
                1
                for r in rows
                if r["approved"] == "APPROVED" and r["completionStatus"] == "NOT_SUBMITTED"
            ),
            "pendingReviewCount": sum(1 for r in rows if r["completionStatus"] == "PENDING_REVIEW"),
            "resubmittableCount": sum(
                1 for r in rows if r["completionStatus"] == "REJECTED_RESUBMITTABLE"
            ),
            "successfulCount": sum(1 for r in rows if r["completionStatus"] == "SUCCESS"),
            "failedCount": sum(1 for r in rows if r["completionStatus"] == "FAILED"),
        }

    async def get_participations(
        self,
        *,
        space_id: int,
        user_id: int,
        approved: str | None = None,
        completion_status: str | None = None,
        identity_type: str | None = None,
        sort_by: str = "joinedAt",
        sort_order: str = "desc",
    ) -> list[dict]:
        await self._ensure_space_exists(space_id)

        approved_filter = self._parse_approved_filter(approved)
        completion_filter = self._parse_completion_status_filter(completion_status)
        identity_filter = self._parse_identity_type_filter(identity_type)
        normalized_sort_by = self._normalize_sort_by(sort_by)
        normalized_sort_order = self._normalize_sort_order(sort_order)

        context = await self._load_context(space_id=space_id, user_id=user_id)
        rows = [self._build_row(membership, context) for membership in context.memberships]

        filtered = [
            row
            for row in rows
            if (approved_filter is None or row["approved"] == approved_filter)
            and (completion_filter is None or row["completionStatus"] == completion_filter)
            and (identity_filter is None or row["identityType"] == identity_filter)
        ]

        reverse = normalized_sort_order == "desc"

        def sort_key(row: dict) -> tuple:
            value = row.get(normalized_sort_by)
            # Keep None values stable at the tail regardless of order.
            is_missing = value is None
            fallback: object
            if normalized_sort_by == "completionStatus":
                fallback = ""
            else:
                fallback = 0
            return (is_missing, value if value is not None else fallback, row["participationId"])

        filtered.sort(key=sort_key, reverse=reverse)
        return filtered

    # ------------------------------------------------------------------
    # Context loading
    # ------------------------------------------------------------------

    async def _ensure_space_exists(self, space_id: int) -> Space:
        space = await self._space_repo.get_by_id(space_id)
        if space is None:
            raise NotFoundError("Resource space not found", data={"type": "space", "id": space_id})
        return space

    async def _load_context(self, *, space_id: int, user_id: int) -> _Context:
        team_ids, admin_team_ids = await self._load_user_team_relations(user_id)
        teams_by_id = await self._load_teams_by_id(team_ids)

        memberships = await self._load_memberships_for_user_in_space(
            space_id=space_id,
            user_id=user_id,
            team_ids=list(teams_by_id.keys()),
        )

        task_ids = sorted({int(m.task_id) for m in memberships})
        tasks_by_id = await self._load_tasks_by_id(task_ids)
        categories_by_id = await self._load_categories_for_space(space_id)
        creator_ids = [int(task.creator_id) for task in tasks_by_id.values()]
        creators_by_id = await self._user_repo.get_by_ids(creator_ids) if creator_ids else {}

        membership_ids = [int(m.id) for m in memberships]
        submissions_by_membership_id = await self._load_latest_submissions_by_membership_id(
            membership_ids
        )
        reviews_by_submission_id = await self._load_reviews_by_submission_id(
            [int(s.id) for s in submissions_by_membership_id.values()]
        )

        return _Context(
            memberships=memberships,
            tasks_by_id=tasks_by_id,
            categories_by_id=categories_by_id,
            creators_by_id=creators_by_id,
            teams_by_id=teams_by_id,
            admin_team_ids=admin_team_ids,
            submissions_by_membership_id=submissions_by_membership_id,
            reviews_by_submission_id=reviews_by_submission_id,
            current_user_id=user_id,
        )

    async def _load_user_team_relations(self, user_id: int) -> tuple[list[int], set[int]]:
        stmt = select(TeamUserRelation).where(
            TeamUserRelation.user_id == user_id,
            TeamUserRelation.deleted_at.is_(None),
        )
        relations = list((await self._session.execute(stmt)).scalars().all())
        team_ids = [int(rel.team_id) for rel in relations]
        admin_team_ids = {
            int(rel.team_id)
            for rel in relations
            if rel.role in (TeamMemberRole.OWNER, TeamMemberRole.ADMIN)
        }
        return team_ids, admin_team_ids

    async def _load_teams_by_id(self, team_ids: list[int]) -> dict[int, Team]:
        if not team_ids:
            return {}
        stmt = select(Team).where(
            Team.id.in_(team_ids),
            Team.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return {int(team.id): team for team in result.scalars().all()}

    async def _load_memberships_for_user_in_space(
        self,
        *,
        space_id: int,
        user_id: int,
        team_ids: list[int],
    ) -> list[TaskMembership]:
        member_conditions = [
            and_(
                TaskMembership.is_team.is_(False),
                TaskMembership.member_id == user_id,
            )
        ]
        if team_ids:
            member_conditions.append(
                and_(
                    TaskMembership.is_team.is_(True),
                    TaskMembership.member_id.in_(team_ids),
                )
            )

        stmt = (
            select(TaskMembership)
            .join(Task, Task.id == TaskMembership.task_id)
            .where(
                TaskMembership.deleted_at.is_(None),
                Task.deleted_at.is_(None),
                Task.space_id == space_id,
                or_(*member_conditions),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _load_tasks_by_id(self, task_ids: list[int]) -> dict[int, Task]:
        if not task_ids:
            return {}
        stmt = select(Task).where(
            Task.id.in_(task_ids),
            Task.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return {int(task.id): task for task in result.scalars().all()}

    async def _load_categories_for_space(self, space_id: int) -> dict[int, SpaceCategory]:
        categories = await self._category_repo.list_categories_for_space(
            space_id, include_archived=True
        )
        return {int(category.id): category for category in categories}

    async def _load_latest_submissions_by_membership_id(
        self,
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
                    TaskSubmission.membership_id == latest_version_subquery.c.membership_id,
                    TaskSubmission.version == latest_version_subquery.c.max_version,
                ),
            )
            .where(TaskSubmission.deleted_at.is_(None))
        )
        result = await self._session.execute(stmt)
        return {int(submission.membership_id): submission for submission in result.scalars().all()}

    async def _load_reviews_by_submission_id(
        self,
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

    # ------------------------------------------------------------------
    # Row construction
    # ------------------------------------------------------------------

    def _build_row(self, membership: TaskMembership, context: _Context) -> dict:
        task = context.tasks_by_id.get(int(membership.task_id))
        category = context.categories_by_id.get(int(task.category_id)) if task is not None else None
        creator = context.creators_by_id.get(int(task.creator_id)) if task is not None else None
        latest_submission = context.submissions_by_membership_id.get(int(membership.id))
        latest_review = (
            context.reviews_by_submission_id.get(int(latest_submission.id))
            if latest_submission is not None
            else None
        )

        is_team = bool(membership.is_team)
        team = context.teams_by_id.get(int(membership.member_id)) if is_team else None

        approved_value = APPROVED_REVERSE_MAP.get(int(membership.approved), "NONE")
        completion_status = (membership.completion_status or "NOT_SUBMITTED").upper()

        return {
            "taskId": int(task.id) if task is not None else int(membership.task_id),
            "taskName": task.name if task is not None else "",
            "publisher": {
                "id": int(creator.id) if creator is not None else 0,
                "name": (creator.username if creator is not None else "") or "",
            },
            "category": {
                "id": int(category.id) if category is not None else 0,
                "name": category.name if category is not None else "",
            },
            "participationId": int(membership.id),
            "identityType": "TEAM" if is_team else "USER",
            "teamName": team.name if team is not None else None,
            "approved": approved_value,
            "completionStatus": completion_status,
            "canSubmit": self._can_submit(
                membership=membership,
                is_team=is_team,
                approved_int=int(membership.approved),
                context=context,
            ),
            "joinedAt": self._to_timestamp_ms(membership.created_at) or 0,
            "deadline": self._to_timestamp_ms(membership.deadline),
            "latestSubmissionAt": self._to_timestamp_ms(
                latest_submission.created_at if latest_submission is not None else None
            ),
            "latestReviewAccepted": (
                bool(latest_review.accepted) if latest_review is not None else None
            ),
            "latestReviewScore": (
                float(latest_review.score) if latest_review is not None else None
            ),
        }

    def _can_submit(
        self,
        *,
        membership: TaskMembership,
        is_team: bool,
        approved_int: int,
        context: _Context,
    ) -> bool:
        if approved_int != APPROVED_MAP["APPROVED"]:
            return False
        if not is_team:
            return int(membership.member_id) == context.current_user_id
        team_id = int(membership.member_id)
        if team_id not in context.teams_by_id:
            return False
        return team_id in context.admin_team_ids

    # ------------------------------------------------------------------
    # Helpers: parsing & timestamps
    # ------------------------------------------------------------------

    @staticmethod
    def _to_timestamp_ms(value: datetime | None) -> int | None:
        if value is None:
            return None
        return int(value.replace(tzinfo=UTC).timestamp() * 1000)

    @staticmethod
    def _parse_approved_filter(value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().upper()
        if normalized not in APPROVED_MAP:
            raise BadRequestError("approved must be APPROVED, DISAPPROVED, or NONE")
        return normalized

    @staticmethod
    def _parse_completion_status_filter(value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().upper()
        if normalized not in COMPLETION_STATUSES:
            raise BadRequestError(
                "completionStatus must be NOT_SUBMITTED, PENDING_REVIEW, "
                "REJECTED_RESUBMITTABLE, FAILED, or SUCCESS"
            )
        return normalized

    @staticmethod
    def _parse_identity_type_filter(value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().upper()
        if normalized not in IDENTITY_TYPES:
            raise BadRequestError("identityType must be USER or TEAM")
        return normalized

    @staticmethod
    def _normalize_sort_by(sort_by: str) -> str:
        normalized = sort_by.strip()
        if normalized not in PARTICIPATION_SORT_FIELDS:
            raise BadRequestError(
                "sortBy must be one of joinedAt, deadline, latestSubmissionAt, completionStatus"
            )
        return normalized

    @staticmethod
    def _normalize_sort_order(sort_order: str) -> str:
        normalized = sort_order.strip().lower()
        if normalized not in {"asc", "desc"}:
            raise BadRequestError("sortOrder must be asc or desc")
        return normalized
