"""The answer a prompted fetch draws from its page is a model call made on the
gateway key the work's model calls use.

So whoever pays for the work's model calls pays for it: a room's agent's on
its project's key, a person's own 芝士's on that person's key, with the model
that key may call. Reading the page itself costs the payer nothing, so a
caller with no key, or a payer out of credits, still gets the page — whole,
with no answer drawn from it — and no model is asked. A fetch without a
prompt makes no model call at all.
"""

import asyncio
import inspect

import pytest

from app.api.routes import fetch as fetch_route
from app.domain.agent.chat import ChatService
from app.domain.assistant import gate
from app.domain.assistant.models import AssistantGatewayKey
from app.domain.fetch.service import FetchOutcome
from tests.conftest import seed_user
from tests.integration.conftest import free_plan_credits, room_agent_headers
from tests.integration.test_assistant_agent import _Gatekeeper
from tests.integration.test_message_edit import _room
from tests.integration.test_project_agent_credential import _issued_token, _project
from tests.integration.test_web_search_is_made_on_the_works_key import _own
from tests.support import assistant_actions

GATEWAY = "http://gateway.test"
URL = "https://t.example/page"
PAGE = "the whole page " * 20


@pytest.fixture
def reader(monkeypatch):
    """The page read: answers from the page when handed a key to answer on,
    and keeps the (gateway, key, model) each answer was asked on."""
    service = type("Service", (), {"answered_on": []})()

    async def fetch(url, prompt=None, *, distill=None, **_):
        if prompt and distill:
            service.answered_on.append(distill)
            return FetchOutcome(url, True, "the answer", "plain-http", [], True)
        return FetchOutcome(url, True, PAGE, "plain-http", [])

    async def project_key(
        self,
        project_id,
        *,
        dream=False,
        task_id=None,
        credit_owner_handle=None,
        machine_conversation_id=None,
    ):
        return f"sk-project-{project_id}"

    def shape(fn):
        return [(p.name, p.kind) for p in inspect.signature(fn).parameters.values()]

    assert shape(project_key) == shape(ChatService.project_gateway_key)
    monkeypatch.setattr(ChatService, "project_gateway_key", project_key)
    monkeypatch.setattr(fetch_route.settings, "anthropic_base_url", GATEWAY)
    monkeypatch.setattr(fetch_route.settings, "agent_model", "the-model-we-run")
    monkeypatch.setattr(fetch_route.settings, "agent_haiku_model", "an-alias-only")
    monkeypatch.setattr(fetch_route.settings, "assistant_model", "the-assistant-model")
    monkeypatch.setattr(fetch_route, "fetch_url", fetch)
    return service


def _minted_for(client, user_id: int, model: str) -> None:
    async def mint() -> None:
        async with client.test_factory() as s:
            row = await s.get(AssistantGatewayKey, user_id)
            row.model = model
            await s.commit()

    asyncio.run(mint())


def test_a_rooms_agent_is_answered_on_its_projects_key_with_the_model_it_runs(
    client, reader
):
    room, project = _room(client)

    r = client.post(
        "/fetch",
        json={"url": URL, "prompt": "讲了什么", "topic": room, "project": project},
        headers=room_agent_headers(client, room),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["distilled"] is True
    assert reader.answered_on == [
        (GATEWAY, f"sk-project-{project}", "the-model-we-run")
    ]


def test_a_persons_cheese_is_answered_on_their_own_key_through_its_gate(
    client, reader, monkeypatch
):
    user_id, headers = _own(client, "alice", "帮我看看这一页讲了什么")
    _minted_for(client, user_id, "the-model-the-key-was-minted-for")

    async def looks(session):
        return _Gatekeeper("放")

    monkeypatch.setattr(gate, "gatekeeper", looks)
    token = headers["Authorization"].removeprefix("Bearer ")
    [r] = assistant_actions.take(
        client, token, {"kind": "fetch", "url": URL, "prompt": "讲了什么"}
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["text"] == "the answer"
    # Its key is good for the one model it was minted for, so that is what it
    # asks — even when the assistant model changed while this answer ran.
    assert reader.answered_on == [
        (GATEWAY, f"sk-person-{user_id}", "the-model-the-key-was-minted-for")
    ]


def test_a_project_out_of_credits_gets_the_page_and_no_model_is_asked(client, reader):
    free_plan_credits(client, 0.0)
    project = _project(client, "alice")
    headers = {"Authorization": f"Bearer {_issued_token(client, project)}"}

    r = client.post(
        "/fetch",
        json={"url": URL, "prompt": "讲了什么", "project": project},
        headers=headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["text"] == PAGE
    assert r.json()["data"]["distilled"] is False
    assert reader.answered_on == []


def test_a_signed_in_person_gets_the_page_and_no_model_is_asked(client, reader):
    login = {"Authorization": f"Bearer {seed_user(client, 'bob')}"}

    r = client.post("/fetch", json={"url": URL, "prompt": "讲了什么"}, headers=login)

    assert r.status_code == 200, r.text
    assert r.json()["data"]["text"] == PAGE
    assert reader.answered_on == []


def test_a_fetch_without_a_prompt_asks_no_model(client, reader):
    room, project = _room(client)

    r = client.post(
        "/fetch",
        json={"url": URL, "topic": room, "project": project},
        headers=room_agent_headers(client, room),
    )

    assert r.status_code == 200, r.text
    assert r.json()["data"]["text"] == PAGE
    assert reader.answered_on == []
