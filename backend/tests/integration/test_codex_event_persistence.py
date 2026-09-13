"""Codex item identity must survive delivery into the real room timeline."""

import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from app.domain.agent.chat import ChatService
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.codex.backlog import CodexBacklog, receive
from app.domain.agent.harness.codex.events import Assembler
from app.domain.agent.harness.codex.subscription import Subscription
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


@pytest.mark.anyio
async def test_reply_committed_before_reader_crash_is_not_duplicated(client, tmp_path):
    project = client.post(
        "/projects",
        json={
            "name": "Reader crash",
            "owner_handle": "alice",
        },
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={
            "project_id": project["id"],
            "title": "Room",
            "created_by": "alice",
        },
    ).json()["data"]
    session = SessionRef(uuid.UUID(project["id"]), uuid.UUID(topic["id"]))
    path = tmp_path / "events.sqlite"
    remote = AsyncMock(
        return_value={
            "events": [
                {
                    "sequence": 1,
                    "at": "2026-09-12T00:00:00+00:00",
                    "record": {
                        "method": "item/completed",
                        "params": {
                            "threadId": "thread",
                            "item": {
                                "type": "agentMessage",
                                "id": "reply",
                                "text": "One durable reply",
                            },
                        },
                        "cheese": {
                            "work_id": str(uuid.uuid4()),
                            "agent_handle": "original-agent",
                        },
                    },
                }
            ]
        }
    )

    def service(compute=None):
        return ChatService(
            session_factory=client.test_factory,
            base_system_prompt="fixture",
            workspace_root=str(tmp_path),
            compute=compute or Mock(),
        )

    first = service()

    async def commit_then_disconnect(*args):
        await first._consume_hook_event(*args)
        raise ConnectionError("reader died after database commit")

    reader = Subscription(session, path, remote, commit_then_disconnect, AsyncMock())
    with pytest.raises(ConnectionError):
        await reader.drain()
    assert len(CodexBacklog(path).unread()) == 1
    remote.return_value = {"events": []}
    restarted = Subscription(
        session, path, remote, service()._consume_hook_event, AsyncMock()
    )
    assert await restarted.drain() == 1
    assert not CodexBacklog(path).unread()
    async with client.test_factory() as db:
        blocks = await BlockRepository(db).list_for_topic(session.topic_id)
    replies = [block for block in blocks if block.content == "One durable reply"]
    assert len(replies) == 1
    assert replies[0].meta["eid"] == "codex:thread:reply"
    assert replies[0].author == "original-agent"
    # A different model item with the same text must survive the platform's
    # general recovery path, which also supports legacy Stop/display echoes.
    remote.return_value = {
        "events": [
            {
                "sequence": 2,
                "at": "2026-09-12T00:00:01+00:00",
                "record": {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread",
                        "item": {
                            "type": "agentMessage",
                            "id": "another-reply",
                            "text": "One durable reply",
                        },
                    },
                    "cheese": {"agent_handle": "original-agent"},
                },
            }
        ]
    }
    await receive(path, remote)
    compute = Mock()
    compute.backlog.side_effect = lambda _: CodexBacklog(path)
    recovery = service(compute)
    frames = [
        frame
        async for frame in recovery._reconcile_spool(
            session.project_id,
            session.topic_id,
            None,
        )
    ]
    assert len(frames) == 1
    async with client.test_factory() as db:
        blocks = await BlockRepository(db).list_for_topic(session.topic_id)
    replies = [block for block in blocks if block.content == "One durable reply"]
    assert len(replies) == 2
    assert {block.author for block in replies} == {"original-agent"}
