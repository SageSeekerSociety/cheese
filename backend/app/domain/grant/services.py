"""Business logic for project grants (capability delegation to a project)."""

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.domain.grant.models import ProjectGrant
from app.domain.grant.repositories import ProjectGrantRepository


class ProjectGrantService:
    def __init__(self, repo: ProjectGrantRepository) -> None:
        self._repo = repo

    async def grant(
        self,
        *,
        project_id: int,
        granted_by_user_id: int,
        resource_type: str,
        action: str,
        resource_id: int | None = None,
    ) -> ProjectGrant:
        """Share a capability with a project. Idempotent: an identical active
        grant by the same holder is returned rather than duplicated.

        NOTE: this records the share. Whether ``granted_by_user_id`` actually
        holds the capability is re-checked live at the authz choke point, so a
        share can never exceed what its granter currently has.
        """
        if not resource_type or not action:
            raise BadRequestError("resource_type and action are required")
        existing = await self._repo.find_active_exact(
            project_id=project_id,
            granted_by_user_id=granted_by_user_id,
            resource_type=resource_type,
            action=action,
            resource_id=resource_id,
        )
        if existing is not None:
            return existing
        return await self._repo.create(
            project_id=project_id,
            granted_by_user_id=granted_by_user_id,
            resource_type=resource_type,
            action=action,
            resource_id=resource_id,
        )

    async def revoke(self, *, grant_id: int, by_user_id: int) -> ProjectGrant:
        grant = await self._repo.get_by_id(grant_id)
        if grant is None:
            raise NotFoundError(f"Grant {grant_id} not found")
        # Only the holder who shared it may pull it back.
        if grant.granted_by_user_id != by_user_id:
            raise ForbiddenError("only the granter can revoke this grant")
        if grant.revoked_at is not None:
            return grant
        return await self._repo.revoke(grant)

    async def list_active(self, project_id: int) -> list[ProjectGrant]:
        return await self._repo.list_active(project_id)

    async def granters_covering(
        self, *, project_id: int, resource_type: str, action: str, resource_id: int
    ) -> list[int]:
        """Holder user-ids whose active grants cover this capability. The authz
        layer then verifies each still holds it (live delegation) — if any does,
        the project actor inherits the capability."""
        grants = await self._repo.find_covering(
            project_id=project_id,
            resource_type=resource_type,
            action=action,
            resource_id=resource_id,
        )
        # Distinct granters, preserving first-seen order.
        seen: set[int] = set()
        out: list[int] = []
        for g in grants:
            if g.granted_by_user_id not in seen:
                seen.add(g.granted_by_user_id)
                out.append(g.granted_by_user_id)
        return out
