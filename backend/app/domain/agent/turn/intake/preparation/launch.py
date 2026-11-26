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

from app.core.errors import UnprocessableEntityError
from app.domain.agent.carried_bodies import carried_bodies
from app.domain.agent.harness import SessionRef
from app.domain.agent.queries import _acting_handle, _agent_at, _named_teammate
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.room.system_prompt import (
    session_env,
    session_kind,
    session_system_prompt,
)
from app.domain.agent.turn.intake.context import (
    Launch,
    carried_skills,
    living_doc_text,
)
from app.domain.agent.turn.intake.rooms import _is_dm
from app.domain.agent.work_policy import uses_session_host
from app.domain.agent_instance.own import current_role, owned_instance
from app.domain.agent_instance.services import AgentInstanceService
from app.domain.agent_session.services import AgentSessionService
from app.domain.project.reads import load_project
from app.domain.prompts import PROFILES
from app.domain.room_task.place import PlaceResolver
from app.domain.task import teaching as teaching_context
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
) -> tuple[str, RoomSessions | None, bool]:
    """The harness this agent's turn runs and the backend it runs on.

    A member's own coding agent runs its own harness where its model
    connection is (`owner_provider`), whatever the room chose; every other
    agent runs the project's harness on the room's machine."""
    owned = await owned_instance(session, agent.instance_id)
    if owned is not None:
        harness = owned.harness
        provider = prep.compute.owned(harness, on_account=owned.on_account)
    else:
        harness, provider = prep.compute.choose(project.settings if project else None)
    return harness, provider, uses_session_host(owned)


async def launch_inputs(
    prep: pkg.TurnPreparation,
    topic_id: uuid.UUID,
    agent_handle: str,
    *,
    acting: str | None = None,
) -> Launch | None:
    """What this seat's session would be started with if a turn started it
    now, read without opening one — no backlog claimed, no policy gate
    passed, nothing written. None when the conversation has nowhere to run,
    or the teammate may not act in it.

    ``topic_id`` is the conversation: a channel or private chat, or a task or
    支线 in one, each of which has a session of its own. ``acting`` is the seat
    a running session already acts under; unnamed, it is read as a turn reads
    it (`_acting_handle`).

    Every input is the one `prepare` and `converse` hand
    `RoomSessions.send`, from the same helpers, so a session started from
    these is the one the next turn would compare equal and keep."""
    async with prep.sessions() as session:
        place = await PlaceResolver(session).conversation(topic_id)
        if place is None or place.room.status == TopicStatus.archived:
            return None
        topic = place.room
        project = await load_project(session, topic.project_id)
        if project is None:
            return None
        agents = AgentInstanceService(session)
        # The teammate the seat names, else the one that works here, as a
        # turn resolves it (`assembly`).
        agent = await _named_teammate(agents, project, agent_handle) or await _agent_at(
            session, place
        )
        if acting is None:
            try:
                acting = await _acting_handle(session, topic.id, agent)
            except UnprocessableEntityError:
                return None
        needs_place = not _is_dm(topic)
        kind = session_kind(place, needs_place=needs_place)
        doc_text = await living_doc_text(session, place, needs_place=needs_place)
        role = await current_role(session, agent.instance_id) or (
            await agents.system_prompt(agent)
        )
        harness, provider, on_session_host = await prep._backend_for(
            session, agent, project
        )
        if provider is None:
            return None
        continuation = await AgentSessionService(session).continuation(
            place.conversation_id,
            agent.handle,
            harness=harness,
            generation=PROFILES[kind].generation,
        )
        # The skills the teammate carries, read as the turn reads them
        # (`assembly`): a prewarmed session whose prompt lacked them would be
        # replaced by the first turn, which is the work prewarming saves.
        teaching = await teaching_context.for_project(session=session, project=project)
        carried = await carried_bodies(
            session,
            topic,
            carried_skills(agent, topic, teaching.teaching.skills if teaching else ()),
        )
    model_kwargs, _route = await prep.model_options(
        project.id,
        provider,
        place.room_id,
        agent=agent,
        acting_agent=acting,
        task_id=place.task_id,
        default_effort=PROFILES[kind].effort,
    )
    model_kwargs["env"] = {**model_kwargs.get("env", {}), **session_env(kind)}
    return Launch(
        session=SessionRef(
            project.id,
            place.room_id,
            agent.handle,
            harness=harness,
            inner_id=place.inner_id,
        ),
        runtime=provider,
        on_session_host=on_session_host,
        system_prompt=session_system_prompt(
            prep.base_prompt,
            kind=kind,
            has_doc=doc_text is not None,
            role=role,
            harness=provider.harness,
            name=agent.display_name,
            carried=carried,
        ),
        resume_token=continuation.resume,
        retired_token=continuation.retired,
        model=model_kwargs.get("model"),
        env=model_kwargs.get("env"),
        needs_place=needs_place,
        acting=acting,
        keeps_nothing=not place.keeps_work,
    )
