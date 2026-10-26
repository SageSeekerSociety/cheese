from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings

if TYPE_CHECKING:
    from app.domain.team.repositories import TeamRepository
from app.auth.space_access import may_teach_task
from app.core.domain_errors import (
    TaskParticipantsReachedLimitError,
    TeamSizeNotEnoughError,
    TeamSizeTooLargeError,
)
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.core.sentences import say
from app.domain.attachment.models import Attachment
from app.domain.attachment.services import AttachmentService
from app.domain.project.services import ClaimInProgressError
from app.domain.space.rank_service import SpaceRankService
from app.domain.space.repositories import (
    SpaceCategoryRepository,
    SpaceDomainGroupRepository,
    SpaceRepository,
    SpaceUserRankRepository,
)
from app.domain.task import claims, package
from app.domain.task.claims import members_claiming_through_another_team
from app.domain.task.inputs import (
    APPROVE_APPROVED,
    APPROVE_DISAPPROVED,
    APPROVE_NONE,
)
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskSubmission,
    TaskSubmissionEntry,
    TaskSubmissionReview,
)
from app.domain.task.repositories import (
    TaskMembershipRepository,
    TaskRepository,
    TaskSubmissionEntryRepository,
    TaskSubmissionRepository,
    TaskSubmissionReviewRepository,
    TaskSubmissionSchemaRepository,
)
from app.domain.task.stages import list_stages_for_tasks, stage_keys, stage_ref
from app.domain.task.submission_state import (
    COMPLETION_STATUS_NOT_SUBMITTED,
    refresh_completion_status,
    refresh_completion_status_for_submission,
)

# Moved to ``task_service`` to keep this module under its size cap; re-exported
# so ``from app.domain.task.services import TaskService`` still resolves.
from app.domain.task.task_service import TaskService as TaskService
from app.domain.task.visibility_service import TaskVisibilityService
from app.domain.team.models import Team
from app.domain.user.repositories import UserRealNameRepository


@dataclass
class _FallbackTeam:
    """Lightweight stand-in for a Team when no TeamRepository is wired.

    Exposes only the attributes the join-status loop reads off a team.
    """

    id: int
    name: str
    intro: str
    avatar_id: int | None


class _RankSpace(Protocol):
    """The only part of a space the rank rule reads.

    Typed structurally so this module keeps its one-way dependency on the
    space domain's services instead of importing its models.
    """

    enable_rank: bool


