"""A project's join link, and the requests it produces.

The link is the same shape a team's is: one per project, permanent until a
manager resets it, and ``join_approval`` decides what following it does. Off,
the person is on the roster the moment they confirm; on, they file a request a
manager approves. Either way the person's own click is what starts it — the
preview never joins anyone.
"""

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.identity.actor import Actor
from app.domain.membership.repositories import MemberRepository
from app.domain.membership.roster import roster
from app.domain.membership.services import MemberService, _reject_execution_identity
from app.domain.project.models import (
    JoinRequestStatus,
    Project,
    ProjectJoinRequest,
    ProjectMember,
    ProjectRole,
)
from app.domain.project.services import ProjectService

_INVALID = "Invitation link is invalid or has been reset"
# The two answers on a manager's decision card, affirmative first. Choosing one
# on the card is the decision itself (``routes/alerts.resolve_notification``).
REQUEST_OPTIONS = ("批准", "拒绝")


class JoinLinkService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _managed_project(self, project_id: uuid.UUID, actor: Actor) -> Project:
        # Serialize link changes and joins through a link on the project row.
        project = await self._session.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if project is None:
            raise NotFoundError("Project not found")
        await MemberService(self._session).require_manager(project_id, actor)
        return project

    @staticmethod
    def _link(project: Project) -> dict:
        return {"token": project.join_token, "approval": project.join_approval}

    async def current(self, project_id: uuid.UUID, actor: Actor) -> dict:
        project = await self._managed_project(project_id, actor)
        if project.join_token is None:
            project.join_token = secrets.token_urlsafe(32)
            await self._session.flush()
        return self._link(project)

    async def reset(self, project_id: uuid.UUID, actor: Actor) -> dict:
        project = await self._managed_project(project_id, actor)
        project.join_token = secrets.token_urlsafe(32)
        await self._session.flush()
        return self._link(project)

    async def set_approval(
        self, project_id: uuid.UUID, approval: bool, actor: Actor
    ) -> dict:
        project = await self._managed_project(project_id, actor)
        if project.join_token is None:
            project.join_token = secrets.token_urlsafe(32)
        project.join_approval = approval
        await self._session.flush()
        return self._link(project)

    async def _project_for_token(self, token: str) -> Project:
        # Take the same lock as reset, then re-check the token, so a reset
        # committed while we waited cannot leave the old link working.
        project_id = await self._session.scalar(
            select(Project.id).where(Project.join_token == token)
        )
        if project_id is None:
            raise NotFoundError(_INVALID)
        project = await self._session.scalar(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if project is None or project.join_token != token:
            raise NotFoundError(_INVALID)
        return project

    async def _pending(
        self, project_id: uuid.UUID, handle: str
    ) -> ProjectJoinRequest | None:
        return await self._session.scalar(
            select(ProjectJoinRequest).where(
                ProjectJoinRequest.project_id == project_id,
                ProjectJoinRequest.requester_handle == handle,
                ProjectJoinRequest.status == JoinRequestStatus.pending,
            )
        )

    async def _status(self, project: Project, handle: str) -> str:
        if any(m.handle == handle for m in await roster(self._session, project.id)):
            return "member"
        if await self._pending(project.id, handle) is not None:
            return "pending"
        return "none"

    def _describe(self, project: Project, status: str) -> dict:
        return {
            "project_id": str(project.id),
            "project_name": project.name,
            "approval": project.join_approval,
            "join_status": status,
        }

    async def describe(self, token: str, actor: Actor) -> dict:
        project = await self._project_for_token(token)
        await _reject_execution_identity(self._session, actor.handle)
        return self._describe(project, await self._status(project, actor.handle))

    async def join(self, token: str, actor: Actor, *, message: str = "") -> dict:
        project = await self._project_for_token(token)
        await _reject_execution_identity(self._session, actor.handle)
        status = await self._status(project, actor.handle)
        if status != "none":
            return self._describe(project, status)
        if not project.join_approval:
            await MemberRepository(self._session).add(
                project_id=project.id, user_handle=actor.handle, role=ProjectRole.member
            )
            return self._describe(project, "member")
        request = ProjectJoinRequest(
            project_id=project.id, requester_handle=actor.handle, message=message
        )
        self._session.add(request)
        await self._session.flush()
        await self._ask_managers(project, request)
        return self._describe(project, "pending")

    async def _managers(self, project: Project) -> list[str]:
        leads = await self._session.scalars(
            select(ProjectMember.user_handle).where(
                ProjectMember.project_id == project.id,
                ProjectMember.role == ProjectRole.lead,
            )
        )
        handles = [project.owner_handle] if project.owner_handle else []
        return handles + [h for h in leads.all() if h not in handles]

    async def _ask_managers(
        self, project: Project, request: ProjectJoinRequest
    ) -> None:
        # Imported here: notification.services imports membership at module load.
        from app.domain.notification.models import NotificationLevel, NotificationType
        from app.domain.notification.services import ProjectNotificationService

        requester = await ProjectService(self._session).person(request.requester_handle)
        name = requester["name"]
        notifications = ProjectNotificationService(self._session)
        for handle in await self._managers(project):
            await notifications.create(
                project_id=project.id,
                level=NotificationLevel.strong,
                kind=NotificationType.DECISION_REQUEST,
                title=f"{name} 申请加入项目「{project.name}」",
                body=request.message,
                target_handle=handle,
                payload={
                    "join_request_id": str(request.id),
                    "project_name": project.name,
                    "requester_handle": request.requester_handle,
                    "message": request.message,
                    "options": list(REQUEST_OPTIONS),
                },
            )

    async def list_pending(self, project_id: uuid.UUID, actor: Actor) -> list[dict]:
        await self._managed_project(project_id, actor)
        projects = ProjectService(self._session)
        rows = await self._session.scalars(
            select(ProjectJoinRequest)
            .where(
                ProjectJoinRequest.project_id == project_id,
                ProjectJoinRequest.status == JoinRequestStatus.pending,
            )
            .order_by(ProjectJoinRequest.created_at)
        )
        items = []
        for row in rows.all():
            items.append(
                {
                    "id": str(row.id),
                    "requester_handle": row.requester_handle,
                    **await projects.person(row.requester_handle),
                    "message": row.message,
                    "created_at": row.created_at.isoformat(),
                }
            )
        return items

    async def decide(
        self,
        project_id: uuid.UUID,
        request_id: uuid.UUID,
        *,
        approve: bool,
        actor: Actor,
    ) -> None:
        project = await self._managed_project(project_id, actor)
        request = await self._session.get(ProjectJoinRequest, request_id)
        if request is None or request.project_id != project_id:
            raise NotFoundError("Join request not found")
        if request.status != JoinRequestStatus.pending:
            raise ValidationError("这条申请已经处理过了")
        if approve:
            await _reject_execution_identity(self._session, request.requester_handle)
            if await self._status(project, request.requester_handle) != "member":
                await MemberRepository(self._session).add(
                    project_id=project_id,
                    user_handle=request.requester_handle,
                    role=ProjectRole.member,
                )
        request.status = (
            JoinRequestStatus.approved if approve else JoinRequestStatus.rejected
        )
        request.decided_by = actor.handle
        request.decided_at = datetime.now(UTC)
        await self._session.flush()
        await self._settle_notifications(project, request)

    async def _settle_notifications(
        self, project: Project, request: ProjectJoinRequest
    ) -> None:
        """Clear every manager's pending item, and tell the person the answer."""
        from app.domain.notification.models import (
            Notification,
            NotificationLevel,
            NotificationType,
        )
        from app.domain.notification.services import ProjectNotificationService

        stmt = select(Notification).where(
            Notification.project_id == project.id,
            Notification.resolved_at.is_(None),
        )
        for row in (await self._session.scalars(stmt)).all():
            payload = dict(row.metadata_payload or {})
            if payload.get("join_request_id") == str(request.id):
                row.resolved_at = request.decided_at
                payload["resolved_choice"] = REQUEST_OPTIONS[
                    0 if request.status == JoinRequestStatus.approved else 1
                ]
                row.metadata_payload = payload
        await self._session.flush()
        verdict = "已通过" if request.status == JoinRequestStatus.approved else "未通过"
        await ProjectNotificationService(self._session).create(
            project_id=project.id,
            level=NotificationLevel.light,
            kind=NotificationType.CHANGE_ALERT,
            title=f"你加入项目「{project.name}」的申请{verdict}",
            target_handle=request.requester_handle,
            payload={"project_name": project.name},
        )
