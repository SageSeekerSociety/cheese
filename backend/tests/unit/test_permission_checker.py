from unittest.mock import AsyncMock

import pytest

from app.auth.checker import PermissionChecker
from app.auth.core import (
    ROLE_HIERARCHY,
    Action,
    AuthUserInfo,
    Permission,
    PermissionConfig,
    PermissionRule,
    Resource,
    Role,
    SystemRole,
)


class TestRoleHierarchy:
    def test_owner_inherits_all_roles(self) -> None:
        roles = ROLE_HIERARCHY.get_all_roles(Role.OWNER)
        assert Role.OWNER in roles
        assert Role.ADMIN in roles
        assert Role.MEMBER in roles

    def test_admin_inherits_member(self) -> None:
        roles = ROLE_HIERARCHY.get_all_roles(Role.ADMIN)
        assert Role.ADMIN in roles
        assert Role.MEMBER in roles
        assert Role.OWNER not in roles

    def test_member_only_has_member(self) -> None:
        roles = ROLE_HIERARCHY.get_all_roles(Role.MEMBER)
        assert Role.MEMBER in roles
        assert Role.ADMIN not in roles
        assert Role.OWNER not in roles

    def test_guest_only_has_guest(self) -> None:
        roles = ROLE_HIERARCHY.get_all_roles(Role.GUEST)
        assert Role.GUEST in roles
        assert len(roles) == 1


class TestPermissionRule:
    def test_always_rule_returns_true(self) -> None:
        rule = PermissionRule()
        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER})
        assert rule.check(user, Action.READ, Resource.TEAM, 1, {}) is True

    def test_condition_must_match(self) -> None:
        def is_owner_check(user, action, resource, resource_id, context):
            return context.get("is_owner") is True

        rule = PermissionRule(conditions=[is_owner_check])
        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER})
        assert rule.check(user, Action.READ, Resource.TEAM, 1, {"is_owner": True}) is True
        assert rule.check(user, Action.READ, Resource.TEAM, 1, {"is_owner": False}) is False

    def test_or_conditions(self) -> None:
        def is_owner_check(user, action, resource, resource_id, context):
            return context.get("is_owner") is True

        def is_admin_check(user, action, resource, resource_id, context):
            return context.get("is_admin") is True

        rule = PermissionRule(or_conditions=[[is_owner_check, is_admin_check]])
        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER})
        assert rule.check(user, Action.READ, Resource.TEAM, 1, {"is_owner": True}) is True
        assert rule.check(user, Action.READ, Resource.TEAM, 1, {"is_admin": True}) is True
        assert (
            rule.check(user, Action.READ, Resource.TEAM, 1, {"is_owner": False, "is_admin": False})
            is False
        )


class TestAuthUserInfo:
    def test_has_system_role(self) -> None:
        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER, SystemRole.ADMIN})
        assert user.has_system_role(SystemRole.USER) is True
        assert user.has_system_role(SystemRole.ADMIN) is True
        assert user.has_system_role(SystemRole.SUPER_ADMIN) is False

    def test_add_domain_role(self) -> None:
        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER})
        user.add_domain_role("team", 1, Role.OWNER)
        assert Role.OWNER in user.domain_roles.get("team:1", set())

    def test_guest_user(self) -> None:
        user = AuthUserInfo(user_id=0, system_roles={SystemRole.GUEST})
        assert user.has_system_role(SystemRole.GUEST) is True
        assert user.has_system_role(SystemRole.USER) is False


