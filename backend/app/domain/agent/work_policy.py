"""The facts a turn is admitted on, read from the room it would run in.

Kept apart from ``admission``, which the work runner imports: these read the
project, the room and the compute pool, and a runner that reached them would
close a loop through the harness packages.
"""

import uuid

from app.core.config import settings
from app.domain.agent.compute_configs import place_choice, project_configs
from app.domain.agent_instance.own import owned_instance
from app.domain.project.repositories import ProjectRepository
from app.domain.room_task.place import PlaceResolver
from app.domain.usage.ledger import Ledger, payer_for_project


def resolve_compute_id(
    project_settings: dict | None, topic=None, task=None
) -> str | None:
    """A task keeps its own choice, a room its; otherwise the project default."""
    if topic is not None:
        return place_choice(topic, task, project_settings).profile
    return project_configs(project_settings).default.profile


async def work_policy(
    sessions, compute, topic_id: uuid.UUID, agent_instance_id: uuid.UUID | None = None
) -> dict | None:
    """Admission facts a turn is gated on before it runs (spec §9.1 算力额度):
    the owning project, its concurrency ceiling, whether its compute credits are
    exhausted, and whether its session starts on the session host. None when
    the topic does not exist (the turn itself will surface the 404)."""
    async with sessions() as session:
        place = await PlaceResolver(session).conversation(topic_id)
        if place is None:
            return None
        topic, task = place.room, place.task
        project = await ProjectRepository(session).get(topic.project_id)
        # A member's own coding agent is paid by its owner's own login and
        # runs on their machine: the project's credits and the session host's
        # memory are not what admits it.
        owned = (
            await owned_instance(session, agent_instance_id)
            if agent_instance_id is not None
            else None
        )
        refused = (
            None
            if owned is not None
            else await Ledger(session).admit(
                await payer_for_project(session, topic.project_id)
            )
        )
    project_settings = project.settings if project else None
    max_concurrent = settings.max_concurrent_turns
    override = (project_settings or {}).get("max_concurrent_turns")
    if isinstance(override, int) and override > 0:
        max_concurrent = override
    # The backend the turn will run on, chosen by the call the turn makes. Every
    # harness runs its session on the session host and reaches the room's
    # machine from there; a turn with no backend here starts no session at all.
    _harness, provider = compute.choose(
        project_settings, resolve_compute_id(project_settings, topic, task)
    )
    return {
        "project_id": str(topic.project_id),
        "max_concurrent_turns": max_concurrent,
        # Why the project's credits refuse a turn now (a sentence the room
        # shows), or None when they admit it.
        "credits_exhausted": refused.message if refused is not None else None,
        "on_session_host": provider is not None and owned is None,
    }
