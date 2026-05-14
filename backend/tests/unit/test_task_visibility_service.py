"""Unit tests for TaskVisibilityService — covering visibility rules and predicate building."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.task.visibility_service import TaskVisibilityService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(**overrides):
    defaults = {
        "id": 1,
        "creator_id": 10,
        "space_id": 100,
        "access_control_enabled": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _user(email="user@cs.edu.cn", email_domain="cs.edu.cn"):
    m = SimpleNamespace()
    m.id = 42
    m.email = email
    m.email_domain = email_domain
    return m


def _mock_session(scalar_returns=None):
    """Build a mock AsyncSession for visibility service.
    scalar_returns maps query 'kind' → return value for scalar_one_or_none."""
    session = AsyncMock()
    session._scalar_returns = scalar_returns or {}

    def _execute_side_effect(stmt):
        # Very simple routing: look at the statement string to decide what to return.
        stmt_str = str(stmt)
        for kind, val in session._scalar_returns.items():
            if kind in stmt_str:
                m = MagicMock()
                m.scalar_one_or_none.return_value = val
                return m
        # Default: no result
        m = MagicMock()
        m.scalar_one_or_none.return_value = None
        return m

    session.execute = AsyncMock(side_effect=_execute_side_effect)
    return session


def _svc(session=None, user_repo=None, admin_repo=None):
    session = session or _mock_session()
    svc = TaskVisibilityService(session=session)
    if user_repo:
        svc._user_repo = user_repo
    if admin_repo:
        svc._admin_repo = admin_repo
    return svc


# ---------------------------------------------------------------------------
# can_view_task
# ---------------------------------------------------------------------------


class TestCanViewTask:
    @pytest.mark.anyio
    async def test_creator_always_can_view(self):
        t = _task(creator_id=10, access_control_enabled=True)
        svc = _svc()
        assert await svc.can_view_task(task=t, user_id=10) is True

    @pytest.mark.anyio
    async def test_space_admin_can_view(self):
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = SimpleNamespace()
        svc = _svc(admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_participant_can_view(self):
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        session = _mock_session({"task_membership": 1})  # has membership row
        svc = _svc(session=session, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_access_control_disabled_everyone_can_view(self):
        t = _task(creator_id=99, access_control_enabled=False)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        svc = _svc(admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_unauthenticated_user_sees_only_public(self):
        t = _task(creator_id=99, access_control_enabled=False)
        svc = _svc()
        # user_id <= 0 → can only see if access control is disabled
        assert await svc.can_view_task(task=t, user_id=0) is True
        assert await svc.can_view_task(task=t, user_id=-1) is True

    @pytest.mark.anyio
    async def test_unauthenticated_user_hidden_when_restricted(self):
        t = _task(creator_id=99, access_control_enabled=True)
        svc = _svc()
        assert await svc.can_view_task(task=t, user_id=0) is False

    @pytest.mark.anyio
    async def test_allowed_domain_can_view(self):
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="cs.edu.cn")
        session = _mock_session({"task_access_domain": 1})  # domain match
        svc = _svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True

    @pytest.mark.anyio
    async def test_denied_domain_cannot_view(self):
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email_domain="other.edu.cn")
        session = _mock_session({"task_access_domain": None})  # no match
        svc = _svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False

    @pytest.mark.anyio
    async def test_user_with_no_email_cannot_view_restricted(self):
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email="", email_domain="")
        svc = _svc(user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False

    @pytest.mark.anyio
    async def test_user_not_found_cannot_view_restricted(self):
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = None
        svc = _svc(user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is False

    @pytest.mark.anyio
    async def test_falls_back_to_email_split_when_no_cache(self):
        """When email_domain is None but email has '@', extract from email."""
        t = _task(creator_id=99, access_control_enabled=True)
        admin_repo = AsyncMock()
        admin_repo.get_relation.return_value = None
        user_repo = AsyncMock()
        user_repo.get_by_id.return_value = _user(email="student@cs.edu.cn", email_domain=None)
        session = _mock_session({"task_access_domain": 1})  # cs.edu.cn matches
        svc = _svc(session=session, user_repo=user_repo, admin_repo=admin_repo)
        assert await svc.can_view_task(task=t, user_id=42) is True
