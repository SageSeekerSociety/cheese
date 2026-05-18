"""Unit tests for Meilisearch search helper + client."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.domain.search.meilisearch_service as ms_mod


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    monkeypatch.setattr(ms_mod, "_client", None)
    monkeypatch.setattr(ms_mod, "_initialized", False)
    yield


class TestMeilisearchSearchIds:

    @pytest.mark.anyio
    async def test_returns_none_when_client_not_configured(self, monkeypatch):
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: None)
        from app.domain.search.search_helper import meilisearch_search_ids

        assert await meilisearch_search_ids("questions", "hello") is None

    @pytest.mark.anyio
    async def test_returns_none_for_empty_query(self, monkeypatch):
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: MagicMock())
        from app.domain.search.search_helper import meilisearch_search_ids

        assert await meilisearch_search_ids("questions", "") is None
        assert await meilisearch_search_ids("questions", "   ") is None

    @pytest.mark.anyio
    async def test_returns_ids_and_total_on_success(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value={
            "hits": [{"id": 10}, {"id": 20}, {"id": 30}],
            "estimatedTotalHits": 42,
        })
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: mock_client)
        from app.domain.search.search_helper import meilisearch_search_ids

        result = await meilisearch_search_ids("questions", "深度学习", limit=3)
        assert result is not None
        ids, total = result
        assert ids == [10, 20, 30]
        assert total == 42

    @pytest.mark.anyio
    async def test_returns_none_on_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search = AsyncMock(side_effect=ConnectionError("down"))
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: mock_client)
        from app.domain.search.search_helper import meilisearch_search_ids

        assert await meilisearch_search_ids("questions", "test") is None

    @pytest.mark.anyio
    async def test_passes_filter_and_sort(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search = AsyncMock(return_value={"hits": [], "estimatedTotalHits": 0})
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: mock_client)
        from app.domain.search.search_helper import meilisearch_search_ids

        await meilisearch_search_ids(
            "knowledge",
            "data",
            limit=5,
            offset=10,
            filter_expr="teamId = 42",
            sort=["createdAt:desc"],
        )
        mock_client.search.assert_called_once_with(
            "knowledge",
            "data",
            limit=5,
            offset=10,
            filter_expr="teamId = 42",
            sort=["createdAt:desc"],
        )


class TestIndexDocument:

    @pytest.mark.anyio
    async def test_no_op_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: None)
        from app.domain.search.search_helper import index_document

        await index_document("questions", {"id": 1, "title": "test"})

    @pytest.mark.anyio
    async def test_indexes_document(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.add_documents = AsyncMock()
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: mock_client)
        from app.domain.search.search_helper import index_document

        await index_document("questions", {"id": 1, "title": "test"})
        mock_client.add_documents.assert_called_once_with("questions", [{"id": 1, "title": "test"}])

    @pytest.mark.anyio
    async def test_swallows_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.add_documents = AsyncMock(side_effect=ConnectionError("down"))
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: mock_client)
        from app.domain.search.search_helper import index_document

        await index_document("questions", {"id": 1})
