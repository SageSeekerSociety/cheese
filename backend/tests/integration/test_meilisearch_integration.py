"""Integration tests for Meilisearch search.

These tests require the dedicated Meilisearch service configured by their test
entry. The rest of the integration suite deliberately keeps the production
fallback, so this module uses its own test-only URL.

Run locally:
    CHEESEX_TEST_MEILISEARCH_URL=http://127.0.0.1:7700 \
    uv run pytest tests/integration/test_meilisearch_integration.py -v
"""

import os
import time
from collections.abc import Generator
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.domain.questions.repositories import QuestionRepository
from tests.integration.conftest import UserCreator

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal


def _wait_for_question_ids(
    api_client: TestClient,
    headers: dict[str, str],
    expected: dict[str, int],
    *,
    timeout_s: float = 10,
    sleep=time.sleep,
) -> None:
    """Wait until each new row is returned through the production search API."""
    deadline = time.monotonic() + timeout_s
    pending = dict(expected)
    while pending:
        for query, expected_id in list(pending.items()):
            response = api_client.get(
                "/questions",
                params={"q": query, "page_size": 10},
                headers=headers,
            )
            assert response.status_code == 200, response.text
            returned = {
                question["id"] for question in response.json()["data"]["questions"]
            }
            if expected_id in returned:
                del pending[query]
        if not pending:
            return
        if time.monotonic() >= deadline:
            pytest.fail(
                "Meilisearch did not return the newly indexed question IDs: "
                f"{sorted(pending.values())}"
            )
        sleep(0.05)


@pytest.fixture(scope="module")
def _ensure_indices(_portal: "BlockingPortal") -> Generator[None]:
    """Point only this module at the dedicated service and configure its index."""
    from app.domain.search import meilisearch_service

    url = os.environ.get("CHEESEX_TEST_MEILISEARCH_URL")
    if not url:
        raise RuntimeError(
            "CHEESEX_TEST_MEILISEARCH_URL is required: start the dedicated "
            "Meilisearch test service before running this module"
        )
    previous = (settings.meilisearch_url, settings.meilisearch_api_key)
    settings.meilisearch_url = url
    settings.meilisearch_api_key = os.environ.get(
        "CHEESEX_TEST_MEILISEARCH_API_KEY", "test-key"
    )
    meilisearch_service._client = None
    meilisearch_service._initialized = False
    _portal.call(meilisearch_service.setup_indices)
    if meilisearch_service.get_search_client() is None:
        raise RuntimeError(f"Meilisearch test service is unavailable at {url}")
    try:
        yield
    finally:
        meilisearch_service._client = None
        meilisearch_service._initialized = False
        settings.meilisearch_url, settings.meilisearch_api_key = previous


class TestMeilisearchQuestionSearch:
    """Test that questions created via API are searchable via Meilisearch."""

    @pytest.fixture
    def search_setup(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        _ensure_indices,
        monkeypatch,
    ) -> dict:
        async def reject_postgres_fallback(*args, **kwargs):
            pytest.fail("search fell back to PostgreSQL instead of using Meilisearch")

        monkeypatch.setattr(QuestionRepository, "search", reject_postgres_fallback)
        creator = user_client.create_user()
        creator.token = user_client.login(
            api_client, creator.username, creator.password
        )
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

        result = {
            "creator": creator,
            "headers": h,
            "q1_id": q1.json()["data"].get("id")
            or q1.json()["data"].get("question", {}).get("id"),
            "q2_id": q2.json()["data"].get("id")
            or q2.json()["data"].get("question", {}).get("id"),
            "q3_id": q3.json()["data"].get("id")
            or q3.json()["data"].get("question", {}).get("id"),
        }
        _wait_for_question_ids(
            api_client,
            h,
            {
                "深度学习": result["q1_id"],
                "PostgreSQL": result["q2_id"],
                "React Vue": result["q3_id"],
            },
        )
        return result

    def test_chinese_keyword_search(self, search_setup: dict, api_client: TestClient):
        """Search for Chinese keywords returns relevant results."""
        resp = api_client.get(
            "/questions",
            params={"q": "深度学习", "page_size": 10},
            headers=search_setup["headers"],
        )
        assert resp.status_code == 200
        questions = resp.json()["data"]["questions"]
        titles = [q["title"] for q in questions]
        assert any("深度学习" in t for t in titles), (
            f"Expected '深度学习' in results: {titles}"
        )

    def test_typo_tolerant_search(self, search_setup: dict, api_client: TestClient):
        """Meilisearch typo tolerance finds results despite typos."""
        resp = api_client.get(
            "/questions",
            params={"q": "PostgreSXL", "page_size": 10},
            headers=search_setup["headers"],
        )
        assert resp.status_code == 200
        questions = resp.json()["data"]["questions"]
        titles = [q["title"] for q in questions]
        assert any("PostgreSQL" in t for t in titles), f"Typo search failed: {titles}"

    def test_partial_chinese_search(self, search_setup: dict, api_client: TestClient):
        """Partial Chinese term still finds relevant results."""
        resp = api_client.get(
            "/questions",
            params={"q": "深度", "page_size": 10},
            headers=search_setup["headers"],
        )
        assert resp.status_code == 200
        questions = resp.json()["data"]["questions"]
        titles = [q["title"] for q in questions]
        assert any("深度学习" in t for t in titles), f"Partial search failed: {titles}"

    def test_no_results_for_unrelated_term(
        self, search_setup: dict, api_client: TestClient
    ):
        """Unrelated search term returns empty."""
        resp = api_client.get(
            "/questions",
            params={"q": "量子力学", "page_size": 10},
            headers=search_setup["headers"],
        )
        assert resp.status_code == 200
        questions = resp.json()["data"]["questions"]
        assert questions == []


def test_readiness_waits_for_every_new_question_id() -> None:
    class Response:
        status_code = 200
        text = ""

        def __init__(self, ids: list[int]) -> None:
            self._ids = ids

        def json(self) -> dict:
            return {"data": {"questions": [{"id": value} for value in self._ids]}}

    class DelayedSecondQuestion:
        def __init__(self) -> None:
            self.calls = {"first": 0, "second": 0}

        def get(self, _path, *, params, headers):
            del headers
            query = params["q"]
            self.calls[query] += 1
            if query == "second" and self.calls[query] < 3:
                return Response([])
            return Response([1 if query == "first" else 2])

    client = DelayedSecondQuestion()
    _wait_for_question_ids(
        client,  # type: ignore[arg-type]
        {},
        {"first": 1, "second": 2},
        sleep=lambda _seconds: None,
    )
    assert client.calls == {"first": 1, "second": 3}
