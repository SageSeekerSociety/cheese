"""The tail of a turn read by a backend that holds none of its bookkeeping.

dev hands running turns to a new backend on every deploy. On 2026-09-27 the
backend that read the last records of a teammate's turn had no ``live.hook_work`` for
it, and 现场 showed three things wrong: the calls and the closing words were
signed by the room's default agent, the closing words landed twice (once as the
message, once as the turn's result), and they sorted above the calls made before
them. The runner stamps every record with who wrote it and the build with when,
so none of that needs the bookkeeping.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.harness.claude_code.events import Assembler
from app.domain.agent.service import AgentResult
from app.domain.block.repositories import BlockRepository
from tests.integration.conftest import post_project, session_auth_headers

TEAMMATE = "cheese-0pu5teammate"
CLOSING = "改好了，验收卡已递给你。"


def _records(work: str) -> list[dict]:
    stamp = {"work_id": work, "agent_handle": TEAMMATE}
    return [
        {
            "type": "assistant",
            "uuid": "call-record",
            "timestamp": "2026-09-27T08:16:20.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Bash",
                        "input": {"command": "git push", "description": "推送"},
                    }
                ]
            },
            "cheese": stamp,
        },
        {
            "type": "assistant",
            "uuid": "words-record",
            "timestamp": "2026-09-27T08:16:25.000Z",
            "message": {"content": [{"type": "text", "text": CLOSING}]},
            "cheese": stamp,
        },
        {
            "type": "result",
            "result": CLOSING,
            "session_id": "claude-session",
            "cheese": stamp,
        },
    ]


@pytest.mark.anyio
async def test_a_turns_tail_read_without_its_bookkeeping_lands_as_it_happened(
    client, tmp_path
):
    project = post_project(
        client, json={"name": "Handover tail"}, owner="alice"
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(topic["id"])
    work = uuid.uuid4()
    # A fresh service: the process that took the turn over, holding nothing.
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="fixture",
        workspace_root=str(tmp_path),
        compute=Mock(),
    )
    assert not service.live.hook_work
    assembler = Assembler({})

    async def deliver() -> None:
        for record in _records(str(work)):
            for event in assembler.accept(record):
                await service._consume_hook_event(
                    project_id,
                    topic_id,
                    work,
                    event,
                    getattr(event, "eid", None) or f"claude:{record.get('uuid')}",
                    # What the subscription passes for a result that succeeded.
                    isinstance(event, AgentResult) and not event.is_error,
                    False,
                )

    client.portal.call(deliver)
    async with client.test_factory() as session:
        blocks = [
            block
            for block in await BlockRepository(session).list_for_topic(topic_id)
            if block.turn_id == work
        ]

    closing = [block for block in blocks if block.content == CLOSING]
    assert len(closing) == 1, "the result repeated the closing words"
    (call,) = [block for block in blocks if (block.meta or {}).get("tool") == "Bash"]
    assert {call.author, closing[0].author} == {TEAMMATE}
    assert call.created_at == datetime(2026, 9, 27, 8, 16, 20, tzinfo=UTC)
    assert call.created_at < closing[0].created_at


@pytest.mark.anyio
async def test_a_result_that_says_something_new_still_lands(client, tmp_path):
    """Only a result repeating the turn's last words is dropped."""
    project = post_project(
        client, json={"name": "Handover result"}, owner="alice"
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room"},
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(topic["id"])
    work = uuid.uuid4()
    service = ChatService(
        session_factory=client.test_request_factory,
        base_system_prompt="fixture",
        workspace_root=str(tmp_path),
        compute=Mock(),
    )
    records = _records(str(work))
    records[-1] = {**records[-1], "result": "另一段话"}
    assembler = Assembler({})

    async def deliver() -> None:
        for record in records:
            for event in assembler.accept(record):
                await service._consume_hook_event(
                    project_id,
                    topic_id,
                    work,
                    event,
                    getattr(event, "eid", None) or f"claude:{record.get('uuid')}",
                    isinstance(event, AgentResult) and not event.is_error,
                    False,
                )

    client.portal.call(deliver)
    async with client.test_factory() as session:
        said = [
            block.content
            for block in await BlockRepository(session).list_for_topic(topic_id)
            if block.turn_id == work
        ]
    assert said.count(CLOSING) == 1
    assert said.count("另一段话") == 1
