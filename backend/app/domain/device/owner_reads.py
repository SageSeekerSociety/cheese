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

`app/api/routes/execution.py` asks its one question through this module too.
Device authentication, transcript authorization and viewer membership also
read only the fields needed for those decisions. Changes to these fields still
require a coordinated owner release; unrelated columns do not.

Outliving the app cuts the other way as well: a release that moves a read onto
a shape only the new backend writes leaves THIS process reading the old one
until somebody releases it separately. So a read is moved in the release BEFORE
the one that moves the write, reading both shapes in between, and the column it
used to read is dropped a release later still.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession
from app.domain.device.models import DeviceRow, DeviceTopicRow, HostedDeviceRow
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.device.supply import binding_visibility, has_runnable_transport
from app.domain.project.models import Project, ProjectMember
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic, TopicMembership


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    device_id: str
    name: str


async def device_for_token(session: AsyncSession, token: str) -> DeviceIdentity | None:
    if not token:
        return None
    row = (
        await session.execute(
            select(DeviceRow.device_id, DeviceRow.name).where(DeviceRow.token == token)
        )
    ).one_or_none()
    return DeviceIdentity(row.device_id, row.name) if row is not None else None


async def device_ran_place(
    session: AsyncSession, device_id: str, project_id: uuid.UUID, place_id: uuid.UUID
) -> bool:
    pinned = await session.scalar(
        select(DeviceTopicRow.device_id).where(DeviceTopicRow.topic_id == place_id)
    )
    if pinned is not None:
        return pinned == device_id
    hosted = await session.scalar(
        select(HostedDeviceRow.device_id).where(HostedDeviceRow.device_id == device_id)
    )
    if hosted is None:
        return False
    return device_id in await SqlDeviceRepository(session).device_ids_by_project(
        project_id
    )


async def execution_device_authorized(
    session: AsyncSession, device_id: str, project_id: uuid.UUID
) -> bool:
    supply = await session.scalar(
        select(DeviceRow.supply)
        .join(HostedDeviceRow, HostedDeviceRow.device_id == DeviceRow.device_id)
        .where(DeviceRow.device_id == device_id)
    )
    return (
        supply is not None
        and has_runnable_transport(binding_visibility(supply))
        and device_id
        in await SqlDeviceRepository(session).device_ids_by_project(project_id)
    )


async def project_member(
    session: AsyncSession, project_id: uuid.UUID, handle: str
) -> bool:
    return (
        await session.scalar(
            select(ProjectMember.project_id).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_handle == handle,
            )
        )
        is not None
    )


async def topic_member(session: AsyncSession, topic_id: uuid.UUID, handle: str) -> bool:
    return (
        await session.scalar(
            select(TopicMembership.topic_id).where(
                TopicMembership.topic_id == topic_id,
                TopicMembership.member_handle == handle,
            )
        )
        is not None
    )


@dataclass(frozen=True, slots=True)
class Place:
    """A room or a task, as the owner needs it: whose project, and what it runs on."""

    project_id: uuid.UUID
    #: The session machines this place's conversations are sitting on. A room
    #: seats several agents and each holds its own session, so this is a set and
    #: not one machine.
    session_machines: frozenset[str]


async def project_exists(session: AsyncSession, project_id: uuid.UUID) -> bool:
    return (
        await session.scalar(select(Project.id).where(Project.id == project_id))
    ) is not None


async def project_owner(session: AsyncSession, project_id: uuid.UUID) -> str | None:
    return await session.scalar(
        select(Project.owner_handle).where(Project.id == project_id)
    )


async def session_places(
    session: AsyncSession, place_id: uuid.UUID
) -> list[tuple[dict, dict | None]]:
    """Every session sitting in this place: where its process runs, and the
    hands it rented. A room seats several agents, so this is a list.
    """
    located = (
        await session.execute(
            select(AgentSession.runtime_location, AgentSession.work_lease).where(
                AgentSession.topic_id == place_id,
                AgentSession.runtime_location.is_not(None),
            )
        )
    ).all()
    return [(row.runtime_location, row.work_lease) for row in located]


async def legacy_execution(session: AsyncSession, place_id: uuid.UUID):
    """Only an un-upgraded lease can be addressed by a pre-session credential.

    A new session becoming the sole row never grants an old room token access.
    Keep this reader until all old screens have naturally reopened.
    """
    rows = (
        await session.execute(
            select(
                AgentSession.id, AgentSession.runtime_location, AgentSession.work_lease
            )
            .where(AgentSession.topic_id == place_id)
            .with_for_update()
        )
    ).all()
    legacy = [
        row
        for row in rows
        if row.work_lease
        and row.work_lease.get("kind") == "device"
        and not row.work_lease.get("generation")
        and not row.work_lease.get("session_id")
    ]
    return legacy[0] if len(legacy) == 1 else None


async def session_execution(
    session: AsyncSession, place_id: uuid.UUID, session_id: uuid.UUID
):
    """Read exactly the session named by the signed execution credential."""
    return (
        await session.execute(
            select(AgentSession.runtime_location, AgentSession.work_lease)
            .where(AgentSession.topic_id == place_id, AgentSession.id == session_id)
            .with_for_update()
        )
    ).one_or_none()


async def place(session: AsyncSession, place_id: uuid.UUID) -> Place | None:
    """The room with this id, or the task with it — a place is either."""
    room = (
        await session.execute(select(Topic.project_id).where(Topic.id == place_id))
    ).one_or_none()
    if room is not None:
        return Place(
            project_id=room.project_id,
            session_machines=frozenset(
                where["device_id"]
                for where, _lease in await session_places(session, place_id)
                if where.get("device_id")
            ),
        )
    task = (
        await session.execute(select(Task.project_id).where(Task.id == place_id))
    ).one_or_none()
    if task is None:
        return None
    return Place(project_id=task.project_id, session_machines=frozenset())
