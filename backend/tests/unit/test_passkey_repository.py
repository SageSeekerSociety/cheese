"""Unit tests for app.domain.passkey.repositories.PasskeyRepository."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.passkey.repositories import PasskeyRepository

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _credential(**overrides):
    defaults = {
        "id": 1,
        "user_id": 10,
        "credential_id": "cred_abc",
        "public_key": b"public_key_bytes",
        "counter": 0,
        "device_type": "platform",
        "backed_up": False,
        "transports": "usb,ble",
        "created_at": NOW,
        "updated_at": NOW,
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


def _mock_rows(lst):
    m = MagicMock()
    m.all.return_value = lst
    return m


def _mock_rowcount(count):
    m = MagicMock()
    m.rowcount = count
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPasskeyRepository:
    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = PasskeyRepository(session)

        result = await repo.create(
            user_id=10,
            credential_id="cred_abc",
            public_key=b"key_bytes",
            counter=0,
            device_type="platform",
            backed_up=False,
            transports=["usb", "ble"],
        )
        assert result.user_id == 10
        assert result.credential_id == "cred_abc"
        assert result.transports == "usb,ble"
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_create_no_transports(self):
        session = _mock_session()
        repo = PasskeyRepository(session)

        result = await repo.create(
            user_id=10,
            credential_id="cred_xyz",
            public_key=b"key",
            device_type="cross-platform",
        )
        assert result.transports is None

    @pytest.mark.anyio
    async def test_get_by_credential_id_found(self):
        session = _mock_session()
        cred = _credential()
        session.execute.return_value = _mock_scalar(cred)
        repo = PasskeyRepository(session)

        result = await repo.get_by_credential_id("cred_abc")
        assert result is cred

    @pytest.mark.anyio
    async def test_get_by_credential_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = PasskeyRepository(session)

        assert await repo.get_by_credential_id("nonexistent") is None

    @pytest.mark.anyio
    async def test_list_by_user(self):
        session = _mock_session()
        cred = _credential()
        session.execute.return_value = _mock_scalars([cred])
        repo = PasskeyRepository(session)

        result = await repo.list_by_user(10)
        assert result == [cred]

    @pytest.mark.anyio
    async def test_get_all_credential_ids_for_user(self):
        session = _mock_session()
        session.execute.return_value = _mock_rows([("cred_abc",), ("cred_xyz",)])
        repo = PasskeyRepository(session)

        result = await repo.get_all_credential_ids_for_user(10)
        assert result == ["cred_abc", "cred_xyz"]

    @pytest.mark.anyio
    async def test_update_counter_found(self):
        session = _mock_session()
        cred = _credential(counter=5)
        session.execute.return_value = _mock_scalar(cred)
        repo = PasskeyRepository(session)

        await repo.update_counter("cred_abc", 10)
        assert cred.counter == 10
        session.flush.assert_awaited_once()

    @pytest.mark.anyio
    async def test_update_counter_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = PasskeyRepository(session)

        await repo.update_counter("nonexistent", 10)
        session.flush.assert_not_awaited()

    @pytest.mark.anyio
    async def test_delete_by_id_success(self):
        session = _mock_session()
        session.execute.return_value = _mock_rowcount(1)
        repo = PasskeyRepository(session)

        result = await repo.delete_by_id(1, 10)
        assert result is True

    @pytest.mark.anyio
    async def test_delete_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_rowcount(0)
        repo = PasskeyRepository(session)

        result = await repo.delete_by_id(999, 10)
        assert result is False

    @pytest.mark.anyio
    async def test_delete_by_credential_id_success(self):
        session = _mock_session()
        session.execute.return_value = _mock_rowcount(1)
        repo = PasskeyRepository(session)

        result = await repo.delete_by_credential_id("cred_abc", 10)
        assert result is True

    @pytest.mark.anyio
    async def test_delete_by_credential_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_rowcount(0)
        repo = PasskeyRepository(session)

        result = await repo.delete_by_credential_id("nonexistent", 10)
        assert result is False
