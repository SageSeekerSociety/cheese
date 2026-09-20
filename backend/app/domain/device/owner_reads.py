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
What is NOT covered yet: the device row itself (`DeviceService.verify_token`)
and project/topic membership, which still load their models through
repositories shared with the business backend.

Outliving the app cuts the other way as well: a release that moves a read to a
new shape leaves THIS process reading the old one until somebody releases it
separately, and a room whose tool calls stop working is not the way to find
that out. So the owner says what it reads (:data:`SCHEMA_READS`) and
`deploy/deploy-docker.sh` refuses to switch the backend over an owner that
answers with anything else.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_session.models import AgentSession
from app.domain.project.models import Project
from app.domain.room_task.models import Task
from app.domain.topic.models import Topic


#: Which shape of the database this build of the owner reads. Bumped by the
#: release that moves one of the reads below, and named in
#: `deploy/deploy-docker.sh` as what a running owner must answer with before an
#: app release may switch the backend to writing that shape.
SCHEMA_READS = "session-place-on-agent-sessions"


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

    The second read is the deploy window, not a second source of truth. The
    release that moved the location onto the session does not replace this
    process, so between the backend switching over and the owner's own release
    this is the only process still running the previous shape — and every room
    that already had a screen open recorded its location on the room. A place
    that has a session row is answered by that row and this never runs.

    P19 drops `topics.session_placement`; this branch goes with it, in the same
    commit, because by then it can only return nothing.
    """
    located = (
        await session.execute(
            select(AgentSession.runtime_location, AgentSession.work_lease).where(
                AgentSession.topic_id == place_id,
                AgentSession.task_id.is_(None),
                AgentSession.runtime_location.is_not(None),
            )
        )
    ).all()
    if located:
        return [(row.runtime_location, row.work_lease) for row in located]
    on_the_room = await session.scalar(
        select(Topic.session_placement).where(Topic.id == place_id)
    )
    if not on_the_room:
        return []
    where = {key: value for key, value in on_the_room.items() if key != "execution"}
    return [(where, on_the_room.get("execution"))]


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
