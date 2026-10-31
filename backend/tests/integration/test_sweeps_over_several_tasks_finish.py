"""A sweep that reaches several tasks in one pass finishes for all of them.

The delivery timer gives up on every instruction that never started, and a
finished task waiting on its write-up instruction closes without it. Two such
tasks given up on in the same pass both close, and both instructions are
marked failed: one task does not take the other's close back with it.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.api.deps import get_chat_service
from app.domain.delivery.agent import instance_for_seat
from app.domain.delivery.models import Delivery
from app.domain.delivery.timer import give_up_stale
from app.domain.room_task.closing import CLOSES_TASK
from app.domain.room_task.models import Task, TaskStatus
from tests.integration.conftest import room_agent_seat, session_auth_headers
from tests.integration.test_the_composer_shows_who_a_message_is_for import _channel


def test_two_tasks_whose_write_up_never_started_both_close(client, stub_hooks):
    project, _, room = _channel(client)
    seat = room_agent_seat(client, room)
    tasks = []
    for _ in range(2):
        made = client.post(
            f"/topics/{room}/tasks", json={}, headers=session_auth_headers("alice")
        )
        assert made.status_code == 200, made.text
        tasks.append(uuid.UUID(made.json()["data"]["id"]))

    async def waiting_for_a_write_up_that_never_started():
        long_ago = datetime.now(UTC) - timedelta(minutes=31)
        for task_id in tasks:  # each accepted on its own, as they would be
            async with client.test_factory() as db:
                await db.execute(
                    update(Task)
                    .where(Task.id == task_id)
                    .values(closing_since=long_ago)
                )
                agent = await instance_for_seat(db, uuid.UUID(project), seat)
                assert agent is not None
                db.add(
                    Delivery(
                        event_id=uuid.uuid4(),
                        recipient_handle=seat,
                        agent_instance_id=agent.id,
                        conversation_id=task_id,
                        dedup_key=str(uuid.uuid4()),
                        type="ROOM_NOTICE",
                        payload={"content": "写一下收尾说明", CLOSES_TASK: True},
                        event_at=long_ago,
                        recorded_at=long_ago,
                        attempts=1,
                        attempt_id=uuid.uuid4(),
                    )
                )
                await db.commit()

    client.portal.call(waiting_for_a_write_up_that_never_started)
    chat = client.app.dependency_overrides[get_chat_service]()

    given_up = client.portal.call(lambda: give_up_stale(client.test_factory, chat=chat))

    async def read_back():
        async with client.test_factory() as db:
            statuses = list(
                await db.scalars(select(Task.status).where(Task.id.in_(tasks)))
            )
            states = list(
                await db.scalars(
                    select(Delivery.state).where(Delivery.conversation_id.in_(tasks))
                )
            )
            return statuses, states

    statuses, states = client.portal.call(read_back)
    assert given_up == 2
    assert statuses == [TaskStatus.closed, TaskStatus.closed]
    assert states == ["failed", "failed"]
