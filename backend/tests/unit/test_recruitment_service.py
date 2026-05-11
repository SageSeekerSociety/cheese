"""Unit tests for app.domain.team.recruitment_services.RecruitmentService."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.team.recruitment_services import RecruitmentService

NOW_DUMMY = "2025-06-01T12:00:00"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post(**overrides):
    defaults = {
        "id": 1,
        "team_id": 10,
        "title": "Looking for members",
        "content": "We need help",
        "contact": "alice@example.com",
        "max_members": 5,
        "status": "OPEN",
        "created_by": 20,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _team(**overrides):
    defaults = {"id": 10, "name": "Alpha"}
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_service(repo=None, team_repo=None):
    return RecruitmentService(
        repo=repo or AsyncMock(),
        team_repo=team_repo or AsyncMock(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreatePost:
    @pytest.mark.anyio
    async def test_success(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        team_repo.get_by_id.return_value = _team()
        team_repo.is_team_at_least_admin.return_value = True
        post = _post()
        repo.create.return_value = post
        svc = _make_service(repo=repo, team_repo=team_repo)

        result = await svc.create_post(
            team_id=10, actor_user_id=20, title="Post", content="Content"
        )
        assert result is post

    @pytest.mark.anyio
    async def test_team_not_found(self):
        team_repo = AsyncMock()
        team_repo.get_by_id.return_value = None
        svc = _make_service(team_repo=team_repo)

        with pytest.raises(NotFoundError):
            await svc.create_post(team_id=999, actor_user_id=20, title="Post", content="Content")

    @pytest.mark.anyio
    async def test_not_admin(self):
        team_repo = AsyncMock()
        team_repo.get_by_id.return_value = _team()
        team_repo.is_team_at_least_admin.return_value = False
        svc = _make_service(team_repo=team_repo)

        with pytest.raises(ForbiddenError):
            await svc.create_post(team_id=10, actor_user_id=99, title="Post", content="Content")


class TestListOpen:
    @pytest.mark.anyio
    async def test_delegates_to_repo(self):
        repo = AsyncMock()
        repo.list_open.return_value = ([], False, None)
        svc = _make_service(repo=repo)

        rows, has_more, next_id = await svc.list_open()
        assert rows == []
        repo.list_open.assert_awaited_once()


class TestListByTeam:
    @pytest.mark.anyio
    async def test_delegates_to_repo(self):
        repo = AsyncMock()
        repo.list_by_team.return_value = []
        svc = _make_service(repo=repo)

        result = await svc.list_by_team(10)
        assert result == []


class TestGetById:
    @pytest.mark.anyio
    async def test_delegates_to_repo(self):
        repo = AsyncMock()
        post = _post()
        repo.get_by_id.return_value = post
        svc = _make_service(repo=repo)

        result = await svc.get_by_id(1)
        assert result is post


class TestUpdatePost:
    @pytest.mark.anyio
    async def test_success(self):
        repo = AsyncMock()
        post = _post(created_by=20)
        repo.get_by_id.return_value = post
        repo.update.return_value = post
        svc = _make_service(repo=repo)

        result = await svc.update_post(post_id=1, actor_user_id=20, title="New")
        assert result is post

    @pytest.mark.anyio
    async def test_not_found(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(NotFoundError):
            await svc.update_post(post_id=999, actor_user_id=20, title="New")

    @pytest.mark.anyio
    async def test_not_creator(self):
        repo = AsyncMock()
        post = _post(created_by=20)
        repo.get_by_id.return_value = post
        svc = _make_service(repo=repo)

        with pytest.raises(ForbiddenError):
            await svc.update_post(post_id=1, actor_user_id=99, title="New")


class TestDeletePost:
    @pytest.mark.anyio
    async def test_success(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        post = _post(team_id=10)
        repo.get_by_id.return_value = post
        team_repo.is_team_at_least_admin.return_value = True
        svc = _make_service(repo=repo, team_repo=team_repo)

        await svc.delete_post(post_id=1, actor_user_id=20)
        repo.soft_delete.assert_awaited_once_with(post)

    @pytest.mark.anyio
    async def test_not_found(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(NotFoundError):
            await svc.delete_post(post_id=999, actor_user_id=20)

    @pytest.mark.anyio
    async def test_not_admin(self):
        repo = AsyncMock()
        team_repo = AsyncMock()
        post = _post(team_id=10)
        repo.get_by_id.return_value = post
        team_repo.is_team_at_least_admin.return_value = False
        svc = _make_service(repo=repo, team_repo=team_repo)

        with pytest.raises(ForbiddenError):
            await svc.delete_post(post_id=1, actor_user_id=99)
