"""A Codex session's reader keeps each harness's identity and survives its own crash."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from app.api import deps as session_turn_deps
from app.domain.agent.chat import ChatService
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.codex.backlog import CodexBacklog
from app.domain.agent.harness.codex.events import Assembler
from app.domain.agent.harness.codex.subscription import Subscription
from app.domain.agent_session.services import AgentSessionService
from tests.integration.conftest import post_project, session_auth_headers


@pytest.mark.anyio
async def test_same_agent_resumes_each_harness_history_independently(client):
    project = post_project(
        client, json={"name": "Harness sessions"}, owner="alice"
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={
            "project_id": project["id"],
            "title": "Room",
        },
        headers=session_auth_headers("alice"),
    ).json()["data"]
    topic_id = uuid.UUID(topic["id"])

    async def exercise():
        async with client.test_request_factory() as session:
            sessions = AgentSessionService(session)
            await sessions.remember(
                conversation_id=topic_id,
                agent_handle="teammate",
                resume_token="claude-thread",
                harness="claude-code",
            )
            assert (
                await sessions.resume_token(topic_id, "teammate", harness="codex")
                is None
            )
            await sessions.remember(
                conversation_id=topic_id,
                agent_handle="teammate",
                resume_token="codex-thread",
                harness="codex",
            )
            await session.commit()
        async with client.test_request_factory() as session:
            sessions = AgentSessionService(session)
            assert (
                await sessions.resume_token(topic_id, "teammate", harness="claude-code")
                == "claude-thread"
            )
            assert (
                await sessions.resume_token(topic_id, "teammate", harness="codex")
                == "codex-thread"
            )

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_late_session_event_preserves_original_teammate_and_harness(
    client, tmp_path
):
    project = post_project(
        client,
        json={
            "name": "Late owner",
        },
        owner="alice",
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={
            "project_id": project["id"],
            "title": "Different current teammate",
        },
        headers=session_auth_headers("alice"),
    ).json()["data"]
    project_id, topic_id = uuid.UUID(project["id"]), uuid.UUID(topic["id"])

    async def teammate() -> str:
        from app.domain.agent_instance.services import AgentInstanceService
        from app.domain.identity.handles import agent_instance_handle

        async with client.test_factory() as session:
            instance = await AgentInstanceService(session).create(
                project_id=project_id,
                handle="original-teammate",
                type_name=None,
                display_name="Original teammate",
            )
            await session.commit()
            return agent_instance_handle(instance.id)

    # The session authored under its teammate's seat; the pointer belongs to
    # that teammate, whoever the room's current agent is.
    seat = client.portal.call(teammate)
    service = ChatService(
        work_runner=session_turn_deps.get_work_runner(),
        session_factory=client.test_request_factory,
        base_system_prompt="fixture",
        workspace_root=str(tmp_path),
        compute=Mock(),
    )
    event = Assembler().accept(
        {
            "method": "thread/started",
            "params": {"thread": {"id": "old-thread"}},
            "cheese": {"agent_handle": seat, "harness": "codex"},
        }
    )[0]
    client.portal.call(
        lambda: service.hook_events.accept(
            project_id,
            topic_id,
            uuid.uuid4(),
            event,
            "late-start",
            False,
            False,
        )
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
async def test_a_reader_that_died_after_its_consumer_committed_reads_on(
    client, tmp_path
):
    project = post_project(
        client,
        json={
            "name": "Reader crash",
        },
        owner="alice",
    ).json()["data"]
    topic = client.post(
        "/topics",
        json={
            "project_id": project["id"],
            "title": "Room",
        },
        headers=session_auth_headers("alice"),
    ).json()["data"]
    session = SessionRef(
        uuid.UUID(project["id"]), uuid.UUID(topic["id"]), harness="codex"
    )
    path = tmp_path / "events.sqlite"
    work = str(uuid.uuid4())
    remote = AsyncMock(
        return_value={
            "events": [
                {
                    "sequence": 1,
                    "at": datetime.now(UTC).isoformat(),
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
                        "cheese": {"work_id": work, "agent_handle": "original-agent"},
                    },
                }
            ]
        }
    )

    def service(compute=None):
        return ChatService(
            work_runner=session_turn_deps.get_work_runner(),
            session_factory=client.test_request_factory,
            base_system_prompt="fixture",
            workspace_root=str(tmp_path),
            compute=compute or Mock(),
        )

    first = service()

    async def commit_then_disconnect(*args):
        await first.hook_events.accept(*args)
        raise ConnectionError("reader died after database commit")

    reader = Subscription(session, path, remote, commit_then_disconnect, AsyncMock())
    with pytest.raises(ConnectionError):
        client.portal.call(lambda: reader.drain())
    assert len(CodexBacklog(path).unread()) == 1
    remote.return_value = {"events": []}
    restarted = Subscription(
        session, path, remote, service().hook_events.accept, AsyncMock()
    )
    assert client.portal.call(lambda: restarted.drain()) == 1
    assert not CodexBacklog(path).unread()
    # A different model item with the same text is read like any other.
    remote.return_value = {
        "events": [
            {
                "sequence": 2,
                "at": datetime.now(UTC).isoformat(),
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
                    "cheese": {"work_id": work, "agent_handle": "original-agent"},
                },
            }
        ]
    }
    assert client.portal.call(lambda: restarted.drain()) == 1
    assert not CodexBacklog(path).unread()
