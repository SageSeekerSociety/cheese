"""Meilisearch integration layer.

When ``MEILISEARCH_URL`` is configured, this service provides full-text
search with CJK tokenization, typo tolerance, and relevance ranking.

When not configured, callers fall back to PostgreSQL FTS.
"""


import logging
from typing import Any

from app.core.config import settings

_logger = logging.getLogger(__name__)

_client: "MeilisearchClient | None" = None
_initialized = False


class MeilisearchClient:
    def __init__(self, url: str, api_key: str) -> None:
        from meilisearch_python_sdk import AsyncClient

        self._client = AsyncClient(url, api_key)
        _logger.info("Meilisearch async client connected to %s", url)

    async def ensure_index(self, uid: str, *, primary_key: str = "id") -> None:
        try:
            await self._client.get_index(uid)
        except Exception:
            await self._client.create_index(uid, primary_key=primary_key)
            _logger.info("Created Meilisearch index '%s'", uid)

    async def configure_index(
        self,
        uid: str,
        *,
        searchable_attributes: list[str] | None = None,
        filterable_attributes: list[str] | None = None,
        sortable_attributes: list[str] | None = None,
    ) -> None:
        index = self._client.index(uid)
        if searchable_attributes:
            await index.update_searchable_attributes(searchable_attributes)
        if filterable_attributes:
            await index.update_filterable_attributes(filterable_attributes)
        if sortable_attributes:
            await index.update_sortable_attributes(sortable_attributes)

    async def add_documents(self, uid: str, documents: list[dict[str, Any]]) -> None:
        if not documents:
            return
        await self._client.index(uid).add_documents(documents)

    async def delete_document(self, uid: str, doc_id: int | str) -> None:
        try:
            await self._client.index(uid).delete_document(str(doc_id))
        except Exception:
            _logger.debug("Failed to delete document %s from index %s", doc_id, uid, exc_info=True)

    async def search(
        self,
        uid: str,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        filter_expr: str | None = None,
        sort: list[str] | None = None,
    ) -> dict[str, Any]:
        index = self._client.index(uid)
        result = await index.search(
            query,
            limit=limit,
            offset=offset,
            filter=filter_expr,
            sort=sort,
        )
        return {
            "hits": result.hits or [],
            "estimatedTotalHits": result.estimated_total_hits or 0,
        }


def get_search_client() -> MeilisearchClient | None:
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


INDEX_QUESTIONS = "questions"
INDEX_TOPICS = "topics"
INDEX_KNOWLEDGE = "knowledge"
INDEX_RECRUITMENT = "recruitment"


async def setup_indices() -> None:
    client = get_search_client()
    if client is None:
        return

    await client.ensure_index(INDEX_QUESTIONS, primary_key="id")
    await client.configure_index(
        INDEX_QUESTIONS,
        searchable_attributes=["title", "content"],
        filterable_attributes=["groupId"],
        sortable_attributes=["createdAt", "updatedAt"],
    )

    await client.ensure_index(INDEX_TOPICS, primary_key="id")
    await client.configure_index(INDEX_TOPICS, searchable_attributes=["name"])

    await client.ensure_index(INDEX_KNOWLEDGE, primary_key="id")
    await client.configure_index(
        INDEX_KNOWLEDGE,
        searchable_attributes=["name", "description"],
        filterable_attributes=["teamId", "type"],
        sortable_attributes=["createdAt", "updatedAt"],
    )

    await client.ensure_index(INDEX_RECRUITMENT, primary_key="id")
    await client.configure_index(
        INDEX_RECRUITMENT,
        searchable_attributes=["title", "content"],
        filterable_attributes=["teamId", "status"],
        sortable_attributes=["createdAt"],
    )

    _logger.info("Meilisearch indices configured")
