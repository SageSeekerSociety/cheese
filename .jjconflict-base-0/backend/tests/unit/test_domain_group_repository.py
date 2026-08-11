"""Unit tests for SpaceDomainGroupRepository and SpaceDomainGroupDomainRepository."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.space.repositories import (
    SpaceDomainGroupDomainRepository,
    SpaceDomainGroupRepository,
)

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _domain_group(**overrides):
    defaults = {
        "id": 1,
        "space_id": 100,
        "name": "计算机学院",
        "description": "计算机相关专业",
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
# SpaceDomainGroupRepository
# ---------------------------------------------------------------------------


class TestSpaceDomainGroupRepository:
    @pytest.mark.anyio
    async def test_list_groups_empty(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = SpaceDomainGroupRepository(session)
        result = await repo.list_groups(space_id=100)
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_groups_returns_groups(self):
        session = _mock_session()
        g1, g2 = _domain_group(id=1, name="A"), _domain_group(id=2, name="B")
        session.execute.return_value = _mock_scalars([g1, g2])
        repo = SpaceDomainGroupRepository(session)
        result = await repo.list_groups(space_id=100)
        assert list(result) == [g1, g2]

    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        g = _domain_group()
        session.execute.return_value = _mock_scalar(g)
        repo = SpaceDomainGroupRepository(session)
        result = await repo.get_by_id(space_id=100, group_id=1)
        assert result is g

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceDomainGroupRepository(session)
        result = await repo.get_by_id(space_id=100, group_id=999)
        assert result is None

    @pytest.mark.anyio
    async def test_exists_name_true(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(1)
        repo = SpaceDomainGroupRepository(session)
        result = await repo.exists_name(space_id=100, name="计算机学院")
        assert result is True

    @pytest.mark.anyio
    async def test_exists_name_false(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = SpaceDomainGroupRepository(session)
        result = await repo.exists_name(space_id=100, name="不存在")
        assert result is False

    @pytest.mark.anyio
    async def test_create_group(self):
        session = _mock_session()
        repo = SpaceDomainGroupRepository(session)
        g = await repo.create_group(space_id=100, name="数学学院", description="数学系")
        assert g.name == "数学学院"
        assert g.description == "数学系"
        assert g.space_id == 100
        assert g.deleted_at is None
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_save_group(self):
        session = _mock_session()
        g = _domain_group(name="old")
        repo = SpaceDomainGroupRepository(session)
        result = await repo.save(g)
        assert result is g
        session.add.assert_called_once_with(g)


# ---------------------------------------------------------------------------
# SpaceDomainGroupDomainRepository
# ---------------------------------------------------------------------------


class TestSpaceDomainGroupDomainRepository:
    @pytest.mark.anyio
    async def test_list_domains_for_group_empty(self):
        session = _mock_session()
        session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))
        repo = SpaceDomainGroupDomainRepository(session)
        result = await repo.list_domains_for_group(group_id=1)
        assert result == []

    @pytest.mark.anyio
    async def test_list_domains_for_group(self):
        session = _mock_session()
        session.execute.return_value = MagicMock(
            all=MagicMock(return_value=[("cs.edu.cn",), ("ee.edu.cn",)])
        )
        repo = SpaceDomainGroupDomainRepository(session)
        result = await repo.list_domains_for_group(group_id=1)
        assert result == ["cs.edu.cn", "ee.edu.cn"]

    @pytest.mark.anyio
    async def test_list_domains_for_groups_empty_input(self):
        session = _mock_session()
        repo = SpaceDomainGroupDomainRepository(session)
        result = await repo.list_domains_for_groups([])
        assert result == {}

    @pytest.mark.anyio
    async def test_list_domains_for_groups(self):
        session = _mock_session()
        session.execute.return_value = MagicMock(
            all=MagicMock(
                return_value=[
                    (1, "cs.edu.cn"),
                    (1, "ai.edu.cn"),
                    (2, "math.edu.cn"),
                ]
            )
        )
        repo = SpaceDomainGroupDomainRepository(session)
        result = await repo.list_domains_for_groups([1, 2])
        assert result == {1: ["cs.edu.cn", "ai.edu.cn"], 2: ["math.edu.cn"]}

    @pytest.mark.anyio
    async def test_replace_domains_clears_old_and_adds_new(self):
        session = _mock_session()
        old_item = SimpleNamespace(
            domain="old.edu.cn",
            deleted_at=None,
            updated_at=None,
        )
        session.execute.return_value = _mock_scalars([old_item])
        repo = SpaceDomainGroupDomainRepository(session)

        await repo.replace_domains(group_id=1, domains=["new.edu.cn"])

        assert old_item.deleted_at is not None
        assert session.add.call_count == 1  # one new domain

    @pytest.mark.anyio
    async def test_soft_delete_by_group(self):
        session = _mock_session()
        d1 = SimpleNamespace(domain="a.edu.cn", deleted_at=None, updated_at=None)
        d2 = SimpleNamespace(domain="b.edu.cn", deleted_at=None, updated_at=None)
        session.execute.return_value = _mock_scalars([d1, d2])
        repo = SpaceDomainGroupDomainRepository(session)

        await repo.soft_delete_by_group(group_id=1)

        assert d1.deleted_at is not None
        assert d2.deleted_at is not None
