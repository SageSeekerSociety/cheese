"""The database reads the device-connection owner is allowed to make.

The owner holds every machine's link, so an app release deliberately leaves it
running its old image (`deploy-docker.sh`: 「leaving device connection owner …
running across this app release」). The same release runs the migrations. A
process running old code against a migrated schema is therefore the normal
state here, not an accident — and an ORM model is a list of every column the
table had when that image was built, so loading one asks for columns the
database may no longer have.

That is not hypothetical: #1240 dropped `topics.agent_instance_id` on
2026-09-19, and for three hours every room command the owner served failed with
`column topics.agent_instance_id does not exist` — 1218 of them, until someone
released the owner for an unrelated reason.

So the owner names the columns it needs, and gets nothing else. Each function
here is one question the owner asks, answered by the fewest columns that answer
it; a column dropped from anywhere else in those tables cannot reach it.

`app/api/routes/execution.py` does the same inline for the admission check, and
says why in the same words. What is NOT covered yet: the device row itself
(`DeviceService.verify_token`) and project/topic membership, which still load
their models through repositories shared with the business backend.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.models import Project
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic


@dataclass(frozen=True, slots=True)
class Place:
    """A room or a task, as the owner needs it: whose project, and what it runs on."""

    project_id: uuid.UUID
    session_placement: dict | None


async def project_exists(session: AsyncSession, project_id: uuid.UUID) -> bool:
    return (
        await session.scalar(select(Project.id).where(Project.id == project_id))
    ) is not None


async def project_owner(session: AsyncSession, project_id: uuid.UUID) -> str | None:
    return await session.scalar(
        select(Project.owner_handle).where(Project.id == project_id)
    )


async def place(session: AsyncSession, place_id: uuid.UUID) -> Place | None:
    """The room with this id, or the task with it — a place is either."""
    room = (
        await session.execute(
            select(Topic.project_id, Topic.session_placement).where(
                Topic.id == place_id
            )
        )
    ).one_or_none()
    if room is not None:
        return Place(
            project_id=room.project_id, session_placement=room.session_placement
        )
    task = (
        await session.execute(select(Task.project_id).where(Task.id == place_id))
    ).one_or_none()
    if task is None:
        return None
    return Place(project_id=task.project_id, session_placement=None)
