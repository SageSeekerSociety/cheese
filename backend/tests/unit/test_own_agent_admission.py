"""A turn of a member's own Claude Code is not admitted on the project's credits.

It runs on its owner's machine with its owner's login (#2991): the project
pays for none of it, so a project whose credits are spent still lets it work,
and it does not wait for the platform's session host either. Every other
teammate in the same room is still held to the project's credits.
"""

import uuid

import pytest
from sqlalchemy import select

from app.core.sentences import say
from app.domain.agent.compute import build_compute_pool
from app.domain.agent.work_policy import work_policy
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.topic.models import Topic
from app.domain.usage.ledger import Ledger, Refusal
from app.domain.user.models import User
from tests.turn_log import a_topic

pytestmark = pytest.mark.anyio


@pytest.fixture
def spent(monkeypatch):
    async def refuse(self, payer):
        return Refusal(message=say("creditsExhausted"), reopens_at=None)

    monkeypatch.setattr(Ledger, "admit", refuse)


async def _project_agents(db_factory, topic_id):
    """The project's 芝士, and a member's own Claude Code beside it."""
    async with db_factory() as session:
        topic = await session.get(Topic, topic_id)
        assert topic is not None
        cheese = await session.scalar(
            select(AgentInstance).where(AgentInstance.project_id == topic.project_id)
        )
        owner = await session.scalar(select(User))
        own = AgentInstance(
            project_id=topic.project_id,
            handle=f"own-{uuid.uuid4().hex[:8]}",
            configuration={},
            display_name="u的 Claude Code",
        )
        session.add(own)
        await session.flush()
        session.add(
            OwnAgent(instance_id=own.id, owner_user_id=owner.id, harness="claude-code")
        )
        await session.commit()
        return cheese.id, own.id


async def test_an_own_agent_works_when_the_projects_credits_are_spent(
    db_factory, spent
):
    topic_id = await a_topic(db_factory)
    cheese, own = await _project_agents(db_factory, topic_id)
    pool = build_compute_pool()

    project_turn = await work_policy(db_factory, pool, topic_id, cheese)
    own_turn = await work_policy(db_factory, pool, topic_id, own)

    assert project_turn is not None and project_turn["credits_exhausted"]
    assert own_turn is not None and not own_turn["credits_exhausted"]
    assert own_turn["on_session_host"] is False, "it runs on its owner's machine"
