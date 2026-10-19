"""A message may name personal agents belonging to different team members."""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.domain.agent import own_calls
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.identity.handles import agent_instance_handle
from app.domain.topic.models import Topic
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.models import User
from tests.integration.conftest import registered
from tests.turn_log import a_topic

pytestmark = pytest.mark.anyio


async def _own(session, project_id, owner):
    agent = AgentInstance(
        project_id=project_id,
        handle=f"own-{uuid.uuid4().hex[:8]}",
        configuration={},
        display_name="Claude Code",
    )
    session.add(agent)
    await session.flush()
    session.add(
        OwnAgent(instance_id=agent.id, owner_user_id=owner.id, harness="claude-code")
    )
    return agent.id


async def test_a_message_seats_personal_agents_of_different_owners(
    db_factory, monkeypatch
):
    topic_id = await a_topic(db_factory)
    seated = []

    async def ensure(self, topic, seat):
        seated.append(seat)

    async def no_question(*args):
        pass

    monkeypatch.setattr(TopicMemberService, "ensure_agent_seat", ensure)
    monkeypatch.setattr(own_calls, "ask_owner_to_join", no_question, raising=False)
    async with db_factory() as session:
        topic = await session.get(Topic, topic_id)
        me = await session.scalar(select(User).where(User.username == "u"))
        other = await session.get(User, await registered(session, "other"))
        mine = await _own(session, topic.project_id, me)
        theirs = await _own(session, topic.project_id, other)
        await session.flush()
        content = " ".join(
            f"<@{agent_instance_handle(i)}>" for i in (mine, theirs, uuid.uuid4())
        )
        await own_calls.seat_if_named(
            session, SimpleNamespace(settings={}), topic, content, "u"
        )
    assert set(seated) == {agent_instance_handle(mine), agent_instance_handle(theirs)}
