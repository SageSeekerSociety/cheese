"""Meilisearch integration layer.

When ``MEILISEARCH_URL`` is configured, this service provides full-text
search with CJK tokenization, typo tolerance, and relevance ranking —
replacing the NestJS-era Elasticsearch integration with a much lighter
runtime (single Rust binary, ~256 MB RAM vs ES's 2 GB+ JVM).

When not configured, callers fall back to PostgreSQL FTS (the baseline
that is always available).

Usage pattern in repositories::

    from app.domain.search.meilisearch_service import get_search_client

    client = get_search_client()
    if client is not None:
        results = client.search("questions", query, limit=page_size)
        # use results
    else:
        # PG FTS fallback
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings

_logger = logging.getLogger(__name__)

# Lazy singleton — initialized on first call to get_search_client().
_client: MeilisearchClient | None = None
_initialized = False


class MeilisearchClient:
    """Thin async-friendly wrapper around the meilisearch SDK."""

    def __init__(self, url: str, api_key: str) -> None:
        import meilisearch

        self._client = meilisearch.Client(url, api_key)
        _logger.info("Meilisearch client connected to %s", url)

    def ensure_index(self, uid: str, *, primary_key: str = "id") -> None:
        """Create the index if it doesn't exist (idempotent)."""
        try:
            self._client.get_index(uid)
        except Exception:
            self._client.create_index(uid, {"primaryKey": primary_key})
            _logger.info("Created Meilisearch index '%s'", uid)

    def configure_index(
        self,
        uid: str,
        *,
        searchable_attributes: list[str] | None = None,
        filterable_attributes: list[str] | None = None,
        sortable_attributes: list[str] | None = None,
    ) -> None:
        """Update index settings (idempotent — Meilisearch merges)."""
        index = self._client.index(uid)
        if searchable_attributes:
            index.update_searchable_attributes(searchable_attributes)
        if filterable_attributes:
            index.update_filterable_attributes(filterable_attributes)
        if sortable_attributes:
            index.update_sortable_attributes(sortable_attributes)

    def add_documents(self, uid: str, documents: list[dict[str, Any]]) -> None:
        """Add or update documents (upsert by primary key)."""
        if not documents:
            return
        self._client.index(uid).add_documents(documents)

    def delete_document(self, uid: str, doc_id: int | str) -> None:
        try:
            self._client.index(uid).delete_document(doc_id)
        except Exception:
            _logger.debug("Failed to delete document %s from index %s", doc_id, uid, exc_info=True)

    def search(
        self,
        uid: str,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        filter_expr: str | None = None,
        sort: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search an index. Returns Meilisearch response dict with 'hits',
        'estimatedTotalHits', etc."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if filter_expr:
            params["filter"] = filter_expr
        if sort:
            params["sort"] = sort
        return self._client.index(uid).search(query, params)


def get_search_client() -> MeilisearchClient | None:
    """Return the singleton Meilisearch client, or None if not configured."""
    global _client, _initialized
    if _initialized:
        return _client
    _initialized = True
    if not settings.meilisearch_url:
        _logger.info("MEILISEARCH_URL not set — using PG FTS only")
        return None
    try:
        _client = MeilisearchClient(settings.meilisearch_url, settings.meilisearch_api_key)
    except Exception:
        _logger.warning("Failed to connect to Meilisearch — falling back to PG FTS", exc_info=True)
        _client = None
    return _client


# ---------------------------------------------------------------------------
# Index definitions — called once at app startup (idempotent).
# ---------------------------------------------------------------------------

INDEX_QUESTIONS = "questions"
INDEX_TOPICS = "topics"
INDEX_KNOWLEDGE = "knowledge"
INDEX_RECRUITMENT = "recruitment"


def setup_indices() -> None:
    """Create and configure all search indices. Safe to call repeatedly."""
    client = get_search_client()
    if client is None:
        return

    client.ensure_index(INDEX_QUESTIONS, primary_key="id")
    client.configure_index(
        INDEX_QUESTIONS,
        searchable_attributes=["title", "content"],
        filterable_attributes=["groupId"],
        sortable_attributes=["createdAt", "updatedAt"],
    )

    client.ensure_index(INDEX_TOPICS, primary_key="id")
    client.configure_index(INDEX_TOPICS, searchable_attributes=["name"])

    client.ensure_index(INDEX_KNOWLEDGE, primary_key="id")
    client.configure_index(
        INDEX_KNOWLEDGE,
        searchable_attributes=["name", "description"],
        filterable_attributes=["teamId", "type"],
        sortable_attributes=["createdAt", "updatedAt"],
    )

    client.ensure_index(INDEX_RECRUITMENT, primary_key="id")
    client.configure_index(
        INDEX_RECRUITMENT,
        searchable_attributes=["title", "content"],
        filterable_attributes=["teamId", "status"],
        sortable_attributes=["createdAt"],
    )

    _logger.info("Meilisearch indices configured")