class TestPermissionChecker:
    @pytest.fixture
    def checker(self) -> PermissionChecker:
        return PermissionChecker()

    def test_register_config(self, checker: PermissionChecker) -> None:
        config = PermissionConfig(Role.MEMBER, Action.READ, Resource.TEAM)
        checker.register_config(config)
        configs = checker.get_configs_for_role(Role.MEMBER)
        assert config in configs

    def test_register_configs(self, checker: PermissionChecker) -> None:
        configs = [
            PermissionConfig(Role.MEMBER, Action.READ, Resource.TEAM),
            PermissionConfig(Role.ADMIN, Action.UPDATE, Resource.TEAM),
        ]
        checker.register_configs(configs)
        assert len(checker.get_configs_for_role(Role.MEMBER)) == 1
        assert len(checker.get_configs_for_role(Role.ADMIN)) == 1

    @pytest.mark.anyio
    async def test_super_admin_bypasses_all(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        user = AuthUserInfo(user_id=1, system_roles={SystemRole.SUPER_ADMIN})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.DELETE,
            resource=Resource.TEAM,
            resource_id=999,
        )
        assert allowed is True

    @pytest.mark.anyio
    async def test_guest_can_create_team(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        config = PermissionConfig(Role.GUEST, Action.CREATE, Resource.TEAM)
        checker.register_config(config)

        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.CREATE,
            resource=Resource.TEAM,
        )
        assert allowed is True

    @pytest.mark.anyio
    async def test_member_can_read_team(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        config = PermissionConfig(Role.MEMBER, Action.READ, Resource.TEAM)
        checker.register_config(config)

        async def fake_role_provider(db, user_id, domain, resource_id):
            return {Role.MEMBER}

        checker.register_role_provider("team", fake_role_provider)

        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.READ,
            resource=Resource.TEAM,
            resource_id=1,
        )
        assert allowed is True

    @pytest.mark.anyio
    async def test_owner_can_delete_team(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        config = PermissionConfig(Role.OWNER, Action.DELETE, Resource.TEAM)
        checker.register_config(config)

        async def fake_role_provider(db, user_id, domain, resource_id):
            return {Role.OWNER}

        checker.register_role_provider("team", fake_role_provider)

        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.DELETE,
            resource=Resource.TEAM,
            resource_id=1,
        )
        assert allowed is True

    @pytest.mark.anyio
    async def test_member_cannot_delete_team(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        config = PermissionConfig(Role.OWNER, Action.DELETE, Resource.TEAM)
        checker.register_config(config)

        async def fake_role_provider(db, user_id, domain, resource_id):
            return {Role.MEMBER}

        checker.register_role_provider("team", fake_role_provider)

        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.DELETE,
            resource=Resource.TEAM,
            resource_id=1,
        )
        assert allowed is False

    @pytest.mark.anyio
    async def test_role_hierarchy_allows_owner_to_do_admin_actions(
        self, checker: PermissionChecker
    ) -> None:
        db = AsyncMock()
        config = PermissionConfig(Role.ADMIN, Action.UPDATE, Resource.TEAM)
        checker.register_config(config)

        async def fake_role_provider(db, user_id, domain, resource_id):
            return {Role.OWNER}

        checker.register_role_provider("team", fake_role_provider)

        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.UPDATE,
            resource=Resource.TEAM,
            resource_id=1,
        )
        assert allowed is True

    @pytest.mark.anyio
    async def test_no_permission_without_role(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        config = PermissionConfig(Role.MEMBER, Action.READ, Resource.TEAM)
        checker.register_config(config)

        async def fake_role_provider(db, user_id, domain, resource_id):
            return set()

        checker.register_role_provider("team", fake_role_provider)

        user = AuthUserInfo(user_id=1, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.READ,
            resource=Resource.TEAM,
            resource_id=1,
        )
        assert allowed is False


class TestPermission:
    def test_permission_creation(self) -> None:
        perm = Permission(action=Action.READ, resource=Resource.TEAM)
        assert perm.action == Action.READ
        assert perm.resource == Resource.TEAM

    def test_permission_equality(self) -> None:
        perm1 = Permission(action=Action.READ, resource=Resource.TEAM)
        perm2 = Permission(action=Action.READ, resource=Resource.TEAM)
        assert perm1.action == perm2.action
        assert perm1.resource == perm2.resource


class TestCrossDomainPermission:
    @pytest.fixture
    def checker(self) -> PermissionChecker:
        return PermissionChecker()

    @pytest.mark.anyio
    async def test_project_permission_depends_on_team_membership(
        self, checker: PermissionChecker
    ) -> None:
        db = AsyncMock()
        config = PermissionConfig(Role.MEMBER, Action.UPDATE, Resource.PROJECT)
        checker.register_config(config)

        async def project_role_provider(db, user_id, domain, resource_id):
            if user_id == 123:
                return {Role.MEMBER}
            return set()

        checker.register_role_provider("project", project_role_provider)

        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.UPDATE,
            resource=Resource.PROJECT,
            resource_id=1,
        )
        assert allowed is True

    @pytest.mark.anyio
    async def test_deny_project_update_without_membership(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        config = PermissionConfig(Role.MEMBER, Action.UPDATE, Resource.PROJECT)
        checker.register_config(config)

        async def project_role_provider(db, user_id, domain, resource_id):
            return set()

        checker.register_role_provider("project", project_role_provider)

        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.UPDATE,
            resource=Resource.PROJECT,
            resource_id=1,
        )
        assert allowed is False

    @pytest.mark.anyio
    async def test_create_permission_with_guest_role(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        config = PermissionConfig(Role.GUEST, Action.CREATE, Resource.PROJECT)
        checker.register_config(config)

        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.CREATE,
            resource=Resource.PROJECT,
        )
        assert allowed is True


