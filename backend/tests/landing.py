"""What happens around a task's merge that no request drives: its AI teammate's
session taking the write-up, and time passing.

The runtime that runs a session is not here; `write_up_read` plays the part a
session plays for the delivery ledger (fenced, read, over), and `written_up`
adds the one thing that closes the task: the session closing it
(`cheese_task(action=close)`).
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import update

from app.api.deps import get_chat_service
from app.domain.agent.announce import announce
from app.domain.agent_instance.adapters import task_teammate
from app.domain.delivery.agent import (
    begin_send,
    dispatch_pending,
    receive_attempt,
    record_task_instruction,
    run_attempt,
)
from app.domain.identity.handles import agent_instance_handle
from app.domain.review import pr_poll
from app.domain.room_task.models import Task
from tests.integration.conftest import task_agent_headers


def _chat(client):
    return client.app.dependency_overrides[get_chat_service]()


def write_up_read(client, task_id: str) -> None:
    """The instructions waiting for the task's AI teammate are each handed to
    its session, which reads them."""
    chat = _chat(client)
    sessions = chat.session_factory
    attempts = []

    def submit(_chat, _topic, *, turn_id, delivery_id, **_kwargs):
        async def read():
            await begin_send(sessions, delivery_id, turn_id)
            async with sessions() as session:
                await receive_attempt(session, turn_id, datetime.now(UTC))
                await session.commit()

        attempts.append(run_attempt(sessions, delivery_id, turn_id, read()))

    async def run():
        await dispatch_pending(
            sessions, chat=chat, runner=SimpleNamespace(submit=submit)
        )
        for attempt in attempts:
            await attempt

    client.portal.call(run)


def teammate_seat(client, task_id: str) -> tuple[uuid.UUID, str]:
    """The task's project, and the seat its AI teammate acts under: the handle
    its session's runner registers and its credential carries."""

    async def seat():
        async with client.test_factory() as session:
            task = await session.get(Task, uuid.UUID(str(task_id)))
            assert task is not None
            teammate = await task_teammate(
                session, task.project_id, task.room_id, task.agent_handle
            )
            assert teammate is not None
            return task.project_id, agent_instance_handle(teammate[0])

    return client.portal.call(seat)


def written_up(client, task_id: str) -> None:
    """The task's AI teammate reads its write-up instruction, writes it up, and
    closes the task from its own session (`cheese_task(action=close)`)."""
    write_up_read(client, task_id)
    project_id, handle = teammate_seat(client, task_id)
    closed = client.post(
        f"/topics/{task_id}/close",
        json={"conclusion": "做完了"},
        headers=task_agent_headers(project_id, task_id, handle),
    )
    assert closed.status_code == 200, closed.text


def time_passes(client, task_id: str, *, minutes: int) -> None:
    """Everything that happened to the task's merge happened ``minutes`` earlier."""
    earlier = timedelta(minutes=minutes)

    async def shift():
        async with client.test_factory() as session:
            await session.execute(
                update(Task)
                .where(
                    Task.id == uuid.UUID(str(task_id)), Task.closing_since.is_not(None)
                )
                .values(closing_since=Task.closing_since - earlier)
            )
            await session.commit()

    client.portal.call(shift)


def watch(client) -> dict:
    """One tick of the platform's sweep of tasks being written up."""
    chat = _chat(client)
    return client.portal.call(
        lambda: pr_poll.sweep_closings(
            chat,
            announce,
            record_instruction=record_task_instruction,
            task_teammate=task_teammate,
        )
    )
