from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.domain.delivery.addressing import Event, Hand, address
from app.domain.delivery.ledger import DeliveryEvent, deliver, event_id_for
from app.domain.notification.models import NotificationType
from app.domain.team.models import (
    ApplicationStatus,
    ApplicationType,
    TeamMemberRole,
    TeamMembershipApplication,
)
from app.domain.team.repositories import (
    TeamMembershipApplicationRepository,
    TeamRepository,
)
from app.domain.user.services import handles_by_ids


async def _notify(
    session: AsyncSession,
    application: TeamMembershipApplication,
    *,
    type_: NotificationType,
    payload: dict[str, Any],
    handed_to: Iterable[int] = (),
    outcome_for: Iterable[int] = (),
) -> None:
    """把这条申请上刚发生的事交给投递账本。

    这里的每一件事都长在一条 `team_membership_application` 上，而参与者和它的关系
    只有两种：**卡递给了他**（申请等管理员审、邀请等被邀请的人回应），或者**他等
    的东西有了结果**（申请被批/被拒、邀请被接受/被谢绝/被取消）。两种都是
    `delivery/addressing.py` 里已经有的关系，所以这里不另写一份「谁会收到」。

    下一步一定在参与者手上：这几件事发生的那一刻，平台这边已经做完了。

    收件人翻成 handle 再交出去（I11）—— 调用点手里是自己算出来的用户 id，而投递
    那一侧只认名册上的名字。
    """
    reviewers = await handles_by_ids(session, handed_to)
    waiting = await handles_by_ids(session, outcome_for)
    await deliver(
        session,
        DeliveryEvent(
            id=event_id_for(type_, application.id),
            type=type_,
            payload=payload,
            # 申请最后一次变化的时刻就是这件事发生的时刻，不是走到这一行的时刻。
            occurred_at=application.updated_at,
        ),
        address(
            Event(reviewers=reviewers, reporter=waiting[0] if waiting else None),
            Hand.participant,
        ),
    )


