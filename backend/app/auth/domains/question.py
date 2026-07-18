from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.checker import permission_checker
from app.auth.core import (
    Action,
    PermissionConfig,
    Resource,
    Role,
)
from app.domain.answers.models import Answer
from app.domain.questions.models import Question


async def get_question_roles(
    db: AsyncSession,
    user_id: int,
    domain: str,
    resource_id: int,
) -> set[Role]:
    roles: set[Role] = set()

    question_stmt = select(Question.created_by_id).where(
        Question.id == resource_id,
        Question.deleted_at.is_(None),
    )
    result = await db.execute(question_stmt)
    created_by = result.scalar_one_or_none()

    if created_by is not None and created_by == user_id:
        roles.add(Role.OWNER)

    return roles


async def get_answer_roles(
    db: AsyncSession,
    user_id: int,
    domain: str,
    resource_id: int,
) -> set[Role]:
    roles: set[Role] = set()

    answer_stmt = select(Answer.created_by_id).where(
        Answer.id == resource_id,
        Answer.deleted_at.is_(None),
    )
    result = await db.execute(answer_stmt)
    created_by = result.scalar_one_or_none()

    if created_by is not None and created_by == user_id:
        roles.add(Role.OWNER)

    return roles


QUESTION_PERMISSIONS = [
    PermissionConfig(Role.GUEST, Action.READ, Resource.QUESTION),
    PermissionConfig(Role.GUEST, Action.CREATE, Resource.QUESTION),
    PermissionConfig(Role.GUEST, Action.READ, Resource.ANSWER),
    PermissionConfig(Role.GUEST, Action.CREATE, Resource.ANSWER),
    PermissionConfig(Role.OWNER, Action.UPDATE, Resource.QUESTION),
    PermissionConfig(Role.OWNER, Action.DELETE, Resource.QUESTION),
    PermissionConfig(Role.OWNER, Action.ADMIN, Resource.QUESTION),
    PermissionConfig(Role.OWNER, Action.UPDATE, Resource.ANSWER),
    PermissionConfig(Role.OWNER, Action.DELETE, Resource.ANSWER),
]


def register_question_permissions() -> None:
    permission_checker.register_configs(QUESTION_PERMISSIONS)
    permission_checker.register_role_provider("question", get_question_roles)
    permission_checker.register_role_provider("answer", get_answer_roles)
