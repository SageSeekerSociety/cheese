"""Codex item identity must survive delivery into the real room timeline."""

import uuid
from unittest.mock import Mock

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.harness.codex.events import Assembler
from app.domain.agent_session.services import AgentSessionService
from app.domain.block.repositories import BlockRepository


@pytest.mark.anyio
async def test_replayed_codex_reply_is_not_persisted_twice(client, tmp_path):
    project = client.post(
        "/projects", json={"name": "Codex events", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={
            "project_id": project["id"],
            "title": "Room",
            "created_by": "alice",
        },
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(topic["id"])
    factory = client.test_factory

    async def deliver(item_id):
        # A fresh service and assembler represent a backend reconnect/replay.
        service = ChatService(
            session_factory=factory,
            base_system_prompt="fixture",
            workspace_root=str(tmp_path),
            compute=Mock(),
        )
        event = Assembler().accept(
            {
                "method": "item/completed",
                "params": {
                    "threadId": "codex-thread",
                    "turnId": "codex-turn",
                    "item": {
                        "type": "agentMessage",
                        "id": item_id,
                        "text": "Same reply",
                    },
                },
            }
        )[0]
        await service._consume_hook_event(
            project_id,
            topic_id,
            uuid.uuid4(),
            event,
            event.eid,
            False,
            False,
        )

    await deliver("message-1")
    await deliver("message-1")
    await deliver("message-2")
    async with factory() as session:
        blocks = await BlockRepository(session).list_for_topic(topic_id)
    replies = [block for block in blocks if block.content == "Same reply"]
    assert len(replies) == 2
    assert {block.meta["eid"] for block in replies} == {
        "codex:codex-thread:message-1",
        "codex:codex-thread:message-2",
    }


@pytest.mark.anyio
async def test_same_agent_resumes_each_harness_history_independently(client):
    project = client.post(
        "/projects", json={"name": "Harness sessions", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={
            "project_id": project["id"],
            "title": "Room",
            "created_by": "alice",
        },
    ).json()["data"]
    topic_id = uuid.UUID(topic["id"])
    async with client.test_factory() as session:
        sessions = AgentSessionService(session)
        await sessions.remember(
            topic_id=topic_id,
            agent_handle="teammate",
            resume_token="claude-thread",
            harness="claude-code",
        )
        assert (
            await sessions.resume_token(topic_id, "teammate", harness="codex") is None
        )
        await sessions.remember(
            topic_id=topic_id,
            agent_handle="teammate",
            resume_token="codex-thread",
            harness="codex",
        )
        await session.commit()
    async with client.test_factory() as session:
        sessions = AgentSessionService(session)
        assert (
            await sessions.resume_token(topic_id, "teammate", harness="claude-code")
            == "claude-thread"
        )
        assert (
            await sessions.resume_token(topic_id, "teammate", harness="codex")
            == "codex-thread"
        )


@pytest.mark.anyio
async def test_late_session_event_preserves_original_teammate_and_harness(
    client, tmp_path
):
    project = client.post(
        "/projects",
        json={
            "name": "Late owner",
            "owner_handle": "alice",
        },
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={
            "project_id": project["id"],
            "title": "Different current teammate",
            "created_by": "alice",
        },
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(topic["id"])
    service = ChatService(
        session_factory=client.test_factory,
        base_system_prompt="fixture",
        workspace_root=str(tmp_path),
        compute=Mock(),
    )
    event = Assembler().accept(
        {
            "method": "thread/started",
            "params": {"thread": {"id": "old-thread"}},
            "cheese": {"agent_handle": "original-teammate", "harness": "codex"},
        }
    )[0]
    await service._consume_hook_event(
        project_id,
        topic_id,
        uuid.uuid4(),
        event,
        "late-start",
        False,
        False,
    )
    async with client.test_factory() as session:
        sessions = AgentSessionService(session)
        assert (
            await sessions.resume_token(
                topic_id,
                "original-teammate",
                harness="codex",
            )
            == "old-thread"
        )
        assert (
            await sessions.resume_token(
                topic_id,
                "original-teammate",
                harness="claude-code",
            )
            is None
        )
