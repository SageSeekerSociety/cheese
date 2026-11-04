"""A platform web search is made on the gateway key the work's model calls use.

The gateway prices the search on that key and the key's spend is drained into
credits with its model calls, so whoever pays for the work's model calls pays
for its searches: a room's agent's on its project's key, a person's own 芝士's
on that person's key. A person's 芝士 searches without its gate: the query
goes only to DeepSeek, which already holds the conversation as the model's
provider. A project out of credits is refused before the gateway is asked,
as its model calls are; a caller with no key of its own gets no search.
"""

import asyncio
import inspect

import pytest

from app.api.routes import fetch as fetch_route
from app.domain.agent.chat import ChatService
from app.domain.assistant.models import AssistantGatewayKey
from app.domain.fetch.search import Result, SearchRefused
from tests.conftest import seed_user
from tests.integration.conftest import free_plan_credits, room_agent_headers
from tests.integration.test_assistant import _ledger_user
from tests.integration.test_assistant_agent import _acting, _asked_in
from tests.integration.test_message_edit import _room
from tests.integration.test_project_agent_credential import _issued_token, _project

PAGE = Result("T", "https://t.example/", "s")
GATEWAY = "http://gateway.test"


@pytest.fixture
def gateway(monkeypatch):
    """The gateway's search tool, answering with ``found`` and keeping which
    key each query was made on; project keys read ``sk-project-<id>``."""
    service = type("Service", (), {"found": [PAGE], "asked": [], "refuse": False})()

    async def search(query, *, gateway, key, limit):
        assert gateway == GATEWAY
        if service.refuse:
            raise SearchRefused()
        service.asked.append((query, key))
        return list(service.found)

    async def project_key(
        self,
        project_id,
        *,
        dream=False,
        task_id=None,
        credit_owner_handle=None,
        machine_conversation_id=None,
        budget_target=None,
    ):
        return f"sk-project-{project_id}"

    def shape(fn):
        return [(p.name, p.kind) for p in inspect.signature(fn).parameters.values()]

    assert shape(project_key) == shape(ChatService.project_gateway_key)
    monkeypatch.setattr(ChatService, "project_gateway_key", project_key)
    monkeypatch.setattr(fetch_route.settings, "anthropic_base_url", GATEWAY)
    monkeypatch.setattr(fetch_route, "search_web", search)
    return service


def _own(client, handle: str, said: str) -> tuple[int, dict]:
    """A person who said ``said`` to their 芝士 and holds a gateway key for it,
    and the credential the 芝士 acts with."""
    token = _acting(client, handle, _asked_in(client, handle, said))
    user_id = _ledger_user(client, handle)[0]

    async def remember() -> None:
        async with client.test_factory() as s:
            s.add(AssistantGatewayKey(user_id=user_id, key=f"sk-person-{user_id}"))
            await s.commit()

    asyncio.run(remember())
    return user_id, {"Authorization": f"Bearer {token}"}


def test_a_persons_cheese_searches_on_their_own_key_without_its_gate(client, gateway):
    user_id, headers = _own(client, "alice", "搜一下 DeepSeek 的搜索接口")

    r = client.get("/web-search", params={"q": "DeepSeek 搜索接口"}, headers=headers)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["results"][0]["url"] == PAGE.url
    assert gateway.asked == [("DeepSeek 搜索接口", f"sk-person-{user_id}")]


def test_a_rooms_agent_searches_on_its_projects_key(client, gateway):
    room, project = _room(client)

    r = client.get(
        "/web-search",
        params={"q": "栅格布局", "topic": room, "project": project},
        headers=room_agent_headers(client, room),
    )

    assert r.status_code == 200, r.text
    assert gateway.asked == [("栅格布局", f"sk-project-{project}")]


def test_an_agent_holding_the_projects_credential_searches_on_its_key(client, gateway):
    project = _project(client, "alice")
    headers = {"Authorization": f"Bearer {_issued_token(client, project)}"}

    r = client.get(
        "/web-search", params={"q": "栅格布局", "project": project}, headers=headers
    )

    assert r.status_code == 200, r.text
    assert gateway.asked == [("栅格布局", f"sk-project-{project}")]


def test_a_project_out_of_credits_is_refused_before_the_gateway_is_asked(
    client, gateway
):
    free_plan_credits(client, 0.0)
    project = _project(client, "alice")
    headers = {"Authorization": f"Bearer {_issued_token(client, project)}"}

    r = client.get(
        "/web-search", params={"q": "栅格布局", "project": project}, headers=headers
    )

    assert r.status_code == 429 and "额度已用完" in r.json()["message"]
    assert gateway.asked == []


def test_a_key_the_gateway_refuses_for_its_budget_is_told_so(client, gateway):
    gateway.refuse = True
    room, project = _room(client)

    r = client.get(
        "/web-search",
        params={"q": "栅格布局", "topic": room, "project": project},
        headers=room_agent_headers(client, room),
    )

    assert r.status_code == 429, r.text
    assert "额度已经用完" in r.json()["message"]


def test_a_signed_in_person_has_no_key_to_search_on(client, gateway):
    login = {"Authorization": f"Bearer {seed_user(client, 'bob')}"}

    r = client.get("/web-search", params={"q": "栅格布局"}, headers=login)

    assert r.status_code == 403, r.text
    assert gateway.asked == []
