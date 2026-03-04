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
from app.domain.space.models import SpaceAdminRelation


async def get_space_roles(
    db: AsyncSession,
    user_id: int,
    domain: str,
    resource_id: int,
) -> set[Role]:
    roles: set[Role] = set()

    admin_stmt = select(SpaceAdminRelation.role).where(
        SpaceAdminRelation.space_id == resource_id,
        SpaceAdminRelation.admin_id == user_id,
        SpaceAdminRelation.deleted_at.is_(None),
    )
    result = await db.execute(admin_stmt)
    role_str = result.scalar_one_or_none()

    if role_str is not None:
        role_mapping = {
            "OWNER": Role.OWNER,
            "ADMIN": Role.ADMIN,
        }
        role = role_mapping.get(role_str.upper())
        if role:
            roles.add(role)

    return roles


SPACE_PERMISSIONS = [
    PermissionConfig(Role.GUEST, Action.READ, Resource.SPACE),
    PermissionConfig(Role.GUEST, Action.READ, Resource.SPACE_CATEGORY),
    PermissionConfig(Role.ADMIN, Action.UPDATE, Resource.SPACE),
    PermissionConfig(Role.ADMIN, Action.CREATE, Resource.SPACE_CATEGORY),
    PermissionConfig(Role.ADMIN, Action.UPDATE, Resource.SPACE_CATEGORY),
    PermissionConfig(Role.ADMIN, Action.DELETE, Resource.SPACE_CATEGORY),
    PermissionConfig(Role.OWNER, Action.DELETE, Resource.SPACE),
    PermissionConfig(Role.OWNER, Action.ADMIN, Resource.SPACE),
]


def register_space_permissions() -> None:
    permission_checker.register_configs(SPACE_PERMISSIONS)
    permission_checker.register_role_provider("space", get_space_roles)
