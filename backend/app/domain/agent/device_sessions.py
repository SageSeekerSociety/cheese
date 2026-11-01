"""Running AI work on a machine, from turn intervals and session placement."""

from functools import cache

from sqlalchemy import (
    ColumnElement,
    Integer,
    Select,
    String,
    and_,
    bindparam,
    func,
    literal,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.models import AgentTurn
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_session.models import AgentSession
from app.domain.conversation.models import Conversation
from app.domain.device.models import DeviceRow, ModelCredentialHost
from app.domain.project.models import Project
from app.domain.room_task.models import Task as RoomTask
from app.domain.team.models import Team
from app.domain.thread.models import Thread
from app.domain.topic.models import Topic
from app.domain.user.tokens import digest_token

_DEVICE = bindparam("device_id", type_=String)


def _running_select(device: ColumnElement[str], *, topic: bool) -> Select:
    """The sessions running on `device`, oldest open turn first; with each
    one's room row when `topic` (the viewer route filters by it)."""
    center = AgentSession.runtime_location["device_id"].as_string()
    executor = AgentSession.work_lease["device_id"].as_string()
    return (
        select(
            AgentSession.id,
            AgentSession.agent_handle,
            AgentInstance.display_name,
            AgentInstance.name_source,
            Project.id,
            Project.name,
            Team.id,
            Team.name,
            Conversation.id,
            Conversation.kind,
            func.coalesce(RoomTask.title, Topic.title),
            RoomTask.room_id,
            Thread.room_id,
            center,
            executor,
            func.min(AgentTurn.started_at).label("since"),
            *([Topic] if topic else []),
        )
        .join(Conversation, Conversation.id == AgentSession.conversation_id)
        .join(Project, Project.id == Conversation.project_id)
        .join(Team, Team.id == Project.team_id)
        .join(
            AgentTurn,
            and_(
                AgentTurn.conversation_id == AgentSession.conversation_id,
                AgentTurn.agent_handle == AgentSession.agent_handle,
                AgentTurn.stopped_at.is_(None),
                or_(
                    AgentTurn.session_id == AgentSession.resume_token,
                    AgentTurn.session_id.is_(None),
                ),
            ),
        )
        .outerjoin(
            AgentInstance,
            and_(
                AgentInstance.project_id == Project.id,
                AgentInstance.handle == AgentSession.agent_handle,
            ),
        )
        .outerjoin(RoomTask, RoomTask.id == Conversation.id)
        .outerjoin(Thread, Thread.id == Conversation.id)
        .join(
            Topic,
            Topic.id
            == func.coalesce(RoomTask.room_id, Thread.room_id, Conversation.id),
        )
        .where(or_(center == device, executor == device))
        .group_by(
            AgentSession.id,
            AgentInstance.display_name,
            AgentInstance.name_source,
            Project.id,
            Team.id,
            Conversation.id,
            RoomTask.id,
            Topic.id,
            Thread.id,
        )
        .order_by(func.min(AgentTurn.started_at))
    )


@cache
def _running_on_statement() -> Select:
    """Built once: putting this select together took 0.8 ms of Python on every
    poll, which a connector repeats every few seconds per machine."""
    return _running_select(_DEVICE, topic=True)


@cache
def _running_for_token_statement() -> Select:
    """The machine a connector token belongs to, with what runs on it, in one
    statement: the connector polls this every few seconds per machine, and the
    token lookup was a statement of its own before it. A device token is looked
    up before a credential host's, as `owner_reads.device_for_token` does; a
    holder with nothing running comes back as one row of empty session
    columns."""
    digest = bindparam("digest", type_=String)
    holder = union_all(
        select(
            DeviceRow.device_id.label("device_id"),
            literal(0, Integer).label("rank"),
        ).where(DeviceRow.token_digest == digest),
        select(ModelCredentialHost.id, literal(1, Integer)).where(
            ModelCredentialHost.token_digest == digest
        ),
    ).subquery("holder")
    running = _running_select(holder.c.device_id, topic=False).lateral("running")
    return (
        select(holder.c.device_id, running)
        .select_from(holder)
        .outerjoin(running, true())
        .order_by(holder.c.rank, running.c.since)
    )


def _described(device_id: str, row) -> dict:
    (
        sid,
        handle,
        name,
        name_source,
        project,
        project_name,
        team,
        team_name,
        conversation,
        kind,
        title,
        task_room,
        thread_room,
        central,
        execution,
        since,
    ) = row
    return {
        "session_id": str(sid),
        "agent_name": name or handle,
        "agent_name_source": name_source.value if name_source else None,
        "project_id": str(project),
        "project_name": project_name,
        "team_id": team,
        "team_name": team_name,
        "conversation_id": str(conversation),
        "conversation_kind": kind.value,
        "conversation_title": title or str(conversation),
        "room_id": str(task_room or thread_room or conversation),
        "roles": (["center"] if central == device_id else [])
        + (["executor"] if execution == device_id else []),
        "since": since.isoformat(),
    }


async def running_on(db: AsyncSession, device_id: str) -> list[tuple[Topic, dict]]:
    rows = await db.execute(_running_on_statement(), {"device_id": device_id})
    return [(row[-1], _described(device_id, row[:-1])) for row in rows]


async def running_for_token(db: AsyncSession, token: str) -> list[dict] | None:
    """What runs on the machine `token` belongs to; None when it belongs to
    none."""
    if not token:
        return None
    rows = (
        await db.execute(
            _running_for_token_statement(), {"digest": digest_token(token)}
        )
    ).all()
    if not rows:
        return None
    device_id = rows[0][0]
    return [
        _described(device_id, row[1:])
        for row in rows
        if row[0] == device_id and row[1] is not None
    ]
