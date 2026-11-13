"""房间看得见分身查了什么 — end to end through a real turn.

A subagent's conclusion is the agent's working, not a line in the room.
"""

import uuid

import pytest

from tests.conftest import StubChannel, settle_turn
from tests.delivery import delivery_task
from tests.integration.conftest import (
    post_project,
)


class SubagentScreen(StubChannel):
    """Spawns a subagent, then hands its conclusion back — the two halves the
    room needs to pair up."""

    def emit_turn(
        self,
        topic_id: uuid.UUID,
        prompt: str,
        reply: str,
        *,
        agent: str | None = None,
    ) -> None:
        del reply
        self.starts(topic_id)
        self.acknowledges(topic_id, prompt)
        self.uses(topic_id, "Task", description="查分页接口现状")
        self.returns(topic_id, "Task", "结论：分页用的是 offset，\n改动点在路由层")
        self.stops(topic_id, "查完了")


@pytest.fixture
def stub_hooks() -> SubagentScreen:
    # Overrides conftest's stub_hooks for this module; `client` picks it up.
    return SubagentScreen()


def _chat(client, topic_id: str) -> None:
    """One turn in the channel's own line. Only the platform starts one there
    (a routine's run, say): a person calling 芝士 is answered in a 支线, and a
    支线 only reads, so it changes nothing to summarise."""
    from app.api.deps import get_chat_service

    chat = client.app.dependency_overrides[get_chat_service]()

    async def run() -> None:
        async for _frame in chat.converse(
            topic_id=uuid.UUID(topic_id),
            author="system",
            content="按规则跑一下",
            summon=True,
            nudge_event="定时规则开始运行",
        ):
            pass
        await settle_turn(chat, uuid.UUID(topic_id))

    client.portal.call(run)


def _topic(client) -> str:
    p = post_project(client, json={"name": "P"}, owner="user-1").json()["data"]
    room = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "话题"},
    ).json()["data"]["id"]
    delivery_task(client, room, commit=False)
    return room


def _transcript(client, topic_id: str) -> list[dict]:
    """施工现场 — event blocks only (that endpoint filters kind=event)."""
    return client.get(f"/topics/{topic_id}/transcript").json()["data"]["data"]


def _blocks(client, topic_id: str) -> list[dict]:
    """Everything in the room, messages included."""
    return client.get(f"/topics/{topic_id}/blocks").json()["data"]["data"]


def test_subagent_output_is_not_copied_into_a_room_event(client):
    topic_id = _topic(client)
    _chat(client, topic_id)
    assert not [
        b for b in _blocks(client, topic_id) if (b.get("meta") or {}).get("subagent")
    ]
