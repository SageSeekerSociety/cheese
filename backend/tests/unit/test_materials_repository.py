"""Unit tests for app.domain.materials.repositories.

Covers MaterialRepository, MaterialBundleRepository.
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.materials.repositories import MaterialBundleRepository, MaterialRepository

NOW = datetime(2025, 6, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _material(**overrides):
    defaults = {
        "id": 1,
        "type": "PDF",
        "url": "/files/test.pdf",
        "name": "test.pdf",
        "uploader_id": 10,
        "created_at": NOW,
        "expires": None,
        "download_count": 0,
        "meta": {},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _bundle(**overrides):
    defaults = {
        "id": 1,
        "title": "Bundle One",
        "content": "Content",
        "creator_id": 10,
        "created_at": NOW,
        "updated_at": NOW,
        "rating": 0,
        "rating_count": 0,
        "my_rating": None,
        "comments_count": 0,
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
    m.scalars.return_value = MagicMock(all=MagicMock(return_value=lst))
    return m


def _mock_rowcount(count):
    m = MagicMock()
    m.rowcount = count
    return m


# ---------------------------------------------------------------------------
# MaterialRepository
# ---------------------------------------------------------------------------


class TestMaterialRepository:
    @pytest.mark.anyio
    async def test_get_by_id_found(self):
        session = _mock_session()
        mat = _material()
        session.execute.return_value = _mock_scalar(mat)
        repo = MaterialRepository(session)

        result = await repo.get_by_id(1)
        assert result is mat

    @pytest.mark.anyio
    async def test_get_by_id_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = MaterialRepository(session)

        assert await repo.get_by_id(999) is None

    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = MaterialRepository(session)

        result = await repo.create(
            type="PDF", url="/files/test.pdf", name="test.pdf", uploader_id=10
        )
        assert result.type == "PDF"
        assert result.url == "/files/test.pdf"
        assert result.name == "test.pdf"
        assert result.download_count == 0
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_create_with_meta(self):
        session = _mock_session()
        repo = MaterialRepository(session)

        result = await repo.create(
            type="IMAGE",
            url="/files/img.png",
            name="img.png",
            uploader_id=10,
            expires=3600,
            meta={"width": 800},
        )
        assert result.expires == 3600
        assert result.meta == {"width": 800}

    @pytest.mark.anyio
    async def test_delete_found(self):
        session = _mock_session()
        mat = _material()
        session.execute.return_value = _mock_scalar(mat)
        repo = MaterialRepository(session)

        result = await repo.delete(1)
        assert result is True
        session.delete.assert_awaited_once_with(mat)

    @pytest.mark.anyio
    async def test_delete_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = MaterialRepository(session)

        result = await repo.delete(999)
        assert result is False

    @pytest.mark.anyio
    async def test_increment_download_count(self):
        session = _mock_session()
        mat = _material(download_count=5)
        session.execute.return_value = _mock_scalar(mat)
        repo = MaterialRepository(session)

        await repo.increment_download_count(1)
        assert mat.download_count == 6

    @pytest.mark.anyio
    async def test_increment_download_count_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = MaterialRepository(session)

        # Should not raise
        await repo.increment_download_count(999)


# ---------------------------------------------------------------------------
# MaterialBundleRepository
# ---------------------------------------------------------------------------


class TestMaterialBundleRepository:
    @pytest.mark.anyio
    async def test_list_all_bundle_ids(self):
        session = _mock_session()
        session.execute.return_value = _mock_rows([(1,), (2,), (3,)])
        repo = MaterialBundleRepository(session)

        result = await repo.list_all_bundle_ids()
        assert result == [1, 2, 3]

    @pytest.mark.anyio
    async def test_list_all_bundle_ids_with_keyword(self):
        session = _mock_session()
        session.execute.return_value = _mock_rows([(1,)])
        repo = MaterialBundleRepository(session)

        result = await repo.list_all_bundle_ids(keyword="test")
        assert result == [1]

    @pytest.mark.anyio
    async def test_list_all_bundle_ids_descending(self):
        session = _mock_session()
        session.execute.return_value = _mock_rows([(3,), (2,), (1,)])
        repo = MaterialBundleRepository(session)

        result = await repo.list_all_bundle_ids(descending=True)
        assert result == [3, 2, 1]

    @pytest.mark.anyio
    async def test_list_all_bundle_ids_with_id_gte(self):
        session = _mock_session()
        session.execute.return_value = _mock_rows([(5,)])
        repo = MaterialBundleRepository(session)

        result = await repo.list_all_bundle_ids(id_gte=5)
        assert result == [5]

    @pytest.mark.anyio
    async def test_list_bundles_basic(self):
        session = _mock_session()
        b = _bundle()
        session.execute.return_value = _mock_scalars([b])
        repo = MaterialBundleRepository(session)

        result = await repo.list_bundles(limit=10)
        assert result == [b]

    @pytest.mark.anyio
    async def test_list_bundles_with_keyword(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = MaterialBundleRepository(session)

        result = await repo.list_bundles(keyword="test", limit=10)
        assert result == []

    @pytest.mark.anyio
    async def test_list_bundles_with_cursor_ascending(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = MaterialBundleRepository(session)

        result = await repo.list_bundles(cursor_id=5, limit=10)
        assert result == []

    @pytest.mark.anyio
    async def test_list_bundles_with_cursor_descending(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = MaterialBundleRepository(session)

        result = await repo.list_bundles(cursor_id=5, descending=True, limit=10)
        assert result == []

    @pytest.mark.anyio
    async def test_list_bundles_with_id_gte(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([])
        repo = MaterialBundleRepository(session)

        result = await repo.list_bundles(id_gte=5, limit=10)
        assert result == []

    @pytest.mark.anyio
    async def test_get_by_id(self):
        session = _mock_session()
        b = _bundle()
        session.execute.return_value = _mock_scalar(b)
        repo = MaterialBundleRepository(session)

        result = await repo.get_by_id(1)
        assert result is b

    @pytest.mark.anyio
    async def test_create(self):
        session = _mock_session()
        repo = MaterialBundleRepository(session)

        result = await repo.create(title="New Bundle", content="Content", creator_id=10)
        assert result.title == "New Bundle"
        assert result.rating == 0
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_update_all_fields(self):
        session = _mock_session()
        b = _bundle()
        repo = MaterialBundleRepository(session)

        result = await repo.update(b, title="New Title", content="New Content")
        assert result.title == "New Title"
        assert result.content == "New Content"

    @pytest.mark.anyio
    async def test_update_partial(self):
        session = _mock_session()
        b = _bundle()
        repo = MaterialBundleRepository(session)

        result = await repo.update(b, title="New Title")
        assert result.title == "New Title"
        assert result.content == "Content"

    @pytest.mark.anyio
    async def test_delete_found(self):
        session = _mock_session()
        b = _bundle()
        session.execute.return_value = _mock_scalar(b)
        repo = MaterialBundleRepository(session)

        result = await repo.delete(1)
        assert result is True

    @pytest.mark.anyio
    async def test_delete_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = MaterialBundleRepository(session)

        result = await repo.delete(999)
        assert result is False

    @pytest.mark.anyio
    async def test_get_materials_for_bundle(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalars([1, 2, 3])
        repo = MaterialBundleRepository(session)

        result = await repo.get_materials_for_bundle(1)
        assert result == [1, 2, 3]

    @pytest.mark.anyio
    async def test_add_material_to_bundle_new(self):
        session = _mock_session()
        session.execute.return_value = _mock_scalar(None)
        repo = MaterialBundleRepository(session)

        await repo.add_material_to_bundle(bundle_id=1, material_id=10)
        session.add.assert_called_once()

    @pytest.mark.anyio
    async def test_add_material_to_bundle_existing(self):
        session = _mock_session()
        existing = SimpleNamespace(bundle_id=1, material_id=10)
        session.execute.return_value = _mock_scalar(existing)
        repo = MaterialBundleRepository(session)

        await repo.add_material_to_bundle(bundle_id=1, material_id=10)
        session.add.assert_not_called()

    @pytest.mark.anyio
    async def test_remove_material_from_bundle_success(self):
        session = _mock_session()
        session.execute.return_value = _mock_rowcount(1)
        repo = MaterialBundleRepository(session)

        result = await repo.remove_material_from_bundle(bundle_id=1, material_id=10)
        assert result is True

    @pytest.mark.anyio
    async def test_remove_material_from_bundle_not_found(self):
        session = _mock_session()
        session.execute.return_value = _mock_rowcount(0)
        repo = MaterialBundleRepository(session)

        result = await repo.remove_material_from_bundle(bundle_id=1, material_id=999)
        assert result is False
