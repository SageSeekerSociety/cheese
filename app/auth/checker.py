from __future__ import annotations

from typing import Any, Callable, Awaitable

from fastapi import Depends, Request

from app.core.errors import AccessDeniedError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.core import (
    Action,
    AuthUserInfo,
    PermissionConfig,
    Resource,
    Role,
    ROLE_HIERARCHY,
    SystemRole,
)
from app.common.auth import get_optional_user_id
from app.db.session import get_db


DomainRoleProvider = Callable[[AsyncSession, int, str, int], Awaitable[set[Role]]]


class PermissionChecker:
    def __init__(self) -> None:
        self._configs: list[PermissionConfig] = []
        self._role_providers: dict[str, DomainRoleProvider] = {}

    def register_config(self, config: PermissionConfig) -> None:
        self._configs.append(config)

    def register_configs(self, configs: list[PermissionConfig]) -> None:
        self._configs.extend(configs)

    def register_role_provider(self, domain: str, provider: DomainRoleProvider) -> None:
        self._role_providers[domain] = provider

    def get_configs_for_role(self, role: Role) -> list[PermissionConfig]:
        return [c for c in self._configs if c.role == role]

    async def get_domain_roles(
        self,
        db: AsyncSession,
        user_id: int,
        domain: str,
        resource_id: int,
    ) -> set[Role]:
        provider = self._role_providers.get(domain)
        if provider is None:
            return set()
        return await provider(db, user_id, domain, resource_id)

    async def check_permission(
        self,
        db: AsyncSession,
        user: AuthUserInfo,
        action: Action,
        resource: Resource,
        resource_id: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> bool:
        ctx = context or {}
        domain = self._get_domain_for_resource(resource)

        if user.has_system_role(SystemRole.SUPER_ADMIN):
            return True

        if resource_id is not None and domain:
            domain_roles = await self.get_domain_roles(db, user.user_id, domain, resource_id)
            for role in domain_roles:
                user.add_domain_role(domain, resource_id, role)

        all_roles: set[Role] = set()
        if resource_id is not None and domain:
            key = f"{domain}:{resource_id}"
            all_roles = user.domain_roles.get(key, set())

        expanded_roles: set[Role] = set()
        for role in all_roles:
            expanded_roles.update(ROLE_HIERARCHY.get_all_roles(role))

        for role in expanded_roles:
            configs = self.get_configs_for_role(role)
            for config in configs:
                if config.action == action and config.resource == resource:
                    if config.rule.check(user, action, resource, resource_id, ctx):
                        return True

        if SystemRole.USER in user.system_roles:
            for config in self._configs:
                if (
                    config.role == Role.GUEST
                    and config.action == action
                    and config.resource == resource
                ):
                    if config.rule.check(user, action, resource, resource_id, ctx):
                        return True

        return False

    def _get_domain_for_resource(self, resource: Resource) -> str | None:
        mapping = {
            Resource.TEAM: "team",
            Resource.TEAM_MEMBERSHIP: "team",
            Resource.TEAM_REQUEST: "team",
            Resource.TEAM_INVITATION: "team",
            Resource.TASK: "task",
            Resource.TASK_PARTICIPANT: "task",
            Resource.TASK_SUBMISSION: "task",
            Resource.SPACE: "space",
            Resource.SPACE_CATEGORY: "space",
            Resource.PROJECT: "project",
            Resource.PROJECT_MEMBERSHIP: "project",
            Resource.KNOWLEDGE: "knowledge",
            Resource.DISCUSSION: "discussion",
            Resource.QUESTION: "question",
            Resource.ANSWER: "answer",
        }
        return mapping.get(resource)


permission_checker = PermissionChecker()


async def get_auth_user(
    request: Request,
    user_id: int | None = Depends(get_optional_user_id),
) -> AuthUserInfo:
    if user_id is None:
        return AuthUserInfo(user_id=0, system_roles={SystemRole.GUEST})
    return AuthUserInfo(user_id=user_id, system_roles={SystemRole.USER})


def require_permission(
    action: Action,
    resource: Resource,
    resource_id_param: str | None = None,
    context_builder: Callable[[Request, AsyncSession, int | None], Awaitable[dict[str, Any]]]
    | None = None,
):
    async def dependency(
        request: Request,
        db: AsyncSession = Depends(get_db),
        auth_user: AuthUserInfo = Depends(get_auth_user),
    ) -> AuthUserInfo:
        resource_id: int | None = None
        if resource_id_param:
            resource_id = request.path_params.get(resource_id_param)
            if resource_id is not None:
                resource_id = int(resource_id)

        context: dict[str, Any] = {}
        if context_builder:
            context = await context_builder(request, db, resource_id)

        allowed = await permission_checker.check_permission(
            db=db,
            user=auth_user,
            action=action,
            resource=resource,
            resource_id=resource_id,
            context=context,
        )

        if not allowed:
            raise AccessDeniedError(
                action=action.value if action else None,
                resource_type=resource.value if resource else None,
                resource_id=resource_id,
            )

        return auth_user

    return Depends(dependency)
