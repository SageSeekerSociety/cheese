"""Unit tests for TaskAccessDomainRepository."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.task.repositories import TaskAccessDomainRepository

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    return session


def _mock_scalars(lst):
    m = MagicMock()
    s = MagicMock()
    s.all.return_value = lst
    m.scalars.return_value = s
    return m


# ---------------------------------------------------------------------------
# TaskAccessDomainRepository
# ---------------------------------------------------------------------------


class TestTaskAccessDomainRepository:
    @pytest.mark.anyio
    async def test_list_by_task_id_empty(self):
        session = _mock_session()
        session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))
        repo = TaskAccessDomainRepository(session)
        result = await repo.list_by_task_id(1)
        assert result == []

    @pytest.mark.anyio
    async def test_list_by_task_id_returns_domains(self):
        session = _mock_session()
        session.execute.return_value = MagicMock(
            all=MagicMock(return_value=[("cs.edu.cn",), ("math.edu.cn",)])
        )
        repo = TaskAccessDomainRepository(session)
        result = await repo.list_by_task_id(1)
        assert result == ["cs.edu.cn", "math.edu.cn"]

    @pytest.mark.anyio
    async def test_replace_domains_clears_old_and_inserts_new(self):
        session = _mock_session()
        old_item = SimpleNamespace(
            domain="old.edu.cn",
            deleted_at=None,
            updated_at=None,
        )
        session.execute.return_value = _mock_scalars([old_item])
        repo = TaskAccessDomainRepository(session)

        await repo.replace_domains(task_id=1, domains=["new.edu.cn", "other.edu.cn"])

        # Old items soft-deleted
        assert old_item.deleted_at is not None
        # Two new rows inserted
        assert session.add.call_count == 2

    @pytest.mark.anyio
    async def test_soft_delete_by_task(self):
        session = _mock_session()
        d1 = SimpleNamespace(domain="a.edu.cn", deleted_at=None, updated_at=None)
        d2 = SimpleNamespace(domain="b.edu.cn", deleted_at=None, updated_at=None)
        session.execute.return_value = _mock_scalars([d1, d2])
        repo = TaskAccessDomainRepository(session)

        await repo.soft_delete_by_task(task_id=1)

        assert d1.deleted_at is not None
        assert d2.deleted_at is not None

    @pytest.mark.anyio
    async def test_soft_delete_by_task_no_existing(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = TaskAccessDomainRepository(session)

        await repo.soft_delete_by_task(task_id=1)
        # No calls to add
        session.add.assert_not_called()
