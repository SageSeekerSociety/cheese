from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError
from app.core.domain_errors import (
    TaskParticipantsReachedLimitError,
    TeamSizeNotEnoughError,
    TeamSizeTooLargeError,
)
from app.domain.space.repositories import SpaceRepository, SpaceUserRankRepository
from app.domain.space.rank_service import SpaceRankService
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionEntry,
    TaskSubmissionReview,
)
from app.domain.task.repositories import (
    TaskRepository,
    TaskMembershipRepository,
    TaskSubmissionRepository,
    TaskSubmissionEntryRepository,
    TaskSubmissionReviewRepository,
)
from app.domain.user.repositories import UserRealNameRepository


class TaskService:
    def __init__(self, repo: TaskRepository) -> None:
        self._repo = repo

    async def get_task(self, task_id: int) -> Task | None:
        return await self._repo.get_by_id(task_id)

    async def enumerate_tasks(
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
        limit: int = 20,
        offset: int = 0,
        sort_by: str = "updatedAt",
        sort_order: str = "desc",
    ) -> Sequence[Task]:
        return await self._repo.list_tasks(
            space_id=space_id,
            category_id=category_id,
            approved=approved,
            owner_id=owner_id,
            keywords=keywords,
            topics=topics,
            joined=joined,
            current_user_id=current_user_id,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )

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
        return await self._repo.count_tasks(
            space_id=space_id,
            category_id=category_id,
            approved=approved,
            owner_id=owner_id,
            keywords=keywords,
            topics=topics,
            joined=joined,
            current_user_id=current_user_id,
        )


