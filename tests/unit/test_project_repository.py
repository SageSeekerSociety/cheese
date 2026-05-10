"""Unit tests for app.domain.project.repositories.

Covers ProjectRepository, ProjectMembershipRepository.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.project.models import ProjectMemberRole
from app.domain.project.repositories import (
    ProjectMembershipRepository,
    ProjectRepository,
)

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project(**overrides):
    defaults = {
        "id": 1,
        "name": "Project X",
        "description": "Desc",
        "color_code": "#ff0000",
        "content": "",
        "team_id": 10,
        "leader_id": 20,
        "parent_id": None,
        "external_task_id": None,
        "github_repo": None,
        "start_date": NOW,
        "end_date": NOW,
        "archived": False,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _membership(**overrides):
    defaults = {
        "id": 1,
        "project_id": 1,
        "user_id": 20,
        "role": ProjectMemberRole.MEMBER.value,
        "notes": "",
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
# ProjectRepository
# ---------------------------------------------------------------------------


class TestProjectRepository:
    @pytest.mark.anyio
    async def test_create_project(self):
        session = _mock_session()
        repo = ProjectRepository(session)

        result = await repo.create_project(
            name="New Project",
            description="Desc",
            color_code="#00ff00",
            team_id=10,
            leader_id=20,
            start_date=NOW,
            end_date=NOW,
        )
        assert result.name == "New Project"
        assert result.archived is False
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_create_project_with_optional_fields(self):
        session = _mock_session()
        repo = ProjectRepository(session)

        result = await repo.create_project(
            name="Project",
            description="Desc",
            color_code="#0000ff",
            team_id=10,
            leader_id=20,
            start_date=NOW,
            end_date=NOW,
            content="Content",
            parent_id=5,
            external_task_id=99,
            github_repo="https://github.com/test",
        )
        assert result.content == "Content"
        assert result.parent_id == 5
        assert result.external_task_id == 99

    @pytest.mark.anyio
    async def test_get_by_ids_empty(self):
        session = _mock_session()
        repo = ProjectRepository(session)

        result = await repo.get_by_ids([])
        assert result == {}

    @pytest.mark.anyio
    async def test_get_by_ids(self):
        session = _mock_session()
        p1 = _project(id=1)
        p2 = _project(id=2, name="Project Y")
        session.execute.return_value = _mock_scalars([p1, p2])
        repo = ProjectRepository(session)

        result = await repo.get_by_ids([1, 2])
        assert result == {1: p1, 2: p2}

    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        p = _project()
        session.execute.return_value = _mock_scalar(p)
        repo = ProjectRepository(session)

        result = await repo.get_by_id(1)
        assert result is p

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = ProjectRepository(session)

        assert await repo.get_by_id(999) is None

    @pytest.mark.anyio
    async def test_save(self):
        session = _mock_session()
        p = _project()
        repo = ProjectRepository(session)

        result = await repo.save(p)
        assert result is p
        assert result.updated_at is not None

    @pytest.mark.anyio
    async def test_soft_delete(self):
        session = _mock_session()
        p = _project()
        repo = ProjectRepository(session)

        await repo.soft_delete(p)
        assert p.deleted_at is not None

    @pytest.mark.anyio
    async def test_list_projects_basic(self):
        session = _mock_session()
        p = _project()
        session.execute.return_value = _mock_scalars([p])
        repo = ProjectRepository(session)

        result = await repo.list_projects(team_id=10)
        assert list(result) == [p]

    @pytest.mark.anyio
    async def test_list_projects_with_all_filters(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = ProjectRepository(session)

        result = await repo.list_projects(
            team_id=10,
            parent_id=5,
            leader_id=20,
            member_id=30,
            archived=False,
        )
        assert list(result) == []


# ---------------------------------------------------------------------------
# ProjectMembershipRepository
# ---------------------------------------------------------------------------


class TestProjectMembershipRepository:
    @pytest.mark.anyio
    async def test_add_member(self):
        session = _mock_session()
        repo = ProjectMembershipRepository(session)

        result = await repo.add_member(project_id=1, user_id=20, role=ProjectMemberRole.MEMBER)
        assert result.project_id == 1
        assert result.user_id == 20
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_get_relation(self):
        session = _mock_session()
        m = _membership()
        session.execute.return_value = _mock_scalar(m)
        repo = ProjectMembershipRepository(session)

        result = await repo.get_relation(1, 20)
        assert result is m

    @pytest.mark.anyio
    async def test_list_members(self):
        session = _mock_session()
        m = _membership()
        session.execute.side_effect = [
            _mock_scalar_one(1),
            _mock_scalars([m]),
        ]
        repo = ProjectMembershipRepository(session)

        rows, total = await repo.list_members(1, limit=10, offset=0)
        assert rows == [m]
        assert total == 1

    @pytest.mark.anyio
    async def test_remove_member(self):
        session = _mock_session()
        m = _membership()
        repo = ProjectMembershipRepository(session)

        await repo.remove_member(m)
        assert m.deleted_at is not None
        assert m.updated_at is not None
