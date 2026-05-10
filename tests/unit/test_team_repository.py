"""Unit tests for app.domain.team.repositories.

Covers TeamRepository and TeamMembershipApplicationRepository.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.errors import ConflictError
from app.domain.team.models import ApplicationStatus, ApplicationType, TeamMemberRole
from app.domain.team.repositories import (
    TeamMembershipApplicationRepository,
    TeamRepository,
    _use_fts,
)

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _team(**overrides):
    defaults = {
        "id": 1,
        "name": "Team Alpha",
        "avatar_id": None,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _relation(**overrides):
    defaults = {
        "id": 1,
        "team_id": 1,
        "user_id": 10,
        "role": TeamMemberRole.MEMBER,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _application(**overrides):
    defaults = {
        "id": 1,
        "team_id": 1,
        "user_id": 10,
        "initiator_id": 10,
        "type": ApplicationType.REQUEST.value,
        "status": ApplicationStatus.PENDING.value,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


def _mock_scalar(val):
    m = MagicMock()
    m.scalar_one_or_none.return_value = val
    return m


def _mock_scalar_one(val):
    m = MagicMock()
    m.scalar_one.return_value = val
    return m


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


# ---------------------------------------------------------------------------
# _use_fts
# ---------------------------------------------------------------------------


class TestUseFts:
    def test_short_returns_false(self):
        assert _use_fts("ab") is False

    def test_emoji_returns_false(self):
        assert _use_fts("🎉🎉🎉") is False

    def test_normal_returns_true(self):
        assert _use_fts("hello") is True


# ---------------------------------------------------------------------------
# TeamRepository
# ---------------------------------------------------------------------------


class TestTeamRepository:
    @pytest.mark.anyio
    async def test_exists_by_name_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(1)
        repo = TeamRepository(session)

        assert await repo.exists_by_name("Team Alpha") is True

    @pytest.mark.anyio
    async def test_exists_by_name_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(0)
        repo = TeamRepository(session)

        assert await repo.exists_by_name("Nobody") is False

    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        t = _team()
        session.execute.return_value = _mock_scalar(t)
        repo = TeamRepository(session)

        result = await repo.get_by_id(1)
        assert result is t

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = TeamRepository(session)

        assert await repo.get_by_id(999) is None

    @pytest.mark.anyio
    async def test_list_teams_no_query(self):
        session = _mock_session()
        t = _team()
        session.execute.return_value = _mock_scalars([t])
        repo = TeamRepository(session)

        result = await repo.list_teams(query=None, limit=10, offset=0)
        assert list(result) == [t]

    @pytest.mark.anyio
    async def test_list_teams_with_name_query(self):
        session = _mock_session()
        t = _team()
        session.execute.return_value = _mock_scalars([t])
        repo = TeamRepository(session)

        result = await repo.list_teams(query="Alpha", limit=10, offset=0)
        assert list(result) == [t]

    @pytest.mark.anyio
    async def test_list_teams_with_numeric_query(self):
        session = _mock_session()
        t = _team()
        session.execute.return_value = _mock_scalars([t])
        repo = TeamRepository(session)

        result = await repo.list_teams(query="1", limit=10, offset=0)
        assert list(result) == [t]

    @pytest.mark.anyio
    async def test_get_by_ids_empty(self):
        session = _mock_session()
        repo = TeamRepository(session)

        result = await repo.get_by_ids([])
        assert result == {}

    @pytest.mark.anyio
    async def test_get_by_ids(self):
        session = _mock_session()
        t1 = _team(id=1)
        t2 = _team(id=2, name="Team Beta")
        session.execute.return_value = _mock_scalars([t1, t2])
        repo = TeamRepository(session)

        result = await repo.get_by_ids([1, 2])
        assert result == {1: t1, 2: t2}

    @pytest.mark.anyio
    async def test_list_teams_of_user_no_teams(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TeamRepository(session)

        result = await repo.list_teams_of_user(10)
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_teams_of_user_with_teams(self):
        session = _mock_session()
        rel = _relation(team_id=1)
        t = _team(id=1)
        session.execute.side_effect = [
            _mock_scalars([rel]),  # relations
            _mock_scalars([t]),  # teams
        ]
        repo = TeamRepository(session)

        result = await repo.list_teams_of_user(10)
        assert list(result) == [t]

    @pytest.mark.anyio
    async def test_list_teams_user_can_use_to_join_task_empty(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TeamRepository(session)

        result = await repo.list_teams_user_can_use_to_join_task(10)
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_teams_user_can_use_to_join_task(self):
        session = _mock_session()
        rel = _relation(team_id=1, role=TeamMemberRole.OWNER)
        t = _team(id=1)
        session.execute.side_effect = [
            _mock_scalars([rel]),
            _mock_scalars([t]),
        ]
        repo = TeamRepository(session)

        result = await repo.list_teams_user_can_use_to_join_task(10)
        assert list(result) == [t]

    @pytest.mark.anyio
    async def test_list_members_of_team(self):
        session = _mock_session()
        rel = _relation()
        session.execute.return_value = _mock_scalars([rel])
        repo = TeamRepository(session)

        result = await repo.list_members_of_team(1)
        assert list(result) == [rel]

    @pytest.mark.anyio
    async def test_get_member_relation_found(self):
        session = _mock_session()
        rel = _relation()
        session.execute.return_value = _mock_scalar(rel)
        repo = TeamRepository(session)

        result = await repo.get_member_relation(1, 10)
        assert result is rel

    @pytest.mark.anyio
    async def test_is_team_member_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = TeamRepository(session)

        assert await repo.is_team_member(1, 10) is True

    @pytest.mark.anyio
    async def test_is_team_member_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = TeamRepository(session)

        assert await repo.is_team_member(1, 999) is False

    @pytest.mark.anyio
    async def test_list_admin_and_owner_ids(self):
        session = _mock_session()
        r1 = _relation(user_id=10, role=TeamMemberRole.OWNER)
        r2 = _relation(user_id=20, role=TeamMemberRole.ADMIN)
        session.execute.return_value = _mock_scalars([r1, r2])
        repo = TeamRepository(session)

        result = await repo.list_admin_and_owner_ids(1)
        assert result == {10, 20}

    @pytest.mark.anyio
    async def test_is_team_at_least_admin_true(self):
        session = _mock_session()
        rel = _relation(role=TeamMemberRole.ADMIN)
        session.execute.return_value = _mock_scalar(rel)
        repo = TeamRepository(session)

        assert await repo.is_team_at_least_admin(1, 10) is True

    @pytest.mark.anyio
    async def test_is_team_at_least_admin_member_role(self):
        session = _mock_session()
        rel = _relation(role=TeamMemberRole.MEMBER)
        session.execute.return_value = _mock_scalar(rel)
        repo = TeamRepository(session)

        assert await repo.is_team_at_least_admin(1, 10) is False

    @pytest.mark.anyio
    async def test_is_team_at_least_admin_not_member(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = TeamRepository(session)

        assert await repo.is_team_at_least_admin(1, 999) is False

    @pytest.mark.anyio
    async def test_add_member_success(self):
        session = _mock_session()
        # get_member_relation returns None
        session.execute.return_value = _mock_scalar(None)
        repo = TeamRepository(session)

        rel = await repo.add_member(1, 10, TeamMemberRole.MEMBER)
        assert rel.team_id == 1
        assert rel.user_id == 10
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_add_member_conflict(self):
        session = _mock_session()
        existing = _relation()
        session.execute.return_value = _mock_scalar(existing)
        repo = TeamRepository(session)

        with pytest.raises(ConflictError, match="already a member"):
            await repo.add_member(1, 10, TeamMemberRole.MEMBER)

    @pytest.mark.anyio
    async def test_soft_delete_member(self):
        session = _mock_session()
        rel = _relation()
        repo = TeamRepository(session)

        await repo.soft_delete_member(rel)
        assert rel.deleted_at is not None
        assert rel.updated_at == rel.deleted_at

    @pytest.mark.anyio
    async def test_soft_delete_team(self):
        session = _mock_session()
        t = _team()
        repo = TeamRepository(session)

        await repo.soft_delete_team(t)
        assert t.deleted_at is not None
        assert t.updated_at == t.deleted_at


# ---------------------------------------------------------------------------
# TeamMembershipApplicationRepository
# ---------------------------------------------------------------------------


class TestTeamMembershipApplicationRepository:
    @pytest.mark.anyio
    async def test_save(self):
        session = _mock_session()
        app = _application()
        repo = TeamMembershipApplicationRepository(session)

        result = await repo.save(app)
        assert result is app
        session.add.assert_called_once_with(app)

    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        app = _application()
        session.execute.return_value = _mock_scalar(app)
        repo = TeamMembershipApplicationRepository(session)

        result = await repo.get_by_id(1)
        assert result is app

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = TeamMembershipApplicationRepository(session)

        assert await repo.get_by_id(999) is None

    @pytest.mark.anyio
    async def test_exists_pending_for_user_and_team_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = TeamMembershipApplicationRepository(session)

        assert await repo.exists_pending_for_user_and_team(10, 1) is True

    @pytest.mark.anyio
    async def test_exists_pending_for_user_and_team_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = TeamMembershipApplicationRepository(session)

        assert await repo.exists_pending_for_user_and_team(10, 1) is False

    @pytest.mark.anyio
    async def test_find_pending_by_id_and_initiator_and_type(self):
        session = _mock_session()
        app = _application()
        session.execute.return_value = _mock_scalar(app)
        repo = TeamMembershipApplicationRepository(session)

        result = await repo.find_pending_by_id_and_initiator_and_type(
            application_id=1, initiator_id=10, type_=ApplicationType.REQUEST
        )
        assert result is app

    @pytest.mark.anyio
    async def test_find_pending_by_id_and_user_and_type(self):
        session = _mock_session()
        app = _application()
        session.execute.return_value = _mock_scalar(app)
        repo = TeamMembershipApplicationRepository(session)

        result = await repo.find_pending_by_id_and_user_and_type(
            application_id=1, user_id=10, type_=ApplicationType.REQUEST
        )
        assert result is app

    @pytest.mark.anyio
    async def test_find_pending_by_id_and_team_and_type(self):
        session = _mock_session()
        app = _application()
        session.execute.return_value = _mock_scalar(app)
        repo = TeamMembershipApplicationRepository(session)

        result = await repo.find_pending_by_id_and_team_and_type(
            application_id=1, team_id=1, type_=ApplicationType.REQUEST
        )
        assert result is app

    @pytest.mark.anyio
    async def test_list_for_user(self):
        session = _mock_session()
        app = _application()
        session.execute.side_effect = [
            _mock_scalars([app]),
            _mock_scalar_one(1),
        ]
        repo = TeamMembershipApplicationRepository(session)

        rows, total = await repo.list_for_user(
            user_id=10, type_=ApplicationType.REQUEST, status=None, limit=10
        )
        assert rows == [app]
        assert total == 1

    @pytest.mark.anyio
    async def test_list_for_user_with_status_filter(self):
        session = _mock_session()
        session.execute.side_effect = [
            _mock_scalars([]),
            _mock_scalar_one(0),
        ]
        repo = TeamMembershipApplicationRepository(session)

        rows, total = await repo.list_for_user(
            user_id=10,
            type_=ApplicationType.REQUEST,
            status=ApplicationStatus.APPROVED,
            limit=10,
        )
        assert rows == []
        assert total == 0

    @pytest.mark.anyio
    async def test_list_for_team(self):
        session = _mock_session()
        app = _application()
        session.execute.side_effect = [
            _mock_scalars([app]),
            _mock_scalar_one(1),
        ]
        repo = TeamMembershipApplicationRepository(session)

        rows, total = await repo.list_for_team(
            team_id=1, type_=ApplicationType.REQUEST, status=None, limit=10
        )
        assert rows == [app]
        assert total == 1

    @pytest.mark.anyio
    async def test_list_for_team_with_status_filter(self):
        session = _mock_session()
        session.execute.side_effect = [
            _mock_scalars([]),
            _mock_scalar_one(0),
        ]
        repo = TeamMembershipApplicationRepository(session)

        rows, total = await repo.list_for_team(
            team_id=1,
            type_=ApplicationType.INVITATION,
            status=ApplicationStatus.PENDING,
            limit=10,
        )
        assert rows == []
        assert total == 0
