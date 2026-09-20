"""Who answers is who was addressed.

A room is a collaboration space: it holds members, several of which may be
agents. Addressing one is how a person picks who answers, and it has to be how
a room picks too — the alternative is the room pointing at an agent, and then
@-ing the second teammate runs the first one's turn (#1192).
"""

import uuid

from app.domain.identity.handles import agent_instance_handle
from tests.integration.conftest import session_auth_headers


def _project(client, name: str = "Two teammates") -> str:
    r = client.post("/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _agent(client, project: str, handle: str, display: str) -> dict:
    r = client.post(
        f"/projects/{project}/agents",
        json={"handle": handle, "display_name": display},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _seat(client, topic: str, handle: str, actor: str = "alice") -> None:
    r = client.post(
        f"/topics/{topic}/members",
        json={"handle": handle, "role": "member", "actor": actor},
        headers=session_auth_headers(actor),
    )
    assert r.status_code == 200, r.text


def test_addressing_the_second_teammate_addresses_the_second_teammate(client):
    project = _project(client)
    first = _agent(client, project, "planner", "规划师")
    second = _agent(client, project, "reviewer", "审稿人")
    topic = client.post(
        "/topics",
        json={"project_id": project, "title": "Room", "created_by": "alice"},
    ).json()["data"]["id"]

    for made in (first, second):
        _seat(client, topic, agent_instance_handle(made["id"]))

    # The platform's record of who a message is for is what decides whose turn
    # runs: the turn resolves its agent from this and nothing else.
    async def recipient_of(content: str) -> dict:
        from app.api.deps import get_chat_service
        from app.domain.agent.chat import ChatService

        chat = client.app.dependency_overrides[get_chat_service]()
        assert isinstance(chat, ChatService)
        payloads, *_ = await chat.post_user_message(
            uuid.UUID(topic),
            author="alice",
            content=content,
            turn_id=None,
            reply_to=None,
        )
        return (payloads[0].get("meta") or {}).get("agent_recipient") or {}

    to_second = client.portal.call(recipient_of, f"@{second['display_name']} 看一下")
    assert to_second["mentioned"] is True
    assert to_second["handle"] == second["handle"], to_second

    to_first = client.portal.call(recipient_of, f"@{first['display_name']} 你来")
    assert to_first["handle"] == first["handle"], to_first
