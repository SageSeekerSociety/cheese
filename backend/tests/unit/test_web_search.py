"""Searching the web for sessions whose model has no search tool.

Codex and pi have none, and Claude Code's WebSearch returns nothing on an
upstream that does not run it, so agents there opened search engines' result
pages with `cheese_fetch` — 153 times in two weeks — and got block pages back.
These tests pin what the search service is asked and what the agent reads.
"""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.routes import fetch as fetch_route
from app.core.errors import SystemBusyError
from app.domain.fetch import search


def _deepseek(monkeypatch, handler):
    original = httpx.AsyncClient

    def with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(search.httpx, "AsyncClient", with_mock)


async def test_results_come_back_best_first_with_the_summary_written_for_models(
    monkeypatch,
):
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://a.example/",
                        "title": " A ",
                        "snippet": "short",
                        "summary": "the longer summary",
                        "date": "2026-10-17T00:00:00Z",
                    },
                    {"url": "https://a.example/", "title": "A", "summary": "again"},
                    {"url": "https://b.example/", "title": "B", "snippet": "only"},
                    {"url": "https://c.example/", "title": "C", "summary": "third"},
                ]
            },
        )

    _deepseek(monkeypatch, handler)

    found = await search.search("芝士 平台", "sk-test", limit=2)

    assert found == [
        search.Result(
            "A", "https://a.example/", "the longer summary", "2026-10-17T00:00:00Z"
        ),
        search.Result("B", "https://b.example/", "only", None),
    ]
    request = asked[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.deepseek.com/v1/tools/search"
    assert request.headers["authorization"] == "Bearer sk-test"
    assert json.loads(request.content) == {"query": "芝士 平台", "count": 2}


async def test_a_search_service_that_fails_says_so(monkeypatch):
    _deepseek(monkeypatch, lambda request: httpx.Response(429, text="too many"))
    with pytest.raises(search.SearchUnavailable, match="HTTP 429"):
        await search.search("q", "sk-test")


async def test_a_deployment_without_a_key_refuses_plainly(monkeypatch):
    """No key is an answer the agent can act on, not an empty list that reads
    as "nothing on the web"."""
    monkeypatch.setattr(fetch_route.settings, "deepseek_api_key", None)
    with pytest.raises(SystemBusyError, match="没有配置网页搜索服务"):
        await fetch_route.web_search(
            None, SimpleNamespace(require_verified_caller=AsyncMock()), q="anything"
        )


async def test_the_route_checks_the_caller_against_the_room_it_names(monkeypatch):
    """A sandbox token verifies only against the room it was minted for, so the
    route passes the room on, as `/fetch` does."""
    monkeypatch.setattr(fetch_route.settings, "deepseek_api_key", "sk-test")

    async def found(query, api_key, *, limit):
        return [search.Result("T", "https://t.example/", "s")]

    monkeypatch.setattr(fetch_route, "search_web", found)
    # The global sandbox override: verified, but naming nobody to charge.
    actor = SimpleNamespace(
        require_verified_caller=AsyncMock(
            return_value=SimpleNamespace(via="anonymous", user_id=None)
        ),
        delegation=lambda: None,
        credential_project=lambda: None,
    )
    topic, project = uuid.uuid4(), uuid.uuid4()

    body = await fetch_route.web_search(
        None, actor, q="q", topic=topic, project=project
    )

    actor.require_verified_caller.assert_awaited_once_with(
        topic_id=topic, project_id=project
    )
    assert body["data"]["results"] == [
        {"title": "T", "url": "https://t.example/", "snippet": "s", "date": None}
    ]
