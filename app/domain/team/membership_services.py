from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.notification.models import NotificationType
from app.domain.notification.publisher import publish_notification_event
from app.domain.team.models import (
    ApplicationStatus,
    ApplicationType,
    TeamMembershipApplication,
    TeamMemberRole,
)
from app.domain.team.repositories import TeamMembershipApplicationRepository, TeamRepository


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

    async def _validate_user_can_apply_or_be_invited(self, user_id: int, team_id: int) -> None:
        if await self._team_repo.is_team_member(team_id, user_id):
            raise BadRequestError("User is already a member of this team.")
        if await self._app_repo.exists_pending_for_user_and_team(user_id, team_id):
            raise BadRequestError("There is already a pending application for this team.")

    async def _update_status(
        self,
        app: TeamMembershipApplication,
        new_status: ApplicationStatus,
        processor_id: int,
    ) -> TeamMembershipApplication:
        now = datetime.now(timezone.utc)
        app.status = new_status.value
        app.processed_by = processor_id
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
            raise NotFoundError("Resource team not found", data={"type": "team", "id": team_id})

        await self._validate_user_can_apply_or_be_invited(user_id, team_id)

        now = datetime.now(timezone.utc)
        app = TeamMembershipApplication(
            user_id=user_id,
            team_id=team_id,
            initiator_id=user_id,
            type=ApplicationType.REQUEST.value,
            status=ApplicationStatus.PENDING.value,
            role="MEMBER",
            message=message or "",
            processed_by=None,
            processed_at=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        saved = await self._app_repo.save(app)

        admin_ids = await self._team_repo.list_admin_and_owner_ids(team_id)
        if admin_ids:
            payload: dict[str, Any] = {
                "requester": {"type": "user", "id": str(user_id)},
                "team": {"type": "team", "id": str(team_id)},
                "application": {
                    "type": "team_membership_application",
                    "id": str(saved.id),
                },
                "message": message or "",
            }
            await publish_notification_event(
                self._session,
                recipient_ids=admin_ids,
                type_=NotificationType.TEAM_JOIN_REQUEST,
                payload=payload,
                actor_id=user_id,
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
                f"User {initiator_user_id} is not authorized to invite members to team {team_id}."
            )

        await self._validate_user_can_apply_or_be_invited(user_id_to_invite, team_id)

        now = datetime.now(timezone.utc)
        app = TeamMembershipApplication(
            user_id=user_id_to_invite,
            team_id=team_id,
            initiator_id=initiator_user_id,
            type=ApplicationType.INVITATION.value,
            status=ApplicationStatus.PENDING.value,
            role=("OWNER" if role == TeamMemberRole.OWNER else "ADMIN" if role == TeamMemberRole.ADMIN else "MEMBER"),
            message=message or "",
            processed_by=None,
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
        await publish_notification_event(
            self._session,
            recipient_ids={user_id_to_invite},
            type_=NotificationType.TEAM_INVITATION,
            payload=payload,
            actor_id=initiator_user_id,
        )
        return saved

    async def accept_team_invitation(self, *, user_id: int, invitation_id: int) -> None:
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
        if await self._team_repo.is_team_member(team_id, user_id):
            raise BadRequestError("Cannot accept invitation, user is already a member.")

        initiator_id = app.initiator_id

        await self._update_status(app, ApplicationStatus.ACCEPTED, processor_id=user_id)
        await self._team_repo.add_member(team_id, user_id, TeamMemberRole.MEMBER)

        payload: dict[str, Any] = {
            "accepter": {"type": "user", "id": str(user_id)},
            "team": {"type": "team", "id": str(team_id)},
            "application": {
                "type": "team_membership_application",
                "id": str(invitation_id),
            },
            "inviter": {"type": "user", "id": str(initiator_id)},
        }
        await publish_notification_event(
            self._session,
            recipient_ids={initiator_id},
            type_=NotificationType.TEAM_INVITATION_ACCEPTED,
            payload=payload,
            actor_id=user_id,
        )

    async def decline_team_invitation(self, *, user_id: int, invitation_id: int) -> None:
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
        await publish_notification_event(
            self._session,
            recipient_ids={initiator_id},
            type_=NotificationType.TEAM_INVITATION_DECLINED,
            payload=payload,
            actor_id=user_id,
        )

    async def approve_team_join_request(
        self,
        *,
        approver_user_id: int,
        team_id: int,
        request_id: int,
    ) -> None:
        if not await self._team_repo.is_team_at_least_admin(team_id, approver_user_id):
            raise ForbiddenError(
                f"User {approver_user_id} is not authorized to approve requests for team {team_id}."
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
        if await self._team_repo.is_team_member(team_id, requester_id):
            raise BadRequestError("Cannot approve request, user is already a member.")

        await self._update_status(app, ApplicationStatus.APPROVED, processor_id=approver_user_id)
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
        await publish_notification_event(
            self._session,
            recipient_ids={requester_id},
            type_=NotificationType.TEAM_REQUEST_APPROVED,
            payload=payload,
            actor_id=approver_user_id,
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
                f"User {rejector_user_id} is not authorized to reject requests for team {team_id}."
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
        await self._update_status(app, ApplicationStatus.REJECTED, processor_id=rejector_user_id)

        payload: dict[str, Any] = {
            "rejector": {"type": "user", "id": str(rejector_user_id)},
            "team": {"type": "team", "id": str(team_id)},
            "application": {
                "type": "team_membership_application",
                "id": str(request_id),
            },
            "requester": {"type": "user", "id": str(requester_id)},
        }
        await publish_notification_event(
            self._session,
            recipient_ids={requester_id},
            type_=NotificationType.TEAM_REQUEST_REJECTED,
            payload=payload,
            actor_id=rejector_user_id,
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
                f"User {canceler_user_id} is not authorized to cancel invitations for team {team_id}."
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
        await self._update_status(app, ApplicationStatus.CANCELED, processor_id=canceler_user_id)

        payload: dict[str, Any] = {
            "canceler": {"type": "user", "id": str(canceler_user_id)},
            "team": {"type": "team", "id": str(team_id)},
            "application": {
                "type": "team_membership_application",
                "id": str(invitation_id),
            },
            "invitedUser": {"type": "user", "id": str(invited_user_id)},
        }
        await publish_notification_event(
            self._session,
            recipient_ids={invited_user_id},
            type_=NotificationType.TEAM_INVITATION_CANCELED,
            payload=payload,
            actor_id=canceler_user_id,
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
        if not await self._team_repo.is_team_at_least_admin(team_id, requesting_user_id):
            raise ForbiddenError(
                f"User {requesting_user_id} is not authorized to view requests for team {team_id}."
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
        if not await self._team_repo.is_team_at_least_admin(team_id, requesting_user_id):
            raise ForbiddenError(
                f"User {requesting_user_id} is not authorized to view invitations for team {team_id}."
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
