"""Running AI work on a machine, from turn intervals and session placement."""

from functools import cache

from sqlalchemy import String, and_, bindparam, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.models import AgentTurn
from app.domain.agent_instance.models import AgentInstance
from app.domain.agent_session.models import AgentSession
from app.domain.conversation.models import Conversation
from app.domain.project.models import Project
from app.domain.room_task.models import Task as RoomTask
from app.domain.team.models import Team
from app.domain.thread.models import Thread
from app.domain.topic.models import Topic

_DEVICE = bindparam("device_id", type_=String)


@cache
def _running_on_statement():
    """Built once: putting this select together took 0.8 ms of Python on every
    poll, which a connector repeats every few seconds per machine."""
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
            func.min(AgentTurn.started_at),
            Topic,
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
        .where(or_(center == _DEVICE, executor == _DEVICE))
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


async def running_on(db: AsyncSession, device_id: str) -> list[tuple[Topic, dict]]:
    rows = await db.execute(_running_on_statement(), {"device_id": device_id})
    return [
        (
            topic,
            {
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
            },
        )
        for sid, handle, name, name_source, project, project_name, team, team_name, conversation, kind, title, task_room, thread_room, central, execution, since, topic in rows
    ]
