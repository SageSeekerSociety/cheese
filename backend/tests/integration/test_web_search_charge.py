"""A platform web search is admitted and paid for like the work that asked.

A room's agent searches on its project. A person's own 芝士 searches only
through its gate, as it reads a web page: a query can carry the person's work
off the platform, so without the pass the gate hands back the route refuses
it. Either way a payer out of credits is refused before the search service is
asked, and only a search that returned results costs anything — DeepSeek bills
$0.75 per 1,000 successful searches, 0.075 credits each.
"""

import asyncio
import uuid

import pytest

from app.api.routes import fetch as fetch_route
from app.domain.assistant import gate
from app.domain.fetch.search import Result
from app.domain.usage.ledger import Ledger, payer_for_person, payer_for_project
from tests.integration.conftest import free_plan_credits, room_agent_headers
from tests.integration.test_assistant import _ledger_user
from tests.integration.test_assistant_agent import _acting, _asked_in, _Gatekeeper
from tests.integration.test_message_edit import _room

ONE_SEARCH = 0.75 / 1000 / 0.01
PAGE = Result("T", "https://t.example/", "s")


@pytest.fixture
def deepseek(monkeypatch):
    """DeepSeek's search API, answering with ``found`` and keeping each query."""
    service = type("Service", (), {"found": [PAGE], "asked": []})()

    async def search(query, api_key, *, limit):
        service.asked.append(query)
        return list(service.found)

    monkeypatch.setattr(fetch_route.settings, "deepseek_api_key", "sk-test")
    monkeypatch.setattr(fetch_route, "search_web", search)
    return service


def _remaining(client, payer_of) -> float:
    async def read() -> float:
        async with client.test_factory() as s:
            return (await Ledger(s).balance(await payer_of(s))).credits_remaining

    return asyncio.run(read())


def _own(client, handle: str, said: str) -> tuple[int, dict]:
    """A person who said ``said`` to their 芝士, and the credential it acts with."""
    token = _acting(client, handle, _asked_in(client, handle, said))
    return _ledger_user(client, handle)[0], {"X-Cheese-Token": token}


def _through_gate(client, monkeypatch, headers: dict, query: str) -> dict:
    """Ask the gate to search ``query``; it lets the search through. Returns
    the call to make and the headers that carry its pass."""

    async def looks(session):
        return _Gatekeeper("放")

    monkeypatch.setattr(gate, "gatekeeper", looks)
    r = client.post(
        "/assistant/agent/act",
        json={"kind": "web_search", "query": query},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    got = r.json()["data"]
    assert got["done"] is True, got
    [call] = got["calls"]
    return {"call": call, "headers": {**headers, "X-Cheese-Pass": got["pass"]}}


def test_a_persons_cheese_without_the_gate_is_refused(client, deepseek):
    _, headers = _own(client, "alice", "搜一下 DeepSeek 的搜索接口")

    r = client.get("/web-search", params={"q": "DeepSeek 搜索接口"}, headers=headers)

    assert r.status_code == 403
    assert deepseek.asked == []


def test_through_the_gate_it_searches_and_the_person_pays(
    client, monkeypatch, deepseek
):
    user_id, headers = _own(client, "alice", "搜一下 DeepSeek 的搜索接口")
    before = _remaining(client, lambda s: payer_for_person(s, user_id))
    passed = _through_gate(client, monkeypatch, headers, "DeepSeek 搜索接口")

    r = client.get(passed["call"]["path"], headers=passed["headers"])

    assert r.status_code == 200, r.text
    assert r.json()["data"]["results"][0]["url"] == PAGE.url
    assert deepseek.asked == ["DeepSeek 搜索接口"]
    after = _remaining(client, lambda s: payer_for_person(s, user_id))
    assert before - after == pytest.approx(ONE_SEARCH)


def test_a_search_that_found_nothing_costs_nothing(client, monkeypatch, deepseek):
    deepseek.found = []
    user_id, headers = _own(client, "alice", "搜一下这个冷门词")
    before = _remaining(client, lambda s: payer_for_person(s, user_id))
    passed = _through_gate(client, monkeypatch, headers, "冷门词")

    r = client.get(passed["call"]["path"], headers=passed["headers"])

    assert r.status_code == 200, r.text
    assert r.json()["data"]["results"] == []
    assert _remaining(client, lambda s: payer_for_person(s, user_id)) == before


def test_with_no_credits_left_the_search_service_is_not_asked(
    client, monkeypatch, deepseek
):
    free_plan_credits(client, 0.0)
    _, headers = _own(client, "alice", "搜一下 DeepSeek 的搜索接口")
    passed = _through_gate(client, monkeypatch, headers, "DeepSeek 搜索接口")

    r = client.get(passed["call"]["path"], headers=passed["headers"])

    assert r.status_code == 429 and "额度已用完" in r.json()["message"]
    assert deepseek.asked == []


def test_a_rooms_agent_searches_directly_on_its_project(client, deepseek):
    room, project = _room(client)
    before = _remaining(client, lambda s: payer_for_project(s, uuid.UUID(project)))

    r = client.get(
        "/web-search",
        params={"q": "栅格布局", "topic": room, "project": project},
        headers=room_agent_headers(client, room),
    )

    assert r.status_code == 200, r.text
    assert deepseek.asked == ["栅格布局"]
    after = _remaining(client, lambda s: payer_for_project(s, uuid.UUID(project)))
    assert before - after == pytest.approx(ONE_SEARCH)
