from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class SystemRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    MODERATOR = "MODERATOR"
    USER = "USER"
    GUEST = "GUEST"


class Action(str, Enum):
    CREATE = "CREATE"
    READ = "READ"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    ADMIN = "ADMIN"


class Resource(str, Enum):
    TEAM = "TEAM"
    TEAM_MEMBERSHIP = "TEAM_MEMBERSHIP"
    TEAM_REQUEST = "TEAM_REQUEST"
    TEAM_INVITATION = "TEAM_INVITATION"
    TASK = "TASK"
    TASK_PARTICIPANT = "TASK_PARTICIPANT"
    TASK_SUBMISSION = "TASK_SUBMISSION"
    SPACE = "SPACE"
    SPACE_CATEGORY = "SPACE_CATEGORY"
    PROJECT = "PROJECT"
    PROJECT_MEMBERSHIP = "PROJECT_MEMBERSHIP"
    KNOWLEDGE = "KNOWLEDGE"
    DISCUSSION = "DISCUSSION"
    NOTIFICATION = "NOTIFICATION"
    USER = "USER"
    USER_IDENTITY = "USER_IDENTITY"
    QUESTION = "QUESTION"
    ANSWER = "ANSWER"
    ATTACHMENT = "ATTACHMENT"


class Role(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    PARTICIPANT = "PARTICIPANT"
    SPACE_ADMIN = "SPACE_ADMIN"
    GUEST = "GUEST"


@dataclass
class Permission:
    action: Action
    resource: Resource

    def __str__(self) -> str:
        return f"{self.action.value}:{self.resource.value}"


@dataclass
class AuthUserInfo:
    user_id: int
    system_roles: set[SystemRole] = field(default_factory=lambda: {SystemRole.USER})
    domain_roles: dict[str, set[Role]] = field(default_factory=dict)

    def has_system_role(self, role: SystemRole) -> bool:
        return role in self.system_roles

    def has_domain_role(self, domain: str, resource_id: int, role: Role) -> bool:
        key = f"{domain}:{resource_id}"
        return role in self.domain_roles.get(key, set())

    def add_domain_role(self, domain: str, resource_id: int, role: Role) -> None:
        key = f"{domain}:{resource_id}"
        if key not in self.domain_roles:
            self.domain_roles[key] = set()
        self.domain_roles[key].add(role)


class RoleHierarchy:
    def __init__(self) -> None:
        self._parents: dict[Role, set[Role]] = {
            Role.OWNER: {Role.ADMIN},
            Role.ADMIN: {Role.MEMBER},
            Role.MEMBER: set(),
            Role.PARTICIPANT: set(),
            Role.SPACE_ADMIN: set(),
            Role.GUEST: set(),
        }

    def get_all_roles(self, role: Role) -> set[Role]:
        result = {role}
        to_check = [role]
        while to_check:
            current = to_check.pop()
            parents = self._parents.get(current, set())
            for parent in parents:
                if parent not in result:
                    result.add(parent)
                    to_check.append(parent)
        return result

    def is_parent_or_equal(self, role: Role, target: Role) -> bool:
        return target in self.get_all_roles(role)


ROLE_HIERARCHY = RoleHierarchy()


PermissionCondition = Callable[[AuthUserInfo, Action, Resource, int | None, dict[str, Any]], bool]


@dataclass
class PermissionRule:
    conditions: list[PermissionCondition] = field(default_factory=list)
    or_conditions: list[list[PermissionCondition]] = field(default_factory=list)

    def check(
        self,
        user: AuthUserInfo,
        action: Action,
        resource: Resource,
        resource_id: int | None,
        context: dict[str, Any],
    ) -> bool:
        for cond in self.conditions:
            if not cond(user, action, resource, resource_id, context):
                return False
        for or_group in self.or_conditions:
            if not any(cond(user, action, resource, resource_id, context) for cond in or_group):
                return False
        return True

    @staticmethod
    def allow_all() -> PermissionRule:
        return PermissionRule()

    @staticmethod
    def owner_only(owner_key: str = "owner_id") -> PermissionRule:
        def check_owner(
            user: AuthUserInfo,
            action: Action,
            resource: Resource,
            resource_id: int | None,
            context: dict[str, Any],
        ) -> bool:
            owner_id = context.get(owner_key)
            return owner_id is not None and owner_id == user.user_id

        return PermissionRule(conditions=[check_owner])


@dataclass
class PermissionConfig:
    role: Role
    action: Action
    resource: Resource
    rule: PermissionRule = field(default_factory=PermissionRule.allow_all)
