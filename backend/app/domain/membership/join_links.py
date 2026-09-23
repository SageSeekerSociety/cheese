"""Shared invitations grant membership only after the recipient confirms."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.domain.identity.actor import Actor
from app.domain.membership.repositories import MemberRepository
from app.domain.membership.roster import roster
from app.domain.membership.services import MemberService, _reject_execution_identity
from app.domain.project.models import Project, ProjectJoinLink, ProjectRole


class JoinLinkService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def _managed_project(self, project_id: uuid.UUID, actor: Actor) -> Project:
        # Serialize link creation, revocation, and joins through a link.
        project = await self._session.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if project is None:
            raise NotFoundError("Project not found")
        await MemberService(self._session).require_manager(project_id, actor)
        return project

    async def _get(self, project_id: uuid.UUID) -> ProjectJoinLink | None:
        return await self._session.scalar(
            select(ProjectJoinLink).where(ProjectJoinLink.project_id == project_id)
        )

    async def current(
        self, project_id: uuid.UUID, actor: Actor
    ) -> ProjectJoinLink | None:
        await MemberService(self._session).require_manager(project_id, actor)
        link = await self._get(project_id)
        return link if link and link.expires_at > datetime.now(UTC) else None

    async def create(self, project_id: uuid.UUID, actor: Actor) -> ProjectJoinLink:
        await self._managed_project(project_id, actor)
        link = await self._get(project_id)
        if link is not None and link.expires_at > datetime.now(UTC):
            return link
        if link is None:
            link = ProjectJoinLink(project_id=project_id)
            self._session.add(link)
        link.token = secrets.token_urlsafe(32)
        link.created_by = actor.handle
        link.expires_at = datetime.now(UTC) + timedelta(days=7)
        await self._session.flush()
        return link

    async def revoke(self, project_id: uuid.UUID, actor: Actor) -> None:
        await self._managed_project(project_id, actor)
        link = await self._get(project_id)
        if link is not None:
            await self._session.delete(link)
            await self._session.flush()

    async def describe(self, token: str, actor: Actor, *, join: bool = False) -> dict:
        link = await self._session.scalar(
            select(ProjectJoinLink).where(ProjectJoinLink.token == token)
        )
        if link is None:
            raise NotFoundError("Invitation link is invalid or expired")
        # Use the same lock as create/revoke, then re-read the link so a
        # revocation committed while we waited cannot leave access open.
        project = await self._session.scalar(
            select(Project).where(Project.id == link.project_id).with_for_update()
        )
        link = await self._session.scalar(
            select(ProjectJoinLink)
            .where(ProjectJoinLink.token == token)
            .execution_options(populate_existing=True)
        )
        if project is None or link is None or link.expires_at <= datetime.now(UTC):
            raise NotFoundError("Invitation link is invalid or expired")
        await _reject_execution_identity(self._session, actor.handle)
        existing = any(
            m.handle == actor.handle for m in await roster(self._session, project.id)
        )
        if join and not existing:
            await MemberRepository(self._session).add(
                project_id=project.id, user_handle=actor.handle, role=ProjectRole.member
            )
        return {
            "project_id": str(project.id),
            "project_name": project.name,
            "already_member": existing or join,
            "expires_at": link.expires_at.isoformat(),
        }
