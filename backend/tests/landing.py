"""What happens around a task's merge that no request drives: its AI teammate's
turns, and time passing.

The runtime that runs a turn is not here; `summary_turn_ends` plays the part a
turn plays for the delivery ledger (fenced, received, over), which is all the
task's closing reads.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import update

from app.api.deps import get_chat_service
from app.domain.delivery.agent import (
    begin_send,
    dispatch_pending,
    receive_attempt,
    run_attempt,
)
from app.domain.review import landing_watch
from app.domain.review.landing_models import TaskLanding
from app.domain.room_task.models import Task


def _chat(client):
    return client.app.dependency_overrides[get_chat_service]()


def summary_turn_ends(client, task_id: str) -> None:
    """The instructions waiting for the task's AI teammate are taken, one turn
    each, and every turn runs to its end."""
    chat = _chat(client)
    sessions = chat.session_factory
    turns = []

    def submit(_chat, _topic, *, turn_id, delivery_id, **_kwargs):
        async def turn():
            await begin_send(sessions, delivery_id, turn_id)
            async with sessions() as session:
                await receive_attempt(session, turn_id, datetime.now(UTC))
                await session.commit()

        turns.append(run_attempt(sessions, delivery_id, turn_id, turn()))

    async def run():
        await dispatch_pending(
            sessions, chat=chat, runner=SimpleNamespace(submit=submit)
        )
        for turn in turns:
            await turn

    client.portal.call(run)


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
            await session.execute(
                update(TaskLanding)
                .where(TaskLanding.task_id == uuid.UUID(str(task_id)))
                .values(
                    landed_at=TaskLanding.landed_at - earlier,
                    watch_until=TaskLanding.watch_until - earlier,
                )
            )
            await session.commit()

    client.portal.call(shift)


def watch(client) -> dict:
    """One tick of the platform's watch on merges."""
    chat = _chat(client)
    return client.portal.call(lambda: landing_watch.watch_landings(chat))
