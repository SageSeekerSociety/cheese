"""Unit tests for app.domain.team.recruitment_repositories.RecruitmentRepository."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.team.recruitment_repositories import RecruitmentRepository, _use_fts

NOW = datetime(2025, 6, 1, 12, 0, 0)


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
        "created_at": NOW,
        "updated_at": NOW,
        "expires_at": None,
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


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUseFts:
    def test_short_returns_false(self):
        assert _use_fts("ab") is False

    def test_normal_returns_true(self):
        assert _use_fts("hello") is True


class TestRecruitmentRepository:
    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = RecruitmentRepository(session)

        result = await repo.create(
            team_id=10,
            title="Looking for members",
            content="We need help",
            contact="alice@example.com",
            max_members=5,
            created_by=20,
            expires_at=None,
        )
        assert result.team_id == 10
        assert result.title == "Looking for members"
        assert result.status == "OPEN"
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        p = _post()
        session.execute.return_value = _mock_scalar(p)
        repo = RecruitmentRepository(session)

        result = await repo.get_by_id(1)
        assert result is p

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = RecruitmentRepository(session)

        assert await repo.get_by_id(999) is None

    @pytest.mark.anyio
    async def test_list_open_no_results(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = RecruitmentRepository(session)

        rows, has_more, next_id = await repo.list_open(page_size=10)
        assert rows == []
        assert has_more is False
        assert next_id is None

    @pytest.mark.anyio
    async def test_list_open_with_results(self):
        session = _mock_session()
        p = _post(id=5)
        session.execute.return_value = _mock_scalars([p])
        repo = RecruitmentRepository(session)

        rows, has_more, next_id = await repo.list_open(page_size=10)
        assert rows == [p]
        assert has_more is False

    @pytest.mark.anyio
    async def test_list_open_has_more(self):
        session = _mock_session()
        posts = [_post(id=10 - i) for i in range(3)]
        session.execute.return_value = _mock_scalars(posts)
        repo = RecruitmentRepository(session)

        rows, has_more, next_id = await repo.list_open(page_size=2)
        assert len(rows) == 2
        assert has_more is True
        assert next_id == rows[-1].id - 1

    @pytest.mark.anyio
    async def test_list_open_with_keyword(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = RecruitmentRepository(session)

        rows, has_more, _ = await repo.list_open(keyword="developer")
        assert rows == []

    @pytest.mark.anyio
    async def test_list_open_with_page_start(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = RecruitmentRepository(session)

        rows, has_more, _ = await repo.list_open(page_start=50)
        assert rows == []

    @pytest.mark.anyio
    async def test_list_by_team(self):
        session = _mock_session()
        p = _post()
        session.execute.return_value = _mock_scalars([p])
        repo = RecruitmentRepository(session)

        result = await repo.list_by_team(10)
        assert result == [p]

    @pytest.mark.anyio
    async def test_update_all_fields(self):
        session = _mock_session()
        p = _post()
        repo = RecruitmentRepository(session)

        result = await repo.update(
            p,
            title="New Title",
            content="New Content",
            contact="bob@example.com",
            max_members=10,
            status="CLOSED",
            expires_at=NOW,
        )
        assert result.title == "New Title"
        assert result.content == "New Content"
        assert result.contact == "bob@example.com"
        assert result.max_members == 10
        assert result.status == "CLOSED"
        assert result.expires_at == NOW

    @pytest.mark.anyio
    async def test_update_partial(self):
        session = _mock_session()
        p = _post()
        repo = RecruitmentRepository(session)

        result = await repo.update(p, title="Updated Title")
        assert result.title == "Updated Title"
        assert result.content == "We need help"  # unchanged

    @pytest.mark.anyio
    async def test_update_set_none_sentinel_fields(self):
        session = _mock_session()
        p = _post(contact="old@example.com", max_members=5)
        repo = RecruitmentRepository(session)

        result = await repo.update(p, contact=None, max_members=None, expires_at=None)
        assert result.contact is None
        assert result.max_members is None
        assert result.expires_at is None

    @pytest.mark.anyio
    async def test_soft_delete(self):
        session = _mock_session()
        p = _post()
        repo = RecruitmentRepository(session)

        await repo.soft_delete(p)
        assert p.deleted_at is not None
