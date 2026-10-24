"""Searching the web for sessions whose model has no search tool.

Codex and pi have none, and Claude Code's WebSearch returns nothing on an
upstream that does not run it, so agents there opened search engines' result
pages with `cheese_web(action=fetch)` — 153 times in two weeks — and got block pages back.
The search is asked of the gateway on the caller's own key; these tests pin
what the gateway is asked and what the agent reads.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.routes import fetch as fetch_route
from app.core.errors import SystemBusyError
from app.domain.fetch import search


def _gateway(monkeypatch, handler):
    original = httpx.AsyncClient

    def with_mock(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr(search.httpx, "AsyncClient", with_mock)


async def test_the_gateway_is_asked_on_the_callers_key_and_answers_best_first(
    monkeypatch,
):
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(
            200,
            json={
                "object": "search",
                "results": [
                    {
                        "url": "https://a.example/",
                        "title": " A ",
                        "snippet": "the summary",
                        "date": "2026-10-17T00:00:00Z",
                    },
                    {"url": "https://a.example/", "title": "A", "snippet": "again"},
                    {"url": "https://b.example/", "title": "B", "snippet": "only"},
                    {"url": "https://c.example/", "title": "C", "snippet": "third"},
                ],
            },
        )

    _gateway(monkeypatch, handler)

    found = await search.search(
        "芝士 平台", gateway="http://gw.test/", key="sk-work", limit=2
    )

    assert found == [
        search.Result("A", "https://a.example/", "the summary", "2026-10-17T00:00:00Z"),
        search.Result("B", "https://b.example/", "only", None),
    ]
    request = asked[0]
    assert (request.method, str(request.url)) == (
        "POST",
        "http://gw.test/v1/search/deepseek-search",
    )
    assert request.headers["authorization"] == "Bearer sk-work"
    assert json.loads(request.content) == {"query": "芝士 平台", "max_results": 2}


async def test_a_key_past_its_budget_is_refused_not_failed(monkeypatch):
    _gateway(
        monkeypatch,
        lambda request: httpx.Response(
            429,
            json={
                "error": {
                    "type": "budget_exceeded",
                    "message": "Budget has been exceeded!",
                }
            },
        ),
    )
    with pytest.raises(search.SearchRefused):
        await search.search("q", gateway="http://gw.test", key="sk-work")


async def test_a_gateway_that_fails_says_so(monkeypatch):
    _gateway(monkeypatch, lambda request: httpx.Response(502, text="bad gateway"))
    with pytest.raises(search.SearchUnavailable, match="HTTP 502"):
        await search.search("q", gateway="http://gw.test", key="sk-work")


async def test_a_deployment_without_a_gateway_refuses_plainly(monkeypatch):
    """No gateway is an answer the agent can act on, not an empty list that
    reads as "nothing on the web"."""
    monkeypatch.setattr(fetch_route.settings, "anthropic_base_url", None)
    with pytest.raises(SystemBusyError, match="网页搜索用不了"):
        await fetch_route.web_search(
            None,
            SimpleNamespace(require_verified_caller=AsyncMock()),
            None,
            q="anything",
        )