class TestOwnerOnlyPermission:
    @pytest.fixture
    def checker(self) -> PermissionChecker:
        return PermissionChecker()

    def test_owner_only_rule_allows_owner(self) -> None:
        rule = PermissionRule.owner_only("owner_id")
        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})
        result = rule.check(user, Action.UPDATE, Resource.TASK, 1, {"owner_id": 123})
        assert result is True

    def test_owner_only_rule_denies_non_owner(self) -> None:
        rule = PermissionRule.owner_only("owner_id")
        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})
        result = rule.check(user, Action.UPDATE, Resource.TASK, 1, {"owner_id": 456})
        assert result is False

    @pytest.mark.anyio
    async def test_owner_can_edit_own_resource(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        rule = PermissionRule.owner_only("owner_id")
        config = PermissionConfig(Role.MEMBER, Action.UPDATE, Resource.TASK, rule=rule)
        checker.register_config(config)

        async def task_role_provider(db, user_id, domain, resource_id):
            return {Role.MEMBER}

        checker.register_role_provider("task", task_role_provider)

        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.UPDATE,
            resource=Resource.TASK,
            resource_id=1,
            context={"owner_id": 123},
        )
        assert allowed is True

    @pytest.mark.anyio
    async def test_member_cannot_edit_others_resource(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        rule = PermissionRule.owner_only("owner_id")
        config = PermissionConfig(Role.MEMBER, Action.UPDATE, Resource.TASK, rule=rule)
        checker.register_config(config)

        async def task_role_provider(db, user_id, domain, resource_id):
            return {Role.MEMBER}

        checker.register_role_provider("task", task_role_provider)

        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.UPDATE,
            resource=Resource.TASK,
            resource_id=1,
            context={"owner_id": 456},
        )
        assert allowed is False


class TestCombinedConditions:
    def test_all_conditions_must_match(self) -> None:
        def is_owner(user, action, resource, resource_id, context):
            return context.get("is_owner") is True

        def is_draft(user, action, resource, resource_id, context):
            return context.get("is_draft") is True

        rule = PermissionRule(conditions=[is_owner, is_draft])
        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})

        assert (
            rule.check(user, Action.UPDATE, Resource.TASK, 1, {"is_owner": True, "is_draft": True})
            is True
        )
        assert (
            rule.check(user, Action.UPDATE, Resource.TASK, 1, {"is_owner": True, "is_draft": False})
            is False
        )
        assert (
            rule.check(user, Action.UPDATE, Resource.TASK, 1, {"is_owner": False, "is_draft": True})
            is False
        )

    def test_or_conditions_any_can_match(self) -> None:
        def is_owner(user, action, resource, resource_id, context):
            return context.get("is_owner") is True

        def is_admin(user, action, resource, resource_id, context):
            return context.get("is_admin") is True

        def is_super_user(user, action, resource, resource_id, context):
            return context.get("is_super_user") is True

        rule = PermissionRule(or_conditions=[[is_owner, is_admin, is_super_user]])
        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})

        assert rule.check(user, Action.UPDATE, Resource.TASK, 1, {"is_owner": True}) is True
        assert rule.check(user, Action.UPDATE, Resource.TASK, 1, {"is_admin": True}) is True
        assert rule.check(user, Action.UPDATE, Resource.TASK, 1, {"is_super_user": True}) is True
        assert rule.check(user, Action.UPDATE, Resource.TASK, 1, {}) is False


class TestMultipleRoles:
    @pytest.fixture
    def checker(self) -> PermissionChecker:
        return PermissionChecker()

    @pytest.mark.anyio
    async def test_user_with_multiple_domain_roles(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        config = PermissionConfig(Role.ADMIN, Action.UPDATE, Resource.TEAM)
        checker.register_config(config)

        async def role_provider(db, user_id, domain, resource_id):
            return {Role.MEMBER, Role.ADMIN}

        checker.register_role_provider("team", role_provider)

        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})
        allowed = await checker.check_permission(
            db=db,
            user=user,
            action=Action.UPDATE,
            resource=Resource.TEAM,
            resource_id=1,
        )
        assert allowed is True

    @pytest.mark.anyio
    async def test_highest_role_takes_effect(self, checker: PermissionChecker) -> None:
        db = AsyncMock()
        delete_config = PermissionConfig(Role.OWNER, Action.DELETE, Resource.TEAM)
        update_config = PermissionConfig(Role.ADMIN, Action.UPDATE, Resource.TEAM)
        checker.register_configs([delete_config, update_config])

        async def role_provider(db, user_id, domain, resource_id):
            return {Role.OWNER}

        checker.register_role_provider("team", role_provider)

        user = AuthUserInfo(user_id=123, system_roles={SystemRole.USER})

        can_delete = await checker.check_permission(
            db=db,
            user=user,
            action=Action.DELETE,
            resource=Resource.TEAM,
            resource_id=1,
        )
        can_update = await checker.check_permission(
            db=db,
            user=user,
            action=Action.UPDATE,
            resource=Resource.TEAM,
            resource_id=1,
        )
        assert can_delete is True
        assert can_update is True
