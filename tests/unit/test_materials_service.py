from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.materials.services import (
    MaterialBundleService,
    MaterialService,
    _bundle_to_dto,
    _material_to_dto,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC)


def _make_material(**overrides) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "type": "PDF",
        "url": "https://example.com/file.pdf",
        "name": "Lecture Notes",
        "uploader_id": 99,
        "created_at": _NOW,
        "expires": None,
        "download_count": 0,
        "meta": {},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_bundle(**overrides) -> SimpleNamespace:
    defaults = {
        "id": 1,
        "title": "Study Pack",
        "content": "A collection of materials",
        "creator_id": 99,
        "created_at": _NOW,
        "updated_at": _NOW,
        "rating": 4.5,
        "rating_count": 10,
        "comments_count": 3,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_material_service(repo: AsyncMock | None = None) -> tuple[MaterialService, AsyncMock]:
    repo = repo or AsyncMock()
    return MaterialService(repo=repo), repo


def _make_bundle_service(
    repo: AsyncMock | None = None,
    material_repo: AsyncMock | None = None,
) -> tuple[MaterialBundleService, AsyncMock, AsyncMock]:
    repo = repo or AsyncMock()
    material_repo = material_repo or AsyncMock()
    return MaterialBundleService(repo=repo, material_repo=material_repo), repo, material_repo


# ---------------------------------------------------------------------------
# _material_to_dto
# ---------------------------------------------------------------------------


class TestMaterialToDto:
    def test_converts_all_fields(self):
        material = _make_material(
            id=7,
            type="IMAGE",
            url="https://img.test/a.png",
            name="Photo",
            uploader_id=42,
            expires=1000,
            download_count=5,
            meta={"size": 1024},
        )
        dto = _material_to_dto(material)

        assert dto["id"] == 7
        assert dto["type"] == "IMAGE"
        assert dto["url"] == "https://img.test/a.png"
        assert dto["name"] == "Photo"
        assert dto["uploaderId"] == 42
        assert dto["expires"] == 1000
        assert dto["downloadCount"] == 5
        assert dto["meta"] == {"size": 1024}

    def test_created_at_as_epoch_ms(self):
        ts = datetime(2025, 6, 15, 12, 0, 0)
        material = _make_material(created_at=ts)
        dto = _material_to_dto(material)
        expected_ms = int(ts.timestamp() * 1000)
        assert dto["createdAt"] == expected_ms

    def test_none_created_at_becomes_zero(self):
        material = _make_material(created_at=None)
        dto = _material_to_dto(material)
        assert dto["createdAt"] == 0


# ---------------------------------------------------------------------------
# _bundle_to_dto
# ---------------------------------------------------------------------------


class TestBundleToDto:
    def test_converts_all_fields(self):
        bundle = _make_bundle(
            id=3,
            title="Pack",
            content="Body",
            creator_id=11,
            rating=3.2,
            rating_count=7,
            comments_count=2,
        )
        dto = _bundle_to_dto(bundle)

        assert dto["id"] == 3
        assert dto["title"] == "Pack"
        assert dto["content"] == "Body"
        assert dto["creatorId"] == 11
        assert dto["rating"] == 3.2
        assert dto["ratingCount"] == 7
        assert dto["commentsCount"] == 2

    def test_timestamps_as_epoch_ms(self):
        ts = datetime(2025, 6, 15, 12, 0, 0)
        bundle = _make_bundle(created_at=ts, updated_at=ts)
        dto = _bundle_to_dto(bundle)
        expected_ms = int(ts.timestamp() * 1000)
        assert dto["createdAt"] == expected_ms
        assert dto["updatedAt"] == expected_ms

    def test_none_created_at_becomes_zero(self):
        bundle = _make_bundle(created_at=None)
        dto = _bundle_to_dto(bundle)
        assert dto["createdAt"] == 0

    def test_none_updated_at_becomes_zero(self):
        bundle = _make_bundle(updated_at=None)
        dto = _bundle_to_dto(bundle)
        assert dto["updatedAt"] == 0

    def test_both_timestamps_none(self):
        bundle = _make_bundle(created_at=None, updated_at=None)
        dto = _bundle_to_dto(bundle)
        assert dto["createdAt"] == 0
        assert dto["updatedAt"] == 0


# ---------------------------------------------------------------------------
# MaterialService.get_material
# ---------------------------------------------------------------------------


class TestMaterialServiceGetMaterial:
    @pytest.mark.anyio
    async def test_returns_dto_when_found(self):
        svc, repo = _make_material_service()
        material = _make_material(id=10)
        repo.get_by_id.return_value = material

        result = await svc.get_material(10)

        repo.get_by_id.assert_awaited_once_with(10)
        assert result["id"] == 10

    @pytest.mark.anyio
    async def test_raises_not_found_when_missing(self):
        svc, repo = _make_material_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Material not found"):
            await svc.get_material(999)


# ---------------------------------------------------------------------------
# MaterialService.create_material
# ---------------------------------------------------------------------------


class TestMaterialServiceCreateMaterial:
    @pytest.mark.anyio
    async def test_returns_id(self):
        svc, repo = _make_material_service()
        repo.create.return_value = _make_material(id=55)

        result = await svc.create_material(
            type="PDF",
            url="https://test.com/f.pdf",
            name="Notes",
            uploader_id=1,
        )

        assert result == {"id": 55}
        repo.create.assert_awaited_once_with(
            type="PDF",
            url="https://test.com/f.pdf",
            name="Notes",
            uploader_id=1,
            expires=None,
            meta=None,
        )

    @pytest.mark.anyio
    async def test_passes_optional_params(self):
        svc, repo = _make_material_service()
        repo.create.return_value = _make_material(id=56)

        result = await svc.create_material(
            type="VIDEO",
            url="https://test.com/v.mp4",
            name="Lecture",
            uploader_id=2,
            expires=3600,
            meta={"duration": 120},
        )

        assert result == {"id": 56}
        repo.create.assert_awaited_once_with(
            type="VIDEO",
            url="https://test.com/v.mp4",
            name="Lecture",
            uploader_id=2,
            expires=3600,
            meta={"duration": 120},
        )


# ---------------------------------------------------------------------------
# MaterialService.delete_material
# ---------------------------------------------------------------------------


class TestMaterialServiceDeleteMaterial:
    @pytest.mark.anyio
    async def test_deletes_own_material(self):
        svc, repo = _make_material_service()
        repo.get_by_id.return_value = _make_material(id=10, uploader_id=5)

        await svc.delete_material(material_id=10, user_id=5)

        repo.delete.assert_awaited_once_with(10)

    @pytest.mark.anyio
    async def test_raises_not_found_when_missing(self):
        svc, repo = _make_material_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Material not found"):
            await svc.delete_material(material_id=999, user_id=5)

    @pytest.mark.anyio
    async def test_raises_forbidden_for_other_user(self):
        svc, repo = _make_material_service()
        repo.get_by_id.return_value = _make_material(id=10, uploader_id=5)

        with pytest.raises(ForbiddenError, match="Only the uploader can delete"):
            await svc.delete_material(material_id=10, user_id=999)

        repo.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# MaterialBundleService._parse_search_query
# ---------------------------------------------------------------------------


class TestParseSearchQuery:
    def _svc(self) -> MaterialBundleService:
        svc, _, _ = _make_bundle_service()
        return svc

    def test_none_query(self):
        assert self._svc()._parse_search_query(None) == (None, None)

    def test_empty_string(self):
        assert self._svc()._parse_search_query("") == (None, None)

    def test_title_prefix(self):
        title, id_gte = self._svc()._parse_search_query("title:physics")
        assert title == "physics"
        assert id_gte is None

    def test_id_gte_prefix(self):
        title, id_gte = self._svc()._parse_search_query("id:>=100")
        assert title is None
        assert id_gte == 100

    def test_both_prefixes(self):
        title, id_gte = self._svc()._parse_search_query("title:math id:>=50")
        assert title == "math"
        assert id_gte == 50

    def test_invalid_id_gte_ignored(self):
        title, id_gte = self._svc()._parse_search_query("id:>=abc")
        assert title is None
        assert id_gte is None

    def test_plain_text_becomes_title_keyword(self):
        title, id_gte = self._svc()._parse_search_query("organic chemistry")
        assert title == "organic chemistry"
        assert id_gte is None

    def test_remaining_text_becomes_title_when_no_title_prefix(self):
        title, id_gte = self._svc()._parse_search_query("physics id:>=10")
        assert title == "physics"
        assert id_gte == 10

    def test_remaining_ignored_when_title_prefix_present(self):
        title, id_gte = self._svc()._parse_search_query("title:math extra words")
        assert title == "math"


# ---------------------------------------------------------------------------
# MaterialBundleService.list_bundles
# ---------------------------------------------------------------------------


class TestBundleServiceListBundles:
    @pytest.mark.anyio
    async def test_basic_listing_no_pagination(self):
        svc, repo, _ = _make_bundle_service()
        bundles = [_make_bundle(id=1), _make_bundle(id=2)]
        repo.list_all_bundle_ids.return_value = [1, 2]
        repo.list_bundles.return_value = bundles

        items, page = await svc.list_bundles(keyword=None, page_start=None, page_size=10)

        assert len(items) == 2
        assert page["pageStart"] == 1
        assert page["pageSize"] == 2
        assert page["hasPrev"] is False
        assert page["prevStart"] == 0
        assert page["hasMore"] is False
        assert page["nextStart"] == 0

    @pytest.mark.anyio
    async def test_pagination_with_page_start(self):
        svc, repo, _ = _make_bundle_service()
        all_ids = [10, 20, 30, 40, 50]
        repo.list_all_bundle_ids.return_value = all_ids
        repo.list_bundles.return_value = [_make_bundle(id=30), _make_bundle(id=40)]

        items, page = await svc.list_bundles(keyword=None, page_start=30, page_size=2)

        assert page["pageStart"] == 30
        assert page["pageSize"] == 2
        assert page["hasPrev"] is True
        assert page["prevStart"] == 20
        assert page["hasMore"] is True
        assert page["nextStart"] == 50

    @pytest.mark.anyio
    async def test_page_start_not_found_falls_back_to_zero(self):
        svc, repo, _ = _make_bundle_service()
        repo.list_all_bundle_ids.return_value = [1, 2, 3]
        repo.list_bundles.return_value = [_make_bundle(id=1), _make_bundle(id=2)]

        items, page = await svc.list_bundles(keyword=None, page_start=999, page_size=2)

        assert page["pageStart"] == 1
        assert page["hasPrev"] is False
        assert page["hasMore"] is True
        assert page["nextStart"] == 3

    @pytest.mark.anyio
    async def test_empty_result(self):
        svc, repo, _ = _make_bundle_service()
        repo.list_all_bundle_ids.return_value = []
        repo.list_bundles.return_value = []

        items, page = await svc.list_bundles(keyword=None, page_start=None, page_size=10)

        assert items == []
        assert page["pageStart"] == 0
        assert page["pageSize"] == 0
        assert page["hasPrev"] is False
        assert page["hasMore"] is False

    @pytest.mark.anyio
    async def test_newest_sort_passes_descending(self):
        svc, repo, _ = _make_bundle_service()
        repo.list_all_bundle_ids.return_value = [3, 2, 1]
        repo.list_bundles.return_value = [_make_bundle(id=3)]

        await svc.list_bundles(keyword=None, page_start=None, page_size=1, sort="newest")

        repo.list_all_bundle_ids.assert_awaited_once_with(
            keyword=None,
            id_gte=None,
            descending=True,
        )
        repo.list_bundles.assert_awaited_once_with(
            keyword=None,
            id_gte=None,
            limit=1,
            cursor_id=None,
            descending=True,
        )

    @pytest.mark.anyio
    async def test_non_newest_sort_is_ascending(self):
        svc, repo, _ = _make_bundle_service()
        repo.list_all_bundle_ids.return_value = [1]
        repo.list_bundles.return_value = [_make_bundle(id=1)]

        await svc.list_bundles(keyword=None, page_start=None, page_size=10, sort="oldest")

        repo.list_all_bundle_ids.assert_awaited_once_with(
            keyword=None,
            id_gte=None,
            descending=False,
        )

    @pytest.mark.anyio
    async def test_keyword_parsed_and_forwarded(self):
        svc, repo, _ = _make_bundle_service()
        repo.list_all_bundle_ids.return_value = [1]
        repo.list_bundles.return_value = [_make_bundle(id=1)]

        await svc.list_bundles(keyword="title:math id:>=5", page_start=None, page_size=10)

        repo.list_all_bundle_ids.assert_awaited_once_with(
            keyword="math",
            id_gte=5,
            descending=False,
        )

    @pytest.mark.anyio
    async def test_last_page_has_no_more(self):
        svc, repo, _ = _make_bundle_service()
        repo.list_all_bundle_ids.return_value = [1, 2, 3]
        repo.list_bundles.return_value = [_make_bundle(id=3)]

        items, page = await svc.list_bundles(keyword=None, page_start=3, page_size=5)

        assert page["hasMore"] is False
        assert page["nextStart"] == 0
        assert page["hasPrev"] is True


# ---------------------------------------------------------------------------
# MaterialBundleService.get_bundle
# ---------------------------------------------------------------------------


class TestBundleServiceGetBundle:
    @pytest.mark.anyio
    async def test_returns_dto_with_material_ids(self):
        svc, repo, _ = _make_bundle_service()
        bundle = _make_bundle(id=10)
        repo.get_by_id.return_value = bundle
        repo.get_materials_for_bundle.return_value = [1, 2, 3]

        dto = await svc.get_bundle(10)

        assert dto["id"] == 10
        assert dto["materialIds"] == [1, 2, 3]

    @pytest.mark.anyio
    async def test_raises_not_found_when_missing(self):
        svc, repo, _ = _make_bundle_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Material bundle not found"):
            await svc.get_bundle(999)


# ---------------------------------------------------------------------------
# MaterialBundleService.get_bundle_detail
# ---------------------------------------------------------------------------


class TestBundleServiceGetBundleDetail:
    @pytest.mark.anyio
    async def test_returns_dto_with_materials_and_creator(self):
        svc, repo, material_repo = _make_bundle_service()
        bundle = _make_bundle(id=10, creator_id=42)
        repo.get_by_id.return_value = bundle
        repo.get_materials_for_bundle.return_value = [1, 2]

        mat1 = _make_material(id=1, name="File1")
        mat2 = _make_material(id=2, name="File2")
        material_repo.get_by_id.side_effect = [mat1, mat2]

        dto = await svc.get_bundle_detail(10)

        assert dto["id"] == 10
        assert len(dto["materials"]) == 2
        assert dto["materials"][0]["name"] == "File1"
        assert dto["materials"][1]["name"] == "File2"
        assert dto["creator"] == {"id": 42}

    @pytest.mark.anyio
    async def test_skips_missing_materials(self):
        svc, repo, material_repo = _make_bundle_service()
        bundle = _make_bundle(id=10, creator_id=42)
        repo.get_by_id.return_value = bundle
        repo.get_materials_for_bundle.return_value = [1, 2, 3]

        material_repo.get_by_id.side_effect = [
            _make_material(id=1),
            None,
            _make_material(id=3),
        ]

        dto = await svc.get_bundle_detail(10)

        assert len(dto["materials"]) == 2
        assert dto["materials"][0]["id"] == 1
        assert dto["materials"][1]["id"] == 3

    @pytest.mark.anyio
    async def test_raises_not_found_when_missing(self):
        svc, repo, _ = _make_bundle_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Material bundle not found"):
            await svc.get_bundle_detail(999)

    @pytest.mark.anyio
    async def test_empty_material_ids(self):
        svc, repo, material_repo = _make_bundle_service()
        bundle = _make_bundle(id=10, creator_id=42)
        repo.get_by_id.return_value = bundle
        repo.get_materials_for_bundle.return_value = []

        dto = await svc.get_bundle_detail(10)

        assert dto["materials"] == []
        assert dto["creator"] == {"id": 42}
        material_repo.get_by_id.assert_not_awaited()


# ---------------------------------------------------------------------------
# MaterialBundleService.create_bundle
# ---------------------------------------------------------------------------


class TestBundleServiceCreateBundle:
    @pytest.mark.anyio
    async def test_creates_without_materials(self):
        svc, repo, material_repo = _make_bundle_service()
        repo.create.return_value = _make_bundle(id=20)

        result = await svc.create_bundle(
            title="New Pack",
            content="Some content",
            creator_id=5,
        )

        assert result == {"id": 20}
        repo.create.assert_awaited_once_with(
            title="New Pack",
            content="Some content",
            creator_id=5,
        )
        repo.add_material_to_bundle.assert_not_awaited()
        material_repo.get_by_id.assert_not_awaited()

    @pytest.mark.anyio
    async def test_creates_with_material_ids(self):
        svc, repo, material_repo = _make_bundle_service()
        repo.create.return_value = _make_bundle(id=20)
        material_repo.get_by_id.side_effect = [
            _make_material(id=1),
            _make_material(id=2),
        ]

        result = await svc.create_bundle(
            title="Pack",
            content="Body",
            creator_id=5,
            material_ids=[1, 2],
        )

        assert result == {"id": 20}
        assert repo.add_material_to_bundle.await_count == 2

    @pytest.mark.anyio
    async def test_raises_not_found_for_missing_material(self):
        svc, repo, material_repo = _make_bundle_service()
        material_repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Material not found"):
            await svc.create_bundle(
                title="Pack",
                content="Body",
                creator_id=5,
                material_ids=[999],
            )

        repo.create.assert_not_awaited()

    @pytest.mark.anyio
    async def test_empty_material_ids_list_skips_validation(self):
        svc, repo, material_repo = _make_bundle_service()
        repo.create.return_value = _make_bundle(id=30)

        result = await svc.create_bundle(
            title="Pack",
            content="Body",
            creator_id=5,
            material_ids=[],
        )

        assert result == {"id": 30}
        material_repo.get_by_id.assert_not_awaited()
        repo.add_material_to_bundle.assert_not_awaited()

    @pytest.mark.anyio
    async def test_none_material_ids_skips_validation(self):
        svc, repo, material_repo = _make_bundle_service()
        repo.create.return_value = _make_bundle(id=31)

        result = await svc.create_bundle(
            title="Pack",
            content="Body",
            creator_id=5,
            material_ids=None,
        )

        assert result == {"id": 31}
        material_repo.get_by_id.assert_not_awaited()


# ---------------------------------------------------------------------------
# MaterialBundleService.update_bundle
# ---------------------------------------------------------------------------


class TestBundleServiceUpdateBundle:
    @pytest.mark.anyio
    async def test_updates_fields(self):
        svc, repo, _ = _make_bundle_service()
        bundle = _make_bundle(id=10, creator_id=5)
        repo.get_by_id.return_value = bundle
        repo.get_materials_for_bundle.return_value = [1]

        dto = await svc.update_bundle(
            bundle_id=10,
            user_id=5,
            title="Updated",
            content="New body",
        )

        repo.update.assert_awaited_once_with(bundle, title="Updated", content="New body")
        assert dto["id"] == 10
        assert dto["materialIds"] == [1]

    @pytest.mark.anyio
    async def test_raises_not_found_when_missing(self):
        svc, repo, _ = _make_bundle_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Material bundle not found"):
            await svc.update_bundle(bundle_id=999, user_id=5)

    @pytest.mark.anyio
    async def test_raises_forbidden_for_other_user(self):
        svc, repo, _ = _make_bundle_service()
        repo.get_by_id.return_value = _make_bundle(id=10, creator_id=5)

        with pytest.raises(ForbiddenError, match="Only the creator can update"):
            await svc.update_bundle(bundle_id=10, user_id=999)

        repo.update.assert_not_awaited()

    @pytest.mark.anyio
    async def test_replaces_material_ids(self):
        svc, repo, _ = _make_bundle_service()
        bundle = _make_bundle(id=10, creator_id=5)
        repo.get_by_id.return_value = bundle
        # First call: current material ids; second call: final material ids
        repo.get_materials_for_bundle.side_effect = [[1, 2], [2, 3]]

        dto = await svc.update_bundle(
            bundle_id=10,
            user_id=5,
            material_ids=[2, 3],
        )

        repo.remove_material_from_bundle.assert_awaited_once_with(
            bundle_id=10,
            material_id=1,
        )
        repo.add_material_to_bundle.assert_awaited_once_with(
            bundle_id=10,
            material_id=3,
        )
        assert dto["materialIds"] == [2, 3]

    @pytest.mark.anyio
    async def test_material_ids_none_skips_sync(self):
        svc, repo, _ = _make_bundle_service()
        bundle = _make_bundle(id=10, creator_id=5)
        repo.get_by_id.return_value = bundle
        repo.get_materials_for_bundle.return_value = [1, 2]

        await svc.update_bundle(bundle_id=10, user_id=5, material_ids=None)

        # get_materials_for_bundle called only once (for the final result)
        repo.get_materials_for_bundle.assert_awaited_once()
        repo.remove_material_from_bundle.assert_not_awaited()
        repo.add_material_to_bundle.assert_not_awaited()

    @pytest.mark.anyio
    async def test_empty_material_ids_removes_all(self):
        svc, repo, _ = _make_bundle_service()
        bundle = _make_bundle(id=10, creator_id=5)
        repo.get_by_id.return_value = bundle
        repo.get_materials_for_bundle.side_effect = [[1, 2], []]

        dto = await svc.update_bundle(
            bundle_id=10,
            user_id=5,
            material_ids=[],
        )

        assert repo.remove_material_from_bundle.await_count == 2
        repo.add_material_to_bundle.assert_not_awaited()
        assert dto["materialIds"] == []

    @pytest.mark.anyio
    async def test_no_changes_when_material_ids_match(self):
        svc, repo, _ = _make_bundle_service()
        bundle = _make_bundle(id=10, creator_id=5)
        repo.get_by_id.return_value = bundle
        repo.get_materials_for_bundle.side_effect = [[1, 2], [1, 2]]

        await svc.update_bundle(
            bundle_id=10,
            user_id=5,
            material_ids=[1, 2],
        )

        repo.remove_material_from_bundle.assert_not_awaited()
        repo.add_material_to_bundle.assert_not_awaited()


# ---------------------------------------------------------------------------
# MaterialBundleService.delete_bundle
# ---------------------------------------------------------------------------


class TestBundleServiceDeleteBundle:
    @pytest.mark.anyio
    async def test_deletes_own_bundle(self):
        svc, repo, _ = _make_bundle_service()
        repo.get_by_id.return_value = _make_bundle(id=10, creator_id=5)

        await svc.delete_bundle(bundle_id=10, user_id=5)

        repo.delete.assert_awaited_once_with(10)

    @pytest.mark.anyio
    async def test_raises_not_found_when_missing(self):
        svc, repo, _ = _make_bundle_service()
        repo.get_by_id.return_value = None

        with pytest.raises(NotFoundError, match="Material bundle not found"):
            await svc.delete_bundle(bundle_id=999, user_id=5)

    @pytest.mark.anyio
    async def test_raises_forbidden_for_other_user(self):
        svc, repo, _ = _make_bundle_service()
        repo.get_by_id.return_value = _make_bundle(id=10, creator_id=5)

        with pytest.raises(ForbiddenError, match="Only the creator can delete"):
            await svc.delete_bundle(bundle_id=10, user_id=999)

        repo.delete.assert_not_awaited()
