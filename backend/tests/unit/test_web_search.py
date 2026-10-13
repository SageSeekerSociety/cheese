"""Searching the web for sessions whose model has no search tool.

Codex and pi have none, and Claude Code's WebSearch fails on a model that is not
Claude, so agents there opened search engines' result pages with `cheese_fetch`
— 153 times in two weeks — and got block pages back. These tests pin what the
search service returns and what the agent reads.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.routes import fetch as fetch_route
from app.core.errors import SystemBusyError
from app.domain.fetch import search


def _searx(monkeypatch, handler):
    original = httpx.AsyncClient

    def with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(search.httpx, "AsyncClient", with_mock)


async def test_results_come_back_best_first_without_duplicates(monkeypatch):
    asked: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://a.example/", "title": " A ", "content": "first"},
                    # The same page through a second engine.
                    {"url": "https://a.example/", "title": "A", "content": "again"},
                    {"url": "https://b.example/", "title": "B", "content": "second"},
                    {"url": "https://c.example/", "title": "C", "content": "third"},
                ]
            },
        )

    _searx(monkeypatch, handler)

    found = await search.search("芝士 平台", "http://searx:8080/", limit=2)

    assert [r.url for r in found] == ["https://a.example/", "https://b.example/"]
    assert found[0] == search.Result("A", "https://a.example/", "first")
    assert asked[0].path == "/search"
    assert asked[0].params["q"] == "芝士 平台" and asked[0].params["format"] == "json"


async def test_a_search_service_that_fails_says_so(monkeypatch):
    _searx(monkeypatch, lambda request: httpx.Response(429, text="too many"))
    with pytest.raises(search.SearchUnavailable, match="HTTP 429"):
        await search.search("q", "http://searx:8080")


async def test_a_deployment_without_search_refuses_plainly(monkeypatch):
    """No service configured is an answer the agent can act on, not an empty list
    that reads as "nothing on the web"."""
    monkeypatch.setattr(fetch_route.settings, "fetch_search_endpoint", None)
    with pytest.raises(SystemBusyError, match="No web search service"):
        await fetch_route.web_search(
            SimpleNamespace(require_verified_caller=AsyncMock()), q="anything"
        )


async def test_the_route_checks_the_caller_against_the_room_it_names(monkeypatch):
    """A sandbox token verifies only against the room it was minted for, so the
    route passes the room on, as `/fetch` does."""
    import uuid

    monkeypatch.setattr(fetch_route.settings, "fetch_search_endpoint", "http://searx")

    async def found(query, endpoint, *, limit):
        return [search.Result("T", "https://t.example/", "s")]

    monkeypatch.setattr(fetch_route, "search_web", found)
    actor = SimpleNamespace(require_verified_caller=AsyncMock())
    topic, project = uuid.uuid4(), uuid.uuid4()

    body = await fetch_route.web_search(actor, q="q", topic=topic, project=project)

    actor.require_verified_caller.assert_awaited_once_with(
        topic_id=topic, project_id=project
    )
    assert body["data"]["results"] == [
        {"title": "T", "url": "https://t.example/", "snippet": "s"}
    ]
