"""Unit tests for ProjectAuthorizer (the orchestrator->business authz gate).

Uses fakes for the RBAC checker, grant service and project repo so the
composition logic — agent gate, direct/on-behalf permission, and live-delegated
grants — is tested in isolation.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.authorization.authorizer import ProjectActor, ProjectAuthorizer
from app.auth.core import Action, Resource
from app.core.errors import PermissionDeniedError
from app.domain.project.models import ProjectAiMode

pytestmark = pytest.mark.anyio


class _FakeChecker:
    """Allows exactly the given user ids to do anything."""

    def __init__(self, allowed_uids: set[int]) -> None:
        self._allowed = allowed_uids

    async def check_permission(self, db, info, action, resource, resource_id=None, context=None):
        return info.user_id in self._allowed


def _authorizer(
    *, allowed_uids: set[int], granters: list[int], ai_mode: int = ProjectAiMode.ASSISTED.value
) -> ProjectAuthorizer:
    grants = SimpleNamespace(granters_covering=AsyncMock(return_value=granters))
    projects = SimpleNamespace(
        get_by_id=AsyncMock(return_value=SimpleNamespace(id=1, ai_mode=ai_mode))
    )
    return ProjectAuthorizer(grants, projects, _FakeChecker(allowed_uids))  # type: ignore[arg-type]


_DB = object()


async def test_user_with_direct_permission_allowed():
    az = _authorizer(allowed_uids={7}, granters=[])
    actor = ProjectActor(kind="user", actor_id=7, project_id=1)
    assert await az.is_allowed(_DB, actor, Action.UPDATE, Resource.TASK, 42) is True


async def test_user_without_permission_denied():
    az = _authorizer(allowed_uids=set(), granters=[])
    actor = ProjectActor(kind="user", actor_id=7, project_id=1)
    assert await az.is_allowed(_DB, actor, Action.UPDATE, Resource.TASK, 42) is False


async def test_agent_blocked_when_ai_off():
    # on-behalf user 5 has the permission, but AI is OFF -> denied.
    az = _authorizer(allowed_uids={5}, granters=[], ai_mode=ProjectAiMode.OFF.value)
    actor = ProjectActor(kind="agent", actor_id=1, project_id=1, on_behalf_of_user_id=5)
    assert await az.is_allowed(_DB, actor, Action.READ, Resource.TASK, 42) is False


async def test_agent_on_behalf_allowed():
    az = _authorizer(allowed_uids={5}, granters=[])
    actor = ProjectActor(kind="agent", actor_id=1, project_id=1, on_behalf_of_user_id=5)
    assert await az.is_allowed(_DB, actor, Action.READ, Resource.TASK, 42) is True


async def test_agent_inherits_delegated_grant():
    # No on-behalf human, but holder 9 shared a covering capability AND still
    # holds it -> the agent inherits it.
    az = _authorizer(allowed_uids={9}, granters=[9])
    actor = ProjectActor(kind="agent", actor_id=1, project_id=1)
    assert await az.is_allowed(_DB, actor, Action.READ, Resource.KNOWLEDGE, 3) is True


async def test_delegation_is_live_granter_lost_permission():
    # Holder 9 shared it but no longer holds it (checker denies 9) -> denied.
    az = _authorizer(allowed_uids=set(), granters=[9])
    actor = ProjectActor(kind="agent", actor_id=1, project_id=1)
    assert await az.is_allowed(_DB, actor, Action.READ, Resource.KNOWLEDGE, 3) is False


async def test_authorize_raises_when_denied():
    az = _authorizer(allowed_uids=set(), granters=[])
    actor = ProjectActor(kind="user", actor_id=7, project_id=1)
    with pytest.raises(PermissionDeniedError):
        await az.authorize(_DB, actor, Action.UPDATE, Resource.TASK, 42)
