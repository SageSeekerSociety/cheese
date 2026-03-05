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
from app.domain.knowledge.models import Knowledge
from app.domain.team.models import TeamMemberRole, TeamUserRelation


async def get_knowledge_roles(
    db: AsyncSession,
    user_id: int,
    domain: str,
    resource_id: int,
) -> set[Role]:
    roles: set[Role] = set()

    knowledge_stmt = select(Knowledge.created_by, Knowledge.team_id).where(
        Knowledge.id == resource_id,
        Knowledge.deleted_at.is_(None),
    )
    result = await db.execute(knowledge_stmt)
    row = result.one_or_none()

    if row is None:
        return roles

    created_by, team_id = row
    if created_by == user_id:
        roles.add(Role.OWNER)

    if team_id:
        team_stmt = select(TeamUserRelation.role).where(
            TeamUserRelation.team_id == team_id,
            TeamUserRelation.user_id == user_id,
            TeamUserRelation.deleted_at.is_(None),
        )
        team_result = await db.execute(team_stmt)
        role_int = team_result.scalar_one_or_none()
        if role_int is not None:
            role_mapping = {
                TeamMemberRole.OWNER: Role.OWNER,
                TeamMemberRole.ADMIN: Role.ADMIN,
                TeamMemberRole.MEMBER: Role.MEMBER,
            }
            role = role_mapping.get(role_int)
            if role:
                roles.add(role)

    return roles


KNOWLEDGE_PERMISSIONS = [
    PermissionConfig(Role.MEMBER, Action.READ, Resource.KNOWLEDGE),
    PermissionConfig(Role.MEMBER, Action.CREATE, Resource.KNOWLEDGE),
    PermissionConfig(Role.OWNER, Action.UPDATE, Resource.KNOWLEDGE),
    PermissionConfig(Role.OWNER, Action.DELETE, Resource.KNOWLEDGE),
    PermissionConfig(Role.ADMIN, Action.DELETE, Resource.KNOWLEDGE),
]


def register_knowledge_permissions() -> None:
    permission_checker.register_configs(KNOWLEDGE_PERMISSIONS)
    permission_checker.register_role_provider("knowledge", get_knowledge_roles)
