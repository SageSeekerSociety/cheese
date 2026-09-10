"""Explicit task fixtures for tests of delivery and workspace consumers."""

import uuid

from app.core.sandbox_auth import mint_scoped_token, scoped_token_claims
from app.domain.room_task.services import TaskService
from app.domain.topic.models import Topic
from tests.machine_work import machine_commits


def delivery_task(client, room_id, *, new=False, commit=True):
    """Seed one task per test room unless a scenario explicitly starts another."""
    room_id = uuid.UUID(str(room_id))
    cache = getattr(client, "delivery_tasks", None)
    if cache is None:
        cache = client.delivery_tasks = {}
    if not new and room_id in cache:
        return cache[room_id]

    async def seed():
        async with client.test_factory() as session:
            room = await session.get(Topic, room_id)
            if room is None:
                return None
            task = await TaskService(session).open_thread(
                project_id=room.project_id,
                room_id=room.id,
                title="Test delivery",
                owner_handle="alice",
                created_by="alice",
                reviewer_handle="alice",
            )
            await session.commit()
            return task

    task = client.portal.call(seed)
    if task is None:
        return None
    cache[room_id] = task
    if commit:
        machine_commits(
            task.project_id, task.id, {f"deliveries/{task.id}.txt": "Test delivery\n"}
        )
    return task


def delivery_task_id(client, room_id, *, commit=True):
    task = delivery_task(client, room_id, commit=commit)
    # An unknown room stays unknown to endpoint tests; do not manufacture it.
    return task.id if task else uuid.UUID(int=0)


def delivery_headers(client, room_id):
    """Authenticate delivery setup without changing decision-request identity."""
    if client.headers.get("Authorization") or scoped_token_claims(
        client.headers.get("X-Cheese-Token", "")
    ):
        return {}
    task = delivery_task(client, room_id)
    if task is None:
        return {}
    return {
        "X-Cheese-Token": mint_scoped_token(
            project_id=str(task.project_id), topic_id=str(task.room_id)
        )
    }