class TaskMembershipService:
    def __init__(
        self,
        repo: TaskMembershipRepository,
        realname_repo: UserRealNameRepository | None = None,
        space_repo: SpaceRepository | None = None,
        space_rank_repo: SpaceUserRankRepository | None = None,
        team_repo: "TeamRepository | None" = None,
    ) -> None:
        self._repo = repo
        self._realname_repo = realname_repo
        self._space_repo = space_repo
        self._space_rank_repo = space_rank_repo
        self._team_repo = team_repo

    @classmethod
    def of(
        cls,
        session: AsyncSession,
        *,
        realname_repo: UserRealNameRepository | None = None,
        space_repo: SpaceRepository | None = None,
        space_rank_repo: SpaceUserRankRepository | None = None,
        team_repo: "TeamRepository | None" = None,
    ) -> "TaskMembershipService":
        """路由工厂用的构造：同域的成员仓库在这里建，跨域的由调用方传进来。"""
        return cls(
            repo=TaskMembershipRepository(session=session),
            realname_repo=realname_repo,
            space_repo=space_repo,
            space_rank_repo=space_rank_repo,
            team_repo=team_repo,
        )

    async def list_memberships_for_task(
        self,
        task_id: int,
        approved: int | None = None,
    ) -> Sequence[TaskMembership]:
        return await self._repo.list_memberships_for_task(
            task_id=task_id, approved=approved
        )

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
        return await self._repo.list_team_memberships_for_user(
            task_id=task_id, user_id=user_id
        )

    async def get_membership_by_id(self, membership_id: int) -> TaskMembership | None:
        return await self._repo.get_by_id(membership_id)

    async def get_membership_by_task_and_member(
        self,
        task_id: int,
        member_id: int,
        is_team: bool,
    ) -> TaskMembership | None:
        return await self._repo.get_by_task_and_member(
            task_id=task_id, member_id=member_id, is_team=is_team
        )

    async def create_membership(
        self,
        *,
        task: Task,
        member_id: int,
        is_team: bool,
        email: str | None,
        phone: str | None,
        apply_reason: str | None,
        personal_advantage: str | None,
        remark: str | None,
    ) -> TaskMembership:
        """领这道题。申请制的题（``START_APPLY``），新的一份申请等出题人通过
        （``approved`` 2），名额、实名都在通过时看（``update_membership``），这里
        只看团队那两条。直接开始的题（``START_DIRECT``），这一份当场就是通过的：
        通过时看的那几条（``_check_approvable``，名额照样先锁题目那一行）在这里看，
        截止时间是题目自己的（``claims.deadline_from``）。领题的人不定自己的截止
        时间：申请制的在通过时定，个人的截止只由出题人在参与者名单上改。

        名额满了（通过的份数到了上限），新的申请也不收：申请了也通过不了。已经在等
        的那些不受影响，出题人照常处理。

        导师不在这里记：领题不建项目，开始做这道题（建项目）那一刻同意的导师才记下
        （``app.api.task_claims.start_claim``）。

        领一道自己（或自己的团队）已经领着的题不是错：答的就是那一份（等着通过
        的、已经通过的都算），调用方接着打开它的项目。被拒的那一份不算领着，可以
        重新申请。

        先拿下这一份领取（``try_lock_claim``，到提交为止）再看、再写：同一个人或
        同一个团队同时点两次，后到的那次不等前一次（它可能正在代码仓库服务那边建
        仓库），当场说「正在建，过一会儿再试」，再试时拿到的就是前一次领下的那一份。
        数据库里还没有约束挡住重复的领取（#4824），已经有重复时
        ``get_by_task_and_member`` 按通过的、再按最新的挑一份。"""

        now = datetime.now(UTC)
        if not await self._repo.try_lock_claim(task.id, member_id, is_team):  # type: ignore[arg-type]
            raise ClaimInProgressError(
                say(
                    "claimInProgress"
                    if await self._would_start(task, member_id, is_team)
                    else "claimBeingChanged"
                )
            )

        existing = await self._repo.get_by_task_and_member(
            task_id=task.id,  # type: ignore[arg-type]
            member_id=member_id,
            is_team=is_team,
        )
        if existing is not None and existing.approved != APPROVE_DISAPPROVED:
            return existing

        # 一个人一道题只领一次：团队来领时，队里不能有人已经通过别的团队领了它。
        if is_team:
            already = await members_claiming_through_another_team(
                self._repo._session,
                task_id=task.id,  # type: ignore[arg-type]
                team_id=member_id,
            )
            if already:
                raise BadRequestError(
                    "A member of this team has already claimed this task "
                    "through another team."
                )

        # 开始方式在这份领取写下之前重读，并且锁住不让它变（改开始方式的那一边
        # 等这里提交）；题目整个截止了，不再收新的领取。
        await claims.read_mode_for_claim(self._repo._session, task)
        claims.refuse_start_after_deadline(task, None, now)

        # 一个人不是团队：只有自己的那个团队不能领团队题，一个人去领个人题。
        if is_team:
            team = await self._repo._session.get(Team, member_id)
            if team is not None and team.personal_owner_user_id is not None:
                raise BadRequestError("A team task is claimed by a team, not a person.")

        direct = claims.starts_now(task)
        if direct:
            await self._check_approvable(task, member_id=member_id, is_team=is_team)
        elif task.participant_limit:
            approved_count = await self._repo.count_approved_for_task(task.id)  # type: ignore[arg-type]
            if approved_count >= task.participant_limit:
                raise TaskParticipantsReachedLimitError(task.id, task.participant_limit)  # type: ignore[arg-type]

        membership = TaskMembership(
            task_id=task.id,  # type: ignore[arg-type]
            member_id=member_id,
            approved=APPROVE_APPROVED if direct else APPROVE_NONE,
            is_team=is_team,
            email=email or "",
            phone=phone or "",
            completion_status=COMPLETION_STATUS_NOT_SUBMITTED,
            created_at=now,
            updated_at=now,
            deadline=claims.deadline_from(task, now) if direct else None,
            deleted_at=None,
            pitch=(apply_reason or "").strip(),
        )
        return await self._repo.save(membership)

    async def _would_start(self, task: Task, member_id: int, is_team: bool) -> bool:
        """Whether the claim request turned away by the claim lock is one that
        makes a project, so the refusal says what is under way: a project being
        made (a claim already approved, or a new one on a task started
        directly), or otherwise an application being decided or written."""
        held = await self._repo.get_by_task_and_member(
            task_id=task.id,  # type: ignore[arg-type]
            member_id=member_id,
            is_team=is_team,
        )
        if held is not None and held.approved != APPROVE_DISAPPROVED:
            return held.approved == APPROVE_APPROVED
        return claims.starts_now(task)

    async def soft_delete_membership(self, membership: TaskMembership) -> None:
        membership.deleted_at = datetime.now(UTC)
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
        - 审批通过时看 ``_check_approvable`` 那几条（名额、队伍人数、实名）；
        - 审批通过而没给截止时间时，截止时间按题目定（``claims.deadline_from``）；
        - 不实现加密实名快照与事件广播，仅更新核心字段。
        """

        previous_approved = membership.approved
        new_approved = previous_approved if approved is None else approved

        # 被拒的那一份，同一个人（或团队）后来又申请了：它成了旧账，处理新的那一份。
        # 没有新的一份时，撤回拒绝照旧可以。先拿下这个成员在这道题上的领取锁
        # （领题那条路拿的是同一把，到提交为止），再看有没有新的一份：正在写的
        # 新申请要么已经提交、这里看得见，要么等这里提交后看见这份又活了的。
        if (
            previous_approved == APPROVE_DISAPPROVED
            and new_approved != APPROVE_DISAPPROVED
        ):
            if not await self._repo.try_lock_claim(
                task.id,  # type: ignore[arg-type]
                membership.member_id,
                membership.is_team,
            ):
                raise ClaimInProgressError(say("claimBeingChanged"))
            if await claims.has_newer_claim(self._repo._session, membership):
                raise BadRequestError(say("claimHasNewerApplication"))

        # 如果本次操作是"从非 APPROVED 变为 APPROVED"，做一些基础检查。
        is_approving = previous_approved != 0 and new_approved == 0
        if is_approving:
            await self._check_approvable(
                task, member_id=membership.member_id, is_team=membership.is_team
            )
            if deadline is None:
                deadline = claims.deadline_from(task, datetime.now(UTC))

        # 应用字段更新
        if approved is not None:
            membership.approved = approved
        deadline_moved = deadline is not None and deadline != membership.deadline
        if deadline is not None:
            membership.deadline = deadline
        if email is not None:
            membership.email = email
        if phone is not None:
            membership.phone = phone
        # TaskMembership 目前模型中不包含 rejectReason 等附加字段，仅在 Task 上维护，故此处忽略。
        _ = reject_reason

        membership.updated_at = datetime.now(UTC)
        saved = await self._repo.save(membership)
        # 截止时间挪了，这份领取的完成状态跟着重推：清扫按旧截止判了失败的，延到以后
        # 又是没交；提前到已经过去的，没交的就读作失败。
        if deadline_moved:
            await refresh_completion_status(self._repo._session, saved)
        return saved

    async def _check_approvable(
        self, task: Task, *, member_id: int, is_team: bool
    ) -> None:
        """What a claim has to meet the moment it is approved, whether the
        publisher approves it or it starts approved (a task started directly).

        - 名额只被通过了的申请占：通过时才看还有没有名额，participant_limit 的 0 是
          「不限」。数名额和写下这一份通过要是一步，所以先锁题目那一行
          （``hold_places``，到提交为止）。
        - TEAM 任务：队伍人数在 min/maxTeamSize 之内。
        - USER 任务 + requireRealName：这个人有实名记录。
        """
        if task.participant_limit:
            await self._repo.hold_places(task.id)  # type: ignore[arg-type]
            approved_count = await self._repo.count_approved_for_task(task.id)  # type: ignore[arg-type]
            if approved_count >= task.participant_limit:
                raise TaskParticipantsReachedLimitError(task.id, task.participant_limit)  # type: ignore[arg-type]

        if is_team:
            team_size = await self._repo.count_team_members(member_id)
            if task.min_team_size is not None and team_size < task.min_team_size:
                raise TeamSizeNotEnoughError(task.min_team_size, team_size)
            if task.max_team_size is not None and team_size > task.max_team_size:
                raise TeamSizeTooLargeError(task.max_team_size, team_size)
            # requireRealName 对 TEAM 的细粒度校验（所有成员实名）留待后续引入 team 视图服务时补齐。
        elif task.require_real_name and self._realname_repo is not None:
            if not await self._realname_repo.has_identity(member_id):
                raise BadRequestError(
                    "Cannot approve: user is missing required real name information."
                )

    async def _rank_reasons(
        self,
        task: Task,
        user_id: int,
        settings: Settings,
        space: _RankSpace | None,
        *,
        team_id: int | None = None,
    ) -> list[dict]:
        """Why ``user_id`` fails the task's rank rule; empty means it passes.

        The space is the task's, not the candidate team's, so the caller reads it
        once and passes it in. Individual and team participation share this one
        rule; ``team_id`` only picks the reason code, message and extra detail.
        """
        rank_repo = self._space_rank_repo
        if space is None or not space.enable_rank or task.rank is None:
            return []
        if rank_repo is None:
            return []
        required_rank = max(0, task.rank - settings.rank_jump)
        if required_rank <= 0:
            return []
        actual_rank = await rank_repo.get_rank(
            space_id=task.space_id,  # type: ignore[arg-type]
            user_id=user_id,
        )
        if actual_rank >= required_rank:
            return []

        details = {
            "userId": user_id,
            "actualRank": actual_rank,
            "requiredRank": required_rank,
            "taskId": task.id,
        }
        if team_id is None:
            return [
                {
                    "code": "USER_RANK_NOT_HIGH_ENOUGH",
                    "message": f"Your rank ({actual_rank}) is not high enough. Required: {required_rank}.",
                    "details": details,
                }
            ]
        details["teamId"] = team_id
        return [
            {
                "code": "TEAM_MEMBER_RANK_NOT_HIGH_ENOUGH",
                "message": f"Your rank ({actual_rank}) is not high enough for this team task. Required: {required_rank}.",
                "details": details,
            }
        ]

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
        now = datetime.now(UTC)
        registration_not_started = (
            task.registration_start_at is not None and now < task.registration_start_at
        )
        registration_closed = False

        # Rank 规则只看题目所属的空间，与候选团队无关：两条路径共用，进分支前读一次。
        space: _RankSpace | None = None
        if (
            settings.rank_check_enforced
            and isinstance(getattr(task, "space_id", None), int)
            and getattr(task, "rank", None) is not None
            and self._space_repo is not None
            and self._space_rank_repo is not None
        ):
            space = await self._space_repo.get_by_id(task.space_id)  # type: ignore[arg-type]

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

            # 名额满了（通过了的申请数到了上限；0 是不限）：题目页写「人数已满」。
            if task.participant_limit:
                approved_count = await self._repo.count_approved_for_task(task.id)  # type: ignore[arg-type]
                if approved_count >= task.participant_limit:
                    reasons.append(
                        {
                            "code": "PARTICIPANT_LIMIT_REACHED",
                            "message": f"Task participant limit ({task.participant_limit}) reached.",
                        }
                    )

            # 已经参与则视为不可再加入（DISAPPROVED 除外）
            existing = await self.get_user_membership(task_id=task.id, user_id=user_id)  # type: ignore[arg-type]
            if existing is not None and existing.approved != 1:
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
            reasons.extend(await self._rank_reasons(task, user_id, settings, space))

            eligible = is_task_approved and not reasons
            return {
                "user": {
                    "eligible": eligible,
                    "reasons": reasons,
                },
                "teams": None,
            }

        # TEAM 类型：返回 team eligibility 数组（含基本 team 尺寸限制与实名要求）。
        # NT semantics (TaskMembershipEligibilityService.getParticipationEligibility):
        # iterate every team the user is OWNER/ADMIN of, regardless of whether that
        # team already has a TaskMembership row. Previously we only iterated existing
        # memberships, so a brand-new user always saw `teams: []` and could never
        # join a TEAM task.
        existing_memberships = await self.list_team_memberships_for_user(
            task_id=task.id,  # type: ignore[arg-type]
            user_id=user_id,
        )
        memberships_by_team_id = {m.member_id: m for m in existing_memberships}

        if self._team_repo is not None:
            candidate_teams = (
                await self._team_repo.list_teams_user_can_use_to_join_task(
                    user_id=user_id,
                )
            )
        else:
            # Fallback when no TeamRepository is wired: only existing memberships.
            candidate_teams = [
                _FallbackTeam(id=m.member_id, name="", intro="", avatar_id=None)
                for m in existing_memberships
            ]

        teams_status: list[dict] = []
        for candidate in candidate_teams:
            team_id = candidate.id
            team_size = await self._repo.count_team_members(team_id)
            existing = memberships_by_team_id.get(team_id)

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

            # 名额满了（通过了的申请数到了上限；0 是不限）：题目页写「人数已满」。
            if task.participant_limit:
                approved_count = await self._repo.count_approved_for_task(task.id)  # type: ignore[arg-type]
                if approved_count >= task.participant_limit:
                    reasons.append(
                        {
                            "code": "PARTICIPANT_LIMIT_REACHED",
                            "message": f"Task participant limit ({task.participant_limit}) reached.",
                        }
                    )

            # 已有 membership 记录（且未软删除）视为已参与
            # NONE(待审批) 和 APPROVED(已批准) 不可重复加入，DISAPPROVED(已拒绝) 允许重新申请
            if existing is not None and existing.approved != 1:
                reasons.append(
                    {
                        "code": "ALREADY_PARTICIPATING",
                        "message": "This team is already participating in this task.",
                    }
                )

            # 一个人一道题只领一次：队里有人已经通过别的团队领了，这个团队就不能再领。
            if existing is None or existing.approved == 1:
                already = await members_claiming_through_another_team(
                    self._repo._session,
                    task_id=task.id,  # type: ignore[arg-type]
                    team_id=team_id,
                )
                if already:
                    reasons.append(
                        {
                            "code": "MEMBER_ALREADY_PARTICIPATING",
                            "message": "A member of this team has already claimed "
                            "this task through another team.",
                            "details": {"userIds": already},
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
            # NT checks allVerified from getTeamMembers(teamId, queryRealNameStatus=true).
            if task.require_real_name and self._realname_repo is not None:
                # Check all team members, not just the requesting user.
                all_verified = True
                if self._team_repo is not None:
                    members = await self._team_repo.list_members_of_team(team_id)
                    for member_rel in members:
                        if not await self._realname_repo.has_identity(
                            member_rel.user_id
                        ):
                            all_verified = False
                            break
                else:
                    # Fallback: check the requesting user only
                    all_verified = await self._realname_repo.has_identity(user_id)
                if not all_verified:
                    reasons.append(
                        {
                            "code": "TEAM_MEMBER_MISSING_REAL_NAME",
                            "message": "One or more team members missing real name info.",
                        }
                    )

            # Rank 规则：TEAM 任务下，检查每个团队对应用户的 rank 是否满足要求。
            reasons.extend(
                await self._rank_reasons(
                    task, user_id, settings, space, team_id=team_id
                )
            )

            teams_status.append(
                {
                    "team": {
                        "id": team_id,
                        "name": getattr(candidate, "name", None) or "",
                        "intro": getattr(candidate, "intro", None) or "",
                        "avatarId": getattr(candidate, "avatar_id", None),
                    },
                    "eligibility": {
                        "eligible": is_task_approved and not reasons,
                        "reasons": reasons,
                    },
                }
            )

        return {
            "user": None,
            "teams": teams_status,
        }


def _submitted_file_to_api(attachment: Attachment) -> dict:
    """A handed-in file as the submission view reads it: the ``Attachment``
    contract, ``meta`` in its ``FileMeta`` shape (``name / size / mime``).

    The row's own ``meta`` stores ``filename / contentType``, so it is mapped
    rather than passed through. ``url`` is the storage link itself: no gated
    download route lets the teacher read a file someone else uploaded, so the
    link is how the reviewer opens it — and only the people allowed to read the
    submission are sent this DTO.
    """
    return {
        "id": attachment.id,
        "type": attachment.type,
        "url": attachment.url,
        "meta": {
            "name": attachment.meta.get("filename") or f"attachment_{attachment.id}",
            "size": attachment.meta.get("size", 0),
            "mime": attachment.meta.get("contentType", "application/octet-stream"),
        },
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
        schema_repo: TaskSubmissionSchemaRepository,
        attachments: AttachmentService,
        session: AsyncSession | None = None,
    ) -> None:
        self._submission_repo = submission_repo
        self._schema_repo = schema_repo
        self._entry_repo = entry_repo
        self._review_repo = review_repo
        self._membership_repo = membership_repo
        self._attachments = attachments
        # 推进完成状态要在同一个事务里读提交表、写领取行 —— 仓库共用这一个 session，
        # 路由的工厂（``get_task_submission_service``）永远把它传进来。为 None 只有
        # 单元测试那种「四个仓库全是 AsyncMock」的构造：那里没有库可写，也就不推。
        self._session = session

    @classmethod
    def of(
        cls,
        session: AsyncSession,
        *,
        attachments: AttachmentService,
    ) -> "TaskSubmissionService":
        """路由工厂用的构造：同域的四个仓库在这里建，附件服务由调用方传进来。"""
        return cls(
            submission_repo=TaskSubmissionRepository(session=session),
            entry_repo=TaskSubmissionEntryRepository(session=session),
            review_repo=TaskSubmissionReviewRepository(session=session),
            membership_repo=TaskMembershipRepository(session=session),
            schema_repo=TaskSubmissionSchemaRepository(session=session),
            attachments=attachments,
            session=session,
        )

    async def _stage_keys(self, task_id: int) -> list[str]:
        """The task's stage ids in order; none without a session to read them."""
        if self._session is None:
            return []
        return await stage_keys(self._session, task_id)

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

    async def _build_review_dto(
        self, review: TaskSubmissionReview | None
    ) -> dict | None:
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
        keys: Sequence[str],
    ) -> dict:
        member_summary = await self._build_member_summary(membership)
        submitter_summary = await self._build_submitter_summary(submission.submitter_id)

        attachment_ids = [
            e.content_attachment_id for e in entries if e.content_attachment_id
        ]
        attachments = {
            a.id: a for a in await self._attachments.get_many(attachment_ids)
        }

        def _entry_to_dto(idx: int, entry: TaskSubmissionEntry) -> dict:
            if entry.content_attachment_id is not None:
                entry_type = "FILE"
            else:
                entry_type = "TEXT"
            content_attachment = None
            attachment = attachments.get(entry.content_attachment_id or 0)
            if attachment is not None:
                content_attachment = _submitted_file_to_api(attachment)
            return {
                # Titled by the name the entry was answered under, not by the
                # form as it reads now. An entry answered under a blank name,
                # or past the end of the form, is numbered instead.
                "title": entry.prompt or f"Entry {idx + 1}",
                "type": entry_type,
                "contentText": entry.content_text,
                "contentAttachment": content_attachment,
            }

        content_dtos = [
            _entry_to_dto(i, e)
            for i, e in enumerate(sorted(entries, key=lambda en: en.index))
        ]

        review_dto = await self._build_review_dto(review)

        created_ms = int(submission.created_at.timestamp() * 1000)
        updated_ms = int(submission.updated_at.timestamp() * 1000)

        return {
            "id": submission.id,
            "member": member_summary,
            "submitter": submitter_summary,
            "version": submission.version,
            # The stage it hands in, by the stage's id (None without stages).
            "stage": stage_ref(submission.stage, keys),
            # 交的是题目的第几版；有版本之前交的是 None。
            "packageRevision": submission.package_revision,
            "createdAt": created_ms,
            "updatedAt": updated_ms,
            "content": content_dtos,
            "review": review_dto if review_dto is not None else None,
        }

    async def _entries_from(
        self, contents: list[dict], *, task_id: int, stage: int, submitter_id: int
    ) -> list[tuple[int, str | None, int | None, str | None]]:
        """Turn the request's entries into ``(index, text, attachment_id, prompt)``.

        ``prompt`` is the name of the form item at the entry's position among
        the items of the entry's stage, read now, so the entry keeps it however
        the form changes afterwards.

        A file entry may only name a file the submitter uploaded. The submission
        view hands out the file's storage link and name, so accepting any id here
        would let a participant read someone else's upload by guessing its id.
        Checked before anything is written, so a refused request changes nothing.
        """
        prompts = dict(
            enumerate(
                item.description
                for item in await self._schema_repo.list_by_task_id(task_id)
                if item.stage == stage
            )
        )
        rows: list[tuple[int, str | None, int | None, str | None]] = []
        for idx, item in enumerate(contents):
            attachment_id_raw = item.get("attachmentId")
            attachment_id: int | None = None
            if attachment_id_raw is not None:
                try:
                    attachment_id = int(attachment_id_raw)
                except (TypeError, ValueError):
                    attachment_id = None
            rows.append(
                (idx, item.get("text"), attachment_id, prompts.get(idx) or None)
            )

        wanted = {a for _, _, a, _ in rows if a is not None}
        found = {a.id: a for a in await self._attachments.get_many(list(wanted))}
        for attachment_id in sorted(wanted):
            attachment = found.get(attachment_id)
            if attachment is None:
                raise NotFoundError.for_resource("attachment", attachment_id)
            if not self._attachments.is_uploader(attachment, submitter_id):
                raise ForbiddenError(
                    "A submission can only include files its submitter uploaded"
                )
        return rows

    async def submit_task(
        self,
        *,
        task_id: int,
        participant_id: int,
        submitter_id: int,
        contents: list[dict],
        stage: int = 0,
    ) -> dict:
        """Create a new submission for a participant in one stage, bumping its
        version (versions count across the membership's stages)."""
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

        entry_tuples = await self._entries_from(
            contents,
            task_id=participant.task_id,
            stage=stage,
            submitter_id=submitter_id,
        )

        submission = await self._submission_repo.create_submission(
            membership_id=participant_id,
            submitter_id=submitter_id,
            version=new_version,
            stage=stage,
            # 交的是题目的哪一版：题目之后再改，这一份按它交的那一版看。
            package_revision=(
                await package.current_number(self._session, participant.task_id)
                if self._session is not None
                else None
            ),
        )

        await self._entry_repo.create_entries(
            submission_id=submission.id,
            entries=entry_tuples,
        )

        entries = list(
            await self._entry_repo.list_by_submission_id(submission_id=submission.id)
        )
        review = await self._review_repo.get_by_submission_id(submission.id)

        # 交了这一版之后，这条领取的完成状态就该是「待评审」了 —— 推一次，别等
        # 看板自己去猜（以前这条路一个状态都不写，看板于是永远显示「未提交」）。
        if self._session is not None:
            await refresh_completion_status(self._session, participant)

        return await self._build_submission_dto(
            submission=submission,
            membership=participant,
            entries=entries,
            review=review if review is not None else None,
            keys=await self._stage_keys(participant.task_id),
        )

    async def stage_of_version(self, participant_id: int, version: int) -> int | None:
        """The stage a live version of this membership hands in; None when there
        is no such version."""
        submission = await self._submission_repo.get_by_membership_and_version(
            membership_id=participant_id, version=version
        )
        return submission.stage if submission is not None else None

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

        entry_tuples = await self._entries_from(
            contents,
            task_id=participant.task_id,
            stage=submission.stage,
            submitter_id=submitter_id,
        )

        # Soft-delete existing entries for this submission.
        await self._entry_repo.soft_delete_by_membership_and_version(
            membership_id=participant_id,
            version=version,
        )

        # Update submission timestamp
        submission.updated_at = datetime.now(UTC)
        submission = await self._submission_repo.save(submission)

        await self._entry_repo.create_entries(
            submission_id=submission.id,
            entries=entry_tuples,
        )

        entries = list(
            await self._entry_repo.list_by_submission_id(submission_id=submission.id)
        )
        review = await self._review_repo.get_by_submission_id(submission.id)

        # 改一版本身不动评审，但这条领取的状态可能是历史存量里的错值（这条轴以前
        # 没人推进），顺手按同一口径纠正一次。
        if self._session is not None:
            await refresh_completion_status(self._session, participant)

        return await self._build_submission_dto(
            submission=submission,
            membership=participant,
            entries=entries,
            review=review if review is not None else None,
            keys=await self._stage_keys(participant.task_id),
        )

    async def list_submissions(
        self,
        *,
        task_id: int,
        participant_id: int | None = None,
        stage: int | None = None,
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
            stage=stage,
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
            stage=stage,
            all_versions=all_versions,
            reviewed=reviewed,
        )

        # Fetch memberships in batch
        membership_ids = {s.membership_id for s in submissions}
        memberships_for_task = await self._membership_repo.list_memberships_for_task(
            task_id=task_id,
            approved=None,
        )
        membership_map = {
            m.id: m for m in memberships_for_task if m.id in membership_ids
        }

        submission_ids = [s.id for s in submissions]
        entries_by_submission = await self._entry_repo.list_by_submission_ids(
            submission_ids
        )
        reviews_by_submission = (
            await self._review_repo.get_by_submission_ids(submission_ids)
            if query_review
            else {}
        )

        keys = await self._stage_keys(task_id)
        items: list[dict] = []
        for submission in submissions:
            membership = membership_map.get(submission.membership_id)
            if membership is None:
                continue
            entries = entries_by_submission.get(submission.id, [])
            review: TaskSubmissionReview | None = reviews_by_submission.get(
                submission.id
            )
            dto = await self._build_submission_dto(
                submission=submission,
                membership=membership,
                entries=entries,
                review=review,
                keys=keys,
            )
            items.append(dto)

        return items, total

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
    ) -> tuple[list[dict], int]:
        """一整门课的提交，按板子取一次（课程的「作业与验收」用）。

        每一行在 ``list_submissions`` 那份 DTO 之上多带三样：``taskId``（哪道
        作业）、``taskTitle``（那道题叫什么，管理员看的是「谁的哪份作业」）、
        ``participantId``（报名记录 id，前端拿它去调提交与评审那几条既有接口）。
        """
        rows = await self._submission_repo.list_for_space(
            space_id=space_id,
            task_id=task_id,
            reviewed=reviewed,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        total = await self._submission_repo.count_for_space(
            space_id=space_id,
            task_id=task_id,
            reviewed=reviewed,
        )

        submission_ids = [submission.id for submission, _, _ in rows]
        entries_by_submission = await self._entry_repo.list_by_submission_ids(
            submission_ids
        )
        reviews_by_submission = await self._review_repo.get_by_submission_ids(
            submission_ids
        )

        stages_by_task = (
            await list_stages_for_tasks(self._session, list({t.id for _, _, t in rows}))
            if self._session is not None
            else {}
        )
        items: list[dict] = []
        for submission, membership, task in rows:
            entries = entries_by_submission.get(submission.id, [])
            review = reviews_by_submission.get(submission.id)
            dto = await self._build_submission_dto(
                submission=submission,
                membership=membership,
                entries=entries,
                review=review,
                keys=[stage.key for stage in stages_by_task.get(task.id, [])],
            )
            dto["taskId"] = task.id
            dto["taskTitle"] = task.name
            dto["participantId"] = membership.id
            items.append(dto)

        return items, total

    async def summary_for_space(
        self,
        *,
        space_id: int,
        task_id: int | None = None,
    ) -> dict:
        """课程那一屏的三个数字：交了多少、还等多少人、多少份等着看。

        **没交的人数 = 报名人数 − 交过东西的人数**；等着看的是每人每个阶段最新那
        一版里还没评审的份数。``None`` 表示那个数字这次拿不到 —— 前端对此的处理是
        整块不显示，而不是画一个 0。
        """
        submissions = await self._submission_repo.count_members_handed_in_for_space(
            space_id=space_id, task_id=task_id
        )
        pending = await self._submission_repo.count_for_space(
            space_id=space_id, task_id=task_id, reviewed=False
        )
        memberships = await self._membership_repo.list_memberships_for_space(space_id)
        if task_id is not None:
            memberships = [m for m in memberships if m.task_id == task_id]
        return {
            "participants": len(memberships),
            "submissions": submissions,
            "pendingReview": pending,
            "missing": len(memberships) - submissions,
        }


class TaskSubmissionReviewService:
    """Simplified Python port of TaskSubmissionReviewService with rank hooks."""

    def __init__(
        self,
        review_repo: TaskSubmissionReviewRepository,
        submission_repo: TaskSubmissionRepository | None = None,
        membership_repo: TaskMembershipRepository | None = None,
        task_repo: TaskRepository | None = None,
        rank_service: SpaceRankService | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self._review_repo = review_repo
        self._submission_repo = submission_repo
        self._membership_repo = membership_repo
        self._task_repo = task_repo
        self._rank_service = rank_service
        # 同 TaskSubmissionService：推进完成状态要在这个事务里读提交与评审、写领取
        # 行。路由工厂永远传 session；为 None 只在单元测试那种全 AsyncMock 的构造里
        # 出现，那里没有库可写。
        self._session = session

    @classmethod
    def of(
        cls,
        session: AsyncSession,
        *,
        rank_service: SpaceRankService | None = None,
    ) -> "TaskSubmissionReviewService":
        """路由工厂用的构造：同域的四个仓库在这里建，排名服务由调用方传进来。"""
        return cls(
            review_repo=TaskSubmissionReviewRepository(session=session),
            submission_repo=TaskSubmissionRepository(session=session),
            membership_repo=TaskMembershipRepository(session=session),
            task_repo=TaskRepository(session=session),
            rank_service=rank_service,
            session=session,
        )

    async def get_review_dto(
        self, submission_id: int, reviewer_id: int | None = None
    ) -> dict:
        """这一版的评审 DTO。给了 `reviewer_id` 就是那个人的那一条，否则是最早的
        一条 —— 一版可以有多位老师各判一份。"""
        review = await self._review_repo.get_by_submission_id(
            submission_id, reviewer_id
        )
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
        reviewer_id: int,
        accepted: bool,
        score: int,
        comment: str,
    ) -> dict:
        review = await self._review_repo.create_review(
            submission_id=submission_id,
            reviewer_id=reviewer_id,
            accepted=accepted,
            score=score,
            comment=comment,
        )
        has_upgraded = await self._maybe_award_rank(
            submission_id=submission_id,
            previous_accepted=None,
            new_accepted=accepted,
        )
        # 判通过就该是 SUCCESS，驳回就该是「可重交」—— 这一步是这条轴真正的推进者。
        await self._refresh_membership_status(submission_id)
        dto = await self.get_review_dto(review.submission_id, reviewer_id)
        dto["hasUpgradedParticipantRank"] = has_upgraded
        return dto

    async def patch_review(
        self,
        *,
        submission_id: int,
        reviewer_id: int | None = None,
        accepted: bool | None = None,
        score: int | None = None,
        comment: str | None = None,
    ) -> dict:
        """改一条评审。给了 `reviewer_id` 就改那个人的那份，没给就是最早的一份
        （单评审时两者是同一份）。

        ponytail: 一版多人时「最早那一份」是个封顶 —— 真要按人改，路径上要带上
        谁的那一份；今天调用方还没带，所以留这个默认。
        """
        review = await self._review_repo.get_by_submission_id(
            submission_id, reviewer_id
        )
        if review is None:
            raise NotFoundError.for_resource("review", submission_id)
        previous_accepted = review.accepted
        if accepted is not None:
            review.accepted = accepted
        if score is not None:
            review.score = score
        if comment is not None:
            review.comment = comment
        review.updated_at = datetime.now(UTC)
        await self._review_repo.save(review)
        has_upgraded = await self._maybe_award_rank(
            submission_id=submission_id,
            previous_accepted=previous_accepted,
            new_accepted=review.accepted,
        )
        # 改判也要跟上：通过改成驳回，这条领取就从 SUCCESS 退回「可重交」。
        await self._refresh_membership_status(submission_id)
        dto = await self.get_review_dto(submission_id, reviewer_id)
        dto["hasUpgradedParticipantRank"] = has_upgraded
        return dto

    async def delete_review(
        self, *, submission_id: int, reviewer_id: int | None = None
    ) -> None:
        review = await self._review_repo.get_by_submission_id(
            submission_id, reviewer_id
        )
        if review is None:
            return
        await self._review_repo.soft_delete(review)
        # 撤销评审是「把话收回去」：没有评审了，这一版就回到队列里，状态必须跟着退回
        # 去，不能留在 SUCCESS 上。
        await self._refresh_membership_status(submission_id)

    async def _refresh_membership_status(self, submission_id: int) -> None:
        """这次评审写入之后，重推那条领取的完成状态。

        三条评审路由（POST / PATCH / PUT / DELETE）都落在上面三个方法里，所以推进
        这条轴只有一个入口，与 ``app.domain.task.submission_state`` 同一口径。
        """
        if self._session is None:
            return
        await refresh_completion_status_for_submission(self._session, submission_id)

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


async def count_distinct_participants(
    session: AsyncSession,
    *,
    space_id: int,
    task_ids: list[int],
) -> int:
    """这些题上加起来**有多少个不同的人**领过。

    逐题的 `participants.total` 是 `TaskMembership` 的行数 —— 一个人领三道题就是 3，
    求和得到的是「领取次数」。首页那句「参与 N 人」要的是**跨题去重后的人**，而列表
    接口不给报名名单（`_enrich_task_models` 把 `participants.examples` 写死成空数组，
    且 `TaskParticipantSummary` 里没有 username），客户端拼不出来，只能在这一层算。

    `task_ids` 就是同一个响应里返回的那几道题，所以这个数和逐题的 `participants.total`
    **同一批题**：两个数字摆在同一行上，不能一个数的是这一页、另一个数的是全板。

    只在 `queryDistinctParticipants` 为真时调用：它跑的 `list_memberships_for_space`
    与 `_enrich_task_models` 里那次是同一条查询，不该让每个调 `/tasks` 的页面都付两遍。
    """
    if not task_ids:
        return 0
    wanted = set(task_ids)
    memberships = await TaskMembershipRepository(
        session=session
    ).list_memberships_for_space(space_id)
    return len(
        {
            membership.member_id
            for membership in memberships
            if membership.task_id in wanted
        }
    )


async def ensure_task_visible_for_ordinary_user(
    *,
    session: AsyncSession,
    task: Task,
    user_id: int,
) -> None:
    # 出题者或本版管理员不受 visibleTaskLimit 限制 —— 这道闸是给成员看的。
    if await may_teach_task(session=session, task=task, user_id=user_id):
        return
    space = await SpaceRepository(session=session).get_by_id(task.space_id)
    if space is None:
        raise NotFoundError(
            "Resource space not found", data={"type": "space", "id": task.space_id}
        )
    task_repo = TaskRepository(session=session)
    if not await task_repo.is_task_visible_for_space_limit(
        task=task,
        visible_task_limit=space.visible_task_limit,
    ):
        raise NotFoundError(
            "Resource task not found", data={"type": "task", "id": task.id}
        )


async def ensure_task_readable(
    *,
    session: AsyncSession,
    task: Task,
    user_id: int,
) -> None:
    """「这道题在这个读者眼里存不存在」—— 题目详情与它的附属读路由共用的那一个判断。

    三道闸，按顺序各答一句话：

    1. **还没过审**（``approved == 2``，且未结项）：对出题者与本版管理员是草稿，
       对其他人还不该存在 —— 403；
    2. **看不见**（``TaskVisibilityService.can_view_task``）：这个人不在这块板里，
       或题目自己设了可见范围而他不在里面 —— 404，与「这道题不存在」同一句话；
    3. **超出本板上限**（``visibleTaskLimit``）：对普通用户来说它就是看不见了 ——
       404。

    为什么要抽出来：材料清单（``/attachments``）是拿着 task id 取数的另一条读
    路由，它只走了第 2 道 —— 而第 2 道不管审核与上限，于是 403 / 404 的题照旧
    把材料清单交出去。
    「题看不见，清单也看不见」是同一件事，只该有一处判断。
    """
    if task.approved == 2 and task.ended_at is None:  # NONE = 未审批
        if not await may_teach_task(session=session, task=task, user_id=user_id):
            raise ForbiddenError(
                "Only space admins or task creator can view unapproved tasks"
            )
    if not await TaskVisibilityService(session=session).can_view_task(
        task=task, user_id=user_id
    ):
        raise NotFoundError(
            "Resource task not found", data={"type": "task", "id": task.id}
        )
    await ensure_task_visible_for_ordinary_user(
        session=session, task=task, user_id=user_id
    )


async def validate_and_get_category_id(
    *,
    space_repo: SpaceRepository,
    category_repo: SpaceCategoryRepository,
    space_id: int,
    category_id: int | None,
) -> int:
    """Validate category for a space, mirroring Kotlin validateAndGetCategory."""

    async def _get_space_default_category_id() -> int:
        space = await space_repo.get_by_id(space_id)
        if space is None:
            raise NotFoundError("Space not found")
        if space.default_category_id is None:
            raise BadRequestError("Space has no default category configured.")
        return space.default_category_id

    async def _load_and_validate_category(cid: int) -> int:
        category = await category_repo.get_by_id_and_space(cid, space_id)
        if category is None:
            raise NotFoundError("Category not found or does not belong to space.")
        if getattr(category, "archived_at", None) is not None:
            raise BadRequestError(
                f"Cannot assign task to an archived category (id={cid})."
            )
        if category.deleted_at is not None:
            raise BadRequestError(
                f"Cannot assign task to a deleted category (id={cid})."
            )
        return category.id

    # When category_id is explicitly provided, validate it.
    if category_id is not None:
        return await _load_and_validate_category(category_id)

    # Otherwise, fall back to space.default_category_id.
    default_cid = await _get_space_default_category_id()
    return await _load_and_validate_category(default_cid)


async def ensure_domain_groups_belong_to_space(
    *,
    session: AsyncSession,
    space_id: int,
    group_ids: Sequence[int],
) -> None:
    """``accessDomainGroupIds`` 点名的每一个域组都得是**这块板**的。

    这些组解析出来的域会并进可见性判据（``TaskVisibilityService`` 把
    ``TaskAccessDomain.domain`` 直接并进读权限的 or 列表），所以拿别的板的组 id 发题，
    等于把那位管理员圈定的名单原样搬到自己这道题上。同一个请求体里的 ``categoryId``
    早就是这么收的（``validate_and_get_category_id`` 问 ``get_by_id_and_space``，
    不属于这块板就 404）；这里补上同一条，话也照抄那一句。
    """
    known = {
        group.id
        for group in await SpaceDomainGroupRepository(session=session).list_groups(
            space_id
        )
    }
    if any(group_id not in known for group_id in group_ids):
        raise NotFoundError("Domain group not found or does not belong to space.")
