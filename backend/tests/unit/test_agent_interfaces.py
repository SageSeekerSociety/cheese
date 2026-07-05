"""Unit tests for the Agent contract via the RecordingAgent reference impl."""

import pytest

from app.agent.adapters.recording import RecordingAgent
from app.agent.authorization.authorizer import ProjectActor
from app.agent.interfaces import Agent, AgentContext, AgentSession

pytestmark = pytest.mark.anyio


def test_recording_agent_satisfies_protocols():
    agent = RecordingAgent()
    assert isinstance(agent, Agent)


async def test_start_send_stop_lifecycle():
    agent = RecordingAgent()
    ctx = AgentContext(
        actor=ProjectActor(kind="agent", actor_id=1, project_id=10),
        role_prompt="you coordinate",
    )
    session = await agent.start(ctx)
    assert isinstance(session, AgentSession)
    assert await session.is_alive() is True

    await session.send("hello")
    await session.send("world")
    assert session.received == ["hello", "world"]
    assert session.context.role_prompt == "you coordinate"

    await session.stop()
    assert await session.is_alive() is False


async def test_send_after_stop_raises():
    agent = RecordingAgent()
    ctx = AgentContext(actor=ProjectActor(kind="agent", actor_id=1, project_id=10))
    session = await agent.start(ctx)
    await session.stop()
    with pytest.raises(RuntimeError):
        await session.send("nope")


async def test_start_yields_distinct_sessions():
    agent = RecordingAgent()
    ctx = AgentContext(actor=ProjectActor(kind="agent", actor_id=1, project_id=10))
    s1 = await agent.start(ctx)
    s2 = await agent.start(ctx)
    assert s1.session_id != s2.session_id
    assert len(agent.sessions) == 2
