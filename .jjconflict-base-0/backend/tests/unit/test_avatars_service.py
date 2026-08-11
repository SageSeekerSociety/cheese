from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import NotFoundError
from app.domain.avatars.services import AvatarService, _avatar_to_dto

NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _avatar(**overrides):
    defaults = {
        "id": 1,
        "url": "https://example.com/avatar.png",
        "name": "Default Avatar",
        "avatar_type": "predefined",
        "usage_count": 5,
        "created_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_service(repo=None) -> tuple[AvatarService, AsyncMock]:
    repo = repo or AsyncMock()
    svc = AvatarService(repo=repo)
    return svc, repo


# ---------------------------------------------------------------------------
# _avatar_to_dto (pure helper)
# ---------------------------------------------------------------------------


class TestAvatarToDto:
    def test_basic_conversion(self):
        avatar = _avatar(id=7, url="https://img.com/a.png", name="Cool")
        dto = _avatar_to_dto(avatar)
        assert dto["id"] == 7
        assert dto["url"] == "https://img.com/a.png"
        assert dto["name"] == "Cool"
        assert dto["avatarType"] == "predefined"
        assert dto["usageCount"] == 5
        assert dto["createdAt"] == NOW_MS

    def test_none_created_at(self):
        avatar = _avatar(created_at=None)
        dto = _avatar_to_dto(avatar)
        assert dto["createdAt"] == 0

    def test_zero_usage_count(self):
        avatar = _avatar(usage_count=0)
        dto = _avatar_to_dto(avatar)
        assert dto["usageCount"] == 0


# ---------------------------------------------------------------------------
# list_predefined_ids
# ---------------------------------------------------------------------------


class TestListPredefinedIds:
    @pytest.mark.anyio
    async def test_returns_ids(self):
        svc, repo = _make_service()
        repo.list_by_type.return_value = [
            _avatar(id=1),
            _avatar(id=2),
            _avatar(id=3),
        ]

        result = await svc.list_predefined_ids()

        repo.list_by_type.assert_awaited_once_with("predefined")
        assert result == [1, 2, 3]

    @pytest.mark.anyio
    async def test_empty_list(self):
        svc, repo = _make_service()
        repo.list_by_type.return_value = []

        result = await svc.list_predefined_ids()

        assert result == []


# ---------------------------------------------------------------------------
# get_avatar
# ---------------------------------------------------------------------------


class TestGetAvatar:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = _avatar(id=42, name="Cute Cat")

        result = await svc.get_avatar(42)

        repo.get_by_id.assert_awaited_once_with(42)
        assert result["id"] == 42
        assert result["name"] == "Cute Cat"

    @pytest.mark.anyio
    async def test_not_found(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Avatar not found"):
            await svc.get_avatar(999)


# ---------------------------------------------------------------------------
# get_avatar_raw
# ---------------------------------------------------------------------------


class TestGetAvatarRaw:
    @pytest.mark.anyio
    async def test_returns_avatar(self):
        svc, repo = _make_service()
        avatar = _avatar(id=10)
        repo.get_by_id.return_value = avatar

        result = await svc.get_avatar_raw(10)

        assert result is avatar

    @pytest.mark.anyio
    async def test_returns_none(self):
        svc, repo = _make_service()
        repo.get_by_id.return_value = None

        result = await svc.get_avatar_raw(999)

        assert result is None


# ---------------------------------------------------------------------------
# get_default
# ---------------------------------------------------------------------------


class TestGetDefault:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo = _make_service()
        repo.get_default.return_value = _avatar(id=1, name="Default")

        result = await svc.get_default()

        repo.get_default.assert_awaited_once()
        assert result["id"] == 1
        assert result["name"] == "Default"

    @pytest.mark.anyio
    async def test_not_found(self):
        svc, repo = _make_service()
        repo.get_default.return_value = None

        with pytest.raises(NotFoundError, match="No default avatar found"):
            await svc.get_default()


# ---------------------------------------------------------------------------
# get_default_raw
# ---------------------------------------------------------------------------


class TestGetDefaultRaw:
    @pytest.mark.anyio
    async def test_returns_avatar(self):
        svc, repo = _make_service()
        avatar = _avatar(id=1)
        repo.get_default.return_value = avatar

        result = await svc.get_default_raw()

        assert result is avatar

    @pytest.mark.anyio
    async def test_returns_none(self):
        svc, repo = _make_service()
        repo.get_default.return_value = None

        result = await svc.get_default_raw()

        assert result is None


# ---------------------------------------------------------------------------
# get_default_id
# ---------------------------------------------------------------------------


class TestGetDefaultId:
    @pytest.mark.anyio
    async def test_success(self):
        svc, repo = _make_service()
        repo.get_default.return_value = _avatar(id=7)

        result = await svc.get_default_id()

        assert result == 7

    @pytest.mark.anyio
    async def test_not_found(self):
        svc, repo = _make_service()
        repo.get_default.return_value = None

        with pytest.raises(NotFoundError, match="No default avatar found"):
            await svc.get_default_id()


# ---------------------------------------------------------------------------
# create_avatar
# ---------------------------------------------------------------------------


class TestCreateAvatar:
    @pytest.mark.anyio
    async def test_success_default_type(self):
        svc, repo = _make_service()
        repo.create.return_value = _avatar(id=99)

        result = await svc.create_avatar(url="https://img.com/new.png", name="New")

        repo.create.assert_awaited_once_with(
            url="https://img.com/new.png", name="New", avatar_type="UPLOADED"
        )
        assert result == {"avatarId": 99}

    @pytest.mark.anyio
    async def test_success_custom_type(self):
        svc, repo = _make_service()
        repo.create.return_value = _avatar(id=50)

        result = await svc.create_avatar(
            url="https://img.com/x.png", name="X", avatar_type="predefined"
        )

        repo.create.assert_awaited_once_with(
            url="https://img.com/x.png", name="X", avatar_type="predefined"
        )
        assert result == {"avatarId": 50}
