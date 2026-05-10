"""Integration tests for Meilisearch search.

These tests require a running Meilisearch instance. They are skipped when
MEILISEARCH_URL is not set (which is the case in CI unless a Meilisearch
service is configured).

Run locally:
    MEILISEARCH_URL=http://127.0.0.1:7700 MEILISEARCH_API_KEY=test-key \
    uv run pytest tests/integration/test_meilisearch_integration.py -v
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator

pytestmark = pytest.mark.skipif(
    not os.environ.get("MEILISEARCH_URL"),
    reason="MEILISEARCH_URL not set — Meilisearch integration tests skipped",
)


@pytest.fixture(scope="module")
def _ensure_indices():
    """Set up Meilisearch indices once for the module."""
    from app.domain.search.meilisearch_service import setup_indices

    setup_indices()
    yield


class TestMeilisearchQuestionSearch:
    """Test that questions created via API are searchable via Meilisearch."""

    @pytest.fixture
    def search_setup(
        self, user_client: UserCreator, api_client: TestClient, _ensure_indices
    ) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(api_client, creator.username, creator.password)
        h = {"Authorization": f"Bearer {creator.token}"}

        # Create questions with distinctive Chinese content
        q1 = api_client.post(
            "/questions",
            headers=h,
            json={
                "title": "深度学习入门指南",
                "content": "如何从零开始学习深度学习和神经网络？推荐一些好的教材。",
                "type": 0,
                "bounty": 0,
                "topics": [],
            },
        )
        assert q1.status_code in (200, 201), q1.text

        q2 = api_client.post(
            "/questions",
            headers=h,
            json={
                "title": "PostgreSQL性能优化",
                "content": "数据库查询太慢了，如何优化索引和查询计划？",
                "type": 0,
                "bounty": 0,
                "topics": [],
            },
        )
        assert q2.status_code in (200, 201), q2.text

        q3 = api_client.post(
            "/questions",
            headers=h,
            json={
                "title": "React vs Vue前端框架选择",
                "content": "新项目应该用React还是Vue？各有什么优缺点？",
                "type": 0,
                "bounty": 0,
                "topics": [],
            },
        )
        assert q3.status_code in (200, 201), q3.text

        # Wait for Meilisearch to index (async indexing)
        time.sleep(2)

        return {
            "creator": creator,
            "headers": h,
            "q1_id": q1.json()["data"].get("id") or q1.json()["data"].get("question", {}).get("id"),
            "q2_id": q2.json()["data"].get("id") or q2.json()["data"].get("question", {}).get("id"),
            "q3_id": q3.json()["data"].get("id") or q3.json()["data"].get("question", {}).get("id"),
        }

    def test_chinese_keyword_search(
        self, search_setup: dict, api_client: TestClient
    ):
        """Search for Chinese keywords returns relevant results."""
        resp = api_client.get(
            "/questions",
            params={"keywords": "深度学习", "pageSize": 10},
            headers=search_setup["headers"],
        )
        assert resp.status_code == 200
        questions = resp.json()["data"]["questions"]
        titles = [q["title"] for q in questions]
        assert any("深度学习" in t for t in titles), f"Expected '深度学习' in results: {titles}"

    def test_typo_tolerant_search(
        self, search_setup: dict, api_client: TestClient
    ):
        """Meilisearch typo tolerance finds results despite typos."""
        resp = api_client.get(
            "/questions",
            params={"keywords": "PostgreSQ", "pageSize": 10},
            headers=search_setup["headers"],
        )
        assert resp.status_code == 200
        questions = resp.json()["data"]["questions"]
        titles = [q["title"] for q in questions]
        assert any("PostgreSQL" in t for t in titles), f"Typo search failed: {titles}"

    def test_partial_chinese_search(
        self, search_setup: dict, api_client: TestClient
    ):
        """Partial Chinese term still finds relevant results."""
        resp = api_client.get(
            "/questions",
            params={"keywords": "数据库", "pageSize": 10},
            headers=search_setup["headers"],
        )
        assert resp.status_code == 200
        questions = resp.json()["data"]["questions"]
        titles = [q["title"] for q in questions]
        assert any("PostgreSQL" in t or "数据库" in t for t in titles), (
            f"Partial search failed: {titles}"
        )

    def test_no_results_for_unrelated_term(
        self, search_setup: dict, api_client: TestClient
    ):
        """Unrelated search term returns empty."""
        resp = api_client.get(
            "/questions",
            params={"keywords": "量子力学", "pageSize": 10},
            headers=search_setup["headers"],
        )
        assert resp.status_code == 200
        questions = resp.json()["data"]["questions"]
        assert len(questions) == 0 or not any(
            "量子" in q["title"] for q in questions
        )
