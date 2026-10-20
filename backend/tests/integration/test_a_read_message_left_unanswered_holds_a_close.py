"""A message read and left unanswered keeps the platform from closing its task.

A task whose last delivery was accepted waits for its teammate's write-up, and
the platform closes it once that is overdue (`closing.close_overdue`) — unless
something is still being done or asked for in it. A message the teammate read
into work that then failed was read and got no answer: closing the task then
would drop somebody's request without a word. Read is not answered.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.domain.block.models import Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.event_author import AuthorType
from app.domain.room_task import closing
from app.domain.room_task.models import Task, TaskStatus
from tests.integration.conftest import open_task, post_project


class _Work:
    """What stops a teammate's work: nothing to stop here."""

    async def stop_work(self, conversation_id: uuid.UUID) -> None:
        return None


def _overdue_task(client) -> tuple[uuid.UUID, uuid.UUID]:
    project = post_project(client, {"name": "Close"}, owner="alice").json()["data"]
    task = open_task(client, project["root_topic_id"], "修登录页", owner="alice")
    task_id = uuid.UUID(task["id"])

    async def overdue() -> None:
        async with client.test_factory() as session:
            await session.execute(
                update(Task)
                .where(Task.id == task_id)
                .values(
                    closing_since=datetime.now(UTC)
                    - closing.CLOSE_AT_THE_LATEST
                    - timedelta(minutes=5)
                )
            )
            await session.commit()

    client.portal.call(overdue)
    return uuid.UUID(project["id"]), task_id


def _message(client, project_id, task_id, *, read_failed: bool) -> None:
    async def write() -> None:
        async with client.test_factory() as session:
            block = Block(
                id=uuid.uuid4(),
                project_id=project_id,
                conversation_id=task_id,
                kind=BlockKind.message,
                author_type=AuthorType.participant,
                author="alice",
                content="顺手把注册页也看一下",
                meta={},
            )
            session.add(block)
            await session.flush()
            if read_failed:
                await BlockRepository(session).mark_read_failed([block.id], "api_error")
            await session.commit()

    client.portal.call(write)


def _close_overdue(client) -> list[closing.Overdue]:
    async def close() -> list[closing.Overdue]:
        async with client.test_factory() as session:
            closed = await closing.close_overdue(session, _Work())
            await session.commit()
            return closed

    return client.portal.call(close)


def _status(client, task_id) -> TaskStatus:
    async def read() -> TaskStatus:
        async with client.test_factory() as session:
            task = await session.get(Task, task_id)
            assert task is not None
            return task.status

    return client.portal.call(read)


def test_a_message_read_into_failed_work_holds_the_close(client):
    project_id, task_id = _overdue_task(client)
    _message(client, project_id, task_id, read_failed=True)

    assert _close_overdue(client) == []

    assert _status(client, task_id) == TaskStatus.open


def test_with_nothing_left_unanswered_the_overdue_task_closes(client):
    project_id, task_id = _overdue_task(client)

    closed = _close_overdue(client)

    assert [overdue.task.id for overdue in closed] == [task_id]
    assert _status(client, task_id) == TaskStatus.closed
