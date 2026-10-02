"""The facts a turn is admitted on, read from the room it would run in.

Kept apart from ``admission``, which the work runner imports: these read the
project, the room and the compute pool, and a runner that reached them would
close a loop through the harness packages.
"""

import uuid

from app.core.config import settings
from app.domain.agent.compute_configs import project_configs, room_choice
from app.domain.project.repositories import ProjectRepository
from app.domain.topic.repositories import TopicRepository
from app.domain.usage.ledger import Ledger, payer_for_project


def resolve_compute_id(project_settings: dict | None, topic=None) -> str | None:
    """A room keeps its choice; otherwise use the explicit project default."""
    if topic is not None:
        return room_choice(topic, project_settings).profile
    return project_configs(project_settings).default.profile


async def work_policy(sessions, compute, topic_id: uuid.UUID) -> dict | None:
    """Admission facts a turn is gated on before it runs (spec §9.1 算力额度):
    the owning project, its concurrency ceiling, whether its compute credits are
    exhausted, and whether its session starts on the session host. None when
    the topic does not exist (the turn itself will surface the 404)."""
    async with sessions() as session:
        topic = await TopicRepository(session).get(topic_id)
        if topic is None:
            return None
        project = await ProjectRepository(session).get(topic.project_id)
        refused = await Ledger(session).admit(
            await payer_for_project(session, topic.project_id)
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
        project_settings, resolve_compute_id(project_settings, topic)
    )
    return {
        "project_id": str(topic.project_id),
        "max_concurrent_turns": max_concurrent,
        # Why the project's credits refuse a turn now (a sentence the room
        # shows), or None when they admit it.
        "credits_exhausted": refused.message if refused is not None else None,
        "on_session_host": provider is not None,
    }
