from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import permission_checker
from app.auth.core import (
    Action,
    PermissionConfig,
    Resource,
    Role,
)
from app.domain.team.models import TeamUserRelation, TeamMemberRole


async def get_team_roles(
    db: AsyncSession,
    user_id: int,
    domain: str,
    resource_id: int,
) -> set[Role]:
    stmt = select(TeamUserRelation.role).where(
        TeamUserRelation.team_id == resource_id,
        TeamUserRelation.user_id == user_id,
        TeamUserRelation.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    role_int = result.scalar_one_or_none()
    if role_int is None:
        return set()

    role_mapping = {
        TeamMemberRole.OWNER: Role.OWNER,
        TeamMemberRole.ADMIN: Role.ADMIN,
        TeamMemberRole.MEMBER: Role.MEMBER,
    }
    role = role_mapping.get(role_int)
    return {role} if role else set()


TEAM_PERMISSIONS = [
    PermissionConfig(Role.GUEST, Action.CREATE, Resource.TEAM),
    PermissionConfig(Role.GUEST, Action.READ, Resource.TEAM),
    PermissionConfig(Role.MEMBER, Action.READ, Resource.TEAM),
    PermissionConfig(Role.MEMBER, Action.READ, Resource.TEAM_MEMBERSHIP),
    PermissionConfig(Role.ADMIN, Action.UPDATE, Resource.TEAM),
    PermissionConfig(Role.ADMIN, Action.READ, Resource.TEAM_REQUEST),
    PermissionConfig(Role.ADMIN, Action.UPDATE, Resource.TEAM_REQUEST),
    PermissionConfig(Role.ADMIN, Action.READ, Resource.TEAM_INVITATION),
    PermissionConfig(Role.ADMIN, Action.CREATE, Resource.TEAM_INVITATION),
    PermissionConfig(Role.ADMIN, Action.DELETE, Resource.TEAM_INVITATION),
    PermissionConfig(Role.ADMIN, Action.CREATE, Resource.TEAM_MEMBERSHIP),
    PermissionConfig(Role.ADMIN, Action.DELETE, Resource.TEAM_MEMBERSHIP),
    PermissionConfig(Role.ADMIN, Action.UPDATE, Resource.TEAM_MEMBERSHIP),
    PermissionConfig(Role.OWNER, Action.DELETE, Resource.TEAM),
    PermissionConfig(Role.OWNER, Action.ADMIN, Resource.TEAM),
]


def register_team_permissions() -> None:
    permission_checker.register_configs(TEAM_PERMISSIONS)
    permission_checker.register_role_provider("team", get_team_roles)