class TaskMembershipService:
    def __init__(
        self,
        repo: TaskMembershipRepository,
        realname_repo: UserRealNameRepository | None = None,
        space_repo: SpaceRepository | None = None,
        space_rank_repo: SpaceUserRankRepository | None = None,
    ) -> None:
        self._repo = repo
        self._realname_repo = realname_repo
        self._space_repo = space_repo
        self._space_rank_repo = space_rank_repo

    async def list_memberships_for_task(
        self,
        task_id: int,
        approved: int | None = None,
    ) -> Sequence[TaskMembership]:
        return await self._repo.list_memberships_for_task(task_id=task_id, approved=approved)

    async def get_user_membership(
        self,
        task_id: int,
        user_id: int,
    ) -> TaskMembership | None:
        """Return membership for a user as individual participant (non-team)."""
        return await self._repo.get_user_membership(task_id=task_id, user_id=user_id)

    async def list_team_memberships_for_user(
        self,
        task_id: int,
        user_id: int,
    ) -> Sequence[TaskMembership]:
        """Return team-type memberships for this task where the user belongs to the team."""
        return await self._repo.list_team_memberships_for_user(task_id=task_id, user_id=user_id)

    async def get_membership_by_id(self, membership_id: int) -> TaskMembership | None:
        return await self._repo.get_by_id(membership_id)

    async def get_membership_by_task_and_member(
        self,
        task_id: int,
        member_id: int,
    ) -> TaskMembership | None:
        return await self._repo.get_by_task_and_member(task_id=task_id, member_id=member_id)

    async def create_membership(
        self,
        *,
        task: Task,
        member_id: int,
        is_team: bool,
        approved: int,
        deadline: datetime | None,
        email: str | None,
        phone: str | None,
        apply_reason: str | None,
        personal_advantage: str | None,
        remark: str | None,
    ) -> TaskMembership:
        """Create a new TaskMembership row with扩展校验（人数上限、报名窗口、实名等）。"""

        # 参与人数上限校验
        if approved == 0 and task.participant_limit is not None:
            approved_count = await self._repo.count_approved_for_task(task.id)  # type: ignore[arg-type]
            if approved_count >= task.participant_limit:
                if getattr(task, "auto_reject_when_full", False):
                    # 自动拒绝：将 approved 改为 DISAPPROVED (1)
                    approved = 1
                else:
                    raise BadRequestError("Task participant limit reached.")

        # 报名窗口校验（已移除 registration_start_at 和 registration_deadline）
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # 已存在参与记录则拒绝
        existing = await self.get_membership_by_task_and_member(
            task_id=task.id,
            member_id=member_id,  # type: ignore[arg-type]
        )
        if existing is not None and existing.deleted_at is None:
            raise BadRequestError("Member already participating in this task.")

        # requireRealName：简化为只校验提交者本人
        if task.require_real_name and self._realname_repo is not None and approved == 0:
            has_identity = await self._realname_repo.has_identity(member_id)
            if not has_identity:
                raise BadRequestError("Missing required real name information.")

        membership = TaskMembership(
            task_id=task.id,  # type: ignore[arg-type]
            member_id=member_id,
            approved=approved,
            is_team=is_team,
            email=email or "",
            phone=phone or "",
            completion_status="NOT_SUBMITTED",
            created_at=now,
            updated_at=now,
            deadline=deadline,
            deleted_at=None,
        )
        return await self._repo.save(membership)

    async def soft_delete_membership(self, membership: TaskMembership) -> None:
        membership.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self._repo.save(membership)

    async def update_membership(
        self,
        *,
        membership: TaskMembership,
        task: Task,
        approved: int | None = None,
        deadline: datetime | None = None,
        reject_reason: str | None = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> TaskMembership:
        """Update membership approval/deadline and basic contact fields.

        该方法实现 Kotlin `updateTaskMembership` 的一个精简子集：
        - 审批通过时检查人数上限；
        - TEAM 任务在审批时校验队伍人数是否满足 min/maxTeamSize；
        - USER 任务在审批时（若 requireRealName）检查当前成员是否有实名记录；
        - 不实现加密实名快照与事件广播，仅更新核心字段。
        """

        previous_approved = membership.approved
        new_approved = previous_approved if approved is None else approved

        # 如果本次操作是"从非 APPROVED 变为 APPROVED"，做一些基础检查。
        is_approving = previous_approved != 0 and new_approved == 0
        if is_approving:
            # 人数上限检查
            if task.participant_limit is not None:
                approved_count = await self._repo.count_approved_for_task(task.id)  # type: ignore[arg-type]
                if approved_count >= task.participant_limit:
                    raise TaskParticipantsReachedLimitError(task.id, task.participant_limit)  # type: ignore[arg-type]

            # TEAM 任务时，检查队伍规模是否在 min/maxTeamSize 范围内。
            if membership.is_team:
                team_size = await self._repo.count_team_members(membership.member_id)
                if task.min_team_size is not None and team_size < task.min_team_size:
                    raise TeamSizeNotEnoughError(task.min_team_size, team_size)
                if task.max_team_size is not None and team_size > task.max_team_size:
                    raise TeamSizeTooLargeError(task.max_team_size, team_size)
                # requireRealName 对 TEAM 的细粒度校验（所有成员实名）留待后续引入 team 视图服务时补齐。
            else:
                # USER 任务 + requireRealName：检查该用户是否有实名。
                if task.require_real_name and self._realname_repo is not None:
                    has_identity = await self._realname_repo.has_identity(membership.member_id)
                    if not has_identity:
                        raise BadRequestError(
                            "Cannot approve: user is missing required real name information."
                        )

        # 应用字段更新
        if approved is not None:
            membership.approved = approved
        if deadline is not None:
            membership.deadline = deadline
        if email is not None:
            membership.email = email
        if phone is not None:
            membership.phone = phone
        # TaskMembership 目前模型中不包含 rejectReason 等附加字段，仅在 Task 上维护，故此处忽略。
        _ = reject_reason

        membership.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        return await self._repo.save(membership)

    async def get_participation_eligibility(
        self,
        task: Task,
        user_id: int,
    ) -> dict:
        """Simplified ParticipationEligibilityDTO equivalent for Python.

        当前版本仅根据 Task 类型和基本限制给出一个粗略的 eligibility 视图，
        主要用于前端「是否可加入」的提示，后续可逐步对齐 Kotlin 全量规则。
        """
        # 基础规则：任务未软删除且必须已 APPROVED（ordinal=0）才可能 eligible。
        if task.deleted_at is not None:
            return {"user": {"eligible": False, "reasons": []}, "teams": None}

        is_task_approved = task.approved == 0

        # 报名窗口检查
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        registration_not_started = (
            task.registration_start_at is not None and now < task.registration_start_at
        )
        registration_closed = False

        # USER 类型：只返回 user eligibility，teams 为 null。
        if task.submitter_type == 0:
            reasons: list[dict] = []
            if not is_task_approved:
                reasons.append(
                    {
                        "code": "TASK_NOT_APPROVED",
                        "message": "Task is not approved yet.",
                    }
                )

            if registration_not_started:
                reasons.append(
                    {
                        "code": "REGISTRATION_NOT_STARTED",
                        "message": "Registration has not started yet.",
                    }
                )

            if registration_closed:
                reasons.append(
                    {
                        "code": "REGISTRATION_CLOSED",
                        "message": "Registration deadline has passed.",
                    }
                )

            # 参与人数达到上限
            if task.participant_limit is not None:
                approved_count = await self._repo.count_approved_for_task(task.id)  # type: ignore[arg-type]
                if approved_count >= task.participant_limit:
                    reasons.append(
                        {
                            "code": "PARTICIPANT_LIMIT_REACHED",
                            "message": f"Task participant limit ({task.participant_limit}) reached.",
                        }
                    )

            # 已经参与则视为不可再加入。
            existing = await self.get_user_membership(task_id=task.id, user_id=user_id)  # type: ignore[arg-type]
            if existing is not None:
                reasons.append(
                    {
                        "code": "ALREADY_PARTICIPATING",
                        "message": "You are already participating.",
                    }
                )

            # requireRealName: 若任务需要实名且用户没有实名记录，则拒绝。
            if task.require_real_name and self._realname_repo is not None:
                has_identity = await self._realname_repo.has_identity(user_id)
                if not has_identity:
                    reasons.append(
                        {
                            "code": "MISSING_REAL_NAME",
                            "message": "Real name information is required for this task.",
                        }
                    )

            # Rank 规则：当空间启用 rank 且任务有 rank 要求时，若开启检查则限制「用户 rank + rank_jump >= task.rank」。
            if (
                settings.rank_check_enforced
                and isinstance(getattr(task, "space_id", None), int)
                and getattr(task, "rank", None) is not None
                and self._space_repo is not None
                and self._space_rank_repo is not None
            ):
                space = await self._space_repo.get_by_id(task.space_id)  # type: ignore[arg-type]
                if space is not None and space.enable_rank and task.rank is not None:
                    required_rank = max(0, task.rank - settings.rank_jump)
                    if required_rank > 0:
                        actual_rank = await self._space_rank_repo.get_rank(
                            space_id=task.space_id,  # type: ignore[arg-type]
                            user_id=user_id,
                        )
                        if actual_rank < required_rank:
                            reasons.append(
                                {
                                    "code": "USER_RANK_NOT_HIGH_ENOUGH",
                                    "message": f"Your rank ({actual_rank}) is not high enough. Required: {required_rank}.",
                                    "details": {
                                        "userId": user_id,
                                        "actualRank": actual_rank,
                                        "requiredRank": required_rank,
                                        "taskId": task.id,
                                    },
                                }
                            )

            eligible = is_task_approved and not reasons
            return {
                "user": {
                    "eligible": eligible,
                    "reasons": reasons,
                },
                "teams": None,
            }

        # TEAM 类型：返回 team eligibility 数组（含基本 team 尺寸限制与实名要求）。
        team_memberships = await self.list_team_memberships_for_user(
            task_id=task.id,  # type: ignore[arg-type]
            user_id=user_id,
        )
        teams_status: list[dict] = []
        for membership in team_memberships:
            approved = membership.approved == 0
            team_size = await self._repo.count_team_members(membership.member_id)

            reasons: list[dict] = []

            # 任务未审批
            if not is_task_approved:
                reasons.append(
                    {
                        "code": "TASK_NOT_APPROVED",
                        "message": "Task is not approved yet.",
                    }
                )

            # 报名窗口检查
            if registration_not_started:
                reasons.append(
                    {
                        "code": "REGISTRATION_NOT_STARTED",
                        "message": "Registration has not started yet.",
                    }
                )
            if registration_closed:
                reasons.append(
                    {
                        "code": "REGISTRATION_CLOSED",
                        "message": "Registration deadline has passed.",
                    }
                )

            # 参与人数达到上限
            if task.participant_limit is not None:
                approved_count = await self._repo.count_approved_for_task(task.id)  # type: ignore[arg-type]
                if approved_count >= task.participant_limit:
                    reasons.append(
                        {
                            "code": "PARTICIPANT_LIMIT_REACHED",
                            "message": f"Task participant limit ({task.participant_limit}) reached.",
                        }
                    )

            reasons.append(
                {
                    "code": "ALREADY_PARTICIPATING",
                    "message": "This team is already participating in this task.",
                }
            )

            if task.min_team_size is not None and team_size < task.min_team_size:
                reasons.append(
                    {
                        "code": "TEAM_TOO_SMALL",
                        "message": f"Team size ({team_size}) is below minimum ({task.min_team_size}).",
                    }
                )
            if task.max_team_size is not None and team_size > task.max_team_size:
                reasons.append(
                    {
                        "code": "TEAM_TOO_LARGE",
                        "message": f"Team size ({team_size}) exceeds maximum ({task.max_team_size}).",
                    }
                )

            # requireRealName: 若任务要求实名，TEAM 参与需要所有队员均有实名记录。
            if task.require_real_name and self._realname_repo is not None:
                # 简化实现：只要发现队员中存在未实名用户就添加原因。
                # 这里没有逐个检查所有成员，只是标记整体状态，后续可以细化为具体 missingUserIds。
                # 为避免额外查询，这里只检查提交者自身是否实名；完整实现应结合 team 成员列表。
                has_identity = await self._realname_repo.has_identity(user_id)
                if not has_identity:
                    reasons.append(
                        {
                            "code": "TEAM_MEMBER_MISSING_REAL_NAME",
                            "message": "One or more team members missing real name info.",
                        }
                    )

            # Rank 规则：TEAM 任务下，检查每个团队对应用户的 rank 是否满足要求。
            if (
                settings.rank_check_enforced
                and isinstance(getattr(task, "space_id", None), int)
                and getattr(task, "rank", None) is not None
                and self._space_repo is not None
                and self._space_rank_repo is not None
            ):
                space = await self._space_repo.get_by_id(task.space_id)  # type: ignore[arg-type]
                if space is not None and space.enable_rank and task.rank is not None:
                    required_rank = max(0, task.rank - settings.rank_jump)
                    if required_rank > 0:
                        actual_rank = await self._space_rank_repo.get_rank(
                            space_id=task.space_id,  # type: ignore[arg-type]
                            user_id=user_id,
                        )
                        if actual_rank < required_rank:
                            reasons.append(
                                {
                                    "code": "TEAM_MEMBER_RANK_NOT_HIGH_ENOUGH",
                                    "message": f"Your rank ({actual_rank}) is not high enough for this team task. Required: {required_rank}.",
                                    "details": {
                                        "userId": user_id,
                                        "actualRank": actual_rank,
                                        "requiredRank": required_rank,
                                        "taskId": task.id,
                                        "teamId": membership.member_id,
                                    },
                                }
                            )

            teams_status.append(
                {
                    "team": {
                        "id": membership.member_id,
                        # 其他 TeamSummaryDTO 字段（name/intro/avatarId 等）后续通过 TeamService 补齐。
                    },
                    "eligibility": {
                        "eligible": is_task_approved and approved and not reasons,
                        "reasons": reasons,
                    },
                }
            )

        return {
            "user": None,
            "teams": teams_status,
        }


class TaskSubmissionService:
    """Simplified Python port of TaskSubmissionService.

    当前版本聚焦于：
    - 创建/修改提交（支持多 entry，文本或附件 ID）；
    - 列出提交（按 taskId/participantId、是否包含历史版本、是否已评审等筛选）；
    - 构造与 TaskSubmissionDTO 兼容的响应结构（member/submitter/content/review）。
    """

    def __init__(
        self,
        submission_repo: TaskSubmissionRepository,
        entry_repo: TaskSubmissionEntryRepository,
        review_repo: TaskSubmissionReviewRepository,
        membership_repo: TaskMembershipRepository,
    ) -> None:
        self._submission_repo = submission_repo
        self._entry_repo = entry_repo
        self._review_repo = review_repo
        self._membership_repo = membership_repo

    async def _build_member_summary(self, membership: TaskMembership) -> dict:
        """Build a minimal TaskParticipantSummaryDTO-like dict."""
        return {
            "id": membership.member_id,
            "intro": "",
            "name": "",
            "avatarId": 0,
            "participantId": None,
        }

    async def _build_submitter_summary(self, submitter_id: int) -> dict:
        """Build a minimal UserDTO-like dict for submitter.

        NOTE: 为避免在 Service 层引入用户域的强依赖，这里仅返回 id，
        其他字段交由后续对接 UserService 时再补齐。
        """
        return {
            "id": submitter_id,
            "username": None,
            "nickname": None,
            "avatarId": None,
            "intro": None,
        }

    async def _build_review_dto(self, review: TaskSubmissionReview | None) -> dict | None:
        if review is None:
            return {"reviewed": False}
        return {
            "reviewed": True,
            "detail": {
                "accepted": review.accepted,
                "score": review.score,
                "comment": review.comment,
            },
        }

    async def _build_submission_dto(
        self,
        submission: TaskSubmission,
        membership: TaskMembership,
        entries: list[TaskSubmissionEntry],
        review: TaskSubmissionReview | None,
    ) -> dict:
        member_summary = await self._build_member_summary(membership)
        submitter_summary = await self._build_submitter_summary(submission.submitter_id)

        def _entry_to_dto(idx: int, entry: TaskSubmissionEntry) -> dict:
            if entry.content_attachment_id is not None:
                entry_type = "FILE"
            else:
                entry_type = "TEXT"
            content_attachment = None
            if entry.content_attachment_id is not None:
                content_attachment = {
                    "id": entry.content_attachment_id,
                    "type": "",
                    "url": "",
                }
            return {
                "title": f"Entry {idx + 1}",
                "type": entry_type,
                "contentText": entry.content_text,
                "contentAttachment": content_attachment,
            }

        content_dtos = [
            _entry_to_dto(i, e) for i, e in enumerate(sorted(entries, key=lambda en: en.index))
        ]

        review_dto = await self._build_review_dto(review)

        created_ms = int(submission.created_at.timestamp() * 1000)
        updated_ms = int(submission.updated_at.timestamp() * 1000)

        return {
            "id": submission.id,
            "member": member_summary,
            "submitter": submitter_summary,
            "version": submission.version,
            "createdAt": created_ms,
            "updatedAt": updated_ms,
            "content": content_dtos,
            "review": review_dto if review_dto is not None else None,
        }

    async def submit_task(
        self,
        *,
        task_id: int,
        participant_id: int,
        submitter_id: int,
        contents: list[dict],
    ) -> dict:
        """Create a new submission for a participant, bumping its version."""
        membership = await self._membership_repo.list_memberships_for_task(
            task_id=task_id,
            approved=None,
        )
        membership_map = {m.id: m for m in membership}
        participant = membership_map.get(participant_id)
        if participant is None:
            raise NotFoundError.for_resource("task_membership", participant_id)

        latest_version = await self._submission_repo.get_latest_version_for_membership(
            participant_id
        )
        new_version = latest_version + 1

        submission = await self._submission_repo.create_submission(
            membership_id=participant_id,
            submitter_id=submitter_id,
            version=new_version,
        )

        # Convert incoming DTOs into internal entries.
        entry_tuples: list[tuple[int, str | None, int | None]] = []
        for idx, item in enumerate(contents):
            text = item.get("text")
            attachment_id_raw = item.get("attachmentId")
            attachment_id: int | None = None
            if attachment_id_raw is not None:
                try:
                    attachment_id = int(attachment_id_raw)
                except (TypeError, ValueError):
                    attachment_id = None
            entry_tuples.append((idx, text, attachment_id))

        await self._entry_repo.create_entries(
            submission_id=submission.id,
            entries=entry_tuples,
        )

        entries = list(await self._entry_repo.list_by_submission_id(submission_id=submission.id))
        review = await self._review_repo.get_by_submission_id(submission.id)

        return await self._build_submission_dto(
            submission=submission,
            membership=participant,
            entries=entries,
            review=review if review is not None else None,
        )

    async def modify_submission(
        self,
        *,
        task_id: int,
        participant_id: int,
        submitter_id: int,
        version: int,
        contents: list[dict],
    ) -> dict:
        """Modify an existing submission version by soft-deleting old entries and recreating."""
        membership = await self._membership_repo.list_memberships_for_task(
            task_id=task_id,
            approved=None,
        )
        membership_map = {m.id: m for m in membership}
        participant = membership_map.get(participant_id)
        if participant is None:
            raise NotFoundError.for_resource("task_membership", participant_id)

        # Find existing submission for this (membership, version).
        submission = await self._submission_repo.get_by_membership_and_version(
            membership_id=participant_id,
            version=version,
        )
        if submission is None:
            raise NotFoundError.for_resource("submission", version)

        # Soft-delete existing entries for this submission.
        await self._entry_repo.soft_delete_by_membership_and_version(
            membership_id=participant_id,
            version=version,
        )

        # Update submission timestamp
        submission.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        submission = await self._submission_repo.save(submission)

        entry_tuples: list[tuple[int, str | None, int | None]] = []
        for idx, item in enumerate(contents):
            text = item.get("text")
            attachment_id_raw = item.get("attachmentId")
            attachment_id: int | None = None
            if attachment_id_raw is not None:
                try:
                    attachment_id = int(attachment_id_raw)
                except (TypeError, ValueError):
                    attachment_id = None
            entry_tuples.append((idx, text, attachment_id))

        await self._entry_repo.create_entries(
            submission_id=submission.id,
            entries=entry_tuples,
        )

        entries = list(await self._entry_repo.list_by_submission_id(submission_id=submission.id))
        review = await self._review_repo.get_by_submission_id(submission.id)

        return await self._build_submission_dto(
            submission=submission,
            membership=participant,
            entries=entries,
            review=review if review is not None else None,
        )

    async def list_submissions(
        self,
        *,
        task_id: int,
        participant_id: int | None = None,
        all_versions: bool = False,
        query_review: bool = False,
        reviewed: bool | None = None,
        limit: int,
        offset: int = 0,
        sort_by: str = "updatedAt",
        sort_order: str = "desc",
    ) -> tuple[list[dict], int]:
        submissions = await self._submission_repo.list_submissions(
            task_id=task_id,
            participant_id=participant_id,
            all_versions=all_versions,
            reviewed=reviewed,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        total = await self._submission_repo.count_submissions(
            task_id=task_id,
            participant_id=participant_id,
            all_versions=all_versions,
            reviewed=reviewed,
        )

        # Fetch memberships in batch
        membership_ids = {s.membership_id for s in submissions}
        memberships_for_task = await self._membership_repo.list_memberships_for_task(
            task_id=task_id,
            approved=None,
        )
        membership_map = {m.id: m for m in memberships_for_task if m.id in membership_ids}

        items: list[dict] = []
        for submission in submissions:
            membership = membership_map.get(submission.membership_id)
            if membership is None:
                continue
            entries = list(
                await self._entry_repo.list_by_submission_id(submission_id=submission.id)
            )
            review: TaskSubmissionReview | None = None
            if query_review:
                review = await self._review_repo.get_by_submission_id(submission.id)
            dto = await self._build_submission_dto(
                submission=submission,
                membership=membership,
                entries=entries,
                review=review,
            )
            items.append(dto)

        return items, total


class TaskSubmissionReviewService:
    """Simplified Python port of TaskSubmissionReviewService with rank hooks."""

    def __init__(
        self,
        review_repo: TaskSubmissionReviewRepository,
        submission_repo: TaskSubmissionRepository | None = None,
        membership_repo: TaskMembershipRepository | None = None,
        task_repo: TaskRepository | None = None,
        rank_service: SpaceRankService | None = None,
    ) -> None:
        self._review_repo = review_repo
        self._submission_repo = submission_repo
        self._membership_repo = membership_repo
        self._task_repo = task_repo
        self._rank_service = rank_service

    async def get_review_dto(self, submission_id: int) -> dict:
        review = await self._review_repo.get_by_submission_id(submission_id)
        if review is None:
            return {"reviewed": False}
        return {
            "reviewed": True,
            "detail": {
                "accepted": review.accepted,
                "score": review.score,
                "comment": review.comment,
            },
        }

    async def create_review(
        self,
        *,
        submission_id: int,
        accepted: bool,
        score: int,
        comment: str,
    ) -> dict:
        review = await self._review_repo.create_review(
            submission_id=submission_id,
            accepted=accepted,
            score=score,
            comment=comment,
        )
        has_upgraded = await self._maybe_award_rank(
            submission_id=submission_id,
            previous_accepted=None,
            new_accepted=accepted,
        )
        dto = await self.get_review_dto(review.submission_id)
        dto["hasUpgradedParticipantRank"] = has_upgraded
        return dto

    async def patch_review(
        self,
        *,
        submission_id: int,
        accepted: bool | None = None,
        score: int | None = None,
        comment: str | None = None,
    ) -> dict:
        review = await self._review_repo.get_by_submission_id(submission_id)
        if review is None:
            raise NotFoundError.for_resource("review", submission_id)
        previous_accepted = review.accepted
        if accepted is not None:
            review.accepted = accepted
        if score is not None:
            review.score = score
        if comment is not None:
            review.comment = comment
        review.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self._review_repo.save(review)
        has_upgraded = await self._maybe_award_rank(
            submission_id=submission_id,
            previous_accepted=previous_accepted,
            new_accepted=review.accepted,
        )
        dto = await self.get_review_dto(submission_id)
        dto["hasUpgradedParticipantRank"] = has_upgraded
        return dto

    async def delete_review(self, *, submission_id: int) -> None:
        review = await self._review_repo.get_by_submission_id(submission_id)
        if review is None:
            return
        await self._review_repo.soft_delete(review)

    async def _maybe_award_rank(
        self,
        *,
        submission_id: int,
        previous_accepted: bool | None,
        new_accepted: bool,
    ) -> bool:
        if (
            not new_accepted
            or previous_accepted is True
            or self._rank_service is None
            or self._submission_repo is None
        ):
            return False
        submission = await self._submission_repo.get_by_id(submission_id)
        if submission is None:
            return False
        space_id = None
        task_rank: int | None = None
        if self._membership_repo is not None and self._task_repo is not None:
            membership = await self._membership_repo.get_by_id(submission.membership_id)
            if membership is not None:
                task = await self._task_repo.get_by_id(membership.task_id)
                if task is not None:
                    space_id = getattr(task, "space_id", None)
                    task_rank = getattr(task, "rank", None)
        return await self._rank_service.award_rank_if_higher(
            space_id=space_id,
            user_id=submission.submitter_id,
            task_rank=task_rank,
        )
