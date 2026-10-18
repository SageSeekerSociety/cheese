"""Handing a batch of due resumes reads the conversations' rooms in one query.

Before, every due resume asked for its conversation's room twice, once to see
whether the room is still open and once for the notice it is handed: a batch
of 100 cost 200 queries. The whole batch's rooms are read at once now, and each
resume is handed the room the one-by-one read named.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import event, select

from app.domain.agent.models import TurnResume
from app.domain.agent.turn_resume import hand_due
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.conversation.services import room_of
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import agent_instance_handle
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService
from tests.integration.conftest import project_service, registered
from tests.support.threads import thread_in


async def _five_due(factory) -> list[uuid.UUID]:
    """A room's own line and four of its 支线, each owed a resume."""
    async with factory() as session:
        await registered(session, "u")
        project = await project_service(session).create(name="P", owner_handle="u")
        room = await TopicService(session).create(
            project_id=project.id, title="T", created_by="u"
        )
        seat = (await TopicMemberService(session).agent_handles(room.id))[0]
        agent = await AgentInstanceService(session).for_seat_handle(project, seat)
        conversations = [room.id] + [await thread_in(session, room) for _ in range(4)]
        failed = datetime.now(UTC) + timedelta(seconds=1)
        session.add_all(
            TurnResume(
                project_id=project.id,
                conversation_id=conversation,
                recipient_handle=agent_instance_handle(agent.instance_id),
                agent_instance_id=agent.instance_id,
                failed_turn_id=uuid.uuid4(),
                reason="rate_limited",
                attempt=1,
                failed_at=failed,
                due_at=failed,
            )
            for conversation in conversations
        )
        await session.commit()
    return conversations


async def test_a_batch_of_due_resumes_reads_its_rooms_once(business_db_factory):
    factory = business_db_factory
    conversations = await _five_due(factory)
    async with factory() as session:
        expected = {c: await room_of(session, c) for c in conversations}

    room_reads: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        # `room_of` and its batch both read the tasks-and-支线 union by name.
        if "inside" in statement:
            room_reads.append(statement)

    engine = factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        handed = await hand_due(factory, now=datetime.now(UTC) + timedelta(minutes=1))
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert handed == 5
    # Read one by one this was two per resume, ten here.
    assert len(room_reads) == 1, room_reads
    async with factory() as session:
        rows = await session.execute(
            select(Delivery.conversation_id, Delivery.payload).where(
                Delivery.conversation_id.in_(conversations)
            )
        )
        told = {c: uuid.UUID(payload["topicId"]) for c, payload in rows}
    assert told == expected
