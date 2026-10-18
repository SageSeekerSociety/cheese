"""A message naming several of the members' own agents asks, once, which of them
its author may call — and gets the answer the one-by-one question would give."""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from app.domain.agent.own_calls import seat_if_named
from app.domain.agent_instance.models import AgentInstance, OwnAgent
from app.domain.agent_instance.own import callable_by, callable_by_many
from app.domain.agent_instance.own.settings import SETTING
from app.domain.identity.handles import agent_instance_handle
from app.domain.topic.models import Topic
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.models import User
from tests.integration.conftest import registered
from tests.turn_log import a_topic

pytestmark = pytest.mark.anyio


async def _own(session, project_id, owner: User) -> uuid.UUID:
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


async def test_naming_four_agents_judges_them_in_one_query(db_factory, monkeypatch):
    topic_id = await a_topic(db_factory)
    seated: list[str] = []

    async def ensure(self, topic, seat):
        seated.append(seat)

    monkeypatch.setattr(TopicMemberService, "ensure_agent_seat", ensure)
    async with db_factory() as session:
        topic = await session.get(Topic, topic_id)
        assert topic is not None
        me = await session.scalar(select(User).where(User.username == "u"))
        other = await session.get(User, await registered(session, "other"))
        assert other is not None
        mine = [await _own(session, topic.project_id, me) for _ in range(2)]
        theirs = await _own(session, topic.project_id, other)
        ghost = uuid.uuid4()
        await session.commit()

        ids = [*mine, theirs, ghost]
        singly = {i: await callable_by(session, i, "u") for i in ids}
        assert singly == {mine[0]: True, mine[1]: True, theirs: False, ghost: False}

        queries: list[str] = []

        def count(conn, cursor, statement, *rest):
            if "own_agents.callers" in statement:
                queries.append(statement)

        engine = session.bind.sync_engine
        event.listen(engine, "before_cursor_execute", count)
        try:
            assert await callable_by_many(session, ids, "u") == singly
            assert len(queries) == 1
            queries.clear()
            content = " ".join(f"<@{agent_instance_handle(i)}>" for i in ids)
            project = SimpleNamespace(settings={SETTING: True})
            await seat_if_named(session, project, topic, content, "u")
            assert len(queries) == 1
        finally:
            event.remove(engine, "before_cursor_execute", count)
    assert sorted(seated) == sorted(agent_instance_handle(i) for i in mine)
