"""The credential a person's 芝士 holds, and the line between it and a room's.

A person's session reaches the platform for two things: the model, on that
person's own gateway key, and the tools that read what that person may see.
What it holds opens those two and nothing else — no room, no project, no
person's login — and nothing that opens a room or a project opens them.

* the tools run as the person the credential names, in a conversation that is
  still theirs, and refuse every other credential;
* the model route swaps in the person's own key, only while a question of that
  conversation is being answered;
* a room's endpoints, the admission route and a person's own API refuse it.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import redis as sync_redis
from redis.asyncio import from_url

from app.api.routes import assistant as assistant_route
from app.api.routes import llm_proxy
from app.core.config import settings
from app.core.sandbox_auth import (
    SANDBOX_TOKEN,
    mint_personal_credential,
    mint_project_agent_credential,
    mint_scoped_token,
)
from app.domain.assistant.models import AssistantGatewayKey
from app.domain.assistant.service import busy_key
from app.domain.task.models import TaskMembership
from tests.conftest import seed_user
from tests.integration.conftest import post_project
from tests.integration.test_assistant import _ledger_user, _start, _task
from tests.integration.test_llm_proxy import _FakeClient


@pytest.fixture
def wired(client, monkeypatch):
    monkeypatch.setattr(
        assistant_route, "async_session_factory", client.test_request_factory
    )
    monkeypatch.setattr(llm_proxy, "async_session_factory", client.test_request_factory)
    monkeypatch.setattr(
        llm_proxy, "get_redis_client", lambda: from_url(settings.redis_url)
    )
    monkeypatch.setattr(llm_proxy.settings, "anthropic_base_url", "http://pool:4000")
    monkeypatch.setattr(llm_proxy.httpx, "AsyncClient", _FakeClient)
    # The test client sends the platform's secret on every request; a session
    # has no such thing.
    monkeypatch.delitem(client.headers, "X-Cheese-Token")
    store = sync_redis.Redis.from_url(settings.redis_url)
    for key in store.scan_iter("assistant:busy:*"):
        store.delete(key)
    return store


def _person(client, handle: str) -> tuple[int, dict, str]:
    """A person with a conversation on a task: their id, login, conversation."""
    login = {"Authorization": f"Bearer {seed_user(client, handle)}"}
    user_id, _, _ = _ledger_user(client, handle)
    conversation = _start(client, _task(client, name=f"{handle} 在看的题"), login)
    return user_id, login, conversation


def _tool(client, token: str, name: str = "my_tasks", **headers):
    return client.post(
        f"/assistant/tools/{name}",
        json={},
        headers={"X-Cheese-Token": token, **headers},
    )


def _join(client, user_id: int, task_id: int) -> None:
    async def join() -> None:
        async with client.test_factory() as s:
            now = datetime.now(UTC)
            s.add(
                TaskMembership(
                    task_id=task_id,
                    member_id=user_id,
                    approved=0,
                    is_team=False,
                    created_at=now,
                    updated_at=now,
                )
            )
            await s.commit()

    asyncio.run(join())


def test_a_tool_runs_as_the_person_the_credential_names(client, wired):
    me, _, conversation = _person(client, "asker")
    other, _, _ = _person(client, "other")
    mine, theirs = _task(client, name="我领的那道"), _task(client, name="别人领的那道")
    _join(client, me, mine)
    _join(client, other, theirs)
    token = mint_personal_credential(user_id=me, conversation_id=conversation)

    r = _tool(client, token)

    assert r.status_code == 200, r.text
    text = r.json()["data"]["text"]
    assert "我领的那道" in text and "别人领的那道" not in text


def test_the_tools_take_no_other_credential(client, wired):
    me, login, conversation = _person(client, "asker")
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    expired = mint_personal_credential(
        user_id=me, conversation_id=conversation, ttl_s=-1
    )
    for token, extra in (
        ("", {}),
        (SANDBOX_TOKEN, {}),
        (mint_scoped_token(project_id=project, topic_id=str(uuid.uuid4())), {}),
        (mint_project_agent_credential(project_id=project, epoch=0), {}),
        (expired, {}),
        # A person's own login is not a session's credential.
        ("", login),
        (login["Authorization"].removeprefix("Bearer "), {}),
    ):
        assert _tool(client, token, **extra).status_code == 401, token


def test_a_credential_opens_only_its_own_persons_conversation(client, wired):
    me, _, _ = _person(client, "asker")
    _, _, theirs = _person(client, "other")

    forged = mint_personal_credential(user_id=me, conversation_id=theirs)
    gone = mint_personal_credential(user_id=me, conversation_id=str(uuid.uuid4()))

    assert _tool(client, forged).status_code == 403
    assert _tool(client, gone).status_code == 403


def _remember_key(client, user_id: int, key: str) -> None:
    async def save() -> None:
        async with client.test_factory() as s:
            s.add(AssistantGatewayKey(user_id=user_id, key=key))
            await s.commit()

    asyncio.run(save())


def test_the_model_is_reached_on_the_persons_key_only_while_they_are_answered(
    client, wired
):
    me, _, conversation = _person(client, "asker")
    _remember_key(client, me, "sk-person")
    token = mint_personal_credential(user_id=me, conversation_id=conversation)

    def ask():
        _FakeClient.seen = {}
        return client.post(
            "/llm/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            content=b'{"model":"m","messages":[]}',
        )

    # No question of the conversation is being answered: nothing to charge.
    assert ask().status_code == 403
    assert _FakeClient.seen == {}

    wired.set(busy_key(conversation), "1", ex=60)
    r = ask()

    assert r.status_code == 200, r.text
    sent = _FakeClient.seen["headers"]
    assert sent["authorization"] == "Bearer sk-person"
    assert token not in str(sent)


def test_a_room_and_a_person_do_not_open_each_others_doors(client, wired):
    me, _, conversation = _person(client, "asker")
    token = mint_personal_credential(user_id=me, conversation_id=conversation)
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    topic = client.post(
        "/topics",
        json={"project_id": project, "title": "Work"},
        headers={"X-Cheese-Token": SANDBOX_TOKEN},
    ).json()["data"]["id"]
    wired.set(busy_key(conversation), "1", ex=60)

    # A room's endpoint, as the agent in that room would call it.
    note = client.post(
        f"/topics/{topic}/note", json={"text": "hi"}, headers={"X-Cheese-Token": token}
    )
    assert note.status_code == 401
    # The admission the metering proxy asks for a room's turns.
    assert (
        client.post(
            "/llm/admission", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 401
    )
    # The person's own API, which takes their login.
    assert (
        client.get(
            f"/assistant/conversations/{conversation}",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 401
    )
    # And a room's credential is no person's on the model route either: it is
    # answered as the room it names, never on a person's key.
    _remember_key(client, me, "sk-person")
    room = mint_scoped_token(project_id=project, topic_id=topic)
    _FakeClient.seen = {}
    client.post(
        "/llm/v1/chat/completions",
        headers={"Authorization": f"Bearer {room}"},
        content=b"{}",
    )
    assert "sk-person" not in str(_FakeClient.seen)
