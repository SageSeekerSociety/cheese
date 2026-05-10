"""Unit tests for Meilisearch search helper + client."""

from unittest.mock import MagicMock

import pytest

import app.domain.search.meilisearch_service as ms_mod


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    """Reset the module-level singleton so each test starts clean."""
    monkeypatch.setattr(ms_mod, "_client", None)
    monkeypatch.setattr(ms_mod, "_initialized", False)
    yield


class TestMeilisearchSearchIds:
    """Tests for search_helper.meilisearch_search_ids."""

    def test_returns_none_when_client_not_configured(self, monkeypatch):
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: None)
        from app.domain.search.search_helper import meilisearch_search_ids

        assert meilisearch_search_ids("questions", "hello") is None

    def test_returns_none_for_empty_query(self, monkeypatch):
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: MagicMock())
        from app.domain.search.search_helper import meilisearch_search_ids

        assert meilisearch_search_ids("questions", "") is None
        assert meilisearch_search_ids("questions", "   ") is None

    def test_returns_ids_and_total_on_success(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search.return_value = {
            "hits": [{"id": 10}, {"id": 20}, {"id": 30}],
            "estimatedTotalHits": 42,
        }
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: mock_client)
        from app.domain.search.search_helper import meilisearch_search_ids

        result = meilisearch_search_ids("questions", "深度学习", limit=3)
        assert result is not None
        ids, total = result
        assert ids == [10, 20, 30]
        assert total == 42

    def test_returns_none_on_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search.side_effect = ConnectionError("down")
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: mock_client)
        from app.domain.search.search_helper import meilisearch_search_ids

        assert meilisearch_search_ids("questions", "test") is None

    def test_passes_filter_and_sort(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search.return_value = {"hits": [], "estimatedTotalHits": 0}
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: mock_client)
        from app.domain.search.search_helper import meilisearch_search_ids

        meilisearch_search_ids(
            "knowledge", "data", limit=5, offset=10,
            filter_expr="teamId = 42", sort=["createdAt:desc"],
        )
        mock_client.search.assert_called_once_with(
            "knowledge", "data",
            limit=5, offset=10, filter_expr="teamId = 42", sort=["createdAt:desc"],
        )


class TestIndexDocument:
    """Tests for search_helper.index_document."""

    def test_no_op_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: None)
        from app.domain.search.search_helper import index_document

        index_document("questions", {"id": 1, "title": "test"})

    def test_indexes_document(self, monkeypatch):
        mock_client = MagicMock()
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: mock_client)
        from app.domain.search.search_helper import index_document

        index_document("questions", {"id": 1, "title": "test"})
        mock_client.add_documents.assert_called_once_with(
            "questions", [{"id": 1, "title": "test"}]
        )

    def test_swallows_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.add_documents.side_effect = ConnectionError("down")
        monkeypatch.setattr(ms_mod, "get_search_client", lambda: mock_client)
        from app.domain.search.search_helper import index_document

        index_document("questions", {"id": 1})