class TeamMembershipService:
    def __init__(
        self,
        session: AsyncSession,
        team_repo: TeamRepository,
        application_repo: TeamMembershipApplicationRepository,
    ) -> None:
        self._session = session
        self._team_repo = team_repo
        self._app_repo = application_repo
        self._default_page_size = 20

    async def _validate_user_can_apply_or_be_invited(
        self, user_id: int, team_id: int
    ) -> None:
        from sqlalchemy import select

        from app.domain.user.models import User

        stmt = select(User.id).where(User.id == user_id)
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise NotFoundError(f"User {user_id} does not exist")
        if await self._team_repo.is_team_member(team_id, user_id):
            raise ConflictError("User is already a member of this team.")
        if await self._app_repo.exists_pending_for_user_and_team(user_id, team_id):
            raise ConflictError("There is already a pending application for this team.")

    async def _update_status(
        self,
        app: TeamMembershipApplication,
        new_status: ApplicationStatus,
        processor_id: int,
    ) -> TeamMembershipApplication:
        now = datetime.now(UTC)
        app.status = new_status.value
        app.processed_by_id = processor_id
        app.processed_at = now
        app.updated_at = now
        await self._app_repo.save(app)
        return app

    async def create_team_join_request(
        self,
        *,
        user_id: int,
        team_id: int,
        message: str | None,
    ) -> TeamMembershipApplication:
        team = await self._team_repo.get_by_id(team_id)
        if team is None:
            raise NotFoundError(
                "Resource team not found", data={"type": "team", "id": team_id}
            )

        await self._validate_user_can_apply_or_be_invited(user_id, team_id)

        now = datetime.now(UTC)
        app = TeamMembershipApplication(
            user_id=user_id,
            team_id=team_id,
            initiator_id=user_id,
            type=ApplicationType.REQUEST.value,
            status=ApplicationStatus.PENDING.value,
            role="MEMBER",
            message=message or "",
            processed_by_id=None,
            processed_at=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        saved = await self._app_repo.save(app)

        payload: dict[str, Any] = {
            "requester": {"type": "user", "id": str(user_id)},
            "team": {"type": "team", "id": str(team_id)},
            "application": {
                "type": "team_membership_application",
                "id": str(saved.id),
            },
            "message": message or "",
        }
        await _notify(
            self._session,
            saved,
            type_=NotificationType.TEAM_JOIN_REQUEST,
            payload=payload,
            handed_to=await self._team_repo.list_admin_and_owner_ids(team_id),
        )

        return saved

    async def cancel_my_join_request(self, *, user_id: int, request_id: int) -> None:
        app = await self._app_repo.find_pending_by_id_and_initiator_and_type(
            application_id=request_id,
            initiator_id=user_id,
            type_=ApplicationType.REQUEST,
        )
        if app is None:
            raise NotFoundError(
                "Pending request initiated by user not found",
                data={"type": "team_membership_application", "id": request_id},
            )
        await self._update_status(app, ApplicationStatus.CANCELED, processor_id=user_id)

    async def create_team_invitation(
        self,
        *,
        initiator_user_id: int,
        team_id: int,
        user_id_to_invite: int,
        role: int | None,
        message: str | None,
    ) -> TeamMembershipApplication:
        if not await self._team_repo.is_team_at_least_admin(team_id, initiator_user_id):
            raise ForbiddenError(
                f"User {initiator_user_id} is not authorized to invite members to team {team_id}."  # noqa: E501
            )

        await self._validate_user_can_apply_or_be_invited(user_id_to_invite, team_id)

        now = datetime.now(UTC)
        app = TeamMembershipApplication(
            user_id=user_id_to_invite,
            team_id=team_id,
            initiator_id=initiator_user_id,
            type=ApplicationType.INVITATION.value,
            status=ApplicationStatus.PENDING.value,
            role=(
                "OWNER"
                if role == TeamMemberRole.OWNER
                else "ADMIN"
                if role == TeamMemberRole.ADMIN
                else "MEMBER"
            ),
            message=message or "",
            processed_by_id=None,
            processed_at=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        saved = await self._app_repo.save(app)

        payload: dict[str, Any] = {
            "inviter": {"type": "user", "id": str(initiator_user_id)},
            "invitedUser": {"type": "user", "id": str(user_id_to_invite)},
            "team": {"type": "team", "id": str(team_id)},
            "application": {
                "type": "team_membership_application",
                "id": str(saved.id),
            },
            "role": saved.role,
            "message": message or "",
        }
        await _notify(
            self._session,
            saved,
            type_=NotificationType.TEAM_INVITATION,
            payload=payload,
            handed_to=[user_id_to_invite],
        )
        return saved

    async def accept_team_invitation(self, *, user_id: int, invitation_id: int) -> None:
        from app.domain.team.services import check_team_locking_status

        app = await self._app_repo.find_pending_by_id_and_user_and_type(
            application_id=invitation_id,
            user_id=user_id,
            type_=ApplicationType.INVITATION,
        )
        if app is None:
            raise NotFoundError(
                "Pending invitation for user not found",
                data={"type": "team_membership_application", "id": invitation_id},
            )

        team_id = app.team_id
        await check_team_locking_status(self._session, team_id)
        if await self._team_repo.is_team_member(team_id, user_id):
            raise BadRequestError("Cannot accept invitation, user is already a member.")

        initiator_id = app.initiator_id

        role_mapping = {
            "OWNER": TeamMemberRole.OWNER,
            "ADMIN": TeamMemberRole.ADMIN,
            "MEMBER": TeamMemberRole.MEMBER,
        }
        member_role = role_mapping.get(app.role, TeamMemberRole.MEMBER)

        await self._update_status(app, ApplicationStatus.ACCEPTED, processor_id=user_id)
        await self._team_repo.add_member(team_id, user_id, member_role)

        payload: dict[str, Any] = {
            "accepter": {"type": "user", "id": str(user_id)},
            "team": {"type": "team", "id": str(team_id)},
            "application": {
                "type": "team_membership_application",
                "id": str(invitation_id),
            },
            "inviter": {"type": "user", "id": str(initiator_id)},
        }
        await _notify(
            self._session,
            app,
            type_=NotificationType.TEAM_INVITATION_ACCEPTED,
            payload=payload,
            outcome_for=[initiator_id],
        )

    async def decline_team_invitation(
        self, *, user_id: int, invitation_id: int
    ) -> None:
        app = await self._app_repo.find_pending_by_id_and_user_and_type(
            application_id=invitation_id,
            user_id=user_id,
            type_=ApplicationType.INVITATION,
        )
        if app is None:
            raise NotFoundError(
                "Pending invitation for user not found",
                data={"type": "team_membership_application", "id": invitation_id},
            )

        initiator_id = app.initiator_id
        team_id = app.team_id

        await self._update_status(app, ApplicationStatus.DECLINED, processor_id=user_id)

        payload: dict[str, Any] = {
            "decliner": {"type": "user", "id": str(user_id)},
            "team": {"type": "team", "id": str(team_id)},
            "application": {
                "type": "team_membership_application",
                "id": str(invitation_id),
            },
            "inviter": {"type": "user", "id": str(initiator_id)},
        }
        await _notify(
            self._session,
            app,
            type_=NotificationType.TEAM_INVITATION_DECLINED,
            payload=payload,
            outcome_for=[initiator_id],
        )

    async def approve_team_join_request(
        self,
        *,
        approver_user_id: int,
        team_id: int,
        request_id: int,
    ) -> None:
        from app.domain.team.services import check_team_locking_status

        if not await self._team_repo.is_team_at_least_admin(team_id, approver_user_id):
            raise ForbiddenError(
                f"User {approver_user_id} is not authorized to approve requests for team {team_id}."  # noqa: E501
            )

        app = await self._app_repo.find_pending_by_id_and_team_and_type(
            application_id=request_id,
            team_id=team_id,
            type_=ApplicationType.REQUEST,
        )
        if app is None:
            raise NotFoundError(
                "Pending request for team not found",
                data={"type": "team_membership_application", "id": request_id},
            )

        await check_team_locking_status(self._session, team_id)

        requester_id = app.user_id
        if await self._team_repo.is_team_member(team_id, requester_id):
            raise BadRequestError("Cannot approve request, user is already a member.")

        await self._update_status(
            app, ApplicationStatus.APPROVED, processor_id=approver_user_id
        )
        await self._team_repo.add_member(team_id, requester_id, TeamMemberRole.MEMBER)

        payload: dict[str, Any] = {
            "approver": {"type": "user", "id": str(approver_user_id)},
            "team": {"type": "team", "id": str(team_id)},
            "application": {
                "type": "team_membership_application",
                "id": str(request_id),
            },
            "requester": {"type": "user", "id": str(requester_id)},
        }
        await _notify(
            self._session,
            app,
            type_=NotificationType.TEAM_REQUEST_APPROVED,
            payload=payload,
            outcome_for=[requester_id],
        )

    async def reject_team_join_request(
        self,
        *,
        rejector_user_id: int,
        team_id: int,
        request_id: int,
    ) -> None:
        if not await self._team_repo.is_team_at_least_admin(team_id, rejector_user_id):
            raise ForbiddenError(
                f"User {rejector_user_id} is not authorized to reject requests for team {team_id}."  # noqa: E501
            )

        app = await self._app_repo.find_pending_by_id_and_team_and_type(
            application_id=request_id,
            team_id=team_id,
            type_=ApplicationType.REQUEST,
        )
        if app is None:
            raise NotFoundError(
                "Pending request for team not found",
                data={"type": "team_membership_application", "id": request_id},
            )

        requester_id = app.user_id
        await self._update_status(
            app, ApplicationStatus.REJECTED, processor_id=rejector_user_id
        )

        payload: dict[str, Any] = {
            "rejector": {"type": "user", "id": str(rejector_user_id)},
            "team": {"type": "team", "id": str(team_id)},
            "application": {
                "type": "team_membership_application",
                "id": str(request_id),
            },
            "requester": {"type": "user", "id": str(requester_id)},
        }
        await _notify(
            self._session,
            app,
            type_=NotificationType.TEAM_REQUEST_REJECTED,
            payload=payload,
            outcome_for=[requester_id],
        )

    async def cancel_team_invitation(
        self,
        *,
        canceler_user_id: int,
        team_id: int,
        invitation_id: int,
    ) -> None:
        if not await self._team_repo.is_team_at_least_admin(team_id, canceler_user_id):
            raise ForbiddenError(
                f"User {canceler_user_id} is not authorized to cancel invitations for team {team_id}."  # noqa: E501
            )

        app = await self._app_repo.find_pending_by_id_and_team_and_type(
            application_id=invitation_id,
            team_id=team_id,
            type_=ApplicationType.INVITATION,
        )
        if app is None:
            raise NotFoundError(
                "Pending invitation for team not found",
                data={"type": "team_membership_application", "id": invitation_id},
            )

        invited_user_id = app.user_id
        await self._update_status(
            app, ApplicationStatus.CANCELED, processor_id=canceler_user_id
        )

        payload: dict[str, Any] = {
            "canceler": {"type": "user", "id": str(canceler_user_id)},
            "team": {"type": "team", "id": str(team_id)},
            "application": {
                "type": "team_membership_application",
                "id": str(invitation_id),
            },
            "invitedUser": {"type": "user", "id": str(invited_user_id)},
        }
        await _notify(
            self._session,
            app,
            type_=NotificationType.TEAM_INVITATION_CANCELED,
            payload=payload,
            outcome_for=[invited_user_id],
        )

    async def list_my_invitations(
        self,
        *,
        user_id: int,
        status: ApplicationStatus | None,
        page_start: int | None,
        page_size: int | None,
    ) -> tuple[list[TeamMembershipApplication], dict]:
        limit = page_size or self._default_page_size
        offset = page_start or 0
        apps, total = await self._app_repo.list_for_user(
            user_id=user_id,
            type_=ApplicationType.INVITATION,
            status=status,
            limit=limit,
            offset=offset,
        )
        returned = len(apps)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return apps, page

    async def list_my_join_requests(
        self,
        *,
        user_id: int,
        status: ApplicationStatus | None,
        page_start: int | None,
        page_size: int | None,
    ) -> tuple[list[TeamMembershipApplication], dict]:
        limit = page_size or self._default_page_size
        offset = page_start or 0
        apps, total = await self._app_repo.list_for_user(
            user_id=user_id,
            type_=ApplicationType.REQUEST,
            status=status,
            limit=limit,
            offset=offset,
        )
        returned = len(apps)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return apps, page

    async def list_team_join_requests(
        self,
        *,
        requesting_user_id: int,
        team_id: int,
        status: ApplicationStatus | None,
        page_start: int | None,
        page_size: int | None,
    ) -> tuple[list[TeamMembershipApplication], dict]:
        if not await self._team_repo.is_team_at_least_admin(
            team_id, requesting_user_id
        ):
            raise ForbiddenError(
                f"User {requesting_user_id} is not authorized to view requests for team {team_id}."  # noqa: E501
            )
        limit = page_size or self._default_page_size
        offset = page_start or 0
        apps, total = await self._app_repo.list_for_team(
            team_id=team_id,
            type_=ApplicationType.REQUEST,
            status=status,
            limit=limit,
            offset=offset,
        )
        returned = len(apps)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return apps, page

    async def list_team_invitations(
        self,
        *,
        requesting_user_id: int,
        team_id: int,
        status: ApplicationStatus | None,
        page_start: int | None,
        page_size: int | None,
    ) -> tuple[list[TeamMembershipApplication], dict]:
        if not await self._team_repo.is_team_at_least_admin(
            team_id, requesting_user_id
        ):
            raise ForbiddenError(
                f"User {requesting_user_id} is not authorized to view invitations for team {team_id}."  # noqa: E501
            )
        limit = page_size or self._default_page_size
        offset = page_start or 0
        apps, total = await self._app_repo.list_for_team(
            team_id=team_id,
            type_=ApplicationType.INVITATION,
            status=status,
            limit=limit,
            offset=offset,
        )
        returned = len(apps)
        has_more = offset + returned < total
        next_start = offset + returned if has_more and returned > 0 else None
        page = {
            "pageStart": offset,
            "pageSize": returned,
            "hasMore": has_more,
            "nextStart": next_start,
            "total": total,
        }
        return apps, page
