"""Meilisearch-first search with PG FTS fallback.

Repositories call ``meilisearch_search_ids`` before their PG FTS path.
If Meilisearch is configured and returns hits, the repo uses the hit
IDs to fetch full rows from PG (preserving Meilisearch's relevance
order). If not configured or the call fails, returns None so the
caller falls through to PG FTS.
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger(__name__)


async def meilisearch_search_ids(
    index_uid: str,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    filter_expr: str | None = None,
    sort: list[str] | None = None,
) -> tuple[list[int], int] | None:
    from app.domain.search.meilisearch_service import get_search_client

    client = get_search_client()
    if client is None or not query or not query.strip():
        return None

    try:
        result: dict[str, Any] = await client.search(
            index_uid,
            query.strip(),
            limit=limit,
            offset=offset,
            filter_expr=filter_expr,
            sort=sort,
        )
        hits = result.get("hits") or []
        ids = [int(h["id"]) for h in hits if "id" in h]
        total = result.get("estimatedTotalHits", len(ids))
        return ids, total
    except Exception:
        _logger.debug("Meilisearch search failed, falling back to PG FTS", exc_info=True)
        return None


async def index_document(index_uid: str, doc: dict) -> None:
    """Index a single document (fire-and-forget, best-effort)."""
    from app.domain.search.meilisearch_service import get_search_client

    client = get_search_client()
    if client is None:
        return
    try:
        await client.add_documents(index_uid, [doc])
    except Exception:
        _logger.debug("Failed to index document in %s", index_uid, exc_info=True)
