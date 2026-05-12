"""Unit tests for app.domain.notification.repositories.NotificationRepository."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.notification.models import NotificationType
from app.domain.notification.repositories import NotificationRepository

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _notification(**overrides):
    defaults = {
        "id": 1,
        "receiver_id": 10,
        "type": NotificationType.MENTION,
        "metadata_payload": {},
        "read": False,
        "is_aggregatable": False,
        "aggregation_key": None,
        "aggregate_until": None,
        "finalized": True,
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


def _mock_rowcount(count):
    m = MagicMock()
    m.rowcount = count
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNotificationRepository:
    @pytest.mark.anyio
    async def test_get_by_id_for_user_found(self):
        session = _mock_session()
        n = _notification()
        session.execute.return_value = _mock_scalar(n)
        repo = NotificationRepository(session)

        result = await repo.get_by_id_for_user(10, 1)
        assert result is n

    @pytest.mark.anyio
    async def test_get_by_id_for_user_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = NotificationRepository(session)

        assert await repo.get_by_id_for_user(10, 999) is None

    @pytest.mark.anyio
    async def test_list_for_user_basic(self):
        session = _mock_session()
        n = _notification()
        session.execute.return_value = _mock_scalars([n])
        repo = NotificationRepository(session)

        result = await repo.list_for_user(10, limit=20)
        assert list(result) == [n]

    @pytest.mark.anyio
    async def test_list_for_user_with_cursor(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = NotificationRepository(session)

        result = await repo.list_for_user(10, limit=20, cursor_created_at=NOW, cursor_id=5)
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_for_user_with_type_filter(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = NotificationRepository(session)

        result = await repo.list_for_user(10, limit=20, type_=NotificationType.REACTION)
        assert list(result) == []

    @pytest.mark.anyio
    async def test_list_for_user_with_read_filter(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = NotificationRepository(session)

        result = await repo.list_for_user(10, limit=20, read=False)
        assert list(result) == []

    @pytest.mark.anyio
    async def test_mark_all_as_read_for_user(self):
        session = _mock_session()
        session.execute.return_value = _mock_rowcount(3)
        repo = NotificationRepository(session)

        result = await repo.mark_all_as_read_for_user(10)
        assert result == 3

    @pytest.mark.anyio
    async def test_count_unread_for_user(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(7)
        repo = NotificationRepository(session)

        assert await repo.count_unread_for_user(10) == 7

    @pytest.mark.anyio
    async def test_set_read_status_for_user(self):
        session = _mock_session()
        session.execute.return_value = _mock_rowcount(1)
        repo = NotificationRepository(session)

        result = await repo.set_read_status_for_user(10, 1, True)
        assert result == 1

    @pytest.mark.anyio
    async def test_find_all_by_ids_for_user_empty(self):
        session = _mock_session()
        repo = NotificationRepository(session)

        result = await repo.find_all_by_ids_for_user(10, [])
        assert result == []

    @pytest.mark.anyio
    async def test_find_all_by_ids_for_user(self):
        session = _mock_session()
        n = _notification()
        session.execute.return_value = _mock_scalars([n])
        repo = NotificationRepository(session)

        result = await repo.find_all_by_ids_for_user(10, [1])
        assert result == [n]

    @pytest.mark.anyio
    async def test_count_for_user_basic(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(10)
        repo = NotificationRepository(session)

        assert await repo.count_for_user(10) == 10

    @pytest.mark.anyio
    async def test_count_for_user_with_filters(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar_one(3)
        repo = NotificationRepository(session)

        result = await repo.count_for_user(10, type_=NotificationType.MENTION, read=False)
        assert result == 3

    @pytest.mark.anyio
    async def test_save_all(self):
        session = _mock_session()
        n1 = _notification(id=1)
        n2 = _notification(id=2)
        repo = NotificationRepository(session)

        await repo.save_all([n1, n2])
        assert session.add.call_count == 2
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_soft_delete_for_user_found(self):
        session = _mock_session()
        n = _notification()
        session.execute.return_value = _mock_scalar(n)
        repo = NotificationRepository(session)

        result = await repo.soft_delete_for_user(10, 1)
        assert result is True
        assert n.deleted_at is not None

    @pytest.mark.anyio
    async def test_soft_delete_for_user_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = NotificationRepository(session)

        result = await repo.soft_delete_for_user(10, 999)
        assert result is False

    @pytest.mark.anyio
    async def test_find_active_aggregation(self):
        session = _mock_session()
        n = _notification(is_aggregatable=True, finalized=False)
        session.execute.return_value = _mock_scalar(n)
        repo = NotificationRepository(session)

        result = await repo.find_active_aggregation(
            recipient_id=10, aggregation_key="REACTION:discussion:1", now=NOW
        )
        assert result is n

    @pytest.mark.anyio
    async def test_find_expired_aggregations(self):
        session = _mock_session()
        n = _notification(is_aggregatable=True, finalized=False)
        session.execute.return_value = _mock_scalars([n])
        repo = NotificationRepository(session)

        result = await repo.find_expired_aggregations(NOW)
        assert result == [n]
