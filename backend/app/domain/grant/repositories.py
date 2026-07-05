"""Data access for project grants. No business logic."""

from datetime import UTC, datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.grant.models import ProjectGrant


def _active(stmt: Select[tuple[ProjectGrant]]) -> Select[tuple[ProjectGrant]]:
    return stmt.where(ProjectGrant.revoked_at.is_(None), ProjectGrant.deleted_at.is_(None))


class ProjectGrantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        project_id: int,
        granted_by_user_id: int,
        resource_type: str,
        action: str,
        resource_id: int | None = None,
    ) -> ProjectGrant:
        now = datetime.now(UTC)
        grant = ProjectGrant(
            project_id=project_id,
            granted_by_user_id=granted_by_user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            revoked_at=None,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self._session.add(grant)
        await self._session.flush()
        return grant

    async def get_by_id(self, grant_id: int) -> ProjectGrant | None:
        stmt: Select[tuple[ProjectGrant]] = select(ProjectGrant).where(
            ProjectGrant.id == grant_id, ProjectGrant.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_active_exact(
        self,
        *,
        project_id: int,
        granted_by_user_id: int,
        resource_type: str,
        action: str,
        resource_id: int | None,
    ) -> ProjectGrant | None:
        """An identical, still-active grant (for idempotent granting)."""
        stmt = _active(
            select(ProjectGrant).where(
                ProjectGrant.project_id == project_id,
                ProjectGrant.granted_by_user_id == granted_by_user_id,
                ProjectGrant.resource_type == resource_type,
                ProjectGrant.action == action,
                ProjectGrant.resource_id.is_(resource_id)
                if resource_id is None
                else ProjectGrant.resource_id == resource_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_active(self, project_id: int) -> list[ProjectGrant]:
        stmt = _active(
            select(ProjectGrant).where(ProjectGrant.project_id == project_id)
        ).order_by(ProjectGrant.id.asc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def find_covering(
        self, *, project_id: int, resource_type: str, action: str, resource_id: int
    ) -> list[ProjectGrant]:
        """Active grants that cover (resource_type, action, resource_id) — either
        a resource-specific grant or a type-wide (resource_id NULL) grant."""
        stmt = _active(
            select(ProjectGrant).where(
                ProjectGrant.project_id == project_id,
                ProjectGrant.resource_type == resource_type,
                ProjectGrant.action == action,
                or_(
                    ProjectGrant.resource_id.is_(None),
                    ProjectGrant.resource_id == resource_id,
                ),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def revoke(self, grant: ProjectGrant) -> ProjectGrant:
        now = datetime.now(UTC)
        grant.revoked_at = now
        grant.updated_at = now
        await self._session.flush()
        return grant
