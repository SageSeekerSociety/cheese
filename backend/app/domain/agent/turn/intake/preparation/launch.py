"""A seat's session as it would be started, and the harness it would run on.

Read without opening anything: no backlog claimed, no policy gate passed,
nothing written. `prepare` and `converse` hand `RoomSessions.send` the same
inputs by the same helpers, so a session started from these is the one the next
turn would compare equal and keep.

`backend_for` is the one answer to "which harness does this agent run on"; a
turn resolves it once and reads it from there, because the session row is keyed
by it and resolving it twice is enough to split one conversation in two.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.domain.agent.harness import SessionRef
from app.domain.agent.queries import _session_agent
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.room.system_prompt import session_system_prompt
from app.domain.agent.turn.intake.context import Launch, living_doc_text
from app.domain.agent.turn.intake.rooms import _is_dm
from app.domain.agent_instance.own import current_role, owned_instance
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_session.services import AgentSessionService
from app.domain.project.reads import load_project
from app.domain.room_task.place import PlaceResolver
from app.domain.topic.models import TopicStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domain.agent.turn.intake import preparation as pkg
    from app.domain.agent_instance.services import ResolvedAgent
    from app.domain.project.models import Project


async def backend_for(
    prep: pkg.TurnPreparation,
    session: AsyncSession,
    agent: ResolvedAgent,
    project: Project | None,
) -> tuple[str, RoomSessions | None]:
    """The harness this agent's turn runs and the backend it runs on.

    A member's own coding agent runs its own harness where its model
    connection is (`owner_provider`), whatever the room chose; every other
    agent runs the project's harness on the room's machine."""
    owned = await owned_instance(session, agent.instance_id)
    if owned is not None:
        return owned.harness, prep.compute.owned(
            owned.harness, on_account=owned.on_account
        )
    return prep.compute.choose(project.settings if project else None)


async def launch_inputs(
    prep: pkg.TurnPreparation,
    topic_id: uuid.UUID,
    agent_handle: str,
    *,
    acting: str,
) -> Launch | None:
    """What this seat's session would be started with if a turn started it
    now, read without opening one — no backlog claimed, no policy gate
    passed, nothing written. None when the room has nowhere to run.

    Every input is the one `prepare` and `converse` hand
    `RoomSessions.send`, from the same helpers, so a session started from
    these is the one the next turn would compare equal and keep."""
    async with prep.sessions() as session:
        place = await PlaceResolver(session).resolve(topic_id)
        if place is None or place.room.status == TopicStatus.archived:
            return None
        topic = place.room
        project = await load_project(session, topic.project_id)
        if project is None:
            return None
        agents = AgentInstanceService(session)
        agent = await _session_agent(agents, topic, project, agent_handle)
        needs_place = not _is_dm(topic)
        doc_text = await living_doc_text(session, place, needs_place=needs_place)
        role = await current_role(session, agent.instance_id) or (
            await agents.system_prompt(agent)
        )
        harness, provider = await prep._backend_for(session, agent, project)
        if provider is None:
            return None
        resume_token = await AgentSessionService(session).resume_token(
            place.room_id, agent.handle, harness=harness
        )
    model_kwargs, _route = await prep.model_options(
        project.id, provider, topic_id, agent=agent, acting_agent=acting
    )
    return Launch(
        session=SessionRef(project.id, topic_id, agent.handle, harness=harness),
        runtime=provider,
        system_prompt=session_system_prompt(
            prep.base_prompt,
            prep.skills,
            needs_place=needs_place,
            has_doc=doc_text is not None,
            role=role,
            harness=provider.harness,
            name=agent.display_name,
        ),
        resume_token=resume_token,
        model=model_kwargs.get("model"),
        env=model_kwargs.get("env"),
        needs_place=needs_place,
    )
