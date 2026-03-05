from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import permission_checker
from app.auth.core import (
    Action,
    PermissionConfig,
    Resource,
    Role,
)
from app.domain.task.models import Task, TaskMembership


async def get_task_roles(
    db: AsyncSession,
    user_id: int,
    domain: str,
    resource_id: int,
) -> set[Role]:
    roles: set[Role] = set()

    task_stmt = select(Task.creator_id, Task.space_id).where(
        Task.id == resource_id,
        Task.deleted_at.is_(None),
    )
    task_result = await db.execute(task_stmt)
    task_row = task_result.one_or_none()

    if task_row is None:
        return roles

    creator_id, space_id = task_row
    if creator_id == user_id:
        roles.add(Role.OWNER)

    membership_stmt = select(TaskMembership.id).where(
        TaskMembership.task_id == resource_id,
        TaskMembership.member_id == user_id,
        TaskMembership.is_team.is_(False),
        TaskMembership.deleted_at.is_(None),
    )
    membership_result = await db.execute(membership_stmt)
    if membership_result.scalar_one_or_none() is not None:
        roles.add(Role.PARTICIPANT)

    return roles


TASK_PERMISSIONS = [
    PermissionConfig(Role.GUEST, Action.READ, Resource.TASK),
    PermissionConfig(Role.GUEST, Action.CREATE, Resource.TASK),
    PermissionConfig(Role.GUEST, Action.READ, Resource.TASK_PARTICIPANT),
    PermissionConfig(Role.OWNER, Action.UPDATE, Resource.TASK),
    PermissionConfig(Role.OWNER, Action.DELETE, Resource.TASK),
    PermissionConfig(Role.OWNER, Action.ADMIN, Resource.TASK),
    PermissionConfig(Role.OWNER, Action.CREATE, Resource.TASK_PARTICIPANT),
    PermissionConfig(Role.OWNER, Action.UPDATE, Resource.TASK_PARTICIPANT),
    PermissionConfig(Role.OWNER, Action.DELETE, Resource.TASK_PARTICIPANT),
    PermissionConfig(Role.PARTICIPANT, Action.READ, Resource.TASK_SUBMISSION),
    PermissionConfig(Role.PARTICIPANT, Action.CREATE, Resource.TASK_SUBMISSION),
    PermissionConfig(Role.PARTICIPANT, Action.UPDATE, Resource.TASK_SUBMISSION),
    PermissionConfig(Role.SPACE_ADMIN, Action.UPDATE, Resource.TASK),
    PermissionConfig(Role.SPACE_ADMIN, Action.DELETE, Resource.TASK),
    PermissionConfig(Role.SPACE_ADMIN, Action.ADMIN, Resource.TASK_PARTICIPANT),
]


def register_task_permissions() -> None:
    permission_checker.register_configs(TASK_PERMISSIONS)
    permission_checker.register_role_provider("task", get_task_roles)
